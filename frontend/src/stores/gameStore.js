import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { gameApi } from '@/services/api'
import { useSocket } from '@/composables/useSocket'

// -------------------------------------------------------
// Fasi — allineate con Phase(str, Enum) in backend
// -------------------------------------------------------
export const PHASES = {
  LOBBY:  'LOBBY',
  DAY:    'DAY',
  VOTING: 'VOTING',
  NIGHT:  'NIGHT',
  ENDED:  'ENDED',
}

// -------------------------------------------------------
// Ruoli — allineati con Role(str, Enum) in backend
// -------------------------------------------------------
export const ROLES = {
  VILLAGER: 'VILLAGER',
  WOLF:     'WOLF',
  SEER:     'SEER',
}

// -------------------------------------------------------
// Vincitori — allineati con Winner(str, Enum) in backend
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
  const myRole           = ref(null)     // uno dei ROLES
  const wolfCompanions  = ref([])       // [{ player_id, username }] — solo per i lupi
  const winner          = ref(null)     // uno dei WINNERS | null
  const timerEnd        = ref(null)     // timestamp UNIX float (da backend)
  const pendingRoleSetup = ref({ wolves: 1, seers: 1, villagers: 3 })
  const currentRoomCode = ref('')
  const isLoading       = ref(false)
  const error           = ref(null)
  const isPaused        = ref(false)
  const pauseReason     = ref('')
  const seerResult      = ref(null)     // { targetId, targetName, role } — solo per il veggente
  const noElimination   = ref(false)
  const noEliminationReason = ref('')
  const voteMap         = ref({})       // { voterId: targetId }
  const gameEndPlayers  = ref([])       // lista finale con ruoli rivelati
  const roomClosedAt    = ref(0)
  const roomClosedMessage = ref('')

  const { emit, on } = useSocket()

  // ---- GETTERS ----
  const alivePlayers  = computed(() => players.value.filter((p) => p.alive))
  const deadPlayers   = computed(() => players.value.filter((p) => !p.alive))
  const me            = computed(() => players.value.find((p) => p.player_id === currentPlayerId.value))
  const isAlive       = computed(() => me.value?.alive ?? false)
  const isWolf        = computed(() => myRole.value === ROLES.WOLF)
  const isSeer        = computed(() => myRole.value === ROLES.SEER)
  const isVillager    = computed(() => myRole.value === ROLES.VILLAGER)

  const secondsLeft = computed(() => {
    if (!timerEnd.value) return null
    const nowSec = Date.now() / 1000
    return Math.max(0, Math.ceil(timerEnd.value - nowSec))
  })

  const phaseDurations = { DAY: 120, VOTING: 60, NIGHT: 45 }
  const timerProgress = computed(() => {
    const total = phaseDurations[phase.value]
    if (!total || !secondsLeft.value) return 0
    return (secondsLeft.value / total) * 100
  })

  const voteCount = computed(() => {
    const counts = {}
    if (!voteMap.value || Object.keys(voteMap.value).length === 0) return counts
    Object.values(voteMap.value).forEach(target => {
      if (target) counts[target] = (counts[target] || 0) + 1
    })
    return counts
  })

  function normalizePlayers(remotePlayers = []) {
    if (!Array.isArray(remotePlayers)) return []

    return remotePlayers.map((player) => ({
      player_id: player.player_id ?? player.id,
      username: player.username ?? player.name ?? player.player_id ?? player.id,
      role: player.role ?? null,
      alive: player.alive ?? true,
      connected: player.connected ?? true,
    }))
  }

  function normalizeRoleSetup(totalPlayers, roleSetup = pendingRoleSetup.value) {
    const safeTotal = Math.max(0, totalPlayers)
    let seers = Math.min(Math.max(roleSetup?.seers ?? 0, 0), safeTotal >= 5 ? 1 : 0)
    let wolves = Math.max(1, roleSetup?.wolves ?? 1)

    const wolvesCap = Math.max(1, Math.floor((Math.max(safeTotal - seers, 0) - 1) / 2))
    wolves = Math.min(wolves, wolvesCap)

    let villagers = Math.max(0, safeTotal - wolves - seers)

    while (villagers <= wolves && wolves > 1) {
      wolves -= 1
      villagers = Math.max(0, safeTotal - wolves - seers)
    }

    while (villagers <= wolves && seers > 0) {
      seers -= 1
      villagers = Math.max(0, safeTotal - wolves - seers)
    }

    return { wolves, seers, villagers }
  }

  function makeSeed(input) {
    let seed = 2166136261
    for (const char of input) {
      seed ^= char.charCodeAt(0)
      seed = Math.imul(seed, 16777619)
    }
    return seed >>> 0
  }

  function seededShuffle(items, seedInput) {
    const shuffled = [...items]
    let seed = makeSeed(seedInput)

    for (let index = shuffled.length - 1; index > 0; index -= 1) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
      const swapIndex = seed % (index + 1)
      ;[shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]]
    }

    return shuffled
  }

  function assignLocalRoles(normalizedPlayers, roleSetup, roomCode = '') {
    const totalPlayers = normalizedPlayers.length
    if (totalPlayers === 0) return []

    const normalizedSetup = normalizeRoleSetup(totalPlayers, roleSetup)
    const rolePool = [
      ...Array(normalizedSetup.wolves).fill(ROLES.WOLF),
      ...Array(normalizedSetup.seers).fill(ROLES.SEER),
      ...Array(normalizedSetup.villagers).fill(ROLES.VILLAGER),
    ]

    const shuffledRoles = seededShuffle(
      rolePool,
      `${roomCode}:${normalizedPlayers.map((player) => player.player_id).join('|')}`
    )

    return normalizedPlayers.map((player, index) => ({
      ...player,
      role: shuffledRoles[index] ?? ROLES.VILLAGER,
      alive: player.alive ?? true,
    }))
  }

  function bootstrapFromLobby(lobbyPlayers = [], playerId = null, roleSetup = null, roomCode = '') {
    const normalizedPlayers = normalizePlayers(lobbyPlayers)
    const effectiveRoleSetup = normalizeRoleSetup(
      normalizedPlayers.length,
      roleSetup ?? pendingRoleSetup.value
    )

    if (normalizedPlayers.length > 0) {
      players.value = assignLocalRoles(normalizedPlayers, effectiveRoleSetup, roomCode)
    }

    if (playerId) {
      currentPlayerId.value = playerId
    }

    currentRoomCode.value = roomCode || currentRoomCode.value
    pendingRoleSetup.value = effectiveRoleSetup
    myRole.value = players.value.find((player) => player.player_id === currentPlayerId.value)?.role ?? null

    if (phase.value === PHASES.LOBBY) {
      phase.value = PHASES.DAY
    }
  }

  // ---- ACTIONS ----

  /** * Gestisce lo snapshot completo inviato dal backend (es. alla riconnessione).
   * Il payload contiene 'state' (snapshot Redis) e 'players' (lista client_id).
   */
  function handleStateSync(payload) {
    console.log('[GameStore] Ricevuto Full State Sync:', payload)
    
    // Se il payload contiene la chiave 'state' (formato RedisEvent del backend)
    if (payload.state) {
      _applyState(payload.state)
    } else {
      // Fallback nel caso il payload sia direttamente l'oggetto stato
      _applyState(payload)
    }

    if (payload.players?.length) {
      const normalizedPlayers = normalizePlayers(payload.players)
      const rolesMissing = normalizedPlayers.every((player) => !player.role)
      players.value = rolesMissing
        ? assignLocalRoles(normalizedPlayers, pendingRoleSetup.value, currentRoomCode.value)
        : normalizedPlayers
      myRole.value = players.value.find((player) => player.player_id === currentPlayerId.value)?.role ?? myRole.value
    }
  }

  /** Carica lo stato iniziale via API REST */
  async function loadState(lobbyCode) {
    isLoading.value = true
    try {
      currentRoomCode.value = lobbyCode
      const data = await gameApi.getState(lobbyCode)
      _applyState(data)
    } catch (err) {
      error.value = err.message
    } finally {
      isLoading.value = false
    }
  }

  async function vote(lobbyCode, targetId) {
    emit('cast_vote', { voter_id: currentPlayerId.value, target_id: targetId })
  }

  async function wolfVote(targetId) {
    emit('wolf_vote', { wolf_id: currentPlayerId.value, target_id: targetId })
  }

  async function seerAction(targetId) {
    emit('seer_action', { seer_id: currentPlayerId.value, target_id: targetId })
  }

  function emitRoomClosed(roomCode = currentRoomCode.value) {
    emit('room_closed', {
      lobby_code: roomCode,
      reason: "L'host ha chiuso la partita.",
    })
  }

  /** Registra i listener Socket.IO */
  function listenToGameEvents() {
    // --- game_state_sync ---
    // Fondamentale per la fault tolerance distribuita
    on('game_state_sync', handleStateSync)

    // Il backend attuale ribatte start_game senza ancora pubblicare phase_changed.
    // Portiamo comunque i client nella GameView e usiamo i dati lobby come base.
    on('start_game', (data = {}) => {
      pendingRoleSetup.value = normalizeRoleSetup(
        players.value.length || data.payload?.players?.length || 0,
        data.payload?.role_setup ?? pendingRoleSetup.value
      )
      if (data.payload?.players?.length) {
        const normalizedPlayers = normalizePlayers(data.payload.players)
        const rolesMissing = normalizedPlayers.every((player) => !player.role)
        players.value = rolesMissing
          ? assignLocalRoles(normalizedPlayers, pendingRoleSetup.value, currentRoomCode.value)
          : normalizedPlayers
        myRole.value = players.value.find((player) => player.player_id === currentPlayerId.value)?.role ?? myRole.value
      } else if (players.value.length > 0 && players.value.every((player) => !player.role)) {
        players.value = assignLocalRoles(players.value, pendingRoleSetup.value, currentRoomCode.value)
        myRole.value = players.value.find((player) => player.player_id === currentPlayerId.value)?.role ?? myRole.value
      }
      if (phase.value === PHASES.LOBBY) {
        phase.value = PHASES.DAY
      }
    })

    on('phase_changed', ({ phase: newPhase, round: newRound, timer_end }) => {
      phase.value             = newPhase
      round.value             = newRound
      timerEnd.value          = timer_end
      isPaused.value          = false
      noElimination.value     = false
      seerResult.value        = null
      voteMap.value           = {}
    })

    on('role_assigned', ({ role, wolf_companions }) => {
      myRole.value         = role
      wolfCompanions.value = wolf_companions ?? []
    })

    on('vote_update', ({ voter_id, target_id }) => {
      voteMap.value = { ...voteMap.value, [voter_id]: target_id }
    })

    on('player_eliminated', ({ player_id, role: revealedRole }) => {
      const player = players.value.find((p) => p.player_id === player_id)
      if (player) {
        player.alive = false
        player.role  = revealedRole
      }
    })

    on('player_killed', ({ player_id }) => {
      const player = players.value.find((p) => p.player_id === player_id)
      if (player) player.alive = false
    })

    on('seer_result', ({ target_id, target_name, role }) => {
      seerResult.value = { targetId: target_id, targetName: target_name, role }
    })

    on('no_elimination', ({ reason }) => {
      noElimination.value       = true
      noEliminationReason.value = reason
    })

    on('game_ended', ({ winner: w, round: finalRound, players: finalPlayers }) => {
      winner.value          = w
      phase.value           = PHASES.ENDED
      round.value           = finalRound
      gameEndPlayers.value  = finalPlayers ?? []
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

    on('game_paused', ({ reason }) => {
      isPaused.value    = true
      pauseReason.value = reason ?? ''
    })

    on('game_resumed', ({ phase: resumePhase, timer_end }) => {
      isPaused.value = false
      if (resumePhase) phase.value   = resumePhase
      if (timer_end)   timerEnd.value = timer_end
    })

    on('room_closed', ({ payload = {} }) => {
      roomClosedMessage.value = payload.reason ?? "L'host ha chiuso la partita."
      roomClosedAt.value = Date.now()
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
    pendingRoleSetup.value    = { wolves: 1, seers: 1, villagers: 3 }
    currentRoomCode.value     = ''
    isPaused.value            = false
    pauseReason.value         = ''
    seerResult.value          = null
    noElimination.value       = false
    noEliminationReason.value = ''
    voteMap.value             = {}
    gameEndPlayers.value      = []
    roomClosedAt.value        = 0
    roomClosedMessage.value   = ''
    error.value               = null
  }

  function _applyState(data) {
    phase.value           = data.phase           ?? PHASES.LOBBY
    round.value           = data.round           ?? 0
    if (data.role_setup) {
      pendingRoleSetup.value = normalizeRoleSetup(
        Array.isArray(data.players) ? data.players.length : players.value.length,
        data.role_setup
      )
    }

    const normalizedPlayers = normalizePlayers(data.players ?? [])
    const rolesMissing = normalizedPlayers.length > 0 && normalizedPlayers.every((player) => !player.role)
    players.value = rolesMissing
      ? assignLocalRoles(normalizedPlayers, pendingRoleSetup.value, currentRoomCode.value)
      : normalizedPlayers
    currentPlayerId.value = data.currentPlayerId ?? currentPlayerId.value
    myRole.value          = data.myRole ?? players.value.find((player) => player.player_id === currentPlayerId.value)?.role ?? myRole.value
    winner.value          = data.winner          ?? null
    timerEnd.value        = data.timer_end       ?? null // Mappatura snake_case da Redis
    isPaused.value        = data.paused          ?? false  
    voteMap.value         = data.voteMap         ?? {}
  }

  return {
    phase, round, players, currentPlayerId, myRole, wolfCompanions,
    winner, timerEnd, isLoading, error, isPaused, pauseReason,
    seerResult, noElimination, noEliminationReason, voteMap,
    gameEndPlayers, roomClosedAt, roomClosedMessage,
    alivePlayers, deadPlayers, me, isAlive, isWolf, isSeer, isVillager,
    secondsLeft, timerProgress, voteCount,
    normalizeRoleSetup, normalizePlayers, bootstrapFromLobby, loadState, handleStateSync,
    vote, wolfVote, seerAction, emitRoomClosed, listenToGameEvents, reset,
    PHASES, ROLES, WINNERS,
  }
})
