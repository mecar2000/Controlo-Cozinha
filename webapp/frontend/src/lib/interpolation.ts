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

/** Inverse-distance weighting exponent. 2 is the conventional choice.
 *
 *  Exported as documentation of the weighting, not as a tunable: the hot loop
 *  in `sampleField` hardcodes the power-2 case (weight = 1/d², computed from
 *  the SQUARED distance so no sqrt is needed at all). Changing this constant
 *  alone will not change the weighting — see the note there. */
export const IDW_POWER = 2

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

  // HOT LOOP. Field.tsx calls this once per texel of every slice — order
  // 10^5 calls per resample — so it works entirely in SQUARED distances and
  // takes exactly one sqrt at the end, for confidence.
  //
  // Both sqrts that used to be here are removable because IDW_POWER is 2:
  // the weight 1/d^2 IS the reciprocal of the squared distance, so squaring
  // a square root just to undo it was pure waste. Math.pow(d, 2) is gone for
  // the same reason — it is roughly an order of magnitude slower than a
  // multiply, and there is no multiply left to do.
  //
  // The comparison against COINCIDENT_EPSILON_M is likewise squared, so the
  // coincident-sensor threshold is unchanged in metres.
  let weightedSum = 0
  let weightTotal = 0
  let nearestTrueDistanceSq = Infinity

  const epsilonSq = COINCIDENT_EPSILON_M * COINCIDENT_EPSILON_M
  const anisotropySq = anisotropy * anisotropy

  for (const s of sensors) {
    const dx = x - s.x
    const dy = y - s.y
    const dz = z - s.z

    const horizontalSq = dx * dx + dy * dy

    // True (unweighted) squared distance drives confidence: how far the
    // nearest real measurement is. Kept squared until the single sqrt below.
    const trueDSq = horizontalSq + dz * dz
    if (trueDSq < nearestTrueDistanceSq) nearestTrueDistanceSq = trueDSq

    // Anisotropic squared distance: vertical separation is divided by the
    // anisotropy factor before squaring, i.e. its square is divided by the
    // factor squared. Same value anisotropicDistance() returns, squared.
    const dSq = horizontalSq + (dz * dz) / anisotropySq

    if (dSq < epsilonSq) {
      // Sitting on a sensor: take its value outright, full confidence.
      return { value: s.value, confidence: 1 }
    }

    const w = 1 / dSq
    weightedSum += w * s.value
    weightTotal += w
  }

  const value = weightTotal === 0 ? 0 : weightedSum / weightTotal
  const confidence = Math.max(
    0,
    1 - Math.sqrt(nearestTrueDistanceSq) / CONFIDENCE_RANGE_M,
  )

  return { value, confidence }
}

/** Highest concentration any sensor is currently reading. */
export function peakConcentration(sensors: readonly SensorSample[]): number {
  let peak = 0
  for (const s of sensors) if (s.value > peak) peak = s.value
  return peak
}
