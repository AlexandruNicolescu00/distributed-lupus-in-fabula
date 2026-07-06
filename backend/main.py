# Entry point FastAPI + Socket.IO - VERSIONE AGGIORNATA
# Gestisce correttamente gli oggetti player completi invece di soli ID

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from core.config import get_settings
from core.instance import INSTANCE_ID
from core.messages import EventType, RedisEvent, WSMessage
from core.metrics import (
    WS_BROADCAST_DURATION_SECONDS,
    WS_MESSAGE_SIZE_BYTES,
    WS_MESSAGES_RECEIVED_TOTAL,
    WS_MESSAGES_SENT_TOTAL,
)
from core.state_store import GameStateStore
from models.game import Phase
from models.events import ErrorPayload
from pubsub.manager import PubSubManager
from services.game_runtime import GameRuntime
from services.lobby_logic import (
    build_player_joined_payload,
    build_player_left_payload,
    build_state_sync_payload,
    ensure_domain_player,
    get_player,
    mark_player_disconnected,
    promote_host_if_needed,
    sync_room_state as sync_lobby_room_state,
)
from services.lobby_runtime import LobbyRuntime
from websocket.connection_manager import ConnectionManager

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()

# ── Socket.IO server ──────────────────────────────────────────────────────────
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)

# ── Istanze singleton ─────────────────────────────────────────────────────────
connection_manager = ConnectionManager()
pubsub_manager     = PubSubManager(sio, connection_manager)
state_store        = GameStateStore()
phase_tasks: dict[str, asyncio.Task] = {}
background_tasks: list[asyncio.Task] = []
grace_tasks: dict[tuple[str, str], asyncio.Task] = {}  # (room_id, client_id) -> task
game_runtime: GameRuntime | None = None
lobby_runtime: LobbyRuntime | None = None

SWEEPER_INTERVAL = 2.0
ANTI_ENTROPY_INTERVAL = 10.0
GRACE_SECONDS = 20


def _domain_redis():
    if state_store._redis is None:
        raise RuntimeError("Game state store is not connected")
    return state_store._redis


def _cancel_phase_timer(room_id: str) -> None:
    task = phase_tasks.pop(room_id, None)
    if task is not None:
        task.cancel()


def _schedule_phase_timer(room_id: str, timer_end: float | None) -> None:
    _cancel_phase_timer(room_id)
    if timer_end is None:
        return

    async def _runner():
        try:
            await asyncio.sleep(max(0.0, timer_end - time.time()))

            # Il timer è scaduto: non è più "pendente". Ci togliamo da phase_tasks
            # PRIMA di avanzare. Se l'avanzamento porta a fine partita,
            # _emit_phase_outcome chiama _cancel_phase_timer: senza questa rimozione
            # cancellerebbe QUESTA stessa task, abortendo l'emissione di game_ended
            # (i client resterebbero bloccati sul timer a 0:00).
            if phase_tasks.get(room_id) is asyncio.current_task():
                phase_tasks.pop(room_id, None)

            # Guardia anti-timer-stale: i timer sono locali alla replica. Se la fase
            # è avanzata su un'ALTRA replica, il timer della fase precedente qui non
            # è stato cancellato e scadrebbe durante una fase successiva, avanzandola
            # in anticipo. Avanza solo se il deadline della fase CORRENTE è passato.
            from core import state_store as _rs
            current_timer_end = await _rs.get_timer_end(_domain_redis(), room_id)
            if current_timer_end is not None and time.time() < current_timer_end - 0.5:
                logger.debug(
                    "Timer stale ignorato | room=%s deadline_corrente=%s", room_id, current_timer_end
                )
                return

            if game_runtime is not None:
                await game_runtime.advance_phase_and_emit(room_id)
        except asyncio.CancelledError:
            logger.debug("Phase timer cancellato | room=%s", room_id)
            raise
        except Exception:
            logger.exception("Errore nel phase timer | room=%s", room_id)

    phase_tasks[room_id] = asyncio.create_task(_runner(), name=f"phase-timer:{room_id}")


async def _timer_sweeper_loop() -> None:
    """Recupera periodicamente i timer di fase scaduti (P7): rende la progressione
    della partita indipendente dalla replica che ha schedulato il timer."""
    while True:
        try:
            await asyncio.sleep(SWEEPER_INTERVAL)
            if game_runtime is not None:
                await game_runtime.recover_expired_timers()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Errore nello sweeper timer")


