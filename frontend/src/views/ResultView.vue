<script setup>
/**
 * ResultsView.vue
 *
 * Schermata di fine partita. Si compone di 3 momenti visivi in sequenza:
 *   Momento 1 (subito) → Annuncio vincitore
 *   Momento 2 (2.8s)   → Rivelazione carte
 *   Momento 3 (5.5s)   → Statistiche + squadre
 *   Azioni    (6.5s)   → Pulsanti finali
 *
 * IMPORTANTE: il risultato (vincitore, giocatori, round) viene CONGELATO
 * al mount in ref locali. Così, quando l'host riavvia la stanza e il backend
 * resetta lo store (phase=LOBBY, winner=null), questa schermata NON cambia
 * scenario: continua a mostrare il risultato reale finché non si naviga via.
 */

import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useGameStore, ROLES, WINNERS, PHASES } from '@/stores/gameStore'
import { useLobbyStore } from '@/stores/lobbyStore'
import { useSocket } from '@/composables/useSocket'

const router = useRouter()
const game   = useGameStore()
const lobby  = useLobbyStore()
const { emit, disconnect } = useSocket()

// ---------------------------------------------------------------------------
// STATO SEQUENZA ANIMAZIONI
// ---------------------------------------------------------------------------
const showCards   = ref(false)
const showStats   = ref(false)
const showActions = ref(false)

// ---------------------------------------------------------------------------
// RISULTATO CONGELATO (snapshot al mount)
// Questi ref vengono riempiti una volta sola e NON dipendono più dallo store.
// ---------------------------------------------------------------------------
const wolvesWon     = ref(false)
const finalPlayers  = ref([])
const finalRound    = ref(0)

onMounted(() => {
  // -------------------------------------------------------------------------
  // MOCK TEMPORANEO (solo sviluppo senza backend)
  // Da rimuovere quando GameView naviga qui dopo aver ricevuto game_ended.
  // -------------------------------------------------------------------------
  if (!game.currentPlayerId) {
    game.winner = WINNERS.WOLVES
    game.round  = 4
    game.players = [
      { player_id: 'p1', username: 'Tu',     role: ROLES.VILLAGER, alive: false },
      { player_id: 'p2', username: 'Marco',  role: ROLES.WOLF,     alive: true  },
      { player_id: 'p3', username: 'Sofia',  role: ROLES.SEER,     alive: false },
      { player_id: 'p4', username: 'Luca',   role: ROLES.WOLF,     alive: true  },
      { player_id: 'p5', username: 'Anna',   role: ROLES.VILLAGER, alive: false },
      { player_id: 'p6', username: 'Giulia', role: ROLES.VILLAGER, alive: false },
    ]
  }

  // 🔒 CONGELIAMO il risultato adesso, prima di qualunque reset dello store
  wolvesWon.value    = game.winner === WINNERS.WOLVES
  finalPlayers.value = [...game.players]
  finalRound.value   = game.round

  // Sequenza animazioni
  setTimeout(() => { showCards.value   = true }, 2800)
  setTimeout(() => { showStats.value   = true }, 5500)
  setTimeout(() => { showActions.value = true }, 6500)
})

// ---------------------------------------------------------------------------
// Quando l'host riavvia la stanza, il backend riporta la fase a LOBBY:
// gli altri giocatori escono dai risultati invece di vedere lo scenario cambiare.
// ---------------------------------------------------------------------------
watch(
  () => game.phase,
  (newPhase) => {
    if (newPhase === PHASES.LOBBY || newPhase === 'LOBBY') {
      router.push('/lobby')
    }
  }
)

// ---------------------------------------------------------------------------
// SQUADRE E STATISTICHE — calcolate sui dati CONGELATI
// ---------------------------------------------------------------------------
const wolves    = computed(() => finalPlayers.value.filter(p => p.role === ROLES.WOLF))
const villagers = computed(() => finalPlayers.value.filter(p => p.role === ROLES.VILLAGER))
const seers     = computed(() => finalPlayers.value.filter(p => p.role === ROLES.SEER))

const survivorsCount  = computed(() => finalPlayers.value.filter(p => p.alive).length)
const eliminatedCount = computed(() => finalPlayers.value.filter(p => !p.alive).length)

