# Questo file definisce il formato dei messaggi scambiati su Redis Pub/Sub
# e tra backend e client WebSocket.
#
# Gli eventi gameplay sono allineati alle dataclass definite in
# `backend/models/events.py`. Manteniamo anche alcuni eventi di sistema/stanza
# già usati nel backend corrente.

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, is_dataclass
from enum import StrEnum
from typing import Any

from models.events import (
    CastVoteEvent,
    GameEndedPayload,
    GamePausedPayload,
    GameResumedPayload,
    NoEliminationPayload,
    PhaseChangedPayload,
    PlayerEliminatedPayload,
    PlayerKilledPayload,
    RoleAssignedPayload,
    SeerActionEvent,
    SeerResultPayload,
    VoteUpdatePayload,
    WolfVoteEvent,
)

from pydantic import BaseModel, Field


# ── Tipi di evento ────────────────────────────────────────────────────────────


class EventType(StrEnum):
    # Gestione stanza
    PLAYER_JOINED = "player_joined"
    PLAYER_LEFT = "player_left"
    ROOM_CREATED = "room_created"
    ROOM_CLOSED = "room_closed"

    # Gameplay
    GAME_START = "game_start"
    GAME_ENDED = "game_ended"
    GAME_STATE_SYNC = "game_state_sync"  # snapshot completo stato di gioco
    PLAYER_ACTION = "player_action"  # azione di un giocatore
    VOTE_UPDATE = "vote_update"
    PLAYER_ELIMINATED = "player_eliminated"
    PLAYER_KILLED = "player_killed"
    SEER_RESULT = "seer_result"
    GAME_PAUSED = "game_paused"
    GAME_RESUMED = "game_resumed"
    PHASE_CHANGED = "phase_changed"
    ROLE_ASSIGNED = "role_assigned"
    NO_ELIMINATION = "no_elimination"

    # Gameplay client -> server
    CAST_VOTE = "cast_vote"
    WOLF_VOTE = "wolf_vote"
    SEER_ACTION = "seer_action"

    # Sistema
    ERROR = "error"
    PONG = "pong"  # risposta al ping del client


SERVER_EVENT_PAYLOAD_TYPES: dict[EventType, type[Any]] = {
    EventType.VOTE_UPDATE: VoteUpdatePayload,
    EventType.PLAYER_ELIMINATED: PlayerEliminatedPayload,
    EventType.PLAYER_KILLED: PlayerKilledPayload,
    EventType.SEER_RESULT: SeerResultPayload,
    EventType.GAME_ENDED: GameEndedPayload,
    EventType.GAME_PAUSED: GamePausedPayload,
    EventType.GAME_RESUMED: GameResumedPayload,
    EventType.PHASE_CHANGED: PhaseChangedPayload,
    EventType.ROLE_ASSIGNED: RoleAssignedPayload,
    EventType.NO_ELIMINATION: NoEliminationPayload,
}

CLIENT_EVENT_PAYLOAD_TYPES: dict[EventType, type[Any]] = {
    EventType.CAST_VOTE: CastVoteEvent,
    EventType.WOLF_VOTE: WolfVoteEvent,
    EventType.SEER_ACTION: SeerActionEvent,
}


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

    @classmethod
    def from_payload(
        cls,
        *,
        event_type: EventType,
        room_id: str,
        sender_id: str,
        payload: Any,
    ) -> "RedisEvent":
        """Crea un RedisEvent accettando sia dict sia dataclass evento/payload."""
        if is_dataclass(payload):
            payload = asdict(payload)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict or dataclass instance")
        return cls(
            event_type=event_type,
            room_id=room_id,
            sender_id=sender_id,
            payload=payload,
        )


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
