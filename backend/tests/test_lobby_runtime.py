from types import SimpleNamespace

import pytest

from core.messages import EventType
from services.lobby_runtime import LobbyRuntime


@pytest.mark.asyncio
async def test_handle_player_ready_emits_ready_change_and_state_sync(monkeypatch):
    emitted = []

    async def fake_set_player_ready(redis, room_id, client_id, *, ready):
        return SimpleNamespace(client_id=client_id, ready=ready, ready_player_ids=["host1", client_id])

    async def fake_sync_room_state(room_id):
        return {
            "host_id": "host1",
            "ready_player_ids": ["host1", "guest1"],
            "players": [
                {"player_id": "host1", "username": "Host", "connected": True},
                {"player_id": "guest1", "username": "Guest", "connected": True},
            ],
        }

    async def fake_emit(event_type, room_id, payload, **kwargs):
        emitted.append((event_type, room_id, payload, kwargs))

    monkeypatch.setattr("services.lobby_runtime.set_player_ready", fake_set_player_ready)

    runtime = LobbyRuntime(
        get_redis=lambda: object(),
        emit_authoritative_event=fake_emit,
        sync_room_state=fake_sync_room_state,
    )

    await runtime.handle_player_ready("ROOM-1", "guest1", {"ready": True})

    assert emitted[0][0] == EventType.LOBBY_PLAYER_READY_CHANGED
    assert emitted[0][1] == "ROOM-1"
    assert emitted[0][2].ready_player_ids == ["host1", "guest1"]

    assert emitted[1][0] == EventType.GAME_STATE_SYNC
    assert emitted[1][1] == "ROOM-1"
    assert emitted[1][2].state["ready_player_ids"] == ["host1", "guest1"]
    assert emitted[1][2].players[1]["player_id"] == "guest1"


@pytest.mark.asyncio
async def test_validate_can_start_game_treats_host_as_implicitly_ready(monkeypatch):
    async def fake_get_game_state(redis, room_id):
        return {
            "phase": "LOBBY",
            "host_id": "host1",
            "ready_player_ids": ["guest1", "guest2"],
        }

    monkeypatch.setattr("services.lobby_runtime.rs.get_game_state", fake_get_game_state)

    runtime = LobbyRuntime(
        get_redis=lambda: object(),
        emit_authoritative_event=lambda *args, **kwargs: None,
        sync_room_state=lambda room_id: None,
    )

    await runtime.validate_can_start_game("ROOM-1", "host1", ["host1", "guest1", "guest2"])
