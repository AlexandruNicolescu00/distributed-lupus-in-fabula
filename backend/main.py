# Entry point FastAPI + Socket.IO.
#
# Socket.IO viene montato come ASGI app separata e composta con FastAPI
# tramite socketio.ASGIApp. Uvicorn serve l'app composta.
#
# URL client:
#   Connessione:  http://game.local/socket.io/  (Socket.IO negozia il transport)
#   Evento emit:  socket.emit('player_action', { room_id, payload })

import logging
import uuid
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from core.config import get_settings
from core.instance import INSTANCE_ID
from core.messages import EventType, RedisEvent, WSMessage
from core.metrics import WS_MESSAGES_RECEIVED_TOTAL, WS_MESSAGES_SENT_TOTAL
from core.state_store import GameStateStore
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
# cors_allowed_origins="*" per sviluppo; restringere in produzione.
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
# SOCKET.IO EVENT HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@sio.event
async def connect(sid: str, environ: dict, auth: dict | None = None):
    """
    Triggered quando un client si connette.
    Il client deve inviare auth={client_id, room_id} oppure
    passarli come query params: /socket.io/?client_id=xxx&room_id=yyy
    """
    # Estrai client_id e room_id da auth o query string
    client_id = None
    room_id   = None

    if auth:
        client_id = auth.get("client_id")
        room_id   = auth.get("room_id")

    if not client_id or not room_id:
        # Fallback: leggi dalla query string WSGI
        query = environ.get("QUERY_STRING", "")
        params = dict(p.split("=") for p in query.split("&") if "=" in p)
        client_id = client_id or params.get("client_id") or str(uuid.uuid4())
        room_id   = room_id   or params.get("room_id",   "lobby")

    logger.info("connect | sid=%s client=%s room=%s instance=%s",
                sid[:8], client_id, room_id, INSTANCE_ID)

    # Entra nella Socket.IO room
    await sio.enter_room(sid, room_id)

    # Registra nel ConnectionManager (per metriche e health check)
    connection_manager.connect(sid, room_id, client_id)

    # Sottoscrivi questa istanza al canale Redis della stanza
    await pubsub_manager.subscribe_room(room_id)

    # Recupera stato persistente e lista player
    players       = await state_store.add_player(room_id, client_id)
    current_state = await state_store.get_state(room_id)

    # Invia state sync al solo client che si (ri)connette
    if current_state is not None:
        sync_event = RedisEvent(
            event_type=EventType.GAME_STATE_SYNC,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={"state": current_state, "players": list(players)},
        )
        ws_msg = WSMessage.from_redis_event(sync_event)
        await sio.emit(EventType.GAME_STATE_SYNC, ws_msg.model_dump(), to=sid)

    # Notifica tutti gli altri client (locale + altre istanze via Redis)
    join_event = RedisEvent(
        event_type=EventType.PLAYER_JOINED,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"client_id": client_id, "players": list(players)},
    )
    ws_msg = WSMessage.from_redis_event(join_event)
    # Emetti nella room escludendo il nuovo arrivato (skip_sid)
    await sio.emit(EventType.PLAYER_JOINED, ws_msg.model_dump(),
                   room=room_id, skip_sid=sid)
    await pubsub_manager.publish(join_event)


@sio.event
async def disconnect(sid: str):
    """Triggered quando un client si disconnette."""
    room_id   = connection_manager.get_room_of(sid)
    client_id = connection_manager.get_client_id(sid)

    if not room_id:
        return

    logger.info("disconnect | sid=%s client=%s room=%s", sid[:8], client_id, room_id)

    connection_manager.disconnect(sid, room_id)

    remaining = await state_store.remove_player(room_id, client_id or sid)

    leave_event = RedisEvent(
        event_type=EventType.PLAYER_LEFT,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"client_id": client_id, "players": list(remaining)},
    )
    ws_msg = WSMessage.from_redis_event(leave_event)

    # Broadcast locale + Redis per altre istanze
    await sio.emit(EventType.PLAYER_LEFT, ws_msg.model_dump(), room=room_id)
    await pubsub_manager.publish(leave_event)

    if connection_manager.client_count(room_id) == 0:
        await pubsub_manager.unsubscribe_room(room_id)
        logger.info("Stanza vuota, unsubscribed da Redis | room=%s", room_id)


@sio.on("*")
async def catch_all(event: str, sid: str, data: dict):
    """
    Handler generico per tutti gli altri eventi inviati dal client.
    Il client emette: socket.emit('player_action', { room_id, payload })
    """
    room_id   = connection_manager.get_room_of(sid)
    client_id = connection_manager.get_client_id(sid) or sid

    if not room_id:
        logger.warning("Evento '%s' da sid senza room: %s", event, sid[:8])
        return

    # Ignora eventi interni Socket.IO
    if event in ("connect", "disconnect", "connect_error"):
        return

    WS_MESSAGES_RECEIVED_TOTAL.labels(
        instance_id=INSTANCE_ID, event_type=event
    ).inc()

    payload = data if isinstance(data, dict) else {"raw": data}

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
# Socket.IO intercetta /socket.io/*, FastAPI gestisce tutto il resto.
asgi_app = socketio.ASGIApp(
    socketio_server=sio,
    other_asgi_app=app,
    socketio_path="socket.io",
)