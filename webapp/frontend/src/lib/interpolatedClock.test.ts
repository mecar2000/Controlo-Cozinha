/**
 * interpolatedClock.test.ts — the countdown/elapsed-clock math LatchPanel
 * (and, later, NumericRail) use to keep a number visibly ticking every
 * second even though the network poll only arrives every 2s (problems.txt:
 * "why is the state updated so frequently... couldn't it be 2s-5s" — yes,
 * but a slower NETWORK poll must not mean the on-screen number visibly
 * steps instead of counting smoothly).
 */
import { describe, expect, it } from 'vitest'

import { interpolatedElapsed } from './interpolatedClock'

describe('interpolatedElapsed', () => {
  it('returns the base value at the moment it was received', () => {
    expect(interpolatedElapsed(10_000, 1_000_000, 1_000_000)).toBe(10_000)
  })

  it('advances by real time elapsed since it was received', () => {
    expect(interpolatedElapsed(10_000, 1_000_000, 1_003_500)).toBe(13_500)
  })

  it('never goes backwards if "now" is somehow before receivedAtMs (clock skew)', () => {
    expect(interpolatedElapsed(10_000, 1_000_000, 999_000)).toBe(10_000)
  })

  it('is null when the base value is null (nothing to interpolate from)', () => {
    expect(interpolatedElapsed(null, 1_000_000, 1_003_000)).toBeNull()
  })

  it('clamps to a ceiling when one is given (a countdown must not run past zero remaining)', () => {
    expect(interpolatedElapsed(9_800, 1_000_000, 1_005_000, { ceiling: 10_000 })).toBe(10_000)
  })

  it('with no ceiling, keeps advancing past any implicit total (elapsed-run clock case)', () => {
    expect(interpolatedElapsed(59_000, 1_000_000, 1_005_000)).toBe(64_000)
  })
})
