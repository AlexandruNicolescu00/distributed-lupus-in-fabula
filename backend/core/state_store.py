"""
Unified Redis state access layer.

This module now contains:
  - `GameStateStore`: room-oriented snapshot storage used by `main.py`
  - raw Redis helpers previously hosted in `services/redis_state.py`

The module therefore covers both realtime room sync and domain-level game
state persistence, while keeping the existing `GameStateStore` API intact.
"""

import json
import logging
import time
from dataclasses import asdict
from typing import Any, Optional

import redis.asyncio as aioredis

from core.config import get_settings
from models.game import GameState, Player, Role

logger = logging.getLogger(__name__)

STATE_TTL = 3600  # seconds


def _prefix() -> str:
    return get_settings().redis_channel_prefix


# ── Legacy room snapshot keys used by main.py ────────────────────────────────

def key_room_state(room_id: str) -> str:
    return f"{_prefix()}:state:{room_id}"


def key_room_players(room_id: str) -> str:
    return f"{_prefix()}:players:{room_id}"


# ── Domain game-state keys merged from services/redis_state.py ───────────────

def key_state(game_id: str) -> str:
    return f"{_prefix()}:{game_id}:state"


def key_players(game_id: str) -> str:
    return f"{_prefix()}:{game_id}:players"


def key_votes(game_id: str) -> str:
    return f"{_prefix()}:{game_id}:votes"


def key_wolf_votes(game_id: str) -> str:
    return f"{_prefix()}:{game_id}:wolf_votes"


def key_wolf_target(game_id: str) -> str:
    return f"{_prefix()}:{game_id}:wolf_target"


def key_seer_action(game_id: str) -> str:
    return f"{_prefix()}:{game_id}:seer_action"


def key_timer_end(game_id: str) -> str:
    return f"{_prefix()}:{game_id}:timer_end"


async def get_game_state(r: aioredis.Redis, game_id: str) -> Optional[dict]:
    raw = await r.get(key_state(game_id))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Corrupted game state for game_id=%s", game_id)
        return None


async def set_game_state(r: aioredis.Redis, game_id: str, state: GameState) -> None:
    data = {
        "game_id": state.game_id,
        "phase": state.phase.value,
        "round": state.round,
        "paused": state.paused,
        "winner": state.winner.value if state.winner else None,
        "timer_end": state.timer_end,
        "wolf_count": state.wolf_count,
        "seer_count": state.seer_count,
        "host_id": state.host_id,
        "ready_player_ids": state.ready_player_ids,
    }
    await r.setex(key_state(game_id), STATE_TTL, json.dumps(data))


async def patch_game_state(r: aioredis.Redis, game_id: str, **fields) -> None:
    current = await get_game_state(r, game_id) or {}
    for key, value in fields.items():
        current[key] = value.value if hasattr(value, "value") else value
    await r.setex(key_state(game_id), STATE_TTL, json.dumps(current))


def _player_to_json(player: Player) -> str:
    data = asdict(player)
    data["role"] = player.role.value if player.role else None
    return json.dumps(data)


def _player_from_json(raw: str) -> Player:
    data = json.loads(raw)
    role_raw = data.get("role")
    data["role"] = Role(role_raw) if role_raw else None
    return Player(**data)


async def get_player(r: aioredis.Redis, game_id: str, player_id: str) -> Optional[Player]:
    raw = await r.hget(key_players(game_id), player_id)
    if raw is None:
        return None
    try:
        return _player_from_json(raw)
    except Exception:
        logger.warning("Corrupted player data: game=%s player=%s", game_id, player_id)
        return None


async def set_player(r: aioredis.Redis, game_id: str, player: Player) -> None:
    await r.hset(key_players(game_id), player.player_id, _player_to_json(player))
    await r.expire(key_players(game_id), STATE_TTL)


async def get_all_players(r: aioredis.Redis, game_id: str) -> dict[str, Player]:
    raw_map = await r.hgetall(key_players(game_id))
    result: dict[str, Player] = {}
    for pid, raw in raw_map.items():
        try:
            result[pid] = _player_from_json(raw)
        except Exception:
            logger.warning("Skipping corrupted player: game=%s player=%s", game_id, pid)
    return result


async def delete_player(r: aioredis.Redis, game_id: str, player_id: str) -> None:
    """Rimuove un singolo giocatore dal database del dominio."""
    await r.hdel(key_players(game_id), player_id)


async def delete_players(r: aioredis.Redis, game_id: str) -> None:
    """Rimuove tutti i giocatori dal database del dominio."""
    await r.delete(key_players(game_id))


