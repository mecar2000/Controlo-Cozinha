/**
 * Field texture packing.
 *
 * The shader itself cannot be unit-tested here (no GL context under vitest's
 * node environment), and it does not need to be: if the ray-box intersection or
 * the compositing is wrong, it is wrong on every pixel and instantly visible.
 *
 * What IS worth testing is everything that can be wrong while still rendering
 * something plausible — the spatial mapping above all. Gas drawn on the wrong
 * side of the room looks fine and is the most dangerous failure this display
 * has.
 */

import { describe, expect, it } from 'vitest'

import { concentrationToRgb, rampPosition } from './colorScale'
import {
  buildRampLut,
  createFieldBuffer,
  packField,
  RAMP_LUT_SIZE,
  SIGMA_PER_M,
  TEX_D,
  TEX_H,
  TEX_W,
  TOTAL_OPTICAL_DEPTH,
} from './fieldTexture'
import { DEFAULT_ANISOTROPY, type SensorSample } from './interpolation'
import { LOWER_CABINET, ROOM } from './roomGeometry'

/** Byte offset of a texel, mirroring packField's own x-fastest layout. */
function texelOffset(ix: number, iy: number, iz: number): number {
  return (ix + TEX_W * (iy + TEX_H * iz)) * 4
}

/** The texel index whose centre is nearest a position along one axis. */
function nearestIndex(pos: number, extent: number, n: number): number {
  return Math.min(n - 1, Math.max(0, Math.round((pos / extent) * n - 0.5)))
}

describe('packField', () => {
  it('puts a sensor’s heat at the texel covering its position', () => {
    // The orientation test. A transposed axis or a half-texel slip still
    // renders a plausible-looking cloud, just in the wrong place — so assert
    // the hot texel is the one actually containing the sensor.
    const sensor: SensorSample = { x: 0.4, y: 0.3, z: 2.2, value: 3.0 }
    const data = createFieldBuffer()
    packField(data, [sensor], DEFAULT_ANISOTROPY)

    const ix = nearestIndex(sensor.x, ROOM.width, TEX_W)
    const iy = nearestIndex(sensor.y, ROOM.depth, TEX_H)
    const iz = nearestIndex(sensor.z, ROOM.height, TEX_D)
    const here = data[texelOffset(ix, iy, iz)]!

    // Against the diagonally opposite corner, which is as far from this
    // sensor as the room allows.
    const far = data[texelOffset(TEX_W - 1 - ix, TEX_H - 1 - iy, TEX_D - 1 - iz)]!

    expect(here).toBeGreaterThan(far)
    expect(here).toBeGreaterThan(0)
  })

  it('separates the z axis from the others', () => {
    // Catches iz/ix transposition specifically: with one floor-level sensor,
    // a low texel must read hotter than a high one at the same x,y.
    const sensor: SensorSample = { x: 1.4, y: 1.1, z: 0.2, value: 2.5 }
    const data = createFieldBuffer()
    packField(data, [sensor], DEFAULT_ANISOTROPY)

    const ix = nearestIndex(sensor.x, ROOM.width, TEX_W)
    const iy = nearestIndex(sensor.y, ROOM.depth, TEX_H)

    const low = data[texelOffset(ix, iy, 0)]!
    const high = data[texelOffset(ix, iy, TEX_D - 1)]!

    expect(low).toBeGreaterThan(high)
  })

  it('marks furniture as solid and leaves it empty', () => {
    const sensors: SensorSample[] = [{ x: 1.4, y: 0.3, z: 0.4, value: 3.0 }]
    const data = createFieldBuffer()
    packField(data, sensors, DEFAULT_ANISOTROPY)

    const inside = {
      x: LOWER_CABINET.x + LOWER_CABINET.width / 2,
      y: LOWER_CABINET.y + LOWER_CABINET.depth / 2,
      z: LOWER_CABINET.height / 2,
    }
    const o = texelOffset(
      nearestIndex(inside.x, ROOM.width, TEX_W),
      nearestIndex(inside.y, ROOM.depth, TEX_H),
      nearestIndex(inside.z, ROOM.height, TEX_D),
    )

    expect(data[o + 3]).toBe(0) // air mask: no air in a cabinet
    expect(data[o]).toBe(0) // and nothing to colour
    expect(data[o + 2]).toBe(0) // and no density
  })

  it('leaves open air masked as air even where there is no gas', () => {
    // A texel below the visibility floor is still AIR. Marking it solid would
    // let neighbouring gas fade against an obstacle that isn't there.
    const data = createFieldBuffer()
    packField(data, [], DEFAULT_ANISOTROPY)

    const o = texelOffset(TEX_W >> 1, TEX_H >> 1, TEX_D >> 1)
    expect(data[o + 3]).toBe(255)
    expect(data[o]).toBe(0)
  })

  it('has nothing to say with no sensors, rather than throwing', () => {
    const data = createFieldBuffer()
    expect(() => packField(data, [], DEFAULT_ANISOTROPY)).not.toThrow()
  })
})

describe('calibration', () => {
  it('preserves the tuned optical depth through the room height', () => {
    // Two lines of arithmetic guarding the one hand-tuned constant that has to
    // survive the port from the slice renderer. Fails the moment someone
    // "tidies" the sigma derivation.
    expect(SIGMA_PER_M * ROOM.height).toBeCloseTo(TOTAL_OPTICAL_DEPTH, 10)
  })
})

describe('buildRampLut', () => {
  it('agrees with the colour scale the legend draws', () => {
    // Ties the shader's colour path to colorScale, which is already tested.
    const lut = buildRampLut()

    // The LUT quantises the ramp onto 256 steps, so the colour at a given slot
    // is the colour of a concentration up to half a step away — a few counts
    // per channel on the ramp's steeper segments. Asserting an exact match
    // would be asserting the quantisation is lossless, which it is not.
    const TOLERANCE = 3

    for (const pctVv of [0, 1.0, 2.0, 4.0, 10.0, 20.0]) {
      const i = Math.round(rampPosition(pctVv) * (RAMP_LUT_SIZE - 1))
      const expected = concentrationToRgb(pctVv)
      expect(Math.abs(lut[i * 4]! - expected.r)).toBeLessThanOrEqual(TOLERANCE)
      expect(Math.abs(lut[i * 4 + 1]! - expected.g)).toBeLessThanOrEqual(TOLERANCE)
      expect(Math.abs(lut[i * 4 + 2]! - expected.b)).toBeLessThanOrEqual(TOLERANCE)
    }
  })

  it('is fully populated, RGBA — not RGB, which WebGL2 cannot upload via texStorage2D', () => {
    const lut = buildRampLut()
    expect(lut).toHaveLength(RAMP_LUT_SIZE * 4)
    // Alpha channel is opaque throughout, even though the shader never reads it.
    for (let i = 0; i < RAMP_LUT_SIZE; i++) {
      expect(lut[i * 4 + 3]).toBe(255)
    }
  })
})
