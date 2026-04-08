import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { useSocket } from '@/composables/useSocket'

export const useLobbyStore = defineStore('lobby', () => {
  const lobbyCode = ref(null)
  const players = ref([])
  const currentPlayerId = ref(null)
  const roleSetup = ref({ wolves: 1, seers: 1 })
  const isLoading = ref(false)
  const error = ref(null)
  const listenersBound = ref(false)

  const { connect, emit, on, isConnected } = useSocket()

  const currentPlayer = computed(() =>
    players.value.find((player) => playerIdOf(player) === currentPlayerId.value) ?? null
  )

  const isHost = computed(() => currentPlayer.value?.is_host || currentPlayer.value?.isHost || false)
  const isCurrentPlayerReady = computed(() => currentPlayer.value?.ready || false)

  const allReady = computed(() => {
    const guests = players.value.filter((player) => !(player.is_host || player.isHost))
    if (guests.length === 0) return false
    return guests.every((player) => player.ready)
  })

  const readyCount = computed(() =>
    players.value.filter((player) => player.ready && !(player.is_host || player.isHost)).length
  )

  const roleSummary = computed(() => normalizeRoleSetup(players.value.length))
  const maxSeers = computed(() => (players.value.length >= 5 ? 1 : 0))
  const maxWolves = computed(() => {
    const totalPlayers = players.value.length
    const seers = Math.min(roleSetup.value.seers, maxSeers.value)
    return Math.max(1, Math.floor((Math.max(totalPlayers - seers, 0) - 1) / 2))
  })

  function extractPayload(message) {
    if (!message || typeof message !== 'object') return {}
    return message.payload && typeof message.payload === 'object'
      ? message.payload
      : message
  }

  function playerIdOf(player) {
    return player?.player_id || player?.id || null
  }

  function hostIdFromPlayers() {
    return players.value.find((player) => player.is_host || player.isHost)?.player_id ?? null
  }

  function normalizeRoleSetup(totalPlayers = players.value.length, source = roleSetup.value) {
    const safeTotal = Math.max(0, totalPlayers)
    let seers = Math.min(Math.max(source?.seers ?? 0, 0), safeTotal >= 5 ? 1 : 0)
    let wolves = Math.max(1, source?.wolves ?? 1)

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

  function normalizeIncomingRoleSetup(remoteSetup = {}, totalPlayers = players.value.length) {
    if (!remoteSetup || typeof remoteSetup !== 'object') {
      return normalizeRoleSetup(totalPlayers)
    }

    return normalizeRoleSetup(totalPlayers, {
      wolves: remoteSetup.wolves ?? remoteSetup.wolf_count ?? roleSetup.value.wolves,
      seers: remoteSetup.seers ?? remoteSetup.seer_count ?? roleSetup.value.seers,
    })
  }

  function parsePlayers(remotePlayers, state = {}) {
    if (!Array.isArray(remotePlayers)) return players.value

    const readyPlayerIds = new Set(state.ready_player_ids ?? [])
    const hostId = state.host_id ?? null
    const hasExplicitHost = remotePlayers.some(
      (player) => typeof player === 'object' && (player.is_host === true || player.isHost === true)
    )

    return remotePlayers.map((player, index) => {
      const resolvedId = typeof player === 'object'
        ? playerIdOf(player) ?? `player-${index}`
        : player ?? `player-${index}`

      const isPlayerHost = hostId
        ? resolvedId === hostId
        : hasExplicitHost
          ? (player?.is_host === true || player?.isHost === true)
          : index === 0

      const readyFromState = readyPlayerIds.has(resolvedId)
      const readyFromPayload = typeof player === 'object' ? player.ready === true : false

      return {
        id: resolvedId,
        player_id: resolvedId,
        name: player?.username || player?.name || resolvedId,
        username: player?.username || player?.name || resolvedId,
        isHost: isPlayerHost,
        is_host: isPlayerHost,
        ready: isPlayerHost ? (readyFromState || readyFromPayload || true) : (readyFromState || readyFromPayload),
        connected: typeof player === 'object' ? player.connected !== false : true,
        alive: typeof player === 'object' ? player.alive ?? true : true,
        role: typeof player === 'object' ? player.role ?? null : null,
      }
    })
  }

  function syncRoleSetup() {
    const normalized = normalizeRoleSetup(players.value.length)
    roleSetup.value = {
      wolves: normalized.wolves,
      seers: normalized.seers,
    }
    return normalized
  }

  function applyRoleSetup(remoteSetup) {
    const normalized = normalizeIncomingRoleSetup(remoteSetup, players.value.length)
    roleSetup.value = {
      wolves: normalized.wolves,
      seers: normalized.seers,
    }
    return syncRoleSetup()
  }

  function updateReadyStatesFromIds(readyPlayerIds = []) {
    const readySet = new Set(readyPlayerIds)
    players.value = players.value.map((player) => {
      const playerId = playerIdOf(player)
      const host = player.is_host || player.isHost
      return {
        ...player,
        ready: host ? true : readySet.has(playerId),
      }
    })
  }

  function ensureHostReadySynced() {
    if (!isHost.value || !currentPlayerId.value) return
    if (currentPlayer.value?.ready) return
    emit('lobby:player_ready', { ready: true })
  }

  function adjustRole(role, delta) {
    const current = syncRoleSetup()

    if (role === 'wolves') {
      roleSetup.value.wolves = Math.max(1, current.wolves + delta)
    }

    if (role === 'seers') {
      roleSetup.value.seers = Math.max(0, current.seers + delta)
    }

    const normalized = syncRoleSetup()
    emit('lobby:update_settings', {
      wolf_count: normalized.wolves,
      seer_count: normalized.seers,
    })
  }

  function listenToLobbyEvents() {
    if (listenersBound.value) return
    listenersBound.value = true

    on('game_state_sync', (message) => {
      const payload = extractPayload(message)
      const state = payload.state || {}
      players.value = parsePlayers(payload.players || [], state)
      applyRoleSetup(state)
      ensureHostReadySynced()
    })

    on('player_joined', (message) => {
      const payload = extractPayload(message)
      if (Array.isArray(payload.players)) {
        players.value = parsePlayers(payload.players, {
          host_id: hostIdFromPlayers(),
        })
        syncRoleSetup()
        return
      }

      const newId = payload.client_id
      if (!newId || players.value.some((player) => playerIdOf(player) === newId)) return

      players.value.push({
        id: newId,
        player_id: newId,
        name: newId,
        username: newId,
        isHost: false,
        is_host: false,
        ready: false,
        connected: true,
        alive: true,
        role: null,
      })
      syncRoleSetup()
    })

    const handleReadyChanged = (message) => {
      const payload = extractPayload(message)

      if (Array.isArray(payload.players)) {
        players.value = parsePlayers(payload.players, {
          ready_player_ids: payload.ready_player_ids,
          host_id: hostIdFromPlayers(),
        })
        return
      }

      if (Array.isArray(payload.ready_player_ids)) {
        updateReadyStatesFromIds(payload.ready_player_ids)
        return
      }

      const targetId = payload.client_id
      const isReady = payload.ready === true
      players.value = players.value.map((player) => {
        if (playerIdOf(player) !== targetId || player.is_host || player.isHost) return player
        return { ...player, ready: isReady }
      })
    }

    on('lobby:player_ready_changed', handleReadyChanged)
    on('player_ready', handleReadyChanged)

    on('lobby:settings_updated', (message) => {
      const payload = extractPayload(message)
      applyRoleSetup(payload)
    })

    on('role_setup_updated', (message) => {
      const payload = extractPayload(message)
      applyRoleSetup(payload.role_setup ?? payload)
    })

    on('player_left', (message) => {
      const payload = extractPayload(message)
      if (Array.isArray(payload.players)) {
        players.value = parsePlayers(payload.players, {
          host_id: hostIdFromPlayers(),
        })
        syncRoleSetup()
        return
      }

      const leftId = payload.client_id
      players.value = players.value.filter((player) => playerIdOf(player) !== leftId)
      syncRoleSetup()
    })
  }

  async function createLobby(playerName) {
    isLoading.value = true
    error.value = null
    try {
      const code = 'WOLF-' + Math.floor(1000 + Math.random() * 9000)

      sessionStorage.setItem('client_id', playerName)
      sessionStorage.setItem('room_id', code)
      localStorage.setItem('client_id', playerName)
      localStorage.setItem('room_id', code)

      lobbyCode.value = code
      currentPlayerId.value = playerName
      players.value = [{
        id: playerName,
        player_id: playerName,
        name: playerName,
        username: playerName,
        isHost: true,
        is_host: true,
        ready: true,
        connected: true,
        alive: true,
        role: null,
      }]
      syncRoleSetup()
    } catch (err) {
      error.value = err.message
    } finally {
      isLoading.value = false
    }
  }

  async function joinLobby(code, playerName) {
    isLoading.value = true
    error.value = null
    try {
      sessionStorage.setItem('client_id', playerName)
      sessionStorage.setItem('room_id', code)
      localStorage.setItem('client_id', playerName)
      localStorage.setItem('room_id', code)

      lobbyCode.value = code
      currentPlayerId.value = playerName
    } catch (err) {
      error.value = err.message
      console.error('[LobbyStore] Errore durante joinLobby:', err)
    } finally {
      isLoading.value = false
    }
  }

  function toggleReady() {
    const newState = !currentPlayer.value?.ready
    emit('lobby:player_ready', { ready: newState })
  }

  function startGame() {
    if (!isHost.value) return
    emit('lobby:start_game', {
      lobby_code: lobbyCode.value,
      role_setup: syncRoleSetup(),
    })
  }

  function kickPlayer(targetId) {
    if (!isHost.value) return
    emit('kick_player', { lobby_code: lobbyCode.value, target_id: targetId })
  }

  function reset() {
    lobbyCode.value = null
    players.value = []
    currentPlayerId.value = null
    roleSetup.value = { wolves: 1, seers: 1 }
    listenersBound.value = false
    error.value = null
  }

  function _initSocket() {
    connect()
  }

  return {
    lobbyCode,
    players,
    currentPlayerId,
    roleSetup,
    isLoading,
    error,
    isConnected,
    currentPlayer,
    isHost,
    isCurrentPlayerReady,
    allReady,
    readyCount,
    roleSummary,
    maxWolves,
    maxSeers,
    createLobby,
    joinLobby,
    toggleReady,
    adjustRole,
    syncRoleSetup,
    applyRoleSetup,
    startGame,
    kickPlayer,
    reset,
    listenToLobbyEvents,
  }
})
