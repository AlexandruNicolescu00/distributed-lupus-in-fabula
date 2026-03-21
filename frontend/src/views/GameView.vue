<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useGameStore, PHASES, ROLES, WINNERS } from '@/stores/gameStore'
import { useLobbyStore } from '@/stores/lobbyStore'
import { useChatStore } from '@/stores/chatStore'
import ChatBox from '@/components/ChatBox.vue'
import PhaseTimer from '@/components/PhaseTimer.vue'


const router  = useRouter()
const game    = useGameStore()
const lobby   = useLobbyStore()
const chat    = useChatStore()

// ---- UI STATE ----
const showRoleBanner   = ref(false)
const chatInput        = ref('')
const myVote           = ref(null)
const nightActionDone  = ref(false)
const showNightOverlay = ref(false)

// ---- MOCK per sviluppo senza backend ----
onMounted(() => {
  if (!game.currentPlayerId) {
    // MOCK TEMPORANEO — rimuovere quando il backend è pronto
    game.currentPlayerId = 'p1'
    game.myRole          = ROLES.VILLAGER
    game.phase           = PHASES.DAY
    game.round           = 1
    game.players = [
      { player_id: 'p1', username: 'Tu',    role: null, alive: true  },
      { player_id: 'p2', username: 'Marco', role: null, alive: true  },
      { player_id: 'p3', username: 'Sofia', role: null, alive: true  },
      { player_id: 'p4', username: 'Luca',  role: null, alive: true  },
      { player_id: 'p5', username: 'Anna',  role: null, alive: false },
    ]
    chat.messages = [
      { id: 1, senderName: 'Marco', text: 'Buongiorno a tutti!', channel: 'global', timestamp: new Date().toISOString() },
      { id: 2, senderName: 'Sofia', text: 'Chi sospettate?',     channel: 'global', timestamp: new Date().toISOString() },
    ]
    // Simula timer_end tra 60 secondi
    game.timerEnd = Date.now() / 1000 + 60
  } else {
    game.listenToGameEvents()
    chat.listenToMessages()
  }

  showRoleBanner.value = true
  setTimeout(() => (showRoleBanner.value = false), 3500)
})

onUnmounted(() => { chat.reset() })

// Overlay notte + reset stato locale quando cambia fase
watch(() => game.phase, (newPhase) => {
  showNightOverlay.value = newPhase === PHASES.NIGHT
  myVote.value           = null
  nightActionDone.value  = false
})

// ---- COMPUTED ----
const phaseLabel = computed(() => {
  const map = {
    [PHASES.DAY]:    '☀️  Giorno',
    [PHASES.VOTING]: '🗳️  Votazione',
    [PHASES.NIGHT]:  '🌙  Notte',
    [PHASES.ENDED]:  '🏁  Fine Partita',
    [PHASES.LOBBY]:  '⏳  Lobby',
  }
  return map[game.phase] ?? game.phase
})

const phaseColor = computed(() => {
  const map = {
    [PHASES.DAY]:    '#e8c87a',
    [PHASES.VOTING]: '#f87171',
    [PHASES.NIGHT]:  '#818cf8',
    [PHASES.ENDED]:  '#4ade80',
  }
  return map[game.phase] ?? '#e8e0d5'
})

const roleLabel = computed(() => {
  const map = {
    [ROLES.VILLAGER]: { icon: '🧑‍🌾', name: 'Contadino', desc: 'Trova i lupi e sopravvivi!' },
    [ROLES.WOLF]:     { icon: '🐺',   name: 'Lupo',      desc: 'Elimina i villagers di notte.' },
    [ROLES.SEER]:     { icon: '🔮',   name: 'Veggente',  desc: 'Ogni notte scopri il ruolo di un giocatore.' },
  }
  return map[game.myRole] ?? { icon: '?', name: 'Sconosciuto', desc: '' }
})

const canVote   = computed(() => game.phase === PHASES.VOTING && game.isAlive && !myVote.value)
const canAct    = computed(() => game.phase === PHASES.NIGHT  && game.isAlive && (game.isWolf || game.isSeer) && !nightActionDone.value)
const canChat   = computed(() => game.isAlive && [PHASES.DAY, PHASES.VOTING].includes(game.phase))

