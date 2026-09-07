/** The five plot-mode transforms — the design spec's named test target. */

import { describe, expect, it } from 'vitest'

import { applyTransform, unitForMode, yExtent, type Point } from './transforms'

const points: Point[] = [
  { t_ms: 0, value: 1, raw_value: 4.1 },
  { t_ms: 1000, value: 3, raw_value: 4.5 },
  { t_ms: 2000, value: 5, raw_value: 4.9 },
]

describe('applyTransform', () => {
  it('returns nothing for an empty series in every mode', () => {
    for (const mode of ['raw', 'converted', 'minmax', 'baseline', 'zscore'] as const) {
      expect(applyTransform([], mode)).toEqual([])
    }
  })

  it('preserves timestamps in every mode', () => {
    for (const mode of ['raw', 'converted', 'minmax', 'baseline', 'zscore'] as const) {
      expect(applyTransform(points, mode).map((p) => p.t_ms)).toEqual([0, 1000, 2000])
    }
  })

  it('does not mutate its input', () => {
    const before = structuredClone(points)
    applyTransform(points, 'zscore')
    expect(points).toEqual(before)
  })

  describe('raw', () => {
    it('plots the as-measured value', () => {
      expect(applyTransform(points, 'raw').map((p) => p.value)).toEqual([4.1, 4.5, 4.9])
    })

    it('falls back to converted when the historian returned no raw column', () => {
      // Better than a flat line of zeroes when raw is simply absent.
      const noRaw: Point[] = [{ t_ms: 0, value: 2 }]
      expect(applyTransform(noRaw, 'raw')[0]!.value).toBe(2)
    })
  })

  describe('converted', () => {
    it('passes the converted value through', () => {
      expect(applyTransform(points, 'converted').map((p) => p.value)).toEqual([1, 3, 5])
    })
  })

  describe('minmax', () => {
    it('normalises the window onto 0-1', () => {
      expect(applyTransform(points, 'minmax').map((p) => p.value)).toEqual([0, 0.5, 1])
    })

    it('draws a flat series along the bottom instead of dividing by zero', () => {
      const flat: Point[] = [
        { t_ms: 0, value: 2 },
        { t_ms: 1, value: 2 },
      ]
      expect(applyTransform(flat, 'minmax').map((p) => p.value)).toEqual([0, 0])
    })

    it('makes differently-scaled sensors directly comparable', () => {
      const small: Point[] = [
        { t_ms: 0, value: 0.1 },
        { t_ms: 1, value: 0.2 },
        { t_ms: 2, value: 0.3 },
      ]
      const large: Point[] = [
        { t_ms: 0, value: 100 },
        { t_ms: 1, value: 200 },
        { t_ms: 2, value: 300 },
      ]
      // Compared with tolerance: the two series normalise to the same
      // shape, but not to bit-identical floats, since 0.1/0.2 and 100/200
      // round differently.
      const a = applyTransform(small, 'minmax').map((p) => p.value)
      const b = applyTransform(large, 'minmax').map((p) => p.value)
      expect(a.length).toBe(b.length)
      a.forEach((v, i) => expect(v).toBeCloseTo(b[i]!, 12))
    })
  })

  describe('baseline', () => {
    it('subtracts each sensor’s own pre-leak resting mean', () => {
      // Resting window is everything at or before 1000: mean of 1 and 3 = 2.
      expect(applyTransform(points, 'baseline', 1000).map((p) => p.value)).toEqual([-1, 1, 3])
    })

    it('falls back to the first sample when there is no pre-leak window', () => {
      // A run whose recording started late still gets a meaningful mode:
      // change relative to where the sensor started.
      expect(applyTransform(points, 'baseline').map((p) => p.value)).toEqual([0, 2, 4])
    })

    it('zeroes a sensor that never moved off its resting value', () => {
      const flat: Point[] = [
        { t_ms: 0, value: 0.42 },
        { t_ms: 1000, value: 0.42 },
      ]
      expect(applyTransform(flat, 'baseline', 1000).map((p) => p.value)).toEqual([0, 0])
    })
  })

  describe('zscore', () => {
    it('centres on zero', () => {
      const out = applyTransform(points, 'zscore')
      const mean = out.reduce((a, p) => a + p.value, 0) / out.length
      expect(mean).toBeCloseTo(0, 10)
    })

    it('scales to unit standard deviation', () => {
      const out = applyTransform(points, 'zscore')
      const mean = out.reduce((a, p) => a + p.value, 0) / out.length
      const sd = Math.sqrt(out.reduce((a, p) => a + (p.value - mean) ** 2, 0) / out.length)
      expect(sd).toBeCloseTo(1, 10)
    })

    it('flattens a constant series instead of dividing by zero', () => {
      const flat: Point[] = [
        { t_ms: 0, value: 5 },
        { t_ms: 1, value: 5 },
      ]
      expect(applyTransform(flat, 'zscore').map((p) => p.value)).toEqual([0, 0])
    })
  })
})

describe('unitForMode', () => {
  it('labels measured modes with their real units', () => {
    expect(unitForMode('raw', 'mA')).toBe('mA')
    expect(unitForMode('converted', 'mA', '%v/v')).toBe('%v/v')
  })

  it('labels normalised modes as unitless', () => {
    expect(unitForMode('minmax')).toBe('0-1')
    expect(unitForMode('zscore')).toBe('SD')
  })

  it('marks baseline as a delta', () => {
    expect(unitForMode('baseline', 'mA', '%v/v')).toBe('Δ %v/v')
  })
})

describe('yExtent', () => {
  it('falls back to a usable range with no data', () => {
    expect(yExtent([])).toEqual([0, 1])
  })

  it('pads so the topmost peak is not clipped', () => {
    const [min, max] = yExtent([{ points }])
    expect(min).toBeLessThan(1)
    expect(max).toBeGreaterThan(5)
  })

  it('gives a flat series room to be visible', () => {
    const [min, max] = yExtent([{ points: [{ t_ms: 0, value: 2 }] }])
    expect(min).toBeLessThan(2)
    expect(max).toBeGreaterThan(2)
  })

  it('spans every series', () => {
    const [min, max] = yExtent([
      { points: [{ t_ms: 0, value: -3 }] },
      { points: [{ t_ms: 0, value: 9 }] },
    ])
    expect(min).toBeLessThanOrEqual(-3)
    expect(max).toBeGreaterThanOrEqual(9)
  })
})
