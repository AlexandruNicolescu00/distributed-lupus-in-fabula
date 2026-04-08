<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useLobbyStore } from '@/stores/lobbyStore'
import { useGameStore, PHASES } from '@/stores/gameStore'
import { useSocket } from '@/composables/useSocket'
import { useClipboard } from '@/composables/useClipboard'
import PlayerCard from '@/components/PlayerCard.vue'
import InfoBox    from '@/components/InfoBox.vue'

// ---- SETUP ROUTING E STORES ----
const router     = useRouter()
const route      = useRoute()
const lobbyStore = useLobbyStore()
const gameStore  = useGameStore()

// Estraiamo disconnect qui in alto, nel setup sincrono, per evitare il warning
const { connect, disconnect, isConnected } = useSocket()
const { copied, copy } = useClipboard()

const lobbyCodeFromUrl = route.params.id || lobbyStore.lobbyCode

// ---- COSTANTI DI GIOCO ----
const MIN_PLAYERS = 5
const MAX_PLAYERS = 12

// ---- LIFECYCLE: CONNESSIONE DISTRIBUITA ----
onMounted(async () => {
  // 1. Validazione sessione: se non abbiamo un codice o un nome, torniamo alla home
  const savedName = sessionStorage.getItem('client_id') || localStorage.getItem('client_id')
  
  if (!lobbyCodeFromUrl || !savedName) {
    console.warn('[LobbyView] Dati sessione mancanti, redirect home')
    router.push('/')
    return
  }

  // 2. Allineiamo lo store locale
  lobbyStore.lobbyCode = lobbyCodeFromUrl
  lobbyStore.currentPlayerId = savedName

  // 3. ATTIVAZIONE LISTENER (Sempre prima della connessione!)
  lobbyStore.listenToLobbyEvents()
  gameStore.listenToGameEvents()

  // 4. CONNESSIONE AL BACKEND
  // Usiamo l'indirizzo del cluster o localhost. useSocket userà l'auth dal localStorage automaticamente.
  const wsUrl = import.meta.env.VITE_WS_URL || 'http://localhost:8000'
  
  connect(wsUrl, {
    auth: {
      client_id: savedName,
      room_id: lobbyCodeFromUrl
    }
  })

  // 5. Caricamento dati via REST (opzionale)
  try {
    if (typeof lobbyStore.loadLobbyData === 'function') {
      await lobbyStore.loadLobbyData(lobbyCodeFromUrl)
    }
  } catch (err) {
    console.warn('[LobbyView] Caricamento REST fallito, attendo sync WebSocket')
  }
})

onUnmounted(() => {
  // Pulizia facoltativa: non disconnettiamo il socket per permettere la transizione al gioco
})

// ---- WATCHERS: TRANSIZIONE AL GIOCO ----
watch(() => gameStore.phase, (newPhase) => {
  if (newPhase && newPhase !== PHASES.LOBBY && newPhase !== 'LOBBY') {
    console.log('[LobbyView] Cambio fase rilevato, vado al gioco:', newPhase)
    router.push(`/game/${lobbyCodeFromUrl}`)
  }
})

// ---- AZIONI UI ----
function copyCode()          { copy(lobbyCodeFromUrl) }

function handleToggleReady() { 
  lobbyStore.toggleReady() 
}

function handleKick(id)      { 
  lobbyStore.kickPlayer(id) 
}

function changeRole(role, delta) {
  if (!lobbyStore.isHost) return
  lobbyStore.adjustRole(role, delta)
}

function handleStart() {
  if (lobbyStore.isHost && lobbyStore.allReady) {
    lobbyStore.startGame() 
  }
}

function leaveLobby() {
  console.log('[LobbyView] Uscita dalla lobby...')

  // 1. Resettiamo lo store locale
  lobbyStore.reset()
  
  // 2. FORZIAMO la disconnessione del socket. 
  // Usiamo la funzione estratta nel setup per evitare warning
  disconnect()
  
  // 3. Torniamo alla home
  router.push('/')
}

