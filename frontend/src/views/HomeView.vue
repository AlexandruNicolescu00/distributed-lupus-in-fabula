<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useLobbyStore } from '@/stores/lobbyStore'
import { lobbyApi } from '@/services/api'
import castleBg from '@/assets/home3.png'

const router = useRouter()
const lobbyStore = useLobbyStore()

// ---- STATE UI ----
const mode = ref(null)          // null | 'create' | 'join'
const playerName = ref('')
const lobbyCode = ref('')       // codice della lobby selezionata dalla lista
const nameError = ref('')
const codeError = ref('')

// ---- LOBBY BROWSER ----
// Le lobby aperte arrivano dal backend (REST /api/lobbies): il frontend non
// interroga mai Redis direttamente. La lista è filtrabile con una ricerca testuale.
const lobbies = ref([])
const lobbySearch = ref('')
const loadingLobbies = ref(false)
const lobbiesError = ref('')
let pollTimer = null
const POLL_INTERVAL = 4000

const isLoading = computed(() => lobbyStore.isLoading)

// ---- REGOLE ----
const showRules = ref(false)

const filteredLobbies = computed(() => {
  const query = lobbySearch.value.trim().toLowerCase()
  if (!query) return lobbies.value
  return lobbies.value.filter(
    (lobby) =>
      lobby.code.toLowerCase().includes(query) ||
      String(lobby.host ?? '').toLowerCase().includes(query),
  )
})

async function fetchLobbies() {
  loadingLobbies.value = true
  lobbiesError.value = ''
  try {
    const data = await lobbyApi.listOpen()
    lobbies.value = Array.isArray(data?.lobbies) ? data.lobbies : []
    // Se la lobby selezionata non è più disponibile, deseleziona.
    if (lobbyCode.value && !lobbies.value.some((l) => l.code === lobbyCode.value)) {
      lobbyCode.value = ''
    }
  } catch (err) {
    lobbiesError.value = 'Impossibile caricare le lobby disponibili.'
    console.error('[Home] fetchLobbies:', err)
  } finally {
    loadingLobbies.value = false
  }
}

function startLobbyPolling() {
  fetchLobbies()
  stopLobbyPolling()
  pollTimer = setInterval(fetchLobbies, POLL_INTERVAL)
}

function stopLobbyPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function selectLobby(code) {
  lobbyCode.value = code
  codeError.value = ''
}

onUnmounted(stopLobbyPolling)

// ---- VALIDAZIONE LOCALE ----
function validateName() {
  const name = playerName.value.trim()
  if (!name) {
    nameError.value = 'Inserisci il tuo nome'
    return false
  }
  if (name.length < 3) {
    nameError.value = 'Almeno 3 caratteri'
    return false
  }
  if (name.length > 15) {
    nameError.value = 'Massimo 15 caratteri'
    return false
  }
  // Regex: Solo lettere, numeri, trattini e underscore. Niente spazi o ^, $, @, ecc.
  if (!/^[a-zA-Z0-9_-]+$/.test(name)) {
    nameError.value = 'Solo lettere, numeri, - e _ (senza spazi)'
    return false
  }
  nameError.value = ''
  return true
}

function validateCode() {
  if (!lobbyCode.value.trim()) {
    codeError.value = 'Seleziona una lobby dalla lista'
    return false
  }
  codeError.value = ''
  return true
}

// ---- AZIONI ----
async function createLobby() {
  if (!validateName()) return

  nameError.value = ''
  lobbyStore.error = null

  try {
    await lobbyStore.createLobby(playerName.value)
    router.push('/lobby')
  } catch (err) {
    nameError.value = err.message
    lobbyStore.error = null
  }
}

async function joinLobby() {
  const isNameValid = validateName()
  const isCodeValid = validateCode()
  if (!isNameValid || !isCodeValid) return

  nameError.value = ''
  codeError.value = ''
  lobbyStore.error = null

  try {
    await lobbyStore.joinLobby(lobbyCode.value, playerName.value)
    router.push('/lobby')
  } catch (err) {
    if (err.message.toLowerCase().includes("codice") || err.message.toLowerCase().includes("stanza")) {
      codeError.value = err.message
    } else {
      nameError.value = err.message
    }
    lobbyStore.error = null
  }
}

