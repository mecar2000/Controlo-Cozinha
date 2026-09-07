/**
 * Interpolation weighting, including the anisotropy — the design spec's
 * named test target. The anisotropy is what stops the field drawing buoyant
 * hydrogen as pooling low, so it gets asserted directly rather than inferred
 * from a rendered result.
 */

import { describe, expect, it } from 'vitest'

import {
  anisotropicDistance,
  buildFieldGrid,
  CONFIDENCE_RANGE_M,
  DEFAULT_ANISOTROPY,
  peakConcentration,
  sampleField,
  type SensorSample,
} from './interpolation'
import { isInsideSolid, LOWER_CABINET, ROOM } from './roomGeometry'

describe('anisotropicDistance', () => {
  it('leaves horizontal separation at face value', () => {
    expect(anisotropicDistance(0, 0, 0, 1, 0, 0)).toBeCloseTo(1, 10)
    expect(anisotropicDistance(0, 0, 0, 0, 1, 0)).toBeCloseTo(1, 10)
  })

  it('inflates vertical separation, so influence spreads sideways not up', () => {
    const horizontal = anisotropicDistance(0, 0, 0, 1, 0, 0)
    const vertical = anisotropicDistance(0, 0, 0, 0, 0, 1)
    expect(vertical).toBeGreaterThan(horizontal)
    expect(vertical).toBeCloseTo(1 / DEFAULT_ANISOTROPY, 10)
  })

  it('is isotropic at anisotropy 1', () => {
    expect(anisotropicDistance(0, 0, 0, 0, 0, 1, 1)).toBeCloseTo(1, 10)
  })

  it('is symmetric', () => {
    expect(anisotropicDistance(0.3, 1.1, 2.0, 1.7, 0.2, 0.4)).toBeCloseTo(
      anisotropicDistance(1.7, 0.2, 0.4, 0.3, 1.1, 2.0),
      10,
    )
  })
})

describe('sampleField', () => {
  it('has nothing to say with no sensors, rather than throwing', () => {
    expect(sampleField(1, 1, 1, [])).toEqual({ value: 0, confidence: 0 })
  })

  it('returns a sensor’s own value at its position, with full confidence', () => {
    const sensors: SensorSample[] = [{ x: 1, y: 1, z: 1, value: 2.5 }]
    expect(sampleField(1, 1, 1, sensors)).toEqual({ value: 2.5, confidence: 1 })
  })

  it('interpolates between two sensors', () => {
    const sensors: SensorSample[] = [
      { x: 0, y: 0, z: 1, value: 0 },
      { x: 2, y: 0, z: 1, value: 4 },
    ]
    const mid = sampleField(1, 0, 1, sensors)
    expect(mid.value).toBeCloseTo(2, 6)
  })

  it('never exceeds the range of its inputs', () => {
    const sensors: SensorSample[] = [
      { x: 0, y: 0, z: 0, value: 1 },
      { x: 2, y: 2, z: 2, value: 3 },
    ]
    for (let x = 0; x <= 2; x += 0.25) {
      const { value } = sampleField(x, 1, 1, sensors)
      expect(value).toBeGreaterThanOrEqual(1)
      expect(value).toBeLessThanOrEqual(3)
    }
  })

  it('spreads a ceiling reading sideways further than downward', () => {
    // One hot sensor at the ceiling. A point the same distance away
    // horizontally must read hotter than one directly below it, or the field
    // is drawing buoyant hydrogen as sinking.
    const sensors: SensorSample[] = [
      { x: 1.4, y: 1.0, z: 2.4, value: 3.0 },
      { x: 1.4, y: 1.0, z: 0.2, value: 0.0 },
    ]
    const sideways = sampleField(2.0, 1.0, 2.4, sensors)
    const below = sampleField(1.4, 1.0, 1.8, sensors)
    expect(sideways.value).toBeGreaterThan(below.value)
  })

  it('falls to zero confidence beyond the confidence range', () => {
    const sensors: SensorSample[] = [{ x: 0, y: 0, z: 0, value: 1 }]
    const far = sampleField(CONFIDENCE_RANGE_M + 0.5, 0, 0, sensors)
    expect(far.confidence).toBe(0)
  })

  it('fades confidence with distance, so guesswork looks thin', () => {
    const sensors: SensorSample[] = [{ x: 0, y: 0, z: 0, value: 1 }]
    const near = sampleField(0.2, 0, 0, sensors)
    const mid = sampleField(0.6, 0, 0, sensors)
    expect(near.confidence).toBeGreaterThan(mid.confidence)
    expect(mid.confidence).toBeGreaterThan(0)
  })
})

describe('buildFieldGrid', () => {
  const sensors: SensorSample[] = [
    { x: 0.5, y: 0.3, z: 0.9, value: 0.4 },
    { x: 1.5, y: 1.5, z: 2.3, value: 2.1 },
  ]

  it('covers the room at the requested resolution', () => {
    const grid = buildFieldGrid(sensors, 0.3)
    expect(grid.nx).toBe(Math.ceil(ROOM.width / 0.3))
    expect(grid.ny).toBe(Math.ceil(ROOM.depth / 0.3))
    expect(grid.nz).toBe(Math.ceil(ROOM.height / 0.3))
    expect(grid.values.length).toBe(grid.nx * grid.ny * grid.nz)
  })

  it('marks cells inside furniture as solid and leaves them empty', () => {
    const grid = buildFieldGrid(sensors, 0.2)
    let solidCount = 0
    for (let i = 0; i < grid.solid.length; i++) {
      if (grid.solid[i]) {
        solidCount++
        expect(grid.values[i]).toBe(0)
        expect(grid.confidence[i]).toBe(0)
      }
    }
    // The lower cabinet alone is a substantial volume; if nothing was
    // marked solid, the furniture test is silently not running.
    expect(solidCount).toBeGreaterThan(0)
  })

  it('agrees with isInsideSolid about the lower cabinet', () => {
    const inside = {
      x: LOWER_CABINET.x + LOWER_CABINET.width / 2,
      y: LOWER_CABINET.y + LOWER_CABINET.depth / 2,
      z: LOWER_CABINET.height / 2,
    }
    expect(isInsideSolid(inside.x, inside.y, inside.z)).toBe(true)
    // Just above the counter is air, and must stay sampleable.
    expect(isInsideSolid(inside.x, inside.y, LOWER_CABINET.height + 0.1)).toBe(false)
  })
})

describe('peakConcentration', () => {
  it('is zero with no sensors', () => {
    expect(peakConcentration([])).toBe(0)
  })

  it('reports the highest reading', () => {
    expect(
      peakConcentration([
        { x: 0, y: 0, z: 0, value: 0.4 },
        { x: 1, y: 1, z: 1, value: 1.84 },
        { x: 2, y: 2, z: 2, value: 0.9 },
      ]),
    ).toBe(1.84)
  })
})
