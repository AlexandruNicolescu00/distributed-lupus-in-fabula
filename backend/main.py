# Entry point FastAPI + Socket.IO - VERSIONE AGGIORNATA
# Gestisce correttamente gli oggetti player completi invece di soli ID

import asyncio
import logging
import random
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
from core.metrics import WS_MESSAGES_RECEIVED_TOTAL, WS_MESSAGES_SENT_TOTAL
from core.state_store import GameStateStore
from models.game import Phase, Role
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
pubsub_manager     = PubSubManager(sio)
state_store        = GameStateStore()
phase_tasks: dict[str, asyncio.Task] = {}
game_runtime: GameRuntime | None = None
lobby_runtime: LobbyRuntime | None = None


def _normalize_role_setup(total_players: int, role_setup: dict | None) -> dict[str, int]:
    role_setup = role_setup or {}
    safe_total = max(0, total_players)
    seers = min(max(int(role_setup.get("seers", 0)), 0), 1 if safe_total >= 5 else 0)
    wolves = max(1, int(role_setup.get("wolves", 1)))

    wolves_cap = max(1, (max(safe_total - seers, 0) - 1) // 2)
    wolves = min(wolves, wolves_cap)

    villagers = max(0, safe_total - wolves - seers)

    while villagers <= wolves and wolves > 1:
      wolves -= 1
      villagers = max(0, safe_total - wolves - seers)

    while villagers <= wolves and seers > 0:
      seers -= 1
      villagers = max(0, safe_total - wolves - seers)

    return {"wolves": wolves, "seers": seers, "villagers": villagers}


def _assign_roles(players: list[dict], role_setup: dict, room_id: str) -> list[dict]:
    ordered_players = sorted(
        players,
        key=lambda player: (
            player.get("joined_at", float("inf")),
            player.get("player_id", ""),
        ),
    )
    normalized_setup = _normalize_role_setup(len(ordered_players), role_setup)
    role_pool = (
        [Role.WOLF.value] * normalized_setup["wolves"]
        + [Role.SEER.value] * normalized_setup["seers"]
        + [Role.VILLAGER.value] * normalized_setup["villagers"]
    )

    rng = random.Random(f"{room_id}:{'|'.join(player['player_id'] for player in ordered_players)}")
    rng.shuffle(role_pool)

    assigned_players = []
    for index, player in enumerate(ordered_players):
        assigned_players.append({
            **player,
            "role": role_pool[index] if index < len(role_pool) else Role.VILLAGER.value,
            "alive": player.get("alive", True),
        })

    return assigned_players

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
            if game_runtime is not None:
                await game_runtime.advance_phase_and_emit(room_id)
        except asyncio.CancelledError:
            logger.debug("Phase timer cancellato | room=%s", room_id)
            raise
        except Exception:
            logger.exception("Errore nel phase timer | room=%s", room_id)

    phase_tasks[room_id] = asyncio.create_task(_runner(), name=f"phase-timer:{room_id}")

# ── Lifespan FastAPI ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Backend avvio | instance_id=%s | env=%s", INSTANCE_ID, settings.app_env)
    await pubsub_manager.startup()
    await state_store.startup()
    yield
    logger.info("Backend spegnimento | instance_id=%s", INSTANCE_ID)
    for task in phase_tasks.values():
        task.cancel()
    await asyncio.gather(*phase_tasks.values(), return_exceptions=True)
    phase_tasks.clear()
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

    # ── 🛡️ CONTROLLI DI SICUREZZA ───────────────────────────────────────────
    
    # 1. Controllo Partita in Corso
    current_state = await state_store.get_state(room_id)
    if current_state:
        phase = current_state.get("phase")
        if phase and phase not in (Phase.LOBBY.value, Phase.ENDED.value):
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

    await ensure_domain_player(_domain_redis(), room_id, client_id)
    await sync_lobby_room_state(_domain_redis(), state_store, room_id)

    # Recupera stato persistente e lista player completa (list[dict])
    players       = await state_store.add_player(room_id, client_id)
    
    # Invia state sync al solo client che si (ri)connette
    sync_event = RedisEvent(
        event_type=EventType.GAME_STATE_SYNC,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"state": current_state or {}, "players": players},
    )
    ws_msg = WSMessage.from_redis_event(sync_event)
    await sio.emit(EventType.GAME_STATE_SYNC, ws_msg.model_dump(), to=sid)

    player = await get_player(_domain_redis(), room_id, client_id)
    if game_runtime:
        await game_runtime.emit_role_assignment_for_player(room_id, client_id, sid)

    # Notifica tutti gli altri client (locale + altre istanze via Redis)
    join_event = RedisEvent(
        event_type=EventType.PLAYER_JOINED,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"client_id": client_id, "player": asdict(player) if player else {}, "players": players},
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

    await mark_player_disconnected(_domain_redis(), room_id, client_id or sid)

    remaining = await state_store.remove_player(room_id, client_id or sid)
    await promote_host_if_needed(_domain_redis(), room_id, remaining)
    await sync_lobby_room_state(_domain_redis(), state_store, room_id)
    leaving_player = await get_player(_domain_redis(), room_id, client_id or sid)

    leave_event = RedisEvent(
        event_type=EventType.PLAYER_LEFT,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"client_id": client_id, "player": asdict(leaving_player) if leaving_player else {}, "players": remaining},
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
) -> None:
    serializable_payload = asdict(payload) if hasattr(payload, "__dataclass_fields__") else payload
    redis_event = RedisEvent(
        event_type=event_type,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload=serializable_payload,
    )
    ws_msg = WSMessage.from_redis_event(redis_event)
    out = ws_msg.model_dump()

    if to is None:
        await sio.emit(event_type, out, room=room_id)
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

    payload = data if isinstance(data, dict) else {"raw": data}
    try:
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
                timer_end=None
            )
            
            # 2. Resuscita i giocatori e pulisci i loro ruoli/voti/stato pronti
            all_players = await rs.get_all_players(r, room_id)
            for p in all_players.values():
                p.alive = True
                p.role = None
                p.has_voted = False
                p.has_acted = False
                p.ready = False # Vogliamo che tutti debbano rimettere "pronto" manualmente
                await rs.set_player(r, room_id, p)
                
            # 3. Sincronizza la memoria locale
            await sync_lobby_room_state(r, state_store, room_id)
            
            # 4. Manda lo State Sync a TUTTI per aggiornare le UI all'istante
            current_state = await state_store.get_state(room_id)
            players_list = await state_store.get_players(room_id)
            
            sync_event = RedisEvent(
                event_type=EventType.GAME_STATE_SYNC,
                room_id=room_id,
                sender_id=INSTANCE_ID,
                payload={"state": current_state or {}, "players": players_list},
            )
            ws_msg = WSMessage.from_redis_event(sync_event)
            await sio.emit(EventType.GAME_STATE_SYNC, ws_msg.model_dump(), room=room_id)
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