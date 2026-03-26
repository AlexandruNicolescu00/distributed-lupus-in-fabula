<script setup>
/**
 * PlayerAvatar.vue
 * Cerchio con iniziali e colore deterministico basato sull'id.
 *
 * Props:
 *   playerId  — stringa usata per calcolare il colore
 *   name      — nome del giocatore (usa le prime 2 lettere)
 *   size      — 'sm' | 'md' | 'lg' (default: 'md')
 *   dead      — se true mostra avatar in grigio
 */
const props = defineProps({
  playerId: { type: String, required: true },
  name:     { type: String, required: true },
  size:     { type: String, default: 'md' },
  dead:     { type: Boolean, default: false },
})

const avatarColors = [
  '#7c3aed', '#16a34a', '#dc2626',
  '#2563eb', '#d97706', '#9333ea',
]

function color() {
  if (props.dead) return '#2a2a3a'
  const idx = props.playerId.charCodeAt(props.playerId.length - 1) % avatarColors.length
  return avatarColors[idx]
}

function initials() {
  return (props.name ?? '?').slice(0, 2).toUpperCase()
}
</script>

<template>
  <div
    class="avatar"
    :class="`avatar--${size}`"
    :style="{ background: color() }"
  >
    {{ initials() }}
  </div>
</template>

<style scoped>
.avatar {
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'Cinzel', serif;
  font-weight: 700;
  color: #fff;
  flex-shrink: 0;
  transition: background 0.3s;
}

.avatar--sm { width: 24px; height: 24px; font-size: 0.55rem; }
.avatar--md { width: 32px; height: 32px; font-size: 0.65rem; }
.avatar--lg { width: 44px; height: 44px; font-size: 0.85rem; }
</style>