// Messaggi visibili in base a fase e ruolo
const visibleMessages = computed(() =>
  chat.messages.filter((m) =>
    m.channel === 'global' || (m.channel === 'wolves' && game.isWolf)
  )
)

// Formattazione timer da secondsLeft
const formattedTimer = computed(() => {
  const s = game.secondsLeft ?? 0
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
})

// ---- AZIONI ----
function castVote(targetId) {
  if (!canVote.value || targetId === game.currentPlayerId) return
  myVote.value = targetId
  game.vote(lobby.lobbyCode, targetId)
}

function castNightAction(targetId) {
  if (!canAct.value) return
  nightActionDone.value = true
  if (game.isWolf) {
    game.wolfVote(targetId)
  } else if (game.isSeer) {
    game.seerAction(targetId)
  }
}

function sendChat() {
  if (!chatInput.value.trim() || !canChat.value) return
  chat.sendMessage(chatInput.value.trim())
  // Mock locale — rimuovere col backend
  chat.messages.push({
    id: Date.now(),
    senderName: 'Tu',
    text: chatInput.value.trim(),
    channel: 'global',
    timestamp: new Date().toISOString(),
  })
  chatInput.value = ''
}

// Mock cambio fase — rimuovere col backend
function mockPhase(p) {
  game.phase     = p
  game.timerEnd  = Date.now() / 1000 + 60
}

// Helpers display
function initials(name) { return (name ?? '?').slice(0, 2).toUpperCase() }
const avatarColors = ['#7c3aed','#16a34a','#dc2626','#2563eb','#d97706','#9333ea']
function avatarColor(id) { return avatarColors[(id ?? '').charCodeAt((id ?? 'x').length - 1) % avatarColors.length] }

// Label ruolo rivelato (per eliminazioni)
function roleRevealLabel(role) {
  const map = { VILLAGER: '🧑‍🌾', WOLF: '🐺', SEER: '🔮' }
  return map[role] ?? '?'
}
</script>