async def clean_disconnected_players(r: aioredis.Redis, game_id: str) -> None:
    """
    Rimuove fisicamente dal database i giocatori che si sono disconnessi.
    Utile a fine partita per ripulire la lobby dai 'fantasmi'.
    """
    players = await get_all_players(r, game_id)
    disconnected = [pid for pid, p in players.items() if not p.connected]
    if disconnected:
        await r.hdel(key_players(game_id), *disconnected)
        logger.info("Pulizia fantasmi completata | game=%s rimossi=%s", game_id, disconnected)


async def record_vote(r: aioredis.Redis, game_id: str, voter_id: str, target_id: str) -> None:
    await r.hset(key_votes(game_id), voter_id, target_id)
    await r.expire(key_votes(game_id), STATE_TTL)


async def get_votes(r: aioredis.Redis, game_id: str) -> dict[str, str]:
    return await r.hgetall(key_votes(game_id))


async def clear_votes(r: aioredis.Redis, game_id: str) -> None:
    await r.delete(key_votes(game_id))


async def record_wolf_vote(r: aioredis.Redis, game_id: str, wolf_id: str, target_id: str) -> None:
    await r.hset(key_wolf_votes(game_id), wolf_id, target_id)
    await r.expire(key_wolf_votes(game_id), STATE_TTL)


async def get_wolf_votes(r: aioredis.Redis, game_id: str) -> dict[str, str]:
    return await r.hgetall(key_wolf_votes(game_id))


async def clear_wolf_votes(r: aioredis.Redis, game_id: str) -> None:
    await r.delete(key_wolf_votes(game_id))
    await r.delete(key_wolf_target(game_id))


async def record_seer_action(r: aioredis.Redis, game_id: str, target_id: str) -> None:
    await r.setex(key_seer_action(game_id), STATE_TTL, target_id)


async def get_seer_action(r: aioredis.Redis, game_id: str) -> Optional[str]:
    return await r.get(key_seer_action(game_id))


async def clear_seer_action(r: aioredis.Redis, game_id: str) -> None:
    await r.delete(key_seer_action(game_id))


async def set_timer_end(r: aioredis.Redis, game_id: str, timer_end: float) -> None:
    await r.setex(key_timer_end(game_id), STATE_TTL, str(timer_end))


async def get_timer_end(r: aioredis.Redis, game_id: str) -> Optional[float]:
    raw = await r.get(key_timer_end(game_id))
    return float(raw) if raw else None


async def delete_game(r: aioredis.Redis, game_id: str) -> None:
    await r.delete(
        key_state(game_id),
        key_players(game_id),
        key_votes(game_id),
        key_wolf_votes(game_id),
        key_wolf_target(game_id),
        key_seer_action(game_id),
        key_timer_end(game_id),
    )
    logger.info("Deleted all Redis keys for game_id=%s", game_id)


class GameStateStore:
    """
    Room-oriented Redis snapshot store used by the websocket transport layer.
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

    def _state_key(self, room_id: str) -> str:
        return key_room_state(room_id)

    def _players_key(self, room_id: str) -> str:
        return key_room_players(room_id)

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
        if not self._redis:
            return patch
        current = await self.get_state(room_id) or {}
        current.update(patch)
        await self._redis.setex(self._state_key(room_id), STATE_TTL, json.dumps(current))
        logger.debug("Stato aggiornato | room=%s | keys=%s", room_id, list(patch.keys()))
        return current

    async def set_state(self, room_id: str, state: dict[str, Any]) -> None:
        if not self._redis:
            return
        await self._redis.setex(self._state_key(room_id), STATE_TTL, json.dumps(state))

    async def delete_state(self, room_id: str) -> None:
        if not self._redis:
            return
        await self._redis.delete(
            self._state_key(room_id), 
            self._players_key(room_id),
            key_state(room_id),
            key_players(room_id)
        )

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
        Rimuove un player dal registro e dal dominio del gioco. 
        Restituisce la lista aggiornata di oggetti player.
        """
        if not self._redis:
            return []
        
        # FIX: Rimozione da ENTRAMBI i registri
        await self._redis.hdel(self._players_key(room_id), client_id)
        await self._redis.hdel(key_players(room_id), client_id)
        
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
                self._players_key(room_id),
                promoted_player["player_id"],
                json.dumps(promoted_player)
            )
            players = await self._get_players_list(room_id)

        return players

    async def get_players(self, room_id: str) -> list[dict[str, Any]]:
        """
        Restituisce gli oggetti player completi della stanza.
        """
        return await self._get_players_list(room_id)

    async def update_player_ready(self, room_id: str, client_id: str, ready: bool) -> Optional[dict[str, Any]]:
        """
        Aggiorna lo stato ready di un player.
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