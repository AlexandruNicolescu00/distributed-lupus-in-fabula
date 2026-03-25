<script setup>
/**
 * PhaseTimer.vue
 * Timer circolare con conto alla rovescia.
 * Legge secondsLeft e timerProgress direttamente dal gameStore.
 *
 * Props:
 *   size     — 'sm' | 'md' | 'lg' (default: 'md')
 *   color    — colore dell'anello (default: '#e8c87a')
 *   showText — mostra il testo HH:MM dentro l'anello (default: true)
 */
import { computed } from 'vue'
import { useGameStore } from '@/stores/gameStore'

const props = defineProps({
  size:     { type: String,  default: 'md' },
  color:    { type: String,  default: '#e8c87a' },
  showText: { type: Boolean, default: true },
})

const game = useGameStore()

const formattedTime = computed(() => {
  const s = game.secondsLeft ?? 0
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
})

// Soglie di colore: giallo → arancio → rosso quando il tempo scarseggia
const ringColor = computed(() => {
  const s = game.secondsLeft ?? 0
  if (s <= 10) return '#f87171'   // rosso — urgente
  if (s <= 20) return '#fb923c'   // arancio — attenzione
  return props.color              // colore base dalla prop
})

const sizeMap = {
  sm: { box: 32,  r: 12.7, stroke: 2,   fontSize: '0.5rem'  },
  md: { box: 44,  r: 17.2, stroke: 2.5, fontSize: '0.62rem' },
  lg: { box: 64,  r: 25.5, stroke: 3,   fontSize: '0.8rem'  },
}

const dim = computed(() => sizeMap[props.size] ?? sizeMap.md)

// Circonferenza = 2πr — usata per stroke-dasharray
const circumference = computed(() => 2 * Math.PI * dim.value.r)

// Offset: 0 = pieno, circumference = vuoto
const dashOffset = computed(() => {
  const progress = game.timerProgress ?? 100
  return circumference.value * (1 - progress / 100)
})

const viewBox = computed(() => {
  const b = dim.value.box
  return `0 0 ${b} ${b}`
})

const center = computed(() => dim.value.box / 2)
</script>

<template>
  <div class="phase-timer" :class="`phase-timer--${size}`">
    <svg
      :viewBox="viewBox"
      xmlns="http://www.w3.org/2000/svg"
      class="timer-svg"
    >
      <!-- Traccia sfondo -->
      <circle
        :cx="center" :cy="center" :r="dim.r"
        fill="none"
        stroke="rgba(255,255,255,0.08)"
        :stroke-width="dim.stroke"
      />
      <!-- Anello progresso -->
      <circle
        :cx="center" :cy="center" :r="dim.r"
        fill="none"
        :stroke="ringColor"
        :stroke-width="dim.stroke"
        :stroke-dasharray="`${circumference} ${circumference}`"
        :stroke-dashoffset="dashOffset"
        stroke-linecap="round"
        :transform="`rotate(-90 ${center} ${center})`"
        style="transition: stroke-dashoffset 1s linear, stroke 0.5s ease"
      />
      <!-- Testo centrale -->
      <text
        v-if="showText"
        :x="center" :y="center"
        text-anchor="middle"
        dominant-baseline="central"
        :font-size="dim.fontSize"
        font-family="'Cinzel', serif"
        font-weight="700"
        fill="#e8e0d5"
      >{{ formattedTime }}</text>
    </svg>
  </div>
</template>

<style scoped>
.phase-timer { display: inline-flex; align-items: center; justify-content: center; }
.timer-svg   { display: block; }

.phase-timer--sm { width: 32px;  height: 32px;  }
.phase-timer--md { width: 44px;  height: 44px;  }
.phase-timer--lg { width: 64px;  height: 64px;  }
</style>
