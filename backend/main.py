# Entry point FastAPI + Socket.IO.
#
# Socket.IO viene montato come ASGI app separata e composta con FastAPI
# tramite socketio.ASGIApp. Uvicorn serve l'app composta.
#
# URL client:
#   Connessione:  http://game.local/socket.io/  (Socket.IO negozia il transport)
#   Evento emit:  socket.emit('player_action', { room_id, payload })

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import socketio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from core.config import get_settings
from core.instance import INSTANCE_ID
from core.messages import EventType, RedisEvent, WSMessage
from core.metrics import WS_MESSAGES_RECEIVED_TOTAL, WS_MESSAGES_SENT_TOTAL
from core import state_store as rs
from core.state_store import GameStateStore
from models.events import GameEndedPayload, PlayerKilledPayload, SeerResultPayload
from models.game import GameState, Phase, Player, Role
from pubsub.manager import PubSubManager
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
phase_tasks: dict[str, asyncio.Task] = {}


def _domain_redis():
    if state_store._redis is None:
        raise RuntimeError("Game state store is not connected")
    return state_store._redis


async def _ensure_domain_player(room_id: str, client_id: str) -> Player:
    redis = _domain_redis()
    player = await rs.get_player(redis, room_id, client_id)
    if player is None:
        player = Player(player_id=client_id, username=client_id)
    player.connected = True
    await rs.set_player(redis, room_id, player)

    if await rs.get_game_state(redis, room_id) is None:
        await rs.set_game_state(redis, room_id, GameState(game_id=room_id))

    return player


async def _mark_player_disconnected(room_id: str, client_id: str) -> None:
    redis = _domain_redis()
    player = await rs.get_player(redis, room_id, client_id)
    if player is None:
        return
    player.connected = False
    await rs.set_player(redis, room_id, player)


async def _sync_room_state(room_id: str) -> None:
    redis = _domain_redis()
    state = await rs.get_game_state(redis, room_id) or {}
    players = await rs.get_all_players(redis, room_id)
    await state_store.set_state(
        room_id,
        {
            "phase": state.get("phase", Phase.LOBBY.value),
            "round": state.get("round", 0),
            "winner": state.get("winner"),
            "timer_end": state.get("timer_end"),
            "paused": state.get("paused", False),
            "wolf_count": state.get("wolf_count"),
            "seer_count": state.get("seer_count"),
            "players": [
                {
                    "player_id": p.player_id,
                    "username": p.username,
                    "role": p.role.value if p.role else None,
                    "alive": p.alive,
                    "connected": p.connected,
                }
                for p in players.values()
            ],
        },
    )


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
            await _advance_phase_and_emit(room_id)
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

    await _ensure_domain_player(room_id, client_id)
    await _sync_room_state(room_id)

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

    redis = _domain_redis()
    player = await rs.get_player(redis, room_id, client_id)
    if player is not None and player.role is not None:
        all_players = await rs.get_all_players(redis, room_id)
        assignment = {
            pid: p.role
            for pid, p in all_players.items()
            if p.role is not None
        }
        role_payload = build_role_payloads(assignment, all_players)[client_id]
        await _emit_authoritative_event(
            EventType.ROLE_ASSIGNED,
            room_id,
            role_payload,
            to=sid,
            publish=False,
        )

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
    await _mark_player_disconnected(room_id, client_id or sid)

    remaining = await state_store.remove_player(room_id, client_id or sid)
    await _sync_room_state(room_id)

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


