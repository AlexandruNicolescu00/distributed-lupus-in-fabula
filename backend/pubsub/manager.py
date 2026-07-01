# PubSubManager — invariato nella logica Redis Pub/Sub.
# Cambia solo il broadcast: invece di usare ConnectionManager.broadcast_to_room()
# usa sio.emit() direttamente, che gestisce l'invio tramite Socket.IO rooms.
#
# Flusso:
#   Redis PUBLISH → _listener_loop → _handle_message → sio.emit(room=room_id)

import asyncio
import logging
import time

import redis.asyncio as aioredis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError

from core.config import get_settings
from core.instance import INSTANCE_ID
from core.messages import RedisEvent, WSMessage
from core.metrics import (
    REDIS_MESSAGES_DEDUPLICATED_TOTAL,
    REDIS_MESSAGES_PUBLISHED_TOTAL,
    REDIS_MESSAGES_RECEIVED_TOTAL,
    REDIS_PUBLISH_DURATION_SECONDS,
    REDIS_PUBLISH_FAILURES_TOTAL,
    REDIS_RECONNECTS_TOTAL,
)

# Numero di tentativi e back-off iniziale per il PUBLISH (rif. teoria:
# features-design-distribuito §5 — heartbeat/timeout/retry con back-off).
_PUBLISH_MAX_RETRIES = 3
_PUBLISH_BASE_DELAY = 0.05  # secondi, raddoppia ad ogni tentativo
_RECONNECT_MAX_DELAY = 10.0  # secondi, cap del back-off di riconnessione

logger = logging.getLogger(__name__)


