<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useLobbyStore } from '@/stores/lobbyStore'
import { useClipboard } from '@/composables/useClipboard'

const router = useRouter()
const lobbyStore = useLobbyStore()
const { copied, copy } = useClipboard()

onMounted(() => {
  // Se non c'è lobbyCode (navigazione diretta o refresh) usa il mock
  if (!lobbyStore.lobbyCode) {
    lobbyStore.lobbyCode = 'WOLF-4821'
    lobbyStore.currentPlayerId = 'p1'
    lobbyStore.players = [
      { id: 'p1', name: 'Tu', isHost: true, ready: true },
      { id: 'p2', name: 'Marco', isHost: false, ready: true },
      { id: 'p3', name: 'Sofia', isHost: false, ready: false },
      { id: 'p4', name: 'Luca', isHost: false, ready: false },
    ]
  }
})

function copyCode() { copy(lobbyStore.lobbyCode) }
function handleToggleReady() { lobbyStore.toggleReady() }
function handleKick(id) { lobbyStore.kickPlayer(id) }
function handleStart() { router.push('/game') }
function leaveLobby() { lobbyStore.reset(); router.push('/') }

const MIN_PLAYERS = 5
const MAX_PLAYERS = 12

const cardPalettes = [
  { bg: '#1a0a2e', border: '#7c3aed', glow: 'rgba(124,58,237,0.5)' },
  { bg: '#0a1a0a', border: '#16a34a', glow: 'rgba(22,163,74,0.5)'  },
  { bg: '#1a0a0a', border: '#dc2626', glow: 'rgba(220,38,38,0.5)'  },
  { bg: '#0a0f1a', border: '#2563eb', glow: 'rgba(37,99,235,0.5)'  },
  { bg: '#1a150a', border: '#d97706', glow: 'rgba(217,119,6,0.5)'  },
  { bg: '#0f0a1a', border: '#9333ea', glow: 'rgba(147,51,234,0.5)' },
]
function palette(id) {
  return cardPalettes[id.charCodeAt(id.length - 1) % cardPalettes.length]
}
function initials(name) { return name.slice(0, 2).toUpperCase() }
</script>

