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
from redis.exceptions import WatchError

from core.config import get_settings
from models.game import GameState, Phase, Player, Role

logger = logging.getLogger(__name__)

# TTL dello stato di gioco. Rinnovato a ogni scrittura. Tenuto ampio per non far
# scadere una partita ATTIVA ma lunga / in fase di stallo (rif. teoria: reliability:
# evitare perdita silenziosa di stato). Per partite tipiche < 1h restava a rischio
# con 3600s; 6h dà margine senza rinunciare al GC delle stanze abbandonate.
STATE_TTL = 6 * 3600  # seconds (6h)


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


def key_active_rooms() -> str:
    """Set globale delle stanze con una partita in corso (per lo sweeper timer)."""
    return f"{_prefix()}:active_rooms"


def key_advance_lock(game_id: str) -> str:
    return f"{_prefix()}:{game_id}:advancing"


async def get_game_state(r: aioredis.Redis, game_id: str) -> Optional[dict]:
    raw = await r.get(key_state(game_id))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Corrupted game state for game_id=%s", game_id)
        return None


def _game_state_to_dict(state: GameState) -> dict:
    return {
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
        # Versione monotona dello stato: incrementata ad ogni patch atomica.
        # Serve come marcatore per l'anti-entropy / convergenza (rif. teoria:
        # eventual-consistency) e per il debug delle divergenze tra repliche.
        "state_version": 0,
    }


async def set_game_state(r: aioredis.Redis, game_id: str, state: GameState) -> None:
    await r.setex(key_state(game_id), STATE_TTL, json.dumps(_game_state_to_dict(state)))


async def create_game_state_if_absent(
    r: aioredis.Redis, game_id: str, host_id: str
) -> bool:
    """Crea lo stato iniziale in modo ATOMICO (SET ... NX EX).

    Ritorna True se lo stato è stato creato da QUESTA chiamata (→ il chiamante è
    l'host). Evita la race condition 'doppio host' quando due client entrano
    insieme in una stanza nuova gestiti da repliche diverse: senza atomicità
    entrambi leggono `state is None` e si auto-eleggono host.
    (Rif. teoria: consistency / atomic data object; SMR.)
    """
    data = _game_state_to_dict(GameState(game_id=game_id, host_id=host_id))
    created = await r.set(key_state(game_id), json.dumps(data), nx=True, ex=STATE_TTL)
    return bool(created)


async def patch_game_state(r: aioredis.Redis, game_id: str, **fields) -> None:
    """Patch ATOMICA dello stato via WATCH/MULTI con retry ottimistico.

    Il vecchio GET→modifica-in-Python→SETEX non era atomico tra repliche: due
    patch concorrenti (es. update_settings + ready) producevano lost update.
    Qui WATCH rileva la modifica concorrente e ri-tenta. Incrementa state_version.
    (Rif. teoria: consistency — lost update / read-modify-write non atomico.)
    """
    key = key_state(game_id)
    async with r.pipeline() as pipe:
        while True:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                current = json.loads(raw) if raw else {}
                for field, value in fields.items():
                    current[field] = value.value if hasattr(value, "value") else value
                current["state_version"] = int(current.get("state_version", 0)) + 1
                pipe.multi()
                pipe.setex(key, STATE_TTL, json.dumps(current))
                await pipe.execute()
                return
            except WatchError:
                # Un'altra replica ha modificato lo stato tra WATCH ed EXEC: ritenta.
                continue


# ── Stanze attive e lock di avanzamento fase (per lo sweeper) ────────────────

async def add_active_room(r: aioredis.Redis, game_id: str) -> None:
    await r.sadd(key_active_rooms(), game_id)


async def remove_active_room(r: aioredis.Redis, game_id: str) -> None:
    await r.srem(key_active_rooms(), game_id)


async def get_active_rooms(r: aioredis.Redis) -> list[str]:
    return list(await r.smembers(key_active_rooms()))


async def acquire_advance_lock(
    r: aioredis.Redis, game_id: str, owner: str, ttl: int = 10
) -> bool:
    """Lock NX che serializza l'avanzamento di fase tra trigger concorrenti e repliche.
    Rilasciare esplicitamente dopo l'uso via release_advance_lock.
    Il TTL (10s) è un fallback di sicurezza in caso di crash.
    """
    return bool(await r.set(key_advance_lock(game_id), owner, nx=True, ex=ttl))


async def release_advance_lock(r: aioredis.Redis, game_id: str, owner: str) -> None:
    """Rilascia il lock solo se siamo ancora i proprietari (script Lua atomico)."""
    _LUA_RELEASE = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""
    await r.eval(_LUA_RELEASE, 1, key_advance_lock(game_id), owner)


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