async def _anti_entropy_loop() -> None:
    """Ribroadcast periodico dello snapshot autoritativo (P9): fa convergere i
    client che hanno perso eventi Pub/Sub (at-most-once)."""
    while True:
        try:
            await asyncio.sleep(ANTI_ENTROPY_INTERVAL)
            if game_runtime is not None:
                await game_runtime.broadcast_state_snapshots()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Errore nell'anti-entropy")


# ── Lifespan FastAPI ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Backend avvio | instance_id=%s | env=%s", INSTANCE_ID, settings.app_env)
    await pubsub_manager.startup()
    await state_store.startup()
    background_tasks.append(asyncio.create_task(_timer_sweeper_loop(), name="timer-sweeper"))
    background_tasks.append(asyncio.create_task(_anti_entropy_loop(), name="anti-entropy"))
    yield
    logger.info("Backend spegnimento | instance_id=%s", INSTANCE_ID)
    for task in phase_tasks.values():
        task.cancel()
    for task in background_tasks:
        task.cancel()
    for task in grace_tasks.values():
        task.cancel()
    await asyncio.gather(*phase_tasks.values(), *background_tasks, *grace_tasks.values(), return_exceptions=True)
    phase_tasks.clear()
    background_tasks.clear()
    grace_tasks.clear()
    await pubsub_manager.shutdown()
    await state_store.shutdown()


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Game Backend",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_env == "development" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status":            "ok",
        "instance_id":       INSTANCE_ID,
        "active_rooms":      connection_manager.active_rooms(),
        "total_connections": connection_manager.client_count(),
    }


# ── Lobby browser ─────────────────────────────────────────────────────────────
@app.get("/api/lobbies")
async def list_lobbies():
    """Elenca le lobby aperte (fase LOBBY) per il browser di lobby lato client.

    Serve la lista a scorrimento con ricerca in HomeView: il frontend interroga
    questo endpoint invece di accedere direttamente a Redis. Ogni replica è
    stateless e legge lo stesso stato condiviso, quindi qualunque backend dietro
    il reverse proxy può rispondere.
    """
    try:
        lobbies = await state_store.list_open_rooms()
    except Exception:
        logger.exception("Errore nel recupero delle lobby aperte")
        return JSONResponse(
            status_code=500,
            content={"detail": "Impossibile recuperare le lobby disponibili"},
        )
    return {"lobbies": lobbies}


# ══════════════════════════════════════════════════════════════════════════════
# SOCKET.IO EVENT HANDLERS - VERSIONE AGGIORNATA
# ══════════════════════════════════════════════════════════════════════════════

