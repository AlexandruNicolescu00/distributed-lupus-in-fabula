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

describe('gameStore - socket events', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.keys(socketHandlers).forEach((key) => delete socketHandlers[key])
  })

  it('handleStateSync reads payload.players from websocket messages', () => {
    const game = useGameStore()
    game.currentPlayerId = 'p1'

    game.handleStateSync({
      payload: {
        state: { phase: PHASES.DAY, round: 2, host_id: 'p1' },
        players: [
          { player_id: 'p1', name: 'Alice', connected: true },
          { player_id: 'p2', username: 'Bob', alive: false },
        ],
      },
    })

    expect(game.phase).toBe(PHASES.DAY)
    expect(game.round).toBe(2)
    expect(game.hostId).toBe('p1')
    expect(game.players).toHaveLength(2)
    expect(game.players[0]).toMatchObject({
      player_id: 'p1',
      username: 'Alice',
      alive: true,
      connected: true,
    })
    expect(game.players[1]).toMatchObject({
      player_id: 'p2',
      username: 'Bob',
      alive: false,
      connected: true,
    })
  })

  it('game_start moves the store out of LOBBY even before phase_changed', () => {
    const game = useGameStore()
    game.listenToGameEvents()

    socketHandlers.game_start?.({})

    expect(game.phase).toBe(PHASES.NIGHT)
  })

  it('phase_changed reads the backend payload wrapper', () => {
    const game = useGameStore()
    game.listenToGameEvents()

    socketHandlers.phase_changed?.({
      payload: {
        phase: PHASES.NIGHT,
        round: 3,
        timer_end: 12345,
      },
    })

    expect(game.phase).toBe(PHASES.NIGHT)
    expect(game.round).toBe(3)
    expect(game.timerEnd).toBe(12345)
  })

  it('role_assigned reads the backend payload wrapper', () => {
    const game = useGameStore()
    game.listenToGameEvents()

    socketHandlers.role_assigned?.({
      payload: {
        role: 'WOLF',
        wolf_companions: [{ player_id: 'p2', username: 'Bob' }],
      },
    })

    expect(game.myRole).toBe('WOLF')
    expect(game.wolfCompanions).toEqual([{ player_id: 'p2', username: 'Bob' }])
  })

  it('handleStateSync keeps roles hidden until the backend assigns my role', () => {
    const game = useGameStore()
    game.currentPlayerId = 'p1'

    game.handleStateSync({
      payload: {
        state: { phase: PHASES.NIGHT, round: 0, host_id: 'p1' },
        players: [
          { player_id: 'p1', username: 'Alice', connected: true, role: null },
          { player_id: 'p2', username: 'Bob', connected: true, role: null },
        ],
      },
    })

    expect(game.players[0].role).toBeNull()
    expect(game.players[1].role).toBeNull()
    expect(game.myRole).toBeNull()
  })
})
