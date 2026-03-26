<script setup>
/**
 * PlayerCard.vue
 * Carta da gioco SVG per un giocatore.
 * Usata in LobbyView (retro misterioso) e GameView (con ruolo rivelato).
 *
 * Props:
 *   player    — { player_id, username, isHost?, ready?, alive?, role? }
 *   isMe      — true se è il giocatore corrente
 *   showRole  — true per mostrare il ruolo rivelato (fase ENDED)
 *   canKick   — true se l'host può rimuovere questo giocatore
 */
import PlayerAvatar from '@/components/PlayerAvatar.vue'

const props = defineProps({
  player:   { type: Object,  required: true },
  isMe:     { type: Boolean, default: false },
  showRole: { type: Boolean, default: false },
  canKick:  { type: Boolean, default: false },
})

const emit = defineEmits(['kick'])

const cardPalettes = [
  { bg: '#1a0a2e', border: '#7c3aed' },
  { bg: '#0a1a0a', border: '#16a34a' },
  { bg: '#1a0a0a', border: '#dc2626' },
  { bg: '#0a0f1a', border: '#2563eb' },
  { bg: '#1a150a', border: '#d97706' },
  { bg: '#0f0a1a', border: '#9333ea' },
]

function palette() {
  const id  = props.player.player_id ?? props.player.id ?? 'x'
  const idx = id.charCodeAt(id.length - 1) % cardPalettes.length
  return cardPalettes[idx]
}

function playerId() {
  return props.player.player_id ?? props.player.id ?? 'x'
}

function username() {
  return props.player.username ?? props.player.name ?? '?'
}

const roleIcons = { VILLAGER: '🧑‍🌾', WOLF: '🐺', SEER: '🔮' }
function roleIcon() {
  return roleIcons[props.player.role] ?? '?'
}

function isAlive() {
  // In lobby non c'è alive — tutti sono vivi
  return props.player.alive !== false
}
</script>

<template>
  <div
    class="pcard"
    :class="{
      'pcard--me':    isMe,
      'pcard--dead':  !isAlive(),
      'pcard--ready': player.ready,
    }"
    :style="{
      '--cb': palette().bg,
      '--cc': palette().border,
    }"
  >
    <!-- SVG retro carta -->
    <div class="pcard-art">
      <svg viewBox="0 0 120 160" xmlns="http://www.w3.org/2000/svg">
        <rect width="120" height="160" rx="8" :fill="palette().bg"/>
        <rect x="6" y="6" width="108" height="148" rx="5"
          fill="none" :stroke="palette().border" stroke-width="0.8" opacity="0.4"/>

        <!-- Ruolo rivelato -->
        <template v-if="showRole && player.role">
          <text x="60" y="85" text-anchor="middle"
            font-size="40" :fill="palette().border" opacity="0.8">
            {{ roleIcon() }}
          </text>
          <text x="60" y="145" text-anchor="middle"
            font-size="7" font-family="Georgia,serif" letter-spacing="1.5"
            :fill="palette().border" opacity="0.5">
            {{ player.role }}
          </text>
        </template>

        <!-- Retro misterioso -->
        <template v-else>
          <circle cx="60" cy="72" r="26" fill="none"
            :stroke="palette().border" stroke-width="1" opacity="0.25"/>
          <circle cx="60" cy="72" r="18" fill="none"
            :stroke="palette().border" stroke-width="0.5" opacity="0.15"/>
          <text x="60" y="85" text-anchor="middle"
            font-size="36" font-family="Georgia,serif"
            :fill="palette().border" opacity="0.55">?</text>
          <text x="60" y="145" text-anchor="middle"
            font-size="6.5" font-family="Georgia,serif" letter-spacing="1.5"
            :fill="palette().border" opacity="0.45">RUOLO SEGRETO</text>
        </template>

        <!-- Angoli decorativi -->
        <text x="11"  y="22"  font-size="9" font-family="serif" :fill="palette().border" opacity="0.6">♦</text>
        <text x="109" y="22"  font-size="9" font-family="serif" :fill="palette().border" opacity="0.6" text-anchor="end">♦</text>
        <text x="11"  y="154" font-size="9" font-family="serif" :fill="palette().border" opacity="0.6">♦</text>
        <text x="109" y="154" font-size="9" font-family="serif" :fill="palette().border" opacity="0.6" text-anchor="end">♦</text>

        <!-- Linee ornamentali -->
        <line x1="20" y1="28"  x2="100" y2="28"  :stroke="palette().border" stroke-width="0.4" opacity="0.25"/>
        <line x1="20" y1="130" x2="100" y2="130" :stroke="palette().border" stroke-width="0.4" opacity="0.25"/>

        <!-- Overlay morto -->
        <rect v-if="!isAlive()" width="120" height="160" rx="8" fill="rgba(0,0,0,0.55)"/>
        <text v-if="!isAlive()" x="60" y="88" text-anchor="middle"
          font-size="32" fill="rgba(255,255,255,0.25)">💀</text>
      </svg>
    </div>

    <!-- Footer carta -->
    <div class="pcard-footer">
      <PlayerAvatar
        :player-id="playerId()"
        :name="username()"
        size="sm"
        :dead="!isAlive()"
      />
      <div class="pcard-name">
        {{ username() }}
        <span v-if="player.isHost" class="pcard-tag host">👑</span>
        <span v-if="isMe"          class="pcard-tag me">tu</span>
      </div>
      <div class="pcard-status" :class="{ ready: player.ready, dead: !isAlive() }">
        <template v-if="!isAlive()">eliminato</template>
        <template v-else-if="player.ready !== undefined">
          {{ player.ready ? '● pronto' : '○ attesa' }}
        </template>
      </div>
    </div>

    <!-- Kick button (solo host, solo in lobby) -->
    <button
      v-if="canKick"
      class="pcard-kick"
      @click.stop="emit('kick', playerId())"
      title="Rimuovi"
    >✕</button>

    <!-- Glow se pronto -->
    <div v-if="player.ready && isAlive()" class="pcard-glow"></div>
  </div>