@sio.event
async def connect(sid: str, environ: dict, auth: dict | None = None):
    """
    Triggered quando un client si connette.
    """
    client_id = None
    room_id   = None

    if auth:
        client_id = auth.get("client_id")
        room_id   = auth.get("room_id")

    if not client_id or not room_id:
        query = environ.get("QUERY_STRING", "")
        params = dict(p.split("=") for p in query.split("&") if "=" in p)
        client_id = client_id or params.get("client_id") or str(uuid.uuid4())
        room_id   = room_id   or params.get("room_id",   "lobby")

    # ──  CONTROLLI DI SICUREZZA ───────────────────────────────────────────
    
    phase = None
    current_state = await state_store.get_state(room_id)
    if current_state:
        phase = current_state.get("phase")
        
        # Controlliamo se il giocatore era già in questa partita (utile se ricarica la pagina dei risultati)
        existing_players = await state_store.get_players(room_id)
        is_known_player = any(p.get("player_id") == client_id for p in existing_players)

        # Se la fase non è la LOBBY iniziale
        if phase and phase != Phase.LOBBY.value:
            # Blocchiamo chiunque sia un giocatore "nuovo"
            if not is_known_player:
                if phase == Phase.ENDED.value:
                    logger.warning("Accesso negato: partita terminata, in attesa di riavvio | client=%s room=%s", client_id, room_id)
                    raise socketio.exceptions.ConnectionRefusedError("La partita è terminata. Attendi che l'host riavvii la lobby prima di entrare.")
                else:
                    logger.warning("Accesso negato: partita in corso | client=%s room=%s", client_id, room_id)
                    raise socketio.exceptions.ConnectionRefusedError("La partita è già in corso.")

    # 2. Controllo Nickname Duplicato (Anti-clone / Anti-doppia scheda)
    if connection_manager.client_connections_in_room(room_id, client_id) > 0:
        logger.warning("Accesso negato: nome in uso | client=%s room=%s", client_id, room_id)
        raise socketio.exceptions.ConnectionRefusedError(f"Il nome '{client_id}' è già in partita.")
        
    # ────────────────────────────────────────────────────────────────────────

    logger.info("connect | sid=%s client=%s room=%s instance=%s",
                sid[:8], client_id, room_id, INSTANCE_ID)

    await sio.enter_room(sid, room_id)
    connection_manager.connect(sid, room_id, client_id)
    await pubsub_manager.subscribe_room(room_id)

    # Leggi il flag connected PRIMA di ensure_domain_player per rilevare una riconnessione
    # su qualsiasi replica (il grace task potrebbe essere su un'altra istanza).
    _player_before = await get_player(_domain_redis(), room_id, client_id)
    _was_disconnected = (
        _player_before is not None
        and not _player_before.connected
        and phase is not None
        and phase != Phase.LOBBY.value
    )

    await ensure_domain_player(_domain_redis(), room_id, client_id)
    await state_store.add_player(room_id, client_id) # Aggiorna la cache preliminare
    
    # Generiamo lo snapshot definitivo della stanza 
    # e lo usiamo COME FONTE ASSOLUTA per i "pronto" e per l'ordinamento.
    current_state = await sync_lobby_room_state(_domain_redis(), state_store, room_id)

    unified_players = sorted(
        current_state.get("players", []),
        key=lambda p: (not p.get("is_host", False), str(p.get("username", "")).lower())
    )

    # Guardia anti-race: se Redis non ha ancora propagato il player (race condition
    # tra replica 1 e replica 2), aggiunge il giocatore manualmente alla lista locale
    # per evitare che il client riceva una game_state_sync senza la propria card.
    if not any(p.get("player_id") == client_id for p in unified_players):
        host_id = current_state.get("host_id")
        unified_players.insert(0, {
            "player_id": client_id,
            "username": client_id,
            "alive": True,
            "connected": True,
            "role": None,
            "is_host": (host_id == client_id) or len(unified_players) == 0,
            "ready": (host_id == client_id) or len(unified_players) == 0,
        })

    current_state["players"] = unified_players

    player = await get_player(_domain_redis(), room_id, client_id)
    personal_state = dict(current_state or {})
    if player and player.role:
        personal_state["myRole"] = player.role.value

    # Invia state sync al solo client che si (ri)connette
    sync_event = RedisEvent(
        event_type=EventType.GAME_STATE_SYNC,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"state": personal_state, "players": unified_players},
    )
    ws_msg = WSMessage.from_redis_event(sync_event)
    await sio.emit(EventType.GAME_STATE_SYNC, ws_msg.model_dump(), to=sid)

    if game_runtime:
        await game_runtime.emit_role_assignment_for_player(room_id, client_id, sid)

    # Cancella il grace task locale se presente (replica corrente)
    grace_key = (room_id, client_id)
    grace_task = grace_tasks.pop(grace_key, None)
    if grace_task is not None and not grace_task.done():
        grace_task.cancel()
        logger.info("Grace task cancellato: %s è rientrato | room=%s", client_id, room_id)

    # Emetti messaggio di rientro e GAME_RESUMED se il giocatore era marcato disconnesso
    # in Redis — gestisce sia la replica corrente che il caso cross-replica dove il
    # grace task è su un'altra istanza e non può essere cancellato localmente.
    if _was_disconnected:
        reconnect_name = player.username if player else client_id
        reconnect_msg = RedisEvent(
            event_type=EventType.CHAT_MESSAGE,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={
                "senderId": "system",
                "senderName": "Sistema",
                "text": f"✅ {reconnect_name} è rientrato nel villaggio! Il gioco riprende.",
                "channel": "global",
            },
        )
        await sio.emit(EventType.CHAT_MESSAGE, WSMessage.from_redis_event(reconnect_msg).model_dump(), room=room_id)
        await pubsub_manager.publish(reconnect_msg)

        resume_event = RedisEvent(
            event_type=EventType.GAME_RESUMED,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={"reason": "player_reconnected", "client_id": client_id},
        )
        await sio.emit(EventType.GAME_RESUMED, WSMessage.from_redis_event(resume_event).model_dump(), room=room_id)
        await pubsub_manager.publish(resume_event)

    # Notifica tutti gli altri client (locale + altre istanze via Redis)
    join_event = RedisEvent(
        event_type=EventType.PLAYER_JOINED,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"client_id": client_id, "player": asdict(player) if player else {}, "players": unified_players},
    )
    ws_msg = WSMessage.from_redis_event(join_event)
    
    await sio.emit(EventType.PLAYER_JOINED, ws_msg.model_dump(), room=room_id, skip_sid=sid)
    await pubsub_manager.publish(join_event)
    

