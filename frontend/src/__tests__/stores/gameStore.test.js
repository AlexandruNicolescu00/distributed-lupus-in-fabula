import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useGameStore, PHASES, ROLES, WINNERS } from '@/stores/gameStore'

// ---------------------------------------------------------------------------
// Setup — crea una Pinia fresca prima di ogni test
// Questo garantisce che ogni test parta da uno stato pulito
// ---------------------------------------------------------------------------
beforeEach(() => {
  setActivePinia(createPinia())
})

// ---------------------------------------------------------------------------
// Helpers — dati mock riusabili nei test
// ---------------------------------------------------------------------------
function makePlayers() {
  return [
    { player_id: 'p1', username: 'Alice', role: ROLES.VILLAGER, alive: true  },
    { player_id: 'p2', username: 'Bob',   role: ROLES.WOLF,     alive: true  },
    { player_id: 'p3', username: 'Carol', role: ROLES.SEER,     alive: true  },
    { player_id: 'p4', username: 'Dave',  role: ROLES.VILLAGER, alive: false },
  ]
}

// ---------------------------------------------------------------------------
// SUITE 1 — Stato iniziale : Verifica che quando crei lo store da zero, tutti i valori partano corretti
// ---------------------------------------------------------------------------
describe('gameStore — stato iniziale', () => {
  it('parte dalla fase LOBBY', () => {
    const game = useGameStore()
    expect(game.phase).toBe(PHASES.LOBBY)
  })

  it('round iniziale è 0', () => {
    const game = useGameStore()
    expect(game.round).toBe(0)
  })

  it('lista giocatori inizialmente vuota', () => {
    const game = useGameStore()
    expect(game.players).toHaveLength(0)
  })

  it('nessun ruolo assegnato inizialmente', () => {
    const game = useGameStore()
    expect(game.myRole).toBeNull()
  })

  it('nessun vincitore inizialmente', () => {
    const game = useGameStore()
    expect(game.winner).toBeNull()
  })

  it('isPaused è false inizialmente', () => {
    const game = useGameStore()
    expect(game.isPaused).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 2 — Getters sui giocatori : Test per verificare che filtrio e che restituiscano i giocatori giusti
// ---------------------------------------------------------------------------
describe('gameStore — alivePlayers / deadPlayers', () => {
  it('alivePlayers restituisce solo i giocatori vivi', () => {
    const game = useGameStore()
    game.players = makePlayers()
    // p1, p2, p3 sono vivi — p4 è morto
    expect(game.alivePlayers).toHaveLength(3)
    expect(game.alivePlayers.every(p => p.alive)).toBe(true)
  })

  it('deadPlayers restituisce solo i giocatori morti', () => {
    const game = useGameStore()
    game.players = makePlayers()
    expect(game.deadPlayers).toHaveLength(1)
    expect(game.deadPlayers[0].player_id).toBe('p4')
  })

  it('alivePlayers è vuoto se tutti sono morti', () => {
    const game = useGameStore()
    game.players = makePlayers().map(p => ({ ...p, alive: false }))
    expect(game.alivePlayers).toHaveLength(0)
  })

  it('me restituisce il giocatore corrente', () => {
    const game = useGameStore()
    game.players = makePlayers()
    game.currentPlayerId = 'p2'
    expect(game.me?.username).toBe('Bob')
  })

  it('me è undefined se currentPlayerId non corrisponde', () => {
    const game = useGameStore()
    game.players = makePlayers()
    game.currentPlayerId = 'p99'
    expect(game.me).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// SUITE 3 — Getters ruolo : Controllano cosa puo fare il giocatore
// ---------------------------------------------------------------------------
describe('gameStore — isWolf / isSeer / isVillager', () => {
  it('isWolf è true se il ruolo è WOLF', () => {
    const game = useGameStore()
    game.myRole = ROLES.WOLF
    expect(game.isWolf).toBe(true)
    expect(game.isSeer).toBe(false)
    expect(game.isVillager).toBe(false)
  })

  it('isSeer è true se il ruolo è SEER', () => {
    const game = useGameStore()
    game.myRole = ROLES.SEER
    expect(game.isSeer).toBe(true)
    expect(game.isWolf).toBe(false)
  })

  it('isVillager è true se il ruolo è VILLAGER', () => {
    const game = useGameStore()
    game.myRole = ROLES.VILLAGER
    expect(game.isVillager).toBe(true)
    expect(game.isWolf).toBe(false)
  })

  it('tutti false se myRole è null', () => {
    const game = useGameStore()
    game.myRole = null
    expect(game.isWolf).toBe(false)
    expect(game.isSeer).toBe(false)
    expect(game.isVillager).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 4 — isAlive : Determina se il giocatore può ancora interagire — votare, chattare, agire. 
// ---------------------------------------------------------------------------
describe('gameStore — isAlive', () => {
  it('isAlive è true se il giocatore corrente è vivo', () => {
    const game = useGameStore()
    game.players = makePlayers()
    game.currentPlayerId = 'p1'
    expect(game.isAlive).toBe(true)
  })

  it('isAlive è false se il giocatore corrente è morto', () => {
    const game = useGameStore()
    game.players = makePlayers()
    game.currentPlayerId = 'p4'   // p4 è morto
    expect(game.isAlive).toBe(false)
  })

  it('isAlive è false se currentPlayerId è null', () => {
    const game = useGameStore()
    game.players = makePlayers()
    game.currentPlayerId = null
    expect(game.isAlive).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 5 — secondsLeft e timerProgress : Test del calcolo timer_end - Date.now()/1000 
// ---------------------------------------------------------------------------
describe('gameStore — timer', () => {
  it('secondsLeft è null se timerEnd è null', () => {
    const game = useGameStore()
    game.timerEnd = null
    expect(game.secondsLeft).toBeNull()
  })

  it('secondsLeft è 0 se timerEnd è nel passato', () => {
    const game = useGameStore()
    game.timerEnd = Date.now() / 1000 - 10   // 10 secondi fa
    expect(game.secondsLeft).toBe(0)
  })

  it('secondsLeft è positivo se timerEnd è nel futuro', () => {
    const game = useGameStore()
    game.timerEnd = Date.now() / 1000 + 30   // 30 secondi nel futuro
    expect(game.secondsLeft).toBeGreaterThan(0)
    expect(game.secondsLeft).toBeLessThanOrEqual(30)
  })

  it('timerProgress è 0 se timerEnd è null', () => {
    const game = useGameStore()
    game.timerEnd = null
    expect(game.timerProgress).toBe(0)
  })

  it('timerProgress è positivo durante DAY con timer attivo', () => {
    const game = useGameStore()
    game.phase    = PHASES.DAY
    game.timerEnd = Date.now() / 1000 + 60   // 60s su 120s totali → ~50%
    expect(game.timerProgress).toBeGreaterThan(0)
    expect(game.timerProgress).toBeLessThanOrEqual(100)
  })
})

// ---------------------------------------------------------------------------
// SUITE 6 — voteCount
// ---------------------------------------------------------------------------
describe('gameStore — voteCount', () => {
  it('voteCount è vuoto se nessuno ha votato', () => {
    const game = useGameStore()
    game.voteMap = {}
    expect(Object.keys(game.voteCount)).toHaveLength(0)
  })

  it('voteCount conta correttamente i voti per target', () => {
    const game = useGameStore()
    // p1 e p2 votano entrambi p3 — p4 vota p1
    game.voteMap = { p1: 'p3', p2: 'p3', p4: 'p1' }
    expect(game.voteCount['p3']).toBe(2)
    expect(game.voteCount['p1']).toBe(1)
    expect(game.voteCount['p2']).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// SUITE 7 — reset() : Quando la partita finisce e si torna alla home, lo store deve essere completamente pulito.
// ---------------------------------------------------------------------------
describe('gameStore — reset()', () => {
  it('reset ripristina tutti i valori allo stato iniziale', () => {
    const game = useGameStore()

    // Modifica lo stato
    game.phase           = PHASES.NIGHT
    game.round           = 5
    game.players         = makePlayers()
    game.myRole          = ROLES.WOLF
    game.winner          = WINNERS.WOLVES
    game.timerEnd        = Date.now() / 1000 + 60
    game.isPaused        = true
    game.voteMap         = { p1: 'p2' }
    game.seerResult      = { targetId: 'p2', targetName: 'Bob', role: ROLES.WOLF }
    game.noElimination   = true

    // Reset
    game.reset()

    // Verifica ripristino
    expect(game.phase).toBe(PHASES.LOBBY)
    expect(game.round).toBe(0)
    expect(game.players).toHaveLength(0)
    expect(game.myRole).toBeNull()
    expect(game.winner).toBeNull()
    expect(game.timerEnd).toBeNull()
    expect(game.isPaused).toBe(false)
    expect(game.voteMap).toEqual({})
    expect(game.seerResult).toBeNull()
    expect(game.noElimination).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 8 — costanti esportate : Questi valori devono essere esattamente in UPPERCASE perché il backend Python li confronta come stringhe. 
// ---------------------------------------------------------------------------
describe('gameStore — costanti PHASES / ROLES / WINNERS', () => {
  it('PHASES contiene tutti i valori attesi in UPPERCASE', () => {
    expect(PHASES.LOBBY).toBe('LOBBY')
    expect(PHASES.DAY).toBe('DAY')
    expect(PHASES.VOTING).toBe('VOTING')
    expect(PHASES.NIGHT).toBe('NIGHT')
    expect(PHASES.ENDED).toBe('ENDED')
  })

  it('ROLES contiene tutti i valori attesi in UPPERCASE', () => {
    expect(ROLES.VILLAGER).toBe('VILLAGER')
    expect(ROLES.WOLF).toBe('WOLF')
    expect(ROLES.SEER).toBe('SEER')
  })

  it('WINNERS contiene tutti i valori attesi in UPPERCASE', () => {
    expect(WINNERS.VILLAGERS).toBe('VILLAGERS')
    expect(WINNERS.WOLVES).toBe('WOLVES')
  })
})
