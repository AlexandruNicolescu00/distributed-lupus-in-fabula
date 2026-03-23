<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useLobbyStore } from '@/stores/lobbyStore'
import { useClipboard } from '@/composables/useClipboard'
import PlayerCard from '@/components/PlayerCard.vue'
import InfoBox    from '@/components/InfoBox.vue'

const router     = useRouter()
const lobbyStore = useLobbyStore()
const { copied, copy } = useClipboard()

onMounted(() => {
  if (!lobbyStore.lobbyCode) {
    // MOCK TEMPORANEO — rimuovere quando il backend è pronto
    lobbyStore.lobbyCode       = 'WOLF-4821'
    lobbyStore.currentPlayerId = 'p1'
    lobbyStore.players = [
      { id: 'p1', name: 'Tu',    isHost: true,  ready: true  },
      { id: 'p2', name: 'Marco', isHost: false, ready: true  },
      { id: 'p3', name: 'Sofia', isHost: false, ready: false },
      { id: 'p4', name: 'Luca',  isHost: false, ready: false },
    ]
  }
})

function copyCode()          { copy(lobbyStore.lobbyCode) }
function handleToggleReady() { lobbyStore.toggleReady() }
function handleKick(id)      { lobbyStore.kickPlayer(id) }
function handleStart()       { router.push('/game') }
function leaveLobby()        { lobbyStore.reset(); router.push('/') }

function toCardPlayer(p) {
  return {
    player_id: p.id ?? p.player_id,
    username:  p.name ?? p.username,
    isHost:    p.isHost,
    ready:     p.ready,
    alive:     true,
  }
}

const MIN_PLAYERS = 5
const MAX_PLAYERS = 12

// Righe per InfoBox — calcolate come computed per reattività
import { computed } from 'vue'
const settingsRows = computed(() => [
  { label: 'Min giocatori', value: MIN_PLAYERS },
  { label: 'Max giocatori', value: MAX_PLAYERS },
  { label: 'Pronti',        value: `${lobbyStore.readyCount} / ${lobbyStore.players.length}` },
])
</script>

<template>
  <div class="lobby-root">
    <div class="lobby-container">

      <!-- HEADER -->
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
            {{ copied ? '✓ copiato!' : lobbyStore.lobbyCode }}
          </button>
        </div>
      </header>

      <!-- BANNER -->
      <div v-if="!lobbyStore.isConnected" class="banner warn">
        ⚠ Connessione persa — riconnessione in corso...
      </div>
      <div v-if="lobbyStore.error" class="banner err">{{ lobbyStore.error }}</div>

      <!-- LAYOUT -->
      <div class="main-layout">

        <!-- GRIGLIA CARTE -->
        <section class="cards-area">
          <div class="cards-title">
            Giocatori
            <span class="cards-count">{{ lobbyStore.players.length }} / {{ MAX_PLAYERS }}</span>
          </div>

          <div class="cards-grid">
            <PlayerCard
              v-for="player in lobbyStore.players"
              :key="player.id ?? player.player_id"
              :player="toCardPlayer(player)"
              :is-me="(player.id ?? player.player_id) === lobbyStore.currentPlayerId"
              :can-kick="lobbyStore.isHost && (player.id ?? player.player_id) !== lobbyStore.currentPlayerId"
              @kick="handleKick"
            />
            <div
              v-for="n in MAX_PLAYERS - lobbyStore.players.length"
              :key="'empty-' + n"
              class="card-empty"
            >
              <svg viewBox="0 0 120 160" xmlns="http://www.w3.org/2000/svg">
                <rect width="120" height="160" rx="8"
                  fill="#0c0c14" stroke="#1e1e2e" stroke-width="1" stroke-dasharray="5 3"/>
                <text x="60" y="90" text-anchor="middle"
                  font-size="30" fill="#1e1e2e" font-family="sans-serif">+</text>
              </svg>
              <div class="empty-label">in attesa...</div>
            </div>
          </div>
        </section>

        <!-- SIDE PANEL -->
        <aside class="side-panel">

          <!-- ← InfoBox sostituisce il vecchio blocco .info-box -->
          <InfoBox title="Impostazioni" :rows="settingsRows" />

          <!-- Barra progresso pronti -->
          <div class="progress-wrap">
            <div
              class="progress-fill"
              :style="{
                width: lobbyStore.players.length
                  ? (lobbyStore.readyCount / lobbyStore.players.length) * 100 + '%'
                  : '0%'
              }"
            ></div>
          </div>

          <!-- Ruoli in gioco -->
          <div class="roles-box">
            <div class="panel-title">Ruoli in gioco</div>
            <div class="role-row"><span>🧑‍🌾</span><span>Contadino</span></div>
            <div class="role-row"><span>🐺</span><span>Lupo</span></div>
            <div class="role-row"><span>🔮</span><span>Veggente</span></div>
            <div class="role-row"><span>🛡️</span><span>Bodyguard</span></div>
            <div class="role-row"><span>👼</span><span>Angelo</span></div>
          </div>

          <button
            v-if="lobbyStore.isHost"
            class="btn-main"
            :disabled="!lobbyStore.allReady || lobbyStore.players.length < MIN_PLAYERS"
            @click="handleStart"
          >
            <span v-if="lobbyStore.players.length < MIN_PLAYERS">Servono {{ MIN_PLAYERS }} giocatori</span>
            <span v-else-if="!lobbyStore.allReady">Aspetta tutti</span>
            <span v-else>🐺 Inizia la partita</span>
          </button>

          <button
            v-else
            class="btn-ready"
            :class="{ active: lobbyStore.currentPlayer?.ready }"
            @click="handleToggleReady"
          >
            {{ lobbyStore.currentPlayer?.ready ? '✓ Pronto — annulla' : 'Sono pronto!' }}
          </button>

          <p class="share-hint">
            Condividi <strong>{{ lobbyStore.lobbyCode }}</strong> con gli amici
          </p>
          <button class="btn-leave" @click="leaveLobby">Abbandona lobby</button>
        </aside>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Lato:wght@300;400;700&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