async def set_players_bulk(r: aioredis.Redis, game_id: str, players) -> None:
    """Persiste più player in un'unica pipeline (1 RTT invece di O(n) round-trip).
    Usato nei reset di fine fase e nell'assegnazione ruoli.
    (Rif. teoria: scalabilità — latency/round-trip hiding.)"""
    players = list(players)
    if not players:
        return
    pipe = r.pipeline(transaction=False)
    for player in players:
        pipe.hset(key_players(game_id), player.player_id, _player_to_json(player))
    pipe.expire(key_players(game_id), STATE_TTL)
    await pipe.execute()


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
        logger.info("Pulizia fantasmi completata nel dominio | game=%s rimossi=%s", game_id, disconnected)


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

    async def list_open_rooms(self) -> list[dict[str, Any]]:
        """Elenca le stanze attualmente joinabili (fase LOBBY) per il lobby browser.

        Il frontend NON accede a Redis: interroga questo metodo tramite l'endpoint
        REST `/api/lobbies`. Scandisce (SCAN, non KEYS, per non bloccare Redis) gli
        snapshot di stanza `{prefix}:state:{room_id}` e ritorna un riassunto leggero
        (codice, host, numero giocatori connessi) delle sole lobby non ancora avviate.
        """
        if not self._redis:
            return []

        prefix = key_room_state("")  # f"{prefix}:state:" — usato per estrarre il room_id
        pattern = key_room_state("*")
        rooms: list[dict[str, Any]] = []

        async for full_key in self._redis.scan_iter(match=pattern, count=100):
            raw = await self._redis.get(full_key)
            if not raw:
                continue
            try:
                snapshot = json.loads(raw)
            except json.JSONDecodeError:
                continue

            phase = snapshot.get("phase", Phase.LOBBY.value)

            # Le stanze terminate non sono mai visibili
            if phase == Phase.ENDED.value:
                continue

            all_players = snapshot.get("players", [])
            if not all_players:
                continue

            # Priorità ai giocatori connessi per host e conteggio, ma la stanza
            # rimane visibile anche se tutti sono temporaneamente disconnessi (grace period).
            connected = [p for p in all_players if p.get("connected", True)]
            display_players = connected if connected else all_players
            host = next((p for p in display_players if p.get("is_host")), display_players[0])
            rooms.append({
                "code": full_key[len(prefix):],
                "host": host.get("username") or host.get("player_id"),
                "player_count": len(connected),
                # "lobby" = in attesa, "in_game" = partita in corso (solo rientro)
                "status": "lobby" if phase == Phase.LOBBY.value else "in_game",
            })

        rooms.sort(key=lambda room: room["code"])
        return rooms

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

    # ── Registro player ───────────────────────────────────────────────────────

    async def _get_players_list(self, room_id: str) -> list[dict[str, Any]]:
        if not self._redis:
            return []
        
        players_raw = await self._redis.hgetall(self._players_key(room_id))
        players = []
        
        for client_id, player_json in players_raw.items():
            try:
                player_obj = json.loads(player_json)
                players.append(player_obj)
            except json.JSONDecodeError:
                continue

        return self._sort_players(players)

    async def add_player(self, room_id: str, client_id: str) -> list[dict[str, Any]]:
        if not self._redis:
            return []
        
        key = self._players_key(room_id)
        current_count = await self._redis.hlen(key)
        is_host = (current_count == 0)
        
        player_data = {
            "player_id": client_id,
            "name": client_id,
            "is_host": is_host,
            "ready": is_host,
            "connected": True,
            "joined_at": self._now(),
        }
        
        await self._redis.hset(key, client_id, json.dumps(player_data))
        await self._redis.expire(key, STATE_TTL)
        
        return await self._get_players_list(room_id)

    async def remove_player(self, room_id: str, client_id: str) -> list[dict[str, Any]]:
        if not self._redis:
            return []
        
        await self._redis.hdel(self._players_key(room_id), client_id)
        await self._redis.hdel(key_players(room_id), client_id)
        
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

    async def set_player_disconnected(self, room_id: str, client_id: str) -> list[dict[str, Any]]:
        if not self._redis:
            return []
        
        key = self._players_key(room_id)
        player_json = await self._redis.hget(key, client_id)
        
        if player_json:
            player_data = json.loads(player_json)
            player_data["connected"] = False
            was_host = player_data.get("is_host", False)
            
            if was_host:
                player_data["is_host"] = False
                
            await self._redis.hset(key, client_id, json.dumps(player_data))
            
            if was_host:
                players = await self._get_players_list(room_id)
                connected_players = [p for p in players if p.get("connected")]
                
                if connected_players:
                    new_host = self._sort_players(connected_players)[0]
                    new_host["is_host"] = True
                    new_host["ready"] = True
                    await self._redis.hset(key, new_host["player_id"], json.dumps(new_host))

        return await self._get_players_list(room_id)

    async def clean_disconnected_players(self, room_id: str) -> list[dict[str, Any]]:
        """
        Rimuove fisicamente dalla memoria WS i giocatori che si sono disconnessi.
        Fondamentale eseguirlo al momento di 'play_again' / 'return_to_lobby'.
        """
        if not self._redis:
            return []
        
        key = self._players_key(room_id)
        players_raw = await self._redis.hgetall(key)
        disconnected_ids = []
        
        for client_id, player_json in players_raw.items():
            try:
                player_data = json.loads(player_json)
                if not player_data.get("connected", True):
                    disconnected_ids.append(client_id)
            except json.JSONDecodeError:
                continue

        if disconnected_ids:
            await self._redis.hdel(key, *disconnected_ids)
            logger.info("GameStateStore: rimossi definitivamente i fantasmi %s in room %s", disconnected_ids, room_id)
            
        return await self._get_players_list(room_id)

    async def get_players(self, room_id: str) -> list[dict[str, Any]]:
        return await self._get_players_list(room_id)

    async def update_player_ready(self, room_id: str, client_id: str, ready: bool) -> Optional[dict[str, Any]]:
        if not self._redis:
            return None
        
        key = self._players_key(room_id)
        player_json = await self._redis.hget(key, client_id)
        
        if not player_json:
            return None
        
        try:
            player_data = json.loads(player_json)
            player_data["ready"] = True if player_data.get("is_host") else ready
            
            await self._redis.hset(key, client_id, json.dumps(player_data))
            await self._redis.expire(key, STATE_TTL)
            
            return player_data
        except json.JSONDecodeError:
            return None