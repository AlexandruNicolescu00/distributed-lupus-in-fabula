"""
models/events.py - Strutture degli eventi Socket.IO.

Ogni dataclass rappresenta il payload di un evento che il server
invia ai client (o riceve dai client). Sono serializzabili in JSON
tramite `dataclasses.asdict()`.

Convenzione di naming:
  - classi *Payload → evento emesso dal server verso i client
  - classi *Event   → evento ricevuto dal client verso il server

Tutti i campi opzionali usano `Optional[X] = None` per chiarezza.
"""

from dataclasses import dataclass, asdict
from typing import Optional

from models.game import Role, Phase, Winner


# ---------------------------------------------------------------------------
# Funzioni ausiliarie
# ---------------------------------------------------------------------------

def to_dict(payload) -> dict:
    """Serializza un evento dataclass in un dizionario compatibile con JSON."""
    return asdict(payload)


# ---------------------------------------------------------------------------
# Eventi emessi dal server → client (broadcast o unicast)
# ---------------------------------------------------------------------------

@dataclass
class VoteUpdatePayload:
    """
    Evento: ``vote_update``
    Direzione: broadcast a tutti i giocatori vivi.
    Scopo: aggiornamento in tempo reale della distribuzione dei voti durante
           la fase VOTING.

    Attributi:
        voter_id:    chi ha espresso il voto.
        target_id:   chi ha ricevuto il voto.
        vote_counts: mappa { player_id → numero di voti ricevuti }.
    """
    event: str                      = "vote_update"
    voter_id:    str                = ""
    target_id:   str                = ""
    vote_counts: dict[str, int]     = None
    skip_count: int = 0   # conteggio dei voti "skip"

    def __post_init__(self):
        if self.vote_counts is None:
            self.vote_counts = {}


@dataclass
class GameStateSyncPayload:
    """
    Evento: ``game_state_sync``
    Direzione: unicast al client che si connette.
    Scopo: fornisce l'ultima istantanea della stanza/partita dopo la connessione
           o la riconnessione.
    """
    event: str = "game_state_sync"
    state: dict = None  
    players: list[str] = None  

    def __post_init__(self):
        if self.state is None:
            self.state = {}
        if self.players is None:
            self.players = []