<template>
  <div class="game-root" :class="`phase--${game.phase.toLowerCase()}`">

    <!-- OVERLAY NOTTE -->
    <Transition name="night">
      <div v-if="showNightOverlay" class="night-overlay">
        <div class="night-content">
          <div class="night-moon">🌙</div>
          <div class="night-title">È calata la notte</div>
          <div class="night-sub" v-if="game.isWolf">Scegli chi eliminare...</div>
          <div class="night-sub" v-else-if="game.isSeer">Scegli chi investigare...</div>
          <div class="night-sub" v-else>Chiudi gli occhi e aspetta...</div>
          <!-- Mostra i compagni lupo -->
          <div v-if="game.isWolf && game.wolfCompanions.length" class="wolf-companions">
            <span class="wc-label">Compagni lupo:</span>
            <span v-for="wc in game.wolfCompanions" :key="wc.player_id" class="wc-name">
              🐺 {{ wc.username }}
            </span>
          </div>
        </div>
      </div>
    </Transition>

    <!-- BANNER RUOLO -->
    <Transition name="banner">
      <div v-if="showRoleBanner" class="role-banner">
        <span class="role-banner-icon">{{ roleLabel.icon }}</span>
        <div>
          <div class="role-banner-name">Sei il {{ roleLabel.name }}</div>
          <div class="role-banner-desc">{{ roleLabel.desc }}</div>
          <!-- Compagni lupo nel banner -->
          <div v-if="game.isWolf && game.wolfCompanions.length" class="role-banner-companions">
            Compagni: {{ game.wolfCompanions.map(w => w.username).join(', ') }}
          </div>
        </div>
      </div>
    </Transition>

    <!-- BANNER PAUSA -->
    <div v-if="game.isPaused" class="pause-banner">
      ⚠️ Partita in pausa — {{ game.pauseReason || 'un giocatore si è disconnesso' }}...
    </div>

    <!-- HEADER -->
    <header class="game-header">
      <div class="brand">🐺 <span>LUPUS</span></div>

      <div class="phase-badge" :style="{ borderColor: phaseColor, color: phaseColor }">
        {{ phaseLabel }}
      </div>

      <PhaseTimer size="md" :color="phaseColor" />

    </header>

    <!-- CORPO -->
    <div class="game-body">

      <!-- COLONNA GIOCATORI -->
      <section class="players-col">
        <div class="col-title">
          Giocatori
          <span class="col-count">{{ game.alivePlayers.length }} vivi</span>
        </div>

        <!-- GIORNO -->
        <div v-if="game.phase === PHASES.DAY" class="players-list">
          <div
            v-for="p in game.alivePlayers" :key="p.player_id"
            class="player-row"
            :class="{ 'is-me': p.player_id === game.currentPlayerId }"
          >
            <div class="p-avatar" :style="{ background: avatarColor(p.player_id) }">
              {{ initials(p.username) }}
            </div>
            <span class="p-name">{{ p.username }}</span>
            <span v-if="p.player_id === game.currentPlayerId" class="p-tag">tu</span>
          </div>
          <div class="dead-divider" v-if="game.deadPlayers.length">Eliminati</div>
          <div v-for="p in game.deadPlayers" :key="p.player_id" class="player-row player-row--dead">
            <div class="p-avatar dead-avatar">{{ initials(p.username) }}</div>
            <span class="p-name">{{ p.username }}</span>
            <span class="p-tag dead-tag">
              {{ p.role ? roleRevealLabel(p.role) : '💀' }}
            </span>
          </div>
        </div>

        <!-- VOTAZIONE -->
        <div v-else-if="game.phase === PHASES.VOTING" class="players-list">
          <div class="vote-hint" v-if="canVote">Clicca su un giocatore per votarlo</div>
          <div class="vote-hint voted" v-else-if="myVote">Hai votato ✓</div>

          <div
            v-for="p in game.alivePlayers.filter(pl => pl.player_id !== game.currentPlayerId)"
            :key="p.player_id"
            class="player-row player-row--vote"
            :class="{ 'is-voted': myVote === p.player_id }"
            @click="castVote(p.player_id)"
          >
            <div class="p-avatar" :style="{ background: avatarColor(p.player_id) }">
              {{ initials(p.username) }}
            </div>
            <span class="p-name">{{ p.username }}</span>
            <!-- vote_counts arriva direttamente dal backend -->
            <div class="vote-dots">
              <span
                v-for="n in (game.voteCounts[p.player_id] ?? 0)"
                :key="n"
                class="vote-dot"
              ></span>
            </div>
            <span v-if="myVote === p.player_id" class="p-tag voted-tag">✓</span>
          </div>

          <!-- No elimination banner -->
          <div v-if="game.noElimination" class="info-pill">
            ⚖️ {{ game.noEliminationReason === 'tie' ? 'Pareggio' : 'Nessun voto' }} — nessuna eliminazione
          </div>
        </div>

        <!-- NOTTE -->
        <div v-else-if="game.phase === PHASES.NIGHT" class="players-list night-list">
          <div v-if="!canAct" class="night-wait">
            <span v-if="nightActionDone">Azione eseguita ✓<br><small>Aspetta gli altri...</small></span>
            <span v-else>Aspetta in silenzio...<br><small>I lupi stanno agendo</small></span>
          </div>
          <template v-else>
            <div class="vote-hint">
              {{ game.isWolf ? '🐺 Chi vuoi eliminare?' : '🔮 Chi vuoi investigare?' }}
            </div>
            <div
              v-for="p in game.alivePlayers.filter(pl => pl.player_id !== game.currentPlayerId)"
              :key="p.player_id"
              class="player-row player-row--vote"
              @click="castNightAction(p.player_id)"
            >
              <div class="p-avatar" :style="{ background: avatarColor(p.player_id) }">
                {{ initials(p.username) }}
              </div>
              <span class="p-name">{{ p.username }}</span>
            </div>
          </template>

          <!-- Risultato veggente — seer_result dal backend -->
          <div v-if="game.seerResult" class="seer-result">
            <span class="seer-icon">🔮</span>
            <span>
              <strong>{{ game.seerResult.targetName }}</strong>
              è {{ game.seerResult.role === ROLES.WOLF ? '🐺 un LUPO!' : '🧑‍🌾 innocente' }}
            </span>
          </div>
        </div>

        <!-- FINE PARTITA — game_ended dal backend -->
        <div v-else-if="game.phase === PHASES.ENDED" class="ended-box">
          <div class="ended-icon">
            {{ game.winner === WINNERS.WOLVES ? '🐺' : '🧑‍🌾' }}
          </div>
          <div class="ended-title">
            {{ game.winner === WINNERS.WOLVES ? 'I Lupi hanno vinto!' : 'Il Villaggio ha vinto!' }}
          </div>
          <!-- Lista finale con ruoli rivelati -->
          <div class="ended-players">
            <div v-for="p in game.players" :key="p.player_id" class="ended-player-row">
              <span class="ended-role">{{ roleRevealLabel(p.role) }}</span>
              <span class="ended-name">{{ p.username }}</span>
              <span class="ended-status">{{ p.alive ? '✓ vivo' : '✗ eliminato' }}</span>
            </div>
          </div>
          <button class="btn-home" @click="router.push('/')">Torna alla Home</button>
        </div>

        <!-- MOCK controlli fase — rimuovere col backend -->
        <div class="mock-controls">
          <div class="mock-label">[ mock fase ]</div>
          <div class="mock-btns">
            <button @click="mockPhase(PHASES.DAY)">Giorno</button>
            <button @click="mockPhase(PHASES.VOTING)">Voto</button>
            <button @click="mockPhase(PHASES.NIGHT)">Notte</button>
            <button @click="mockPhase(PHASES.ENDED)">Fine</button>
          </div>
        </div>
      </section>

      <!-- CHAT -->
      <section class="chat-col">
        <ChatBox />
      </section>

      <!-- SIDEBAR RUOLO -->
      <aside class="role-col">
        <div class="col-title">Il tuo ruolo</div>
        <div class="role-card">
          <div class="role-card-icon">{{ roleLabel.icon }}</div>
          <div class="role-card-name">{{ roleLabel.name }}</div>
          <div class="role-card-desc">{{ roleLabel.desc }}</div>
          <!-- Compagni lupo nella sidebar -->
          <div v-if="game.isWolf && game.wolfCompanions.length" class="wolf-list">
            <div class="wolf-list-title">Compagni lupo</div>
            <div v-for="wc in game.wolfCompanions" :key="wc.player_id" class="wolf-item">
              🐺 {{ wc.username }}
            </div>
          </div>
        </div>

        <div class="round-box">
          <span class="round-label">Round</span>
          <span class="round-num">{{ game.round }}</span>
        </div>

        <div class="count-box">
          <div class="count-row">
            <span>Vivi</span>
            <strong>{{ game.alivePlayers.length }}</strong>
          </div>
          <div class="count-row">
            <span>Eliminati</span>
            <strong>{{ game.deadPlayers.length }}</strong>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Lato:wght@300;400;700&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

