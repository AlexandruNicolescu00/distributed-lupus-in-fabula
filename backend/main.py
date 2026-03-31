# Entry point FastAPI + Socket.IO - VERSIONE AGGIORNATA
# Gestisce correttamente gli oggetti player completi invece di soli ID

import logging
import random
import uuid
from contextlib import asynccontextmanager

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
from pubsub.manager import PubSubManager
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


# ── Lifespan FastAPI ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Backend avvio | instance_id=%s | env=%s", INSTANCE_ID, settings.app_env)
    await pubsub_manager.startup()
    await state_store.startup()
    yield
    logger.info("Backend spegnimento | instance_id=%s", INSTANCE_ID)
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
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
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
    AGGIORNAMENTO: ora invia oggetti player completi invece di solo ID
    """
    # Estrai client_id e room_id da auth o query string
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

    logger.info("connect | sid=%s client=%s room=%s instance=%s",
                sid[:8], client_id, room_id, INSTANCE_ID)

    # Entra nella Socket.IO room
    await sio.enter_room(sid, room_id)

    # Registra nel ConnectionManager
    connection_manager.connect(sid, room_id, client_id)

    # Sottoscrivi questa istanza al canale Redis
    await pubsub_manager.subscribe_room(room_id)

    # ═══════════════════════════════════════════════════════════════════════
    # AGGIORNAMENTO PRINCIPALE: add_player ora restituisce list[dict]
    # ═══════════════════════════════════════════════════════════════════════
    players = await state_store.add_player(room_id, client_id)
    current_state = await state_store.get_state(room_id)

    # Invia sempre uno snapshot iniziale al client che si connette.
    # Anche senza stato partita, la lobby deve ricevere la lista players.
    sync_event = RedisEvent(
        event_type=EventType.GAME_STATE_SYNC,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"state": current_state or {}, "players": players},
    )
    ws_msg = WSMessage.from_redis_event(sync_event)
    await sio.emit(EventType.GAME_STATE_SYNC, ws_msg.model_dump(), to=sid)

    # Notifica tutti gli altri client (locale + altre istanze via Redis)
    join_event = RedisEvent(
        event_type=EventType.PLAYER_JOINED,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"client_id": client_id, "players": players},  # players è già list[dict]
    )
    ws_msg = WSMessage.from_redis_event(join_event)
    
    # Emetti nella room escludendo il nuovo arrivato (skip_sid)
    await sio.emit(EventType.PLAYER_JOINED, ws_msg.model_dump(),
                   room=room_id, skip_sid=sid)
    await pubsub_manager.publish(join_event)


@sio.event
async def disconnect(sid: str):
    """
    Triggered quando un client si disconnette.
    AGGIORNAMENTO: now invia oggetti player completi
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

    # ═══════════════════════════════════════════════════════════════════════
    # AGGIORNAMENTO: remove_player ora restituisce list[dict]
    # ═══════════════════════════════════════════════════════════════════════
    remaining = await state_store.remove_player(room_id, client_id or sid)

    leave_event = RedisEvent(
        event_type=EventType.PLAYER_LEFT,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"client_id": client_id, "players": remaining},  # remaining è list[dict]
    )
    ws_msg = WSMessage.from_redis_event(leave_event)

    # Broadcast locale + Redis per altre istanze
    await sio.emit(EventType.PLAYER_LEFT, ws_msg.model_dump(), room=room_id)
    await pubsub_manager.publish(leave_event)

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


@sio.on("*")
async def catch_all(event: str, sid: str, data: dict):
    """
    Handler generico per tutti gli altri eventi inviati dal client.
    
    AGGIORNAMENTO: gestisce correttamente 'player_ready' aggiornando Redis
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

    # ═══════════════════════════════════════════════════════════════════════
    # AGGIORNAMENTO: gestione speciale per 'player_ready'
    # ═══════════════════════════════════════════════════════════════════════
    if event == EventType.PLAYER_READY:
        ready_state = payload.get("ready", True)
        updated_player = await state_store.update_player_ready(room_id, client_id, ready_state)
        
        if updated_player:
            # Ottieni la lista aggiornata di tutti i player
            all_players = await state_store.get_players(room_id)
            
            redis_event = RedisEvent(
                event_type=event,
                room_id=room_id,
                sender_id=INSTANCE_ID,
                payload={
                    "client_id": client_id,
                    "ready": ready_state,
                    "players": all_players  # Invia sempre la lista completa
                },
            )
        else:
            # Fallback se il player non è stato trovato
            redis_event = RedisEvent(
                event_type=event,
                room_id=room_id,
                sender_id=INSTANCE_ID,
                payload={**payload, "client_id": client_id},
            )
    elif event == EventType.ROLE_SETUP_UPDATED:
        role_setup = payload.get("role_setup", {})
        await state_store.update_state(room_id, {"role_setup": role_setup})
        redis_event = RedisEvent(
            event_type=event,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={"client_id": client_id, "role_setup": role_setup},
        )
    elif event == EventType.GAME_START:
        current_players = await state_store.get_players(room_id)
        role_setup = _normalize_role_setup(len(current_players), payload.get("role_setup"))
        assigned_players = _assign_roles(current_players, role_setup, room_id)
        updated_state = await state_store.update_state(
            room_id,
            {
                "role_setup": role_setup,
                "phase": Phase.DAY.value,
                "round": 1,
                "players": assigned_players,
            },
        )
        redis_event = RedisEvent(
            event_type=event,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={
                **payload,
                "client_id": client_id,
                "role_setup": role_setup,
                "players": assigned_players,
                "state": updated_state,
            },
        )
    elif event == EventType.ROOM_CLOSED:
        await state_store.delete_state(room_id)
        redis_event = RedisEvent(
            event_type=event,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={
                "client_id": client_id,
                "reason": payload.get("reason", "L'host ha chiuso la lobby."),
            },
        )
    else:
        # Altri eventi (start_game, kick_player, ecc.)
        redis_event = RedisEvent(
            event_type=event,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={**payload, "client_id": client_id},
        )

    # Aggiorna stato persistente per eventi rilevanti
    if event in (EventType.GAME_STATE_SYNC, EventType.PLAYER_ACTION):
        await state_store.update_state(room_id, {
            "last_event":     event,
            "last_client_id": client_id,
            **payload,
        })

    ws_msg = WSMessage.from_redis_event(redis_event)
    out    = ws_msg.model_dump()

    # Broadcast a tutti i client della room (incluso mittente)
    await sio.emit(event, out, room=room_id)
    WS_MESSAGES_SENT_TOTAL.labels(instance_id=INSTANCE_ID, event_type=event).inc()

    # Pubblica su Redis per le altre istanze
    await pubsub_manager.publish(redis_event)


# ── App ASGI composta ─────────────────────────────────────────────────────────
asgi_app = socketio.ASGIApp(
    socketio_server=sio,
    other_asgi_app=app,
    socketio_path="socket.io",
)