class PubSubManager:
    """
    Gestisce la connessione Redis e il loop di ascolto Pub/Sub.
    Riceve il riferimento all'istanza socketio.AsyncServer per
    poter fare emit direttamente nelle room.
    """

    def __init__(self, sio, connection_manager=None) -> None:
        self._settings = get_settings()
        self._sio = sio   # socketio.AsyncServer
        # Registro locale sid↔client per recapitare gli eventi privati (unicast)
        # provenienti da altre repliche alla socket giusta di QUESTA replica.
        self._connection_manager = connection_manager

        self._redis:    aioredis.Redis | None = None
        self._pubsub:   PubSub | None         = None
        self._listener_task: asyncio.Task | None = None
        self._subscribed_channels: set[str] = set()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def startup(self) -> None:
        logger.info("PubSubManager avvio | instance_id=%s | redis=%s",
                    INSTANCE_ID, self._settings.redis_url)

        self._redis = aioredis.from_url(
            self._settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
        )
        await self._redis.ping()
        logger.info("Connessione Redis stabilita")

        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await self._subscribe_channel(self._settings.redis_global_channel)

        self._listener_task = asyncio.create_task(
            self._listener_loop(), name="redis-pubsub-listener"
        )
        logger.info("Listener Redis Pub/Sub avviato")

    async def shutdown(self) -> None:
        logger.info("PubSubManager shutdown")
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.aclose()
        if self._redis:
            await self._redis.aclose()

    # ── Gestione canali ────────────────────────────────────────────────────

    async def subscribe_room(self, room_id: str) -> None:
        channel = f"{self._settings.redis_channel_prefix}:{room_id}"
        if channel not in self._subscribed_channels:
            await self._subscribe_channel(channel)

    async def unsubscribe_room(self, room_id: str) -> None:
        channel = f"{self._settings.redis_channel_prefix}:{room_id}"
        if channel in self._subscribed_channels and self._pubsub:
            await self._pubsub.unsubscribe(channel)
            self._subscribed_channels.discard(channel)
            logger.debug("Unsubscribed da canale: %s", channel)

    async def _subscribe_channel(self, channel: str) -> None:
        if self._pubsub and channel not in self._subscribed_channels:
            await self._pubsub.subscribe(channel)
            self._subscribed_channels.add(channel)
            logger.debug("Subscribed a canale: %s", channel)

    # ── Publish ────────────────────────────────────────────────────────────

    async def publish(self, event: RedisEvent) -> None:
        if not self._redis:
            return
        event.sender_id = INSTANCE_ID
        channel = event.channel(self._settings.redis_channel_prefix)
        payload = event.model_dump_json()

        # Retry con exponential back-off: Redis Pub/Sub è at-most-once e la rete
        # NON è affidabile (pitfalls). Senza retry un blip di Redis fa divergere
        # i client delle altre repliche (vedono l'evento solo localmente).
        delay = _PUBLISH_BASE_DELAY
        for attempt in range(1, _PUBLISH_MAX_RETRIES + 1):
            try:
                start = time.perf_counter()
                await self._redis.publish(channel, payload)
                REDIS_PUBLISH_DURATION_SECONDS.labels(instance_id=INSTANCE_ID).observe(
                    time.perf_counter() - start
                )
                REDIS_MESSAGES_PUBLISHED_TOTAL.labels(
                    instance_id=INSTANCE_ID, channel=channel
                ).inc()
                logger.debug("Pubblicato su %s | event_type=%s", channel, event.event_type)
                return
            except (RedisError, OSError) as exc:
                logger.warning(
                    "PUBLISH fallito (tentativo %d/%d) su %s: %s",
                    attempt, _PUBLISH_MAX_RETRIES, channel, exc,
                )
                if attempt < _PUBLISH_MAX_RETRIES:
                    await asyncio.sleep(delay)
                    delay *= 2

        # Esauriti i retry: l'evento cross-replica è perso. Lo registriamo come
        # metrica (observability) — la convergenza è demandata all'anti-entropy
        # periodico (snapshot) e al re-sync alla riconnessione del client.
        REDIS_PUBLISH_FAILURES_TOTAL.labels(instance_id=INSTANCE_ID).inc()
        logger.error(
            "PUBLISH DEFINITIVAMENTE fallito su %s | event_type=%s: evento perso",
            channel, event.event_type,
        )

    async def publish_global(self, event: RedisEvent) -> None:
        if not self._redis:
            return
        event.sender_id = INSTANCE_ID
        await self._redis.publish(
            self._settings.redis_global_channel,
            event.model_dump_json()
        )

    # ── Listener loop ──────────────────────────────────────────────────────

    async def _listener_loop(self) -> None:
        logger.info("Listener loop avviato | instance=%s", INSTANCE_ID)
        while True:
            try:
                if self._pubsub is None:
                    await self._reconnect()
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    await asyncio.sleep(0)
                    continue
                await self._handle_message(message)
            except asyncio.CancelledError:
                logger.info("Listener loop cancellato")
                break
            except (RedisError, OSError) as exc:
                # Connessione Redis caduta: ricostruisci connessione + pubsub e
                # RI-SOTTOSCRIVI tutti i canali. Senza questo, dopo un drop di
                # Redis il listener restava su una connessione morta e non
                # riceveva più alcun evento cross-replica.
                logger.error("Listener: errore connessione Redis, riconnetto: %s", exc)
                await self._reconnect()
            except Exception as exc:
                logger.error("Errore nel listener loop: %s", exc, exc_info=True)
                await asyncio.sleep(1.0)

    async def _reconnect(self) -> None:
        """Ricostruisce connessione Redis + PubSub e ri-sottoscrive i canali noti.
        Riprova all'infinito con exponential back-off (rif. teoria: retry/back-off,
        pitfalls 'the network is reliable')."""
        delay = 0.5
        while True:
            try:
                # Chiudi le risorse vecchie (best-effort)
                if self._pubsub is not None:
                    try:
                        await self._pubsub.aclose()
                    except Exception:
                        pass
                if self._redis is not None:
                    try:
                        await self._redis.aclose()
                    except Exception:
                        pass

                self._redis = aioredis.from_url(
                    self._settings.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    max_connections=10,
                )
                await self._redis.ping()
                self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)

                channels = list(self._subscribed_channels)
                self._subscribed_channels.clear()
                # Risottoscrivi sempre il canale globale + tutte le stanze note
                if self._settings.redis_global_channel not in channels:
                    channels.append(self._settings.redis_global_channel)
                for channel in channels:
                    await self._subscribe_channel(channel)

                REDIS_RECONNECTS_TOTAL.labels(instance_id=INSTANCE_ID).inc()
                logger.info(
                    "Riconnesso a Redis | canali ri-sottoscritti=%d", len(channels)
                )
                return
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError) as exc:
                logger.warning(
                    "Reconnect a Redis fallito, retry tra %.1fs: %s", delay, exc
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    async def _handle_message(self, message: dict) -> None:
        raw_data = message.get("data")
        channel  = message.get("channel", "")

        if not raw_data or not isinstance(raw_data, str):
            return

        try:
            event = RedisEvent.model_validate_json(raw_data)
        except Exception as exc:
            logger.warning("Messaggio Redis non valido su %s: %s", channel, exc)
            return

        # Deduplicazione
        if event.sender_id == INSTANCE_ID:
            REDIS_MESSAGES_DEDUPLICATED_TOTAL.labels(instance_id=INSTANCE_ID).inc()
            return

        REDIS_MESSAGES_RECEIVED_TOTAL.labels(
            instance_id=INSTANCE_ID, channel=channel
        ).inc()

        ws_message = WSMessage.from_redis_event(event)
        data = ws_message.model_dump()

        # Evento PRIVATO (unicast): consegna solo alla socket locale del
        # destinatario, se è connesso a QUESTA replica. Se non c'è, ignora —
        # NON fare broadcast nella room (l'evento è riservato a quel client).
        if event.target_client_id is not None:
            sid = None
            if self._connection_manager is not None:
                sid = self._connection_manager.get_sid(event.room_id, event.target_client_id)
            if sid is not None:
                await self._sio.emit(event.event_type, data, to=sid)
                logger.debug(
                    "Evento privato instradato | client=%s event_type=%s room=%s",
                    event.target_client_id, event.event_type, event.room_id,
                )
            return

        # Emetti nella room Socket.IO — raggiunge tutti i client connessi a questa stanza
        if channel == self._settings.redis_global_channel:
            # Broadcast globale: emetti a tutti i client connessi su questa istanza
            await self._sio.emit(event.event_type, data)
        else:
            # Emetti solo nella room specifica
            await self._sio.emit(event.event_type, data, room=event.room_id)

        logger.debug("Messaggio instradato | channel=%s event_type=%s room=%s",
                     channel, event.event_type, event.room_id)