// ---------------------------------------------------------------------------
// PALETTE CARTE PER RUOLO
// ---------------------------------------------------------------------------
const rolePalette = {
  [ROLES.WOLF]:     { bg: '#1a0505', border: '#dc2626', glow: 'rgba(220,38,38,0.6)'  },
  [ROLES.SEER]:     { bg: '#0f0a1a', border: '#7c3aed', glow: 'rgba(124,58,237,0.6)' },
  [ROLES.VILLAGER]: { bg: '#0a1205', border: '#16a34a', glow: 'rgba(22,163,74,0.6)'  },
}
function palette(role) {
  return rolePalette[role] ?? rolePalette[ROLES.VILLAGER]
}

// ---------------------------------------------------------------------------
// AZIONI FINALI
// ---------------------------------------------------------------------------
function playAgain() {
  if (lobby.isHost) {
    console.log('[ResultsView] L\'host richiede il riavvio della stanza')
    emit('return_to_lobby', { room_id: lobby.lobbyCode })
  }
  router.push('/lobby')
}

function goHome() {
  console.log('[ResultsView] Il giocatore abbandona la partita')
  game.reset()
  lobby.reset()
  disconnect()
  router.push('/')
}

// ---------------------------------------------------------------------------
// ICONE E NOMI DEI RUOLI
// ---------------------------------------------------------------------------
const roleIcon = {
  [ROLES.WOLF]:     '🐺',
  [ROLES.SEER]:     '🔮',
  [ROLES.VILLAGER]: '🧑‍🌾',
}
const roleName = {
  [ROLES.WOLF]:     'Lupo',
  [ROLES.SEER]:     'Veggente',
  [ROLES.VILLAGER]: 'Contadino',
}
</script>