@sio.event
async def disconnect(sid: str):
    """
    Triggered quando un client si disconnette.
    """
    room_id   = connection_manager.get_room_of(sid)
    client_id = connection_manager.get_client_id(sid)

    if not room_id:
        return

    logger.info("disconnect | sid=%s client=%s room=%s", sid[:8], client_id, room_id)

    connection_manager.disconnect(sid, room_id)
    if client_id and connection_manager.client_connections_in_room(room_id, client_id) > 0:
        logger.info(
            "client ancora connesso su un'altra socket | client=%s room=%s",
            client_id, room_id
        )
        return

    # ──  LOGICA DI ABBANDONO A PARTITA IN CORSO ──
    current_state = await state_store.get_state(room_id)
    phase = current_state.get("phase") if current_state else Phase.LOBBY.value
    is_midgame = phase not in (Phase.LOBBY.value, Phase.ENDED.value)

    leaving_player = await get_player(_domain_redis(), room_id, client_id or sid)
    player_name = leaving_player.username if leaving_player else (client_id or sid)

    if is_midgame and leaving_player and leaving_player.alive:
        # Avvia grace timer: il giocatore ha GRACE_SECONDS per rientrare
        # prima di essere eliminato dalla partita.
        grace_key = (room_id, client_id or sid)

        async def _grace_expired(r_id: str, c_id: str, p_name: str) -> None:
            await asyncio.sleep(GRACE_SECONDS)
            grace_tasks.pop((r_id, c_id), None)

            # Ricontrolla via Redis: se il giocatore è riconnesso su un'altra replica
            # il flag `connected` sarà True e non va eliminato.
            from core import state_store as rs
            player_now = await rs.get_player(_domain_redis(), r_id, c_id)
            if player_now is None or not player_now.alive:
                return
            if player_now.connected:
                logger.info("Grace scaduto ma %s è rientrato su altra replica, skip | room=%s", c_id, r_id)
                return

            logger.info("Grace scaduto: elimino %s | room=%s", c_id, r_id)

            # Elimina il giocatore
            if player_now:
                player_now.alive = False
                await rs.set_player(_domain_redis(), r_id, player_now)

            # Messaggio di fuga definitiva
            fled_msg = RedisEvent(
                event_type=EventType.CHAT_MESSAGE,
                room_id=r_id,
                sender_id=INSTANCE_ID,
                payload={
                    "senderId": "system",
                    "senderName": "Sistema",
                    "text": f"💨 {p_name} è fuggito definitivamente dal villaggio!",
                    "channel": "global",
                },
            )
            await sio.emit(EventType.CHAT_MESSAGE, WSMessage.from_redis_event(fled_msg).model_dump(), room=r_id)
            await pubsub_manager.publish(fled_msg)

            # Riprende il gioco (era in pausa)
            resume_event = RedisEvent(
                event_type=EventType.GAME_RESUMED,
                room_id=r_id,
                sender_id=INSTANCE_ID,
                payload={"reason": "grace_expired", "client_id": c_id},
            )
            await sio.emit(EventType.GAME_RESUMED, WSMessage.from_redis_event(resume_event).model_dump(), room=r_id)
            await pubsub_manager.publish(resume_event)

            # Controllo vittoria istantaneo
            from services.game_logic import check_winner, _end_game
            from models.events import PhaseChangedPayload, GameEndedPayload
            r = _domain_redis()
            winner = await check_winner(r, r_id)
            if winner:
                cs = await state_store.get_state(r_id)
                result = {}
                await _end_game(r, r_id, winner, (cs or {}).get("round", 0), result)

                # GAME_ENDED con final_players aggiornati (alive=False per chi è uscito)
                game_ended_payload = GameEndedPayload(
                    winner=winner.value,
                    reason="player_fled",
                    round=result.get("round", 0),
                    players=result.get("final_players", []),
                )
                ge_event = RedisEvent(event_type=EventType.GAME_ENDED, room_id=r_id, sender_id=INSTANCE_ID, payload=asdict(game_ended_payload))
                await sio.emit(EventType.GAME_ENDED, WSMessage.from_redis_event(ge_event).model_dump(), room=r_id)
                await pubsub_manager.publish(ge_event)

                phase_payload = PhaseChangedPayload(phase=Phase.ENDED.value, round=result.get("round", 0), timer_end=None)
                await _emit_authoritative_event(EventType.PHASE_CHANGED, r_id, phase_payload)
                _cancel_phase_timer(r_id)
            else:
                # Se siamo in NIGHT e il giocatore eliminato era wolf/seer,
                # potrebbe non essere rimasto nessuno da attendere → avanza subito.
                cs = await state_store.get_state(r_id)
                if cs and cs.get("phase") == Phase.NIGHT.value and game_runtime is not None:
                    await game_runtime._check_night_actions_complete(r_id)

        # Pausa il gioco e avvisa in chat
        pause_event = RedisEvent(
            event_type=EventType.GAME_PAUSED,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={"reason": "player_disconnected", "client_id": client_id, "grace_seconds": GRACE_SECONDS},
        )
        await sio.emit(EventType.GAME_PAUSED, WSMessage.from_redis_event(pause_event).model_dump(), room=room_id)
        await pubsub_manager.publish(pause_event)

        disc_msg = RedisEvent(
            event_type=EventType.CHAT_MESSAGE,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={
                "senderId": "system",
                "senderName": "Sistema",
                "text": f"⚠️ {player_name} si è disconnesso. Ha {GRACE_SECONDS} secondi per rientrare...",
                "channel": "global",
            },
        )
        await sio.emit(EventType.CHAT_MESSAGE, WSMessage.from_redis_event(disc_msg).model_dump(), room=room_id)
        await pubsub_manager.publish(disc_msg)

        grace_tasks[grace_key] = asyncio.create_task(
            _grace_expired(room_id, client_id or sid, player_name),
            name=f"grace:{room_id}:{client_id}",
        )

    # ── STANDARD DISCONNECT LOGIC (CON ELEZIONE HOST) ──
    await mark_player_disconnected(_domain_redis(), room_id, client_id or sid)
    
    if phase == Phase.LOBBY.value:
        remaining_raw = await state_store.remove_player(room_id, client_id or sid)
    else:
        remaining_raw = await state_store.set_player_disconnected(room_id, client_id or sid)
        
    # Non promuoviamo un nuovo host se c'è un grace timer attivo: il giocatore
    # potrebbe rientrare e ritrovare l'host al suo posto.
    grace_active = (room_id, client_id or sid) in grace_tasks
    if not grace_active:
        await promote_host_if_needed(_domain_redis(), room_id, remaining_raw)
    
    # Generiamo snapshot e ordiniamo per il LEAVE
    current_state = await sync_lobby_room_state(_domain_redis(), state_store, room_id)
    unified_players = sorted(
        current_state.get("players", []),
        key=lambda p: (not p.get("is_host", False), str(p.get("username", "")).lower())
    )

    leave_event = RedisEvent(
        event_type=EventType.PLAYER_LEFT,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"client_id": client_id, "player": asdict(leaving_player) if leaving_player else {}, "players": unified_players},
    )
    ws_msg = WSMessage.from_redis_event(leave_event)

    # Broadcast locale + Redis per altre istanze
    await sio.emit(EventType.PLAYER_LEFT, ws_msg.model_dump(), room=room_id)
    await pubsub_manager.publish(leave_event)

    if lobby_runtime is not None:
        await lobby_runtime.handle_disconnect(room_id, client_id or sid)

    if connection_manager.client_count(room_id) == 0:
        await pubsub_manager.unsubscribe_room(room_id)
        logger.info("Stanza vuota, unsubscribed da Redis | room=%s", room_id)
        
    # ── CONTROLLO VITTORIA ISTANTANEO (Se l'abbandono cambia le sorti) ──
    if is_midgame:
        from services.game_logic import check_winner, _end_game
        from models.events import PhaseChangedPayload
        r = _domain_redis()
        winner = await check_winner(r, room_id)
        if winner:
            logger.info("Vittoria istantanea causata da disconnessione | room=%s winner=%s", room_id, winner.value)
            result = {}
            await _end_game(r, room_id, winner, current_state.get("round", 0), result)
            
            # Spara il cambio fase a ENDED
            payload = PhaseChangedPayload(
                phase=Phase.ENDED.value,
                round=current_state.get("round", 0),
                timer_end=None
            )
            await _emit_authoritative_event(EventType.PHASE_CHANGED, room_id, payload)
            
            # Forza il sync dello stato ai client (per fargli vedere i ruoli/morti aggiornati)
            sync_state = await state_store.get_state(room_id)
            # Dobbiamo inviare i dati freschi ordinati
            sync_players = sorted(
                (await state_store.get_players(room_id)),
                key=lambda p: (not p.get("is_host", False), str(p.get("username", "")).lower())
            )
            sync_event = RedisEvent(
                event_type=EventType.GAME_STATE_SYNC,
                room_id=room_id,
                sender_id=INSTANCE_ID,
                payload={"state": sync_state or {}, "players": sync_players},
            )
            await sio.emit(EventType.GAME_STATE_SYNC, WSMessage.from_redis_event(sync_event).model_dump(), room=room_id)
            # Propaga anche alle altre repliche, altrimenti i client connessi a
            # un'altra istanza non vedono lo stato finale (ruoli/morti) aggiornato.
            await pubsub_manager.publish(sync_event)
            _cancel_phase_timer(room_id)


