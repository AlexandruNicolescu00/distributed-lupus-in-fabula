import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from core.config import get_settings
from core.instance import INSTANCE_ID
from core.messages import ClientMessage, EventType, RedisEvent, WSMessage
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

# ── Istanze singleton ─────────────────────────────────────────────────────────
# Condivise per tutto il processo (un solo worker per container)

connection_manager = ConnectionManager()
pubsub_manager = PubSubManager(connection_manager)
state_store = GameStateStore()


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(
        "Backend avvio | instance_id=%s | env=%s", INSTANCE_ID, settings.app_env
    )
    await pubsub_manager.startup()
    await state_store.startup()
    yield
    # Shutdown
    logger.info("Backend spegnimento | instance_id=%s", INSTANCE_ID)
    await pubsub_manager.shutdown()
    await state_store.shutdown()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Game Backend",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_env == "development" else None,
)

# Prometheus: espone /metrics con metriche HTTP automatiche
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


# ── Health check ──────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "instance_id": INSTANCE_ID,
        "active_rooms": connection_manager.active_rooms(),
        "total_connections": connection_manager.client_count(),
    }


# ── WebSocket endpoint ────────────────────────────────────────────────────────


@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    """
    Endpoint WebSocket per una stanza di gioco.

    URL: ws://<host>/ws/<room_id>
    Query param opzionale: ?client_id=<uuid>  (se non fornito viene generato)

    Flusso:
    1. Connessione accettata e registrata nel ConnectionManager locale
    2. Questa istanza si sottoscrive al canale Redis della stanza (se non già fatto)
    3. Loop di ricezione messaggi dal client
    4. Ogni messaggio del client viene:
       a. inoltrato direttamente ai WS locali della stanza
       b. pubblicato su Redis → propagato alle altre istanze
    5. Alla disconnessione: cleanup locale + eventuale unsubscribe Redis
    """
    # Genera o accetta client_id
    client_id = websocket.query_params.get("client_id") or str(uuid.uuid4())

    logger.info(
        "Nuova connessione WS | room=%s client=%s instance=%s",
        room_id,
        client_id,
        INSTANCE_ID,
    )

    # 1. Accetta la connessione e registra nel manager locale
    await connection_manager.connect(websocket, room_id, client_id)

    # 2. Sottoscrivi questa istanza al canale Redis della stanza
    await pubsub_manager.subscribe_room(room_id)

    # 3. Registra il player e recupera lo stato corrente della stanza
    players = await state_store.add_player(room_id, client_id)
    current_state = await state_store.get_state(room_id)

    # 4. Invia subito lo stato corrente al client che si (ri)connette
    #    Questo garantisce che anche un client su un'istanza diversa riceva
    #    lo snapshot aggiornato indipendentemente dall'istanza precedente.
    if current_state is not None:
        sync_event = RedisEvent(
            event_type=EventType.GAME_STATE_SYNC,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={
                "state": current_state,
                "players": list(players),
            },
        )
        sync_msg = WSMessage.from_redis_event(sync_event)
        await connection_manager.send_to_client(
            sync_msg.model_dump_json(), room_id, client_id
        )

    # 5. Notifica gli altri client che è arrivato un giocatore
    join_event = RedisEvent(
        event_type=EventType.PLAYER_JOINED,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={"client_id": client_id, "players": list(players)},
    )
    # Invia direttamente ai client locali (escludendo il nuovo arrivato)
    ws_msg = WSMessage.from_redis_event(join_event)
    await connection_manager.broadcast_to_room(
        ws_msg.model_dump_json(), room_id, exclude_client=client_id
    )
    # Pubblica su Redis per le altre istanze
    await pubsub_manager.publish(join_event)

    # 4. Loop di ricezione
    try:
        while True:
            raw = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=settings.ws_heartbeat_interval * 2,
            )

            await _handle_client_message(raw, room_id, client_id)

    except asyncio.TimeoutError:
        # Client non ha inviato nulla entro il timeout — considerato disconnesso
        logger.info("Timeout WS | room=%s client=%s", room_id, client_id)

    except WebSocketDisconnect as exc:
        logger.info(
            "Client disconnesso | room=%s client=%s code=%s",
            room_id,
            client_id,
            exc.code,
        )

    except Exception as exc:
        logger.error("Errore WS | room=%s client=%s: %s", room_id, client_id, exc)

    finally:
        # 5. Cleanup
        connection_manager.disconnect(room_id, client_id)

        # Rimuovi player dal registro globale
        remaining = await state_store.remove_player(room_id, client_id)

        # Pubblica evento di uscita
        leave_event = RedisEvent(
            event_type=EventType.PLAYER_LEFT,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={"client_id": client_id, "players": list(remaining)},
        )
        # Broadcast locale ai client sulla stessa istanza — necessario perché
        # la deduplicazione in PubSubManager scarta i messaggi con sender_id
        # uguale a INSTANCE_ID, quindi i client locali non lo riceverebbero
        # mai tramite il canale Redis.
        leave_ws_msg = WSMessage.from_redis_event(leave_event)
        await connection_manager.broadcast_to_room(
            leave_ws_msg.model_dump_json(), room_id
        )
        # Pubblica su Redis per le istanze remote
        await pubsub_manager.publish(leave_event)

        # Se la stanza è ora vuota su questa istanza, annulla la sottoscrizione Redis
        if connection_manager.client_count(room_id) == 0:
            await pubsub_manager.unsubscribe_room(room_id)
            logger.info("Stanza vuota, unsubscribed da Redis | room=%s", room_id)


