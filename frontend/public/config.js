// Configurazione runtime iniettata dal container all'avvio (vedi docker-entrypoint.sh).
// Questo è il DEFAULT usato in dev (`npm run dev`) e come fallback se l'entrypoint
// non gira: WS_URL vuoto → l'app ripiega su VITE_WS_URL o sulla stessa origine.
window.__APP_CONFIG__ = { WS_URL: '' }
