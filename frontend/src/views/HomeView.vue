<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useLobbyStore } from '@/stores/lobbyStore'

const router = useRouter()
const lobbyStore = useLobbyStore()

// ---- STATE UI ----
const mode = ref(null)          // null | 'create' | 'join'
const playerName = ref('')
const lobbyCode = ref('')
const nameError = ref('')
const codeError = ref('')

const isLoading = computed(() => lobbyStore.isLoading)

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
    codeError.value = 'Inserisci il codice lobby'
    return false
  }
  codeError.value = ''
  return true
}

// ---- AZIONI ----
async function createLobby() {
  // 1. Controllo locale immediato
  if (!validateName()) return

  // 2. Pulizia errori
  nameError.value = ''
  lobbyStore.error = null

  try {
    // 3. Creazione tramite lo store
    await lobbyStore.createLobby(playerName.value)
    router.push('/lobby')
  } catch (err) {
    // 4. Se lo store si lamenta, mostriamo l'errore
    nameError.value = err.message
    lobbyStore.error = null
  }
}

async function joinLobby() {
  // Uso l'operatore || invece di | per un corretto cortocircuito logico
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
    // Se l'errore contiene la parola "codice" o "stanza", lo mettiamo sotto il codice
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
}
</script>

<template>
  <div class="home-bg">
    <div class="bg-layer">
      <div class="moon"></div>
      <div v-for="n in 30" :key="n" class="star" :style="{ '--i': n }"></div>
      <div class="fog fog-1"></div>
      <div class="fog fog-2"></div>
    </div>

    <main class="home-wrap">
      <header class="hero" :class="{ 'hero--shrink': mode !== null }">
        <div class="wolf-icon">🐺</div>
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
          <label class="field-label">Codice Lobby</label>
          <input
            v-model="lobbyCode"
            class="field-input field-input--code"
            :class="{ 'field-input--error': codeError }"
            type="text"
            placeholder="es. WOLF-4821"
            maxlength="12"
            @keyup.enter="joinLobby"
            @input="codeError = ''"
          />
          <span v-if="codeError" class="field-error">{{ codeError }}</span>
        </div>

        <span v-if="lobbyStore.error" class="server-error">{{ lobbyStore.error }}</span>

        <button class="btn-primary" @click="joinLobby" :disabled="isLoading">
          <span v-if="isLoading" class="spinner"></span>
          <span v-else>🚪 Entra nella Lobby</span>
        </button>
      </section>
    </main>
  </div>
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

/* Luna */
.moon {
  position: absolute;
  top: 8%;
  right: 12%;
  width: 90px;
  height: 90px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, #fff8e7, #e8c87a 40%, #b8942a);
  box-shadow: 0 0 40px rgba(232,200,122,0.3), 0 0 80px rgba(232,200,122,0.1);
  animation: moonPulse 6s ease-in-out infinite alternate;
}
@keyframes moonPulse {
  from { box-shadow: 0 0 40px rgba(232,200,122,0.3), 0 0 80px rgba(232,200,122,0.1); }
  to   { box-shadow: 0 0 60px rgba(232,200,122,0.5), 0 0 120px rgba(232,200,122,0.2); }
}

/* Stelle */
.star {
  position: absolute;
  width: 2px; height: 2px;
  border-radius: 50%;
  background: #fff;
  left: calc(var(--i) * 3.2% + 2%);
  top:  calc(var(--i) * 2.8% + 5%);
  opacity: calc(0.2 + (var(--i) * 0.025));
  animation: twinkle calc(2s + var(--i) * 0.3s) ease-in-out infinite alternate;
}
@keyframes twinkle {
  from { opacity: 0.1; transform: scale(1); }
  to   { opacity: 0.9; transform: scale(1.5); }
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
.hero--shrink .wolf-icon { font-size: 2.5rem; }
.hero--shrink .title-main { font-size: 2rem; }

.wolf-icon {
  font-size: 4.5rem;
  display: block;
  margin-bottom: 0.5rem;
  filter: drop-shadow(0 0 20px rgba(232,200,122,0.4));
  animation: wolfFloat 4s ease-in-out infinite alternate;
}
@keyframes wolfFloat {
  from { transform: translateY(0); }
  to   { transform: translateY(-8px); }
}

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
  text-shadow: 0 0 30px rgba(232,200,122,0.3);
  transition: font-size 0.4s;
}
.title-sub {
  font-family: 'Cinzel', serif;
  font-size: 0.85rem;
  letter-spacing: 0.35em;
  color: rgba(232,200,122,0.45);
  text-transform: uppercase;
  font-weight: 700;
}

.tagline {
  margin-top: 1rem;
  font-size: 1.1rem;
  font-style: italic;
  color: rgba(232,224,213,0.45);
  letter-spacing: 0.02em;
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
  background: rgba(255,255,255,0.03);
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
  background: rgba(232,200,122,0.05);
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
  color: rgba(232,224,213,0.45);
  font-style: italic;
}

.mode-divider {
  font-size: 0.8rem;
  color: rgba(232,224,213,0.25);
  letter-spacing: 0.15em;
  text-transform: uppercase;
  padding: 0.8rem 0;
}

/* ---- FORM PANEL ---- */
.form-panel {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 1.2rem;
  animation: fadeUp 0.35s ease both;
}

.back-btn {
  background: none;
  border: none;
  color: rgba(232,224,213,0.35);
  font-family: 'Crimson Text', serif;
  font-size: 0.9rem;
  cursor: pointer;
  padding: 0;
  align-self: flex-start;
  transition: color 0.2s;
  letter-spacing: 0.05em;
}
.back-btn:hover { color: rgba(232,224,213,0.7); }

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
  color: rgba(232,200,122,0.5);
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
.field-input::placeholder { color: rgba(232,224,213,0.25); }
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

.field-error {
  font-size: 0.8rem;
  color: #e05555;
  font-style: italic;
}

.server-error {
  font-size: 0.85rem;
  color: #e05555;
  background: rgba(220,60,60,0.08);
  border: 1px solid rgba(220,60,60,0.2);
  border-radius: 8px;
  padding: 0.6rem 0.9rem;
  font-style: italic;
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
</style>