@sio.on("chat:message")
@sio.on(EventType.CHAT_MESSAGE.value)
async def handle_chat_message(sid: str, data: dict):
    room_id = connection_manager.get_room_of(sid)
    client_id = connection_manager.get_client_id(sid) or sid
    logger.info("chat_message received | sid=%s client=%s room=%s data=%s", sid[:8], client_id, room_id, data)

    if not room_id:
        logger.warning("Chat da sid senza room: %s", sid[:8])
        return

    payload = data if isinstance(data, dict) else {"raw": data}
    text = str(payload.get("text", "")).strip()
    if not text:
        return

    current_players = await state_store.get_players(room_id)
    sender = next(
        (player for player in current_players if player.get("player_id") == client_id),
        None,
    )

    redis_event = RedisEvent(
        event_type=EventType.CHAT_MESSAGE,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={
            "senderId": client_id,
            "senderName": sender.get("name", client_id) if sender else client_id,
            "text": text,
            "channel": payload.get("channel", "global"),
        },
    )

    ws_msg = WSMessage.from_redis_event(redis_event)
    out = ws_msg.model_dump()
    logger.info("chat_message emit | room=%s payload=%s", room_id, redis_event.payload)

    await sio.emit(EventType.CHAT_MESSAGE, out, room=room_id)
    WS_MESSAGES_SENT_TOTAL.labels(
        instance_id=INSTANCE_ID,
        event_type=EventType.CHAT_MESSAGE,
    ).inc()
    await pubsub_manager.publish(redis_event)


