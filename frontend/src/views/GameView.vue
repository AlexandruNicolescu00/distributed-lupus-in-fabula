<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useGameStore, PHASES, ROLES, WINNERS } from '@/stores/gameStore'
import { useLobbyStore } from '@/stores/lobbyStore'
import { useChatStore } from '@/stores/chatStore'
import { useSocket } from '@/composables/useSocket' //

// Componenti UI
import ChatBox    from '@/components/ChatBox.vue'
import PhaseTimer from '@/components/PhaseTimer.vue'
import InfoBox    from '@/components/InfoBox.vue'

// ---- SETUP ROUTING E STORES ----
const router = useRouter()
const route  = useRoute()
const game   = useGameStore()
const lobby  = useLobbyStore()
const chat   = useChatStore()
const { connect, isConnected } = useSocket() //

// ---- STATO LOCALE ----
const showRoleBanner   = ref(false)
const myVote           = ref(null)
const nightActionDone  = ref(false)
const showNightOverlay = ref(false)
const lobbyCode       = route.params.id || lobby.lobbyCode // Recupera codice da URL o store

// ---- LIFECYCLE: CONNESSIONE DISTRIBUITA ----
onMounted(async () => {
  if (!lobbyCode) {
    console.error('[GameView] Codice stanza mancante, ritorno alla home')
    router.push('/')
    return
  }

  // 1. Inizializziamo i listener (devono essere pronti PRIMA della connessione)
  game.listenToGameEvents()
  chat.listenToMessages()

  // 2. Gestione Identità (per Fault Tolerance su Redis)
  // Usiamo un client_id persistente: se il player ricarica o cambia istanza, Redis lo riconosce
  let clientId = localStorage.getItem('client_id')
  if (!clientId) {
    clientId = `user_${Math.random().toString(36).substr(2, 9)}`
    localStorage.setItem('client_id', clientId)
  }
  game.currentPlayerId = clientId
  game.bootstrapFromLobby(lobby.players, clientId)

  // 3. Connessione al Backend (via Ingress NGINX /socket.io/)
  // Passiamo auth per permettere al backend di mappare sid -> client_id
  const wsUrl = import.meta.env.VITE_WS_URL || 'http://game.local'
  connect(wsUrl, {
    auth: {
      client_id: clientId,
      room_id: lobbyCode
    }
  })

  // 4. Caricamento Stato Iniziale (REST fallback per velocità UI)
  try {
    await game.loadState(lobbyCode)
  } catch (err) {
    console.warn('[GameView] Impossibile caricare stato via REST, attendo WebSocket...')
  }

  // Banner iniziale
  showRoleBanner.value = true
  setTimeout(() => (showRoleBanner.value = false), 4000)
})

onUnmounted(() => { 
  // Pulizia dello stato locale, ma manteniamo la connessione socket 
  // se stiamo solo navigando tra componenti interni
  chat.reset() 
})

// ---- WATCHERS ----

// Gestione Atmosfera e Reset Azioni
watch(() => game.phase, (newPhase) => {
  showNightOverlay.value = (newPhase === PHASES.NIGHT)
  myVote.value           = null
  nightActionDone.value  = false
  
  if (newPhase === PHASES.ENDED) {
    // Lasciamo vedere l'ultima animazione prima dei risultati
    setTimeout(() => router.push(`/results/${lobbyCode}`), 3000)
  }
})

// ---- COMPUTED ----
const phaseLabel = computed(() => {
  const map = {
    [PHASES.DAY]:    '☀️  Giorno',
    [PHASES.VOTING]: '🗳️  Votazione',
    [PHASES.NIGHT]:  '🌙  Notte',
    [PHASES.ENDED]:  '🏁  Fine Partita',
  }
  return map[game.phase] ?? 'Preparazione'
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
    [ROLES.SEER]:     { icon: '🔮',   name: 'Veggente',  desc: 'Indaga sui sospetti.' },
  }
  return map[game.myRole] ?? { icon: '❓', name: 'In attesa...', desc: 'Il tuo ruolo verrà rivelato presto.' }
})

const canVote = computed(() => game.phase === PHASES.VOTING && game.isAlive && !myVote.value)
const canAct  = computed(() => game.phase === PHASES.NIGHT && game.isAlive && (game.isWolf || game.isSeer) && !nightActionDone.value)
const visiblePlayers = computed(() => {
  if (game.players.length > 0) return game.players
  return game.normalizePlayers(lobby.players)
})

const sidebarRows = computed(() => [
  { label: 'Round',     value: game.round || 1 },
  { label: 'Vivi',      value: visiblePlayers.value.filter((p) => p.alive).length },
  { label: 'Eliminati', value: visiblePlayers.value.filter((p) => !p.alive).length },
])

// ---- FUNZIONI AZIONE (Inviano eventi via Socket.IO -> Redis PubSub) ----
function castVote(targetId) {
  if (!canVote.value || targetId === game.currentPlayerId) return
  myVote.value = targetId
  game.vote(lobbyCode, targetId) 
}

function castNightAction(targetId) {
  if (!canAct.value) return
  nightActionDone.value = true
  game.isWolf ? game.wolfVote(targetId) : game.seerAction(targetId)
}

function avatarColor(id) { 
  const colors = ['#7c3aed','#16a34a','#dc2626','#2563eb','#d97706']
  return colors[(id ?? '').charCodeAt(0) % colors.length] 
}

