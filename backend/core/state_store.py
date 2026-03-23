# ─────────────────────────────────────────────────────────────────────────────
# Persistenza dello stato di gioco su Redis.
# Usa Redis come key-value store (NON Pub/Sub) per mantenere lo snapshot
# corrente di ogni stanza, condiviso tra tutte le istanze backend.
#
# Questo permette a un client che si riconnette (anche su un'istanza diversa)
# di ricevere lo stato aggiornato senza che nessun'altra istanza debba
# rispondergli esplicitamente.
#
# Schema delle chiavi Redis:
#   game:state:<room_id>   → JSON dello snapshot corrente della stanza
#   game:players:<room_id> → SET dei client_id attualmente nella stanza
#
# TTL: 3600s — se una stanza è inattiva per 1 ora lo stato viene rimosso.
# ─────────────────────────────────────────────────────────────────────────────

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from core.config import get_settings

logger = logging.getLogger(__name__)

STATE_TTL = 3600  # secondi


class GameStateStore:
    """
    Interfaccia per leggere e scrivere lo stato di gioco su Redis.
    Usa una connessione separata dal PubSubManager (connessione normale,
    non dedicata a pub/sub).
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._redis: Optional[aioredis.Redis] = None

    async def startup(self) -> None:
        self._redis = aioredis.from_url(
            self._settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await self._redis.ping()
        logger.info("GameStateStore connesso a Redis")

    async def shutdown(self) -> None:
        if self._redis:
            await self._redis.aclose()

    # ── Chiavi ────────────────────────────────────────────────────────────────

    def _state_key(self, room_id: str) -> str:
        return f"{self._settings.redis_channel_prefix}:state:{room_id}"

    def _players_key(self, room_id: str) -> str:
        return f"{self._settings.redis_channel_prefix}:players:{room_id}"

    # ── Stato stanza ──────────────────────────────────────────────────────────

    async def get_state(self, room_id: str) -> Optional[dict[str, Any]]:
        """
        Restituisce lo snapshot corrente della stanza, o None se non esiste.
        Chiamato alla riconnessione di un client per inviargli lo stato attuale.
        """
        if not self._redis:
            return None
        raw = await self._redis.get(self._state_key(room_id))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Stato stanza corrotto per room=%s", room_id)
            return None

    async def update_state(self, room_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """
        Aggiorna lo stato della stanza con i campi in patch (merge superficiale).
        Restituisce lo stato aggiornato.
        Chiamato ogni volta che arriva un evento GAME_STATE_SYNC o PLAYER_ACTION.
        """
        if not self._redis:
            return patch

        current = await self.get_state(room_id) or {}
        current.update(patch)
        await self._redis.setex(
            self._state_key(room_id),
            STATE_TTL,
            json.dumps(current),
        )
        logger.debug(
            "Stato aggiornato | room=%s | keys=%s", room_id, list(patch.keys())
        )
        return current

    async def set_state(self, room_id: str, state: dict[str, Any]) -> None:
        """Sovrascrive completamente lo stato della stanza."""
        if not self._redis:
            return
        await self._redis.setex(
            self._state_key(room_id),
            STATE_TTL,
            json.dumps(state),
        )

    async def delete_state(self, room_id: str) -> None:
        """Rimuove lo stato della stanza (chiamato alla chiusura della stanza)."""
        if not self._redis:
            return
        await self._redis.delete(self._state_key(room_id), self._players_key(room_id))

    # ── Registro player ───────────────────────────────────────────────────────

    async def add_player(self, room_id: str, client_id: str) -> set[str]:
        """Aggiunge un player al registro della stanza. Restituisce il set aggiornato."""
        if not self._redis:
            return {client_id}
        await self._redis.sadd(self._players_key(room_id), client_id)
        await self._redis.expire(self._players_key(room_id), STATE_TTL)
        members = await self._redis.smembers(self._players_key(room_id))
        return set(members)

    async def remove_player(self, room_id: str, client_id: str) -> set[str]:
        """Rimuove un player dal registro. Restituisce il set aggiornato."""
        if not self._redis:
            return set()
        await self._redis.srem(self._players_key(room_id), client_id)
        members = await self._redis.smembers(self._players_key(room_id))
        return set(members)

    async def get_players(self, room_id: str) -> set[str]:
        """Restituisce i client_id registrati nella stanza."""
        if not self._redis:
            return set()
        members = await self._redis.smembers(self._players_key(room_id))
        return set(members)
