"""
services/game_runtime.py

Manages the background tasks and timer loop for an active game room.

Responsibilities:
  - Phase auto-advance (the timer loop)
  - Delegating logic to game_logic.py
  - Emitting Socket.IO events to clients based on logic results
  - Checking win conditions and broadcasting game_ended
"""

import asyncio
import logging
import time

from core import state_store as rs
from models.events import (
    SeerResultPayload,
    PlayerKilledPayload,
    GameEndedPayload,
    VoteUpdatePayload,
)
from models.game import Phase
from pubsub.manager import PubSubManager
from services import game_logic

logger = logging.getLogger(__name__)


class GameRuntime:
    def __init__(self, room_id: str, pubsub: PubSubManager, get_redis=None):
        self.room_id = room_id
        self.pubsub = pubsub
        self.r = pubsub.r
        self.get_redis = get_redis
        self._task: asyncio.Task | None = None
        self._running = False
        self._loop_trigger = asyncio.Event()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Starts the game loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=f"game_loop_{self.room_id}")
        logger.info("Game loop started | room=%s", self.room_id)

    async def stop(self) -> None:
        """Stops the game loop immediately."""
        self._running = False
        self._loop_trigger.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Game loop stopped | room=%s", self.room_id)

    def _trigger_advance(self) -> None:
        """Wakes up the loop immediately (used to skip timer when everyone voted)."""
        self._loop_trigger.set()

    # ── The Timer Loop ────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """
        Background loop: wakes up when timer_end expires or when explicitly triggered.
        Calls game_logic.advance_phase() and emits the results.
        """
        while self._running:
            try:
                state = await rs.get_game_state(self.r, self.room_id)
                if not state:
                    logger.warning("Game state missing, stopping loop | room=%s", self.room_id)
                    await self.stop()
                    break

                phase = Phase(state["phase"])
                
                # If phase is LOBBY or ENDED, we don't have an active timer loop
                if phase in (Phase.LOBBY, Phase.ENDED):
                    # Sleep long, wait for manual trigger or stop
                    self._loop_trigger.clear()
                    await asyncio.wait_for(self._loop_trigger.wait(), timeout=86400)
                    continue

                timer_end = state.get("timer_end")

                # If no timer is set but phase is active, emergency advance
                if not timer_end:
                    logger.warning("Active phase %s with no timer_end | room=%s", phase, self.room_id)
                    await self._advance()
                    continue

                now = time.time()
                time_to_wait = timer_end - now

                if time_to_wait > 0:
                    self._loop_trigger.clear()
                    try:
                        # Wait until timer expires OR we get an explicit _trigger_advance()
                        await asyncio.wait_for(self._loop_trigger.wait(), timeout=time_to_wait)
                    except asyncio.TimeoutError:
                        # Timer naturally expired
                        pass

                # If we get here (either timeout expired or triggered early) -> advance phase
                if self._running:
                    await self._advance()

            except asyncio.CancelledError:
                logger.info("Game loop cancelled | room=%s", self.room_id)
                break
            except Exception as e:
                logger.exception("Error in game loop | room=%s error=%s", self.room_id, e)
                await asyncio.sleep(5)  # Backoff on error

    async def _advance(self) -> None:
        """Calls game_logic.advance_phase and broadcasts all relevant events."""
        try:
            res = await game_logic.advance_phase(self.r, self.room_id)
        except Exception as e:
            logger.exception("Error advancing phase | room=%s error=%s", self.room_id, e)
            return

        winner = res.get("winner")

        # 1. Handle Night result events
        night_result = res.get("night_result")
        if night_result:
            killed_id = night_result.get("killed_player_id")
            seer_target_id = night_result.get("seer_target_id")
            seer_target_role = night_result.get("seer_target_role")

            if killed_id:
                # Tell everyone who died
                payload = PlayerKilledPayload(player_id=killed_id)
                await self.pubsub.emit_to_room(self.room_id, "player_killed", payload.model_dump())

            if seer_target_id and seer_target_role:
                # Find who the seer is and tell ONLY them the result
                players = await rs.get_all_players(self.r, self.room_id)
                seer_id = next((pid for pid, p in players.items() if p.role and p.role.value == "seer"), None)
                if seer_id:
                    target_name = players[seer_target_id].username
                    seer_payload = SeerResultPayload(
                        target_id=seer_target_id,
                        target_name=target_name,
                        role=seer_target_role,
                    )
                    await self.pubsub.emit_to_user(self.room_id, seer_id, "seer_result", seer_payload.model_dump())

        # 2. Handle Voting result events
        elim_payload = res.get("eliminated_player")
        if elim_payload:
            await self.pubsub.emit_to_room(self.room_id, "player_eliminated", elim_payload.model_dump())

        no_elim_payload = res.get("no_elimination")
        if no_elim_payload:
            await self.pubsub.emit_to_room(self.room_id, "no_elimination", no_elim_payload.model_dump())

        # 3. Handle Winner / Phase Change
        if winner:
            await self.pubsub.emit_to_room(
                self.room_id,
                "game_ended",
                GameEndedPayload(
                    winner=winner.value,
                    round=res["round"],
                    players=res.get("final_players", []),
                ).model_dump(),
            )
            await self.stop()
        else:
            # Broadcast the new phase
            next_phase = res["next_phase"]
            if next_phase:
                payload = game_logic.build_phase_changed_payload(
                    next_phase,
                    res["round"],
                    res["timer_end"],
                )
                await self.pubsub.emit_to_room(self.room_id, "phase_changed", payload.model_dump())


    # ── Incoming Player Actions ───────────────────────────────────────────────

    async def handle_cast_vote(self, voter_id: str, target_id: str) -> None:
        """Handles a daytime vote and checks if we can skip the timer."""
        try:
            update_payload = await game_logic.cast_vote(self.r, self.room_id, voter_id, target_id)
            await self.pubsub.emit_to_room(self.room_id, "vote_update", update_payload.model_dump())
            
            # Check if all alive players have voted -> skip timer
            players = await rs.get_all_players(self.r, self.room_id)
            alive_count = sum(1 for p in players.values() if p.alive)
            votes = await rs.get_votes(self.r, self.room_id)
            
            if len(votes) == alive_count:
                logger.info("All alive players voted. Skipping voting timer | room=%s", self.room_id)
                self._trigger_advance()
                
        except ValueError as e:
            logger.warning("Invalid vote cast | room=%s voter=%s reason=%s", self.room_id, voter_id, e)
            await self.pubsub.emit_error(self.room_id, voter_id, str(e))

    async def handle_wolf_vote(self, wolf_id: str, target_id: str) -> None:
        """Handles a wolf vote and checks if all wolves have voted."""
        try:
            await game_logic.record_wolf_vote(self.r, self.room_id, wolf_id, target_id)
            # Acknowledge the wolf that action was registered (optional frontend trigger)
            await self.pubsub.emit_to_user(self.room_id, wolf_id, "action_registered", {"action": "wolf_vote"})
            
            # Check if night actions are complete
            await self._check_night_actions_complete()

        except ValueError as e:
            logger.warning("Invalid wolf vote | room=%s wolf=%s reason=%s", self.room_id, wolf_id, e)
            await self.pubsub.emit_error(self.room_id, wolf_id, str(e))

    async def handle_seer_action(self, seer_id: str, target_id: str) -> None:
        """Handles the seer action and checks if night actions are complete."""
        try:
            await game_logic.record_seer_action(self.r, self.room_id, seer_id, target_id)
            await self.pubsub.emit_to_user(self.room_id, seer_id, "action_registered", {"action": "seer_action"})
            
            # Check if night actions are complete
            await self._check_night_actions_complete()
            
        except ValueError as e:
            logger.warning("Invalid seer action | room=%s seer=%s reason=%s", self.room_id, seer_id, e)
            await self.pubsub.emit_error(self.room_id, seer_id, str(e))
            
    async def _check_night_actions_complete(self) -> None:
        """Helper to check if all wolves and seer have acted. If so, skips timer."""
        players = await rs.get_all_players(self.r, self.room_id)
        
        # Check wolves
        alive_wolves = [p for p in players.values() if p.alive and p.role and p.role.value == 'wolf']
        wolf_votes = await rs.get_wolf_votes(self.r, self.room_id)
        wolves_done = len(wolf_votes) == len(alive_wolves)
        
        # Check seer
        alive_seer = next((p for p in players.values() if p.alive and p.role and p.role.value == 'seer'), None)
        seer_action = await rs.get_seer_action(self.r, self.room_id)
        seer_done = (alive_seer is None) or (seer_action is not None)
        
        if wolves_done and seer_done:
            logger.info("All night actions complete. Skipping night timer | room=%s", self.room_id)
            self._trigger_advance()