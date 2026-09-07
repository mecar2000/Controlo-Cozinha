/**
 * Colour-scale mapping at range boundaries — the cases the design spec's
 * testing section names: 0, 4%, 20%, and above.
 */

import { describe, expect, it } from 'vitest'

import {
  concentrationToRgb,
  isAtOrAboveLel,
  LEGEND_TICKS_PCT_VV,
  LEL_PCT_VV,
  pctLelToPctVv,
  pctVvToPctLel,
  rampPosition,
  SCALE_MAX_PCT_VV,
} from './colorScale'

describe('rampPosition', () => {
  it('starts at 0 and saturates at 1', () => {
    expect(rampPosition(0)).toBe(0)
    expect(rampPosition(SCALE_MAX_PCT_VV)).toBe(1)
  })

  it('spends 70% of the ramp below LEL, where the experiment lives', () => {
    expect(rampPosition(LEL_PCT_VV)).toBeCloseTo(0.7, 6)
    expect(rampPosition(LEL_PCT_VV / 2)).toBeCloseTo(0.35, 6)
  })

  it('compresses 4-20 %v/v into the remaining 30%', () => {
    expect(rampPosition(12)).toBeCloseTo(0.85, 6)
  })

  it('saturates rather than running off the end', () => {
    expect(rampPosition(50)).toBe(1)
    expect(rampPosition(1e6)).toBe(1)
  })

  it('clamps negatives and non-finite readings to the bottom', () => {
    expect(rampPosition(-1)).toBe(0)
    expect(rampPosition(NaN)).toBe(0)
  })

  it('is monotonic across the whole range', () => {
    let previous = -1
    for (let pct = 0; pct <= 24; pct += 0.1) {
      const p = rampPosition(pct)
      expect(p).toBeGreaterThanOrEqual(previous)
      previous = p
    }
  })
})

describe('concentrationToRgb', () => {
  it('is deep blue at zero, never black', () => {
    expect(concentrationToRgb(0)).toEqual({ r: 0x0b, g: 0x3d, b: 0x91 })
  })

  it('is orange exactly at LEL, the boundary between the two regimes', () => {
    expect(concentrationToRgb(LEL_PCT_VV)).toEqual({ r: 0xff, g: 0x8a, b: 0x1f })
  })

  it('ends in pale magenta, so past LEL reads as wrong rather than hot', () => {
    expect(concentrationToRgb(SCALE_MAX_PCT_VV)).toEqual({ r: 0xf0, g: 0xa6, b: 0xff })
  })

  it('holds the saturated colour above the scale maximum', () => {
    expect(concentrationToRgb(100)).toEqual(concentrationToRgb(SCALE_MAX_PCT_VV))
  })

  it('keeps 4, 10 and 20 %v/v visibly distinct', () => {
    const a = concentrationToRgb(4)
    const b = concentrationToRgb(10)
    const c = concentrationToRgb(20)
    const distance = (p: typeof a, q: typeof a) =>
      Math.abs(p.r - q.r) + Math.abs(p.g - q.g) + Math.abs(p.b - q.b)
    expect(distance(a, b)).toBeGreaterThan(60)
    expect(distance(b, c)).toBeGreaterThan(60)
  })

  it('produces in-gamut channels across the range', () => {
    for (let pct = 0; pct <= 25; pct += 0.25) {
      const { r, g, b } = concentrationToRgb(pct)
      for (const ch of [r, g, b]) {
        expect(ch).toBeGreaterThanOrEqual(0)
        expect(ch).toBeLessThanOrEqual(255)
        expect(Number.isInteger(ch)).toBe(true)
      }
    }
  })
})

describe('LEL conversions', () => {
  it('maps LEL to 100%', () => {
    expect(pctVvToPctLel(LEL_PCT_VV)).toBe(100)
    expect(pctVvToPctLel(2)).toBe(50)
  })

  it('round-trips', () => {
    expect(pctLelToPctVv(pctVvToPctLel(1.8))).toBeCloseTo(1.8, 10)
  })

  it('trips at or above threshold, matching the firmware convention', () => {
    expect(isAtOrAboveLel(LEL_PCT_VV)).toBe(true)
    expect(isAtOrAboveLel(3.999)).toBe(false)
  })
})

describe('legend', () => {
  it('ticks span the full scale', () => {
    expect(LEGEND_TICKS_PCT_VV[0]).toBe(0)
    expect(LEGEND_TICKS_PCT_VV[LEGEND_TICKS_PCT_VV.length - 1]).toBe(SCALE_MAX_PCT_VV)
  })
})
