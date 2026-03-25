/**
 * Wrapper per le chiamate REST al backend FastAPI.
 * Centralizza base URL, headers, e gestione errori.
 *
 * Esempio:
 *   import { api } from '@/services/api'
 *   const lobby = await api.post('/lobby/create', { playerName: 'Marco' })
 */

const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

async function request(method, path, body = null) {
  const options = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body) options.body = JSON.stringify(body)

  const response = await fetch(`${BASE_URL}${path}`, options)

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Errore sconosciuto' }))
    throw new Error(err.detail ?? `HTTP ${response.status}`)
  }

  return response.json()
}

export const api = {
  get:    (path)        => request('GET',    path),
  post:   (path, body)  => request('POST',   path, body),
  put:    (path, body)  => request('PUT',    path, body),
  delete: (path)        => request('DELETE', path),
}

// -------------------------------------------------------
// Endpoints specifici del gioco (da completare col team)
// -------------------------------------------------------

export const lobbyApi = {
  /** Crea una nuova stanza, ritorna { lobbyCode, playerId } */
  create: (playerName) => api.post('/lobby/create', { playerName }),

  /** Entra in una stanza esistente */
  join: (lobbyCode, playerName) => api.post('/lobby/join', { lobbyCode, playerName }),

  /** Recupera lo stato corrente della lobby */
  getState: (lobbyCode) => api.get(`/lobby/${lobbyCode}`),

  /** Abbandona la lobby */
  leave: (lobbyCode, playerId) => api.post('/lobby/leave', { lobbyCode, playerId }),
}

export const gameApi = {
  /** Avvia la partita (solo host) */
  start: (lobbyCode) => api.post(`/game/${lobbyCode}/start`),

  /** Recupera lo stato corrente della partita (usato al resume) */
  getState: (lobbyCode) => api.get(`/game/${lobbyCode}/state`),

  /** Vota un giocatore per eliminarlo */
  vote: (lobbyCode, targetId) => api.post(`/game/${lobbyCode}/vote`, { targetId }),

  /** Azione notturna (lupo o veggente) */
  nightAction: (lobbyCode, targetId) => api.post(`/game/${lobbyCode}/night-action`, { targetId }),
}
