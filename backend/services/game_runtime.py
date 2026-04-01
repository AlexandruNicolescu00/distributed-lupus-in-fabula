from typing import Any, Awaitable, Callable

from core import state_store as rs
from core.messages import EventType
from models.events import (
    GameEndedPayload,
    PlayerKilledPayload,
    SeerActionAcceptedPayload,
    SeerResultPayload,
    WolfVoteAcceptedPayload,
)
from models.game import Phase, Role
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
    ) -> None:
        self._get_redis = get_redis
        self._connection_manager = connection_manager
        self._emit_authoritative_event = emit_authoritative_event
        self._sync_room_state = sync_room_state
        self._schedule_phase_timer = schedule_phase_timer
        self._cancel_phase_timer = cancel_phase_timer

    async def _emit_role_assignments(self, room_id: str, payloads: dict[str, Any]) -> None:
        for client_id, payload in payloads.items():
            sid = self._connection_manager.get_sid(room_id, client_id)
            if sid is None:
                continue
            await self._emit_authoritative_event(
                EventType.ROLE_ASSIGNED,
                room_id,
                payload,
                to=sid,
                publish=False,
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
            sid = self._connection_manager.get_sid(room_id, seer.player_id) if seer is not None else None
            if sid is not None:
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

        await self._sync_room_state(room_id)

    async def advance_phase_and_emit(self, room_id: str) -> None:
        result = await advance_phase(self._get_redis(), room_id)
        await self._emit_phase_outcome(room_id, result)

    async def handle_cast_vote(self, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
        target_id = payload.get("target_id")
        if not target_id:
            raise ValueError("Missing target_id for cast_vote")

        vote_update = await cast_vote(self._get_redis(), room_id, client_id, target_id)
        await self._emit_authoritative_event(EventType.VOTE_UPDATE, room_id, vote_update)

    async def handle_wolf_vote(self, sid: str, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
        target_id = payload.get("target_id")
        if not target_id:
            raise ValueError("Missing target_id for wolf_vote")

        await record_wolf_vote(self._get_redis(), room_id, client_id, target_id)
        await self._emit_authoritative_event(
            EventType.WOLF_VOTE,
            room_id,
            WolfVoteAcceptedPayload(target_id=target_id),
            to=sid,
            publish=False,
        )

    async def handle_seer_action(self, sid: str, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
        target_id = payload.get("target_id")
        if not target_id:
            raise ValueError("Missing target_id for seer_action")

        await record_seer_action(self._get_redis(), room_id, client_id, target_id)
        await self._emit_authoritative_event(
            EventType.SEER_ACTION,
            room_id,
            SeerActionAcceptedPayload(target_id=target_id),
            to=sid,
            publish=False,
        )

    async def handle_game_start(self, room_id: str, payload: dict[str, Any]) -> None:
        player_ids = self._connection_manager.get_client_ids(room_id)
        if len(player_ids) < 5:
            raise ValueError("Need at least 5 connected players to start the game")

        redis = self._get_redis()
        wolf_count = payload.get("wolf_count")
        seer_count = payload.get("seer_count")
        if wolf_count is not None:
            wolf_count = int(wolf_count)
        if seer_count is not None:
            seer_count = int(seer_count)

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
        )
        players = await rs.get_all_players(redis, room_id)
        await self._emit_role_assignments(room_id, build_role_payloads(assignment, players))

        timer_end = await set_phase(redis, room_id, Phase.NIGHT, round_number=0)
        await self._sync_room_state(room_id)
        await self._emit_authoritative_event(
            EventType.PHASE_CHANGED,
            room_id,
            build_phase_changed_payload(Phase.NIGHT, 0, timer_end),
        )
        self._schedule_phase_timer(room_id, timer_end)

    async def handle_phase_advance(self, room_id: str) -> None:
        await self.advance_phase_and_emit(room_id)