<template>
  <div class="lobby-root">
    <div class="bg-stars">
      <span v-for="n in 40" :key="n" class="star" :style="{ '--i': n }"></span>
    </div>

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
            <div
              v-for="player in lobbyStore.players"
              :key="player.id"
              class="card"
              :class="{
                'card--me': player.id === lobbyStore.currentPlayerId,
                'card--ready': player.ready,
              }"
              :style="{
                '--cb': palette(player.id).bg,
                '--cc': palette(player.id).border,
                '--cg': palette(player.id).glow,
              }"
            >
              <!-- SVG illustrazione retro carta -->
              <div class="card-art">
                <svg viewBox="0 0 120 160" xmlns="http://www.w3.org/2000/svg">
                  <rect width="120" height="160" rx="8" :fill="palette(player.id).bg"/>
                  <rect x="6" y="6" width="108" height="148" rx="5"
                    fill="none" :stroke="palette(player.id).border" stroke-width="0.8" opacity="0.4"/>                 
                  <!-- simbolo centrale ? -->
                  <circle cx="60" cy="72" r="26" fill="none"
                    :stroke="palette(player.id).border" stroke-width="1" opacity="0.25"/>
                  <circle cx="60" cy="72" r="18" fill="none"
                    :stroke="palette(player.id).border" stroke-width="0.5" opacity="0.15"/>
                  <text x="60" y="85" text-anchor="middle"
                    font-size="36" font-family="Georgia,serif"
                    :fill="palette(player.id).border" opacity="0.55">?</text>
                  <!-- angoli decorativi -->
                  <text x="11" y="22" font-size="9" font-family="serif"
                    :fill="palette(player.id).border" opacity="0.6">♦</text>
                  <text x="109" y="22" text-anchor="end" font-size="9" font-family="serif"
                    :fill="palette(player.id).border" opacity="0.6">♦</text>
                  <text x="11" y="154" font-size="9" font-family="serif"
                    :fill="palette(player.id).border" opacity="0.6">♦</text>
                  <text x="109" y="154" text-anchor="end" font-size="9" font-family="serif"
                    :fill="palette(player.id).border" opacity="0.6">♦</text>
                  <!-- linee ornamentali -->
                  <line x1="20" y1="28" x2="100" y2="28"
                    :stroke="palette(player.id).border" stroke-width="0.4" opacity="0.25"/>
                  <line x1="20" y1="130" x2="100" y2="130"
                    :stroke="palette(player.id).border" stroke-width="0.4" opacity="0.25"/>
                  <!-- scritta in basso -->
                  <text x="60" y="145" text-anchor="middle"
                    font-size="6.5" font-family="Georgia,serif" letter-spacing="1.5"
                    :fill="palette(player.id).border" opacity="0.45">RUOLO SEGRETO</text>
                </svg>
              </div>

              <!-- Footer carta -->
              <div class="card-footer">
                <div class="card-avatar">{{ initials(player.name) }}</div>
                <div class="card-name">
                  {{ player.name }}
                  <span v-if="player.isHost" class="tag host">👑</span>
                  <span v-if="player.id === lobbyStore.currentPlayerId" class="tag me">tu</span>
                </div>
                <div class="card-status" :class="{ ready: player.ready }">
                  {{ player.ready ? '● pronto' : '○ attesa' }}
                </div>
              </div>

              <button
                v-if="lobbyStore.isHost && player.id !== lobbyStore.currentPlayerId"
                class="kick-btn"
                @click="handleKick(player.id)"
              >✕</button>

              <div v-if="player.ready" class="card-glow-ring"></div>
            </div>

            <!-- Slot vuoti -->
            <div
              v-for="n in MAX_PLAYERS - lobbyStore.players.length"
              :key="'e' + n"
              class="card card--empty"
            >
              <div class="card-art">
                <svg viewBox="0 0 120 160" xmlns="http://www.w3.org/2000/svg">
                  <rect width="120" height="160" rx="8"
                    fill="#0c0c14" stroke="#1e1e2e" stroke-width="1" stroke-dasharray="5 3"/>
                  <text x="60" y="90" text-anchor="middle"
                    font-size="30" fill="#1e1e2e" font-family="sans-serif">+</text>
                </svg>
              </div>
              <div class="card-footer">
                <div class="empty-label">in attesa...</div>
              </div>
            </div>
          </div>
        </section>

        <!-- SIDE PANEL -->
        <aside class="side-panel">
          <div class="info-box">
            <div class="panel-title">Impostazioni</div>
            <div class="info-row"><span>Min giocatori</span><strong>{{ MIN_PLAYERS }}</strong></div>
            <div class="info-row"><span>Max giocatori</span><strong>{{ MAX_PLAYERS }}</strong></div>
            <div class="info-row">
              <span>Pronti</span>
              <strong>{{ lobbyStore.readyCount }} / {{ lobbyStore.players.length }}</strong>
            </div>
          </div>

          <div class="progress-wrap">
            <div class="progress-fill" :style="{
              width: lobbyStore.players.length
                ? (lobbyStore.readyCount / lobbyStore.players.length) * 100 + '%'
                : '0%'
            }"></div>
          </div>

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
            <span v-if="lobbyStore.players.length < MIN_PLAYERS">
              Servono {{ MIN_PLAYERS }} giocatori
            </span>
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

.lobby-root {
  min-height: 100vh;
  background: #07070f;
  font-family: 'Lato', sans-serif;
  color: #e8e0d5;
  position: relative;
  overflow-x: hidden;
}

.bg-stars { position: fixed; inset: 0; pointer-events: none; z-index: 0; }
.star {
  position: absolute; width: 1.5px; height: 1.5px; border-radius: 50%; background: #fff;
  left: calc(var(--i) * 2.5%); top: calc(var(--i) * 3.1% + 2%);
  animation: tw calc(3s + var(--i) * 0.25s) ease-in-out infinite alternate;
}
@keyframes tw { from { opacity: 0.08; } to { opacity: 0.6; } }

.lobby-container {
  position: relative; z-index: 1;
  max-width: 1200px; margin: 0 auto;
  padding: 1.5rem 1.5rem 3rem;
  display: flex; flex-direction: column; gap: 1rem;
}

