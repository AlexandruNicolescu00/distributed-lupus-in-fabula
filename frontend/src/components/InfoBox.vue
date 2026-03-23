<script setup>
/**
 * InfoBox.vue
 * Box riusabile con titolo e lista di righe chiave/valore.
 * Usato in LobbyView (impostazioni partita) e GameView (round, conteggi).
 *
 * Props:
 *   title — titolo del box (uppercase automatico via CSS)
 *   rows  — array di { label: string, value: string|number }
 *
 * Esempio:
 *   <InfoBox
 *     title="Impostazioni"
 *     :rows="[
 *       { label: 'Min giocatori', value: 5 },
 *       { label: 'Max giocatori', value: 12 },
 *       { label: 'Pronti', value: '3 / 5' },
 *     ]"
 *   />
 */
defineProps({
  title: { type: String, required: true },
  rows:  { type: Array,  required: true },
})
</script>

<template>
  <div class="info-box">
    <div class="info-box__title">{{ title }}</div>
    <div
      v-for="(row, i) in rows"
      :key="i"
      class="info-box__row"
    >
      <span class="info-box__label">{{ row.label }}</span>
      <strong class="info-box__value">{{ row.value }}</strong>
    </div>
  </div>
</template>

<style scoped>
.info-box {
  background: rgba(255,255,255,0.025);
  border: 1px solid rgba(255,255,255,0.06);
  border-radius: 12px;
  padding: 1rem;
}

.info-box__title {
  font-family: 'Cinzel', serif;
  font-size: 0.68rem;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: rgba(232,200,122,0.4);
  margin-bottom: 0.65rem;
}

.info-box__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.82rem;
  padding: 0.28rem 0;
  border-bottom: 1px solid rgba(255,255,255,0.04);
  color: rgba(232,224,213,0.55);
}
.info-box__row:last-child { border-bottom: none; }

.info-box__label { flex: 1; }
.info-box__value { color: #e8e0d5; font-weight: 700; }
</style>