function selectMode(selected) {
  mode.value = selected
  nameError.value = ''
  codeError.value = ''
  lobbyStore.error && (lobbyStore.error = null)

  if (selected === 'join') {
    lobbyCode.value = ''
    lobbySearch.value = ''
    startLobbyPolling()
  } else {
    stopLobbyPolling()
  }
}
</script>

<template>
  <div class="home-bg">
    <div class="bg-layer">
      <img :src="castleBg" alt="" class="bg-image" />
      <div class="bg-scrim"></div>

      <!-- nebbia animata SOPRA l'immagine per darle movimento -->
      <div class="fog fog-1"></div>
      <div class="fog fog-2"></div>
    </div>

    <main class="home-wrap">
      <header class="hero" :class="{ 'hero--shrink': mode !== null }">
        <h1 class="title">
          <span class="title-main">LUPUS</span>
          <span class="title-sub">in fabula</span>
        </h1>
        <p class="tagline" v-if="mode === null">
          Un gioco di inganni, alleanze e sopravvivenza
        </p>
      </header>

      <Transition name="fade">
        <div v-if="lobbyStore.error && mode === null" class="server-error" style="width: 100%; text-align: center; margin-bottom: 1rem;">
          {{ lobbyStore.error }}
        </div>
      </Transition>

      <section v-if="mode === null" class="mode-select">
        <button class="mode-card mode-card--create" @click="selectMode('create')">
          <span class="mode-icon">🏰</span>
          <span class="mode-label">Crea Partita</span>
          <span class="mode-desc">Apri una nuova lobby e invita i tuoi amici</span>
        </button>
        <div class="mode-divider">oppure</div>
        <button class="mode-card mode-card--join" @click="selectMode('join')">
          <span class="mode-icon">🚪</span>
          <span class="mode-label">Entra in Lobby</span>
          <span class="mode-desc">Hai un codice? Unisciti alla partita</span>
        </button>
      </section>

      <section v-else-if="mode === 'create'" class="form-panel">
        <button class="back-btn" @click="selectMode(null)">← indietro</button>
        <h2 class="form-title">Nuova Partita</h2>

        <div class="field">
          <label class="field-label">Il tuo nome</label>
          <input
            v-model="playerName"
            class="field-input"
            :class="{ 'field-input--error': nameError }"
            type="text"
            placeholder="Come ti chiami?"
            maxlength="20"
            @keyup.enter="createLobby"
            @input="nameError = ''"
            autofocus
          />
          <span v-if="nameError" class="field-error">{{ nameError }}</span>
        </div>

        <span v-if="lobbyStore.error" class="server-error">{{ lobbyStore.error }}</span>

        <button class="btn-primary" @click="createLobby" :disabled="isLoading">
          <span v-if="isLoading" class="spinner"></span>
          <span v-else>🐺 Crea la Lobby</span>
        </button>
      </section>

      <section v-else-if="mode === 'join'" class="form-panel">
        <button class="back-btn" @click="selectMode(null)">← indietro</button>
        <h2 class="form-title">Entra in Partita</h2>

        <div class="field">
          <label class="field-label">Il tuo nome</label>
          <input
            v-model="playerName"
            class="field-input"
            :class="{ 'field-input--error': nameError }"
            type="text"
            placeholder="Come ti chiami?"
            maxlength="20"
            @input="nameError = ''"
            autofocus
          />
          <span v-if="nameError" class="field-error">{{ nameError }}</span>
        </div>

        <div class="field">
          <div class="field-label-row">
            <label class="field-label">Lobby disponibili</label>
            <button
              class="refresh-btn"
              type="button"
              :disabled="loadingLobbies"
              @click="fetchLobbies"
              title="Aggiorna la lista"
            >
              <span :class="{ 'refresh-spin': loadingLobbies }">⟳</span>
            </button>
          </div>

          <input
            v-model="lobbySearch"
            class="field-input"
            type="text"
            placeholder="🔍 Cerca per codice o host…"
          />

          <div class="lobby-list" :class="{ 'lobby-list--error': codeError }">
            <p v-if="lobbiesError" class="lobby-empty">{{ lobbiesError }}</p>
            <p v-else-if="loadingLobbies && lobbies.length === 0" class="lobby-empty">
              Caricamento lobby…
            </p>
            <p v-else-if="filteredLobbies.length === 0" class="lobby-empty">
              {{ lobbies.length === 0 ? 'Nessuna lobby aperta al momento' : 'Nessun risultato per la ricerca' }}
            </p>

            <button
              v-for="lobby in filteredLobbies"
              :key="lobby.code"
              type="button"
              class="lobby-item"
              :class="{ 'lobby-item--active': lobby.code === lobbyCode }"
              @click="selectLobby(lobby.code)"
              @dblclick="joinLobby"
            >
              <span class="lobby-item-code">{{ lobby.code }}</span>
              <span class="lobby-item-meta">
                <span class="lobby-item-host">👑 {{ lobby.host }}</span>
                <span class="lobby-item-count">👥 {{ lobby.player_count }}</span>
              </span>
            </button>
          </div>
          <span v-if="codeError" class="field-error">{{ codeError }}</span>
        </div>

        <span v-if="lobbyStore.error" class="server-error">{{ lobbyStore.error }}</span>

        <button class="btn-primary" @click="joinLobby" :disabled="isLoading || !lobbyCode">
          <span v-if="isLoading" class="spinner"></span>
          <span v-else>🚪 Entra nella Lobby</span>
        </button>
      </section>
    </main>
  </div>

  <!-- Teleport al body per evitare overflow:hidden del parent -->
  <Teleport to="body">
    <button class="rules-btn" @click="showRules = true" title="Regole del gioco">📜</button>

    <Transition name="modal">
      <div v-if="showRules" class="rules-overlay" @click.self="showRules = false">
        <div class="rules-modal">
          <button class="rules-close" @click="showRules = false">✕</button>
          <h2 class="rules-title">📜 Regole del Gioco</h2>

          <div class="rules-body">
            <section class="rules-section">
              <h3>🎯 Obiettivo</h3>
              <p><strong>Villagers:</strong> scoprono e eliminano tutti i lupi durante le votazioni diurne.</p>
              <p><strong>Lupi:</strong> eliminano i villagers di notte finché sono in maggioranza.</p>
            </section>

            <section class="rules-section">
              <h3>👥 Ruoli</h3>
              <ul>
                <li><strong>🐺 Lupo</strong> — ogni notte sceglie insieme agli altri lupi una vittima da eliminare.</li>
                <li><strong>🔮 Veggente</strong> — ogni notte può scoprire il ruolo segreto di un giocatore.</li>
                <li><strong>🌾 Contadino</strong> — non ha poteri speciali, ma vota di giorno per eliminare i sospetti.</li>
              </ul>
            </section>

            <section class="rules-section">
              <h3>🌙 Fasi di gioco</h3>
              <ol>
                <li><strong>Notte</strong> — i lupi votano una vittima; il veggente (se vivo) indaga un giocatore.</li>
                <li><strong>Giorno</strong> — viene rivelata la vittima notturna; si discute.</li>
                <li><strong>Votazione</strong> — tutti i vivi votano chi eliminare. Chi riceve più voti viene espulso.</li>
              </ol>
            </section>

            <section class="rules-section">
              <h3>⚡ Regole speciali</h3>
              <ul>
                <li>Se un giocatore si disconnette, la partita si mette in <strong>pausa per 20 secondi</strong>. Se non rientra viene eliminato.</li>
                <li>In caso di <strong>parità</strong> nei voti di giorno, nessuno viene eliminato.</li>
                <li>I lupi vedono l'identità dei propri compagni lupo.</li>
              </ul>
            </section>

            <section class="rules-section">
              <h3>🏆 Vittoria</h3>
              <p>I <strong>villagers</strong> vincono quando tutti i lupi sono eliminati.</p>
              <p>I <strong>lupi</strong> vincono quando uguagliano o superano il numero dei villagers in vita.</p>
            </section>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Crimson+Text:ital,wght@0,400;0,600;1,400&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