async def _emit_error(sid: str, room_id: str, message: str) -> None:
    await _emit_authoritative_event(
        EventType.ERROR,
        room_id,
        ErrorPayload(message=message),
        to=sid,
        publish=False,
    )


async def _emit_authoritative_event(
    event_type: EventType,
    room_id: str,
    payload: Any,
    *,
    to: str | None = None,
    publish: bool = True,
    target_client_id: str | None = None,
) -> None:
    serializable_payload = asdict(payload) if hasattr(payload, "__dataclass_fields__") else payload
    redis_event = RedisEvent(
        event_type=event_type,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload=serializable_payload,
        target_client_id=target_client_id,
    )
    ws_msg = WSMessage.from_redis_event(redis_event)
    out = ws_msg.model_dump()

    # ── Evento PRIVATO (unicast) recapitabile cross-replica ──────────────────
    # Consegna locale immediata se il destinatario è su questa replica (bassa
    # latenza) E pubblica su Redis così la replica che ospita davvero il client
    # possa recapitarlo. La dedup (sender_id) evita il doppio invio sull'origine.
    if target_client_id is not None:
        if to is not None:
            await sio.emit(event_type, out, to=to)
            WS_MESSAGES_SENT_TOTAL.labels(instance_id=INSTANCE_ID, event_type=event_type).inc()
        await pubsub_manager.publish(redis_event)
        return

    if to is None:
        start = time.perf_counter()
        await sio.emit(event_type, out, room=room_id)
        WS_BROADCAST_DURATION_SECONDS.labels(instance_id=INSTANCE_ID).observe(
            time.perf_counter() - start
        )
    else:
        await sio.emit(event_type, out, to=to)

    WS_MESSAGES_SENT_TOTAL.labels(instance_id=INSTANCE_ID, event_type=event_type).inc()

    if publish and to is None:
        await pubsub_manager.publish(redis_event)


game_runtime = GameRuntime(
    get_redis=_domain_redis,
    connection_manager=connection_manager,
    emit_authoritative_event=_emit_authoritative_event,
    sync_room_state=lambda room_id: sync_lobby_room_state(_domain_redis(), state_store, room_id),
    schedule_phase_timer=_schedule_phase_timer,
    cancel_phase_timer=_cancel_phase_timer,
)

lobby_runtime = LobbyRuntime(
    get_redis=_domain_redis,
    emit_authoritative_event=_emit_authoritative_event,
    sync_room_state=lambda room_id: sync_lobby_room_state(_domain_redis(), state_store, room_id),
)


