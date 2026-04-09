from types import SimpleNamespace

import fakeredis.aioredis as fakeredis
import pytest

from core import state_store as rs
from models.game import GameState, Phase, Player, Role
from services.lobby_logic import build_room_snapshot


@pytest.mark.asyncio
async def test_build_room_snapshot_hides_roles_before_game_end(monkeypatch):
    monkeypatch.setattr("core.state_store.get_settings", lambda: SimpleNamespace(redis_channel_prefix="test"))
    redis = fakeredis.FakeRedis(decode_responses=True)

    await rs.set_game_state(redis, "ROOM-1", GameState(game_id="ROOM-1", phase=Phase.NIGHT, host_id="p1"))
    await rs.set_player(redis, "ROOM-1", Player(player_id="p1", username="Alice", role=Role.WOLF))
    await rs.set_player(redis, "ROOM-1", Player(player_id="p2", username="Bob", role=Role.SEER))

    snapshot = await build_room_snapshot(redis, "ROOM-1")

    assert snapshot["players"][0]["role"] is None
    assert snapshot["players"][1]["role"] is None

    await redis.aclose()
