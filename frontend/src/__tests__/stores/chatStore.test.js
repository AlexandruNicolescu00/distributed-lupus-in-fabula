import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from '@/stores/chatStore'
import { useGameStore, PHASES, ROLES } from '@/stores/gameStore'

// ---------------------------------------------------------------------------
// Setup — Pinia fresca prima di ogni test
// ---------------------------------------------------------------------------
beforeEach(() => {
  setActivePinia(createPinia())
})

// ---------------------------------------------------------------------------
// Helper — messaggi mock per i tre canali
// ---------------------------------------------------------------------------
function makeMessages() {
  return [
    { id: 1, senderId: 'p1', senderName: 'Alice', text: 'Ciao',         channel: 'global', timestamp: new Date().toISOString() },
    { id: 2, senderId: 'p2', senderName: 'Bob',   text: 'Sono il lupo', channel: 'wolves', timestamp: new Date().toISOString() },
    { id: 3, senderId: 'p3', senderName: 'Carol', text: 'Sono morta',   channel: 'dead',   timestamp: new Date().toISOString() },
  ]
}

// ---------------------------------------------------------------------------
// SUITE 1 — Stato iniziale
// ---------------------------------------------------------------------------
describe('chatStore — stato iniziale', () => {
  it('messages è vuoto inizialmente', () => {
    const chat = useChatStore()
    expect(chat.messages).toHaveLength(0)
  })

  it('isOpen è false inizialmente', () => {
    const chat = useChatStore()
    expect(chat.isOpen).toBe(false)
  })

  it('unreadCount è 0 inizialmente', () => {
    const chat = useChatStore()
    expect(chat.unreadCount).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// SUITE 2 — visibleMessages per fase e ruolo
// ---------------------------------------------------------------------------
describe('chatStore — visibleMessages', () => {
  it('durante il giorno mostra solo messaggi global', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.DAY
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p1', username: 'Alice', role: ROLES.VILLAGER, alive: true }]
    game.currentPlayerId = 'p1'
    chat.messages = makeMessages()

    const visible = chat.visibleMessages
    expect(visible.every(m => m.channel === 'global')).toBe(true)
    expect(visible).toHaveLength(1)
  })

  it('i lupi vedono anche i messaggi wolves durante la notte', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.NIGHT
    game.myRole = ROLES.WOLF
    game.players = [{ player_id: 'p2', username: 'Bob', role: ROLES.WOLF, alive: true }]
    game.currentPlayerId = 'p2'
    chat.messages = makeMessages()

    const visible = chat.visibleMessages
    const channels = visible.map(m => m.channel)
    expect(channels).toContain('wolves')
  })

  it('i morti vedono solo i messaggi dead', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.DAY
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p3', username: 'Carol', role: ROLES.VILLAGER, alive: false }]
    game.currentPlayerId = 'p3'
    chat.messages = makeMessages()

    const visible = chat.visibleMessages
    expect(visible.every(m => m.channel === 'dead')).toBe(true)
    expect(visible).toHaveLength(1)
  })

  it('i vivi non vedono i messaggi dead', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.DAY
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p1', username: 'Alice', role: ROLES.VILLAGER, alive: true }]
    game.currentPlayerId = 'p1'
    chat.messages = makeMessages()

    const visible = chat.visibleMessages
    expect(visible.some(m => m.channel === 'dead')).toBe(false)
  })

  it('i non-lupi non vedono i messaggi wolves', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.DAY
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p1', username: 'Alice', role: ROLES.VILLAGER, alive: true }]
    game.currentPlayerId = 'p1'
    chat.messages = makeMessages()

    const visible = chat.visibleMessages
    expect(visible.some(m => m.channel === 'wolves')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 3 — activeChannel
// ---------------------------------------------------------------------------
describe('chatStore — activeChannel', () => {
  it('canale global durante il giorno per un vivo', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.DAY
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p1', username: 'Alice', role: ROLES.VILLAGER, alive: true }]
    game.currentPlayerId = 'p1'
    expect(chat.activeChannel).toBe('global')
  })

  it('canale wolves di notte per un lupo', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.NIGHT
    game.myRole = ROLES.WOLF
    game.players = [{ player_id: 'p2', username: 'Bob', role: ROLES.WOLF, alive: true }]
    game.currentPlayerId = 'p2'
    expect(chat.activeChannel).toBe('wolves')
  })

  it('canale dead per un giocatore morto', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.DAY
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p3', username: 'Carol', role: ROLES.VILLAGER, alive: false }]
    game.currentPlayerId = 'p3'
    expect(chat.activeChannel).toBe('dead')
  })
})

// ---------------------------------------------------------------------------
// SUITE 4 — canChat
// ---------------------------------------------------------------------------
describe('chatStore — canChat', () => {
  it('vivo di giorno può chattare', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.DAY
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p1', username: 'Alice', role: ROLES.VILLAGER, alive: true }]
    game.currentPlayerId = 'p1'
    expect(chat.canChat).toBe(true)
  })

  it('vivo non-lupo di notte NON può chattare', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.NIGHT
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p1', username: 'Alice', role: ROLES.VILLAGER, alive: true }]
    game.currentPlayerId = 'p1'
    expect(chat.canChat).toBe(false)
  })

  it('lupo di notte PUÒ chattare', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.NIGHT
    game.myRole = ROLES.WOLF
    game.players = [{ player_id: 'p2', username: 'Bob', role: ROLES.WOLF, alive: true }]
    game.currentPlayerId = 'p2'
    expect(chat.canChat).toBe(true)
  })

  it('morto PUÒ chattare nel canale dead', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.DAY
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p3', username: 'Carol', role: ROLES.VILLAGER, alive: false }]
    game.currentPlayerId = 'p3'
    expect(chat.canChat).toBe(true)
  })

  it('nessuno può chattare in LOBBY', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.LOBBY
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p1', username: 'Alice', role: ROLES.VILLAGER, alive: true }]
    game.currentPlayerId = 'p1'
    expect(chat.canChat).toBe(false)
  })

  it('nessuno può chattare in ENDED', () => {
    const chat = useChatStore()
    const game = useGameStore()
    game.phase  = PHASES.ENDED
    game.myRole = ROLES.VILLAGER
    game.players = [{ player_id: 'p1', username: 'Alice', role: ROLES.VILLAGER, alive: true }]
    game.currentPlayerId = 'p1'
    expect(chat.canChat).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 5 — openChat / closeChat / unreadCount
// ---------------------------------------------------------------------------
describe('chatStore — openChat / closeChat', () => {
  it('openChat imposta isOpen a true e azzera unreadCount', () => {
    const chat = useChatStore()
    chat.unreadCount = 5
    chat.openChat()
    expect(chat.isOpen).toBe(true)
    expect(chat.unreadCount).toBe(0)
  })

  it('closeChat imposta isOpen a false', () => {
    const chat = useChatStore()
    chat.openChat()
    chat.closeChat()
    expect(chat.isOpen).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 6 — reset()
// ---------------------------------------------------------------------------
describe('chatStore — reset()', () => {
  it('reset ripristina tutti i valori', () => {
    const chat = useChatStore()
    chat.messages    = makeMessages()
    chat.isOpen      = true
    chat.unreadCount = 3

    chat.reset()

    expect(chat.messages).toHaveLength(0)
    expect(chat.isOpen).toBe(false)
    expect(chat.unreadCount).toBe(0)
  })
})
