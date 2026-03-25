import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { lobbyApi } from '@/services/api'
import { useSocket } from '@/composables/useSocket'

export const useLobbyStore = defineStore('lobby', () => {
  // ---- STATE ----
  const lobbyCode = ref(null)
  const players = ref([])          // { id, name, isHost, ready }
  const currentPlayerId = ref(null)
  const isLoading = ref(false)
  const error = ref(null)

  const { connect, emit, on, off, isConnected } = useSocket()

  // ---- GETTERS ----
  const currentPlayer = computed(() =>
    players.value.find((p) => p.id === currentPlayerId.value) ?? null
  )
  const isHost = computed(() => currentPlayer.value?.isHost ?? false)
  const allReady = computed(() =>
    players.value.filter((p) => !p.isHost).every((p) => p.ready)
  )
  const readyCount = computed(() => players.value.filter((p) => p.ready).length)

  // ---- ACTIONS ----

  async function createLobby(playerName) {
  isLoading.value = true
  error.value = null
  try {
    // MOCK TEMPORANEO — sostituire con la riga sotto quando il backend è pronto
    // const data = await lobbyApi.create(playerName)
    const data = {
      lobbyCode: 'WOLF-' + Math.floor(1000 + Math.random() * 9000),
      playerId: 'p' + Date.now(),
    }

    lobbyCode.value = data.lobbyCode
    currentPlayerId.value = data.playerId

    // Aggiungi te stesso alla lista giocatori
    players.value = [
      { id: data.playerId, name: playerName, isHost: true, ready: true }
    ]

    // _connectSocket()  // commentato finché il backend non è pronto
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
    // MOCK TEMPORANEO — sostituire con la riga sotto quando il backend è pronto
    // const data = await lobbyApi.join(code, playerName)
    const data = {
      playerId: 'p' + Date.now(),
    }

    lobbyCode.value = code
    currentPlayerId.value = data.playerId

    // Simula altri giocatori già in lobby
    players.value = [
      { id: 'p_host', name: 'Host', isHost: true, ready: true },
      { id: data.playerId, name: playerName, isHost: false, ready: false },
    ]

    // _connectSocket()  // commentato finché il backend non è pronto
  } catch (err) {
    error.value = err.message
  } finally {
    isLoading.value = false
  }
}

  /** Segna il giocatore corrente come pronto/non pronto */
  function toggleReady() {
    emit('lobby:ready_toggle', { lobbyCode: lobbyCode.value, playerId: currentPlayerId.value })
  }

  /** Avvia la partita (solo host) */
  function startGame() {
    if (!isHost.value) return
    emit('lobby:start_game', { lobbyCode: lobbyCode.value })
  }

  /** Rimuove un giocatore dalla lobby (solo host) */
  function kickPlayer(targetId) {
    if (!isHost.value) return
    emit('lobby:kick_player', { lobbyCode: lobbyCode.value, targetId })
  }

  /** Resetta lo store (es. quando si esce dalla lobby) */
  function reset() {
    lobbyCode.value = null
    players.value = []
    currentPlayerId.value = null
    error.value = null
  }

  // ---- SOCKET PRIVATO ----
  function _connectSocket() {
    connect()

    // Entra nella stanza Socket.IO
    emit('lobby:join', { lobbyCode: lobbyCode.value, playerId: currentPlayerId.value })

    // Aggiornamento lista giocatori
    on('lobby:players_updated', (updatedPlayers) => {
      players.value = updatedPlayers
    })

    // Un giocatore è stato kickato
    on('lobby:player_kicked', ({ playerId }) => {
      if (playerId === currentPlayerId.value) {
        reset()
        // Il router redirect è gestito dal componente che ascolta reset
      }
    })
  }

  return {
    // state
    lobbyCode, players, currentPlayerId, isLoading, error, isConnected,
    // getters
    currentPlayer, isHost, allReady, readyCount,
    // actions
    createLobby, joinLobby, toggleReady, startGame, kickPlayer, reset,
  }
})
