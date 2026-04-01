"""
services/game_logic.py

All functions receive an aioredis.Redis client as first argument and operate
on a specific game_id. They use core/state_store.py for all I/O.

Implemented here
  - assign_roles()       
  - set_phase()          
  - check_winner()       
  - cast_vote()          
  - tally_votes()        
  - eliminate_player()   
"""

import logging
import random
import time
from typing import Optional

import redis.asyncio as aioredis

from core.config import get_settings
from core import state_store as rs
from models.game import GameState, Phase, Player, Role, Winner, SKIP_VOTE_TARGET
from models.events import (
    NoEliminationPayload,
    PhaseChangedPayload,
    PlayerEliminatedPayload,
    RoleAssignedPayload,
    VoteUpdatePayload,
)

logger = logging.getLogger(__name__)


# ── F1-2 · assign_roles() ─────────────────────────────────────────────────────

def _wolf_count(player_count: int) -> int:
    """
    Returns the number of wolves to assign based on player count.
        5–6  players → 1 wolf
        7–9  players → 2 wolves
        10+  players → 3 wolves
    """
    if player_count >= 10:
        return 3
    if player_count >= 7:
        return 2
    return 1


def _default_role_counts(player_count: int) -> tuple[int, int]:
    return _wolf_count(player_count), 1


def _validate_role_counts(player_count: int, wolf_count: int, seer_count: int) -> None:
    if player_count < 5:
        raise ValueError(f"Need at least 5 players, got {player_count}")
    if wolf_count < 1:
        raise ValueError("wolf_count must be at least 1")
    if seer_count < 0:
        raise ValueError("seer_count cannot be negative")
    if wolf_count + seer_count >= player_count:
        raise ValueError("wolf_count + seer_count must leave at least 1 villager")


async def assign_roles(
    r: aioredis.Redis,
    game_id: str,
    player_ids: list[str],
    wolf_count: int | None = None,
    seer_count: int | None = None,
) -> dict[str, Role]:
    """
    Randomly assigns roles to all players and persists them on Redis.

    Returns a dict { player_id → Role } (used by the caller to send
    unicast role_assigned events to each client).

    Wolves also receive the list of wolf_companions in their payload.
    """
    if wolf_count is None or seer_count is None:
        default_wolves, default_seers = _default_role_counts(len(player_ids))
        wolf_count = default_wolves if wolf_count is None else wolf_count
        seer_count = default_seers if seer_count is None else seer_count

    _validate_role_counts(len(player_ids), wolf_count, seer_count)

    shuffled = player_ids[:]
    random.shuffle(shuffled)

    # Assign: first wolves → WOLF, next seers → SEER, rest → VILLAGER
    assignment: dict[str, Role] = {}
    for i, pid in enumerate(shuffled):
        if i < wolf_count:
            assignment[pid] = Role.WOLF
        elif i < wolf_count + seer_count:
            assignment[pid] = Role.SEER
        else:
            assignment[pid] = Role.VILLAGER

    # Persist each player with their role
    for pid, role in assignment.items():
        player = await rs.get_player(r, game_id, pid)
        if player is None:
            # Create player record if it doesn't exist yet
            player = Player(player_id=pid, username=pid)
        player.role = role
        await rs.set_player(r, game_id, player)

    wolf_ids = [pid for pid, role in assignment.items() if role == Role.WOLF]
    logger.info(
        "Roles assigned | game=%s players=%d wolves=%d seers=%d",
        game_id, len(player_ids), wolf_count, seer_count,
    )
    return assignment


def build_role_payloads(
    assignment: dict[str, Role],
    players: dict[str, Player],
) -> dict[str, RoleAssignedPayload]:
    """
    Builds the RoleAssignedPayload for each player.
    Wolves receive wolf_companions; others receive an empty list.
    Called by the router after assign_roles() to emit unicast events.
    """
    wolf_ids = [pid for pid, role in assignment.items() if role == Role.WOLF]
    companions_info = [
        {"player_id": pid, "username": players[pid].username}
        for pid in wolf_ids
        if pid in players
    ]

    payloads: dict[str, RoleAssignedPayload] = {}
    for pid, role in assignment.items():
        payloads[pid] = RoleAssignedPayload(
            role=role.value,
            wolf_companions=[
                companion for companion in companions_info
                if companion["player_id"] != pid
            ] if role == Role.WOLF else [],
        )
    return payloads


# ── F1-3 · set_phase() ────────────────────────────────────────────────────────

