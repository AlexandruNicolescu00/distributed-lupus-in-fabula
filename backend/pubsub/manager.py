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

from core.config import get_settings
from core.instance import INSTANCE_ID
from core.messages import RedisEvent, WSMessage
from core.metrics import (
    REDIS_MESSAGES_DEDUPLICATED_TOTAL,
    REDIS_MESSAGES_PUBLISHED_TOTAL,
    REDIS_MESSAGES_RECEIVED_TOTAL,
    REDIS_PUBLISH_DURATION_SECONDS,
)

logger = logging.getLogger(__name__)


class PubSubManager:
    """
    Gestisce la connessione Redis e il loop di ascolto Pub/Sub.
    Riceve il riferimento all'istanza socketio.AsyncServer per
    poter fare emit direttamente nelle room.
    """

    def __init__(self, sio) -> None:
        self._settings = get_settings()
        self._sio = sio   # socketio.AsyncServer

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

        start = time.perf_counter()
        await self._redis.publish(channel, payload)
        REDIS_PUBLISH_DURATION_SECONDS.labels(instance_id=INSTANCE_ID).observe(
            time.perf_counter() - start
        )
        REDIS_MESSAGES_PUBLISHED_TOTAL.labels(
            instance_id=INSTANCE_ID, channel=channel
        ).inc()
        logger.debug("Pubblicato su %s | event_type=%s", channel, event.event_type)

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
        if not self._pubsub:
            return
        logger.info("Listener loop avviato | instance=%s", INSTANCE_ID)
        while True:
            try:
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
            except Exception as exc:
                logger.error("Errore nel listener loop: %s", exc, exc_info=True)
                await asyncio.sleep(1.0)

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

        # Emetti nella room Socket.IO — raggiunge tutti i client connessi a questa stanza
        if channel == self._settings.redis_global_channel:
            # Broadcast globale: emetti a tutti i client connessi su questa istanza
            await self._sio.emit(event.event_type, data)
        else:
            # Emetti solo nella room specifica
            await self._sio.emit(event.event_type, data, room=event.room_id)

        logger.debug("Messaggio instradato | channel=%s event_type=%s room=%s",
                     channel, event.event_type, event.room_id)