<template>
  <div class="results-root" :class="wolvesWon ? 'theme--wolves' : 'theme--village'">

    <div class="particles">
      <span v-for="n in 25" :key="n" class="particle" :style="{ '--i': n }"></span>
    </div>

    <div class="results-wrap">

      <section class="moment-announce">

        <div class="announce-icon">{{ wolvesWon ? '🐺' : '🌅' }}</div>

        <div class="announce-label">
          {{ wolvesWon ? 'I Lupi hanno vinto!' : 'Il Villaggio ha vinto!' }}
        </div>

        <div class="announce-sub">
          {{ wolvesWon
            ? 'Il buio ha inghiottito il villaggio...'
            : 'La luce ha trionfato sull\'oscurità!' }}
        </div>

        <div class="announce-rounds">Partita durata {{ finalRound }} round</div>
      </section>

      <Transition name="fade-up">
        <section v-if="showCards" class="moment-cards">
          <div class="section-title">Identità rivelate</div>

          <div class="cards-grid">
            <div
              v-for="(player, idx) in finalPlayers"
              :key="player.player_id"
              class="result-card"
              :class="{ 'result-card--dead': !player.alive }"
              :style="{
                '--cb':    palette(player.role).bg,
                '--cc':    palette(player.role).border,
                '--cg':    palette(player.role).glow,
                '--delay': `${idx * 0.15}s`,
              }"
            >
              <div class="result-card__art">
                <svg viewBox="0 0 120 160" xmlns="http://www.w3.org/2000/svg">
                  <rect width="120" height="160" rx="8" :fill="palette(player.role).bg"/>
                  <rect x="6" y="6" width="108" height="148" rx="5"
                    fill="none" :stroke="palette(player.role).border"
                    stroke-width="0.8" opacity="0.5"/>

                  <text x="60" y="82" text-anchor="middle"
                    font-size="42" :fill="palette(player.role).border" opacity="0.9">
                    {{ roleIcon[player.role] ?? '?' }}
                  </text>

                  <text x="60" y="142" text-anchor="middle"
                    font-size="8" font-family="Georgia,serif" letter-spacing="1.5"
                    :fill="palette(player.role).border" opacity="0.6">
                    {{ roleName[player.role]?.toUpperCase() ?? '?' }}
                  </text>

                  <text x="11"  y="22"  font-size="9" font-family="serif" :fill="palette(player.role).border" opacity="0.5">♦</text>
                  <text x="109" y="22"  font-size="9" font-family="serif" :fill="palette(player.role).border" opacity="0.5" text-anchor="end">♦</text>
                  <text x="11"  y="154" font-size="9" font-family="serif" :fill="palette(player.role).border" opacity="0.5">♦</text>
                  <text x="109" y="154" font-size="9" font-family="serif" :fill="palette(player.role).border" opacity="0.5" text-anchor="end">♦</text>

                  <line x1="20" y1="28"  x2="100" y2="28"  :stroke="palette(player.role).border" stroke-width="0.4" opacity="0.2"/>
                  <line x1="20" y1="128" x2="100" y2="128" :stroke="palette(player.role).border" stroke-width="0.4" opacity="0.2"/>

                  <rect v-if="!player.alive" width="120" height="160" rx="8" fill="rgba(0,0,0,0.6)"/>
                  <text v-if="!player.alive" x="60" y="90" text-anchor="middle"
                    font-size="28" fill="rgba(255,255,255,0.2)">💀</text>
                </svg>
              </div>

              <div class="result-card__footer">
                <div class="result-card__name">{{ player.username }}</div>
                <div class="result-card__status" :class="player.alive ? 'alive' : 'dead'">
                  {{ player.alive ? '✓ sopravvissuto' : '✗ eliminato' }}
                </div>
              </div>

              <div
                v-if="(wolvesWon && player.role === ROLES.WOLF) ||
                      (!wolvesWon && player.role !== ROLES.WOLF)"
                class="result-card__glow"
              ></div>
            </div>
          </div>
        </section>
      </Transition>

      <Transition name="fade-up">
        <section v-if="showStats" class="moment-stats">
          <div class="section-title">Riepilogo partita</div>

          <div class="stats-grid">
            <div class="stat-card">
              <div class="stat-value">{{ finalRound }}</div>
              <div class="stat-label">Round giocati</div>
            </div>
            <div class="stat-card">
              <div class="stat-value">{{ survivorsCount }}</div>
              <div class="stat-label">Sopravvissuti</div>
            </div>
            <div class="stat-card">
              <div class="stat-value">{{ eliminatedCount }}</div>
              <div class="stat-label">Eliminati</div>
            </div>
            <div class="stat-card">
              <div class="stat-value">{{ wolves.length }}</div>
              <div class="stat-label">Lupi in gioco</div>
            </div>
          </div>

          <div class="teams-wrap">
            <div class="team team--wolves" :class="{ 'team--winner': wolvesWon }">
              <div class="team-title">🐺 Lupi</div>
              <div v-for="p in wolves" :key="p.player_id" class="team-player">
                <span>{{ p.username }}</span>
                <span class="team-player-status">{{ p.alive ? '✓' : '💀' }}</span>
              </div>
            </div>

            <div class="team-divider">VS</div>

            <div class="team team--village" :class="{ 'team--winner': !wolvesWon }">
              <div class="team-title">🧑‍🌾 Villaggio</div>
              <div
                v-for="p in [...villagers, ...seers]"
                :key="p.player_id"
                class="team-player"
              >
                <span>{{ p.username }} {{ p.role === ROLES.SEER ? '🔮' : '' }}</span>
                <span class="team-player-status">{{ p.alive ? '✓' : '💀' }}</span>
              </div>
            </div>
          </div>
        </section>
      </Transition>

      <Transition name="fade-up">
        <div v-if="showActions" class="final-actions">
          <button v-if="lobby.isHost" class="btn-play-again" @click="playAgain">
            Gioca ancora (Riavvia Stanza)
          </button>

          <button v-else class="btn-play-again" @click="playAgain">
            Torna in Lobby
          </button>

          <button class="btn-home" @click="goHome">
            Torna alla Home
          </button>
        </div>
      </Transition>

    </div>
  </div>
</template>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Crimson+Text:ital,wght@0,400;0,600;1,400&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