/* ---- SFONDO ---- */
.home-bg {
  min-height: 100vh;
  background: #06060e;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'Crimson Text', serif;
  color: #e8e0d5;
  position: relative;
  overflow: hidden;
}

.bg-layer {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
}
.bg-image {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
}

/* Velo scuro per far risaltare i testi sopra l'immagine */
.bg-scrim {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to bottom,
    rgba(6,6,14,0.55) 0%,
    rgba(6,6,14,0.15) 35%,
    rgba(6,6,14,0.65) 100%
  );
}

/* Nebbia */
.fog {
  position: absolute;
  bottom: 0;
  width: 200%;
  height: 300px;
  background: linear-gradient(to top, rgba(20,10,30,0.8) 0%, transparent 100%);
  border-radius: 50%;
}
.fog-1 {
  left: -50%;
  animation: fogMove 20s ease-in-out infinite alternate;
}
.fog-2 {
  left: -20%;
  opacity: 0.5;
  animation: fogMove 28s ease-in-out infinite alternate-reverse;
}
@keyframes fogMove {
  from { transform: translateX(0) scaleY(1); }
  to   { transform: translateX(8%) scaleY(1.2); }
}

/* ---- LAYOUT ---- */
.home-wrap {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 480px;
  padding: 2rem 1.5rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2.5rem;
}

