import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { gameApi } from '@/services/api'
import { useSocket } from '@/composables/useSocket'

// -------------------------------------------------------
// Fasi — allineate con Phase(str, Enum) in models/game.py
// Valori MAIUSCOLO come da backend
// -------------------------------------------------------
export const PHASES = {
  LOBBY:  'LOBBY',
  DAY:    'DAY',
  VOTING: 'VOTING',
  NIGHT:  'NIGHT',
  ENDED:  'ENDED',
}

// -------------------------------------------------------
// Ruoli — allineati con Role(str, Enum) in models/game.py
// Valori MAIUSCOLO come da backend
// -------------------------------------------------------
export const ROLES = {
  VILLAGER: 'VILLAGER',
  WOLF:     'WOLF',
  SEER:     'SEER',
}

// -------------------------------------------------------
// Vincitori — allineati con Winner(str, Enum) in models/game.py
// -------------------------------------------------------
export const WINNERS = {
  VILLAGERS: 'VILLAGERS',
  WOLVES:    'WOLVES',
}

export const useGameStore = defineStore('game', () => {
  // ---- STATE ----
  const phase           = ref(PHASES.LOBBY)
  const round           = ref(0)
  const players         = ref([])       // { player_id, username, role, alive, connected }
  const currentPlayerId = ref(null)
  const myRole          = ref(null)     // uno dei ROLES
  const wolfCompanions  = ref([])       // [{ player_id, username }] — solo per i lupi
  const winner          = ref(null)     // uno dei WINNERS | null
  const timerEnd        = ref(null)     // timestamp UNIX float (da backend) — era phaseDeadline
  const isLoading       = ref(false)
  const error           = ref(null)
  const isPaused        = ref(false)
  const pauseReason     = ref('')
  const seerResult      = ref(null)     // { targetId, targetName, role } — solo per il veggente
  const noElimination   = ref(false)
  const noEliminationReason = ref('')
  const voteMap         = ref({})       // { voterId: targetId }
  const gameEndPlayers  = ref([])       // lista finale con ruoli rivelati

  const { emit, on } = useSocket()

  // ---- GETTERS ----
  const alivePlayers  = computed(() => players.value.filter((p) => p.alive))
  const deadPlayers   = computed(() => players.value.filter((p) => !p.alive))
  const me            = computed(() => players.value.find((p) => p.player_id === currentPlayerId.value))
  const isAlive       = computed(() => me.value?.alive ?? false)
  const isWolf        = computed(() => myRole.value === ROLES.WOLF)
  const isSeer        = computed(() => myRole.value === ROLES.SEER)
  const isVillager    = computed(() => myRole.value === ROLES.VILLAGER)

  /**
   * Secondi rimanenti calcolati dal timestamp UNIX del backend.
   * Il backend invia timer_end come float UNIX (es. 1718000000.0).
   */
  const secondsLeft = computed(() => {
    if (!timerEnd.value) return null
    const nowSec = Date.now() / 1000
    return Math.max(0, Math.ceil(timerEnd.value - nowSec))
  })

  /**
   * Progresso percentuale del timer (100% = pieno, 0% = scaduto).
   * Usa le durate di fase da settings.py del backend.
   */
  const phaseDurations = { DAY: 120, VOTING: 60, NIGHT: 45 }
  const timerProgress = computed(() => {
    const total = phaseDurations[phase.value]
    if (!total || !secondsLeft.value) return 0
    return (secondsLeft.value / total) * 100
  })

  /**
   * Conta i voti per ciascun target dal voteMap locale.
   * Restituisce sempre un oggetto { targetId: count }.
   */
  const voteCount = computed(() => {
    const counts = {}
    
    // Se voteMap è vuoto o undefined, restituisce oggetto vuoto
    if (!voteMap.value || Object.keys(voteMap.value).length === 0) {
      return counts
    }
    
    // Conta i voti per ogni target
    Object.values(voteMap.value).forEach(target => {
      if (target) {
        counts[target] = (counts[target] || 0) + 1
      }
    })
    
    return counts
  })

  // ---- ACTIONS ----

  /** Carica lo stato della partita (resume dopo disconnessione) */
  async function loadState(lobbyCode) {
    isLoading.value = true
    try {
      const data = await gameApi.getState(lobbyCode)
      _applyState(data)
    } catch (err) {
      error.value = err.message
    } finally {
      isLoading.value = false
    }
  }

  /** Vota un giocatore durante VOTING — emette cast_vote come da CastVoteEvent */
  async function vote(lobbyCode, targetId) {
    try {
      // Emit diretto via Socket.IO (come da events.py: CastVoteEvent)
      emit('cast_vote', {
        voter_id:  currentPlayerId.value,
        target_id: targetId,
      })
    } catch (err) {
      error.value = err.message
    }
  }

  /** Azione notturna lupo — emette wolf_vote come da WolfVoteEvent */
  async function wolfVote(targetId) {
    emit('wolf_vote', {
      wolf_id:   currentPlayerId.value,
      target_id: targetId,
    })
  }

  /** Azione notturna veggente — emette seer_action come da SeerActionEvent */
  async function seerAction(targetId) {
    emit('seer_action', {
      seer_id:   currentPlayerId.value,
      target_id: targetId,
    })
  }

  /**
   * Registra tutti i listener Socket.IO per gli eventi di gioco.
   * Nomi e payload allineati con models/events.py della PR backend.
   * Chiamare una volta sola quando si entra nella GameView.
   */
  function listenToGameEvents() {

    // --- phase_changed → PhaseChangedPayload ---
    // { event, phase, round, timer_end }
    on('phase_changed', ({ phase: newPhase, round: newRound, timer_end }) => {
      phase.value             = newPhase        // es. "DAY", "VOTING", "NIGHT"
      round.value             = newRound
      timerEnd.value          = timer_end       // timestamp UNIX float
      isPaused.value          = false
      noElimination.value     = false
      seerResult.value        = null
      voteMap.value           = {}
    })

    // --- role_assigned → RoleAssignedPayload ---
    // { event, role, wolf_companions }
    on('role_assigned', ({ role, wolf_companions }) => {
      myRole.value         = role               // es. "WOLF", "VILLAGER", "SEER"
      wolfCompanions.value = wolf_companions ?? []
    })

    // --- vote_update → VoteUpdatePayload ---
    // { event, voter_id, target_id, vote_counts, skip_count }
    on('vote_update', ({ voter_id, target_id }) => {
      voteMap.value = { ...voteMap.value, [voter_id]: target_id }
    })

    // --- player_eliminated → PlayerEliminatedPayload ---
    // { event, player_id, username, role, round }
    // Eliminazione di giorno — ruolo rivelato
    on('player_eliminated', ({ player_id, role: revealedRole }) => {
      const player = players.value.find((p) => p.player_id === player_id)
      if (player) {
        player.alive = false
        player.role  = revealedRole   // ruolo rivelato alla community
      }
    })

    // --- player_killed → PlayerKilledPayload ---
    // { event, player_id, username }
    // Uccisione di notte — ruolo NON rivelato
    on('player_killed', ({ player_id }) => {
      const player = players.value.find((p) => p.player_id === player_id)
      if (player) player.alive = false
    })

    // --- seer_result → SeerResultPayload ---
    // { event, target_id, target_name, role }
    // Unicast solo al veggente
    on('seer_result', ({ target_id, target_name, role }) => {
      seerResult.value = {
        targetId:   target_id,
        targetName: target_name,
        role,                         // es. "WOLF" o "VILLAGER"
      }
    })

    // --- no_elimination → NoEliminationPayload ---
    // { event, reason }  reason: "tie" | "no_votes"
    on('no_elimination', ({ reason }) => {
      noElimination.value       = true
      noEliminationReason.value = reason
    })

    // --- game_ended → GameEndedPayload ---
    // { event, winner, reason, round, players }
    // winner: "VILLAGERS" | "WOLVES"
    on('game_ended', ({ winner: w, reason, round: finalRound, players: finalPlayers }) => {
      winner.value          = w
      phase.value           = PHASES.ENDED
      round.value           = finalRound
      gameEndPlayers.value  = finalPlayers ?? []

      // Aggiorna la lista locale con i ruoli rivelati
      if (finalPlayers?.length) {
        players.value = finalPlayers.map((fp) => ({
          player_id: fp.player_id,
          username:  fp.username,
          role:      fp.role,
          alive:     fp.alive,
          connected: true,
        }))
      }
    })

    // --- game_paused → GamePausedPayload ---
    // { event, reason }
    on('game_paused', ({ reason }) => {
      isPaused.value    = true
      pauseReason.value = reason ?? ''
    })

    // --- game_resumed → GameResumedPayload ---
    // { event, phase, timer_end }
    on('game_resumed', ({ phase: resumePhase, timer_end }) => {
      isPaused.value = false
      if (resumePhase) phase.value   = resumePhase
      if (timer_end)   timerEnd.value = timer_end
    })
  }

  function reset() {
    phase.value               = PHASES.LOBBY
    round.value               = 0
    players.value             = []
    myRole.value              = null
    wolfCompanions.value      = []
    winner.value              = null
    timerEnd.value            = null
    isPaused.value            = false
    pauseReason.value         = ''
    seerResult.value          = null
    noElimination.value       = false
    noEliminationReason.value = ''
    voteMap.value             = {}
    gameEndPlayers.value      = []
    error.value               = null
  }

  // ---- PRIVATO ----
  function _applyState(data) {
    phase.value           = data.phase          ?? PHASES.LOBBY
    round.value           = data.round          ?? 0
    players.value         = data.players        ?? []
    currentPlayerId.value = data.currentPlayerId ?? currentPlayerId.value
    myRole.value          = data.myRole         ?? null
    winner.value          = data.winner         ?? null
    timerEnd.value        = data.timer_end      ?? null   // snake_case come dal backend
    isPaused.value        = data.paused         ?? false  
    voteMap.value         = data.voteMap        ?? {}
  }

  return {
    // state
    phase, round, players, currentPlayerId, myRole, wolfCompanions,
    winner, timerEnd, isLoading, error, isPaused, pauseReason,
    seerResult, noElimination, noEliminationReason, voteMap,
    gameEndPlayers,
    // getters
    alivePlayers, deadPlayers, me, isAlive, isWolf, isSeer, isVillager,
    secondsLeft, timerProgress, voteCount,
    // actions
    loadState, vote, wolfVote, seerAction, listenToGameEvents, reset,
    // costanti esportate
    PHASES, ROLES, WINNERS,
  }
})