// ---- MAPPATURA DATI (Fix per visualizzazione nomi e stato pronto) ----
function toCardPlayer(p) {
  // Il backend manda p.id o p.player_id. Usiamo quello che c'è.
  const id = p.player_id || p.id || '?'
  
  // Assicuriamoci che isHost e ready siano valori booleani puliti
  const isPlayerHost = p.isHost === true || p.is_host === true
  
  // L'host è sempre pronto. Per gli altri, usiamo il loro stato.
  const isPlayerReady = isPlayerHost ? true : (p.ready === true)

  return {
    player_id: id,
    username:  p.username || p.name || id,
    isHost:    isPlayerHost,
    ready:     isPlayerReady, // Assicuriamoci che questa proprietà si chiami 'ready'
    alive:     true,
    connected: p.connected ?? true
  }
}

const settingsRows = computed(() => [
  { label: 'Min giocatori', value: MIN_PLAYERS },
  { label: 'Max giocatori', value: MAX_PLAYERS },
  { label: 'Pronti',         value: `${lobbyStore.readyCount} / ${Math.max(0, lobbyStore.players.length - 1)}` },
  { label: 'Lupi',           value: lobbyStore.roleSummary.wolves },
  { label: 'Veggenti',       value: lobbyStore.roleSummary.seers },
  { label: 'Villici',        value: lobbyStore.roleSummary.villagers },
])
</script>

