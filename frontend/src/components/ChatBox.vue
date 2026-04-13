<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useChatStore } from '@/stores/chatStore'
import { useGameStore } from '@/stores/gameStore'

const chat = useChatStore()
const game = useGameStore()

const chatInput = ref('')
const messagesEl = ref(null)

watch(
  () => chat.visibleMessages.length,
  async () => {
    await nextTick()
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  }
)

const channelLabel = computed(() => {
  if (chat.activeChannel === chat.CHANNELS.WOLVES) return 'Lupi'
  if (chat.activeChannel === chat.CHANNELS.DEAD) return 'Spettatori'
  return 'Globale'
})

function formatTime(iso) {
  try {
    const date = new Date(iso)
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  } catch {
    return ''
  }
}

function isMe(msg) {
  return msg.senderId === game.currentPlayerId
}

function send() {
  if (!chatInput.value.trim() || !chat.canChat) return
  chat.sendMessage(chatInput.value.trim())
  chatInput.value = ''
}
</script>

<template>
  <div class="chatbox">
    <div class="chatbox-header">
      <span class="chatbox-title">Chat</span>
      <span class="chatbox-channel" :class="`channel--${chat.activeChannel}`">
        {{ channelLabel }}
      </span>
    </div>

    <div ref="messagesEl" class="chatbox-messages">
      <div v-if="chat.visibleMessages.length === 0" class="chatbox-empty">
        Nessun messaggio ancora...
      </div>

      <div
        v-for="msg in chat.visibleMessages"
        :key="msg.id"
        class="chatbox-msg"
        :class="{ 
          'chatbox-msg--me': isMe(msg),
          'msg--dead': msg.channel === 'dead'
        }"
      >
        <div class="msg-meta">
          <span class="msg-sender">
            <span v-if="msg.channel === 'dead'" class="ghost-icon">👻 </span>
            {{ isMe(msg) ? 'Tu' : msg.senderName }}
          </span>
          <span class="msg-time">{{ formatTime(msg.timestamp) }}</span>
        </div>
        <div class="msg-bubble">{{ msg.text }}</div>
      </div>
    </div>

    <div class="chatbox-input-wrap">
      <input
        v-model="chatInput"
        class="chatbox-input"
        :placeholder="chat.canChat ? 'Scrivi...' : 'Chat non disponibile'"
        :disabled="!chat.canChat"
        maxlength="200"
        @keyup.enter="send"
      />
      <button
        class="chatbox-send"
        :disabled="!chat.canChat || !chatInput.trim()"
        @click="send"
      >
        Invia
      </button>
    </div>
  </div>
</template>

<style scoped>
.chatbox {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: rgba(255,255,255,0.02);
  border: 1px solid rgba(255,255,255,0.06);
  border-radius: 12px;
  overflow: hidden;
}

.chatbox-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.55rem 0.8rem;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  flex-shrink: 0;
}

.chatbox-title {
  font-family: 'Cinzel', serif;
  font-size: 0.68rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: rgba(232,200,122,0.45);
}

.chatbox-channel {
  font-size: 0.68rem;
  padding: 0.1rem 0.5rem;
  border-radius: 10px;
  font-weight: 700;
}

.channel--global { background: rgba(232,200,122,0.08); color: rgba(232,200,122,0.6); }
.channel--wolves { background: rgba(248,113,113,0.1); color: #f87171; }
.channel--dead { background: rgba(148,163,184,0.1); color: rgba(148,163,184,0.6); }

.chatbox-messages {
  flex: 1;
  overflow-y: auto;
  padding: 0.6rem 0.7rem;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  min-height: 0;
}

.chatbox-empty {
  font-size: 0.78rem;
  color: rgba(232,224,213,0.2);
  font-style: italic;
  text-align: center;
  padding: 1rem 0;
}

.chatbox-msg {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  max-width: 85%;
  align-self: flex-start;
}

.chatbox-msg--me {
  align-self: flex-end;
}

.msg-meta {
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
  padding: 0 0.2rem;
}

.chatbox-msg--me .msg-meta {
  flex-direction: row-reverse;
}

.msg-sender {
  font-size: 0.65rem;
  font-weight: 700;
  color: rgba(232,200,122,0.5);
}

.chatbox-msg--me .msg-sender {
  color: rgba(144,200,255,0.6);
}

.msg-time {
  font-size: 0.6rem;
  color: rgba(232,224,213,0.2);
}

.msg-bubble {
  font-size: 0.82rem;
  color: rgba(232,224,213,0.85);
  line-height: 1.45;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.05);
  border-radius: 8px;
  padding: 0.35rem 0.6rem;
  word-break: break-word;
}

.chatbox-msg--me .msg-bubble {
  background: rgba(100,180,255,0.08);
  border-color: rgba(100,180,255,0.1);
}

/* 👻 STILE FANTASMI (Aggiunto in modo supersicuro) 👻 */
.msg--dead {
  opacity: 0.65;
}
.msg--dead .msg-bubble {
  background: rgba(148,163,184,0.08);
  border-color: rgba(148,163,184,0.15);
  color: rgba(148,163,184,0.9);
  font-style: italic;
}
.msg--dead .msg-sender {
  color: rgba(148,163,184,0.7);
}
.ghost-icon {
  font-size: 0.7rem;
  filter: grayscale(1);
}

.chatbox-input-wrap {
  display: flex;
  gap: 0.35rem;
  padding: 0.55rem 0.7rem;
  border-top: 1px solid rgba(255,255,255,0.05);
  flex-shrink: 0;
}

.chatbox-input {
  flex: 1;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 8px;
  padding: 0.45rem 0.65rem;
  color: #e8e0d5;
  font-size: 0.82rem;
  outline: none;
  font-family: inherit;
  transition: border-color 0.2s;
}

.chatbox-input:focus { border-color: rgba(232,200,122,0.3); }
.chatbox-input:disabled { opacity: 0.3; cursor: not-allowed; }
.chatbox-input::placeholder { color: rgba(232,224,213,0.2); }

.chatbox-send {
  background: rgba(232,200,122,0.08);
  border: 1px solid rgba(232,200,122,0.2);
  border-radius: 8px;
  color: #e8c87a;
  min-width: 56px;
  cursor: pointer;
  font-size: 0.78rem;
  transition: all 0.2s;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.chatbox-send:hover:not(:disabled) { background: rgba(232,200,122,0.15); }
.chatbox-send:disabled { opacity: 0.2; cursor: not-allowed; }
</style>