</template>

<style scoped>
.pcard {
  position: relative;
  border-radius: 10px;
  background: var(--cb, #0d0d14);
  border: 1px solid var(--cc, #1e1e2e);
  overflow: hidden;
  transition: transform 0.25s ease, box-shadow 0.25s ease;
  cursor: default;
}
.pcard:not(.pcard--dead):hover {
  transform: translateY(-6px) rotate(-0.8deg);
  box-shadow: 0 16px 32px rgba(0,0,0,0.6), 0 0 16px var(--cc);
}
.pcard--me   { border-color: #e8c87a !important; box-shadow: 0 0 14px rgba(232,200,122,0.2); }
.pcard--dead { opacity: 0.45; }

.pcard-art { width: 100%; }
.pcard-art svg { display: block; width: 100%; height: auto; }

.pcard-footer {
  padding: 0.5rem 0.5rem 0.65rem;
  background: rgba(0,0,0,0.45);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.2rem;
}
.pcard-name {
  font-size: 0.78rem;
  font-weight: 700;
  color: #e8e0d5;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  flex-wrap: wrap;
  justify-content: center;
  text-align: center;
}
.pcard-tag {
  font-size: 0.58rem;
  padding: 0.05rem 0.3rem;
  border-radius: 8px;
}
.pcard-tag.host { background: rgba(232,200,122,0.12); color: #e8c87a; }
.pcard-tag.me   { background: rgba(100,180,255,0.1);  color: #90c8ff; }

.pcard-status {
  font-size: 0.65rem;
  color: rgba(232,224,213,0.35);
  letter-spacing: 0.03em;
}
.pcard-status.ready { color: #4ade80; }
.pcard-status.dead  { color: rgba(248,113,113,0.4); }

.pcard-kick {
  position: absolute;
  top: 5px; right: 5px;
  background: rgba(220,38,38,0.12);
  border: 1px solid rgba(220,38,38,0.25);
  color: #f87171;
  width: 18px; height: 18px;
  border-radius: 50%;
  font-size: 0.6rem;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  opacity: 0;
  transition: opacity 0.2s;
}
.pcard:hover .pcard-kick { opacity: 1; }

.pcard-glow {
  position: absolute; inset: 0;
  border-radius: 10px;
  box-shadow: inset 0 0 0 1px var(--cc);
  pointer-events: none;
  animation: gp 2s ease-in-out infinite alternate;
}
@keyframes gp { from { opacity: 0.3; } to { opacity: 1; } }
</style>
