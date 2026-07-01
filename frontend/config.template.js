// Template processato da `envsubst` in docker-entrypoint.sh all'avvio del container.
// ${WS_URL} viene sostituito con il valore dell'omonima env var (impostata da k8s).
window.__APP_CONFIG__ = { WS_URL: '${WS_URL}' }