/* ---- ROOT & TEMI ---- */
.results-root {
  min-height: 100vh;
  font-family: 'Crimson Text', serif;
  color: #e8e0d5;
  position: relative;
  overflow-x: hidden;
  transition: background 2s ease;
}
.theme--wolves {
  background:
    linear-gradient(rgba(7,1,10,0.35), rgba(7,1,10,0.55)),
    radial-gradient(ellipse at 50% 0%, rgba(139,0,0,0.35) 0%, rgba(7,1,10,0) 55%),
    url('../assets/wolf_result1.png');
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  background-attachment: fixed;
}
.theme--village {
  background:
    linear-gradient(rgba(1,10,7,0.32), rgba(1,10,7,0.52)),
    radial-gradient(ellipse at 50% 0%, rgba(22,101,52,0.30) 0%, rgba(1,10,7,0) 55%),
    url('../assets/farmer_result1.png');
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  background-attachment: fixed;
}
/* ---- PARTICELLE STELLATE ---- */
.particles { position: fixed; inset: 0; pointer-events: none; z-index: 0; }
.particle {
  position: absolute;
  width: 2px; height: 2px; border-radius: 50%; background: #fff;
  left: calc(var(--i) * 3.8%);
  top:  calc(var(--i) * 3.5% + 5%);
  animation: twinkle calc(3s + var(--i) * 0.3s) ease-in-out infinite alternate;
}
@keyframes twinkle { from { opacity: 0.05; } to { opacity: 0.5; } }

/* ---- CONTENITORE PRINCIPALE ---- */
.results-wrap {
  position: relative; z-index: 1;
  max-width: 900px; margin: 0 auto;
  padding: 3rem 1.5rem 4rem;
  display: flex; flex-direction: column; gap: 3.5rem;
}

