import redis.asyncio as aioredis

from core import state_store as rs
from core.state_store import GameStateStore
from models.events import GameStateSyncPayload, PlayerJoinedPayload, PlayerLeftPayload
from models.game import GameState, Phase, Player


def player_payload(player: Player, *, reveal_role: bool = True) -> dict[str, object]:
    return {
        "player_id": player.player_id,
        "username": player.username,
        "alive": player.alive,
        "connected": player.connected,
        "role": player.role.value if reveal_role and player.role else None,
    }


async def ensure_domain_player(
    redis: aioredis.Redis,
    room_id: str,
    client_id: str,
) -> Player:
    player = await rs.get_player(redis, room_id, client_id)
    if player is None:
        player = Player(player_id=client_id, username=client_id)
    player.connected = True
    await rs.set_player(redis, room_id, player)

    if await rs.get_game_state(redis, room_id) is None:
        await rs.set_game_state(redis, room_id, GameState(game_id=room_id))

    return player


async def mark_player_disconnected(
    redis: aioredis.Redis,
    room_id: str,
    client_id: str,
) -> None:
    player = await rs.get_player(redis, room_id, client_id)
    if player is None:
        return
    player.connected = False
    await rs.set_player(redis, room_id, player)


async def get_player(
    redis: aioredis.Redis,
    room_id: str,
    client_id: str,
) -> Player | None:
    return await rs.get_player(redis, room_id, client_id)


async def build_room_snapshot(redis: aioredis.Redis, room_id: str) -> dict:
    state = await rs.get_game_state(redis, room_id) or {}
    players = await rs.get_all_players(redis, room_id)
    return {
        "phase": state.get("phase", Phase.LOBBY.value),
        "round": state.get("round", 0),
        "winner": state.get("winner"),
        "timer_end": state.get("timer_end"),
        "paused": state.get("paused", False),
        "wolf_count": state.get("wolf_count"),
        "seer_count": state.get("seer_count"),
        "players": [
            {
                "player_id": p.player_id,
                "username": p.username,
                "role": p.role.value if p.role else None,
                "alive": p.alive,
                "connected": p.connected,
            }
            for p in players.values()
        ],
    }


async def sync_room_state(
    redis: aioredis.Redis,
    state_store: GameStateStore,
    room_id: str,
) -> dict:
    snapshot = await build_room_snapshot(redis, room_id)
    await state_store.set_state(room_id, snapshot)
    return snapshot


def build_state_sync_payload(current_state: dict, players: list[str]) -> GameStateSyncPayload:
    return GameStateSyncPayload(state=current_state, players=players)


def build_player_joined_payload(
    client_id: str,
    player: Player | None,
    players: list[str],
) -> PlayerJoinedPayload:
    return PlayerJoinedPayload(
        client_id=client_id,
        player=player_payload(player) if player is not None else None,
        players=players,
    )


def build_player_left_payload(
    client_id: str,
    player: Player | None,
    players: list[str],
) -> PlayerLeftPayload:
    return PlayerLeftPayload(
        client_id=client_id,
        player=player_payload(player, reveal_role=False) if player is not None else None,
        players=players,
    )
