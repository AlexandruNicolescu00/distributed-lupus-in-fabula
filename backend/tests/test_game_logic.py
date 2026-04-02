"""
tests/test_game_logic.py — TDD tests for Fase 1 + Fase 2.

Run with:
    pytest tests/test_game_logic.py -v

Uses fakeredis — no real Redis instance needed.
"""

import pytest
import pytest_asyncio
import fakeredis.aioredis as fakeredis

from core import state_store as rs
from models.game import Phase, Player, Role, Winner, SKIP_VOTE_TARGET
from models.events import PlayerEliminatedPayload, VoteUpdatePayload
from services.game_logic import (
    _wolf_count,
    advance_phase,
    assign_roles,
    build_role_payloads,
    can_player_act,
    cast_vote,
    check_winner,
    eliminate_player,
    record_seer_action,
    record_wolf_vote,
    resolve_night,
    set_phase,
    tally_votes,
)

GAME_ID = "test-game"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def r():
    """Fake async Redis client — reset between tests."""
    client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def game_with_players(r):
    """Seeds 6 players (no roles) and a LOBBY state. Returns player_ids."""
    ids = [f"p{i}" for i in range(1, 7)]
    for pid in ids:
        await rs.set_player(r, GAME_ID, Player(player_id=pid, username=f"Player{pid}"))
    await rs.patch_game_state(
        r, GAME_ID, phase=Phase.LOBBY.value, round=0, paused=False, winner=None, timer_end=None
    )
    return ids


async def _seed_game(r, phase: Phase, round_num: int = 1):
    """Seeds a standard 6-player game (1 wolf, 1 seer, 4 villagers) in the given phase."""
    players = {
        "wolf1": Player("wolf1", "BigBad",  role=Role.WOLF,     alive=True),
        "seer1": Player("seer1", "Oracle",  role=Role.SEER,     alive=True),
        "v1":    Player("v1",    "Alice",   role=Role.VILLAGER, alive=True),
        "v2":    Player("v2",    "Bob",     role=Role.VILLAGER, alive=True),
        "v3":    Player("v3",    "Carol",   role=Role.VILLAGER, alive=True),
        "v4":    Player("v4",    "Dave",    role=Role.VILLAGER, alive=True),
    }
    for p in players.values():
        await rs.set_player(r, GAME_ID, p)
    await rs.patch_game_state(
        r, GAME_ID, phase=phase.value, round=round_num, paused=False, winner=None, timer_end=None
    )
    return players


# ══════════════════════════════════════════════════════════════════════════════
# Daytime game state
# ══════════════════════════════════════════════════════════════════════════════

# ── F1-1: core/state_store.py raw helpers ────────────────────────────────────