function initials(name) { return (name ?? '?').slice(0, 2).toUpperCase() }
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
        <div class="night-content">
          <div class="night-moon">🌙</div>
          <div class="night-title">La Notte è calata</div>
          <div class="night-sub">
            <template v-if="game.isWolf">Coordina l'attacco con i tuoi compagni lupi.</template>
            <template v-else-if="game.isSeer">Usa i tuoi poteri per smascherare un lupo.</template>
            <template v-else>Il villaggio dorme. Spera di risvegliarti domani.</template>
          </div>
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

    <div class="game-body">
      
      <section class="players-col">
        <div class="col-title">Popolazione <span>{{ visiblePlayers.filter((p) => p.alive).length }} vivi</span></div>
        
        <div class="players-list">
          <template v-if="game.phase !== PHASES.ENDED">
            
            <div v-for="p in visiblePlayers" :key="p.player_id" 
              class="player-row" 
              :class="{ 
                'is-me': p.player_id === game.currentPlayerId,
                'is-dead': !p.alive,
                'can-interact': (canVote || canAct) && p.alive && p.player_id !== game.currentPlayerId,
                'is-selected': myVote === p.player_id || nightActionDone && p.selected
              }"
              @click="game.phase === PHASES.VOTING ? castVote(p.player_id) : castNightAction(p.player_id)">
              
              <div class="p-avatar" :style="{ background: p.alive ? avatarColor(p.player_id) : '#2a2a3a' }">
                {{ initials(p.username) }}
              </div>
              
              <div class="p-info">
                <span class="p-name">{{ p.username }}</span>
                <span v-if="p.player_id === game.currentPlayerId" class="p-tag">tu</span>
                <span v-if="!p.alive" class="p-dead-tag">ELIMINATO</span>
                <span v-else class="p-conn">{{ p.connected === false ? 'disconnesso' : 'connesso' }}</span>
              </div>

              <div v-if="game.phase === PHASES.VOTING && p.alive" class="vote-badges">
                <span v-for="n in (game.voteCount[p.player_id] || 0)" :key="n" class="vote-dot"></span>
              </div>
            </div>

          </template>
        </div>
      </section>

      <section class="chat-col">
        <ChatBox />
      </section>

      <aside class="role-col">
        <div class="col-title">Il Tuo Destino</div>
        
        <div class="role-card">
          <div class="role-card-icon">{{ roleLabel.icon }}</div>
          <div class="role-card-name">{{ roleLabel.name }}</div>
          <div class="role-card-desc">{{ roleLabel.desc }}</div>
          
          <div v-if="game.isWolf && game.wolfCompanions.length" class="wolf-companions-list">
            <div class="wc-title">Branchia:</div>
            <div v-for="w in game.wolfCompanions" :key="w.player_id" class="wc-item">
              🐺 {{ w.username }}
            </div>
          </div>
        </div>

        <InfoBox title="Cronologia" :rows="sidebarRows" />

        <div class="action-feedback">
          <p>Interfaccia pronta: il game loop completo arrivera con gli eventi backend.</p>
        </div>

        <div v-if="game.seerResult" class="action-feedback">
          <p>🔮 Visione: <strong>{{ game.seerResult.targetName }}</strong> è {{ game.seerResult.role }}</p>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.game-root { min-height: 100vh; background: #07070f; color: #e8e0d5; display: flex; flex-direction: column; }
.game-header { display: flex; align-items: center; justify-content: space-between; padding: 1rem 2rem; background: rgba(0,0,0,0.4); backdrop-filter: blur(10px); border-bottom: 1px solid rgba(255,255,255,0.05); }

/* Overlay Riconnessione */
.network-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 999; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.loader { border: 4px solid #f3f3f3; border-top: 4px solid #818cf8; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin-bottom: 1rem; }
@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }

.game-body { flex: 1; display: grid; grid-template-columns: 300px 1fr 250px; overflow: hidden; }

/* Liste Giocatori */
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

/* Voti */
.vote-badges { display: flex; gap: 4px; flex-wrap: wrap; }
.vote-dot { width: 8px; height: 8px; background: #f87171; border-radius: 50%; box-shadow: 0 0 5px #f87171; }

/* Sidebars */
.role-col { padding: 1rem; display: flex; flex-direction: column; gap: 1rem; background: rgba(255,255,255,0.01); }
.role-card { padding: 1.5rem; background: rgba(232, 200, 122, 0.05); border: 1px solid rgba(232, 200, 122, 0.1); border-radius: 16px; text-align: center; }
.role-card-icon { font-size: 3rem; margin-bottom: 0.5rem; }
.role-card-name { font-family: 'Cinzel', serif; color: #e8c87a; font-size: 1.2rem; }

.action-feedback { padding: 1rem; background: rgba(129, 140, 248, 0.1); border-radius: 10px; font-size: 0.85rem; border: 1px solid rgba(129, 140, 248, 0.2); }

/* Night Overlay */
.night-overlay { position: fixed; inset: 0; background: rgba(5, 5, 20, 0.9); z-index: 100; display: flex; align-items: center; justify-content: center; pointer-events: none; }
.night-content { text-align: center; color: #818cf8; }
.night-moon { font-size: 4rem; margin-bottom: 1rem; animation: pulse 2s infinite; }

@keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }
</style>
