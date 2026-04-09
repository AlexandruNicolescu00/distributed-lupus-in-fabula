from types import SimpleNamespace

import fakeredis.aioredis as fakeredis
import pytest

from core import state_store as rs
from models.game import GameState
from services.lobby_logic import promote_host_if_needed


@pytest.mark.asyncio
async def test_promote_host_if_needed_promotes_first_remaining_player(monkeypatch):
    monkeypatch.setattr("core.state_store.get_settings", lambda: SimpleNamespace(redis_channel_prefix="test"))
    redis = fakeredis.FakeRedis(decode_responses=True)

    await rs.set_game_state(
        redis,
        "ROOM-1",
        GameState(game_id="ROOM-1", host_id="host1", ready_player_ids=["host1", "guest2"]),
    )

    promoted_host = await promote_host_if_needed(
        redis,
        "ROOM-1",
        [
          {"player_id": "guest1", "is_host": True},
          {"player_id": "guest2", "is_host": False},
        ],
    )
    state = await rs.get_game_state(redis, "ROOM-1")

    assert promoted_host == "guest1"
    assert state["host_id"] == "guest1"
    assert state["ready_player_ids"] == ["guest1", "guest2"]

    await redis.aclose()