class TestRedisState:

    @pytest.mark.asyncio
    async def test_set_and_get_player(self, r):
        p = Player(player_id="p1", username="Alice", role=Role.WOLF)
        await rs.set_player(r, GAME_ID, p)
        loaded = await rs.get_player(r, GAME_ID, "p1")
        assert loaded is not None
        assert loaded.role == Role.WOLF
        assert loaded.username == "Alice"

    @pytest.mark.asyncio
    async def test_get_player_none(self, r):
        assert await rs.get_player(r, GAME_ID, "nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_all_players(self, r):
        for pid in ["a", "b", "c"]:
            await rs.set_player(r, GAME_ID, Player(player_id=pid, username=pid))
        all_p = await rs.get_all_players(r, GAME_ID)
        assert set(all_p.keys()) == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_patch_game_state(self, r):
        await rs.patch_game_state(r, GAME_ID, phase="DAY", round=1)
        state = await rs.get_game_state(r, GAME_ID)
        assert state["phase"] == "DAY"
        assert state["round"] == 1

    @pytest.mark.asyncio
    async def test_record_and_get_votes(self, r):
        await rs.record_vote(r, GAME_ID, "p1", "p2")
        await rs.record_vote(r, GAME_ID, "p3", "p2")
        assert await rs.get_votes(r, GAME_ID) == {"p1": "p2", "p3": "p2"}

    @pytest.mark.asyncio
    async def test_clear_votes(self, r):
        await rs.record_vote(r, GAME_ID, "p1", "p2")
        await rs.clear_votes(r, GAME_ID)
        assert await rs.get_votes(r, GAME_ID) == {}

    @pytest.mark.asyncio
    async def test_wolf_vote_round_trip(self, r):
        await rs.record_wolf_vote(r, GAME_ID, "wolf1", "victim")
        assert await rs.get_wolf_votes(r, GAME_ID) == {"wolf1": "victim"}

    @pytest.mark.asyncio
    async def test_seer_action_round_trip(self, r):
        await rs.record_seer_action(r, GAME_ID, "p5")
        assert await rs.get_seer_action(r, GAME_ID) == "p5"

    @pytest.mark.asyncio
    async def test_set_and_get_timer_end(self, r):
        import time
        t = time.time() + 60
        await rs.set_timer_end(r, GAME_ID, t)
        loaded = await rs.get_timer_end(r, GAME_ID)
        assert abs(loaded - t) < 0.001

    @pytest.mark.asyncio
    async def test_delete_game_clears_all_keys(self, r):
        await rs.set_player(r, GAME_ID, Player(player_id="p1", username="Alice"))
        await rs.record_vote(r, GAME_ID, "p1", "p2")
        await rs.record_wolf_vote(r, GAME_ID, "w1", "v1")
        await rs.record_seer_action(r, GAME_ID, "p3")
        await rs.patch_game_state(r, GAME_ID, phase="DAY")
        await rs.delete_game(r, GAME_ID)
        assert await rs.get_game_state(r, GAME_ID) is None
        assert await rs.get_all_players(r, GAME_ID) == {}
        assert await rs.get_votes(r, GAME_ID) == {}

    @pytest.mark.asyncio
    async def test_player_role_none_serialization(self, r):
        """Player with role=None must survive a Redis round-trip."""
        p = Player(player_id="p99", username="Unknown")
        await rs.set_player(r, GAME_ID, p)
        loaded = await rs.get_player(r, GAME_ID, "p99")
        assert loaded.role is None


# ── F1-2: assign_roles() ─────────────────────────────────────────────────────

class TestAssignRoles:

    def test_wolf_count_thresholds(self):
        assert _wolf_count(4) == 1
        assert _wolf_count(6) == 1
        assert _wolf_count(7) == 2
        assert _wolf_count(9) == 2
        assert _wolf_count(10) == 3
        assert _wolf_count(15) == 3

    @pytest.mark.asyncio
    async def test_assign_roles_6_players(self, r, game_with_players):
        assignment = await assign_roles(r, GAME_ID, game_with_players)
        wolves    = [p for p, role in assignment.items() if role == Role.WOLF]
        seers     = [p for p, role in assignment.items() if role == Role.SEER]
        villagers = [p for p, role in assignment.items() if role == Role.VILLAGER]
        assert len(wolves) == 1
        assert len(seers) == 1
        assert len(villagers) == 4

    @pytest.mark.asyncio
    async def test_assign_roles_persisted_on_redis(self, r, game_with_players):
        assignment = await assign_roles(r, GAME_ID, game_with_players)
        for pid, expected_role in assignment.items():
            loaded = await rs.get_player(r, GAME_ID, pid)
            assert loaded is not None
            assert loaded.role == expected_role

    @pytest.mark.asyncio
    async def test_assign_roles_raises_on_too_few_players(self, r):
        with pytest.raises(ValueError, match="at least 5"):
            await assign_roles(r, GAME_ID, ["p1", "p2", "p3"])

    @pytest.mark.asyncio
    async def test_assign_roles_produces_valid_distribution(self, r):
        """Both runs must produce valid assignments (wolf + seer + villagers)."""
        ids = [f"p{i}" for i in range(1, 7)]
        for pid in ids:
            for gid in ["g-A", "g-B"]:
                await rs.set_player(r, gid, Player(player_id=pid, username=pid))
        a1 = await assign_roles(r, "g-A", ids)
        a2 = await assign_roles(r, "g-B", ids)
        assert set(a1.values()) == {Role.WOLF, Role.SEER, Role.VILLAGER}
        assert set(a2.values()) == {Role.WOLF, Role.SEER, Role.VILLAGER}

    @pytest.mark.asyncio
    async def test_assign_roles_uses_custom_role_counts(self, r, game_with_players):
        assignment = await assign_roles(
            r,
            GAME_ID,
            game_with_players,
            wolf_count=2,
            seer_count=2,
        )
        wolves = [p for p, role in assignment.items() if role == Role.WOLF]
        seers = [p for p, role in assignment.items() if role == Role.SEER]
        villagers = [p for p, role in assignment.items() if role == Role.VILLAGER]
        assert len(wolves) == 2
        assert len(seers) == 2
        assert len(villagers) == 2

    @pytest.mark.asyncio
    async def test_assign_roles_rejects_invalid_custom_counts(self, r, game_with_players):
        with pytest.raises(ValueError, match="at least 1 villager"):
            await assign_roles(r, GAME_ID, game_with_players, wolf_count=4, seer_count=2)

    @pytest.mark.asyncio
    async def test_build_role_payloads_wolf_companions(self, r, game_with_players):
        assignment = await assign_roles(r, GAME_ID, game_with_players)
        players = await rs.get_all_players(r, GAME_ID)
        payloads = build_role_payloads(assignment, players)
        for pid, role in assignment.items():
            payload = payloads[pid]
            assert payload.role == role.value
            if role != Role.WOLF:
                assert payload.wolf_companions == []
            else:
                assert isinstance(payload.wolf_companions, list)
                assert all(companion["player_id"] != pid for companion in payload.wolf_companions)


# ── F1-3: set_phase() + check_winner() ───────────────────────────────────────

class TestSetPhase:

    @pytest.mark.asyncio
    async def test_set_phase_day(self, r):
        timer_end = await set_phase(r, GAME_ID, Phase.DAY, round_number=1)
        state = await rs.get_game_state(r, GAME_ID)
        assert state["phase"] == "DAY"
        assert state["round"] == 1
        assert timer_end is not None and timer_end > 0

    @pytest.mark.asyncio
    async def test_set_phase_lobby_no_timer(self, r):
        assert await set_phase(r, GAME_ID, Phase.LOBBY) is None

    @pytest.mark.asyncio
    async def test_set_phase_ended_no_timer(self, r):
        assert await set_phase(r, GAME_ID, Phase.ENDED) is None

    @pytest.mark.asyncio
    async def test_set_phase_voting_timer_range(self, r):
        import time
        before = time.time()
        timer_end = await set_phase(r, GAME_ID, Phase.VOTING)
        after = time.time()
        assert before + 55 < timer_end < after + 65  # 60s default ± 5s tolerance

    @pytest.mark.asyncio
    async def test_set_phase_persists_timer_end_key(self, r):
        await set_phase(r, GAME_ID, Phase.NIGHT)
        assert await rs.get_timer_end(r, GAME_ID) is not None


class TestCheckWinner:

    async def _seed(self, r, roles: list[Role]):
        for i, role in enumerate(roles):
            await rs.set_player(r, GAME_ID, Player(f"p{i}", f"P{i}", role=role, alive=True))

    @pytest.mark.asyncio
    async def test_no_winner_normal_game(self, r):
        await self._seed(r, [Role.WOLF, Role.SEER, Role.VILLAGER, Role.VILLAGER])
        assert await check_winner(r, GAME_ID) is None

    @pytest.mark.asyncio
    async def test_villagers_win_all_wolves_dead(self, r):
        await self._seed(r, [Role.SEER, Role.VILLAGER])
        assert await check_winner(r, GAME_ID) == Winner.VILLAGERS

    @pytest.mark.asyncio
    async def test_wolves_win_parity(self, r):
        await self._seed(r, [Role.WOLF, Role.VILLAGER])
        assert await check_winner(r, GAME_ID) == Winner.WOLVES

    @pytest.mark.asyncio
    async def test_wolves_win_majority(self, r):
        await self._seed(r, [Role.WOLF, Role.WOLF, Role.VILLAGER])
        assert await check_winner(r, GAME_ID) == Winner.WOLVES

    @pytest.mark.asyncio
    async def test_dead_players_not_counted(self, r):
        await rs.set_player(r, GAME_ID, Player("w1", "Wolf", role=Role.WOLF, alive=False))
        await rs.set_player(r, GAME_ID, Player("v1", "Vill", role=Role.VILLAGER, alive=True))
        assert await check_winner(r, GAME_ID) == Winner.VILLAGERS

    @pytest.mark.asyncio
    async def test_seer_counts_as_villager(self, r):
        await rs.set_player(r, GAME_ID, Player("w1", "Wolf", role=Role.WOLF, alive=True))
        await rs.set_player(r, GAME_ID, Player("s1", "Seer", role=Role.SEER, alive=True))
        assert await check_winner(r, GAME_ID) == Winner.WOLVES


# ── F1-4: cast_vote() + tally_votes() + eliminate_player() ───────────────────

class TestCastVote:

    async def _seed_voting_game(self, r):
        ids = [f"p{i}" for i in range(1, 7)]
        for pid in ids:
            role = Role.WOLF if pid == "p1" else Role.VILLAGER
            await rs.set_player(r, GAME_ID, Player(pid, pid, role=role))
        await rs.patch_game_state(r, GAME_ID, phase=Phase.VOTING.value, round=1)
        return ids

    @pytest.mark.asyncio
    async def test_cast_vote_records_and_returns_payload(self, r):
        await self._seed_voting_game(r)
        payload = await cast_vote(r, GAME_ID, "p2", "p1")
        assert isinstance(payload, VoteUpdatePayload)
        assert payload.voter_id == "p2"
        assert payload.vote_counts.get("p1") == 1

    @pytest.mark.asyncio
    async def test_cast_vote_sets_has_voted(self, r):
        await self._seed_voting_game(r)
        await cast_vote(r, GAME_ID, "p2", "p1")
        assert (await rs.get_player(r, GAME_ID, "p2")).has_voted is True

    @pytest.mark.asyncio
    async def test_cast_vote_duplicate_raises(self, r):
        await self._seed_voting_game(r)
        await cast_vote(r, GAME_ID, "p2", "p1")
        with pytest.raises(ValueError, match="already voted"):
            await cast_vote(r, GAME_ID, "p2", "p3")

    @pytest.mark.asyncio
    async def test_cast_vote_dead_voter_raises(self, r):
        await self._seed_voting_game(r)
        dead = await rs.get_player(r, GAME_ID, "p3")
        dead.alive = False
        await rs.set_player(r, GAME_ID, dead)
        with pytest.raises(ValueError, match="alive player"):
            await cast_vote(r, GAME_ID, "p3", "p1")

    @pytest.mark.asyncio
    async def test_cast_vote_skip(self, r):
        await self._seed_voting_game(r)
        payload = await cast_vote(r, GAME_ID, "p2", SKIP_VOTE_TARGET)
        assert payload.skip_count == 1
        assert payload.vote_counts == {}

    @pytest.mark.asyncio
    async def test_cast_vote_running_tally(self, r):
        await self._seed_voting_game(r)
        await cast_vote(r, GAME_ID, "p2", "p1")
        payload = await cast_vote(r, GAME_ID, "p3", "p1")
        assert payload.vote_counts.get("p1") == 2


class TestTallyVotes:

    @pytest.mark.asyncio
    async def test_tally_clear_winner(self, r):
        await rs.record_vote(r, GAME_ID, "p1", "p3")
        await rs.record_vote(r, GAME_ID, "p2", "p3")
        await rs.record_vote(r, GAME_ID, "p4", "p5")
        assert await tally_votes(r, GAME_ID) == "p3"

    @pytest.mark.asyncio
    async def test_tally_tie_returns_none(self, r):
        await rs.record_vote(r, GAME_ID, "p1", "p3")
        await rs.record_vote(r, GAME_ID, "p2", "p4")
        assert await tally_votes(r, GAME_ID) is None

    @pytest.mark.asyncio
    async def test_tally_no_votes_returns_none(self, r):
        assert await tally_votes(r, GAME_ID) is None

    @pytest.mark.asyncio
    async def test_tally_skip_votes_do_not_count(self, r):
        await rs.record_vote(r, GAME_ID, "p1", SKIP_VOTE_TARGET)
        await rs.record_vote(r, GAME_ID, "p2", SKIP_VOTE_TARGET)
        await rs.record_vote(r, GAME_ID, "p3", "p4")
        assert await tally_votes(r, GAME_ID) == "p4"

    @pytest.mark.asyncio
    async def test_tally_all_skip_returns_none(self, r):
        await rs.record_vote(r, GAME_ID, "p1", SKIP_VOTE_TARGET)
        await rs.record_vote(r, GAME_ID, "p2", SKIP_VOTE_TARGET)
        assert await tally_votes(r, GAME_ID) is None


class TestEliminatePlayer:

    async def _seed(self, r, pid, role=Role.VILLAGER, alive=True):
        await rs.set_player(r, GAME_ID, Player(pid, f"User{pid}", role=role, alive=alive))
        await rs.patch_game_state(r, GAME_ID, phase=Phase.VOTING.value, round=2)

    @pytest.mark.asyncio
    async def test_eliminate_marks_player_dead(self, r):
        await self._seed(r, "p1")
        await eliminate_player(r, GAME_ID, "p1")
        assert (await rs.get_player(r, GAME_ID, "p1")).alive is False

    @pytest.mark.asyncio
    async def test_eliminate_returns_payload_with_role(self, r):
        await self._seed(r, "p1", Role.WOLF)
        payload = await eliminate_player(r, GAME_ID, "p1")
        assert isinstance(payload, PlayerEliminatedPayload)
        assert payload.role == "WOLF"

    @pytest.mark.asyncio
    async def test_eliminate_clears_votes(self, r):
        await self._seed(r, "p1")
        await rs.record_vote(r, GAME_ID, "p2", "p1")
        await eliminate_player(r, GAME_ID, "p1")
        assert await rs.get_votes(r, GAME_ID) == {}

    @pytest.mark.asyncio
    async def test_eliminate_already_dead_raises(self, r):
        await self._seed(r, "p1", alive=False)
        with pytest.raises(ValueError, match="already eliminated"):
            await eliminate_player(r, GAME_ID, "p1")

    @pytest.mark.asyncio
    async def test_eliminate_nonexistent_raises(self, r):
        with pytest.raises(ValueError, match="not found"):
            await eliminate_player(r, GAME_ID, "ghost")

    @pytest.mark.asyncio
    async def test_eliminate_resets_has_voted_for_others(self, r):
        await self._seed(r, "p1", Role.WOLF)
        voter = Player("p2", "Voter", role=Role.VILLAGER, has_voted=True)
        await rs.set_player(r, GAME_ID, voter)
        await eliminate_player(r, GAME_ID, "p1")
        assert (await rs.get_player(r, GAME_ID, "p2")).has_voted is False


# ══════════════════════════════════════════════════════════════════════════════
# FASE 2 — Night game state
# ══════════════════════════════════════════════════════════════════════════════

# ── F2-1: can_player_act() ────────────────────────────────────────────────────

class TestCanPlayerAct:

    @pytest.mark.asyncio
    async def test_cast_vote_allowed_in_voting(self, r):
        await _seed_game(r, Phase.VOTING)
        ok, reason = await can_player_act(r, GAME_ID, "v1", "cast_vote")
        assert ok is True and reason == ""

    @pytest.mark.asyncio
    async def test_cast_vote_blocked_in_day(self, r):
        await _seed_game(r, Phase.DAY)
        ok, reason = await can_player_act(r, GAME_ID, "v1", "cast_vote")
        assert ok is False
        assert "DAY" in reason or "phase" in reason.lower()

    @pytest.mark.asyncio
    async def test_wolf_vote_allowed_for_wolf_in_night(self, r):
        await _seed_game(r, Phase.NIGHT)
        ok, _ = await can_player_act(r, GAME_ID, "wolf1", "wolf_vote")
        assert ok is True

    @pytest.mark.asyncio
    async def test_wolf_vote_blocked_for_villager(self, r):
        await _seed_game(r, Phase.NIGHT)
        ok, reason = await can_player_act(r, GAME_ID, "v1", "wolf_vote")
        assert ok is False
        assert "WOLF" in reason or "role" in reason.lower()

    @pytest.mark.asyncio
    async def test_seer_action_allowed_for_seer_in_night(self, r):
        await _seed_game(r, Phase.NIGHT)
        ok, _ = await can_player_act(r, GAME_ID, "seer1", "seer_action")
        assert ok is True

    @pytest.mark.asyncio
    async def test_seer_action_blocked_for_wolf(self, r):
        await _seed_game(r, Phase.NIGHT)
        ok, _ = await can_player_act(r, GAME_ID, "wolf1", "seer_action")
        assert ok is False

    @pytest.mark.asyncio
    async def test_dead_player_cannot_act(self, r):
        await _seed_game(r, Phase.VOTING)
        dead = await rs.get_player(r, GAME_ID, "v1")
        dead.alive = False
        await rs.set_player(r, GAME_ID, dead)
        ok, reason = await can_player_act(r, GAME_ID, "v1", "cast_vote")
        assert ok is False and "alive" in reason.lower()

    @pytest.mark.asyncio
    async def test_already_voted_blocked(self, r):
        await _seed_game(r, Phase.VOTING)
        voter = await rs.get_player(r, GAME_ID, "v1")
        voter.has_voted = True
        await rs.set_player(r, GAME_ID, voter)
        ok, reason = await can_player_act(r, GAME_ID, "v1", "cast_vote")
        assert ok is False and "already voted" in reason.lower()

    @pytest.mark.asyncio
    async def test_already_acted_blocked(self, r):
        await _seed_game(r, Phase.NIGHT)
        wolf = await rs.get_player(r, GAME_ID, "wolf1")
        wolf.has_acted = True
        await rs.set_player(r, GAME_ID, wolf)
        ok, reason = await can_player_act(r, GAME_ID, "wolf1", "wolf_vote")
        assert ok is False and "already acted" in reason.lower()

    @pytest.mark.asyncio
    async def test_unknown_action_blocked(self, r):
        await _seed_game(r, Phase.NIGHT)
        ok, reason = await can_player_act(r, GAME_ID, "wolf1", "teleport")
        assert ok is False and "unknown" in reason.lower()

    @pytest.mark.asyncio
    async def test_nonexistent_player_blocked(self, r):
        await _seed_game(r, Phase.VOTING)
        ok, reason = await can_player_act(r, GAME_ID, "ghost", "cast_vote")
        assert ok is False and "not found" in reason.lower()


# ── F2-2: record_wolf_vote() ──────────────────────────────────────────────────

class TestRecordWolfVote:

    @pytest.mark.asyncio
    async def test_wolf_vote_recorded_and_has_acted_set(self, r):
        await _seed_game(r, Phase.NIGHT)
        await record_wolf_vote(r, GAME_ID, "wolf1", "v1")
        assert await rs.get_wolf_votes(r, GAME_ID) == {"wolf1": "v1"}
        assert (await rs.get_player(r, GAME_ID, "wolf1")).has_acted is True

    @pytest.mark.asyncio
    async def test_wolf_cannot_vote_in_day_phase(self, r):
        await _seed_game(r, Phase.DAY)
        with pytest.raises(ValueError):
            await record_wolf_vote(r, GAME_ID, "wolf1", "v1")

    @pytest.mark.asyncio
    async def test_wolf_cannot_vote_twice(self, r):
        await _seed_game(r, Phase.NIGHT)
        await record_wolf_vote(r, GAME_ID, "wolf1", "v1")
        with pytest.raises(ValueError, match="already acted"):
            await record_wolf_vote(r, GAME_ID, "wolf1", "v2")

    @pytest.mark.asyncio
    async def test_wolf_cannot_target_another_wolf(self, r):
        await _seed_game(r, Phase.NIGHT)
        wolf2 = Player("wolf2", "Partner", role=Role.WOLF, alive=True)
        await rs.set_player(r, GAME_ID, wolf2)
        with pytest.raises(ValueError, match="wolf"):
            await record_wolf_vote(r, GAME_ID, "wolf1", "wolf2")

    @pytest.mark.asyncio
    async def test_wolf_cannot_target_dead_player(self, r):
        await _seed_game(r, Phase.NIGHT)
        dead = await rs.get_player(r, GAME_ID, "v1")
        dead.alive = False
        await rs.set_player(r, GAME_ID, dead)
        with pytest.raises(ValueError, match="alive"):
            await record_wolf_vote(r, GAME_ID, "wolf1", "v1")


class TestRecordSeerAction:

    @pytest.mark.asyncio
    async def test_seer_action_recorded_and_has_acted_set(self, r):
        await _seed_game(r, Phase.NIGHT)
        await record_seer_action(r, GAME_ID, "seer1", "wolf1")
        assert await rs.get_seer_action(r, GAME_ID) == "wolf1"
        assert (await rs.get_player(r, GAME_ID, "seer1")).has_acted is True

    @pytest.mark.asyncio
    async def test_seer_cannot_act_in_day_phase(self, r):
        await _seed_game(r, Phase.DAY)
        with pytest.raises(ValueError):
            await record_seer_action(r, GAME_ID, "seer1", "wolf1")

    @pytest.mark.asyncio
    async def test_seer_cannot_target_dead_player(self, r):
        await _seed_game(r, Phase.NIGHT)
        dead = await rs.get_player(r, GAME_ID, "v1")
        dead.alive = False
        await rs.set_player(r, GAME_ID, dead)
        with pytest.raises(ValueError, match="alive"):
            await record_seer_action(r, GAME_ID, "seer1", "v1")


# ── F2-3: resolve_night() ─────────────────────────────────────────────────────

class TestResolveNight:

    @pytest.mark.asyncio
    async def test_kill_with_majority_vote(self, r):
        await _seed_game(r, Phase.NIGHT)
        wolf2 = Player("wolf2", "Wolf2", role=Role.WOLF, alive=True)
        await rs.set_player(r, GAME_ID, wolf2)
        await rs.record_wolf_vote(r, GAME_ID, "wolf1", "v1")
        await rs.record_wolf_vote(r, GAME_ID, "wolf2", "v1")
        result = await resolve_night(r, GAME_ID)
        assert result["killed_player_id"] == "v1"
        assert (await rs.get_player(r, GAME_ID, "v1")).alive is False

    @pytest.mark.asyncio
    async def test_no_kill_on_wolf_vote_tie(self, r):
        await _seed_game(r, Phase.NIGHT)
        wolf2 = Player("wolf2", "Wolf2", role=Role.WOLF, alive=True)
        await rs.set_player(r, GAME_ID, wolf2)
        await rs.record_wolf_vote(r, GAME_ID, "wolf1", "v1")
        await rs.record_wolf_vote(r, GAME_ID, "wolf2", "v2")
        result = await resolve_night(r, GAME_ID)
        assert result["killed_player_id"] is None
        assert (await rs.get_player(r, GAME_ID, "v1")).alive is True
        assert (await rs.get_player(r, GAME_ID, "v2")).alive is True

    @pytest.mark.asyncio
    async def test_no_kill_when_no_wolf_votes(self, r):
        await _seed_game(r, Phase.NIGHT)
        result = await resolve_night(r, GAME_ID)
        assert result["killed_player_id"] is None

    @pytest.mark.asyncio
    async def test_seer_result_returned_when_acted(self, r):
        await _seed_game(r, Phase.NIGHT)
        await rs.record_seer_action(r, GAME_ID, "wolf1")
        result = await resolve_night(r, GAME_ID)
        assert result["seer_target_id"] == "wolf1"
        assert result["seer_target_role"] == "WOLF"

    @pytest.mark.asyncio
    async def test_seer_result_none_when_not_acted(self, r):
        await _seed_game(r, Phase.NIGHT)
        result = await resolve_night(r, GAME_ID)
        assert result["seer_target_id"] is None
        assert result["seer_target_role"] is None

    @pytest.mark.asyncio
    async def test_resolve_night_cleans_up_wolf_votes(self, r):
        await _seed_game(r, Phase.NIGHT)
        await rs.record_wolf_vote(r, GAME_ID, "wolf1", "v1")
        await resolve_night(r, GAME_ID)
        assert await rs.get_wolf_votes(r, GAME_ID) == {}

    @pytest.mark.asyncio
    async def test_resolve_night_cleans_up_seer_action(self, r):
        await _seed_game(r, Phase.NIGHT)
        await rs.record_seer_action(r, GAME_ID, "v2")
        await resolve_night(r, GAME_ID)
        assert await rs.get_seer_action(r, GAME_ID) is None

    @pytest.mark.asyncio
    async def test_resolve_night_resets_has_acted(self, r):
        await _seed_game(r, Phase.NIGHT)
        wolf = await rs.get_player(r, GAME_ID, "wolf1")
        wolf.has_acted = True
        await rs.set_player(r, GAME_ID, wolf)
        await resolve_night(r, GAME_ID)
        assert (await rs.get_player(r, GAME_ID, "wolf1")).has_acted is False

    @pytest.mark.asyncio
    async def test_seer_killed_at_night_game_continues(self, r):
        """Edge case: seer is the wolf kill target — game must continue."""
        await _seed_game(r, Phase.NIGHT)
        await rs.record_wolf_vote(r, GAME_ID, "wolf1", "seer1")
        result = await resolve_night(r, GAME_ID)
        assert result["killed_player_id"] == "seer1"
        assert (await rs.get_player(r, GAME_ID, "seer1")).alive is False
        # 1 wolf vs 4 villagers → no winner yet
        assert await check_winner(r, GAME_ID) is None


# ── F2-4: advance_phase() ─────────────────────────────────────────────────────

class TestAdvancePhase:

    @pytest.mark.asyncio
    async def test_day_advances_to_voting(self, r):
        await _seed_game(r, Phase.DAY, round_num=1)
        result = await advance_phase(r, GAME_ID)
        assert result["next_phase"] == Phase.VOTING
        assert result["winner"] is None
        assert result["timer_end"] is not None

    @pytest.mark.asyncio
    async def test_voting_with_elimination_advances_to_night(self, r):
        await _seed_game(r, Phase.VOTING, round_num=1)
        for voter in ["wolf1", "seer1", "v2", "v3"]:
            await rs.record_vote(r, GAME_ID, voter, "v1")
            p = await rs.get_player(r, GAME_ID, voter)
            p.has_voted = True
            await rs.set_player(r, GAME_ID, p)
        result = await advance_phase(r, GAME_ID)
        assert result["next_phase"] == Phase.NIGHT
        assert result["eliminated_player"].player_id == "v1"
        assert result["winner"] is None

    @pytest.mark.asyncio
    async def test_voting_tie_advances_to_night_no_elimination(self, r):
        await _seed_game(r, Phase.VOTING, round_num=1)
        await rs.record_vote(r, GAME_ID, "v1", "v2")
        await rs.record_vote(r, GAME_ID, "v2", "v3")
        result = await advance_phase(r, GAME_ID)
        assert result["next_phase"] == Phase.NIGHT
        assert result["eliminated_player"] is None
        assert result["no_elimination"].reason == "tie"

    @pytest.mark.asyncio
    async def test_voting_no_votes_advances_to_night(self, r):
        await _seed_game(r, Phase.VOTING, round_num=1)
        result = await advance_phase(r, GAME_ID)
        assert result["next_phase"] == Phase.NIGHT
        assert result["no_elimination"].reason == "no_votes"

    @pytest.mark.asyncio
    async def test_night_advances_to_day_increments_round(self, r):
        await _seed_game(r, Phase.NIGHT, round_num=1)
        result = await advance_phase(r, GAME_ID)
        assert result["next_phase"] == Phase.DAY
        assert result["round"] == 2

    @pytest.mark.asyncio
    async def test_voting_wolf_eliminated_villagers_win(self, r):
        """Eliminating the last wolf ends the game immediately."""
        await _seed_game(r, Phase.VOTING, round_num=2)
        for pid in ["seer1", "v1", "v2", "v3"]:
            p = await rs.get_player(r, GAME_ID, pid)
            p.alive = False
            await rs.set_player(r, GAME_ID, p)
        await rs.record_vote(r, GAME_ID, "v4", "wolf1")
        p = await rs.get_player(r, GAME_ID, "v4")
        p.has_voted = True
        await rs.set_player(r, GAME_ID, p)
        result = await advance_phase(r, GAME_ID)
        assert result["winner"] == Winner.VILLAGERS
        assert result["next_phase"] == Phase.ENDED

    @pytest.mark.asyncio
    async def test_night_wolves_win_after_kill(self, r):
        """After night kill reaching parity, wolves win."""
        await _seed_game(r, Phase.NIGHT, round_num=2)
        for pid in ["v1", "v2", "v3", "seer1"]:
            p = await rs.get_player(r, GAME_ID, pid)
            p.alive = False
            await rs.set_player(r, GAME_ID, p)
        await rs.record_wolf_vote(r, GAME_ID, "wolf1", "v4")
        result = await advance_phase(r, GAME_ID)
        assert result["winner"] == Winner.WOLVES
        assert result["next_phase"] == Phase.ENDED

    @pytest.mark.asyncio
    async def test_ended_state_persisted_after_game_over(self, r):
        await _seed_game(r, Phase.VOTING, round_num=1)
        for pid in ["seer1", "v1", "v2", "v3"]:
            p = await rs.get_player(r, GAME_ID, pid)
            p.alive = False
            await rs.set_player(r, GAME_ID, p)
        await rs.record_vote(r, GAME_ID, "v4", "wolf1")
        p = await rs.get_player(r, GAME_ID, "v4")
        p.has_voted = True
        await rs.set_player(r, GAME_ID, p)
        await advance_phase(r, GAME_ID)
        state = await rs.get_game_state(r, GAME_ID)
        assert state["phase"] == "ENDED"
        assert state["winner"] == "VILLAGERS"

    @pytest.mark.asyncio
    async def test_advance_phase_raises_on_missing_game(self, r):
        with pytest.raises(RuntimeError, match="not found"):
            await advance_phase(r, "nonexistent-game")
