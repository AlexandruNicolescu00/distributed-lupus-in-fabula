# Gestisce tutte le connessioni WebSocket attive su QUESTA istanza backend.
# Non conosce le altre istanze — la propagazione inter-istanza è compito
# del PubSubManager (pubsub/manager.py).

import asyncio
import logging
import time
from collections import defaultdict

from fastapi import WebSocket

from core.metrics import (
    ACTIVE_PLAYERS,
    ACTIVE_ROOMS,
    WS_ACTIVE_CONNECTIONS,
    WS_BROADCAST_DURATION_SECONDS,
    WS_CONNECTIONS_TOTAL,
    WS_DISCONNECTIONS_TOTAL,
    WS_MESSAGES_SENT_TOTAL,
)
from pubsub.manager import INSTANCE_ID

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Registro in-memory delle connessioni WebSocket attive su questa istanza.

    Struttura:
        _rooms: { room_id → { client_id → WebSocket } }

    Thread-safety: asyncio è single-threaded per istanza, quindi non
    servono lock espliciti. Se si introduce un executor esterno, aggiungere asyncio.Lock.
    """

    def __init__(self) -> None:
        # room_id → { client_id → WebSocket }
        self._rooms: dict[str, dict[str, WebSocket]] = defaultdict(dict)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def connect(self, websocket: WebSocket, room_id: str, client_id: str) -> None:
        """Accetta la connessione e la registra nella stanza."""
        await websocket.accept()
        self._rooms[room_id][client_id] = websocket

        WS_CONNECTIONS_TOTAL.labels(instance_id=INSTANCE_ID).inc()
        self._update_gauges()

        logger.info(
            "Client connesso | room=%s client=%s | attivi in stanza: %d",
            room_id,
            client_id,
            len(self._rooms[room_id]),
        )

    def disconnect(self, room_id: str, client_id: str, reason: str = "normal") -> None:
        """Rimuove il client dal registro. Non chiude il socket (già chiuso da chi chiama)."""
        room = self._rooms.get(room_id, {})
        room.pop(client_id, None)
        if not room:
            self._rooms.pop(room_id, None)

        WS_DISCONNECTIONS_TOTAL.labels(instance_id=INSTANCE_ID, reason=reason).inc()
        self._update_gauges()

        logger.info(
            "Client disconnesso | room=%s client=%s reason=%s",
            room_id,
            client_id,
            reason,
        )

    def _update_gauges(self) -> None:
        """Aggiorna le gauge Prometheus con lo stato corrente."""
        total = self.client_count()
        rooms = len(self._rooms)
        WS_ACTIVE_CONNECTIONS.labels(instance_id=INSTANCE_ID).set(total)
        ACTIVE_PLAYERS.labels(instance_id=INSTANCE_ID).set(total)
        ACTIVE_ROOMS.labels(instance_id=INSTANCE_ID).set(rooms)

    # ── Invio messaggi ─────────────────────────────────────────────────────

    async def send_to_client(
        self,
        message: str,
        room_id: str,
        client_id: str,
        event_type: str = "unknown",
    ) -> bool:
        """
        Invia un messaggio a un singolo client.
        Restituisce True se riuscito, False se il client non è più connesso.
        """
        ws = self._rooms.get(room_id, {}).get(client_id)
        if ws is None:
            return False
        try:
            await ws.send_text(message)
            WS_MESSAGES_SENT_TOTAL.labels(
                instance_id=INSTANCE_ID, event_type=event_type
            ).inc()
            return True
        except Exception as exc:
            logger.warning("Errore invio a client %s: %s", client_id, exc)
            self.disconnect(room_id, client_id, reason="error")
            return False

    async def broadcast_to_room(
        self,
        message: str,
        room_id: str,
        exclude_client: str | None = None,
        event_type: str = "unknown",
    ) -> None:
        """
        Invia un messaggio a tutti i client della stanza su questa istanza.
        Usato dal PubSubManager quando riceve un evento da Redis.
        """
        clients = dict(self._rooms.get(room_id, {}))  # copia per iterazione sicura
        if not clients:
            return

        start = time.perf_counter()

        tasks = [
            self.send_to_client(message, room_id, cid, event_type=event_type)
            for cid in clients
            if cid != exclude_client
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        WS_BROADCAST_DURATION_SECONDS.labels(instance_id=INSTANCE_ID).observe(
            time.perf_counter() - start
        )

        failed = sum(1 for r in results if r is False or isinstance(r, Exception))
        if failed:
            logger.warning(
                "broadcast_to_room: %d invii falliti su %d | room=%s",
                failed,
                len(tasks),
                room_id,
            )

    async def broadcast_global(self, message: str, event_type: str = "unknown") -> None:
        """Invia un messaggio a tutti i client di tutte le stanze su questa istanza."""
        all_room_ids = list(self._rooms.keys())
        for room_id in all_room_ids:
            await self.broadcast_to_room(message, room_id, event_type=event_type)

    # ── Statistiche ────────────────────────────────────────────────────────

    def client_count(self, room_id: str | None = None) -> int:
        """Numero di client connessi — per una stanza o in totale."""
        if room_id:
            return len(self._rooms.get(room_id, {}))
        return sum(len(clients) for clients in self._rooms.values())

    def active_rooms(self) -> list[str]:
        """Lista degli ID delle stanze con almeno un client connesso."""
        return list(self._rooms.keys())
