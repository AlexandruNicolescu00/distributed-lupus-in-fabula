import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { gameApi } from '@/services/api'
import { useSocket } from '@/composables/useSocket'

export const PHASES = {
  LOBBY: 'LOBBY',
  DAY: 'DAY',
  VOTING: 'VOTING',
  NIGHT: 'NIGHT',
  ENDED: 'ENDED',
}

export const ROLES = {
  VILLAGER: 'VILLAGER',
  WOLF: 'WOLF',
  SEER: 'SEER',
}

export const WINNERS = {
  VILLAGERS: 'VILLAGERS',
  WOLVES: 'WOLVES',
}

export const useGameStore = defineStore('game', () => {
  const phase = ref(PHASES.LOBBY)
  const round = ref(0)
  const players = ref([])
  const currentPlayerId = ref(null)
  const hostId = ref(null)
  const myRole = ref(null)
  const wolfCompanions = ref([])
  const winner = ref(null)
  const timerEnd = ref(null)
  const pendingRoleSetup = ref({ wolves: 1, seers: 1, villagers: 3 })
  const currentRoomCode = ref('')
  const isLoading = ref(false)
  const error = ref(null)
  const isPaused = ref(false)
  const pauseReason = ref('')
  const seerResult = ref(null)
  const noElimination = ref(false)
  const noEliminationReason = ref('')
  const voteMap = ref({})
  const gameEndPlayers = ref([])
  const roomClosedAt = ref(0)
  const roomClosedMessage = ref('')

  const { emit, on } = useSocket()

  const alivePlayers = computed(() => players.value.filter((player) => player.alive))
  const deadPlayers = computed(() => players.value.filter((player) => !player.alive))
  const me = computed(() => players.value.find((player) => player.player_id === currentPlayerId.value))
  const isAlive = computed(() => me.value?.alive ?? false)
  const isWolf = computed(() => myRole.value === ROLES.WOLF)
  const isSeer = computed(() => myRole.value === ROLES.SEER)
  const isVillager = computed(() => myRole.value === ROLES.VILLAGER)

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
    Object.values(voteMap.value).forEach((target) => {
      if (target) counts[target] = (counts[target] || 0) + 1
    })
    return counts
  })

  function extractPayload(message) {
    if (!message || typeof message !== 'object') return {}
    return message.payload && typeof message.payload === 'object'
      ? message.payload
      : message
  }

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

  function resolveRoleSetupFromState(state = {}, fallbackPlayersLength = players.value.length) {
    const totalPlayers = Array.isArray(state.players) ? state.players.length : fallbackPlayersLength

    if (state.role_setup) {
      return normalizeRoleSetup(totalPlayers, state.role_setup)
    }

    if (state.wolf_count != null || state.seer_count != null) {
      return normalizeRoleSetup(totalPlayers, {
        wolves: state.wolf_count ?? pendingRoleSetup.value.wolves,
        seers: state.seer_count ?? pendingRoleSetup.value.seers,
      })
    }

    return normalizeRoleSetup(totalPlayers, pendingRoleSetup.value)
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

  function handleStateSync(message) {
    const payload = extractPayload(message)
    const state = payload.state || payload

    _applyState(state)

    if (payload.players?.length) {
      const normalizedPlayers = normalizePlayers(payload.players)
      const rolesMissing = normalizedPlayers.every((player) => !player.role)
      players.value = rolesMissing
        ? assignLocalRoles(normalizedPlayers, pendingRoleSetup.value, currentRoomCode.value)
        : normalizedPlayers
      myRole.value = players.value.find((player) => player.player_id === currentPlayerId.value)?.role ?? myRole.value
    }
  }

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

  function listenToGameEvents() {
    on('game_state_sync', handleStateSync)

    const handleGameStart = (message = {}) => {
      const payload = extractPayload(message)
      const incomingPlayers = payload.players?.length ? payload.players : players.value
      pendingRoleSetup.value = resolveRoleSetupFromState(
        payload.role_setup ? { role_setup: payload.role_setup } : payload,
        incomingPlayers.length
      )

      if (payload.players?.length) {
        const normalizedPlayers = normalizePlayers(payload.players)
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
    }

    on('game_start', handleGameStart)
    on('start_game', handleGameStart)

    on('phase_changed', (message) => {
      const payload = extractPayload(message)
      phase.value = payload.phase ?? phase.value
      round.value = payload.round ?? round.value
      timerEnd.value = payload.timer_end ?? null
      isPaused.value = false
      noElimination.value = false
      seerResult.value = null
      voteMap.value = {}
    })

    on('role_assigned', (message) => {
      const payload = extractPayload(message)
      myRole.value = payload.role ?? myRole.value
      wolfCompanions.value = payload.wolf_companions ?? []
    })

    on('vote_update', (message) => {
      const payload = extractPayload(message)
      voteMap.value = { ...voteMap.value, [payload.voter_id]: payload.target_id }
    })

    on('player_eliminated', (message) => {
      const payload = extractPayload(message)
      const player = players.value.find((entry) => entry.player_id === payload.player_id)
      if (player) {
        player.alive = false
        player.role = payload.role
      }
    })

    on('player_killed', (message) => {
      const payload = extractPayload(message)
      const player = players.value.find((entry) => entry.player_id === payload.player_id)
      if (player) player.alive = false
    })

    on('seer_result', (message) => {
      const payload = extractPayload(message)
      seerResult.value = {
        targetId: payload.target_id,
        targetName: payload.target_name,
        role: payload.role,
      }
    })

    on('no_elimination', (message) => {
      const payload = extractPayload(message)
      noElimination.value = true
      noEliminationReason.value = payload.reason
    })

    on('game_ended', (message) => {
      const payload = extractPayload(message)
      winner.value = payload.winner ?? null
      phase.value = PHASES.ENDED
      round.value = payload.round ?? round.value
      gameEndPlayers.value = payload.players ?? []

      if (payload.players?.length) {
        players.value = payload.players.map((player) => ({
          player_id: player.player_id,
          username: player.username,
          role: player.role,
          alive: player.alive,
          connected: player.connected ?? true,
        }))
      }
    })

    on('game_paused', (message) => {
      const payload = extractPayload(message)
      isPaused.value = true
      pauseReason.value = payload.reason ?? ''
    })

    on('game_resumed', (message) => {
      const payload = extractPayload(message)
      isPaused.value = false
      if (payload.phase) phase.value = payload.phase
      if (payload.timer_end) timerEnd.value = payload.timer_end
    })

    on('room_closed', (message) => {
      const payload = extractPayload(message)
      roomClosedMessage.value = payload.reason ?? "L'host ha chiuso la partita."
      roomClosedAt.value = Date.now()
    })
  }

  function reset() {
    phase.value = PHASES.LOBBY
    round.value = 0
    players.value = []
    myRole.value = null
    wolfCompanions.value = []
    winner.value = null
    timerEnd.value = null
    pendingRoleSetup.value = { wolves: 1, seers: 1, villagers: 3 }
    currentRoomCode.value = ''
    isPaused.value = false
    pauseReason.value = ''
    seerResult.value = null
    noElimination.value = false
    noEliminationReason.value = ''
    voteMap.value = {}
    gameEndPlayers.value = []
    roomClosedAt.value = 0
    roomClosedMessage.value = ''
    error.value = null
  }

  function _applyState(data) {
    phase.value = data.phase ?? PHASES.LOBBY
    round.value = data.round ?? 0
    pendingRoleSetup.value = resolveRoleSetupFromState(data)

    const normalizedPlayers = normalizePlayers(data.players ?? [])
    const rolesMissing = normalizedPlayers.length > 0 && normalizedPlayers.every((player) => !player.role)
    players.value = rolesMissing
      ? assignLocalRoles(normalizedPlayers, pendingRoleSetup.value, currentRoomCode.value)
      : normalizedPlayers
    currentPlayerId.value = data.currentPlayerId ?? currentPlayerId.value
    hostId.value = data.host_id ?? hostId.value
    myRole.value = data.myRole ?? players.value.find((player) => player.player_id === currentPlayerId.value)?.role ?? myRole.value
    winner.value = data.winner ?? null
    timerEnd.value = data.timer_end ?? null
    isPaused.value = data.paused ?? false
    voteMap.value = data.vote_map ?? data.voteMap ?? {}
  }

  return {
    phase,
    round,
    players,
    currentPlayerId,
    hostId,
    myRole,
    wolfCompanions,
    winner,
    timerEnd,
    isLoading,
    error,
    isPaused,
    pauseReason,
    seerResult,
    noElimination,
    noEliminationReason,
    voteMap,
    gameEndPlayers,
    roomClosedAt,
    roomClosedMessage,
    alivePlayers,
    deadPlayers,
    me,
    isAlive,
    isWolf,
    isSeer,
    isVillager,
    secondsLeft,
    timerProgress,
    voteCount,
    normalizeRoleSetup,
    normalizePlayers,
    bootstrapFromLobby,
    loadState,
    handleStateSync,
    vote,
    wolfVote,
    seerAction,
    emitRoomClosed,
    listenToGameEvents,
    reset,
    PHASES,
    ROLES,
    WINNERS,
  }
})
