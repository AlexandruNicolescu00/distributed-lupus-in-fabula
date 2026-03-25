import { ref, onUnmounted } from 'vue'
import { io } from 'socket.io-client'

// Istanza singleton — una sola connessione per tutta l'app
let socket = null

export function useSocket() {
  const isConnected = ref(false)
  const error = ref(null)

  /**
   * Connette al server Socket.IO
   * @param {string} url - es. 'http://localhost:8000'
   * @param {object} options - opzioni socket.io (auth, query, ecc.)
   */
  function connect(url = import.meta.env.VITE_WS_URL ?? 'http://localhost:8000', options = {}) {
    if (socket?.connected) return

    socket = io(url, {
      autoConnect: true,
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1500,
      ...options,
    })

    socket.on('connect', () => {
      isConnected.value = true
      error.value = null
      console.log('[Socket] Connesso:', socket.id)
    })

    socket.on('disconnect', (reason) => {
      isConnected.value = false
      console.warn('[Socket] Disconnesso:', reason)
    })

    socket.on('connect_error', (err) => {
      error.value = err.message
      console.error('[Socket] Errore connessione:', err.message)
    })
  }

  /**
   * Disconnette e pulisce il socket
   */
  function disconnect() {
    socket?.disconnect()
    socket = null
    isConnected.value = false
  }

  /**
   * Invia un evento al server
   * @param {string} event - nome evento
   * @param {any} data - payload
   */
  function emit(event, data) {
    if (!socket?.connected) {
      console.warn(`[Socket] Tentativo emit '${event}' senza connessione`)
      return
    }
    socket.emit(event, data)
  }

  /**
   * Ascolta un evento dal server
   * @param {string} event - nome evento
   * @param {function} callback
   */
  function on(event, callback) {
    socket?.on(event, callback)
  }

  /**
   * Rimuove un listener
   * @param {string} event
   * @param {function} callback
   */
  function off(event, callback) {
    socket?.off(event, callback)
  }

  // Pulizia automatica quando il componente viene smontato
  onUnmounted(() => {
    // Non disconnettiamo il socket globale, rimuoviamo solo i listener
    // che questo componente ha registrato (gestito manualmente da chi usa on/off)
  })

  return {
    socket,
    isConnected,
    error,
    connect,
    disconnect,
    emit,
    on,
    off,
  }
}
