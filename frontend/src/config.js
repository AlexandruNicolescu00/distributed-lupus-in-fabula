// Risoluzione dell'endpoint Socket.IO con precedenza:
//   1. window.__APP_CONFIG__.WS_URL  → iniettato a RUNTIME dal container (k8s/compose)
//   2. import.meta.env.VITE_WS_URL   → build-time, usato in dev (`npm run dev`)
//   3. window.location.origin        → fallback: stessa origine che ha servito la pagina
export function getWsUrl() {
  const runtime = window.__APP_CONFIG__?.WS_URL
  if (runtime) return runtime
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL
  return window.location.origin
}

// Base URL per le chiamate REST (`/api/*`). Stessa priorità di getWsUrl:
//   1. config runtime iniettato dal container (in prod REST e WS condividono host)
//   2. VITE_API_URL (build-time)
//   3. in dev (`npm run dev`) il backend è su :8000, separato dal dev-server Vite
//   4. altrimenti stessa origine → il reverse proxy instrada /api/* al backend
export function getApiUrl() {
  const runtime = window.__APP_CONFIG__?.WS_URL
  if (runtime) return runtime
  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL
  if (import.meta.env.DEV) return 'http://localhost:8000'
  return window.location.origin
}
