# Questo file definisce il formato dei messaggi scambiati su Redis Pub/Sub
# e tra backend e client WebSocket.
#
# Una volta ricevuta la specifica dal Membro 1, aggiornare:
#   - EventType: aggiungere/rinominare i tipi di evento di gioco
#   - GameEvent.payload: adattare i campi al protocollo concordato
#
# Il formato attuale è un placeholder ragionevole.

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ── Tipi di evento ────────────────────────────────────────────────────────────


class EventType(StrEnum):
    # Gestione stanza
    PLAYER_JOINED = "player_joined"
    PLAYER_LEFT = "player_left"
    PLAYER_READY = "player_ready"
    ROLE_SETUP_UPDATED = "role_setup_updated"
    ROOM_CREATED = "room_created"
    ROOM_CLOSED = "room_closed"

    # Gameplay (placeholder — aggiornare con Membro 1)
    GAME_START = "start_game"
    GAME_END = "game_end"
    GAME_STATE_SYNC = "game_state_sync"  # snapshot completo stato di gioco
    PLAYER_ACTION = "player_action"  # azione di un giocatore
    CHAT_MESSAGE = "chat_message"

    # Sistema
    ERROR = "error"
    PONG = "pong"  # risposta al ping del client


# ── Messaggio pubblicato su Redis Pub/Sub ─────────────────────────────────────


class RedisEvent(BaseModel):
    """
    Formato del messaggio che viaggia su Redis tra le istanze backend.

    Struttura JSON su Redis:
    {
        "event_id":    "550e8400-e29b-41d4-a716-446655440000",
        "event_type":  "player_joined",
        "room_id":     "room_42",
        "sender_id":   "backend-instance-3",   <- istanza che ha pubblicato
        "timestamp":   1712345678.123,
        "payload":     { ... }
    }
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    room_id: str
    sender_id: str  # ID istanza backend (per deduplicazione)
    timestamp: float = Field(default_factory=time.time)
    payload: dict[str, Any] = Field(default_factory=dict)

    def channel(self, prefix: str) -> str:
        """Restituisce il nome del canale Redis per questo evento."""
        return f"{prefix}:{self.room_id}"


# ── Messaggio inviato al client WebSocket ─────────────────────────────────────


class WSMessage(BaseModel):
    """
    Formato del messaggio inviato dal backend al client WebSocket.
    Include instance_id per permettere ai client (e ai test) di sapere
    quale istanza backend ha processato l'evento.
    """

    event_id: str
    event_type: EventType
    room_id: str
    timestamp: float
    payload: dict[str, Any]
    instance_id: str = ""  # INSTANCE_ID del backend mittente

    @classmethod
    def from_redis_event(cls, event: RedisEvent) -> "WSMessage":
        return cls(
            event_id=event.event_id,
            event_type=event.event_type,
            room_id=event.room_id,
            timestamp=event.timestamp,
            payload=event.payload,
            instance_id=event.sender_id,
        )


# ── Messaggio ricevuto dal client WebSocket ───────────────────────────────────


class ClientMessage(BaseModel):
    """
    Formato del messaggio inviato dal client al backend via WebSocket.
    """

    event_type: EventType
    room_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