# ── Handler messaggi client ───────────────────────────────────────────────────


async def _handle_client_message(raw: str, room_id: str, client_id: str) -> None:
    """
    Processa un messaggio in arrivo dal client WebSocket.

    Strategia:
    - Valida il messaggio con Pydantic
    - Risponde a PING con PONG direttamente (nessun publish Redis)
    - Per tutti gli altri eventi: broadcast locale + publish Redis
    """
    try:
        client_msg = ClientMessage.model_validate_json(raw)
    except Exception as exc:
        logger.warning("Messaggio client non valido | client=%s: %s", client_id, exc)
        # Invia errore al solo client che ha mandato il messaggio malformato
        error_event = RedisEvent(
            event_type=EventType.ERROR,
            room_id=room_id,
            sender_id=INSTANCE_ID,
            payload={"detail": "Formato messaggio non valido", "error": str(exc)},
        )
        ws_msg = WSMessage.from_redis_event(error_event)
        await connection_manager.send_to_client(
            ws_msg.model_dump_json(), room_id, client_id
        )
        return

    # ── Ping/Pong (keepalive) ─────────────────────────────────────────────
    # Gestito localmente senza passare per Redis
    if client_msg.event_type == EventType.PONG:
        return  # Risposta a un nostro ping, nulla da fare

    # ── Tutti gli altri eventi ────────────────────────────────────────────
    event = RedisEvent(
        event_type=client_msg.event_type,
        room_id=room_id,
        sender_id=INSTANCE_ID,
        payload={**client_msg.payload, "client_id": client_id},
    )

    # Aggiorna lo stato persistente su Redis per gli eventi rilevanti.
    # Garantisce che i client che si riconnettono (anche su istanza diversa)
    # ricevano lo stato aggiornato tramite game_state_sync.
    if client_msg.event_type in (EventType.GAME_STATE_SYNC, EventType.PLAYER_ACTION):
        await state_store.update_state(
            room_id,
            {
                "last_event": client_msg.event_type,
                "last_client_id": client_id,
                **client_msg.payload,
            },
        )

    ws_msg = WSMessage.from_redis_event(event)
    serialized = ws_msg.model_dump_json()

    # Broadcast ai client locali della stanza (incluso il mittente per conferma)
    await connection_manager.broadcast_to_room(serialized, room_id)

    # Publish su Redis → le altre istanze inoltrano ai loro client
    await pubsub_manager.publish(event)

    logger.debug(
        "Messaggio gestito | room=%s client=%s event=%s",
        room_id,
        client_id,
        client_msg.event_type,
    )