.lobby-root { min-height: 100vh; background: #07070f; font-family: 'Lato', sans-serif; color: #e8e0d5; }
.lobby-container { max-width: 1200px; margin: 0 auto; padding: 1.5rem 1.5rem 3rem; display: flex; flex-direction: column; gap: 1rem; }

.top-bar { display: flex; align-items: center; justify-content: space-between; padding-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.07); }
.brand { display: flex; align-items: center; gap: 0.7rem; }
.brand-wolf { font-size: 2rem; }
.brand-name { font-family: 'Cinzel', serif; font-size: 1.4rem; font-weight: 900; color: #e8c87a; letter-spacing: 0.1em; line-height: 1; }
.brand-sub  { font-family: 'Cinzel', serif; font-size: 0.6rem; letter-spacing: 0.3em; color: rgba(232,200,122,0.35); text-transform: uppercase; }
.code-area  { text-align: right; }
.code-hint  { font-size: 0.6rem; letter-spacing: 0.2em; text-transform: uppercase; color: rgba(232,200,122,0.35); margin-bottom: 0.25rem; }
.code-btn   { font-family: 'Cinzel', serif; font-size: 1.1rem; font-weight: 700; color: #e8c87a; background: rgba(232,200,122,0.07); border: 1px solid rgba(232,200,122,0.2); border-radius: 6px; padding: 0.35rem 0.9rem; cursor: pointer; letter-spacing: 0.08em; transition: all 0.2s; }
.code-btn:hover  { background: rgba(232,200,122,0.13); }
.code-btn.copied { color: #7ec8a0; border-color: rgba(126,200,160,0.35); }

.banner      { padding: 0.6rem 1rem; border-radius: 8px; font-size: 0.85rem; }
.banner.warn { background: rgba(217,119,6,0.1);  border: 1px solid rgba(217,119,6,0.3);  color: #fbbf24; }
.banner.err  { background: rgba(220,38,38,0.1);  border: 1px solid rgba(220,38,38,0.3);  color: #f87171; }

.main-layout { display: grid; grid-template-columns: 1fr 270px; gap: 1.5rem; align-items: start; }
@media (max-width: 800px) { .main-layout { grid-template-columns: 1fr; } }

.cards-title { font-family: 'Cinzel', serif; font-size: 0.72rem; letter-spacing: 0.2em; text-transform: uppercase; color: rgba(232,200,122,0.45); margin-bottom: 1rem; display: flex; justify-content: space-between; }
.cards-count { color: rgba(232,200,122,0.25); }
.cards-grid  { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 0.9rem; }

.card-empty  { border-radius: 10px; overflow: hidden; opacity: 0.25; }
.card-empty svg { display: block; width: 100%; height: auto; }
.empty-label { padding: 0.5rem; background: rgba(0,0,0,0.4); font-size: 0.72rem; color: rgba(232,224,213,0.2); font-style: italic; text-align: center; }

.side-panel { display: flex; flex-direction: column; gap: 1rem; }

.roles-box { background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 1rem; }
.panel-title { font-family: 'Cinzel', serif; font-size: 0.68rem; letter-spacing: 0.2em; text-transform: uppercase; color: rgba(232,200,122,0.4); margin-bottom: 0.65rem; }
.role-row { display: flex; align-items: center; gap: 0.55rem; font-size: 0.82rem; padding: 0.28rem 0; border-bottom: 1px solid rgba(255,255,255,0.04); color: rgba(232,224,213,0.55); }
.role-row:last-child { border-bottom: none; }

.progress-wrap { height: 3px; background: rgba(255,255,255,0.06); border-radius: 2px; overflow: hidden; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #7c0000, #e8c87a); border-radius: 2px; transition: width 0.5s ease; }

.btn-main, .btn-ready, .btn-leave { width: 100%; padding: 0.8rem 1rem; border-radius: 10px; border: none; font-family: 'Cinzel', serif; font-size: 0.82rem; font-weight: 700; letter-spacing: 0.05em; cursor: pointer; transition: all 0.2s; display: flex; align-items: center; justify-content: center; min-height: 46px; }
.btn-main { background: linear-gradient(135deg, #7c0000, #b91c1c); color: #fff; box-shadow: 0 4px 16px rgba(124,0,0,0.35); }
.btn-main:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 6px 22px rgba(124,0,0,0.55); }
.btn-main:disabled { opacity: 0.3; cursor: not-allowed; background: rgba(255,255,255,0.06); box-shadow: none; color: rgba(232,224,213,0.25); }
.btn-ready { background: rgba(232,200,122,0.07); border: 1px solid rgba(232,200,122,0.2); color: #e8c87a; }
.btn-ready.active { background: rgba(74,222,128,0.07); border-color: rgba(74,222,128,0.25); color: #4ade80; }
.btn-ready:hover { transform: translateY(-1px); }
.btn-leave { background: none; border: 1px solid rgba(248,113,113,0.12); color: rgba(248,113,113,0.35); font-size: 0.72rem; padding: 0.45rem; }
.btn-leave:hover { border-color: rgba(248,113,113,0.3); color: rgba(248,113,113,0.65); }
.share-hint { font-size: 0.72rem; color: rgba(232,224,213,0.28); text-align: center; line-height: 1.5; }
.share-hint strong { color: rgba(232,200,122,0.4); }
</style>
