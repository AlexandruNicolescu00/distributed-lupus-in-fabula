import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { useSocket } from '@/composables/useSocket'
import { useGameStore, PHASES } from '@/stores/gameStore'

export const useChatStore = defineStore('chat', () => {
  const messages = ref([])
  const isOpen = ref(false)
  const unreadCount = ref(0)
  const listenersBound = ref(false)
  const seenMessageIds = ref(new Set())
  const messageHandler = ref(null)
  const { emit, on, off } = useSocket()

  const CHANNELS = {
    GLOBAL: 'global',
    WOLVES: 'wolves',
    DEAD: 'dead',
  }

  const visibleMessages = computed(() => {
    const gameStore = useGameStore()
    return messages.value.filter((msg) => {
      if (msg.channel === CHANNELS.GLOBAL) return true
      if (msg.channel === CHANNELS.WOLVES) return Boolean(gameStore.isWolf)
      if (msg.channel === CHANNELS.DEAD) return Boolean(gameStore.me) && !gameStore.isAlive
      return false
    })
  })

  const activeChannel = computed(() => {
    const gameStore = useGameStore()
    if (gameStore.me && !gameStore.isAlive) return CHANNELS.DEAD
    if (gameStore.isWolf && gameStore.phase === PHASES.NIGHT) return CHANNELS.WOLVES
    return CHANNELS.GLOBAL
  })

  const canChat = computed(() => {
    const gameStore = useGameStore()
    if (gameStore.phase === PHASES.ENDED) return false
    return true
  })

  function sendMessage(text) {
    if (!canChat.value || !text.trim()) return
    console.log('[ChatStore] Invio messaggio:', {
      text: text.trim(),
      channel: activeChannel.value,
    })
    emit('chat_message', {
      text: text.trim(),
      channel: activeChannel.value,
    })
  }

  function listenToMessages() {
    if (!messageHandler.value) {
      messageHandler.value = (msg) => {
        console.log('[ChatStore] Messaggio ricevuto dal socket:', msg)
        const payload = msg?.payload ?? msg ?? {}
        const messageId = msg?.event_id ?? `${payload.senderId}-${payload.text}-${msg?.timestamp ?? Date.now()}`

        if (seenMessageIds.value.has(messageId)) {
          return
        }

        seenMessageIds.value.add(messageId)
        messages.value.push({
          id: messageId,
          senderId: payload.senderId,
          senderName: payload.senderName,
          text: payload.text,
          channel: payload.channel,
          timestamp: msg?.timestamp ?? new Date().toISOString(),
        })

        if (!isOpen.value) unreadCount.value += 1
      }
    }

    if (listenersBound.value) {
      off('chat_message', messageHandler.value)
    }

    on('chat_message', messageHandler.value)
    listenersBound.value = true
  }

  function openChat() {
    isOpen.value = true
    unreadCount.value = 0
  }

  function closeChat() {
    isOpen.value = false
  }

  function reset() {
    if (listenersBound.value && messageHandler.value) {
      off('chat_message', messageHandler.value)
    }

    messages.value = []
    unreadCount.value = 0
    isOpen.value = false
    seenMessageIds.value = new Set()
    listenersBound.value = false
  }

  return {
    messages,
    isOpen,
    unreadCount,
    visibleMessages,
    activeChannel,
    canChat,
    sendMessage,
    listenToMessages,
    openChat,
    closeChat,
    reset,
    CHANNELS,
  }
})