.game-root {
  min-height: 100vh; background: #07070f;
  font-family: 'Lato', sans-serif; color: #e8e0d5;
  display: flex; flex-direction: column;
  transition: background 1s ease;
}
.phase--night  { background: #03030a; }
.phase--voting { background: #0f0707; }
.phase--day    { background: #07070f; }
.phase--ended  { background: #040a04; }

/* OVERLAY NOTTE */
.night-overlay { position: fixed; inset: 0; z-index: 100; background: rgba(2,2,15,0.96); display: flex; align-items: center; justify-content: center; }
.night-content { text-align: center; }
.night-moon { font-size: 5rem; animation: moonRise 1s ease both; }
@keyframes moonRise { from { opacity:0; transform: translateY(30px); } to { opacity:1; transform:translateY(0); } }
.night-title { font-family:'Cinzel',serif; font-size:2rem; font-weight:900; color:#818cf8; margin-top:1rem; letter-spacing:0.1em; }
.night-sub { font-size:1rem; color:rgba(232,224,213,0.45); margin-top:0.5rem; font-style:italic; }
.wolf-companions { margin-top:1rem; display:flex; gap:0.5rem; flex-wrap:wrap; justify-content:center; }
.wc-label { font-size:0.75rem; color:rgba(232,224,213,0.4); }
.wc-name { font-size:0.85rem; color:#f87171; background:rgba(248,113,113,0.1); padding:0.2rem 0.6rem; border-radius:8px; }
.night-enter-active, .night-leave-active { transition: opacity 1s ease; }
.night-enter-from, .night-leave-to { opacity: 0; }

/* BANNER RUOLO */
.role-banner { position: fixed; top: 1.5rem; left: 50%; transform: translateX(-50%); z-index: 90; background: rgba(10,10,20,0.95); border: 1px solid rgba(232,200,122,0.3); border-radius: 14px; padding: 1rem 1.5rem; display: flex; align-items: center; gap: 1rem; box-shadow: 0 8px 30px rgba(0,0,0,0.5); min-width: 280px; }
.role-banner-icon { font-size: 2.5rem; }
.role-banner-name { font-family:'Cinzel',serif; font-size:1.1rem; font-weight:700; color:#e8c87a; }
.role-banner-desc { font-size:0.82rem; color:rgba(232,224,213,0.5); margin-top:0.2rem; font-style:italic; }
.role-banner-companions { font-size:0.75rem; color:#f87171; margin-top:0.3rem; }
.banner-enter-active { animation: bannerIn 0.4s ease both; }
.banner-leave-active { animation: bannerOut 0.4s ease both; }
@keyframes bannerIn  { from { opacity:0; transform:translateX(-50%) translateY(-20px); } to { opacity:1; transform:translateX(-50%) translateY(0); } }
@keyframes bannerOut { from { opacity:1; } to { opacity:0; transform:translateX(-50%) translateY(-10px); } }

/* BANNER PAUSA */
.pause-banner { background: rgba(217,119,6,0.12); border-bottom: 1px solid rgba(217,119,6,0.3); color: #fbbf24; font-size:0.85rem; padding: 0.6rem 1.5rem; text-align:center; }

/* HEADER */
.game-header { display: flex; align-items: center; justify-content: space-between; padding: 0.9rem 1.5rem; border-bottom: 1px solid rgba(255,255,255,0.06); position: sticky; top: 0; z-index: 10; background: rgba(7,7,15,0.95); backdrop-filter: blur(8px); }
.brand { font-family:'Cinzel',serif; font-size:1.1rem; font-weight:900; color:#e8c87a; letter-spacing:0.1em; }
.phase-badge { font-family:'Cinzel',serif; font-size:0.85rem; font-weight:700; border: 1px solid; border-radius:20px; padding: 0.3rem 1rem; letter-spacing:0.08em; transition: color 0.5s, border-color 0.5s; }
.timer-wrap { position:relative; width:40px; height:40px; display:flex; align-items:center; justify-content:center; }
.timer-ring { position:absolute; inset:0; width:100%; height:100%; }
.timer-text { font-family:'Cinzel',serif; font-size:0.65rem; font-weight:700; color:#e8e0d5; z-index:1; }

/* BODY */
.game-body { flex:1; display:grid; grid-template-columns: 280px 1fr 200px; height: calc(100vh - 57px); }
@media (max-width: 900px) { .game-body { grid-template-columns: 1fr; height: auto; } }

/* COLONNE */
.players-col, .chat-col, .role-col { border-right: 1px solid rgba(255,255,255,0.05); display:flex; flex-direction:column; overflow:hidden; }
.role-col { border-right: none; }
.col-title { font-family:'Cinzel',serif; font-size:0.7rem; letter-spacing:0.2em; text-transform:uppercase; color:rgba(232,200,122,0.4); padding: 0.9rem 1rem 0.6rem; border-bottom: 1px solid rgba(255,255,255,0.05); display:flex; align-items:center; justify-content:space-between; flex-shrink:0; }
.col-count { font-size:0.65rem; color:rgba(232,200,122,0.3); font-family:'Lato',sans-serif; text-transform:none; letter-spacing:0; }

/* LISTA GIOCATORI */
.players-list { flex:1; overflow-y:auto; padding:0.6rem; display:flex; flex-direction:column; gap:0.35rem; }
.player-row { display:flex; align-items:center; gap:0.7rem; padding: 0.55rem 0.7rem; border-radius:8px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.04); transition: all 0.2s; }
.player-row.is-me { border-color:rgba(232,200,122,0.2); background:rgba(232,200,122,0.04); }
.player-row--dead { opacity:0.35; }
.player-row--vote { cursor:pointer; }
.player-row--vote:hover { border-color:rgba(248,113,113,0.3); background:rgba(248,113,113,0.05); transform:translateX(2px); }
.player-row--vote.is-voted { border-color:rgba(248,113,113,0.5); background:rgba(248,113,113,0.08); }
.p-avatar { width:28px; height:28px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-family:'Cinzel',serif; font-size:0.6rem; font-weight:700; color:#fff; flex-shrink:0; }
.dead-avatar { background:#2a2a3a !important; }
.p-name { font-size:0.85rem; font-weight:700; flex:1; }
.p-tag { font-size:0.6rem; padding:0.08rem 0.35rem; border-radius:8px; background:rgba(100,180,255,0.1); color:#90c8ff; }
.dead-tag { background:none; font-size:0.9rem; }
.voted-tag { background:rgba(248,113,113,0.15); color:#f87171; }
.vote-dots { display:flex; gap:3px; flex-wrap:wrap; }
.vote-dot { width:7px; height:7px; border-radius:50%; background:#f87171; }
.dead-divider { font-size:0.65rem; letter-spacing:0.15em; text-transform:uppercase; color:rgba(232,224,213,0.2); padding:0.4rem 0.2rem 0.2rem; }
.vote-hint { font-size:0.78rem; color:rgba(232,224,213,0.4); font-style:italic; padding:0.3rem 0.5rem; }
.vote-hint.voted { color:#4ade80; }
.info-pill { margin:0.5rem; padding:0.5rem 0.7rem; border-radius:8px; background:rgba(232,200,122,0.06); border:1px solid rgba(232,200,122,0.15); font-size:0.75rem; color:rgba(232,200,122,0.6); text-align:center; }
.night-list { background:rgba(3,3,20,0.5); }
.night-wait { text-align:center; padding:2rem 1rem; color:rgba(130,140,248,0.5); font-style:italic; font-size:0.88rem; line-height:1.8; }
.seer-result { margin-top:1rem; padding:0.8rem; border-radius:8px; background:rgba(103,63,215,0.1); border:1px solid rgba(103,63,215,0.3); display:flex; align-items:center; gap:0.6rem; font-size:0.85rem; }
.seer-icon { font-size:1.2rem; }

/* FINE PARTITA */
.ended-box { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:0.8rem; padding:2rem; overflow-y:auto; }
.ended-icon { font-size:4rem; }
.ended-title { font-family:'Cinzel',serif; font-size:1.3rem; font-weight:700; color:#e8c87a; text-align:center; }
.ended-players { width:100%; display:flex; flex-direction:column; gap:0.3rem; max-height:200px; overflow-y:auto; }
.ended-player-row { display:flex; align-items:center; gap:0.5rem; font-size:0.8rem; padding:0.3rem 0.5rem; border-radius:6px; background:rgba(255,255,255,0.03); }
.ended-role { font-size:1rem; }
.ended-name { flex:1; font-weight:700; }
.ended-status { font-size:0.7rem; color:rgba(232,224,213,0.4); }
.btn-home { background:linear-gradient(135deg,#16a34a,#15803d); border:none; border-radius:10px; color:#fff; font-family:'Cinzel',serif; font-size:0.9rem; font-weight:700; padding:0.8rem 1.5rem; cursor:pointer; transition:all 0.2s; margin-top:0.5rem; }
.btn-home:hover { transform:translateY(-2px); }

/* MOCK */
.mock-controls { padding:0.6rem; border-top:1px dashed rgba(255,255,255,0.06); flex-shrink:0; }
.mock-label { font-size:0.6rem; color:rgba(255,255,255,0.15); letter-spacing:0.1em; margin-bottom:0.3rem; }
.mock-btns { display:flex; gap:0.3rem; flex-wrap:wrap; }
.mock-btns button { font-size:0.65rem; padding:0.2rem 0.5rem; border-radius:4px; border:1px solid rgba(255,255,255,0.1); background:rgba(255,255,255,0.04); color:rgba(232,224,213,0.4); cursor:pointer; }
.mock-btns button:hover { background:rgba(255,255,255,0.08); color:#e8e0d5; }

/* CHAT */
.chat-channel { font-size:0.65rem; color:rgba(232,224,213,0.3); font-family:'Lato',sans-serif; text-transform:none; letter-spacing:0; }
.chat-messages { flex:1; overflow-y:auto; padding:0.7rem; display:flex; flex-direction:column; gap:0.4rem; }
.chat-empty { font-size:0.8rem; color:rgba(232,224,213,0.2); font-style:italic; text-align:center; padding:1rem; }
.chat-msg { display:flex; flex-direction:column; gap:0.1rem; }
.chat-msg--me .msg-sender { color:#90c8ff; }
.msg-sender { font-size:0.68rem; font-weight:700; color:rgba(232,200,122,0.5); }
.msg-text { font-size:0.85rem; color:rgba(232,224,213,0.85); line-height:1.4; background:rgba(255,255,255,0.03); border-radius:6px; padding:0.35rem 0.55rem; }
.chat-msg--me .msg-text { background:rgba(100,180,255,0.07); }
.chat-input-wrap { display:flex; gap:0.4rem; padding:0.7rem; border-top:1px solid rgba(255,255,255,0.05); flex-shrink:0; }
.chat-input { flex:1; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); border-radius:8px; padding:0.5rem 0.7rem; color:#e8e0d5; font-family:'Lato',sans-serif; font-size:0.85rem; outline:none; }
.chat-input:focus { border-color:rgba(232,200,122,0.3); }
.chat-input:disabled { opacity:0.3; cursor:not-allowed; }
.chat-send { background:rgba(232,200,122,0.1); border:1px solid rgba(232,200,122,0.2); border-radius:8px; color:#e8c87a; width:34px; cursor:pointer; font-size:0.9rem; transition:all 0.2s; flex-shrink:0; }
.chat-send:hover:not(:disabled) { background:rgba(232,200,122,0.18); }
.chat-send:disabled { opacity:0.25; cursor:not-allowed; }

/* SIDEBAR RUOLO */
.role-col { padding:0; }
.role-card { margin:0.7rem; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07); border-radius:12px; padding:1rem; text-align:center; }
.role-card-icon { font-size:2.2rem; margin-bottom:0.4rem; }
.role-card-name { font-family:'Cinzel',serif; font-size:0.9rem; font-weight:700; color:#e8c87a; }
.role-card-desc { font-size:0.75rem; color:rgba(232,224,213,0.4); margin-top:0.4rem; font-style:italic; line-height:1.5; }
.wolf-list { margin-top:0.8rem; border-top:1px solid rgba(248,113,113,0.15); padding-top:0.6rem; }
.wolf-list-title { font-size:0.65rem; letter-spacing:0.15em; text-transform:uppercase; color:rgba(248,113,113,0.4); margin-bottom:0.4rem; }
.wolf-item { font-size:0.8rem; color:#f87171; padding:0.2rem 0; }
.round-box { margin:0.7rem; display:flex; justify-content:space-between; align-items:center; padding:0.6rem 0.8rem; background:rgba(255,255,255,0.02); border-radius:8px; border:1px solid rgba(255,255,255,0.05); }
.round-label { font-size:0.72rem; color:rgba(232,224,213,0.4); letter-spacing:0.1em; text-transform:uppercase; }
.round-num { font-family:'Cinzel',serif; font-size:1.1rem; font-weight:700; color:#e8c87a; }
.count-box { margin:0 0.7rem; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:8px; padding:0.6rem 0.8rem; }
.count-row { display:flex; justify-content:space-between; font-size:0.82rem; padding:0.25rem 0; border-bottom:1px solid rgba(255,255,255,0.04); color:rgba(232,224,213,0.5); }
.count-row:last-child { border-bottom:none; }
.count-row strong { color:#e8e0d5; }
</style>
