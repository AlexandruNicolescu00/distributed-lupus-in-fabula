<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useGameStore, PHASES, ROLES } from '@/stores/gameStore'
import { useLobbyStore } from '@/stores/lobbyStore'
import { useChatStore } from '@/stores/chatStore'
import { useSocket } from '@/composables/useSocket'

import ChatBox from '@/components/ChatBox.vue'
import PhaseTimer from '@/components/PhaseTimer.vue'
import InfoBox from '@/components/InfoBox.vue'
import PlayerCard from '@/components/PlayerCard.vue'

const router = useRouter()
const route = useRoute()
const game = useGameStore()
const lobby = useLobbyStore()
const chat = useChatStore()
const { connect, disconnect, emit, isConnected } = useSocket()

const showRoleBanner = ref(false)
const myVote = ref(null)
const nightActionDone = ref(false)
const lobbyCode = route.params.id || lobby.lobbyCode

const isCurrentUserHost = computed(() =>
  lobby.isHost || (game.hostId && game.hostId === game.currentPlayerId)
)

const showNightOverlay = computed(() => game.phase === PHASES.NIGHT)

onMounted(async () => {
  if (!lobbyCode) {
    router.push('/')
    return
  }
  
  game.listenToGameEvents()
  chat.listenToMessages()

  let clientId = sessionStorage.getItem('client_id')
  if (!clientId) {
    clientId = `user_${Math.random().toString(36).slice(2, 11)}`
    sessionStorage.setItem('client_id', clientId)
  }

  game.currentPlayerId = clientId
  
  if (game.players.length === 0) {
    game.bootstrapFromLobby(lobby.players, clientId, lobby.roleSummary, lobbyCode)
  }

  const wsUrl = import.meta.env.VITE_WS_URL || 'http://localhost:8000'
  connect(wsUrl, {
    auth: {
      client_id: clientId,
      room_id: lobbyCode,
    },
  })

  try {
    await game.loadState(lobbyCode)
  } catch {
    console.warn('[GameView] Stato iniziale non disponibile via REST, attendo WebSocket')
  }

  showRoleBanner.value = true
  setTimeout(() => {
    showRoleBanner.value = false
  }, 4000)
})

onUnmounted(() => {
  chat.reset()
})

watch(
  () => game.phase,
  (newPhase) => {
    // 1. Azzeriamo le azioni del turno
    myVote.value = null
    nightActionDone.value = false
    
    // 2. Puliamo SEMPRE i pallini dei voti al cambio fase (visivamente)
    game.voteMap = {}

    // 3. La visione del Veggente si cancella SOLO quando ricomincia una nuova Notte.
    // In questo modo il Veggente può leggere il risultato per tutto il Giorno e la Votazione!
    if (newPhase === PHASES.NIGHT) {
      game.seerResult = null
    }

    if (newPhase === PHASES.ENDED) {
      setTimeout(() => router.push(`/results/${lobbyCode}`), 3000)
    }
  }
)

const phaseLabel = computed(() => {
  const map = {
    [PHASES.DAY]: 'Giorno',
    [PHASES.VOTING]: 'Votazione',
    [PHASES.NIGHT]: 'Notte',
    [PHASES.ENDED]: 'Fine Partita',
  }
  return map[game.phase] ?? 'Preparazione'
})

const phaseColor = computed(() => {
  const map = {
    [PHASES.DAY]: '#e8c87a',
    [PHASES.VOTING]: '#f87171',
    [PHASES.NIGHT]: '#818cf8',
    [PHASES.ENDED]: '#4ade80',
  }
  return map[game.phase] ?? '#e8e0d5'
})

const roleLabel = computed(() => {
  const map = {
    [ROLES.VILLAGER]: { icon: '🧑‍🌾', name: 'Contadino', desc: 'Trova i lupi e sopravvivi.' },
    [ROLES.WOLF]: { icon: '🐺', name: 'Lupo', desc: 'Elimina i villici e resta nascosto.' },
    [ROLES.SEER]: { icon: '🔮', name: 'Veggente', desc: 'Scopri chi sono i lupi.' },
  }
  const normalizedRole = game.myRole ? game.myRole.toUpperCase() : null
  return map[normalizedRole] ?? { icon: '❓', name: 'In attesa', desc: 'Il tuo ruolo verrà rivelato presto.' }
})

