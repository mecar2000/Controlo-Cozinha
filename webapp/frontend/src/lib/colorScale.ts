/**
 * colorScale — hydrogen concentration to colour.
 *
 * PINNED TO ABSOLUTE CONCENTRATION, NEVER AUTO-SCALED. Auto-scaling to the
 * current min/max would paint a safe 0.2% room bright red, which trains
 * people to ignore the display. The same 1.8 %v/v is the same colour today
 * as it was last month.
 *
 * The ramp is piecewise:
 *   0-4 %v/v   (0-100% LEL)  ~70% of the ramp — fine resolution where the
 *                             experiment actually lives
 *   4-20 %v/v                ~30% — compressed but still readable, so 4%,
 *                             10% and 20% stay visibly distinct
 *   >=20 %v/v                saturates
 *
 * It ends in pale magenta rather than a deeper red: past LEL the field should
 * stop reading as "hot" and start reading as WRONG, in a colour that has no
 * other job in the interface.
 */

/** Lower explosive limit for hydrogen in air, %v/v. */
export const LEL_PCT_VV = 4.0

/** Concentration at which the ramp saturates. */
export const SCALE_MAX_PCT_VV = 20.0

/** Fraction of the ramp spent below LEL. */
const BELOW_LEL_SHARE = 0.7

export interface Rgb {
  r: number
  g: number
  b: number
}

/** Ramp stops as [position 0..1 along the ramp, colour]. */
const STOPS: Array<[number, Rgb]> = [
  // 0 -> 4 %v/v, occupying the first 70% of the ramp.
  [0.0, { r: 0x0b, g: 0x3d, b: 0x91 }],
  [0.175, { r: 0x1b, g: 0x9a, b: 0xaa }],
  [0.35, { r: 0x7f, g: 0xd8, b: 0x58 }],
  [0.525, { r: 0xf2, g: 0xe6, b: 0x35 }],
  [0.7, { r: 0xff, g: 0x8a, b: 0x1f }],
  // 4 -> 20 %v/v, occupying the remaining 30%.
  [0.8, { r: 0xe0, g: 0x1b, b: 0x1b }],
  [0.9, { r: 0x8e, g: 0x0b, b: 0x52 }],
  [1.0, { r: 0xf0, g: 0xa6, b: 0xff }],
]

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

/**
 * Maps %v/v onto its position along the ramp, 0..1.
 *
 * Exported because the legend needs the same mapping to place its ticks —
 * a legend drawn on a different scale from the field would be worse than
 * no legend.
 */
export function rampPosition(pctVv: number): number {
  if (!Number.isFinite(pctVv) || pctVv <= 0) return 0
  if (pctVv >= SCALE_MAX_PCT_VV) return 1
  if (pctVv <= LEL_PCT_VV) {
    return (pctVv / LEL_PCT_VV) * BELOW_LEL_SHARE
  }
  const aboveShare = (pctVv - LEL_PCT_VV) / (SCALE_MAX_PCT_VV - LEL_PCT_VV)
  return BELOW_LEL_SHARE + aboveShare * (1 - BELOW_LEL_SHARE)
}

/**
 * The inverse of rampPosition: the concentration at a given point along the
 * ramp. Used to bake the ramp into a lookup table, which is indexed by ramp
 * position but has to be filled by concentration.
 */
export function rampPositionToPctVv(p: number): number {
  if (p <= 0) return 0
  if (p >= 1) return SCALE_MAX_PCT_VV
  if (p <= BELOW_LEL_SHARE) {
    return (p / BELOW_LEL_SHARE) * LEL_PCT_VV
  }
  const aboveShare = (p - BELOW_LEL_SHARE) / (1 - BELOW_LEL_SHARE)
  return LEL_PCT_VV + aboveShare * (SCALE_MAX_PCT_VV - LEL_PCT_VV)
}

/** Colour for a concentration in %v/v. */
export function concentrationToRgb(pctVv: number): Rgb {
  const p = rampPosition(pctVv)

  let lower = STOPS[0]!
  let upper = STOPS[STOPS.length - 1]!
  for (let i = 0; i < STOPS.length - 1; i++) {
    const a = STOPS[i]!
    const b = STOPS[i + 1]!
    if (p >= a[0] && p <= b[0]) {
      lower = a
      upper = b
      break
    }
  }

  const span = upper[0] - lower[0]
  const t = span === 0 ? 0 : (p - lower[0]) / span
  return {
    r: Math.round(lerp(lower[1].r, upper[1].r, t)),
    g: Math.round(lerp(lower[1].g, upper[1].g, t)),
    b: Math.round(lerp(lower[1].b, upper[1].b, t)),
  }
}

export function concentrationToCss(pctVv: number): string {
  const { r, g, b } = concentrationToRgb(pctVv)
  return `rgb(${r} ${g} ${b})`
}

/** Packed 0xRRGGBB, for Three.js materials. */
export function concentrationToHex(pctVv: number): number {
  const { r, g, b } = concentrationToRgb(pctVv)
  return (r << 16) | (g << 8) | b
}

/** %v/v as a percentage of the lower explosive limit. */
export function pctVvToPctLel(pctVv: number): number {
  return (pctVv / LEL_PCT_VV) * 100
}

export function pctLelToPctVv(pctLel: number): number {
  return (pctLel / 100) * LEL_PCT_VV
}

/** True at or above LEL. At-or-above (>=) matches the firmware's boundary
 *  convention everywhere — a reading exactly at threshold trips. */
export function isAtOrAboveLel(pctVv: number): boolean {
  return pctVv >= LEL_PCT_VV
}

/**
 * Legend ticks, in %v/v. Chosen so both regimes stay readable.
 *
 * Sparse above LEL on purpose. The ramp compresses 4-20 %v/v into its last
 * 30%, so evenly-spaced ticks up there (8, 12, 16, 20) landed about 17px
 * apart on the compact legend — close enough for two-digit labels to collide.
 * Below LEL is where the experiment lives and where the ramp has room, so
 * that half keeps its fine ticks.
 */
export const LEGEND_TICKS_PCT_VV = [0, 1, 2, 3, 4, 10, 20] as const

/** CSS linear-gradient stops for the legend bar, matching the field exactly. */
export function legendGradientCss(): string {
  const stops = STOPS.map(([p, c]) => `rgb(${c.r} ${c.g} ${c.b}) ${(p * 100).toFixed(1)}%`)
  return `linear-gradient(to right, ${stops.join(', ')})`
}
