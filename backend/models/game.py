"""
models/game.py — Shared type contract across all modules.

Defines:
  - Role:      player roles
  - Phase:     game loop phases
  - Player:    state of a single player
  - GameState: global state of a game
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

SKIP_VOTE_TARGET = "__skip__"  # Special target_id for "skip" votes during the VOTING phase.


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Role(str, Enum):
    """Roles assignable to players."""
    VILLAGER = "VILLAGER"
    WOLF     = "WOLF"
    SEER     = "SEER"


class Phase(str, Enum):
    """Game loop phases.

    Order: LOBBY → DAY → VOTING → NIGHT → (loop) → ENDED
    """
    LOBBY  = "LOBBY"
    DAY    = "DAY"
    VOTING = "VOTING"
    NIGHT  = "NIGHT"
    ENDED  = "ENDED"


class Winner(str, Enum):
    """Possible winners of a finished game."""
    VILLAGERS = "VILLAGERS"
    WOLVES    = "WOLVES"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Player:
    """Represents a player within a game.

    Attributes:
        player_id:  unique identifier of the player (e.g. socket_id).
        username:   name displayed in chat.
        role:       assigned role; None until roles are assigned.
        alive:      True if the player is still in the game.
        connected:  True if the WebSocket connection is active.
        has_voted:  True if the player has already voted in the current round (day or night).
        has_acted:  True if the player has already performed the special nighttime action
                    (Seer only).
    """
    player_id: str
    username:  str
    role:      Optional[Role] = None
    alive:     bool           = True
    connected: bool           = True
    has_voted: bool           = False
    has_acted: bool           = False  # special nighttime action (Seer)

    def is_wolf(self) -> bool:
        return self.role is Role.WOLF

    def is_seer(self) -> bool:
        return self.role is Role.SEER

    def is_villager(self) -> bool:
        return self.role is Role.VILLAGER

    def reset_round_flags(self) -> None:
        """Resets flags at the start of each new round/phase."""
        self.has_voted = False
        self.has_acted = False


@dataclass
class GameState:
    """Complete state of a game.

    Attributes:
        game_id:    unique identifier of the game (UUID).
        phase:      current phase of the game loop.
        round:      current round number (starts at 1 on the first night).
        players:    dictionary player_id → Player.
        timer_end:  UNIX timestamp (float) of phase timer expiration.
                    None in LOBBY and ENDED.
        paused:     True if the game is paused (e.g. all wolves disconnected).
        winner:     winner of the game; None until the game is finished.
    """
    game_id:   str
    phase:     Phase                     = Phase.LOBBY
    round:     int                       = 0
    players:   dict[str, Player]         = field(default_factory=dict)
    timer_end: Optional[float]           = None
    paused:    bool                      = False
    winner:    Optional[Winner]          = None
    wolf_count: Optional[int]            = None
    seer_count: Optional[int]            = None

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def alive_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]

    def alive_wolves(self) -> list[Player]:
        return [p for p in self.alive_players() if p.is_wolf()]

    def alive_villagers(self) -> list[Player]:
        """Returns alive villagers + seer (i.e. non-wolves)."""
        return [p for p in self.alive_players() if not p.is_wolf()]

    def connected_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.connected]

    def get_player(self, player_id: str) -> Optional[Player]:
        return self.players.get(player_id)

    def player_count(self) -> int:
        return len(self.players)

    def is_over(self) -> bool:
        return self.phase is Phase.ENDED