async def _emit_error(sid: str, room_id: str, message: str) -> None:
    await sio.emit(
        EventType.ERROR,
        {
            "event_type": EventType.ERROR,
            "room_id": room_id,
            "timestamp": None,
            "payload": {"message": message},
            "instance_id": INSTANCE_ID,
        },
        to=sid,
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


async def _emit_role_assignments(room_id: str, payloads: dict[str, Any]) -> None:
    for client_id, payload in payloads.items():
        sid = connection_manager.get_sid(room_id, client_id)
        if sid is None:
            continue
        await _emit_authoritative_event(
            EventType.ROLE_ASSIGNED,
            room_id,
            payload,
            to=sid,
            publish=False,
        )


async def _emit_game_end(room_id: str, result: dict[str, Any]) -> None:
    winner = result.get("winner")
    payload = GameEndedPayload(
        winner=winner.value if hasattr(winner, "value") else str(winner),
        reason="all_wolves_dead" if getattr(winner, "value", str(winner)) == "VILLAGERS" else "wolves_parity",
        round=result.get("round", 0),
        players=result.get("final_players", []),
    )
    await _emit_authoritative_event(EventType.GAME_ENDED, room_id, payload)


async def _emit_night_resolution(room_id: str, result: dict[str, Any]) -> None:
    night_result = result.get("night_result")
    if not night_result:
        return

    killed_player_id = night_result.get("killed_player_id")
    if killed_player_id:
        victim = await rs.get_player(_domain_redis(), room_id, killed_player_id)
        if victim is not None:
            await _emit_authoritative_event(
                EventType.PLAYER_KILLED,
                room_id,
                PlayerKilledPayload(
                    player_id=victim.player_id,
                    username=victim.username,
                ),
            )

    seer_target_id = night_result.get("seer_target_id")
    seer_target_role = night_result.get("seer_target_role")
    if seer_target_id and seer_target_role:
        all_players = await rs.get_all_players(_domain_redis(), room_id)
        seer = next((p for p in all_players.values() if p.role and p.role.value == "SEER"), None)
        sid = connection_manager.get_sid(room_id, seer.player_id) if seer is not None else None
        if sid is not None:
            target = all_players.get(seer_target_id)
            await _emit_authoritative_event(
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


async def _emit_phase_outcome(room_id: str, result: dict[str, Any]) -> None:
    if result.get("eliminated_player") is not None:
        await _emit_authoritative_event(
            EventType.PLAYER_ELIMINATED,
            room_id,
            result["eliminated_player"],
        )

    if result.get("no_elimination") is not None:
        await _emit_authoritative_event(
            EventType.NO_ELIMINATION,
            room_id,
            result["no_elimination"],
        )

    await _emit_night_resolution(room_id, result)

    if result.get("winner") is not None:
        _cancel_phase_timer(room_id)
        await _emit_game_end(room_id, result)
        await _sync_room_state(room_id)
        return

    next_phase = result.get("next_phase")
    if next_phase is not None:
        await _emit_authoritative_event(
            EventType.PHASE_CHANGED,
            room_id,
            build_phase_changed_payload(
                phase=next_phase,
                round_number=result.get("round", 0),
                timer_end=result.get("timer_end"),
            ),
        )
        _schedule_phase_timer(room_id, result.get("timer_end"))

    await _sync_room_state(room_id)


async def _advance_phase_and_emit(room_id: str) -> None:
    result = await advance_phase(_domain_redis(), room_id)
    await _emit_phase_outcome(room_id, result)


async def _handle_cast_vote(room_id: str, client_id: str, payload: dict[str, Any]) -> None:
    target_id = payload.get("target_id")
    if not target_id:
        raise ValueError("Missing target_id for cast_vote")

    vote_update = await cast_vote(_domain_redis(), room_id, client_id, target_id)
    await _emit_authoritative_event(EventType.VOTE_UPDATE, room_id, vote_update)


async def _handle_wolf_vote(sid: str, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
    target_id = payload.get("target_id")
    if not target_id:
        raise ValueError("Missing target_id for wolf_vote")

    await record_wolf_vote(_domain_redis(), room_id, client_id, target_id)
    await _emit_authoritative_event(
        EventType.WOLF_VOTE,
        room_id,
        {"target_id": target_id, "accepted": True},
        to=sid,
        publish=False,
    )


async def _handle_seer_action(sid: str, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
    target_id = payload.get("target_id")
    if not target_id:
        raise ValueError("Missing target_id for seer_action")

    await record_seer_action(_domain_redis(), room_id, client_id, target_id)
    await _emit_authoritative_event(
        EventType.SEER_ACTION,
        room_id,
        {"target_id": target_id, "accepted": True},
        to=sid,
        publish=False,
    )


async def _handle_game_start(room_id: str, payload: dict[str, Any]) -> None:
    player_ids = connection_manager.get_client_ids(room_id)
    if len(player_ids) < 5:
        raise ValueError("Need at least 5 connected players to start the game")

    redis = _domain_redis()
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
    await _emit_role_assignments(room_id, build_role_payloads(assignment, players))

    timer_end = await set_phase(redis, room_id, Phase.DAY, round_number=1)
    await _sync_room_state(room_id)
    await _emit_authoritative_event(
        EventType.PHASE_CHANGED,
        room_id,
        build_phase_changed_payload(Phase.DAY, 1, timer_end),
    )
    _schedule_phase_timer(room_id, timer_end)


async def _handle_phase_advance(room_id: str) -> None:
    await _advance_phase_and_emit(room_id)


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
    try:
        if event == EventType.CAST_VOTE:
            await _handle_cast_vote(room_id, client_id, payload)
            return
        if event == EventType.WOLF_VOTE:
            await _handle_wolf_vote(sid, room_id, client_id, payload)
            return
        if event == EventType.SEER_ACTION:
            await _handle_seer_action(sid, room_id, client_id, payload)
            return
        if event in (EventType.GAME_START, "lobby:start_game"):
            await _handle_game_start(room_id, payload)
            return
        if event in ("phase:advance", "game:advance_phase"):
            await _handle_phase_advance(room_id)
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

    await _broadcast_passthrough(event, room_id, client_id, payload)


# ── App ASGI composta ─────────────────────────────────────────────────────────
# Socket.IO intercetta /socket.io/*, FastAPI gestisce tutto il resto.
asgi_app = socketio.ASGIApp(
    socketio_server=sio,
    other_asgi_app=app,
    socketio_path="socket.io",
)
