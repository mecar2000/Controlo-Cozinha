/**
 * Replay resampling onto a common grid — including sensors with gaps and
 * runs shorter than one grid step, the two cases the design spec names.
 */

import { describe, expect, it } from 'vitest'

import {
  buildTimeGrid,
  chooseGridStepMs,
  indexForTime,
  resampleAll,
  resampleSeries,
  seriesExtent,
  valueAt,
  type SeriesPoint,
} from './resample'

const points: SeriesPoint[] = [
  { t_ms: 1000, value: 0 },
  { t_ms: 2000, value: 10 },
  { t_ms: 4000, value: 20 },
]

describe('valueAt', () => {
  it('is zero for an empty series', () => {
    expect(valueAt([], 1500)).toBe(0)
  })

  it('returns exact values at sample instants', () => {
    expect(valueAt(points, 1000)).toBe(0)
    expect(valueAt(points, 2000)).toBe(10)
    expect(valueAt(points, 4000)).toBe(20)
  })

  it('interpolates linearly between samples', () => {
    expect(valueAt(points, 1500)).toBeCloseTo(5, 10)
    expect(valueAt(points, 3000)).toBeCloseTo(15, 10)
  })

  it('holds flat at the ends rather than extrapolating a trend', () => {
    // A sensor that stopped reporting must not have a trend projected past
    // its last real measurement.
    expect(valueAt(points, 0)).toBe(0)
    expect(valueAt(points, 99_999)).toBe(20)
  })

  it('handles a single-sample series', () => {
    expect(valueAt([{ t_ms: 5000, value: 7 }], 0)).toBe(7)
    expect(valueAt([{ t_ms: 5000, value: 7 }], 10_000)).toBe(7)
  })

  it('bridges a gap without inventing structure inside it', () => {
    // LTTB leaves gaps where a sensor was silent. Interpolating across one
    // is correct; the value must stay monotonic between the bracketing
    // samples rather than dipping.
    const gapped: SeriesPoint[] = [
      { t_ms: 0, value: 1 },
      { t_ms: 60_000, value: 2 },
    ]
    expect(valueAt(gapped, 30_000)).toBeCloseTo(1.5, 10)
    expect(valueAt(gapped, 15_000)).toBeCloseTo(1.25, 10)
  })
})

describe('chooseGridStepMs', () => {
  it('adapts to run length', () => {
    const short = chooseGridStepMs(30_000)
    const long = chooseGridStepMs(2 * 60 * 60 * 1000)
    expect(long).toBeGreaterThan(short)
  })

  it('snaps to a readable cadence', () => {
    const allowed = [50, 100, 200, 250, 500, 1000, 2000, 5000, 10_000, 30_000, 60_000, 120_000]
    for (const duration of [1000, 30_000, 300_000, 3_600_000, 86_400_000]) {
      expect(allowed).toContain(chooseGridStepMs(duration))
    }
  })

  it('handles a zero-length run', () => {
    expect(chooseGridStepMs(0)).toBeGreaterThan(0)
  })
})

describe('buildTimeGrid', () => {
  it('spans the requested window at the requested step', () => {
    const grid = buildTimeGrid(0, 10_000, 1000)
    expect(grid.times.length).toBe(11)
    expect(grid.times[0]).toBe(0)
    expect(grid.times[10]).toBe(10_000)
  })

  it('gives a run shorter than one grid step a single frame', () => {
    // Otherwise replay of a very short run shows an empty scrubber.
    const grid = buildTimeGrid(0, 200, 1000)
    expect(grid.times.length).toBe(1)
    expect(grid.times[0]).toBe(0)
  })

  it('handles a zero-length window', () => {
    const grid = buildTimeGrid(5000, 5000, 1000)
    expect(grid.times.length).toBe(1)
  })
})

describe('resampleSeries', () => {
  it('puts every sensor on the same instants', () => {
    const grid = buildTimeGrid(1000, 4000, 1000)
    const out = resampleSeries('s1', points, grid)
    expect(Array.from(out.values)).toEqual([0, 10, 15, 20])
  })

  it('aligns series that LTTB returned on different timestamps', () => {
    // This is the whole reason the grid exists: LTTB downsamples each sensor
    // independently, so a heatmap has no single instant to read from.
    const a: SeriesPoint[] = [
      { t_ms: 0, value: 0 },
      { t_ms: 1000, value: 10 },
    ]
    const b: SeriesPoint[] = [
      { t_ms: 250, value: 100 },
      { t_ms: 900, value: 200 },
    ]
    const grid = buildTimeGrid(0, 1000, 500)
    const [ra, rb] = resampleAll(
      [
        { key: 'a', points: a },
        { key: 'b', points: b },
      ],
      grid,
    )
    expect(ra!.values.length).toBe(rb!.values.length)
    expect(ra!.values.length).toBe(grid.times.length)
  })

  it('yields zeroes for a sensor with no data at all', () => {
    const grid = buildTimeGrid(0, 2000, 1000)
    const out = resampleSeries('empty', [], grid)
    expect(Array.from(out.values)).toEqual([0, 0, 0])
  })
})

describe('indexForTime', () => {
  const grid = buildTimeGrid(1000, 5000, 1000)

  it('maps an instant onto the nearest grid index', () => {
    expect(indexForTime(grid, 1000)).toBe(0)
    expect(indexForTime(grid, 3000)).toBe(2)
    expect(indexForTime(grid, 3400)).toBe(2)
    expect(indexForTime(grid, 3600)).toBe(3)
  })

  it('clamps outside the window rather than indexing off the end', () => {
    expect(indexForTime(grid, -99_999)).toBe(0)
    expect(indexForTime(grid, 99_999)).toBe(grid.times.length - 1)
  })
})

describe('seriesExtent', () => {
  it('is null when nothing has data', () => {
    expect(seriesExtent([])).toBeNull()
    expect(seriesExtent([{ points: [] }])).toBeNull()
  })

  it('spans every series', () => {
    expect(
      seriesExtent([
        { points: [{ t_ms: 500, value: 1 }] },
        { points: [{ t_ms: 100, value: 1 }, { t_ms: 900, value: 1 }] },
      ]),
    ).toEqual({ startMs: 100, endMs: 900 })
  })

  it('ignores empty series among populated ones', () => {
    expect(
      seriesExtent([{ points: [] }, { points: [{ t_ms: 42, value: 1 }] }]),
    ).toEqual({ startMs: 42, endMs: 42 })
  })
})