async def _broadcast_passthrough(event: str, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
    redis_event = RedisEvent(
        event_type=event,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={**payload, "client_id": client_id},
    )

    if event in (EventType.GAME_STATE_SYNC, EventType.PLAYER_ACTION):
        await state_store.update_state(room_id, {
            "last_event": event,
            "last_client_id": client_id,
            **payload,
        })

    ws_msg = WSMessage.from_redis_event(redis_event)
    out = ws_msg.model_dump()
    await sio.emit(event, out, room=room_id)
    WS_MESSAGES_SENT_TOTAL.labels(instance_id=INSTANCE_ID, event_type=event).inc()
    await pubsub_manager.publish(redis_event)


@sio.on("*")
async def catch_all(event: str, sid: str, data: dict):
    """
    Handler generico per tutti gli altri eventi inviati dal client.
    """
    room_id   = connection_manager.get_room_of(sid)
    client_id = connection_manager.get_client_id(sid) or sid

    if not room_id:
        logger.warning("Evento '%s' da sid senza room: %s", event, sid[:8])
        return

    # Ignora eventi interni Socket.IO
    if event in ("connect", "disconnect", "connect_error"):
        return

    # La chat ha un handler dedicato: non va rilavorata nel catch-all
    if event in ("chat:message", EventType.CHAT_MESSAGE):
        return

    WS_MESSAGES_RECEIVED_TOTAL.labels(
        instance_id=INSTANCE_ID, event_type=event
    ).inc()
    WS_MESSAGE_SIZE_BYTES.labels(instance_id=INSTANCE_ID).observe(
        len(str(data).encode("utf-8")) if data is not None else 0
    )

    payload = data if isinstance(data, dict) else {"raw": data}
    try:
        # ===================================================================
        # BLOCCO ANTI-CHIUSURA FORZATA
        # ===================================================================
        if event in ("room_closed", "close_room"):
            logger.warning("Tentativo bloccato di chiudere forzatamente la stanza | room=%s client=%s", room_id, client_id)
            return

        # ===================================================================
        # KICK DI UN GIOCATORE (SOLO HOST)
        # ===================================================================
        if event == "kick_player":
            target_id = payload.get("target_id")
            if not target_id:
                return
            
            # Recuperiamo i dati della lobby come dizionario per leggere "is_host"
            current_players = await state_store.get_players(room_id)
            host_data = next((p for p in current_players if p.get("player_id") == client_id), None)
            
            # 1. Verifica che chi richiede il kick sia davvero l'host
            if not host_data or not (host_data.get("is_host") or host_data.get("isHost")):
                logger.warning("Tentativo di kick da non-host bloccato | client=%s room=%s", client_id, room_id)
                return
                
            logger.info("Host %s sta espellendo %s dalla stanza %s", client_id, target_id, room_id)
            
            r = _domain_redis()
            from core import state_store as rs
            
            # 2. DISINTEGRAZIONE TOTALE DAI DUE DATABASE
            await rs.delete_player(r, room_id, target_id) #Rimuove fisicamente dal DB profondo
            remaining_raw = await state_store.remove_player(room_id, target_id) #Rimuove dalla memoria in tempo reale della stanza
            
            #Generiamo snapshot e ordiniamo per il KICK
            current_state = await sync_lobby_room_state(r, state_store, room_id)
            unified_players = sorted(
                current_state.get("players", []),
                key=lambda p: (not p.get("is_host", False), str(p.get("username", "")).lower())
            )
            
            # 3. Aggiorna la lobby di tutti (così sparisce la sua carta immediatamente e ordinata)
            leave_event = RedisEvent(
                event_type=EventType.PLAYER_LEFT,
                room_id=room_id,
                sender_id=INSTANCE_ID,
                payload={"client_id": target_id, "player": {"player_id": target_id}, "players": unified_players},
            )
            await sio.emit(EventType.PLAYER_LEFT, WSMessage.from_redis_event(leave_event).model_dump(), room=room_id)
            await pubsub_manager.publish(leave_event)
            
            # 4. Spedisci l'evento "kicked" in broadcast a tutta la stanza.
            kick_payload = {"target_id": target_id, "reason": "Sei stato espulso dall'host."}
            
            await sio.emit("kicked", kick_payload, room=room_id)
            kick_redis_event = RedisEvent(
                event_type="kicked",
                room_id=room_id,
                sender_id=INSTANCE_ID,
                payload=kick_payload
            )
            await pubsub_manager.publish(kick_redis_event)
            return

        # ===================================================================
        # GESTIONE RIAVVIO PARTITA (Da ENDED a LOBBY)
        # ===================================================================
        if event in ("return_to_lobby", "play_again"):
            logger.info("L'host ha richiesto il riavvio della partita | room=%s", room_id)
            from core import state_store as rs  # Importiamo l'accesso diretto a Redis
            r = _domain_redis()
            
            # 1. Resetta lo stato della stanza a LOBBY e pulisci le variabili di fine round
            await rs.patch_game_state(
                r, room_id, 
                phase=Phase.LOBBY.value, 
                winner=None, 
                round=0, 
                timer_end=None,
                ready_player_ids=[] #  FORZA TUTTI A NON ESSERE PRONTI!
            )
            
            # 1.5. Ripulisci i fantasmi disconnessi DA ENTRAMBI I DATABASE!
            await rs.clean_disconnected_players(r, room_id) # Elimina dal DB profondo (Game Logic)
            await state_store.clean_disconnected_players(room_id) # Elimina dalla cache Socket.IO

            # NOTA: il cross-check con connection_manager.get_client_ids() è stato
            # rimosso perché con più repliche i giocatori connessi all'altra replica
            # risultano "ghost" localmente e venivano cancellati erroneamente.
            # rs.clean_disconnected_players() sopra usa il flag `connected` in Redis
            # che è corretto cross-replica e basta per rimuovere i ghost reali.
            
            # 2. Resuscita i giocatori e pulisci i loro ruoli/voti/stato pronti
            all_players = await rs.get_all_players(r, room_id)
            for p in all_players.values():
                p.alive = True
                p.role = None
                p.has_voted = False
                p.has_acted = False
                p.ready = False # Vogliamo che tutti debbano rimettere "pronto" manualmente
                await rs.set_player(r, room_id, p)
                
            # 3. Sincronizza la memoria locale e ordina
            current_state = await sync_lobby_room_state(r, state_store, room_id)
            unified_players = sorted(
                current_state.get("players", []),
                key=lambda p: (not p.get("is_host", False), str(p.get("username", "")).lower())
            )
            
            # 4. Manda lo State Sync a TUTTI per aggiornare le UI all'istante (con i fantasmi spariti)
            sync_event = RedisEvent(
                event_type=EventType.GAME_STATE_SYNC,
                room_id=room_id,
                sender_id=INSTANCE_ID,
                payload={"state": current_state or {}, "players": unified_players},
            )
            ws_msg = WSMessage.from_redis_event(sync_event)
            await sio.emit(EventType.GAME_STATE_SYNC, ws_msg.model_dump(), room=room_id)
            # Propaga anche alle ALTRE repliche: senza questo publish, i client
            # connessi a un'altra istanza non ricevono lo snapshot ripulito e
            # continuano a vedere il vecchio host (fantasma) nella lobby ricostruita.
            await pubsub_manager.publish(sync_event)
            return
        # ===================================================================

        if event == EventType.CAST_VOTE:
            await game_runtime.handle_cast_vote(room_id, client_id, payload)
            return
        if event == EventType.WOLF_VOTE:
            await game_runtime.handle_wolf_vote(sid, room_id, client_id, payload)
            return
        if event == EventType.SEER_ACTION:
            await game_runtime.handle_seer_action(sid, room_id, client_id, payload)
            return
        if event in (EventType.LOBBY_UPDATE_SETTINGS, "role_setup_updated"):
            await lobby_runtime.handle_update_settings(room_id, client_id, payload)
            return
        if event in (EventType.LOBBY_PLAYER_READY, "player_ready"):
            await lobby_runtime.handle_player_ready(room_id, client_id, payload)
            return
        if event in (EventType.GAME_START, "lobby:start_game", "start_game"):
            await lobby_runtime.validate_can_start_game(
                room_id,
                client_id,
                connection_manager.get_client_ids(room_id),
            )
            await game_runtime.handle_game_start(room_id)
            return
        if event in ("phase:advance", "game:advance_phase"):
            await game_runtime.handle_phase_advance(room_id)
            return
    except ValueError as exc:
        logger.info(
            "Rejected gameplay event | event=%s client=%s room=%s reason=%s",
            event, client_id, room_id, exc,
        )
        await _emit_error(sid, room_id, str(exc))
        return
    except Exception:
        logger.exception(
            "Errore nella gestione evento gameplay | event=%s client=%s room=%s",
            event, client_id, room_id,
        )
        await _emit_error(sid, room_id, f"Internal error while handling {event}")
        return

    # Se non è uno degli eventi coperti, facciamo semplicemente passthrough
    await _broadcast_passthrough(event, room_id, client_id, payload)


# ── App ASGI composta ─────────────────────────────────────────────────────────
asgi_app = socketio.ASGIApp(
    socketio_server=sio,
    other_asgi_app=app,
    socketio_path="socket.io",
)