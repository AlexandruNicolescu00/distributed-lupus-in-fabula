import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { useSocket } from '@/composables/useSocket'

export const useLobbyStore = defineStore('lobby', () => {
  // ── STATE ────────────────────────────────────────────────────────────────
  const lobbyCode = ref(null)
  const players = ref([])         // Array di oggetti: { player_id, name, is_host, ready, connected }
  const currentPlayerId = ref(null)
  const roleSetup = ref({ wolves: 1, seers: 1 })
  const isLoading = ref(false)
  const error = ref(null)
  const listenersBound = ref(false)

  const { connect, emit, on, isConnected } = useSocket()

  // ── GETTERS ──────────────────────────────────────────────────────────────
  const currentPlayer = computed(() =>
    players.value.find((p) => (p.player_id || p.id) === currentPlayerId.value) ?? null
  )
  
  const isHost = computed(() => currentPlayer.value?.is_host || currentPlayer.value?.isHost || false)
  
  const isCurrentPlayerReady = computed(() => currentPlayer.value?.ready || false)
  
  // LOGICA CORRETTA: Controlla solo se i GUEST sono pronti
  const allReady = computed(() => {
    const guests = players.value.filter(p => !p.is_host && !p.isHost)
    if (guests.length === 0) return false // Non si può giocare da soli
    return guests.every((p) => p.ready)
  })
  
  // LOGICA CORRETTA: Conta solo i GUEST pronti
  const readyCount = computed(() => {
    return players.value.filter((p) => p.ready && !p.is_host && !p.isHost).length
  })

  const roleSummary = computed(() => normalizeRoleSetup(players.value.length))
  const maxSeers = computed(() => (players.value.length >= 5 ? 1 : 0))
  const maxWolves = computed(() => {
    const totalPlayers = players.value.length
    const seers = Math.min(roleSetup.value.seers, maxSeers.value)
    return Math.max(1, Math.floor((Math.max(totalPlayers - seers, 0) - 1) / 2))
  })

  // ── HELPER INTERNO ───────────────────────────────────────────────────────
  /**
   * Centralizza la mappatura dei giocatori per garantire la coerenza dei dati
   * e forzare l'host a essere sempre 'pronto'.
   */
  function parsePlayers(remotePlayers) {
    if (!remotePlayers || !Array.isArray(remotePlayers)) return players.value

    const hasExplicitHost = remotePlayers.some(
      (player) => typeof player === 'object' && (player.is_host === true || player.isHost === true)
    )

    return remotePlayers.map((p, index) => {
      // Se il backend invia oggetti complessi
      if (typeof p === 'object') {
        const isPlayerHost = hasExplicitHost
          ? (p.is_host === true || p.isHost === true)
          : index === 0
        return {
          id: p.player_id || p.id,
          player_id: p.player_id || p.id,
          name: p.name || p.player_id || p.id,
          isHost: isPlayerHost,
          is_host: isPlayerHost,
          ready: isPlayerHost ? true : (p.ready || false), // L'host è sempre pronto
          connected: p.connected !== false
        }
      } 
      // Fallback se il backend invia solo un array di stringhe (ID)
      else {
        const isPlayerHost = index === 0
        return {
          id: p,
          player_id: p,
          name: p,
          isHost: isPlayerHost,
          is_host: isPlayerHost,
          ready: isPlayerHost ? true : false,
          connected: true
        }
      }
    })
  }

  function normalizeRoleSetup(totalPlayers = players.value.length) {
    const safeTotal = Math.max(0, totalPlayers)
    let seers = Math.min(Math.max(roleSetup.value.seers, 0), safeTotal >= 5 ? 1 : 0)
    let wolves = Math.max(1, roleSetup.value.wolves)

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

  function syncRoleSetup() {
    const normalized = normalizeRoleSetup(players.value.length)
    roleSetup.value = {
      wolves: normalized.wolves,
      seers: normalized.seers,
    }
    return normalized
  }

  function applyRoleSetup(remoteSetup) {
    if (!remoteSetup || typeof remoteSetup !== 'object') return syncRoleSetup()

    roleSetup.value = {
      wolves: Math.max(1, remoteSetup.wolves ?? roleSetup.value.wolves),
      seers: Math.max(0, remoteSetup.seers ?? roleSetup.value.seers),
    }

    return syncRoleSetup()
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
    emit('role_setup_updated', { role_setup: normalized })
  }

  // ── ACTIONS ──────────────────────────────────────────────────────────────

  /**
   * Registra i listener del socket per aggiornare la UI in tempo reale.
   */
  function listenToLobbyEvents() {
    if (listenersBound.value) return
    listenersBound.value = true
    console.log('[LobbyStore] In ascolto eventi...')

    // ════════════════════════════════════════════════════════════════════════
    // 1. SINCRONIZZAZIONE TOTALE (Ricevuto all'entrata)
    // ════════════════════════════════════════════════════════════════════════
    on('game_state_sync', (data) => {
      console.log('[LobbyStore] Sync ricevuto:', data)
      const remotePlayers = data.payload?.players || []
      const remoteRoleSetup = data.payload?.state?.role_setup
      if (remotePlayers.length > 0) {
        players.value = parsePlayers(remotePlayers)
        if (remoteRoleSetup) {
          applyRoleSetup(remoteRoleSetup)
        } else {
          syncRoleSetup()
        }
        console.log('[LobbyStore] Players aggiornati:', players.value)
      }
    })

    // ════════════════════════════════════════════════════════════════════════
    // 2. NUOVO GIOCATORE (Ricevuto quando qualcuno entra)
    // ════════════════════════════════════════════════════════════════════════
    on('player_joined', (data) => {
      console.log('[LobbyStore] Nuovo giocatore:', data)
      const remotePlayers = data.payload?.players
      
      if (remotePlayers && Array.isArray(remotePlayers)) {
        players.value = parsePlayers(remotePlayers)
        syncRoleSetup()
      } else {
        // Fallback estremo se il backend non invia l'array aggiornato
        const newId = data.payload?.client_id
        if (newId && !players.value.find(p => (p.player_id || p.id) === newId)) {
          players.value.push({
            id: newId,
            player_id: newId,
            name: newId,
            isHost: false,
            is_host: false,
            ready: false,
            connected: true
          })
          syncRoleSetup()
        }
      }
    })

    // ════════════════════════════════════════════════════════════════════════
    // 3. CAMBIO STATO PRONTO
    // ════════════════════════════════════════════════════════════════════════
    on('player_ready', (data) => {
      console.log('[LobbyStore] Player ready update:', data)
      const remotePlayers = data.payload?.players
      
      if (remotePlayers && Array.isArray(remotePlayers)) {
        players.value = parsePlayers(remotePlayers)
        syncRoleSetup()
      } else {
        // Fallback locale
        const targetId = data.payload?.client_id
        const isReady = data.payload?.ready
        
        const p = players.value.find(p => (p.player_id || p.id) === targetId)
        if (p && !p.isHost) {
          p.ready = isReady
        }
      }
    })

    // ════════════════════════════════════════════════════════════════════════
    // 4. GIOCATORE ESCE
    // ════════════════════════════════════════════════════════════════════════
    on('player_left', (data) => {
      console.log('[LobbyStore] Player left:', data)
      const remotePlayers = data.payload?.players
      
      if (remotePlayers && Array.isArray(remotePlayers)) {
        players.value = parsePlayers(remotePlayers)
        syncRoleSetup()
      } else {
        // Fallback locale
        const leftId = data.payload?.client_id
        players.value = players.value.filter(p => (p.player_id || p.id) !== leftId)
        syncRoleSetup()
      }
    })

    on('role_setup_updated', (data) => {
      const remoteRoleSetup = data.payload?.role_setup
      if (remoteRoleSetup) {
        applyRoleSetup(remoteRoleSetup)
      }
    })

  }

  /**
   * Crea una nuova lobby (diventa host)
   */
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
        isHost: true,
        is_host: true,
        ready: true,
        connected: true,
      }]
      syncRoleSetup()
    } catch (err) {
      error.value = err.message
    } finally {
      isLoading.value = false
    }
  }

  /**
   * Unisciti a una lobby esistente
   */
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
      console.error("[LobbyStore] Errore durante joinLobby:", err)
    } finally {
      isLoading.value = false
    }
  }

  /**
   * Segna il giocatore corrente come pronto/non pronto
   */
  function toggleReady() {
    const newState = !currentPlayer.value?.ready
    console.log('[LobbyStore] Cambiando stato pronto a:', newState)
    
    emit('player_ready', { 
      ready: newState 
    })
  }

  /**
   * Avvia la partita (solo host)
   */
  function startGame() {
    if (!isHost.value) return
    emit('start_game', {
      lobby_code: lobbyCode.value,
      role_setup: syncRoleSetup(),
    })
  }

  /**
   * Rimuove un giocatore dalla lobby (solo host)
   */
  function kickPlayer(targetId) {
    if (!isHost.value) return
    emit('kick_player', { lobby_code: lobbyCode.value, target_id: targetId })
  }

  /**
   * Resetta lo store
   */
  function reset() {
    lobbyCode.value = null
    players.value = []
    currentPlayerId.value = null
    roleSetup.value = { wolves: 1, seers: 1 }
    listenersBound.value = false
    error.value = null
  }

  // ── LOGICA INTERNA ──────────────────────────────────────────────────────
  function _initSocket() {
    connect() 
  }

  return {
    // state
    lobbyCode, 
    players, 
    currentPlayerId, 
    roleSetup,
    isLoading, 
    error, 
    isConnected,
    
    // getters
    currentPlayer, 
    isHost, 
    isCurrentPlayerReady,
    allReady, 
    readyCount,
    roleSummary,
    maxWolves,
    maxSeers,
    
    // actions
    createLobby, 
    joinLobby, 
    toggleReady, 
    adjustRole,
    syncRoleSetup,
    applyRoleSetup,
    startGame, 
    kickPlayer, 
    reset, 
    listenToLobbyEvents
  }
})
