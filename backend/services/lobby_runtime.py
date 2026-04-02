from typing import Any, Awaitable, Callable

from core import state_store as rs
from core.messages import EventType
from models.game import Phase
from services.lobby_logic import (
    maybe_close_room_for_departing_host,
    set_player_ready,
    update_lobby_settings,
)


class LobbyRuntime:
    def __init__(
        self,
        *,
        get_redis: Callable[[], Any],
        emit_authoritative_event: Callable[..., Awaitable[None]],
        sync_room_state: Callable[[str], Awaitable[dict]],
    ) -> None:
        self._get_redis = get_redis
        self._emit_authoritative_event = emit_authoritative_event
        self._sync_room_state = sync_room_state

    async def handle_update_settings(self, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
        wolf_count = payload.get("wolf_count")
        seer_count = payload.get("seer_count")
        if wolf_count is not None:
            wolf_count = int(wolf_count)
        if seer_count is not None:
            seer_count = int(seer_count)

        settings_payload = await update_lobby_settings(
            self._get_redis(),
            room_id,
            client_id,
            wolf_count=wolf_count,
            seer_count=seer_count,
        )
        await self._sync_room_state(room_id)
        await self._emit_authoritative_event(
            EventType.LOBBY_SETTINGS_UPDATED,
            room_id,
            settings_payload,
        )

    async def handle_player_ready(self, room_id: str, client_id: str, payload: dict[str, Any]) -> None:
        ready = payload.get("ready", True)
        ready_payload = await set_player_ready(
            self._get_redis(),
            room_id,
            client_id,
            ready=bool(ready),
        )
        await self._sync_room_state(room_id)
        await self._emit_authoritative_event(
            EventType.LOBBY_PLAYER_READY_CHANGED,
            room_id,
            ready_payload,
        )

    async def handle_disconnect(self, room_id: str, client_id: str) -> None:
        room_closed_payload = await maybe_close_room_for_departing_host(
            self._get_redis(),
            room_id,
            client_id,
        )
        if room_closed_payload is None:
            return
        await self._emit_authoritative_event(
            EventType.ROOM_CLOSED,
            room_id,
            room_closed_payload,
        )

    async def validate_can_start_game(self, room_id: str, client_id: str, connected_player_ids: list[str]) -> None:
        redis = self._get_redis()
        state = await rs.get_game_state(redis, room_id) or {}
        if state.get("phase", Phase.LOBBY.value) != Phase.LOBBY.value:
            raise ValueError("The game can only be started during LOBBY")
        if state.get("host_id") != client_id:
            raise ValueError("Only the host can start the game")

        ready_player_ids = set(state.get("ready_player_ids", []))
        missing_ready = [
            player_id
            for player_id in connected_player_ids
            if player_id not in ready_player_ids
        ]
        if missing_ready:
            raise ValueError("All connected players must be ready before starting the game")
