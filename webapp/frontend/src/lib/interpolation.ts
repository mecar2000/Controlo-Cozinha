/**
 * interpolation — sensor readings to a 3D concentration field.
 *
 * Two properties matter more than smoothness here:
 *
 * 1. ANISOTROPY. Hydrogen is buoyant and stratifies at the ceiling. Isotropic
 *    weighting would draw a ceiling-level reading bleeding downward as
 *    readily as sideways, painting H2 pooling low — the opposite of the
 *    physics. Vertical distance is therefore compressed relative to
 *    horizontal, so a sensor's influence spreads along its own height layer
 *    much further than it spreads up or down.
 *
 * 2. CONFIDENCE. Opacity falls off with distance from the nearest sensor, so
 *    regions the sensors genuinely constrain look solid and guesswork looks
 *    thin. Six sensors cannot describe a room; the field must not pretend
 *    otherwise. Sensors-only mode stays one click away as the trustworthy
 *    baseline.
 */

import { isInsideSolid, ROOM } from './roomGeometry'

/**
 * How much vertical distance is compressed relative to horizontal.
 *
 * A value of 1 is isotropic. Below 1, vertical separation counts for LESS
 * than horizontal, so influence spreads sideways within a height layer.
 *
 * UNVERIFIED ESTIMATE. The design spec lists this as an open question needing
 * a real run to calibrate against. It is deliberately a named, surfaced
 * tunable rather than a number buried in a loop — the settings panel shows
 * it, and the field legend says when it is at its default.
 */
export const DEFAULT_ANISOTROPY = 0.35

/** Inverse-distance weighting exponent. 2 is the conventional choice. */
const IDW_POWER = 2

/** Below this separation, a sample takes the sensor's value outright,
 *  avoiding a division by zero at the sensor position itself. */
const COINCIDENT_EPSILON_M = 0.01

/**
 * Distance beyond which a sample is treated as unconstrained.
 *
 * Sized against the real sensor spacing in this room, which is roughly
 * 1.2-1.6 m between neighbours. A range shorter than that spacing leaves the
 * volume BETWEEN sensors at near-zero confidence, and the field renders as
 * thin shells clinging to each sensor and the surfaces near it — which is
 * what a 1.2 m range produced here. At 2.2 m the region actually bracketed
 * by sensors reads as solid, and confidence still falls away in the corners
 * and the near half of the room where nothing is measuring.
 */
export const CONFIDENCE_RANGE_M = 2.2

export interface SensorSample {
  x: number
  y: number
  z: number
  /** %v/v. */
  value: number
}

export interface FieldSample {
  /** Interpolated concentration, %v/v. */
  value: number
  /**
   * 0..1. How well the sensors constrain this point: 1 at a sensor, falling
   * to 0 at CONFIDENCE_RANGE_M away. Drives opacity, so guesswork looks
   * thin.
   */
  confidence: number
}

/**
 * Anisotropic distance: horizontal separation at face value, vertical
 * separation divided by the anisotropy factor so it counts for more distance
 * (and therefore less influence) than the same separation sideways.
 */
export function anisotropicDistance(
  ax: number,
  ay: number,
  az: number,
  bx: number,
  by: number,
  bz: number,
  anisotropy: number = DEFAULT_ANISOTROPY,
): number {
  const dx = ax - bx
  const dy = ay - by
  const dz = (az - bz) / anisotropy
  return Math.sqrt(dx * dx + dy * dy + dz * dz)
}

/**
 * Inverse-distance-weighted concentration at one point, with the confidence
 * that goes with it.
 *
 * Returns zero confidence for an empty sensor list rather than throwing: a
 * layout with no enabled sensors is a real state (during setup), and the
 * field simply has nothing to say about it.
 */
export function sampleField(
  x: number,
  y: number,
  z: number,
  sensors: readonly SensorSample[],
  anisotropy: number = DEFAULT_ANISOTROPY,
): FieldSample {
  if (sensors.length === 0) return { value: 0, confidence: 0 }

  let weightedSum = 0
  let weightTotal = 0
  let nearestTrueDistance = Infinity

  for (const s of sensors) {
    const d = anisotropicDistance(x, y, z, s.x, s.y, s.z, anisotropy)

    // True (unweighted) distance drives confidence: how far the nearest
    // real measurement is, in metres a person can reason about.
    const tdx = x - s.x
    const tdy = y - s.y
    const tdz = z - s.z
    const trueD = Math.sqrt(tdx * tdx + tdy * tdy + tdz * tdz)
    if (trueD < nearestTrueDistance) nearestTrueDistance = trueD

    if (d < COINCIDENT_EPSILON_M) {
      // Sitting on a sensor: take its value outright, full confidence.
      return { value: s.value, confidence: 1 }
    }

    const w = 1 / Math.pow(d, IDW_POWER)
    weightedSum += w * s.value
    weightTotal += w
  }

  const value = weightTotal === 0 ? 0 : weightedSum / weightTotal
  const confidence = Math.max(0, 1 - nearestTrueDistance / CONFIDENCE_RANGE_M)

  return { value, confidence }
}

export interface FieldGrid {
  /** Cells along each axis. */
  nx: number
  ny: number
  nz: number
  /** Cell spacing in metres. */
  step: number
  /** Concentration per cell, indexed [ix + nx * (iy + ny * iz)]. */
  values: Float32Array
  /** Confidence per cell, same indexing. */
  confidence: Float32Array
  /** True for cells inside furniture, which hold no air. */
  solid: Uint8Array
}

/**
 * Samples the whole room onto a regular grid.
 *
 * Cells inside solid furniture are marked and left at zero: there is no air
 * inside a cabinet, and drawing concentration there is visibly wrong.
 */
export function buildFieldGrid(
  sensors: readonly SensorSample[],
  step = 0.15,
  anisotropy: number = DEFAULT_ANISOTROPY,
): FieldGrid {
  const nx = Math.max(1, Math.ceil(ROOM.width / step))
  const ny = Math.max(1, Math.ceil(ROOM.depth / step))
  const nz = Math.max(1, Math.ceil(ROOM.height / step))

  const values = new Float32Array(nx * ny * nz)
  const confidence = new Float32Array(nx * ny * nz)
  const solid = new Uint8Array(nx * ny * nz)

  for (let iz = 0; iz < nz; iz++) {
    const z = (iz + 0.5) * step
    for (let iy = 0; iy < ny; iy++) {
      const y = (iy + 0.5) * step
      for (let ix = 0; ix < nx; ix++) {
        const x = (ix + 0.5) * step
        const i = ix + nx * (iy + ny * iz)

        if (isInsideSolid(x, y, z)) {
          solid[i] = 1
          continue
        }

        const s = sampleField(x, y, z, sensors, anisotropy)
        values[i] = s.value
        confidence[i] = s.confidence
      }
    }
  }

  return { nx, ny, nz, step, values, confidence, solid }
}

/** Highest concentration any sensor is currently reading. */
export function peakConcentration(sensors: readonly SensorSample[]): number {
  let peak = 0
  for (const s of sensors) if (s.value > peak) peak = s.value
  return peak
}
