# Con Socket.IO le room sono gestite nativamente dal server.
# Questo modulo diventa un wrapper sottile che:
#   - mantiene il mapping sid → client_id (per logging e metriche)
#   - aggiorna le gauge Prometheus a ogni connect/disconnect
#   - espone client_count() e active_rooms() usati da /health e state_store
#
# L'invio dei messaggi avviene tramite sio.emit() direttamente in main.py
# e in pubsub/manager.py — non più tramite questo modulo.

import logging
from collections import defaultdict

from core.instance import INSTANCE_ID
from core.metrics import (
    ACTIVE_PLAYERS,
    ACTIVE_ROOMS,
    WS_ACTIVE_CONNECTIONS,
    WS_CONNECTIONS_TOTAL,
    WS_DISCONNECTIONS_TOTAL,
)

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Registro in-memory delle connessioni Socket.IO attive su questa istanza.

    Struttura:
        _rooms:   { room_id → set[sid] }
        _clients: { sid → client_id }
    """

    def __init__(self) -> None:
        self._rooms:   dict[str, set[str]] = defaultdict(set)
        self._clients: dict[str, str]      = {}   # sid → client_id

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self, sid: str, room_id: str, client_id: str) -> None:
        """Registra una nuova connessione Socket.IO."""
        self._rooms[room_id].add(sid)
        self._clients[sid] = client_id

        WS_CONNECTIONS_TOTAL.labels(instance_id=INSTANCE_ID).inc()
        self._update_gauges()

        logger.info("Client connesso | room=%s sid=%s client=%s | attivi: %d",
                    room_id, sid[:8], client_id, self.client_count())

    def disconnect(self, sid: str, room_id: str, reason: str = "normal") -> None:
        """Rimuove una connessione Socket.IO."""
        self._rooms[room_id].discard(sid)
        if not self._rooms[room_id]:
            del self._rooms[room_id]
        self._clients.pop(sid, None)

        WS_DISCONNECTIONS_TOTAL.labels(instance_id=INSTANCE_ID, reason=reason).inc()
        self._update_gauges()

        logger.info("Client disconnesso | room=%s sid=%s reason=%s",
                    room_id, sid[:8], reason)

    def _update_gauges(self) -> None:
        total = self.client_count()
        rooms = len(self._rooms)
        WS_ACTIVE_CONNECTIONS.labels(instance_id=INSTANCE_ID).set(total)
        ACTIVE_PLAYERS.labels(instance_id=INSTANCE_ID).set(total)
        ACTIVE_ROOMS.labels(instance_id=INSTANCE_ID).set(rooms)

    # ── Query ──────────────────────────────────────────────────────────────

    def get_room_of(self, sid: str) -> str | None:
        """Restituisce la room_id di un sid, o None se non trovato."""
        for room_id, sids in self._rooms.items():
            if sid in sids:
                return room_id
        return None

    def get_client_id(self, sid: str) -> str | None:
        return self._clients.get(sid)

    def client_connections_in_room(self, room_id: str, client_id: str) -> int:
        """Conta quante connessioni attive ha un client in una room su questa istanza."""
        return sum(
            1
            for sid in self._rooms.get(room_id, set())
            if self._clients.get(sid) == client_id
        )

    def get_sid(self, room_id: str, client_id: str) -> str | None:
        for sid in self._rooms.get(room_id, set()):
            if self._clients.get(sid) == client_id:
                return sid
        return None

    def get_client_ids(self, room_id: str) -> list[str]:
        return [
            self._clients[sid]
            for sid in self._rooms.get(room_id, set())
            if sid in self._clients
        ]

    def client_count(self, room_id: str | None = None) -> int:
        if room_id:
            return len(self._rooms.get(room_id, set()))
        return sum(len(sids) for sids in self._rooms.values())

    def active_rooms(self) -> list[str]:
        return list(self._rooms.keys())