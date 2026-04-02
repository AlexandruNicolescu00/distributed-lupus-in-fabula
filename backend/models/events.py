"""
models/events.py — Socket.IO event structures.

Each dataclass represents the payload of an event that the server
sends to clients (or receives from clients). They are JSON-serializable
via dataclasses.asdict().

Naming convention:
  - *Payload classes → event emitted by the server to clients
  - *Event classes   → event received from client to server

All optional fields use Optional[X] = None for clarity.
"""

from dataclasses import dataclass, asdict
from typing import Optional

from models.game import Role, Phase, Winner


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def to_dict(payload) -> dict:
    """Serializes a dataclass event into a JSON-safe dictionary."""
    return asdict(payload)


# ---------------------------------------------------------------------------
# Events emitted by the server → client (broadcast or unicast)
# ---------------------------------------------------------------------------

@dataclass
class VoteUpdatePayload:
    """
    Event: ``vote_update``
    Direction: broadcast to all alive players.
    Purpose: real-time update of vote distribution during
             the VOTING phase.

    Attributes:
        voter_id:    who cast the vote.
        target_id:   who received the vote.
        vote_counts: map { player_id → number of received votes }.
    """
    event: str                      = "vote_update"
    voter_id:    str                = ""
    target_id:   str                = ""
    vote_counts: dict[str, int]     = None  # type: ignore[assignment]
    skip_count: int = 0   # count of "skip" votes

    def __post_init__(self):
        if self.vote_counts is None:
            self.vote_counts = {}


@dataclass
class GameStateSyncPayload:
    """
    Event: ``game_state_sync``
    Direction: unicast to the connecting client.
    Purpose: provides the latest room/game snapshot after connect or reconnect.
    """
    event: str = "game_state_sync"
    state: dict = None  # type: ignore[assignment]
    players: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.state is None:
            self.state = {}
        if self.players is None:
            self.players = []


@dataclass
class PlayerPresencePayload:
    """
    Shared payload shape for ``player_joined`` and ``player_left``.
    """
    client_id: str = ""
    player: Optional[dict] = None
    players: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.players is None:
            self.players = []


@dataclass
class PlayerJoinedPayload(PlayerPresencePayload):
    event: str = "player_joined"


@dataclass
class PlayerLeftPayload(PlayerPresencePayload):
    event: str = "player_left"


@dataclass
class LobbySettingsUpdatedPayload:
    event: str = "lobby:settings_updated"
    host_id: str = ""
    wolf_count: Optional[int] = None
    seer_count: Optional[int] = None


@dataclass
class LobbyPlayerReadyChangedPayload:
    event: str = "lobby:player_ready_changed"
    client_id: str = ""
    ready: bool = False
    ready_player_ids: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.ready_player_ids is None:
            self.ready_player_ids = []


@dataclass
class RoomClosedPayload:
    event: str = "room_closed"
    reason: str = ""
    host_id: str = ""


@dataclass
class PlayerEliminatedPayload:
    """
    Event: ``player_eliminated``
    Direction: broadcast to all players.
    Purpose: notifies a daytime elimination; the role is revealed.

    Attributes:
        player_id: eliminated player.
        username:  player's name.
        role:      revealed role (only for DAYTIME elimination).
        round:     round in which the elimination occurred.
    """
    event: str      = "player_eliminated"
    player_id: str  = ""
    username: str   = ""
    role: str       = ""      # Role.value
    round: int      = 0
    player: Optional[dict] = None


@dataclass
class PlayerKilledPayload:
    """
    Event: ``player_killed``
    Direction: broadcast to all players.
    Purpose: notifies a nighttime death; the role is NOT revealed.

    Attributes:
        player_id: player killed during the night.
        username:  player's name.
    """
    event: str      = "player_killed"
    player_id: str  = ""
    username: str   = ""
    player: Optional[dict] = None


@dataclass
class SeerResultPayload:
    """
    Event: ``seer_result``
    Direction: unicast to the Seer only.
    Purpose: response to the Seer's nighttime action.

    Attributes:
        target_id:   inspected player.
        target_name: username of the inspected player.
        role:        role of the inspected player.
    """
    event: str          = "seer_result"
    target_id: str      = ""
    target_name: str    = ""
    role: str           = ""   # Role.value


@dataclass
class ActionAcceptedPayload:
    """
    Shared ack payload for accepted player actions.
    """
    target_id: str = ""
    accepted: bool = True


