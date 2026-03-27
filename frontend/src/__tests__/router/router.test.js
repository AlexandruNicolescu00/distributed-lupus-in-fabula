import { describe, expect, it } from 'vitest'

import router from '@/router'

describe('router', () => {
  it('risolve la GameView con id lobby nel path', () => {
    const resolved = router.resolve('/game/WOLF-1234')

    expect(resolved.name).toBe('game')
    expect(resolved.params.id).toBe('WOLF-1234')
  })

  it('risolve la ResultsView con id lobby nel path', () => {
    const resolved = router.resolve('/results/WOLF-1234')

    expect(resolved.name).toBe('result')
    expect(resolved.params.id).toBe('WOLF-1234')
  })
})