.top-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.07);
}
.brand { display: flex; align-items: center; gap: 0.7rem; }
.brand-wolf { font-size: 2rem; }
.brand-name { font-family: 'Cinzel', serif; font-size: 1.4rem; font-weight: 900; color: #e8c87a; letter-spacing: 0.1em; line-height: 1; }
.brand-sub { font-family: 'Cinzel', serif; font-size: 0.6rem; letter-spacing: 0.3em; color: rgba(232,200,122,0.35); text-transform: uppercase; }

.code-area { text-align: right; }
.code-hint { font-size: 0.6rem; letter-spacing: 0.2em; text-transform: uppercase; color: rgba(232,200,122,0.35); margin-bottom: 0.25rem; }
.code-btn { font-family: 'Cinzel', serif; font-size: 1.1rem; font-weight: 700; color: #e8c87a; background: rgba(232,200,122,0.07); border: 1px solid rgba(232,200,122,0.2); border-radius: 6px; padding: 0.35rem 0.9rem; cursor: pointer; letter-spacing: 0.08em; transition: all 0.2s; }
.code-btn:hover { background: rgba(232,200,122,0.13); }
.code-btn.copied { color: #7ec8a0; border-color: rgba(126,200,160,0.35); }

.banner { padding: 0.6rem 1rem; border-radius: 8px; font-size: 0.85rem; }
.banner.warn { background: rgba(217,119,6,0.1); border: 1px solid rgba(217,119,6,0.3); color: #fbbf24; }
.banner.err { background: rgba(220,38,38,0.1); border: 1px solid rgba(220,38,38,0.3); color: #f87171; }

.main-layout { display: grid; grid-template-columns: 1fr 270px; gap: 1.5rem; align-items: start; }
@media (max-width: 800px) { .main-layout { grid-template-columns: 1fr; } }

.cards-title { font-family: 'Cinzel', serif; font-size: 0.72rem; letter-spacing: 0.2em; text-transform: uppercase; color: rgba(232,200,122,0.45); margin-bottom: 1rem; display: flex; justify-content: space-between; }
.cards-count { color: rgba(232,200,122,0.25); }

.cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 0.9rem; }

.card {
  position: relative; border-radius: 10px;
  background: var(--cb, #0d0d14); border: 1px solid var(--cc, #1e1e2e);
  overflow: hidden; transition: transform 0.25s ease, box-shadow 0.25s ease;
}
.card:not(.card--empty):hover { transform: translateY(-6px) rotate(-0.8deg); box-shadow: 0 16px 32px rgba(0,0,0,0.6), 0 0 20px var(--cg, transparent); }
.card--me { border-color: #e8c87a !important; box-shadow: 0 0 14px rgba(232,200,122,0.2); }
.card--empty { opacity: 0.25; cursor: default; }

.card-art { width: 100%; }
.card-art svg { display: block; width: 100%; height: auto; }

.card-footer { padding: 0.5rem 0.4rem 0.65rem; background: rgba(0,0,0,0.45); display: flex; flex-direction: column; align-items: center; gap: 0.2rem; }
.card-avatar { width: 26px; height: 26px; border-radius: 50%; background: rgba(232,200,122,0.12); border: 1px solid rgba(232,200,122,0.25); display: flex; align-items: center; justify-content: center; font-family: 'Cinzel', serif; font-size: 0.6rem; font-weight: 700; color: #e8c87a; }
.card-name { font-size: 0.78rem; font-weight: 700; color: #e8e0d5; display: flex; align-items: center; gap: 0.25rem; flex-wrap: wrap; justify-content: center; text-align: center; }
.card-status { font-size: 0.65rem; color: rgba(232,224,213,0.35); letter-spacing: 0.03em; }
.card-status.ready { color: #4ade80; }
.empty-label { font-size: 0.72rem; color: rgba(232,224,213,0.2); font-style: italic; }

.tag { font-size: 0.58rem; padding: 0.05rem 0.3rem; border-radius: 8px; }
.tag.host { background: rgba(232,200,122,0.12); color: #e8c87a; }
.tag.me { background: rgba(100,180,255,0.1); color: #90c8ff; }

.card-glow-ring { position: absolute; inset: 0; border-radius: 10px; box-shadow: inset 0 0 0 1px var(--cc); pointer-events: none; animation: gp 2s ease-in-out infinite alternate; }
@keyframes gp { from { opacity: 0.3; } to { opacity: 1; } }

.kick-btn { position: absolute; top: 5px; right: 5px; background: rgba(220,38,38,0.12); border: 1px solid rgba(220,38,38,0.25); color: #f87171; width: 18px; height: 18px; border-radius: 50%; font-size: 0.6rem; cursor: pointer; display: flex; align-items: center; justify-content: center; opacity: 0; transition: opacity 0.2s; }
.card:hover .kick-btn { opacity: 1; }

.side-panel { display: flex; flex-direction: column; gap: 1rem; }

.info-box, .roles-box { background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 1rem; }
.panel-title { font-family: 'Cinzel', serif; font-size: 0.68rem; letter-spacing: 0.2em; text-transform: uppercase; color: rgba(232,200,122,0.4); margin-bottom: 0.65rem; }
.info-row { display: flex; justify-content: space-between; font-size: 0.82rem; padding: 0.28rem 0; border-bottom: 1px solid rgba(255,255,255,0.04); color: rgba(232,224,213,0.55); }
.info-row:last-child { border-bottom: none; }
.info-row strong { color: #e8e0d5; }
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