async def set_phase(
    r: aioredis.Redis,
    game_id: str,
    phase: Phase,
    round_number: Optional[int] = None,
) -> float | None:
    """
    Transitions the game to a new phase and sets timer_end on Redis.

    Returns the timer_end timestamp (float) or None for phases without a timer
    (LOBBY, ENDED).
    """
    settings = get_settings()
    durations = settings.phase_durations

    duration_map = {
        Phase.LOBBY:  durations.lobby,
        Phase.DAY:    durations.day,
        Phase.VOTING: durations.voting,
        Phase.NIGHT:  durations.night,
        Phase.ENDED:  0,
    }

    duration = duration_map.get(phase, 0)
    timer_end = (time.time() + duration) if duration > 0 else None

    patch: dict = {"phase": phase.value, "timer_end": timer_end}
    if round_number is not None:
        patch["round"] = round_number

    await rs.patch_game_state(r, game_id, **patch)

    if timer_end:
        await rs.set_timer_end(r, game_id, timer_end)

    logger.info(
        "Phase set | game=%s phase=%s timer_end=%s",
        game_id, phase.value, timer_end,
    )
    return timer_end


def build_phase_changed_payload(
    phase: Phase,
    round_number: int,
    timer_end: Optional[float],
) -> PhaseChangedPayload:
    """Builds the broadcast payload for a phase transition."""
    return PhaseChangedPayload(
        phase=phase.value,
        round=round_number,
        timer_end=timer_end,
    )


# ── F1-3 · check_winner() ────────────────────────────────────────────────────

async def check_winner(r: aioredis.Redis, game_id: str) -> Optional[Winner]:
    """
    Evaluates win conditions and returns the winner, or None if the game continues.

    Called:
      1. After eliminate_player() (end of VOTING phase)
      2. After resolve_night()    (end of NIGHT phase)

    Win conditions (from design doc):
      VILLAGERS win: all wolves are dead.
      WOLVES win:    #alive_wolves >= #alive_villagers (wolves ≥ non-wolves).
    """
    players = await rs.get_all_players(r, game_id)
    alive = [p for p in players.values() if p.alive]

    alive_wolves = [p for p in alive if p.role == Role.WOLF]
    alive_villagers = [p for p in alive if p.role != Role.WOLF]

    if len(alive_wolves) == 0:
        logger.info("Winner: VILLAGERS (no wolves left) | game=%s", game_id)
        return Winner.VILLAGERS

    if len(alive_wolves) >= len(alive_villagers):
        logger.info(
            "Winner: WOLVES (wolves=%d >= villagers=%d) | game=%s",
            len(alive_wolves), len(alive_villagers), game_id,
        )
        return Winner.WOLVES

    return None


# ── F1-4 · cast_vote() ───────────────────────────────────────────────────────

async def cast_vote(
    r: aioredis.Redis,
    game_id: str,
    voter_id: str,
    target_id: str,
) -> VoteUpdatePayload:
    """
    Records a daytime vote from voter_id → target_id.

    Validates:
      - voter must be alive
      - voter must not have already voted (has_voted flag)
      - target must be alive (unless it's the skip sentinel)

    Returns a VoteUpdatePayload with the current vote tally (for broadcast).
    Raises ValueError on validation failure.
    """
    voter = await rs.get_player(r, game_id, voter_id)
    if voter is None or not voter.alive:
        raise ValueError(f"Voter {voter_id} is not an alive player")
    if voter.has_voted:
        raise ValueError(f"Player {voter_id} has already voted this round")

    if target_id != SKIP_VOTE_TARGET:
        target = await rs.get_player(r, game_id, target_id)
        if target is None or not target.alive:
            raise ValueError(f"Target {target_id} is not an alive player")

    # Mark voter as voted
    voter.has_voted = True
    await rs.set_player(r, game_id, voter)

    # Record vote
    await rs.record_vote(r, game_id, voter_id, target_id)

    # Build tally for broadcast
    all_votes = await rs.get_votes(r, game_id)
    return _build_vote_update(voter_id, target_id, all_votes)


def _build_vote_update(
    voter_id: str,
    target_id: str,
    all_votes: dict[str, str],
) -> VoteUpdatePayload:
    vote_counts: dict[str, int] = {}
    skip_count = 0
    for _voter, _target in all_votes.items():
        if _target == SKIP_VOTE_TARGET:
            skip_count += 1
        else:
            vote_counts[_target] = vote_counts.get(_target, 0) + 1

    return VoteUpdatePayload(
        voter_id=voter_id,
        target_id=target_id,
        vote_counts=vote_counts,
        skip_count=skip_count,
    )