/* ---- HERO ---- */
.hero {
  text-align: center;
  transition: all 0.4s ease;
}
.hero--shrink .tagline { display: none; }
.hero--shrink .title-main { font-size: 2rem; }

.title {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.1rem;
}
.title-main {
  font-family: 'Cinzel', serif;
  font-size: 3.5rem;
  font-weight: 900;
  letter-spacing: 0.15em;
  color: #e8c87a;
  line-height: 1;
  text-shadow: 0 2px 14px rgba(0,0,0,0.9), 0 0 30px rgba(232,200,122,0.3);
  transition: font-size 0.4s;
}
.title-sub {
  font-family: 'Cinzel', serif;
  font-size: 0.85rem;
  letter-spacing: 0.35em;
  color: rgba(232,200,122,0.75);
  text-transform: uppercase;
  font-weight: 700;
  text-shadow: 0 1px 8px rgba(0,0,0,0.8);
}

.tagline {
  margin-top: 1rem;
  font-size: 1.1rem;
  font-style: italic;
  color: rgba(232,224,213,0.85);
  letter-spacing: 0.02em;
  text-shadow: 0 1px 8px rgba(0,0,0,0.85);
}

/* ---- MODE SELECT ---- */
.mode-select {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0;
  animation: fadeUp 0.5s ease both;
}
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(20px); }
  to   { opacity: 1; transform: translateY(0); }
}

.mode-card {
  width: 100%;
  background: rgba(8, 8, 18, 0.55);
  backdrop-filter: blur(4px);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 14px;
  padding: 1.5rem 1.8rem;
  cursor: pointer;
  text-align: left;
  display: grid;
  grid-template-columns: 2.5rem 1fr;
  grid-template-rows: auto auto;
  column-gap: 1rem;
  align-items: center;
  transition: all 0.25s ease;
  color: #e8e0d5;
}
.mode-card:hover {
  border-color: rgba(232,200,122,0.35);
  background: rgba(232,200,122,0.08);
  transform: translateY(-2px);
}
.mode-card--create:hover { box-shadow: 0 8px 30px rgba(139,0,0,0.25); }
.mode-card--join:hover   { box-shadow: 0 8px 30px rgba(232,200,122,0.15); }

.mode-icon {
  font-size: 2rem;
  grid-row: 1 / 3;
  align-self: center;
}
.mode-label {
  font-family: 'Cinzel', serif;
  font-size: 1rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: #e8c87a;
}
.mode-desc {
  font-size: 0.85rem;
  color: rgba(232,224,213,0.8);
  font-style: italic;
}

.mode-divider {
  font-size: 0.8rem;
  color: rgba(232,224,213,0.6);
  letter-spacing: 0.15em;
  text-transform: uppercase;
  padding: 0.8rem 0;
  text-shadow: 0 1px 6px rgba(0,0,0,0.8);
}

/* ---- FORM PANEL ---- */
.form-panel {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 1.2rem;
  background: rgba(8, 8, 18, 0.6);
  border: 1px solid rgba(232, 200, 122, 0.12);
  border-radius: 18px;
  padding: 1.6rem;
  backdrop-filter: blur(6px);
  animation: fadeUp 0.35s ease both;
}

