import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const socketMock = {
  connect: vi.fn(),
  emit: vi.fn(),
  on: vi.fn(),
  isConnected: { value: false },
}

vi.mock('@/composables/useSocket', () => ({
  useSocket: () => socketMock,
}))

import { useLobbyStore } from '@/stores/lobbyStore'

beforeEach(() => {
  setActivePinia(createPinia())
  socketMock.connect.mockReset()
  socketMock.emit.mockReset()
  socketMock.on.mockReset()
})

function makePlayers() {
  return [
    { id: 'p1', name: 'Alice', isHost: true, ready: true },
    { id: 'p2', name: 'Bob', isHost: false, ready: true },
    { id: 'p3', name: 'Carol', isHost: false, ready: false },
    { id: 'p4', name: 'Dave', isHost: false, ready: false },
  ]
}

describe('lobbyStore - initial state', () => {
  it('starts empty', () => {
    const lobby = useLobbyStore()
    expect(lobby.lobbyCode).toBeNull()
    expect(lobby.players).toHaveLength(0)
    expect(lobby.currentPlayerId).toBeNull()
    expect(lobby.isLoading).toBe(false)
    expect(lobby.error).toBeNull()
  })
})

describe('lobbyStore - derived state', () => {
  it('returns the current player', () => {
    const lobby = useLobbyStore()
    lobby.players = makePlayers()
    lobby.currentPlayerId = 'p2'
    expect(lobby.currentPlayer?.name).toBe('Bob')
  })

  it('detects the current host', () => {
    const lobby = useLobbyStore()
    lobby.players = makePlayers()
    lobby.currentPlayerId = 'p1'
    expect(lobby.isHost).toBe(true)
  })

  it('counts only ready guests', () => {
    const lobby = useLobbyStore()
    lobby.players = makePlayers()
    expect(lobby.readyCount).toBe(1)
  })

  it('is false when at least one guest is not ready', () => {
    const lobby = useLobbyStore()
    lobby.players = makePlayers()
    expect(lobby.allReady).toBe(false)
  })

  it('is false when the host is alone in the lobby', () => {
    const lobby = useLobbyStore()
    lobby.players = [{ id: 'p1', name: 'Alice', isHost: true, ready: true }]
    expect(lobby.allReady).toBe(false)
  })
})

describe('lobbyStore - lobby integration payloads', () => {
  it('maps host, ready players and role counts from game_state_sync payload', () => {
    const lobby = useLobbyStore()
    lobby.currentPlayerId = 'host1'
    lobby.listenToLobbyEvents()

    const syncHandler = socketMock.on.mock.calls.find(([event]) => event === 'game_state_sync')[1]
    syncHandler({
      payload: {
        state: {
          host_id: 'host1',
          ready_player_ids: ['host1', 'guest1'],
          wolf_count: 2,
          seer_count: 1,
        },
        players: [
          { player_id: 'host1', username: 'Alice', connected: true },
          { player_id: 'guest1', username: 'Bob', connected: true },
          { player_id: 'guest2', username: 'Carol', connected: true },
          { player_id: 'guest3', username: 'Dave', connected: true },
          { player_id: 'guest4', username: 'Eve', connected: true },
          { player_id: 'guest5', username: 'Frank', connected: true },
        ],
      },
    })

    expect(lobby.players[0].isHost).toBe(true)
    expect(lobby.players[1].ready).toBe(true)
    expect(lobby.players[2].ready).toBe(false)
    expect(lobby.roleSetup).toEqual({ wolves: 2, seers: 1 })
  })

  it('updates ready states from lobby:player_ready_changed payload', () => {
    const lobby = useLobbyStore()
    lobby.players = [
      { player_id: 'host1', isHost: true, is_host: true, ready: true },
      { player_id: 'guest1', isHost: false, is_host: false, ready: false },
      { player_id: 'guest2', isHost: false, is_host: false, ready: false },
    ]
    lobby.listenToLobbyEvents()

    const readyHandler = socketMock.on.mock.calls.find(([event]) => event === 'lobby:player_ready_changed')[1]
    readyHandler({
      payload: {
        client_id: 'guest2',
        ready: true,
        ready_player_ids: ['host1', 'guest2'],
      },
    })

    expect(lobby.players[1].ready).toBe(false)
    expect(lobby.players[2].ready).toBe(true)
  })

  it('emits canonical lobby events for ready and settings updates', () => {
    const lobby = useLobbyStore()
    lobby.players = [
      { player_id: 'host1', isHost: true, is_host: true, ready: true },
      { player_id: 'guest1', isHost: false, is_host: false, ready: false },
      { player_id: 'guest2', isHost: false, is_host: false, ready: false },
      { player_id: 'guest3', isHost: false, is_host: false, ready: false },
      { player_id: 'guest4', isHost: false, is_host: false, ready: false },
    ]
    lobby.currentPlayerId = 'guest1'

    lobby.toggleReady()
    expect(socketMock.emit).toHaveBeenCalledWith('lobby:player_ready', { ready: true })

    lobby.currentPlayerId = 'host1'
    lobby.adjustRole('seers', 1)
    expect(socketMock.emit).toHaveBeenCalledWith('lobby:update_settings', {
      wolf_count: 1,
      seer_count: 1,
    })
  })
})

describe('lobbyStore - reset', () => {
  it('restores the initial state', () => {
    const lobby = useLobbyStore()
    lobby.lobbyCode = 'WOLF-1234'
    lobby.players = makePlayers()
    lobby.currentPlayerId = 'p1'
    lobby.error = 'qualcosa e andato storto'

    lobby.reset()

    expect(lobby.lobbyCode).toBeNull()
    expect(lobby.players).toHaveLength(0)
    expect(lobby.currentPlayerId).toBeNull()
    expect(lobby.error).toBeNull()
  })
})
