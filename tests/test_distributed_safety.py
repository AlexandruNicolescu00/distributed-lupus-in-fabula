"""
Test delle garanzie distribuite introdotte in Fase 4 (ottimizzazioni):

- P1: creazione host ATOMICA (anti doppio-host) — create_game_state_if_absent
- P2: lock di avanzamento fase (un solo vincitore) — acquire_advance_lock
- P3: patch stato atomica con versione monotona — patch_game_state / state_version
- P7: registro delle stanze attive — add/remove/get_active_rooms
- P15: persistenza bulk in pipeline — set_players_bulk

Rif. teoria: consistency / state-machine-replication / scalabilità.
"""
import fakeredis.aioredis as fakeredis
import pytest

from core import state_store as rs
from models.game import Player, Role


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


# ── P1: host atomico ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_game_state_if_absent_elects_single_host(r):
    first = await rs.create_game_state_if_absent(r, "room-1", "alice")
    second = await rs.create_game_state_if_absent(r, "room-1", "bob")

    assert first is True          # alice crea lo stato → è host
    assert second is False        # bob arriva dopo → NON sovrascrive
    state = await rs.get_game_state(r, "room-1")
    assert state["host_id"] == "alice"


# ── P2: lock di avanzamento ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_acquire_advance_lock_only_one_winner(r):
    won_a = await rs.acquire_advance_lock(r, "room-2", "instance-A")
    won_b = await rs.acquire_advance_lock(r, "room-2", "instance-B")

    assert won_a is True          # primo trigger avanza
    assert won_b is False         # secondo trigger (timer/manuale/sweeper) è no-op


@pytest.mark.asyncio
async def test_advance_lock_is_per_room(r):
    assert await rs.acquire_advance_lock(r, "room-A", "x") is True
    assert await rs.acquire_advance_lock(r, "room-B", "x") is True


# ── P3: patch atomica + versione ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_game_state_merges_and_bumps_version(r):
    await rs.create_game_state_if_absent(r, "room-3", "alice")
    v0 = (await rs.get_game_state(r, "room-3"))["state_version"]

    await rs.patch_game_state(r, "room-3", wolf_count=2)
    await rs.patch_game_state(r, "room-3", seer_count=1)

    state = await rs.get_game_state(r, "room-3")
    assert state["wolf_count"] == 2          # merge non distruttivo
    assert state["seer_count"] == 1
    assert state["host_id"] == "alice"       # campo preesistente preservato
    assert state["state_version"] == v0 + 2  # versione monotona


@pytest.mark.asyncio
async def test_patch_game_state_serializes_enum_values(r):
    await rs.create_game_state_if_absent(r, "room-4", "alice")
    await rs.patch_game_state(r, "room-4", phase=Role.WOLF)  # ha .value
    assert (await rs.get_game_state(r, "room-4"))["phase"] == "WOLF"


# ── P7: registro stanze attive ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_rooms_add_remove(r):
    await rs.add_active_room(r, "room-5")
    await rs.add_active_room(r, "room-6")
    assert set(await rs.get_active_rooms(r)) == {"room-5", "room-6"}

    await rs.remove_active_room(r, "room-5")
    assert set(await rs.get_active_rooms(r)) == {"room-6"}


# ── P15: bulk in pipeline ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_players_bulk_persists_all(r):
    players = [
        Player(player_id="p1", username="p1", role=Role.WOLF),
        Player(player_id="p2", username="p2", role=Role.VILLAGER),
    ]
    await rs.set_players_bulk(r, "room-7", players)

    stored = await rs.get_all_players(r, "room-7")
    assert set(stored.keys()) == {"p1", "p2"}
    assert stored["p1"].role == Role.WOLF


@pytest.mark.asyncio
async def test_set_players_bulk_empty_is_noop(r):
    await rs.set_players_bulk(r, "room-8", [])
    assert await rs.get_all_players(r, "room-8") == {}
