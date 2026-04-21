import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { useSocket } from '@/composables/useSocket'

export const useLobbyStore = defineStore('lobby', () => {
  const lobbyCode = ref(null)
  const players = ref([])
  const currentPlayerId = ref(null)
  const roleSetup = ref({ wolves: 1, seers: 0 }) // <- Partiamo con 0 veggenti di default!
  const hostPlayerId = ref(null)
  const readyPlayerIds = ref([])
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
  const readyProgress = computed(() => {
    const guestsCount = Math.max(0, players.value.filter((player) => !(player.is_host || player.isHost)).length)
    if (guestsCount === 0) return 0
    return (readyCount.value / guestsCount) * 100
  })

  const roleSummary = computed(() => normalizeRoleSetup(players.value.length))
  const maxSeers = computed(() => 1) // L'host può mettere al massimo 1 veggente se vuole
  const maxWolves = computed(() => {
    const totalPlayers = players.value.length
    const seers = Math.min(roleSetup.value.seers, maxSeers.value)
    return Math.max(1, Math.floor((Math.max(totalPlayers - seers, 0) - 1) / 2))
  })

  // ---- UTILS & VALIDATION ----

  function validateNickname(name) {
    if (!name || typeof name !== 'string') {
      throw new Error("Il nome non può essere vuoto.")
    }
    const trimmed = name.trim()
    if (trimmed.length < 3) {
      throw new Error("Il nome deve avere almeno 3 caratteri.")
    }
    if (trimmed.length > 15) {
      throw new Error("Il nome non può superare i 15 caratteri.")
    }
    // Permette solo lettere (A-Z, a-z), numeri (0-9), trattini (-) e underscore (_)
    if (!/^[a-zA-Z0-9_-]+$/.test(trimmed)) {
      throw new Error("Il nome può contenere solo lettere, numeri, trattini e underscore (senza spazi).")
    }
    return trimmed
  }

  function extractPayload(message) {
    let msg = message
    if (typeof msg === 'string') {
      try { msg = JSON.parse(msg) } catch (e) { return {} }
    }
    // Nel caso il payload sia annidato e stringificato a sua volta
    if (msg?.payload && typeof msg.payload === 'string') {
      try { msg.payload = JSON.parse(msg.payload) } catch (e) {}
    }
    return msg?.payload && typeof msg.payload === 'object' ? msg.payload : (msg || {})
  }

  function playerIdOf(player) {
    return player?.player_id || player?.id || null
  }

  function hostIdFromPlayers() {
    return players.value.find((player) => player.is_host || player.isHost)?.player_id ?? null
  }

  function syncLobbyState(state = {}) {
    if (state.host_id !== undefined) {
      hostPlayerId.value = state.host_id ?? null
    } else if (!hostPlayerId.value) {
      hostPlayerId.value = hostIdFromPlayers()
    }

    if (Array.isArray(state.ready_player_ids)) {
      readyPlayerIds.value = [...state.ready_player_ids]
    }
  }

  // 🔥 FIX BLINDATO: Il frontend smette di auto-bilanciare. Usa quello che decide l'host.
  function normalizeRoleSetup(totalPlayers = players.value.length, source = roleSetup.value) {
    const safeTotal = Math.max(0, totalPlayers)
    
    // Mantiene il numero di seers scelto, massimo 1 (se si è in pochi si può non metterne)
    let seers = Math.min(Math.max(source?.seers ?? 0, 0), 1)
    
    // Mantiene il numero di lupi scelto
    let wolves = Math.max(1, source?.wolves ?? 1)

    // Blocca i lupi se sono troppi rispetto ai contadini
    const wolvesCap = Math.max(1, Math.floor((Math.max(safeTotal - seers, 0) - 1) / 2))
    wolves = Math.min(wolves, wolvesCap)

    let villagers = Math.max(0, safeTotal - wolves - seers)

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

    syncLobbyState(state)

    const readySet = new Set(readyPlayerIds.value)
    const hostId = hostPlayerId.value
    const hasExplicitHost = remotePlayers.some(
      (player) => typeof player === 'object' && (player.is_host === true || player.isHost === true)
    )

    let parsed = remotePlayers.map((player, index) => {
      const resolvedId = typeof player === 'object'
        ? playerIdOf(player) ?? `player-${index}`
        : player ?? `player-${index}`

      const isPlayerHost = hasExplicitHost
        ? (player?.is_host === true || player?.isHost === true)
        : hostId
          ? resolvedId === hostId
          : index === 0

      // Se sei l'host, sei sempre pronto. Altrimenti controllo il set locale O il payload
      const readyFromState = readySet.has(resolvedId)
      const readyFromPayload = typeof player === 'object' ? player.ready === true : false
      const isReady = isPlayerHost ? true : (readyFromState || readyFromPayload)

      return {
        id: resolvedId,
        player_id: resolvedId,
        name: player?.username || player?.name || resolvedId,
        username: player?.username || player?.name || resolvedId,
        isHost: isPlayerHost,
        is_host: isPlayerHost,
        ready: isReady,
        connected: typeof player === 'object' ? player.connected !== false : true,
        alive: typeof player === 'object' ? player.alive ?? true : true,
        role: typeof player === 'object' ? player.role ?? null : null,
      }
    })

    // L'host sempre in cima, gli altri in ordine alfabetico. NON FUNZIONA
    parsed.sort((a, b) => {
        if (a.is_host) return -1;
        if (b.is_host) return 1;
        return a.username.localeCompare(b.username);
    });

    return parsed;
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

  function updateReadyStatesFromIds(nextReadyPlayerIds = []) {
    readyPlayerIds.value = Array.isArray(nextReadyPlayerIds) ? [...nextReadyPlayerIds] : []
    const readySet = new Set(readyPlayerIds.value)
    players.value = players.value.map((player) => {
      const playerId = playerIdOf(player)
      const host = player.is_host || player.isHost
      return {
        ...player,
        ready: host ? true : readySet.has(playerId),
      }
    })
  }

  function setPlayerReadyLocally(targetId, isReady) {
    if (!targetId) return

    const nextReadySet = new Set(readyPlayerIds.value)
    if (isReady) {
      nextReadySet.add(targetId)
    } else {
      nextReadySet.delete(targetId)
    }

    updateReadyStatesFromIds([...nextReadySet])
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
      console.log('[Lobby] game_state_sync:', payload)
      
      const state = payload.state || {}
      if (state.host_id) hostPlayerId.value = state.host_id
      if (state.ready_player_ids) readyPlayerIds.value = [...state.ready_player_ids]
      
      // APPLICA PARSEPLAYERS
      if (payload.players) {
        players.value = parsePlayers(payload.players, state)
      }
      
      // Mantiene i ruoli ricevuti. Se non li riceve mantiene i correnti.
      if (state.wolf_count !== undefined || state.seer_count !== undefined) {
        roleSetup.value.wolves = state.wolf_count ?? roleSetup.value.wolves
        roleSetup.value.seers = state.seer_count ?? roleSetup.value.seers
      } else if (state.role_setup) {
        roleSetup.value.wolves = state.role_setup.wolves ?? roleSetup.value.wolves
        roleSetup.value.seers = state.role_setup.seers ?? roleSetup.value.seers
      }
    })

    on('player_joined', (message) => {
      const payload = extractPayload(message)
      console.log('[Lobby] player_joined:', payload)
      if (payload.players) {
        players.value = parsePlayers(payload.players, { ready_player_ids: readyPlayerIds.value })
      }
      // Assicuriamoci che i ruoli vengano ri-sincronizzati per non sforare i massimi
      syncRoleSetup()
    })

    on('player_left', (message) => {
      const payload = extractPayload(message)
      console.log('[Lobby] player_left:', payload)
      if (payload.players) {
        players.value = parsePlayers(payload.players, { ready_player_ids: readyPlayerIds.value })
      }
      // Assicuriamoci che i ruoli vengano ri-sincronizzati per non sforare i massimi
      syncRoleSetup()
    })

    const handleReady = (message) => {
      const payload = extractPayload(message)
      if (payload.ready_player_ids) {
          readyPlayerIds.value = [...payload.ready_player_ids]
      }
      
      if (payload.players) {
        players.value = parsePlayers(payload.players, { ready_player_ids: readyPlayerIds.value })
      }
    }
    on('player_ready', handleReady)
    on('lobby:player_ready_changed', handleReady)

    const handleSettings = (message) => {
      const payload = extractPayload(message)
      const setup = payload.role_setup || payload
      if (setup.wolves !== undefined || setup.wolf_count !== undefined) {
        roleSetup.value.wolves = setup.wolves ?? setup.wolf_count
      }
      if (setup.seers !== undefined || setup.seer_count !== undefined) {
        roleSetup.value.seers = setup.seers ?? setup.seer_count
      }
    }
    on('role_setup_updated', handleSettings)
    on('lobby:settings_updated', handleSettings)

    on('room_closed', () => {
      error.value = "L'host ha chiuso la lobby."
    })
  }

  // ---- ACTIONS ----

  async function createLobby(playerName) {
    isLoading.value = true
    error.value = null
    try {
      const safeName = validateNickname(playerName) // Validazione
      const code = 'WOLF-' + Math.floor(1000 + Math.random() * 9000)

      sessionStorage.setItem('client_id', safeName)
      sessionStorage.setItem('room_id', code)

      lobbyCode.value = code
      currentPlayerId.value = safeName
      players.value = [{
        id: safeName,
        player_id: safeName,
        name: safeName,
        username: safeName,
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
      throw err 
    } finally {
      isLoading.value = false
    }
  }

  async function joinLobby(code, playerName) {
    isLoading.value = true
    error.value = null
    try {
      if (!code || typeof code !== 'string' || code.trim() === '') {
        throw new Error("Inserisci un codice stanza valido.")
      }
      const safeCode = code.trim().toUpperCase()
      const safeName = validateNickname(playerName) // Validazione
      
      sessionStorage.setItem('client_id', safeName)
      sessionStorage.setItem('room_id', safeCode)

      lobbyCode.value = safeCode
      currentPlayerId.value = safeName
    } catch (err) {
      error.value = err.message
      console.error('[LobbyStore] Errore durante joinLobby:', err)
      throw err 
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

    const finalSetup = syncRoleSetup()
    emit('lobby:update_settings', {
      wolf_count: finalSetup.wolves,
      seer_count: finalSetup.seers,
    })

    setTimeout(() => {
      emit('lobby:start_game', {
        lobby_code: lobbyCode.value,
      })
    }, 100)
  }

  // L'host espelle un giocatore
  function kickPlayer(targetId) {
    if (!isHost.value) return
    emit('kick_player', { target_id: targetId })
  }

  function reset() {
    lobbyCode.value = null
    players.value = []
    currentPlayerId.value = null
    roleSetup.value = { wolves: 1, seers: 0 } // Reset a 0 veggenti
    hostPlayerId.value = null
    readyPlayerIds.value = []
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
    readyProgress,
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