const visiblePlayers = computed(() => {
  if (game.players.length > 0) return game.players
  return game.normalizePlayers(lobby.players)
})

const ownPlayerCard = computed(() => {
  const me = visiblePlayers.value.find((player) => player.player_id === game.currentPlayerId)
  if (!me) return null

  return {
    player_id: me.player_id,
    username: me.username,
    role: me.role ?? game.myRole,
    ready: true,
    alive: me.alive,
    connected: me.connected,
  }
})

// Controllo sicurezza locale: Puoi votare/agire SOLO se la tua card dice che sei vivo
const canVote = computed(() => game.phase === PHASES.VOTING && ownPlayerCard.value?.alive)
const canAct = computed(() => game.phase === PHASES.NIGHT && ownPlayerCard.value?.alive && (game.isWolf || game.isSeer))

const sidebarRows = computed(() => [
  { label: 'Round', value: game.round || 1 },
  { label: 'Vivi', value: visiblePlayers.value.filter((player) => player.alive).length },
  { label: 'Eliminati', value: visiblePlayers.value.filter((player) => !player.alive).length },
])

function castVote(targetId) {
  if (!canVote.value || targetId === game.currentPlayerId) return
  
  // Aggiorna visivamente il voto
  myVote.value = targetId
  game.vote(lobbyCode, targetId)
}

function castNightAction(targetId) {
  if (!canAct.value) return
  
  nightActionDone.value = true
  if (game.isWolf) {
    game.wolfVote(targetId)
  } else {
    game.seerAction(targetId)
  }
}

function avatarColor(id) {
  const colors = ['#7c3aed', '#16a34a', '#dc2626', '#2563eb', '#d97706']
  return colors[(id ?? '').charCodeAt(0) % colors.length]
}

function initials(name) {
  return (name ?? '?').slice(0, 2).toUpperCase()
}

function leaveGame() {
  game.reset()
  lobby.reset()
  chat.reset()
  disconnect()
  router.push('/')
}
</script>

