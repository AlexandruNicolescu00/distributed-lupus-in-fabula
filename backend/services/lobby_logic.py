import redis.asyncio as aioredis

from core import state_store as rs
from core.state_store import GameStateStore
from models.events import (
    GameStateSyncPayload,
    LobbyPlayerReadyChangedPayload,
    LobbySettingsUpdatedPayload,
    PlayerJoinedPayload,
    PlayerLeftPayload,
    RoomClosedPayload,
)
from models.game import GameState, Phase, Player
from services.game_logic import _default_role_counts, _validate_role_counts


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

    state = await rs.get_game_state(redis, room_id)
    if state is None:
        await rs.set_game_state(redis, room_id, GameState(game_id=room_id, host_id=client_id))
    elif not state.get("host_id"):
        await rs.patch_game_state(redis, room_id, host_id=client_id)

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
    state = await rs.get_game_state(redis, room_id) or {}
    ready_player_ids = set(state.get("ready_player_ids", []))
    if client_id in ready_player_ids:
        ready_player_ids.discard(client_id)
        await rs.patch_game_state(redis, room_id, ready_player_ids=sorted(ready_player_ids))


async def promote_host_if_needed(
    redis: aioredis.Redis,
    room_id: str,
    players_snapshot: list[dict],
) -> str | None:
    state = await rs.get_game_state(redis, room_id) or {}
    current_host_id = state.get("host_id")

    if not players_snapshot:
        await rs.patch_game_state(redis, room_id, host_id=None, ready_player_ids=[])
        return None

    remaining_ids = [player.get("player_id") for player in players_snapshot if player.get("player_id")]
    if current_host_id in remaining_ids:
        return current_host_id

    promoted_host_id = players_snapshot[0].get("player_id")
    if not promoted_host_id:
        return None

    ready_player_ids = set(state.get("ready_player_ids", []))
    if current_host_id:
        ready_player_ids.discard(current_host_id)
    ready_player_ids.add(promoted_host_id)

    await rs.patch_game_state(
        redis,
        room_id,
        host_id=promoted_host_id,
        ready_player_ids=sorted(ready_player_ids),
    )
    return promoted_host_id


async def get_player(
    redis: aioredis.Redis,
    room_id: str,
    client_id: str,
) -> Player | None:
    return await rs.get_player(redis, room_id, client_id)


async def build_room_snapshot(redis: aioredis.Redis, room_id: str) -> dict:
    state = await rs.get_game_state(redis, room_id) or {}
    players = await rs.get_all_players(redis, room_id)
    host_id = state.get("host_id")
    ready_player_ids = state.get("ready_player_ids", [])
    phase = state.get("phase", Phase.LOBBY.value)
    reveal_roles = phase == Phase.ENDED.value
    return {
        "phase": phase,
        "round": state.get("round", 0),
        "winner": state.get("winner"),
        "timer_end": state.get("timer_end"),
        "paused": state.get("paused", False),
        "wolf_count": state.get("wolf_count"),
        "seer_count": state.get("seer_count"),
        "host_id": host_id,
        "ready_player_ids": ready_player_ids,
        "players": [
            {
                "player_id": p.player_id,
                "username": p.username,
                "role": p.role.value if reveal_roles and p.role else None,
                "alive": p.alive,
                "connected": p.connected,
                "is_host": p.player_id == host_id,
                "ready": p.player_id == host_id or p.player_id in ready_player_ids,
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


async def update_lobby_settings(
    redis: aioredis.Redis,
    room_id: str,
    client_id: str,
    *,
    wolf_count: int | None,
    seer_count: int | None,
) -> LobbySettingsUpdatedPayload:
    state = await rs.get_game_state(redis, room_id) or {}
    if state.get("phase", Phase.LOBBY.value) != Phase.LOBBY.value:
        raise ValueError("Lobby settings can only be changed during LOBBY")
    if state.get("host_id") != client_id:
        raise ValueError("Only the host can update lobby settings")

    players = await rs.get_all_players(redis, room_id)
    connected_count = sum(1 for player in players.values() if player.connected)
    current_wolf_count = state.get("wolf_count")
    current_seer_count = state.get("seer_count")
    default_wolf_count, default_seer_count = _default_role_counts(connected_count)
    resolved_wolf_count = default_wolf_count if current_wolf_count is None else current_wolf_count
    resolved_seer_count = default_seer_count if current_seer_count is None else current_seer_count
    resolved_wolf_count = resolved_wolf_count if wolf_count is None else wolf_count
    resolved_seer_count = resolved_seer_count if seer_count is None else seer_count

    _validate_role_counts(connected_count, resolved_wolf_count, resolved_seer_count)
    await rs.patch_game_state(
        redis,
        room_id,
        wolf_count=resolved_wolf_count,
        seer_count=resolved_seer_count,
    )
    return LobbySettingsUpdatedPayload(
        host_id=client_id,
        wolf_count=resolved_wolf_count,
        seer_count=resolved_seer_count,
    )


async def set_player_ready(
    redis: aioredis.Redis,
    room_id: str,
    client_id: str,
    *,
    ready: bool,
) -> LobbyPlayerReadyChangedPayload:
    state = await rs.get_game_state(redis, room_id) or {}
    if state.get("phase", Phase.LOBBY.value) != Phase.LOBBY.value:
        raise ValueError("Ready state can only be changed during LOBBY")

    player = await rs.get_player(redis, room_id, client_id)
    if player is None or not player.connected:
        raise ValueError("Only connected lobby players can update ready state")

    ready_player_ids = set(state.get("ready_player_ids", []))
    if ready:
        ready_player_ids.add(client_id)
    else:
        ready_player_ids.discard(client_id)

    resolved_ready_player_ids = sorted(ready_player_ids)
    await rs.patch_game_state(redis, room_id, ready_player_ids=resolved_ready_player_ids)
    return LobbyPlayerReadyChangedPayload(
        client_id=client_id,
        ready=ready,
        ready_player_ids=resolved_ready_player_ids,
    )


async def maybe_close_room_for_departing_host(
    redis: aioredis.Redis,
    room_id: str,
    client_id: str,
) -> RoomClosedPayload | None:
    return None
