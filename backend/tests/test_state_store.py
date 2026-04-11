import fakeredis.aioredis as fakeredis
import pytest

from core.state_store import GameStateStore


@pytest.mark.asyncio
async def test_add_player_tracks_join_order_and_first_host(monkeypatch):
    timestamps = iter([100.0, 101.0, 102.0])

    store = GameStateStore()
    store._redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(store, "_now", lambda: next(timestamps))

    players = await store.add_player("room-1", "alice")
    players = await store.add_player("room-1", "bob")
    players = await store.add_player("room-1", "carol")

    assert [player["player_id"] for player in players] == ["alice", "bob", "carol"]
    assert players[0]["is_host"] is True
    assert players[0]["ready"] is True
    assert all(player["joined_at"] for player in players)


@pytest.mark.asyncio
async def test_remove_host_promotes_oldest_remaining_player(monkeypatch):
    timestamps = iter([100.0, 101.0, 102.0])

    store = GameStateStore()
    store._redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(store, "_now", lambda: next(timestamps))

    await store.add_player("room-2", "alice")
    await store.add_player("room-2", "bob")
    await store.add_player("room-2", "carol")

    remaining = await store.remove_player("room-2", "alice")

    assert [player["player_id"] for player in remaining] == ["bob", "carol"]
    assert remaining[0]["is_host"] is True
    assert remaining[0]["ready"] is True
    assert remaining[1]["is_host"] is False
