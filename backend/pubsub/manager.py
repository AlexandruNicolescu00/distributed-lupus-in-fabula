# Cuore dell'infrastruttura multi-istanza.
#
# Flusso publish (client → Redis → altre istanze):
#   Client WS ──► backend A ──► Redis PUBLISH game:room_42 ──► backend B, C...
#                                                               └─► WS clients di B, C
#
# Flusso subscribe (Redis → questa istanza → client WS locali):
#   Redis ──► listener task ──► ConnectionManager.broadcast_to_room()
#
# Deduplicazione: ogni istanza ha un INSTANCE_ID univoco.
# Quando backend A pubblica un evento, lo riceve anche lui stesso via Redis.
# Confrontando sender_id con INSTANCE_ID si evita il doppio invio ai client locali.

import asyncio
import json
import logging
import os
import time
import uuid

import redis.asyncio as aioredis
from redis.asyncio.client import PubSub

from core.config import get_settings
from core.messages import RedisEvent, WSMessage
from core.metrics import (
    REDIS_MESSAGES_DEDUPLICATED_TOTAL,
    REDIS_MESSAGES_PUBLISHED_TOTAL,
    REDIS_MESSAGES_RECEIVED_TOTAL,
    REDIS_PUBLISH_DURATION_SECONDS,
)
from websocket.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

# ID univoco di questa istanza — assegnato all'avvio del processo
INSTANCE_ID: str = os.environ.get("HOSTNAME", str(uuid.uuid4())[:8])


class PubSubManager:
    """
    Gestisce la connessione Redis e il loop di ascolto Pub/Sub.

    Lifecycle:
        startup()  → chiamato all'avvio FastAPI (lifespan)
        shutdown() → chiamato allo spegnimento FastAPI (lifespan)

    Dipende da ConnectionManager per recapitare i messaggi ai WS locali.
    """

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._settings = get_settings()
        self._cm = connection_manager

        self._redis: aioredis.Redis | None = None
        self._pubsub: PubSub | None = None
        self._listener_task: asyncio.Task | None = None

        # Canali sottoscritti: set di stringhe "prefix:room_id"
        self._subscribed_channels: set[str] = set()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """
        Crea il pool di connessioni Redis e avvia il task di ascolto.
        Chiamare nel lifespan FastAPI (startup).
        """
        logger.info(
            "PubSubManager avvio | instance_id=%s | redis=%s",
            INSTANCE_ID,
            self._settings.redis_url,
        )

        self._redis = aioredis.from_url(
            self._settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            # Pool separato per pub/sub (connessione dedicata, non condivisa)
            max_connections=10,
        )

        # Verifica connettività
        await self._redis.ping()
        logger.info("Connessione Redis stabilita")

        # Crea il client Pub/Sub
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)

        # Sottoscrivi subito al canale globale
        await self._subscribe_channel(self._settings.redis_global_channel)

        # Avvia il loop di ascolto come task asyncio in background
        self._listener_task = asyncio.create_task(
            self._listener_loop(),
            name="redis-pubsub-listener",
        )
        logger.info("Listener Redis Pub/Sub avviato")

    async def shutdown(self) -> None:
        """Termina il task di ascolto e chiude le connessioni Redis."""
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

        logger.info("PubSubManager spento correttamente")

    # ── Gestione canali ────────────────────────────────────────────────────

    async def subscribe_room(self, room_id: str) -> None:
        """
        Sottoscrive questa istanza al canale della stanza.
        Chiamato quando il primo client si connette a una stanza.
        """
        channel = f"{self._settings.redis_channel_prefix}:{room_id}"
        if channel not in self._subscribed_channels:
            await self._subscribe_channel(channel)

    async def unsubscribe_room(self, room_id: str) -> None:
        """
        Annulla la sottoscrizione al canale della stanza.
        Chiamato quando l'ultima connessione in una stanza viene chiusa.
        """
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
        """
        Pubblica un evento sul canale Redis della stanza.
        Inietta automaticamente INSTANCE_ID come sender_id.
        """
        if not self._redis:
            logger.error("publish() chiamato prima di startup()")
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

        logger.debug(
            "Pubblicato su %s | event_type=%s | event_id=%s",
            channel,
            event.event_type,
            event.event_id,
        )

    async def publish_global(self, event: RedisEvent) -> None:
        """Pubblica un evento sul canale globale (broadcast a tutti i client)."""
        if not self._redis:
            return
        event.sender_id = INSTANCE_ID
        payload = event.model_dump_json()
        await self._redis.publish(self._settings.redis_global_channel, payload)

    # ── Listener loop ──────────────────────────────────────────────────────

    async def _listener_loop(self) -> None:
        """
        Task asyncio in esecuzione continua.
        Legge i messaggi in arrivo da Redis e li instrada ai client WS locali.
        """
        if not self._pubsub:
            return

        logger.info("Listener loop avviato | instance=%s", INSTANCE_ID)

        while True:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )

                if message is None:
                    await asyncio.sleep(0)  # yield al loop asyncio
                    continue

                await self._handle_message(message)

            except asyncio.CancelledError:
                logger.info("Listener loop cancellato")
                break
            except Exception as exc:
                logger.error("Errore nel listener loop: %s", exc, exc_info=True)
                # Backoff prima di riprovare per evitare busy-loop su errore persistente
                await asyncio.sleep(1.0)

    async def _handle_message(self, message: dict) -> None:
        """
        Processa un singolo messaggio Redis:
        1. Deserializza il JSON
        2. Deduplica (scarta eventi generati da questa stessa istanza)
        3. Instrada ai client WS locali della stanza
        """
        raw_data = message.get("data")
        channel = message.get("channel", "")

        if not raw_data or not isinstance(raw_data, str):
            return

        try:
            event = RedisEvent.model_validate_json(raw_data)
        except Exception as exc:
            logger.warning(
                "Messaggio Redis non valido su %s: %s | data=%r",
                channel,
                exc,
                raw_data[:200],
            )
            return

        # ── Deduplicazione ─────────────────────────────────────────────────
        # L'istanza che ha pubblicato l'evento lo riceve anche lei da Redis.
        # Lo scarta per evitare di inviarlo due volte ai propri client
        # (il backend lo ha già inoltrato direttamente al momento del publish).
        if event.sender_id == INSTANCE_ID:
            REDIS_MESSAGES_DEDUPLICATED_TOTAL.labels(instance_id=INSTANCE_ID).inc()
            logger.debug(
                "Evento deduplicato (origine locale) | event_id=%s", event.event_id
            )
            return

        REDIS_MESSAGES_RECEIVED_TOTAL.labels(
            instance_id=INSTANCE_ID, channel=channel
        ).inc()

        # ── Instradamento ──────────────────────────────────────────────────
        ws_message = WSMessage.from_redis_event(event)
        serialized = ws_message.model_dump_json()

        if channel == self._settings.redis_global_channel:
            await self._cm.broadcast_global(serialized)
        else:
            await self._cm.broadcast_to_room(serialized, room_id=event.room_id)

        logger.debug(
            "Messaggio instradato | channel=%s event_type=%s room=%s",
            channel,
            event.event_type,
            event.room_id,
        )
