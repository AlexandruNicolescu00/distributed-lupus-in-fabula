"""
models/game.py - Contratto di tipi condiviso tra tutti i moduli.

Definisce:
  - Role:      ruoli dei giocatori
  - Phase:     fasi del ciclo di gioco
  - Player:    stato di un singolo giocatore
  - GameState: stato globale di una partita
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

SKIP_VOTE_TARGET = "__skip__"  # target_id speciale per i voti "skip" durante la fase VOTING.


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------

class Role(str, Enum):
    """Ruoli assegnabili ai giocatori."""
    VILLAGER = "VILLAGER"
    WOLF     = "WOLF"
    SEER     = "SEER"


class Phase(str, Enum):
    """Fasi del ciclo di gioco.

    Ordine: LOBBY → DAY → VOTING → NIGHT → (loop) → ENDED
    """
    LOBBY  = "LOBBY"
    DAY    = "DAY"
    VOTING = "VOTING"
    NIGHT  = "NIGHT"
    ENDED  = "ENDED"


class Winner(str, Enum):
    """Possibili vincitori di una partita conclusa."""
    VILLAGERS = "VILLAGERS"
    WOLVES    = "WOLVES"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class Player:
    """Rappresenta un giocatore all'interno di una partita.

    Attributi:
        player_id:  identificatore univoco del giocatore (es. socket_id).
        username:   nome visualizzato in chat.
        role:       ruolo assegnato; None finché i ruoli non vengono assegnati.
        alive:      True se il giocatore è ancora in partita.
        connected:  True se la connessione WebSocket è attiva.
        has_voted:  True se il giocatore ha già votato nel round corrente (giorno o notte).
        has_acted:  True se il giocatore ha già svolto l'azione speciale notturna
                    (solo Veggente).
    """
    player_id: str
    username:  str
    role:      Optional[Role] = None
    alive:     bool           = True
    connected: bool           = True
    has_voted: bool           = False
    has_acted: bool           = False  # azione notturna speciale (Veggente)

    def is_wolf(self) -> bool:
        return self.role is Role.WOLF

    def is_seer(self) -> bool:
        return self.role is Role.SEER

    def is_villager(self) -> bool:
        return self.role is Role.VILLAGER

    def reset_round_flags(self) -> None:
        """Reimposta i flag all'inizio di ogni nuovo round/fase."""
        self.has_voted = False
        self.has_acted = False


@dataclass
class GameState:
    """Stato completo di una partita.

    Attributi:
        game_id:    identificatore univoco della partita (UUID).
        phase:      fase corrente del ciclo di gioco.
        round:      numero del round corrente. La partita inizia al round 0 durante
                    la notte iniziale, poi passa al round 1 nel primo giorno.
        players:    dizionario player_id → Player.
        timer_end:  timestamp UNIX (float) della scadenza del timer di fase.
                    None in LOBBY e ENDED.
        paused:     True se la partita è in pausa (es. tutti i lupi disconnessi).
    winner:     vincitore della partita; None finché la partita non è conclusa.
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
    host_id: Optional[str]               = None
    ready_player_ids: list[str]          = field(default_factory=list)

    # ------------------------------------------------------------------
    # Funzioni ausiliarie
    # ------------------------------------------------------------------

    def alive_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]

    def alive_wolves(self) -> list[Player]:
        return [p for p in self.alive_players() if p.is_wolf()]

    def alive_villagers(self) -> list[Player]:
        """Restituisce i villager vivi + il veggente (cioè i non-lupi)."""
        return [p for p in self.alive_players() if not p.is_wolf()]

    def connected_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.connected]

    def get_player(self, player_id: str) -> Optional[Player]:
        return self.players.get(player_id)

    def player_count(self) -> int:
        return len(self.players)

    def is_over(self) -> bool:
        return self.phase is Phase.ENDED
