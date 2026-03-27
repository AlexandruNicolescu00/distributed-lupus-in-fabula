# ─────────────────────────────────────────────────────────────────────────────
# Persistenza dello stato di gioco su Redis - VERSIONE AGGIORNATA
# Usa Redis Hash per memorizzare oggetti player completi (non solo ID)
# ─────────────────────────────────────────────────────────────────────────────

import json
import logging
import time
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

    def _now(self) -> float:
        return time.time()

    def _sort_players(self, players: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Ordina i player in modo deterministico.
        Priorita: ordine di ingresso in lobby, poi player_id come fallback.
        """
        return sorted(
            players,
            key=lambda player: (
                player.get("joined_at", float("inf")),
                player.get("player_id", ""),
            ),
        )

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

    # ── Registro player (AGGIORNATO) ──────────────────────────────────────────

    async def _get_players_list(self, room_id: str) -> list[dict[str, Any]]:
        """Helper interno: restituisce la lista completa di oggetti player."""
        if not self._redis:
            return []
        
        players_raw = await self._redis.hgetall(self._players_key(room_id))
        players = []
        
        for client_id, player_json in players_raw.items():
            try:
                player_obj = json.loads(player_json)
                players.append(player_obj)
            except json.JSONDecodeError:
                logger.warning(f"Player data corrotto per {client_id} in {room_id}")
                continue

        return self._sort_players(players)

    async def add_player(self, room_id: str, client_id: str) -> list[dict[str, Any]]:
        """
        Aggiunge un player al registro della stanza. 
        Restituisce la lista aggiornata di oggetti player completi.
        
        CAMBIAMENTO CHIAVE: ora restituisce list[dict] invece di set[str]
        """
        if not self._redis:
            return [{
                "player_id": client_id,
                "name": client_id,
                "ready": True,
                "is_host": True,
                "connected": True,
                "joined_at": self._now(),
            }]
        
        key = self._players_key(room_id)
        
        # Determina se questo player è l'host (primo ad entrare)
        current_count = await self._redis.hlen(key)
        is_host = (current_count == 0)
        
        # Crea l'oggetto player completo
        player_data = {
            "player_id": client_id,
            "name": client_id,
            "is_host": is_host,
            "ready": is_host,
            "connected": True,
            "joined_at": self._now(),
        }
        
        # Salva come JSON nella hash Redis
        await self._redis.hset(key, client_id, json.dumps(player_data))
        await self._redis.expire(key, STATE_TTL)
        
        # Restituisci tutti i player
        return await self._get_players_list(room_id)

    async def remove_player(self, room_id: str, client_id: str) -> list[dict[str, Any]]:
        """
        Rimuove un player dal registro. 
        Restituisce la lista aggiornata di oggetti player.
        
        CAMBIAMENTO CHIAVE: ora restituisce list[dict] invece di set[str]
        """
        if not self._redis:
            return []
        
        key = self._players_key(room_id)
        await self._redis.hdel(key, client_id)
        
        # Se era l'host, promuovi in modo deterministico il player entrato prima.
        players = await self._get_players_list(room_id)

        if not players:
            await self.delete_state(room_id)
            return []

        if players and not any(p.get("is_host") for p in players):
            promoted_player = self._sort_players(players)[0]
            promoted_player["is_host"] = True
            promoted_player["ready"] = True
            await self._redis.hset(
                key,
                promoted_player["player_id"],
                json.dumps(promoted_player)
            )
            players = await self._get_players_list(room_id)

        return players

    async def get_players(self, room_id: str) -> list[dict[str, Any]]:
        """
        Restituisce gli oggetti player completi della stanza.
        
        CAMBIAMENTO CHIAVE: ora restituisce list[dict] invece di set[str]
        """
        return await self._get_players_list(room_id)

    async def update_player_ready(self, room_id: str, client_id: str, ready: bool) -> Optional[dict[str, Any]]:
        """
        NUOVA FUNZIONE: Aggiorna lo stato ready di un player.
        Restituisce l'oggetto player aggiornato o None se non trovato.
        """
        if not self._redis:
            return None
        
        key = self._players_key(room_id)
        player_json = await self._redis.hget(key, client_id)
        
        if not player_json:
            logger.warning(f"Player {client_id} non trovato in {room_id}")
            return None
        
        try:
            player_data = json.loads(player_json)
            player_data["ready"] = True if player_data.get("is_host") else ready
            
            await self._redis.hset(key, client_id, json.dumps(player_data))
            await self._redis.expire(key, STATE_TTL)
            
            return player_data
        except json.JSONDecodeError:
            logger.error(f"Dati corrotti per player {client_id}")
            return None
