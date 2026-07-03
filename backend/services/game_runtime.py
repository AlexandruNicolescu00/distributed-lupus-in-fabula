import logging
import time
from typing import Any, Awaitable, Callable

from core import state_store as rs
from core.instance import INSTANCE_ID
from core.messages import EventType
from models.events import (
    GameEndedPayload,
    GameStateSyncPayload,
    PlayerKilledPayload,
    SeerActionAcceptedPayload,
    SeerResultPayload,
    WolfVoteAcceptedPayload,
)
from models.game import Phase, Role

logger = logging.getLogger(__name__)
from services.game_logic import (
    advance_phase,
    assign_roles,
    build_phase_changed_payload,
    build_role_payloads,
    cast_vote,
    record_seer_action,
    record_wolf_vote,
    set_phase,
)
from services.lobby_logic import player_payload


class GameRuntime:
    def __init__(
        self,
        *,
        get_redis: Callable[[], Any],
        connection_manager: Any,
        emit_authoritative_event: Callable[..., Awaitable[None]],
        sync_room_state: Callable[[str], Awaitable[dict]],
        schedule_phase_timer: Callable[[str, float | None], None],
        cancel_phase_timer: Callable[[str], None],
        **kwargs # Accetta eventuali altri parametri imprevisti da main.py
    ) -> None:
        self._get_redis = get_redis
        self._connection_manager = connection_manager
        self._emit_authoritative_event = emit_authoritative_event
        self._sync_room_state = sync_room_state
        self._schedule_phase_timer = schedule_phase_timer
        self._cancel_phase_timer = cancel_phase_timer

    async def _emit_role_assignments(self, room_id: str, payloads: dict[str, Any]) -> None:
        for client_id, payload in payloads.items():
            # sid locale se il giocatore è su QUESTA replica (consegna diretta a
            # bassa latenza). In ogni caso passiamo target_client_id così la replica
            # che lo ospita recapiti il ruolo privato via Pub/Sub: i 5 giocatori
            # possono essere distribuiti su repliche diverse, e senza questo chi era
            # su un'altra replica restava con "ruolo segreto".
            sid = self._connection_manager.get_sid(room_id, client_id)
            await self._emit_authoritative_event(
                EventType.ROLE_ASSIGNED,
                room_id,
                payload,
                to=sid,
                publish=False,
                target_client_id=client_id,
            )

    async def emit_role_assignment_for_player(self, room_id: str, client_id: str, sid: str) -> None:
        redis = self._get_redis()
        player = await rs.get_player(redis, room_id, client_id)
        if player is None or player.role is None:
            return

        all_players = await rs.get_all_players(redis, room_id)
        assignment = {
            player_id: current_player.role
            for player_id, current_player in all_players.items()
            if current_player.role is not None
        }
        role_payload = build_role_payloads(assignment, all_players).get(client_id)
        if role_payload is None:
            return

        await self._emit_authoritative_event(
            EventType.ROLE_ASSIGNED,
            room_id,
            role_payload,
            to=sid,
            publish=False,
        )

    async def _emit_game_end(self, room_id: str, result: dict[str, Any]) -> None:
        winner = result.get("winner")
        payload = GameEndedPayload(
            winner=winner.value if hasattr(winner, "value") else str(winner),
            reason="all_wolves_dead" if getattr(winner, "value", str(winner)) == "VILLAGERS" else "wolves_parity",
            round=result.get("round", 0),
            players=result.get("final_players", []),
        )
        await self._emit_authoritative_event(EventType.GAME_ENDED, room_id, payload)

    async def _emit_night_resolution(self, room_id: str, result: dict[str, Any]) -> None:
        night_result = result.get("night_result")
        if not night_result:
            return

        redis = self._get_redis()

        killed_player_id = night_result.get("killed_player_id")
        if killed_player_id:
            victim = await rs.get_player(redis, room_id, killed_player_id)
            if victim is not None:
                await self._emit_authoritative_event(
                    EventType.PLAYER_KILLED,
                    room_id,
                    PlayerKilledPayload(
                        player_id=victim.player_id,
                        username=victim.username,
                        player=player_payload(victim, reveal_role=False),
                    ),
                )

        seer_target_id = night_result.get("seer_target_id")
        seer_target_role = night_result.get("seer_target_role")
        if seer_target_id and seer_target_role:
            all_players = await rs.get_all_players(redis, room_id)
            seer = next((p for p in all_players.values() if p.role and p.role.value == "SEER"), None)
            if seer is not None:
                # La risoluzione della notte può girare su una replica diversa da
                # quella a cui è connesso il veggente: consegna locale se presente,
                # e comunque recapito cross-replica via target_client_id.
                sid = self._connection_manager.get_sid(room_id, seer.player_id)
                target = all_players.get(seer_target_id)
                await self._emit_authoritative_event(
                    EventType.SEER_RESULT,
                    room_id,
                    SeerResultPayload(
                        target_id=seer_target_id,
                        target_name=target.username if target else seer_target_id,
                        role=seer_target_role,
                    ),
                    to=sid,
                    publish=False,
                    target_client_id=seer.player_id,
                )

    async def _emit_phase_outcome(self, room_id: str, result: dict[str, Any]) -> None:
        if result.get("eliminated_player") is not None:
            await self._emit_authoritative_event(
                EventType.PLAYER_ELIMINATED,
                room_id,
                result["eliminated_player"],
            )

        if result.get("no_elimination") is not None:
            await self._emit_authoritative_event(
                EventType.NO_ELIMINATION,
                room_id,
                result["no_elimination"],
            )

        await self._emit_night_resolution(room_id, result)

        if result.get("winner") is not None:
            self._cancel_phase_timer(room_id)
            await rs.remove_active_room(self._get_redis(), room_id)
            await self._emit_game_end(room_id, result)
            await self._sync_room_state(room_id)
            return

        next_phase = result.get("next_phase")
        if next_phase is not None:
            await self._emit_authoritative_event(
                EventType.PHASE_CHANGED,
                room_id,
                build_phase_changed_payload(
                    phase=next_phase,
                    round_number=result.get("round", 0),
                    timer_end=result.get("timer_end"),
                ),
            )
            self._schedule_phase_timer(room_id, result.get("timer_end"))
        if next_phase == Phase.NIGHT:
            await self._check_night_actions_complete(room_id)

        await self._sync_room_state(room_id)

    async def advance_phase_and_emit(self, room_id: str) -> None:
        # Lock atomico (NX) che serializza i tre trigger di avanzamento — timer di
        # fase, evento manuale `phase:advance`, sweeper di recupero — anche tra
        # repliche diverse. Senza, due trigger ravvicinati eseguivano due salti di
        # fase / doppia eliminazione. (Rif. teoria: state-machine-replication.)
        if not await rs.acquire_advance_lock(self._get_redis(), room_id, INSTANCE_ID):
            logger.debug("advance_phase saltato (lock non acquisito) | room=%s", room_id)
            return
        result = await advance_phase(self._get_redis(), room_id)
        await self._emit_phase_outcome(room_id, result)

    async def handle_cast_vote(self, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
        target_id = payload.get("target_id")
        if not target_id:
            raise ValueError("Missing target_id for cast_vote")

        redis = self._get_redis()
        vote_update = await cast_vote(redis, room_id, client_id, target_id)
        await self._emit_authoritative_event(EventType.VOTE_UPDATE, room_id, vote_update)

        # Controllo Timer: Tutti i vivi hanno votato?
        players = await rs.get_all_players(redis, room_id)
        alive_count = sum(1 for p in players.values() if p.alive)
        votes = await rs.get_votes(redis, room_id)

        if len(votes) == alive_count:
            # Salta il timer
            self._cancel_phase_timer(room_id)
            await self.advance_phase_and_emit(room_id)

    async def handle_wolf_vote(self, sid: str, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
        target_id = payload.get("target_id")
        if not target_id:
            raise ValueError("Missing target_id for wolf_vote")

        redis = self._get_redis()
        await record_wolf_vote(redis, room_id, client_id, target_id)
        await self._emit_authoritative_event(
            EventType.WOLF_VOTE,
            room_id,
            WolfVoteAcceptedPayload(target_id=target_id),
            to=sid,
            publish=False,
        )
        
        await self._check_night_actions_complete(room_id)

    async def handle_seer_action(self, sid: str, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
        target_id = payload.get("target_id")
        if not target_id:
            raise ValueError("Missing target_id for seer_action")

        redis = self._get_redis()
        await record_seer_action(redis, room_id, client_id, target_id)
        await self._emit_authoritative_event(
            EventType.SEER_ACTION,
            room_id,
            SeerActionAcceptedPayload(target_id=target_id),
            to=sid,
            publish=False,
        )
        
        await self._check_night_actions_complete(room_id)

    async def _check_night_actions_complete(self, room_id: str) -> None:
        """Helper to check if all wolves and seer have acted. If so, skips timer."""
        redis = self._get_redis()
        players = await rs.get_all_players(redis, room_id)

        # Check wolves
        alive_wolves = [p for p in players.values() if p.alive and p.role and p.role.value == 'WOLF']
        wolf_votes = await rs.get_wolf_votes(redis, room_id)
        wolves_done = len(alive_wolves) == 0 or len(wolf_votes) >= len(alive_wolves)

        # Check seer
        alive_seer = next((p for p in players.values() if p.alive and p.role and p.role.value == 'SEER'), None)
        seer_action = await rs.get_seer_action(redis, room_id)
        seer_done = (alive_seer is None) or (seer_action is not None)

        logger.debug(
            "night_check | room=%s wolves=%d votes=%d wolves_done=%s alive_seer=%s seer_done=%s",
            room_id, len(alive_wolves), len(wolf_votes), wolves_done,
            alive_seer.player_id if alive_seer else None, seer_done,
        )

        if wolves_done and seer_done:
            self._cancel_phase_timer(room_id)
            await self.advance_phase_and_emit(room_id)

    async def handle_game_start(self, room_id: str) -> None:
        redis = self._get_redis()

        # Lista AUTORITATIVA dei giocatori: vive in Redis ed è condivisa da tutte le
        # repliche. Il connection_manager è invece stato locale del singolo processo,
        # quindi con più repliche dietro il reverse proxy vede solo il sottoinsieme
        # di socket connessi a QUESTA replica. Contare i socket locali qui impediva
        # l'avvio quando i 5 giocatori erano distribuiti su pod diversi (rif. teoria:
        # stato condiviso vs stato di processo / statelessness delle repliche).
        all_players = await rs.get_all_players(redis, room_id)
        player_ids = [pid for pid, player in all_players.items() if player.connected]
        if len(player_ids) < 5:
            raise ValueError("Need at least 5 connected players to start the game")

        state = await rs.get_game_state(redis, room_id) or {}
        wolf_count = state.get("wolf_count")
        seer_count = state.get("seer_count")

        # Pulizia stato residuo da partite precedenti nella stessa stanza
        await rs.clear_wolf_votes(redis, room_id)
        await rs.clear_seer_action(redis, room_id)

        # Reset has_acted/has_voted per tutti i giocatori (potrebbero essere rimasti
        # da una partita interrotta prima di _resolve_night)
        all_players_pre = await rs.get_all_players(redis, room_id)
        to_reset = []
        for p in all_players_pre.values():
            if p.has_acted or p.has_voted:
                p.has_acted = False
                p.has_voted = False
                to_reset.append(p)
        if to_reset:
            await rs.set_players_bulk(redis, room_id, to_reset)

        assignment = await assign_roles(
            redis,
            room_id,
            player_ids,
            wolf_count=wolf_count,
            seer_count=seer_count,
        )
        resolved_wolf_count = sum(1 for role in assignment.values() if role == Role.WOLF)
        resolved_seer_count = sum(1 for role in assignment.values() if role == Role.SEER)
        await rs.patch_game_state(
            redis,
            room_id,
            wolf_count=resolved_wolf_count,
            seer_count=resolved_seer_count,
            ready_player_ids=[],
        )
        players = await rs.get_all_players(redis, room_id)
        await self._emit_role_assignments(room_id, build_role_payloads(assignment, players))

        timer_end = await set_phase(redis, room_id, Phase.NIGHT, round_number=0)
        # Registra la stanza come "attiva": lo sweeper potrà recuperarne il timer
        # se la replica che lo ha schedulato muore (P7).
        await rs.add_active_room(redis, room_id)
        await self._sync_room_state(room_id)
        await self._emit_authoritative_event(
            EventType.PHASE_CHANGED,
            room_id,
            build_phase_changed_payload(Phase.NIGHT, 0, timer_end),
        )
        self._schedule_phase_timer(room_id, timer_end)
        await self._check_night_actions_complete(room_id)

    async def handle_phase_advance(self, room_id: str) -> None:
        await self.advance_phase_and_emit(room_id)

    # ── Recovery distribuito dei timer + anti-entropy ──────────────────────────

    async def recover_expired_timers(self) -> None:
        """Sweeper: fa avanzare le fasi il cui timer è scaduto, a prescindere da
        quale replica lo aveva schedulato. È la rete di sicurezza contro la perdita
        del timer in-memory quando una replica crasha o viene rimossa dall'HPA (P7).
        L'avanzamento passa per `acquire_advance_lock`, quindi una sola replica
        vince e non c'è doppio avanzamento."""
        redis = self._get_redis()
        now = time.time()
        for room_id in await rs.get_active_rooms(redis):
            try:
                state = await rs.get_game_state(redis, room_id)
                if state is None:
                    await rs.remove_active_room(redis, room_id)
                    continue
                phase = state.get("phase")
                if phase == Phase.ENDED.value:
                    await rs.remove_active_room(redis, room_id)
                    continue
                if phase == Phase.LOBBY.value:
                    continue
                timer_end = state.get("timer_end")
                if timer_end is not None and timer_end <= now:
                    await self.advance_phase_and_emit(room_id)
            except Exception:
                logger.exception("Sweeper timer: errore su room=%s", room_id)

    async def broadcast_state_snapshots(self) -> None:
        """Anti-entropy: ribroadcast periodico dello snapshot autoritativo alle
        stanze attive. I client che hanno perso un evento Pub/Sub (at-most-once,
        es. durante un drop di Redis o appena dopo lo scaling) riconvergono entro
        l'intervallo. (Rif. teoria: eventual-consistency.) Server-side: nessuna
        modifica al client, che già gestisce `game_state_sync`."""
        redis = self._get_redis()
        for room_id in await rs.get_active_rooms(redis):
            try:
                snapshot = await self._sync_room_state(room_id)
                await self._emit_authoritative_event(
                    EventType.GAME_STATE_SYNC,
                    room_id,
                    GameStateSyncPayload(
                        state=snapshot,
                        players=snapshot.get("players", []),
                    ),
                )
            except Exception:
                logger.exception("Anti-entropy: errore su room=%s", room_id)
