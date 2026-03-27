import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const socketHandlers = {}

vi.mock('@/composables/useSocket', () => ({
  useSocket: () => ({
    emit: vi.fn(),
    on: (event, callback) => {
      socketHandlers[event] = callback
    },
  }),
}))

import { PHASES, useGameStore } from '@/stores/gameStore'

describe('gameStore — socket events', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.keys(socketHandlers).forEach((key) => delete socketHandlers[key])
  })

  it('handleStateSync usa anche payload.players del backend', () => {
    const game = useGameStore()

    game.handleStateSync({
      state: { phase: PHASES.DAY, round: 2 },
      players: [
        { player_id: 'p1', name: 'Alice', connected: true },
        { player_id: 'p2', username: 'Bob', alive: false },
      ],
    })

    expect(game.phase).toBe(PHASES.DAY)
    expect(game.round).toBe(2)
    expect(game.players).toEqual([
      { player_id: 'p1', username: 'Alice', role: null, alive: true, connected: true },
      { player_id: 'p2', username: 'Bob', role: null, alive: false, connected: true },
    ])
  })

  it('start_game porta fuori dalla fase LOBBY anche prima di phase_changed', () => {
    const game = useGameStore()
    game.listenToGameEvents()

    socketHandlers.start_game?.({})

    expect(game.phase).toBe(PHASES.DAY)
  })
})
