import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useTimer } from '@/composables/useTimer'

// ---------------------------------------------------------------------------
// Setup — fake timers per controllare il tempo nei test
// Vitest/Jest permette di avanzare il tempo manualmente con vi.advanceTimersByTime()
// senza aspettare secondi reali.
// ---------------------------------------------------------------------------
beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

// ---------------------------------------------------------------------------
// SUITE 1 — Stato iniziale
// ---------------------------------------------------------------------------
describe('useTimer — stato iniziale', () => {
  it('seconds parte dal valore passato come argomento', () => {
    const { seconds } = useTimer(30)
    expect(seconds.value).toBe(30)
  })

  it('seconds di default è 60', () => {
    const { seconds } = useTimer()
    expect(seconds.value).toBe(60)
  })

  it('isRunning è false inizialmente', () => {
    const { isRunning } = useTimer(30)
    expect(isRunning.value).toBe(false)
  })

  it('isExpired è false inizialmente', () => {
    const { isExpired } = useTimer(30)
    expect(isExpired.value).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 2 — formattedTime
// ---------------------------------------------------------------------------
describe('useTimer — formattedTime', () => {
  it('formatta correttamente 90 secondi come 01:30', () => {
    const { formattedTime } = useTimer(90)
    expect(formattedTime.value).toBe('01:30')
  })

  it('formatta correttamente 0 secondi come 00:00', () => {
    const { seconds, formattedTime } = useTimer(30)
    seconds.value = 0
    expect(formattedTime.value).toBe('00:00')
  })

  it('formatta correttamente 65 secondi come 01:05', () => {
    const { formattedTime } = useTimer(65)
    expect(formattedTime.value).toBe('01:05')
  })

  it('formatta correttamente 30 secondi come 00:30', () => {
    const { formattedTime } = useTimer(30)
    expect(formattedTime.value).toBe('00:30')
  })
})

// ---------------------------------------------------------------------------
// SUITE 3 — start() e countdown
// ---------------------------------------------------------------------------
describe('useTimer — start()', () => {
  it('isRunning diventa true dopo start()', () => {
    const { isRunning, start } = useTimer(10)
    start()
    expect(isRunning.value).toBe(true)
  })

  it('seconds scende di 1 ogni secondo', () => {
    const { seconds, start } = useTimer(10)
    start()
    vi.advanceTimersByTime(3000)   // avanza 3 secondi
    expect(seconds.value).toBe(7)
  })

  it('seconds non va sotto 0', () => {
    const { seconds, start } = useTimer(2)
    start()
    vi.advanceTimersByTime(5000)   // avanza più del necessario
    expect(seconds.value).toBe(0)
  })

  it('isExpired diventa true quando il timer scade', () => {
    const { isExpired, start } = useTimer(2)
    start()
    vi.advanceTimersByTime(3000)
    expect(isExpired.value).toBe(true)
  })

  it('callback onExpire viene chiamato alla scadenza', () => {
    const onExpire = vi.fn()
    const { start } = useTimer(2)
    start(onExpire)
    vi.advanceTimersByTime(3000)
    expect(onExpire).toHaveBeenCalledOnce()
  })

  it('chiamare start() due volte non crea due interval', () => {
    const { seconds, start } = useTimer(10)
    start()
    start()   // seconda chiamata ignorata
    vi.advanceTimersByTime(2000)
    expect(seconds.value).toBe(8)   // scende di 2, non 4
  })
})

// ---------------------------------------------------------------------------
// SUITE 4 — stop()
// ---------------------------------------------------------------------------
describe('useTimer — stop()', () => {
  it('stop() blocca il countdown', () => {
    const { seconds, start, stop } = useTimer(10)
    start()
    vi.advanceTimersByTime(2000)
    stop()
    vi.advanceTimersByTime(3000)   // passa altro tempo ma il timer è fermo
    expect(seconds.value).toBe(8)  // si è fermato a 8
  })

  it('isRunning diventa false dopo stop()', () => {
    const { isRunning, start, stop } = useTimer(10)
    start()
    stop()
    expect(isRunning.value).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 5 — reset()
// ---------------------------------------------------------------------------
describe('useTimer — reset()', () => {
  it('reset() riporta seconds al valore iniziale', () => {
    const { seconds, start, reset } = useTimer(10)
    start()
    vi.advanceTimersByTime(4000)
    reset()
    expect(seconds.value).toBe(10)
  })

  it('reset() ferma il timer', () => {
    const { isRunning, start, reset } = useTimer(10)
    start()
    reset()
    expect(isRunning.value).toBe(false)
  })

  it('reset() con nuovo valore aggiorna seconds', () => {
    const { seconds, reset } = useTimer(10)
    reset(30)
    expect(seconds.value).toBe(30)
  })

  it('reset() azzera isExpired', () => {
    const { isExpired, start, reset } = useTimer(1)
    start()
    vi.advanceTimersByTime(2000)
    expect(isExpired.value).toBe(true)
    reset()
    expect(isExpired.value).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// SUITE 6 — progress
// ---------------------------------------------------------------------------
describe('useTimer — progress', () => {
  it('progress è 100 all\'inizio', () => {
    const { progress } = useTimer(60)
    expect(progress.value).toBe(100)
  })

  it('progress diminuisce col tempo', () => {
    const { progress, start } = useTimer(10)
    start()
    vi.advanceTimersByTime(5000)   // metà del tempo
    expect(progress.value).toBeCloseTo(50, 0)
  })

  it('progress è 0 quando il timer è scaduto', () => {
    const { progress, start } = useTimer(2)
    start()
    vi.advanceTimersByTime(3000)
    expect(progress.value).toBe(0)
  })
})