@dataclass
class WolfVoteAcceptedPayload(ActionAcceptedPayload):
    event: str = "wolf_vote"


@dataclass
class SeerActionAcceptedPayload(ActionAcceptedPayload):
    event: str = "seer_action"


@dataclass
class GameEndedPayload:
    """
    Event: ``game_ended``
    Direction: broadcast to all players.
    Purpose: notifies the end of the game with the winner and final state.

    Attributes:
        winner:   who won (VILLAGERS | WOLVES).
        reason:   descriptive string of the reason (e.g. "all_wolves_dead").
        round:    round in which the game ended.
        players:  list of dictionaries { player_id, username, role, alive }
                  to display the final screen.
    """
    event: str              = "game_ended"
    winner: str             = ""    # Winner.value
    reason: str             = ""
    round: int              = 0
    players: list[dict]     = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.players is None:
            self.players = []


@dataclass
class GamePausedPayload:
    """
    Event: ``game_paused``
    Direction: broadcast to all connected players.
    Purpose: notifies that the game is paused (e.g. all wolves disconnected).

    Attributes:
        reason: reason for the pause (e.g. "all_wolves_disconnected").
    """
    event: str   = "game_paused"
    reason: str  = ""


@dataclass
class GameResumedPayload:
    """
    Event: ``game_resumed``
    Direction: broadcast to all connected players.
    Purpose: notifies that the game has resumed after a pause.

    Attributes:
        phase:     current phase at the moment of resumption.
        timer_end: UNIX timestamp of timer expiration (float).
    """
    event: str              = "game_resumed"
    phase: str              = ""     # Phase.value
    timer_end: Optional[float] = None


@dataclass
class PhaseChangedPayload:
    """
    Event: ``phase_changed``
    Direction: broadcast to all players.
    Purpose: notifies a phase transition with the new timer.

    Attributes:
        phase:     new phase.
        round:     current round.
        timer_end: UNIX timestamp of expiration (float).
    """
    event: str              = "phase_changed"
    phase: str              = ""     # Phase.value
    round: int              = 0
    timer_end: Optional[float] = None


@dataclass
class RoleAssignedPayload:
    """
    Event: ``role_assigned``
    Direction: unicast to a single player.
    Purpose: communicates the assigned role at the start of the game.

    Attributes:
        role:             assigned role.
        wolf_companions:  list of { player_id, username } of fellow wolves.
                          Empty list for Villager and Seer.
    """
    event: str                  = "role_assigned"
    role: str                   = ""   # Role.value
    wolf_companions: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.wolf_companions is None:
            self.wolf_companions = []


@dataclass
class NoEliminationPayload:
    """
    Event: ``no_elimination``
    Direction: broadcast to all players.
    Purpose: notifies that the daytime vote did not result in an elimination
             (tie or no votes).

    Attributes:
        reason: "tie" | "no_votes"
    """
    event: str  = "no_elimination"
    reason: str = ""


@dataclass
class ErrorPayload:
    """
    Event: ``error``
    Direction: unicast to the client whose action failed.
    """
    event: str = "error"
    message: str = ""


# ---------------------------------------------------------------------------
# Events received from client → server
# ---------------------------------------------------------------------------

@dataclass
class CastVoteEvent:
    """
    Event: ``cast_vote``  (client → server)
    Purpose: records a player's daytime vote.
    """
    voter_id:  str = ""
    target_id: str = ""


@dataclass
class WolfVoteEvent:
    """
    Event: ``wolf_vote``  (client → server)
    Purpose: records the private nighttime vote of a Werewolf.
    """
    wolf_id:   str = ""
    target_id: str = ""


@dataclass
class SeerActionEvent:
    """
    Event: ``seer_action``  (client → server)
    Purpose: the Seer chooses who to inspect during the night.
    """
    seer_id:   str = ""
    target_id: str = ""


@dataclass
class LobbyUpdateSettingsEvent:
    """
    Event: ``lobby:update_settings``  (client → server)
    Purpose: updates pre-game lobby settings controlled by the host.
    """
    wolf_count: Optional[int] = None
    seer_count: Optional[int] = None


@dataclass
class LobbyPlayerReadyEvent:
    """
    Event: ``lobby:player_ready``  (client → server)
    Purpose: toggles the ready state of a connected lobby player.
    """
    ready: bool = True
