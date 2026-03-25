import { ref, computed, onUnmounted } from 'vue'

/**
 * Timer conto alla rovescia riusabile.
 * Usato in: countdown lobby, fasi di gioco (notte/giorno/voto)
 *
 * Esempio:
 *   const { seconds, formattedTime, isRunning, start, stop, reset } = useTimer(30)
 */
export function useTimer(initialSeconds = 60) {
  const seconds = ref(initialSeconds)
  const isRunning = ref(false)
  const isExpired = ref(false)
  let interval = null

  const formattedTime = computed(() => {
    const m = Math.floor(seconds.value / 60)
    const s = seconds.value % 60
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  })

  const progress = computed(() => {
    // percentuale rimanente (100% = pieno, 0% = scaduto)
    return (seconds.value / initialSeconds) * 100
  })

  function start(onExpire = null) {
    if (isRunning.value) return
    isRunning.value = true
    isExpired.value = false

    interval = setInterval(() => {
      if (seconds.value <= 0) {
        stop()
        isExpired.value = true
        onExpire?.()
        return
      }
      seconds.value--
    }, 1000)
  }

  function stop() {
    clearInterval(interval)
    interval = null
    isRunning.value = false
  }

  function reset(newSeconds = initialSeconds) {
    stop()
    seconds.value = newSeconds
    isExpired.value = false
  }

  // Pulizia automatica se il componente viene smontato mentre il timer gira
  onUnmounted(() => stop())

  return {
    seconds,
    formattedTime,
    progress,
    isRunning,
    isExpired,
    start,
    stop,
    reset,
  }
}
