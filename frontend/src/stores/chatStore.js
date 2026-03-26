import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { useSocket } from '@/composables/useSocket'
import { useGameStore, PHASES } from '@/stores/gameStore'

export const useChatStore = defineStore('chat', () => {
  // ---- STATE ----
  const messages = ref([])   // { id, senderId, senderName, text, channel, timestamp }
  const isOpen = ref(false)
  const unreadCount = ref(0)

  const { emit, on } = useSocket()

  // ---- CANALI ----
  // 'global' → tutti i giocatori vivi, solo di giorno
  // 'wolves' → solo i lupi, solo di notte
  // 'dead'   → solo i morti (spettatori)
  const CHANNELS = { GLOBAL: 'global', WOLVES: 'wolves', DEAD: 'dead' }

  // ---- GETTERS ----

  /** Messaggi visibili al giocatore corrente in base a fase e ruolo */
  const visibleMessages = computed(() => {
  const gameStore = useGameStore()
  // Se il giocatore è morto → vede solo il canale dead
  if (!gameStore.isAlive) {
    return messages.value.filter((msg) => msg.channel === CHANNELS.DEAD)
  }
  return messages.value.filter((msg) => {
    if (msg.channel === CHANNELS.DEAD)   return false
    if (msg.channel === CHANNELS.WOLVES) return gameStore.isWolf
    if (msg.channel === CHANNELS.GLOBAL) return gameStore.phase === PHASES.DAY || gameStore.phase === PHASES.VOTING
    return false
  })
})

  /** Canale attivo per il giocatore in base alla fase corrente */
  const activeChannel = computed(() => {
    const gameStore = useGameStore()
    if (!gameStore.isAlive)  return CHANNELS.DEAD
    if (gameStore.isWolf && gameStore.phase === PHASES.NIGHT) return CHANNELS.WOLVES
    return CHANNELS.GLOBAL
  })

  /** Il giocatore può scrivere in questo momento? */
 const canChat = computed(() => {
  const gameStore = useGameStore()
  // I morti possono chattare nel canale dead
  if (!gameStore.isAlive) return true
  if (gameStore.phase === PHASES.NIGHT && !gameStore.isWolf) return false
  if (gameStore.phase === PHASES.LOBBY || gameStore.phase === PHASES.ENDED) return false
  return true
})

  // ---- ACTIONS ----

  /** Invia un messaggio nel canale attivo */
  function sendMessage(text) {
    if (!canChat.value || !text.trim()) return
    emit('chat:message', {
      text: text.trim(),
      channel: activeChannel.value,
    })
  }

  /** Registra il listener per i messaggi in arrivo */
  function listenToMessages() {
    on('chat:message', (msg) => {
      messages.value.push({
        id: msg.id ?? Date.now(),
        senderId: msg.senderId,
        senderName: msg.senderName,
        text: msg.text,
        channel: msg.channel,
        timestamp: msg.timestamp ?? new Date().toISOString(),
      })

      if (!isOpen.value) unreadCount.value++
    })
  }

  function openChat()  { isOpen.value = true;  unreadCount.value = 0 }
  function closeChat() { isOpen.value = false }

  function reset() {
    messages.value = []
    unreadCount.value = 0
    isOpen.value = false
  }

  return {
    // state
    messages, isOpen, unreadCount,
    // getters
    visibleMessages, activeChannel, canChat,
    // actions
    sendMessage, listenToMessages, openChat, closeChat, reset,
    // costanti
    CHANNELS,
  }
})
