import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useLobbyStore } from '@/stores/lobbyStore'

// ---------------------------------------------------------------------------
// Setup — Pinia fresca prima di ogni test
// ---------------------------------------------------------------------------
beforeEach(() => {
  setActivePinia(createPinia())
})

// ---------------------------------------------------------------------------
// Helper — lista giocatori mock
// ---------------------------------------------------------------------------
function makePlayers() {
  return [
    { id: 'p1', name: 'Alice', isHost: true,  ready: true  },
    { id: 'p2', name: 'Bob',   isHost: false, ready: true  },
    { id: 'p3', name: 'Carol', isHost: false, ready: false },
    { id: 'p4', name: 'Dave',  isHost: false, ready: false },
  ]
}

// ---------------------------------------------------------------------------
// SUITE 1 — Stato iniziale :Controlla se l'host può avviare la partita. 
// Testiamo tre casi: alcuni non pronti, tutti pronti, e il caso edge di solo l'host in lobby
// ---------------------------------------------------------------------------
describe('lobbyStore — stato iniziale', () => {
  it('lobbyCode è null inizialmente', () => {
    const lobby = useLobbyStore()
    expect(lobby.lobbyCode).toBeNull()
  })

  it('players è vuoto inizialmente', () => {
    const lobby = useLobbyStore()
    expect(lobby.players).toHaveLength(0)
  })

  it('currentPlayerId è null inizialmente', () => {
    const lobby = useLobbyStore()
    expect(lobby.currentPlayerId).toBeNull()
  })

  it('isLoading è false inizialmente', () => {
    const lobby = useLobbyStore()
    expect(lobby.isLoading).toBe(false)
  })

  it('error è null inizialmente', () => {
    const lobby = useLobbyStore()
    expect(lobby.error).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// SUITE 2 — currentPlayer
// ---------------------------------------------------------------------------
describe('lobbyStore — currentPlayer', () => {
  it('restituisce il giocatore corrente', () => {
    const lobby = useLobbyStore()
    lobby.players         = makePlayers()
    lobby.currentPlayerId = 'p2'
    expect(lobby.currentPlayer?.name).toBe('Bob')
  })

  it('restituisce null se currentPlayerId non corrisponde', () => {
    const lobby = useLobbyStore()
    lobby.players         = makePlayers()
    lobby.currentPlayerId = 'p99'
    expect(lobby.currentPlayer).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// SUITE 3 — isHost : determina se vedi il pulsante "Inizia la partita" o "Sono pronto"
// ---------------------------------------------------------------------------
describe('lobbyStore — isHost', () => {
  it('isHost è true se il giocatore corrente è host', () => {
    const lobby = useLobbyStore()
    lobby.players         = makePlayers()
    lobby.currentPlayerId = 'p1'   // p1 è host
    expect(lobby.isHost).toBe(true)
  })

  it('isHost è false se il giocatore corrente non è host', () => {
    const lobby = useLobbyStore()
    lobby.players         = makePlayers()
    lobby.currentPlayerId = 'p2'
    expect(lobby.isHost).toBe(false)
  })

  it('isHost è false se currentPlayerId è null', () => {
    const lobby = useLobbyStore()
    lobby.players         = makePlayers()
    lobby.currentPlayerId = null
    expect(lobby.isHost).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 4 — readyCount : Conta i giocatori pronti
// ---------------------------------------------------------------------------
describe('lobbyStore — readyCount', () => {
  it('conta correttamente i giocatori pronti', () => {
    const lobby = useLobbyStore()
    lobby.players = makePlayers()   // p1 e p2 pronti → 2
    expect(lobby.readyCount).toBe(2)
  })

  it('è 0 se nessuno è pronto', () => {
    const lobby = useLobbyStore()
    lobby.players = makePlayers().map(p => ({ ...p, ready: false }))
    expect(lobby.readyCount).toBe(0)
  })

  it('è uguale a players.length se tutti sono pronti', () => {
    const lobby = useLobbyStore()
    lobby.players = makePlayers().map(p => ({ ...p, ready: true }))
    expect(lobby.readyCount).toBe(lobby.players.length)
  })
})

// ---------------------------------------------------------------------------
// SUITE 5 — allReady
// ---------------------------------------------------------------------------
describe('lobbyStore — allReady', () => {
  it('allReady è false se almeno un non-host non è pronto', () => {
    const lobby = useLobbyStore()
    lobby.players = makePlayers()   // p3 e p4 non pronti
    expect(lobby.allReady).toBe(false)
  })

  it('allReady è true se tutti i non-host sono pronti', () => {
    const lobby = useLobbyStore()
    // p1 è host (non conta per allReady), p2/p3/p4 tutti pronti
    lobby.players = makePlayers().map(p => ({ ...p, ready: true }))
    expect(lobby.allReady).toBe(true)
  })

  it('allReady è true se ci sono solo host', () => {
    const lobby = useLobbyStore()
    // Solo l'host in lobby — filter non-host è vuoto → every() su array vuoto → true
    lobby.players = [{ id: 'p1', name: 'Alice', isHost: true, ready: true }]
    expect(lobby.allReady).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// SUITE 6 — reset()
// ---------------------------------------------------------------------------
describe('lobbyStore — reset()', () => {
  it('reset ripristina tutti i valori allo stato iniziale', () => {
    const lobby = useLobbyStore()

    // Modifica lo stato
    lobby.lobbyCode       = 'WOLF-1234'
    lobby.players         = makePlayers()
    lobby.currentPlayerId = 'p1'
    lobby.error           = 'qualcosa è andato storto'

    // Reset
    lobby.reset()

    // Verifica
    expect(lobby.lobbyCode).toBeNull()
    expect(lobby.players).toHaveLength(0)
    expect(lobby.currentPlayerId).toBeNull()
    expect(lobby.error).toBeNull()
  })
})
