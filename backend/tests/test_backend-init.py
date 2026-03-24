"""
tests/test_backend-init.py — Tests for Phase 0 (backend initialization).

Run with:
    pytest tests/test_backend-init.py -v
"""

import pytest
import asyncio
from dataclasses import asdict

# ---------------------------------------------------------------------------
# F0-1 — models/game.py
# ---------------------------------------------------------------------------

class TestRole:
    def test_values_exist(self):
        from models.game import Role
        assert Role.VILLAGER.value == "VILLAGER"
        assert Role.WOLF.value == "WOLF"
        assert Role.SEER.value == "SEER"

    def test_is_str_enum(self):
        from models.game import Role
        # Role must be usable as a string (for JSON serialization)
        assert Role.WOLF == "WOLF"


class TestPhase:
    def test_all_phases_exist(self):
        from models.game import Phase
        for name in ("LOBBY", "DAY", "VOTING", "NIGHT", "ENDED"):
            assert Phase[name].value == name

    def test_is_str_enum(self):
        from models.game import Phase
        assert Phase.DAY == "DAY"


class TestPlayer:
    def test_defaults(self):
        from models.game import Player
        p = Player(player_id="p1", username="Alice")
        assert p.alive is True
        assert p.connected is True
        assert p.role is None
        assert p.has_voted is False
        assert p.has_acted is False

    def test_role_checks(self):
        from models.game import Player, Role
        w = Player("w1", "Wolf", role=Role.WOLF)
        s = Player("s1", "Seer", role=Role.SEER)
        v = Player("v1", "Vill", role=Role.VILLAGER)
        assert w.is_wolf() and not w.is_seer()
        assert s.is_seer() and not s.is_wolf()
        assert v.is_villager() and not v.is_wolf()

    def test_reset_round_flags(self):
        from models.game import Player
        p = Player("p1", "Alice")
        p.has_voted = True
        p.has_acted = True
        p.reset_round_flags()
        assert p.has_voted is False
        assert p.has_acted is False


class TestGameState:
    def _make_state(self):
        from models.game import GameState, Player, Role
        gs = GameState(game_id="game-1")
        gs.players = {
            "w1": Player("w1", "Wolf",     role=Role.WOLF),
            "s1": Player("s1", "Seer",     role=Role.SEER),
            "v1": Player("v1", "Villager", role=Role.VILLAGER),
        }
        return gs

    def test_alive_players(self):
        gs = self._make_state()
        gs.players["v1"].alive = False
        assert len(gs.alive_players()) == 2

    def test_alive_wolves(self):
        gs = self._make_state()
        assert len(gs.alive_wolves()) == 1

    def test_alive_villagers_includes_seer(self):
        gs = self._make_state()
        vills = gs.alive_villagers()
        ids = {p.player_id for p in vills}
        assert "s1" in ids
        assert "v1" in ids
        assert "w1" not in ids

    def test_is_over(self):
        from models.game import GameState, Phase
        gs = GameState(game_id="g2")
        assert not gs.is_over()
        gs.phase = Phase.ENDED
        assert gs.is_over()


# ---------------------------------------------------------------------------
# F0-2 — models/events.py
# ---------------------------------------------------------------------------

class TestEvents:
    def test_vote_update_serializable(self):
        from models.events import VoteUpdatePayload, to_dict
        ev = VoteUpdatePayload(voter_id="p1", target_id="p2", vote_counts={"p2": 1})
        d = to_dict(ev)
        assert d["event"] == "vote_update"
        assert d["vote_counts"] == {"p2": 1}

    def test_player_killed_no_role(self):
        from models.events import PlayerKilledPayload, to_dict
        ev = PlayerKilledPayload(player_id="p3", username="Bob")
        d = to_dict(ev)
        assert "role" not in d or d.get("role") == ""

    def test_seer_result_unicast(self):
        from models.events import SeerResultPayload, to_dict
        ev = SeerResultPayload(target_id="w1", target_name="Wolf", role="WOLF")
        d = to_dict(ev)
        assert d["event"] == "seer_result"
        assert d["role"] == "WOLF"

    def test_game_ended_has_players_list(self):
        from models.events import GameEndedPayload, to_dict
        ev = GameEndedPayload(winner="VILLAGERS", reason="all_wolves_dead", round=3)
        d = to_dict(ev)
        assert d["players"] == []

    def test_role_assigned_wolf_companions(self):
        from models.events import RoleAssignedPayload, to_dict
        companions = [{"player_id": "w2", "username": "Lupo2"}]
        ev = RoleAssignedPayload(role="WOLF", wolf_companions=companions)
        d = to_dict(ev)
        assert len(d["wolf_companions"]) == 1

    def test_no_elimination_reasons(self):
        from models.events import NoEliminationPayload, to_dict
        for reason in ("tie", "no_votes"):
            d = to_dict(NoEliminationPayload(reason=reason))
            assert d["reason"] == reason


# ---------------------------------------------------------------------------
# F0-3a — settings.py
# ---------------------------------------------------------------------------

class TestSettings:
    def test_defaults_exist(self):
        from settings import settings
        assert settings.redis_url.startswith("redis://")
        assert isinstance(settings.app_port, int)
        assert isinstance(settings.cors_origins, list)

    def test_phase_durations(self):
        from settings import settings
        d = settings.phase_durations
        assert d.day == 120
        assert d.voting == 60
        assert d.night == 45
        assert d.night_wolf + d.night_seer == d.night

    def test_redis_kwargs(self):
        from settings import settings
        kwargs = settings.redis_kwargs()
        assert "url" in kwargs


# ---------------------------------------------------------------------------
# F0-3b — infra/redis_client.py
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis(monkeypatch):
    """Replaces init_redis with an in-memory fakeredis connection."""
    import fakeredis.aioredis as fakeredis
    import infra.redis_client as rc

    fake = fakeredis.FakeRedis(decode_responses=True)

    async def _fake_init():
        rc._redis = fake
        return fake

    monkeypatch.setattr(rc, "init_redis", _fake_init)
    monkeypatch.setattr(rc, "_redis", fake)
    return fake


@pytest.mark.asyncio
async def test_redis_set_get(mock_redis):
    from infra.redis_client import get_redis
    r = get_redis()
    await r.set("test:key", "hello")
    val = await r.get("test:key")
    assert val == "hello"


@pytest.mark.asyncio
async def test_redis_keys_schema():
    from infra.redis_client import RedisKeys
    gid = "abc123"
    assert RedisKeys.game_state(gid)   == f"game:{gid}:state"
    assert RedisKeys.votes(gid)        == f"game:{gid}:votes"
    assert RedisKeys.wolf_votes(gid)   == f"game:{gid}:wolf_votes"
    assert RedisKeys.seer_action(gid)  == f"game:{gid}:seer_action"
    assert RedisKeys.pubsub_channel(gid) == f"channel:{gid}:events"


def test_get_redis_raises_if_not_init(monkeypatch):
    import infra.redis_client as rc
    monkeypatch.setattr(rc, "_redis", None)
    with pytest.raises(RuntimeError, match="not initialised"):
        rc.get_redis()