# ── F1-4 · tally_votes() ─────────────────────────────────────────────────────

async def tally_votes(r: aioredis.Redis, game_id: str) -> Optional[str]:
    """
    Counts daytime votes and returns the player_id with the most votes.

    Returns None if:
      - no votes were cast
      - there is a tie between two or more players

    Skip votes (SKIP_VOTE_TARGET) count toward quorum but not toward any player.
    """
    all_votes = await rs.get_votes(r, game_id)

    vote_counts: dict[str, int] = {}
    for target in all_votes.values():
        if target == SKIP_VOTE_TARGET:
            continue
        vote_counts[target] = vote_counts.get(target, 0) + 1

    if not vote_counts:
        return None

    max_votes = max(vote_counts.values())
    leaders = [pid for pid, count in vote_counts.items() if count == max_votes]

    if len(leaders) > 1:
        logger.info("Daytime vote tie between %s | game=%s", leaders, game_id)
        return None  # tie → no elimination

    return leaders[0]


# ── F1-4 · eliminate_player() ────────────────────────────────────────────────

async def eliminate_player(
    r: aioredis.Redis,
    game_id: str,
    player_id: str,
) -> PlayerEliminatedPayload:
    """
    Marks a player as eliminated (alive=False) and resets daytime votes.

    The role IS revealed (daytime elimination — different from night kill).
    Returns a PlayerEliminatedPayload for broadcast.
    Raises ValueError if the player does not exist or is already dead.
    """
    player = await rs.get_player(r, game_id, player_id)
    if player is None:
        raise ValueError(f"Player {player_id} not found in game {game_id}")
    if not player.alive:
        raise ValueError(f"Player {player_id} is already eliminated")

    player.alive = False
    await rs.set_player(r, game_id, player)

    # Clear daytime votes for the next round
    await rs.clear_votes(r, game_id)

    # Reset has_voted flag for all remaining players
    all_players = await rs.get_all_players(r, game_id)
    for p in all_players.values():
        if p.player_id != player_id and p.has_voted:
            p.has_voted = False
            await rs.set_player(r, game_id, p)

    state_raw = await rs.get_game_state(r, game_id)
    round_number = state_raw.get("round", 0) if state_raw else 0

    logger.info(
        "Player eliminated | game=%s player=%s role=%s round=%d",
        game_id, player_id, player.role, round_number,
    )

    return PlayerEliminatedPayload(
        player_id=player.player_id,
        username=player.username,
        role=player.role.value if player.role else "",
        round=round_number,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Night game state
# ═══════════════════════════════════════════════════════════════════════════════

# ── F2-1 · can_player_act() ───────────────────────────────────────────────────

# Map each client-sent event to (required_phase, required_role, uses_has_acted_flag)
_ACTION_RULES: dict[str, tuple[Phase, Role | None, bool]] = {
    # event_type          phase           role          uses has_acted
    "cast_vote":        (Phase.VOTING,   None,          False),  # any alive player
    "wolf_vote":        (Phase.NIGHT,    Role.WOLF,     True),
    "seer_action":      (Phase.NIGHT,    Role.SEER,     True),
}


async def can_player_act(
    r: aioredis.Redis,
    game_id: str,
    player_id: str,
    action: str,
) -> tuple[bool, str]:
    """
    Single guard for all player actions.

    Returns (True, "") if the action is allowed, or (False, reason) otherwise.

    Checks in order:
      1. action is a known event type
      2. player exists and is alive
      3. current phase matches the required phase for this action
      4. player's role matches the required role (if any)
      5. player has not already acted this phase (for night actions)
         or has not already voted (for cast_vote)
    """
    rule = _ACTION_RULES.get(action)
    if rule is None:
        return False, f"Unknown action: {action}"

    required_phase, required_role, uses_has_acted = rule

    player = await rs.get_player(r, game_id, player_id)
    if player is None:
        return False, f"Player {player_id} not found"
    if not player.alive:
        return False, f"Player {player_id} is not alive"

    state = await rs.get_game_state(r, game_id)
    if state is None:
        return False, "Game state not found"

    current_phase = Phase(state["phase"])
    if current_phase != required_phase:
        return False, f"Action '{action}' not allowed in phase {current_phase.value}"

    if required_role is not None and player.role != required_role:
        return False, f"Action '{action}' requires role {required_role.value}, player has {player.role}"

    if uses_has_acted and player.has_acted:
        return False, f"Player {player_id} has already acted this night"

    if action == "cast_vote" and player.has_voted:
        return False, f"Player {player_id} has already voted this round"

    return True, ""


# ── F2-2 · record_wolf_vote() ─────────────────────────────────────────────────

async def record_wolf_vote(
    r: aioredis.Redis,
    game_id: str,
    wolf_id: str,
    target_id: str,
) -> None:
    """
    Records a wolf's nighttime vote and marks has_acted=True.

    Uses can_player_act as guard — raises ValueError if not allowed.
    target_id must be an alive non-wolf player.
    """
    allowed, reason = await can_player_act(r, game_id, wolf_id, "wolf_vote")
    if not allowed:
        raise ValueError(reason)

    # Validate target: must be alive and not a wolf
    target = await rs.get_player(r, game_id, target_id)
    if target is None or not target.alive:
        raise ValueError(f"Target {target_id} is not an alive player")
    if target.role == Role.WOLF:
        raise ValueError(f"Wolves cannot vote to kill another wolf ({target_id})")

    await rs.record_wolf_vote(r, game_id, wolf_id, target_id)

    wolf = await rs.get_player(r, game_id, wolf_id)
    wolf.has_acted = True
    await rs.set_player(r, game_id, wolf)

    logger.info("Wolf vote recorded | game=%s wolf=%s target=%s", game_id, wolf_id, target_id)


async def record_seer_action(
    r: aioredis.Redis,
    game_id: str,
    seer_id: str,
    target_id: str,
) -> None:
    """
    Records the seer's nighttime inspection target and marks has_acted=True.

    Uses can_player_act as guard — raises ValueError if not allowed.
    target_id must be an alive player.
    """
    allowed, reason = await can_player_act(r, game_id, seer_id, "seer_action")
    if not allowed:
        raise ValueError(reason)

    target = await rs.get_player(r, game_id, target_id)
    if target is None or not target.alive:
        raise ValueError(f"Target {target_id} is not an alive player")

    await rs.record_seer_action(r, game_id, target_id)

    seer = await rs.get_player(r, game_id, seer_id)
    seer.has_acted = True
    await rs.set_player(r, game_id, seer)

    logger.info("Seer action recorded | game=%s seer=%s target=%s", game_id, seer_id, target_id)


# ── F2-3 · resolve_night() ────────────────────────────────────────────────────

async def resolve_night(
    r: aioredis.Redis,
    game_id: str,
) -> dict:
    """
    Applies end-of-night actions in order:
      1. Tally wolf votes → kill target (or no kill on tie)
      2. Deliver seer_result to seer (if they acted)
      3. Clean up wolf_votes, seer_action, and has_acted flags

    Returns a result dict with:
      {
        "killed_player_id": str | None,   # None on wolf-vote tie
        "seer_target_id":   str | None,   # None if seer didn't act
        "seer_target_role": str | None,
      }

    The caller (advance_phase / router) is responsible for emitting
    Socket.IO events based on the returned dict.
    """
    # ── 1. Wolf vote tally ────────────────────────────────────────────────────
    wolf_votes = await rs.get_wolf_votes(r, game_id)
    killed_player_id: Optional[str] = None

    if wolf_votes:
        # Count votes per target
        tally: dict[str, int] = {}
        for target in wolf_votes.values():
            tally[target] = tally.get(target, 0) + 1

        max_votes = max(tally.values())
        leaders = [pid for pid, count in tally.items() if count == max_votes]

        if len(leaders) == 1:
            killed_player_id = leaders[0]
            victim = await rs.get_player(r, game_id, killed_player_id)
            if victim and victim.alive:
                victim.alive = False
                await rs.set_player(r, game_id, victim)
                logger.info(
                    "Night kill | game=%s victim=%s", game_id, killed_player_id
                )
            else:
                killed_player_id = None  # already dead (shouldn't happen)
        else:
            logger.info("Wolf vote tie — no kill this night | game=%s", game_id)

    # ── 2. Seer action ────────────────────────────────────────────────────────
    seer_target_id: Optional[str] = None
    seer_target_role: Optional[str] = None

    seer_action_target = await rs.get_seer_action(r, game_id)
    if seer_action_target:
        seer_target = await rs.get_player(r, game_id, seer_action_target)
        if seer_target:
            seer_target_id = seer_action_target
            seer_target_role = seer_target.role.value if seer_target.role else None
            logger.info(
                "Seer result | game=%s target=%s role=%s",
                game_id, seer_target_id, seer_target_role,
            )

    # ── 3. Cleanup ────────────────────────────────────────────────────────────
    await rs.clear_wolf_votes(r, game_id)
    await rs.clear_seer_action(r, game_id)

    # Reset has_acted for all players (next night)
    all_players = await rs.get_all_players(r, game_id)
    for p in all_players.values():
        if p.has_acted:
            p.has_acted = False
            await rs.set_player(r, game_id, p)

    return {
        "killed_player_id": killed_player_id,
        "seer_target_id":   seer_target_id,
        "seer_target_role": seer_target_role,
    }


# ── F2-4 · advance_phase() ────────────────────────────────────────────────────

async def advance_phase(
    r: aioredis.Redis,
    game_id: str,
) -> dict:
    """
    Central phase-transition method. Called by the phase timer on expiry.

    Orchestrates:
      1. Reads current phase
      2. For VOTING: tally votes, optionally eliminate, check winner
      3. For NIGHT:  resolve_night(), check winner
      4. If winner found: transition to ENDED, return result
      5. Otherwise: transition to next phase, increment round if needed

    Returns a dict describing what happened:
      {
        "next_phase":        Phase,
        "round":             int,
        "timer_end":         float | None,
        "winner":            Winner | None,
        "eliminated_player": PlayerEliminatedPayload | None,  # from VOTING
        "night_result":      dict | None,                     # from NIGHT
        "no_elimination":    NoEliminationPayload | None,     # VOTING tie/no-votes
      }

    Does NOT emit Socket.IO events — that is the router's responsibility.
    """
    state = await rs.get_game_state(r, game_id)
    if state is None:
        raise RuntimeError(f"Game {game_id} not found")

    current_phase = Phase(state["phase"])
    current_round = state.get("round", 0)

    result: dict = {
        "next_phase":        None,
        "round":             current_round,
        "timer_end":         None,
        "winner":            None,
        "eliminated_player": None,
        "night_result":      None,
        "no_elimination":    None,
    }

    # ── VOTING phase ends ─────────────────────────────────────────────────────
    if current_phase == Phase.VOTING:
        most_voted = await tally_votes(r, game_id)

        if most_voted is not None:
            elim_payload = await eliminate_player(r, game_id, most_voted)
            result["eliminated_player"] = elim_payload
        else:
            # Tie or no votes — determine reason
            all_votes = await rs.get_votes(r, game_id)
            reason = "no_votes" if not all_votes else "tie"
            result["no_elimination"] = NoEliminationPayload(reason=reason)
            await rs.clear_votes(r, game_id)

        winner = await check_winner(r, game_id)
        if winner:
            result["winner"] = winner
            await _end_game(r, game_id, winner, current_round, result)
            return result

        # No winner → go to NIGHT
        timer_end = await set_phase(r, game_id, Phase.NIGHT)
        result["next_phase"] = Phase.NIGHT
        result["timer_end"] = timer_end

    # ── NIGHT phase ends ──────────────────────────────────────────────────────
    elif current_phase == Phase.NIGHT:
        night_result = await resolve_night(r, game_id)
        result["night_result"] = night_result

        winner = await check_winner(r, game_id)
        if winner:
            result["winner"] = winner
            await _end_game(r, game_id, winner, current_round, result)
            return result

        # No winner → go to DAY, increment round
        new_round = current_round + 1
        timer_end = await set_phase(r, game_id, Phase.DAY, round_number=new_round)
        result["next_phase"] = Phase.DAY
        result["round"] = new_round
        result["timer_end"] = timer_end

    # ── DAY phase ends ────────────────────────────────────────────────────────
    elif current_phase == Phase.DAY:
        # Day ends → go to VOTING (same round)
        timer_end = await set_phase(r, game_id, Phase.VOTING)
        result["next_phase"] = Phase.VOTING
        result["timer_end"] = timer_end

    else:
        logger.warning(
            "advance_phase called in unexpected phase %s | game=%s",
            current_phase, game_id,
        )

    return result


async def _end_game(
    r: aioredis.Redis,
    game_id: str,
    winner: Winner,
    round_number: int,
    result: dict,
) -> None:
    """Persists ENDED state and populates result with final player list."""
    await rs.patch_game_state(
        r, game_id,
        phase=Phase.ENDED.value,
        winner=winner.value,
        timer_end=None,
    )
    result["next_phase"] = Phase.ENDED
    result["round"] = round_number

    all_players = await rs.get_all_players(r, game_id)
    result["final_players"] = [
        {
            "player_id": p.player_id,
            "username":  p.username,
            "role":      p.role.value if p.role else None,
            "alive":     p.alive,
        }
        for p in all_players.values()
    ]
    logger.info("Game ended | game=%s winner=%s round=%d", game_id, winner.value, round_number)