<template>
  <div class="game-root" :class="`phase--${game.phase.toLowerCase()}`">

    <Transition name="fade">
      <div v-if="!isConnected" class="network-overlay">
        <div class="loader"></div>
        <p>Connessione al server interrotta...</p>
        <span>Ripristino sessione via Redis in corso</span>
      </div>
    </Transition>

    <Transition name="night">
      <div v-if="showNightOverlay" class="night-overlay">
        
        <div v-if="!game.myRole" class="night-content role-unknown">
          <div class="night-moon">🎭</div>
          <div class="night-title">Assegnazione Ruoli...</div>
          <div class="night-sub">Chiudi gli occhi. Il tuo destino sta per essere deciso.</div>
        </div>

        <div v-else-if="ownPlayerCard && !ownPlayerCard.alive" class="night-content">
          <div class="night-moon">👻</div>
          <div class="night-title">Il riposo eterno</div>
          <div class="night-sub">Sei un fantasma. Goditi lo spettacolo senza interferire.</div>
          <div class="timer-display">Tempo all'alba: {{ game.secondsLeft ?? 0 }}s</div>
        </div>

        <div v-else-if="game.isVillager" class="night-content role-villager">
          <div class="night-moon">💤</div>
          <div class="night-title">Stai dormendo</div>
          <div class="night-sub">
            Sei un pacifico Contadino. La notte è buia e piena di terrori...
            Spera solo di svegliarti vivo domattina.
          </div>
          <div class="timer-display">Tempo all'alba: {{ game.secondsLeft ?? 0 }}s</div>
        </div>

        <div v-else-if="game.isSeer" class="night-content role-seer">
          <div class="night-moon seer-eye">🔮</div>
          <div class="night-title">Sfera di Cristallo</div>
          <div class="night-sub">
            <span v-if="nightActionDone">Hai già avuto la tua visione per stanotte.</span>
            <span v-else>Scegli una mente in cui curiosare per scoprire la sua vera natura.</span>
          </div>
          
          <div class="action-grid" v-if="!nightActionDone">
            <button 
              v-for="player in visiblePlayers.filter(p => p.alive && p.player_id !== game.currentPlayerId)" 
              :key="player.player_id"
              class="target-btn seer-btn"
              @click="castNightAction(player.player_id)"
            >
              Indaga {{ player.username }}
            </button>
          </div>

          <div v-if="game.seerResult" class="vision-result">
            La tua visione è chiara: <strong>{{ game.seerResult.targetName }}</strong> è della fazione dei {{ game.seerResult.role === ROLES.WOLF ? '🐺 Lupi' : '🧑‍🌾 Contadini' }}.
          </div>

          <div class="timer-display">Tempo all'alba: {{ game.secondsLeft ?? 0 }}s</div>
        </div>

        <div v-else-if="game.isWolf" class="night-content role-wolf">
          <div class="night-moon wolf-claw">🐺</div>
          <div class="night-title">L'ora della Caccia</div>
          <div class="night-sub">
            <span v-if="nightActionDone">In attesa dei tuoi fratelli...</span>
            <span v-else>Decidi chi sarà la vittima stanotte.</span>
          </div>

          <div class="wolf-dashboard">
            <div class="action-grid" v-if="!nightActionDone">
               <button 
                v-for="player in visiblePlayers.filter(p => p.alive && !game.wolfCompanions.some(w => w.player_id === p.player_id) && p.player_id !== game.currentPlayerId)" 
                :key="player.player_id"
                class="target-btn wolf-btn"
                @click="castNightAction(player.player_id)"
              >
                Sbrana {{ player.username }}
              </button>
            </div>

            <div class="wolf-chat-container">
              <div class="wolf-chat-header">Chat del Branco</div>
              <ChatBox /> 
            </div>
          </div>

          <div class="timer-display">Tempo all'alba: {{ game.secondsLeft ?? 0 }}s</div>
        </div>

      </div>
    </Transition>

    <Transition name="banner">
      <div v-if="showRoleBanner" class="role-banner">
        <span class="role-banner-icon">{{ roleLabel.icon }}</span>
        <div>
          <div class="role-banner-name">Sei il {{ roleLabel.name }}</div>
          <div class="role-banner-desc">{{ roleLabel.desc }}</div>
        </div>
      </div>
    </Transition>

    <header class="game-header">
      <div class="brand">🐺 <span>LUPUS</span></div>
      <div class="phase-badge" :style="{ borderColor: phaseColor, color: phaseColor }">
        {{ phaseLabel }}
      </div>
      <PhaseTimer size="md" :color="phaseColor" />
    </header>

    <div v-if="game.isPaused" class="pause-banner">
      ⚠️ Partita in pausa: {{ game.pauseReason || 'In attesa che i giocatori rientrino' }}...
    </div>

    <div class="game-body" v-show="!showNightOverlay">
      <section class="players-col">
        <div class="col-title">Popolazione <span>{{ visiblePlayers.filter((player) => player.alive).length }} vivi</span></div>

        <div class="players-list">
          <template v-if="game.phase !== PHASES.ENDED">
            <div
              v-for="player in visiblePlayers"
              :key="player.player_id"
              class="player-row"
              :class="{
                'is-me': player.player_id === game.currentPlayerId,
                'is-dead': !player.alive,
                'can-interact': (canVote || canAct) && player.alive && player.player_id !== game.currentPlayerId,
                'is-selected': myVote === player.player_id || (nightActionDone && player.selected),
              }"
              @click="game.phase === PHASES.VOTING ? castVote(player.player_id) : castNightAction(player.player_id)"
            >
              <div class="p-avatar" :style="{ background: player.alive ? avatarColor(player.player_id) : '#2a2a3a' }">
                {{ initials(player.username) }}
              </div>

              <div class="p-info">
                <span class="p-name">{{ player.username }}</span>
                <span v-if="player.player_id === game.currentPlayerId" class="p-tag">tu</span>
                <span v-if="!player.alive" class="p-dead-tag">ELIMINATO</span>
                <span v-else class="p-conn">{{ player.connected === false ? 'disconnesso' : 'connesso' }}</span>
              </div>

              <div v-if="game.phase === PHASES.VOTING && player.alive" class="vote-badges">
                <span v-for="n in (game.voteCount[player.player_id] || 0)" :key="n" class="vote-dot"></span>
              </div>
            </div>
          </template>
        </div>
      </section>

      <section class="chat-col">
        <ChatBox />
      </section>

      <aside class="role-col">
        <div v-if="ownPlayerCard" class="revealed-card">
          <PlayerCard :player="ownPlayerCard" :is-me="true" :show-role="true" />
          <div class="role-hint">{{ roleLabel.desc }}</div>
        </div>

        <InfoBox title="Cronologia" :rows="sidebarRows" />

        <div v-if="game.isWolf && game.wolfCompanions.length" class="wolf-box">
          <div class="wolf-box-title">Il Branco</div>
          <div v-for="wolf in game.wolfCompanions" :key="wolf.player_id" class="wolf-box-item">
            🐺 {{ wolf.username }}
          </div>
        </div>

        <div v-if="game.isSeer && game.seerResult" class="action-feedback">
          <p>🔮 Visione: <strong>{{ game.seerResult.targetName }}</strong> è {{ game.seerResult.role === ROLES.WOLF ? 'Lupo' : 'Contadino' }}</p>
        </div>

        <button class="leave-game-btn leave-game-btn--sidebar" @click="leaveGame">
          Abbandona Partita
        </button>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.game-root {
  min-height: 100vh;
  color: #e8e0d5;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(circle at top, rgba(124, 58, 237, 0.14), transparent 28%),
    radial-gradient(circle at bottom right, rgba(232, 200, 122, 0.08), transparent 24%),
    #07070f;
}
.game-header { display: flex; align-items: center; justify-content: space-between; padding: 1rem 2rem; background: rgba(0,0,0,0.4); backdrop-filter: blur(10px); border-bottom: 1px solid rgba(255,255,255,0.05); }
.network-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 999; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.loader { border: 4px solid #f3f3f3; border-top: 4px solid #818cf8; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin-bottom: 1rem; }
@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
.game-body { flex: 1; display: grid; grid-template-columns: 300px 1fr 270px; overflow: hidden; }
.col-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-family: 'Cinzel', serif;
  font-size: 1rem;
  letter-spacing: 0.04em;
  padding: 1rem 1rem 0;
}
.col-title span {
  font-family: 'Lato', sans-serif;
  font-size: 0.78rem;
  color: rgba(232,224,213,0.55);
}
.players-list { padding: 1rem; display: flex; flex-direction: column; gap: 0.5rem; overflow-y: auto; }
.player-row { display: flex; align-items: center; gap: 1rem; padding: 0.8rem; background: rgba(255,255,255,0.03); border-radius: 12px; border: 1px solid transparent; transition: 0.2s; }
.player-row.can-interact { cursor: pointer; }
.player-row.can-interact:hover { border-color: #818cf8; background: rgba(129, 140, 248, 0.1); }
.player-row.is-selected { border-color: #f87171; background: rgba(248, 113, 113, 0.1); }
.player-row.is-dead { opacity: 0.4; filter: grayscale(1); }
.p-avatar { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; }
.p-info { flex: 1; display: flex; flex-direction: column; }
.p-name { font-weight: 600; font-size: 0.95rem; }
.p-tag { font-size: 0.7rem; color: #e8c87a; font-weight: bold; }
.p-conn { font-size: 0.72rem; color: rgba(232,224,213,0.45); }
.vote-badges { display: flex; gap: 4px; flex-wrap: wrap; }
.vote-dot { width: 8px; height: 8px; background: #f87171; border-radius: 50%; box-shadow: 0 0 5px #f87171; }
.role-col {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  background: rgba(255,255,255,0.01);
  border-left: 1px solid rgba(255,255,255,0.05);
}
.revealed-card { display: flex; flex-direction: column; gap: 0.8rem; }
.role-hint {
  text-align: center;
  font-size: 0.9rem;
  color: rgba(232,224,213,0.75);
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.06);
  border-radius: 12px;
  padding: 0.85rem 0.9rem;
}
.wolf-box {
  padding: 1rem;
  background: rgba(248,113,113,0.08);
  border: 1px solid rgba(248,113,113,0.15);
  border-radius: 14px;
}
.wolf-box-title {
  font-family: 'Cinzel', serif;
  color: #fca5a5;
  margin-bottom: 0.5rem;
}
.wolf-box-item {
  font-size: 0.9rem;
  color: rgba(232,224,213,0.85);
}
.action-feedback { padding: 1rem; background: rgba(192, 132, 252, 0.1); border-radius: 10px; font-size: 0.85rem; border: 1px solid rgba(192, 132, 252, 0.3); }
.pause-banner { padding: 0.9rem 2rem; background: rgba(248,113,113,0.08); color: #fca5a5; border-bottom: 1px solid rgba(248,113,113,0.15); }
.role-banner {
  position: fixed;
  top: 1.5rem;
  left: 50%;
  transform: translateX(-50%);
  z-index: 200;
  display: flex;
  align-items: center;
  gap: 0.9rem;
  padding: 0.9rem 1.2rem;
  border-radius: 16px;
  background: rgba(10,10,18,0.92);
  border: 1px solid rgba(232,200,122,0.18);
  box-shadow: 0 20px 40px rgba(0,0,0,0.45);
}
.role-banner-icon { font-size: 1.8rem; }
.role-banner-name { font-family: 'Cinzel', serif; color: #e8c87a; }
.role-banner-desc { font-size: 0.82rem; color: rgba(232,224,213,0.72); }
.brand { font-family: 'Cinzel', serif; letter-spacing: 0.18rem; color: #e8c87a; display: flex; align-items: center; gap: 0.4rem; }
.leave-game-btn { padding: 0.7rem 1rem; border: 1px solid rgba(248,113,113,0.35); border-radius: 10px; background: rgba(248,113,113,0.08); color: #fca5a5; cursor: pointer; font-weight: 600; }
.leave-game-btn:hover { background: rgba(248,113,113,0.15); border-color: rgba(248,113,113,0.6); }
.leave-game-btn--sidebar { width: 100%; margin-top: auto; }
.night-overlay { position: fixed; inset: 0; background: rgba(5, 5, 20, 0.95); z-index: 100; display: flex; align-items: center; justify-content: center; backdrop-filter: blur(8px); }
.night-content { text-align: center; max-width: 800px; width: 100%; padding: 2rem; }
.night-title { font-size: 2.5rem; margin-bottom: 0.5rem; font-family: 'Cinzel', serif; }
.night-sub { font-size: 1.1rem; opacity: 0.8; margin-bottom: 2rem; }
.timer-display { margin-top: 2rem; font-family: monospace; font-size: 1.2rem; background: rgba(0,0,0,0.5); padding: 0.5rem 1rem; display: inline-block; border-radius: 8px; }
.role-unknown { color: #a1a1aa; }
.role-villager { color: #94a3b8; }
.role-seer { color: #c084fc; }
.seer-eye { font-size: 5rem; animation: float 3s ease-in-out infinite; }
.role-wolf { color: #fca5a5; }
.wolf-claw { font-size: 5rem; text-shadow: 0 0 20px rgba(220,38,38,0.6); }
@keyframes float { 0% { transform: translateY(0); } 50% { transform: translateY(-15px); } 100% { transform: translateY(0); } }
.action-grid { display: flex; flex-wrap: wrap; gap: 1rem; justify-content: center; margin-bottom: 2rem; }
.target-btn { padding: 1rem 2rem; border-radius: 12px; font-weight: bold; font-size: 1rem; cursor: pointer; transition: 0.2s; border: 2px solid transparent; }
.seer-btn { background: rgba(192, 132, 252, 0.1); color: #c084fc; border-color: rgba(192, 132, 252, 0.3); }
.seer-btn:hover { background: rgba(192, 132, 252, 0.3); transform: scale(1.05); }
.wolf-btn { background: rgba(220, 38, 38, 0.1); color: #fca5a5; border-color: rgba(220, 38, 38, 0.3); }
.wolf-btn:hover { background: rgba(220, 38, 38, 0.3); transform: scale(1.05); }
.wolf-dashboard { display: grid; grid-template-columns: 1fr 350px; gap: 2rem; align-items: start; text-align: left; }
.wolf-chat-container { background: rgba(0,0,0,0.5); border: 1px solid rgba(220,38,38,0.2); border-radius: 16px; height: 400px; display: flex; flex-direction: column; overflow: hidden; pointer-events: auto; }
.wolf-chat-header { background: rgba(220,38,38,0.15); padding: 0.8rem; font-weight: bold; text-align: center; border-bottom: 1px solid rgba(220,38,38,0.2); }
.vision-result { padding: 1.5rem; background: rgba(192, 132, 252, 0.1); border: 1px solid rgba(192, 132, 252, 0.3); border-radius: 12px; font-size: 1.2rem; animation: float 3s; }
@media (max-width: 900px) {
  .game-body { grid-template-columns: 1fr; }
  .role-col { order: -1; }
}
@media (max-width: 800px) {
  .wolf-dashboard { grid-template-columns: 1fr; }
}
</style>