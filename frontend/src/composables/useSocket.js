import { ref, onUnmounted } from 'vue'
import { io } from 'socket.io-client'

// Istanza singleton — una sola connessione per tutta l'app
let socket = null
const pendingListeners = new Map()

function getStoredValue(key) {
  return sessionStorage.getItem(key) || localStorage.getItem(key)
}

function bindPendingListeners() {
  if (!socket) return

  for (const [event, callbacks] of pendingListeners.entries()) {
    callbacks.forEach((callback) => {
      socket.off(event, callback)
      socket.on(event, callback)
    })
  }
}

export function useSocket() {
  const isConnected = ref(false)
  const error = ref(null)

  /**
   * Connette al server Socket.IO
   * @param {string} url - es. 'http://localhost:8000'
   * @param {object} options - opzioni socket.io (auth, query, ecc.)
   */
  function connect(url = import.meta.env.VITE_WS_URL ?? 'http://localhost:8000', options = {}) {
    // Se il socket esiste già ed è connesso, non fare nulla
    if (socket?.connected) {
      isConnected.value = true
      return
    }

    // Configurazione avanzata per il tuo backend distribuito
    const config = {
      autoConnect: true,
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1500,
      // FIX CORS & 404: Forza l'uso dei WebSocket puri invece del polling HTTP
      transports: ['websocket'], 
      // FIX AUTH: Passa i dati richiesti dal tuo main.py (riga 76 del backend)
      auth: {
        client_id: options.auth?.client_id || getStoredValue('client_id'),
        room_id: options.auth?.room_id || getStoredValue('room_id') || 'lobby',
      },
      ...options
    }

    console.log(`[Socket] Tentativo di connessione a ${url}...`, config.auth)

    socket = io(url, config)
    bindPendingListeners()

    // Gestione Eventi di Sistema
    socket.on('connect', () => {
      isConnected.value = true
      error.value = null
      console.log('[Socket] Connesso con successo! ID:', socket.id)
    })

    socket.on('disconnect', (reason) => {
      isConnected.value = false
      console.warn('[Socket] Disconnesso:', reason)
    })

    socket.on('connect_error', (err) => {
      error.value = err.message
      console.error('[Socket] Errore connessione:', err.message)
      
      // Se fallisce il websocket, potrebbe esserci un problema di rete o server
      if (err.message === 'xhr poll error') {
        console.error('[Socket] Errore critico: Il server non risponde o i CORS sono bloccati.')
      }
    })
  }

  /**
   * Disconnette e pulisce il socket
   */
  function disconnect() {
    if (socket) {
      socket.disconnect()
      socket = null
      isConnected.value = false
      console.log('[Socket] Connessione chiusa manualmente')
    }
  }

  /**
   * Invia un evento al server
   * @param {string} event - nome evento
   * @param {any} data - payload
   */
  function emit(event, data) {
    if (!socket?.connected) {
      console.warn(`[Socket] Impossibile inviare '${event}': socket non connesso`)
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
    if (!pendingListeners.has(event)) {
      pendingListeners.set(event, new Set())
    }
    pendingListeners.get(event).add(callback)

    if (!socket) {
      console.warn(`[Socket] Attenzione: registro listener '${event}' prima di connettere`)
      return
    }

    socket.off(event, callback)
    socket.on(event, callback)
  }

  /**
   * Rimuove un listener
   * @param {string} event
   * @param {function} callback
   */
  function off(event, callback) {
    pendingListeners.get(event)?.delete(callback)
    if (pendingListeners.get(event)?.size === 0) {
      pendingListeners.delete(event)
    }
    socket?.off(event, callback)
  }

  // Pulizia automatica quando il componente viene smontato
  onUnmounted(() => {
    // Nota: Non chiudiamo il socket qui perché è un singleton globale,
    // ma chi usa useSocket() dovrebbe usare off() se necessario.
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