<template>
  <div class="lobby-root">
    
    <Transition name="fade">
      <div v-if="!isConnected" class="conn-overlay">
        <div class="spinner"></div>
        <p>Sincronizzazione con il cluster...</p>
      </div>
    </Transition>

    <div class="lobby-container">

      <header class="top-bar">
        <div class="brand">
          <span class="brand-wolf">🐺</span>
          <div>
            <div class="brand-name">LUPUS</div>
            <div class="brand-sub">in fabula</div>
          </div>
        </div>
        <div class="code-area">
          <div class="code-hint">codice lobby</div>
          <button class="code-btn" :class="{ copied }" @click="copyCode">
            {{ copied ? '✓ copiato!' : lobbyCodeFromUrl }}
          </button>
        </div>
      </header>

      <Transition name="fade">
        <div v-if="lobbyStore.error" class="banner err">{{ lobbyStore.error }}</div>
      </Transition>

      <div class="main-layout">

        <section class="cards-area">
          <div class="cards-title">
            Giocatori Connessi
            <span class="cards-count">{{ lobbyStore.players.length }} / {{ MAX_PLAYERS }}</span>
          </div>

          <div class="cards-grid">
            <PlayerCard
              v-for="player in lobbyStore.players"
              :key="player.player_id || player.id"
              :player="toCardPlayer(player)"
              :is-me="(player.player_id || player.id) === lobbyStore.currentPlayerId"
              :can-kick="lobbyStore.isHost && (player.player_id || player.id) !== lobbyStore.currentPlayerId"
              @kick="handleKick"
            />
            
            <div
              v-for="n in Math.max(0, MAX_PLAYERS - lobbyStore.players.length)"
              :key="'empty-' + n"
              class="card-empty"
            >
              <div class="empty-inner">+</div>
              <div class="empty-label">in attesa...</div>
            </div>
          </div>
        </section>

        <aside class="side-panel">
          <InfoBox title="Impostazioni Lobby" :rows="settingsRows" />

          <div class="progress-container">
            <div
              class="progress-bar"
              :style="{
                width: lobbyStore.players.length > 1
                  ? (lobbyStore.readyCount / (lobbyStore.players.length - 1)) * 100 + '%'
                  : '0%'
              }"
            ></div>
          </div>

          <div class="roles-box">
            <div class="panel-title">Ruoli in questa partita</div>
            <div class="role-preview">
              <span>🧑‍🌾 Villici</span>
              <span>🐺 Lupi</span>
              <span>🔮 Veggente</span>
            </div>
            <p class="role-info">I ruoli verranno assegnati casualmente all'inizio.</p>
            <div class="role-config">
              <div class="role-row">
                <span>Lupi</span>
                <div class="role-controls">
                  <button
                    class="role-btn"
                    :disabled="!lobbyStore.isHost || lobbyStore.roleSummary.wolves <= 1"
                    @click="changeRole('wolves', -1)"
                  >-</button>
                  <strong>{{ lobbyStore.roleSummary.wolves }}</strong>
                  <button
                    class="role-btn"
                    :disabled="!lobbyStore.isHost || lobbyStore.roleSummary.wolves >= lobbyStore.maxWolves"
                    @click="changeRole('wolves', 1)"
                  >+</button>
                </div>
              </div>
              <div class="role-row">
                <span>Veggente</span>
                <div class="role-controls">
                  <button
                    class="role-btn"
                    :disabled="!lobbyStore.isHost || lobbyStore.roleSummary.seers <= 0"
                    @click="changeRole('seers', -1)"
                  >-</button>
                  <strong>{{ lobbyStore.roleSummary.seers }}</strong>
                  <button
                    class="role-btn"
                    :disabled="!lobbyStore.isHost || lobbyStore.roleSummary.seers >= lobbyStore.maxSeers"
                    @click="changeRole('seers', 1)"
                  >+</button>
                </div>
              </div>
              <div class="role-row">
                <span>Villici</span>
                <strong>{{ lobbyStore.roleSummary.villagers }}</strong>
              </div>
            </div>
          </div>

          <div class="actions-footer">
            <template v-if="lobbyStore.isHost">
              <button
                class="btn-main"
                :disabled="!lobbyStore.allReady || lobbyStore.players.length < MIN_PLAYERS"
                @click="handleStart"
              >
                <span v-if="lobbyStore.players.length < MIN_PLAYERS">Min. {{ MIN_PLAYERS }} giocatori</span>
                <span v-else-if="!lobbyStore.allReady">In attesa dei pronti...</span>
                <span v-else>🐺 Inizia Partita</span>
              </button>
            </template>

            <template v-else>
              <button
                class="btn-ready"
                :class="{ 'is-active': lobbyStore.isCurrentPlayerReady }"
                @click="handleToggleReady"
              >
                {{ lobbyStore.isCurrentPlayerReady ? '✓ Pronto (Annulla)' : 'Sono Pronto!' }}
              </button>
            </template>

            <button class="btn-leave" @click="leaveLobby">Esci dalla Lobby</button>
          </div>
          
          <p class="share-footer">
            Invia il codice <strong>{{ lobbyCodeFromUrl }}</strong> ai tuoi amici per farli unire.
          </p>
        </aside>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lobby-root { min-height: 100vh; background: #07070f; color: #e8e0d5; font-family: 'Lato', sans-serif; overflow-x: hidden; }
.lobby-container { max-width: 1100px; margin: 0 auto; padding: 2rem; display: flex; flex-direction: column; gap: 2rem; }

/* Overlay Connessione */
.conn-overlay { position: fixed; inset: 0; background: rgba(7,7,15,0.95); z-index: 1000; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1rem; }
.spinner { width: 40px; height: 40px; border: 3px solid rgba(232, 200, 122, 0.1); border-top-color: #e8c87a; border-radius: 50%; animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Header & Code Area */
.top-bar { display: flex; justify-content: space-between; align-items: flex-end; padding-bottom: 1.5rem; border-bottom: 1px solid rgba(255,255,255,0.08); }
.brand { display: flex; align-items: center; gap: 1rem; }
.brand-wolf { font-size: 2.5rem; }
.brand-name { font-family: 'Cinzel', serif; font-size: 1.8rem; color: #e8c87a; line-height: 1; letter-spacing: 2px; }
.brand-sub { font-size: 0.8rem; color: #a1a1aa; text-transform: uppercase; letter-spacing: 4px; }

.code-area { text-align: right; }
.code-hint { font-size: 0.6rem; text-transform: uppercase; color: #a1a1aa; margin-bottom: 0.3rem; letter-spacing: 1px; }
.code-btn { font-family: 'Cinzel', serif; background: rgba(232,200,122,0.05); border: 1px solid rgba(232,200,122,0.4); color: #e8c87a; padding: 0.6rem 1.2rem; border-radius: 8px; cursor: pointer; transition: 0.3s; font-size: 1.1rem; }
.code-btn:hover { background: rgba(232,200,122,0.15); border-color: #e8c87a; }
.code-btn.copied { background: #4ade80; color: #07070f; border-color: #4ade80; }

/* Main Layout */
.main-layout { display: grid; grid-template-columns: 1fr 320px; gap: 2.5rem; }

/* Cards Area */
.cards-area { display: flex; flex-direction: column; gap: 1.5rem; }
.cards-title { font-family: 'Cinzel', serif; font-size: 1.2rem; display: flex; justify-content: space-between; align-items: center; }
.cards-count { font-size: 0.9rem; color: #a1a1aa; font-family: 'Lato', sans-serif; }
.cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 1.2rem; }

.card-empty { aspect-ratio: 3/4; background: rgba(255,255,255,0.02); border: 1px dashed rgba(255,255,255,0.1); border-radius: 12px; display: flex; flex-direction: column; align-items: center; justify-content: center; opacity: 0.4; transition: 0.3s; }
.empty-inner { font-size: 2rem; color: rgba(255,255,255,0.05); font-weight: 300; }
.empty-label { font-size: 0.7rem; color: rgba(255,255,255,0.2); text-transform: uppercase; }

/* Side Panel */
.side-panel { display: flex; flex-direction: column; gap: 1.5rem; }
.progress-container { height: 6px; background: rgba(255,255,255,0.05); border-radius: 3px; overflow: hidden; margin-top: -0.5rem; }
.progress-bar { height: 100%; background: #e8c87a; transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1); box-shadow: 0 0 15px rgba(232,200,122,0.3); }

.roles-box { background: rgba(255,255,255,0.02); padding: 1.2rem; border-radius: 12px; border: 1px solid rgba(255,255,255,0.05); }
.panel-title { font-size: 0.8rem; text-transform: uppercase; color: #e8c87a; letter-spacing: 1px; margin-bottom: 0.8rem; }
.role-preview { display: flex; flex-wrap: wrap; gap: 0.8rem; margin-bottom: 1rem; }
.role-preview span { background: rgba(255,255,255,0.05); padding: 0.3rem 0.6rem; border-radius: 6px; font-size: 0.85rem; }
.role-info { font-size: 0.75rem; color: rgba(255,255,255,0.3); font-style: italic; line-height: 1.4; }
.role-config { display: flex; flex-direction: column; gap: 0.8rem; margin-top: 1rem; }
.role-row { display: flex; justify-content: space-between; align-items: center; gap: 1rem; font-size: 0.9rem; }
.role-controls { display: flex; align-items: center; gap: 0.6rem; }
.role-btn { width: 28px; height: 28px; border-radius: 8px; border: 1px solid rgba(232,200,122,0.3); background: rgba(232,200,122,0.05); color: #e8c87a; cursor: pointer; }
.role-btn:disabled { opacity: 0.35; cursor: not-allowed; }

/* Action Buttons */
.actions-footer { display: flex; flex-direction: column; gap: 0.8rem; }
.btn-main, .btn-ready { width: 100%; padding: 1.1rem; border-radius: 12px; border: none; font-family: 'Cinzel', serif; font-weight: bold; font-size: 1rem; cursor: pointer; transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); }

.btn-main { background: #7c0000; color: white; box-shadow: 0 4px 15px rgba(124,0,0,0.3); }
.btn-main:hover:not(:disabled) { background: #a80000; transform: translateY(-2px); }
.btn-main:disabled { background: #1a1a1a; color: #444; cursor: not-allowed; box-shadow: none; }

.btn-ready { background: rgba(232,200,122,0.05); color: #e8c87a; border: 1px solid rgba(232,200,122,0.4); }
.btn-ready:hover { background: rgba(232,200,122,0.1); border-color: #e8c87a; }
.btn-ready.is-active { background: #4ade80; color: #07070f; border-color: #4ade80; box-shadow: 0 4px 15px rgba(74,222,128,0.2); }

.btn-leave { background: transparent; border: 1px solid rgba(248,113,113,0.2); color: rgba(248,113,113,0.7); padding: 0.7rem; border-radius: 8px; cursor: pointer; font-size: 0.8rem; transition: 0.2s; }
.btn-leave:hover { background: rgba(248,113,113,0.05); border-color: #f87171; color: #f87171; }

.share-footer { font-size: 0.75rem; color: rgba(255,255,255,0.2); text-align: center; line-height: 1.5; }
.banner.err { background: rgba(220,38,38,0.1); border: 1px solid rgba(239,68,68,0.5); color: #f87171; padding: 1rem; border-radius: 12px; text-align: center; font-size: 0.9rem; }

/* Transitions */
.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

@media (max-width: 900px) {
  .main-layout { grid-template-columns: 1fr; }
  .side-panel { order: -1; }
}
</style>
