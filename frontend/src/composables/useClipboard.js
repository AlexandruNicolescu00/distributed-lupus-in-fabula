import { ref } from 'vue'

/**
 * Copia testo negli appunti con feedback visivo.
 * Già usato nella LobbyView per copiare il codice partita.
 *
 * Esempio:
 *   const { copied, copy } = useClipboard()
 *   await copy('WOLF-1234')
 */
export function useClipboard(resetDelay = 2000) {
  const copied = ref(false)

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text)
      copied.value = true
      setTimeout(() => (copied.value = false), resetDelay)
    } catch (err) {
      console.error('[Clipboard] Errore copia:', err)
      // Fallback per browser senza supporto clipboard API
      fallbackCopy(text)
    }
  }

  function fallbackCopy(text) {
    const el = document.createElement('textarea')
    el.value = text
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.focus()
    el.select()
    try {
      document.execCommand('copy')
      copied.value = true
      setTimeout(() => (copied.value = false), 2000)
    } catch (err) {
      console.error('[Clipboard] Fallback fallito:', err)
    }
    document.body.removeChild(el)
  }

  return { copied, copy }
}