/* ---- MOMENTO 1 — ANNUNCIO ---- */
.moment-announce {
  text-align: center;
  animation: heroIn 1.2s cubic-bezier(0.22, 1, 0.36, 1) both;
}
@keyframes heroIn {
  from { opacity: 0; transform: scale(0.85) translateY(30px); }
  to   { opacity: 1; transform: scale(1)    translateY(0);     }
}
.announce-icon {
  font-size: 6rem; display: block; margin-bottom: 1rem;
  animation: iconPulse 3s ease-in-out infinite alternate;
}
@keyframes iconPulse {
  from { transform: scale(1);    filter: drop-shadow(0 0 20px rgba(255,255,255,0.2)); }
  to   { transform: scale(1.06); filter: drop-shadow(0 0 40px rgba(255,255,255,0.4)); }
}
.announce-label {
  font-family: 'Cinzel', serif;
  font-size: clamp(1.8rem, 5vw, 3rem);
  font-weight: 900; letter-spacing: 0.08em; line-height: 1.1; margin-bottom: 0.7rem;
}
.theme--wolves  .announce-label { color: #fca5a5; text-shadow: 0 0 40px rgba(220,38,38,0.5); }
.theme--village .announce-label { color: #86efac; text-shadow: 0 0 40px rgba(22,163,74,0.5);  }
.announce-sub     { font-size: 1.2rem; font-style: italic; color: rgba(232,224,213,0.5); margin-bottom: 0.5rem; }
.announce-rounds  { font-family: 'Cinzel', serif; font-size: 0.75rem; letter-spacing: 0.2em; text-transform: uppercase; color: rgba(232,224,213,0.25); }

/* ---- TITOLO SEZIONE ---- */
.section-title { font-family: 'Cinzel', serif; font-size: 0.75rem; letter-spacing: 0.25em; text-transform: uppercase; color: rgba(232,200,122,0.4); margin-bottom: 1.5rem; text-align: center; }

/* ---- MOMENTO 2 — GRIGLIA CARTE ---- */
.cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 1rem; }

.result-card {
  position: relative; border-radius: 10px;
  background: var(--cb, #0d0d14); border: 1px solid var(--cc, #1e1e2e);
  overflow: hidden;
  animation: cardReveal 0.6s cubic-bezier(0.34, 1.56, 0.64, 1) both;
  animation-delay: var(--delay, 0s);
  transition: transform 0.25s, box-shadow 0.25s;
}
@keyframes cardReveal {
  from { opacity: 0; transform: rotateY(90deg) scale(0.8); }
  to   { opacity: 1; transform: rotateY(0deg)  scale(1);   }
}
.result-card:hover:not(.result-card--dead) {
  transform: translateY(-5px);
  box-shadow: 0 12px 28px rgba(0,0,0,0.5), 0 0 16px var(--cg);
}
.result-card--dead { opacity: 0.5; }
.result-card__art svg { display: block; width: 100%; height: auto; }
.result-card__footer  { padding: 0.45rem 0.4rem 0.6rem; background: rgba(0,0,0,0.45); text-align: center; }
.result-card__name    { font-size: 0.78rem; font-weight: 600; color: #e8e0d5; margin-bottom: 0.15rem; }
.result-card__status  { font-size: 0.62rem; letter-spacing: 0.05em; }
.result-card__status.alive { color: #4ade80; }
.result-card__status.dead  { color: rgba(248,113,113,0.5); }
.result-card__glow { position: absolute; inset: 0; border-radius: 10px; box-shadow: inset 0 0 0 1px var(--cc); pointer-events: none; animation: glowPulse 2s ease-in-out infinite alternate; }
@keyframes glowPulse { from { opacity: 0.3; } to { opacity: 1; } }

/* ---- MOMENTO 3 — STATISTICHE ---- */
.stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.8rem; margin-bottom: 2rem; }
@media (max-width: 600px) { .stats-grid { grid-template-columns: repeat(2, 1fr); } }
.stat-card   { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.07); border-radius: 12px; padding: 1rem 0.5rem; text-align: center; }
.stat-value  { font-family: 'Cinzel', serif; font-size: 2rem; font-weight: 900; color: #e8c87a; line-height: 1; margin-bottom: 0.35rem; }
.stat-label  { font-size: 0.72rem; color: rgba(232,224,213,0.35); letter-spacing: 0.08em; text-transform: uppercase; }

/* Squadre affiancate */
.teams-wrap  { display: grid; grid-template-columns: 1fr auto 1fr; gap: 1rem; align-items: start; }
@media (max-width: 600px) { .teams-wrap { grid-template-columns: 1fr; } .team-divider { text-align: center; } }
.team        { background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 1rem; transition: border-color 0.5s, box-shadow 0.5s; }
.team--winner { border-color: rgba(232,200,122,0.3); box-shadow: 0 0 20px rgba(232,200,122,0.08); }
.team-title   { font-family: 'Cinzel', serif; font-size: 0.8rem; font-weight: 700; letter-spacing: 0.1em; color: rgba(232,200,122,0.6); margin-bottom: 0.7rem; text-transform: uppercase; }
.team-player  { display: flex; justify-content: space-between; align-items: center; font-size: 0.9rem; padding: 0.3rem 0; border-bottom: 1px solid rgba(255,255,255,0.04); color: rgba(232,224,213,0.7); }
.team-player:last-child { border-bottom: none; }
.team-player-status { font-size: 0.8rem; color: rgba(232,224,213,0.3); }
.team-divider { font-family: 'Cinzel', serif; font-size: 0.7rem; font-weight: 900; letter-spacing: 0.15em; color: rgba(232,224,213,0.15); padding-top: 1.2rem; align-self: center; }

/* ---- AZIONI FINALI ---- */
.final-actions   { display: flex; gap: 1rem; justify-content: center; flex-wrap: wrap; }
.btn-play-again, .btn-home { padding: 0.9rem 2rem; border-radius: 12px; border: none; font-family: 'Cinzel', serif; font-size: 0.9rem; font-weight: 700; letter-spacing: 0.06em; cursor: pointer; transition: all 0.25s; }
.btn-play-again  { background: linear-gradient(135deg, #7c0000, #b91c1c); color: #fff; box-shadow: 0 4px 20px rgba(124,0,0,0.4); }
.btn-play-again:hover { transform: translateY(-2px); box-shadow: 0 8px 28px rgba(124,0,0,0.6); }
.btn-home        { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); color: rgba(232,224,213,0.6); }
.btn-home:hover  { background: rgba(255,255,255,0.08); color: #e8e0d5; transform: translateY(-1px); }

/* ---- TRANSIZIONE FADE-UP ---- */
.fade-up-enter-active { animation: fadeUpIn 0.7s cubic-bezier(0.22, 1, 0.36, 1) both; }
@keyframes fadeUpIn {
  from { opacity: 0; transform: translateY(30px); }
  to   { opacity: 1; transform: translateY(0);     }
}
</style>