.back-btn {
  background: none;
  border: none;
  color: rgba(232,224,213,0.6);
  font-family: 'Crimson Text', serif;
  font-size: 0.9rem;
  cursor: pointer;
  padding: 0;
  align-self: flex-start;
  transition: color 0.2s;
  letter-spacing: 0.05em;
}
.back-btn:hover { color: rgba(232,224,213,0.9); }

.form-title {
  font-family: 'Cinzel', serif;
  font-size: 1.3rem;
  font-weight: 700;
  color: #e8c87a;
  letter-spacing: 0.08em;
}

.field { display: flex; flex-direction: column; gap: 0.4rem; }

.field-label {
  font-size: 0.75rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: rgba(232,200,122,0.85);
}

.field-input {
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 10px;
  padding: 0.85rem 1rem;
  color: #e8e0d5;
  font-family: 'Crimson Text', serif;
  font-size: 1.05rem;
  outline: none;
  transition: border-color 0.2s, background 0.2s;
}
.field-input::placeholder { color: rgba(232,224,213,0.5); }
.field-input:focus {
  border-color: rgba(232,200,122,0.4);
  background: rgba(255,255,255,0.06);
}
.field-input--error { border-color: rgba(220,60,60,0.5) !important; }
.field-input--code {
  font-family: 'Cinzel', serif;
  font-size: 1.1rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

/* ---- LOBBY BROWSER ---- */
.field-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.refresh-btn {
  background: none;
  border: none;
  color: rgba(232,200,122,0.85);
  font-size: 1.1rem;
  cursor: pointer;
  padding: 0 0.2rem;
  line-height: 1;
  transition: color 0.2s, transform 0.2s;
}
.refresh-btn:hover:not(:disabled) { color: #e8c87a; transform: rotate(90deg); }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.refresh-spin { display: inline-block; animation: spin 0.8s linear infinite; }

.lobby-list {
  margin-top: 0.5rem;
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.4rem;
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 12px;
  background: rgba(0,0,0,0.2);
  scrollbar-width: thin;
  scrollbar-color: rgba(232,200,122,0.4) transparent;
}
.lobby-list--error { border-color: rgba(220,60,60,0.5); }
.lobby-list::-webkit-scrollbar { width: 8px; }
.lobby-list::-webkit-scrollbar-thumb {
  background: rgba(232,200,122,0.35);
  border-radius: 4px;
}

.lobby-empty {
  text-align: center;
  color: rgba(232,224,213,0.6);
  font-style: italic;
  font-size: 0.9rem;
  padding: 1.4rem 0.5rem;
}

.lobby-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.8rem;
  width: 100%;
  text-align: left;
  padding: 0.7rem 0.9rem;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 10px;
  background: rgba(255,255,255,0.03);
  color: #e8e0d5;
  cursor: pointer;
  font-family: 'Crimson Text', serif;
  transition: all 0.18s ease;
}
.lobby-item:hover {
  border-color: rgba(232,200,122,0.35);
  background: rgba(232,200,122,0.08);
}
.lobby-item--active {
  border-color: rgba(232,200,122,0.7);
  background: rgba(232,200,122,0.14);
  box-shadow: 0 0 0 1px rgba(232,200,122,0.4) inset;
}

.lobby-item-code {
  font-family: 'Cinzel', serif;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #e8c87a;
  text-transform: uppercase;
}
.lobby-item-meta {
  display: flex;
  align-items: center;
  gap: 0.9rem;
  font-size: 0.85rem;
  color: rgba(232,224,213,0.85);
  white-space: nowrap;
}

.field-error {
  font-size: 0.85rem;
  color: #ff8a8a;
  font-style: italic;
  text-shadow: 0 1px 4px rgba(0,0,0,0.9);
}

.server-error {
  font-size: 0.9rem;
  color: #ffd9d9;
  background: rgba(60, 8, 8, 0.92);
  border: 1px solid rgba(248, 113, 113, 0.6);
  border-radius: 10px;
  padding: 0.8rem 1rem;
  font-style: italic;
  text-align: center;
  box-shadow: 0 4px 20px rgba(0,0,0,0.5);
  backdrop-filter: blur(4px);
}

/* ---- PULSANTE PRINCIPALE ---- */
.btn-primary {
  width: 100%;
  padding: 1rem;
  background: linear-gradient(135deg, #7a0000, #c0392b);
  border: none;
  border-radius: 12px;
  color: #fff;
  font-family: 'Cinzel', serif;
  font-size: 1rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  cursor: pointer;
  box-shadow: 0 4px 24px rgba(139,0,0,0.4);
  transition: all 0.25s ease;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 52px;
  margin-top: 0.4rem;
}
.btn-primary:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 8px 32px rgba(139,0,0,0.55);
}
.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

/* ---- SPINNER ---- */
.spinner {
  width: 20px; height: 20px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  display: inline-block;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ---- PULSANTE REGOLE ---- */
.rules-btn {
  position: fixed;
  top: 1.2rem;
  right: 1.4rem;
  z-index: 100;
  background: rgba(8, 8, 18, 0.65);
  border: 1px solid rgba(232, 200, 122, 0.3);
  border-radius: 50%;
  width: 48px;
  height: 48px;
  font-size: 1.4rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(6px);
  transition: all 0.25s ease;
  box-shadow: 0 2px 12px rgba(0,0,0,0.4);
}
.rules-btn:hover {
  border-color: rgba(232, 200, 122, 0.7);
  background: rgba(232, 200, 122, 0.12);
  transform: scale(1.08);
}

/* ---- MODAL REGOLE ---- */
.rules-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
}

.rules-modal {
  position: relative;
  background: rgba(10, 8, 22, 0.97);
  border: 1px solid rgba(232, 200, 122, 0.25);
  border-radius: 20px;
  padding: 2rem 2rem 1.8rem;
  max-width: 540px;
  width: 100%;
  max-height: 80vh;
  overflow-y: auto;
  box-shadow: 0 16px 60px rgba(0,0,0,0.7), 0 0 0 1px rgba(232,200,122,0.1) inset;
  scrollbar-width: thin;
  scrollbar-color: rgba(232,200,122,0.3) transparent;
}
.rules-modal::-webkit-scrollbar { width: 6px; }
.rules-modal::-webkit-scrollbar-thumb {
  background: rgba(232,200,122,0.3);
  border-radius: 3px;
}

.rules-close {
  position: absolute;
  top: 1rem;
  right: 1rem;
  background: none;
  border: none;
  color: rgba(232,224,213,0.6);
  font-size: 1.1rem;
  cursor: pointer;
  line-height: 1;
  padding: 0.2rem 0.4rem;
  border-radius: 6px;
  transition: color 0.2s, background 0.2s;
}
.rules-close:hover {
  color: #e8c87a;
  background: rgba(232,200,122,0.1);
}

.rules-title {
  font-family: 'Cinzel', serif;
  font-size: 1.4rem;
  font-weight: 700;
  color: #e8c87a;
  letter-spacing: 0.06em;
  margin-bottom: 1.4rem;
  text-align: center;
}

.rules-body {
  display: flex;
  flex-direction: column;
  gap: 1.2rem;
}

.rules-section h3 {
  font-family: 'Cinzel', serif;
  font-size: 0.9rem;
  font-weight: 700;
  color: #e8c87a;
  letter-spacing: 0.08em;
  margin-bottom: 0.5rem;
  border-bottom: 1px solid rgba(232,200,122,0.15);
  padding-bottom: 0.3rem;
}

.rules-section p,
.rules-section li {
  font-size: 0.95rem;
  color: rgba(232,224,213,0.9);
  line-height: 1.6;
  margin-bottom: 0.3rem;
}

.rules-section ul,
.rules-section ol {
  padding-left: 1.3rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

/* ---- ANIMAZIONE MODAL ---- */
.modal-enter-active, .modal-leave-active {
  transition: opacity 0.25s ease;
}
.modal-enter-active .rules-modal,
.modal-leave-active .rules-modal {
  transition: transform 0.25s ease, opacity 0.25s ease;
}
.modal-enter-from, .modal-leave-to {
  opacity: 0;
}
.modal-enter-from .rules-modal,
.modal-leave-to .rules-modal {
  transform: scale(0.93) translateY(12px);
  opacity: 0;
}
</style>