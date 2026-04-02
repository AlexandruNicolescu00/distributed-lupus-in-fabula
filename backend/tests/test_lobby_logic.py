import pytest
import pytest_asyncio
import fakeredis.aioredis as fakeredis

from core import state_store as rs
from models.game import GameState, Phase
from services.lobby_logic import (
    ensure_domain_player,
    maybe_close_room_for_departing_host,
    set_player_ready,
    update_lobby_settings,
)

GAME_ID = "lobby-game"


@pytest_asyncio.fixture
async def r():
    client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_domain_player_sets_first_host(r):
    await ensure_domain_player(r, GAME_ID, "host1")
    state = await rs.get_game_state(r, GAME_ID)
    assert state is not None
    assert state["host_id"] == "host1"


@pytest.mark.asyncio
async def test_update_lobby_settings_uses_partial_updates_and_defaults(r):
    for pid in ["host1", "p2", "p3", "p4", "p5"]:
        await ensure_domain_player(r, GAME_ID, pid)

    payload = await update_lobby_settings(r, GAME_ID, "host1", wolf_count=2, seer_count=None)
    state = await rs.get_game_state(r, GAME_ID)

    assert payload.wolf_count == 2
    assert payload.seer_count == 1
    assert state["wolf_count"] == 2
    assert state["seer_count"] == 1


@pytest.mark.asyncio
async def test_update_lobby_settings_rejects_non_host(r):
    for pid in ["host1", "p2", "p3", "p4", "p5"]:
        await ensure_domain_player(r, GAME_ID, pid)

    with pytest.raises(ValueError, match="Only the host"):
        await update_lobby_settings(r, GAME_ID, "p2", wolf_count=2, seer_count=1)


@pytest.mark.asyncio
async def test_set_player_ready_updates_ready_ids(r):
    await ensure_domain_player(r, GAME_ID, "host1")
    payload = await set_player_ready(r, GAME_ID, "host1", ready=True)
    state = await rs.get_game_state(r, GAME_ID)

    assert payload.ready is True
    assert payload.ready_player_ids == ["host1"]
    assert state["ready_player_ids"] == ["host1"]


@pytest.mark.asyncio
async def test_host_disconnect_closes_room_only_in_lobby(r):
    await ensure_domain_player(r, GAME_ID, "host1")
    payload = await maybe_close_room_for_departing_host(r, GAME_ID, "host1")
    assert payload is not None
    assert payload.reason == "host_disconnected"

    await rs.set_game_state(r, GAME_ID, GameState(game_id=GAME_ID, phase=Phase.DAY, host_id="host1"))
    payload = await maybe_close_room_for_departing_host(r, GAME_ID, "host1")
    assert payload is None