@dataclass
class PlayerPresencePayload:
    """
    Forma di payload condivisa per ``player_joined`` e ``player_left``.
    """
    client_id: str = ""
    player: Optional[dict] = None
    players: list[str] = None 

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
    ready_player_ids: list[str] = None  

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
    Evento: ``player_eliminated``
    Direzione: broadcast a tutti i giocatori.
    Scopo: notifica un'eliminazione diurna; il ruolo viene rivelato.

    Attributi:
        player_id: giocatore eliminato.
        username:  nome del giocatore.
        role:      ruolo rivelato (solo per eliminazione DIURNA).
        round:     round in cui è avvenuta l'eliminazione.
    """
    event: str      = "player_eliminated"
    player_id: str  = ""
    username: str   = ""
    role: str       = ""      
    round: int      = 0
    player: Optional[dict] = None


@dataclass
class PlayerKilledPayload:
    """
    Evento: ``player_killed``
    Direzione: broadcast a tutti i giocatori.
    Scopo: notifica una morte notturna; il ruolo NON viene rivelato.

    Attributi:
        player_id: giocatore ucciso durante la notte.
        username:  nome del giocatore.
    """
    event: str      = "player_killed"
    player_id: str  = ""
    username: str   = ""
    player: Optional[dict] = None


@dataclass
class SeerResultPayload:
    """
    Evento: ``seer_result``
    Direzione: unicast solo al Veggente.
    Scopo: risposta all'azione notturna del Veggente.

    Attributi:
        target_id:   giocatore ispezionato.
        target_name: nome del giocatore ispezionato.
        role:        ruolo del giocatore ispezionato.
    """
    event: str          = "seer_result"
    target_id: str      = ""
    target_name: str    = ""
    role: str           = ""   


@dataclass
class ActionAcceptedPayload:
    """
    Payload di conferma condiviso per le azioni del giocatore accettate.
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
    Evento: ``game_ended``
    Direzione: broadcast a tutti i giocatori.
    Scopo: notifica la fine della partita con il vincitore e lo stato finale.

    Attributi:
        winner:   chi ha vinto (VILLAGERS | WOLVES).
        reason:   stringa descrittiva del motivo (es. "all_wolves_dead").
        round:    round in cui la partita è terminata.
        players:  lista di dizionari { player_id, username, role, alive }
                  da mostrare nella schermata finale.
    """
    event: str              = "game_ended"
    winner: str             = ""    
    reason: str             = ""
    round: int              = 0
    players: list[dict]     = None  

    def __post_init__(self):
        if self.players is None:
            self.players = []


@dataclass
class GamePausedPayload:
    """
    Evento: ``game_paused``
    Direzione: broadcast a tutti i giocatori connessi.
    Scopo: notifica che la partita è in pausa (es. tutti i lupi disconnessi).

    Attributi:
        reason: motivo della pausa (es. "all_wolves_disconnected").
    """
    event: str   = "game_paused"
    reason: str  = ""


@dataclass
class GameResumedPayload:
    """
    Evento: ``game_resumed``
    Direzione: broadcast a tutti i giocatori connessi.
    Scopo: notifica che la partita è ripresa dopo una pausa.

    Attributi:
        phase:     fase corrente al momento della ripresa.
        timer_end: timestamp UNIX della scadenza del timer (float).
    """
    event: str              = "game_resumed"
    phase: str              = ""     
    timer_end: Optional[float] = None


@dataclass
class PhaseChangedPayload:
    """
    Evento: ``phase_changed``
    Direzione: broadcast a tutti i giocatori.
    Scopo: notifica un cambio di fase con il nuovo timer.

    Attributi:
        phase:     nuova fase.
        round:     round corrente.
        timer_end: timestamp UNIX della scadenza (float).
    """
    event: str              = "phase_changed"
    phase: str              = ""     
    round: int              = 0
    timer_end: Optional[float] = None


@dataclass
class RoleAssignedPayload:
    """
    Evento: ``role_assigned``
    Direzione: unicast a un singolo giocatore.
    Scopo: comunica il ruolo assegnato all'inizio della partita.

    Attributi:
        role:             ruolo assegnato.
        wolf_companions:  lista di { player_id, username } dei compagni lupo.
                          Lista vuota per Villager e Seer.
    """
    event: str                  = "role_assigned"
    role: str                   = ""   
    wolf_companions: list[dict] = None 

    def __post_init__(self):
        if self.wolf_companions is None:
            self.wolf_companions = []


@dataclass
class NoEliminationPayload:
    """
    Evento: ``no_elimination``
    Direzione: broadcast a tutti i giocatori.
    Scopo: notifica che il voto diurno non ha prodotto un'eliminazione
           (pareggio o nessun voto).

    Attributi:
        reason: "tie" | "no_votes"
    """
    event: str  = "no_elimination"
    reason: str = ""


@dataclass
class ErrorPayload:
    """
    Evento: ``error``
    Direzione: unicast al client la cui azione è fallita.
    """
    event: str = "error"
    message: str = ""


# ---------------------------------------------------------------------------
# Eventi ricevuti dal client → server
# ---------------------------------------------------------------------------

@dataclass
class CastVoteEvent:
    """
    Evento: ``cast_vote``  (client → server)
    Scopo: registra il voto diurno di un giocatore.
    """
    voter_id:  str = ""
    target_id: str = ""


@dataclass
class WolfVoteEvent:
    """
    Evento: ``wolf_vote``  (client → server)
    Scopo: registra il voto notturno privato di un Lupo.
    """
    wolf_id:   str = ""
    target_id: str = ""


@dataclass
class SeerActionEvent:
    """
    Evento: ``seer_action``  (client → server)
    Scopo: il Veggente sceglie chi ispezionare durante la notte.
    """
    seer_id:   str = ""
    target_id: str = ""


@dataclass
class LobbyUpdateSettingsEvent:
    """
    Evento: ``lobby:update_settings``  (client → server)
    Scopo: aggiorna le impostazioni della lobby pre-partita controllate dall'host.
    """
    wolf_count: Optional[int] = None
    seer_count: Optional[int] = None


@dataclass
class LobbyPlayerReadyEvent:
    """
    Evento: ``lobby:player_ready``  (client → server)
    Scopo: alterna lo stato di pronto di un giocatore connesso in lobby.
    """
    ready: bool = True
