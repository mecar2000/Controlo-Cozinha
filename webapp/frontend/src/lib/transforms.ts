/**
 * transforms — the five plot modes, over one fetched series.
 *
 * DataAcquisition already stores raw and converted per reading with conv_id
 * provenance, so none of these need new storage or a second fetch. They are a
 * few lines each over data already in hand.
 *
 *   raw        V / mA as measured
 *   converted  ppm or %v/v
 *   minmax     0-1 over the visible window; shapes directly comparable
 *   baseline   each sensor minus its own pre-leak resting value
 *   zscore     mean 0, SD 1 over the window
 *
 * The heatmap is ALWAYS %v/v regardless of this toggle, because its colour
 * scale is pinned to LEL. This setting governs the plot only.
 */

export type PlotMode = 'raw' | 'converted' | 'minmax' | 'baseline' | 'zscore'

export const PLOT_MODES: ReadonlyArray<{ id: PlotMode; label: string; hint: string }> = [
  { id: 'raw', label: 'raw', hint: 'V or mA as measured' },
  { id: 'converted', label: 'converted', hint: 'ppm or %v/v' },
  { id: 'minmax', label: 'min-max', hint: '0-1 over the visible window' },
  { id: 'baseline', label: 'baseline', hint: 'change from each sensor’s resting value' },
  { id: 'zscore', label: 'z-score', hint: 'mean 0, SD 1 over the window' },
]

export interface Point {
  t_ms: number
  value: number
  raw_value?: number
}

/**
 * Applies a plot mode to one series.
 *
 * `baselineEndMs` bounds the pre-leak resting window for baseline mode —
 * normally the moment the leak phase began, so "resting" means genuinely
 * before any gas flowed.
 */
export function applyTransform(
  points: readonly Point[],
  mode: PlotMode,
  baselineEndMs?: number,
): Point[] {
  if (points.length === 0) return []

  switch (mode) {
    case 'raw':
      // Falls back to the converted value when the historian returned no
      // raw column, rather than plotting a flat line of zeroes.
      return points.map((p) => ({ ...p, value: p.raw_value ?? p.value }))

    case 'converted':
      return points.map((p) => ({ ...p }))

    case 'minmax': {
      let min = Infinity
      let max = -Infinity
      for (const p of points) {
        if (p.value < min) min = p.value
        if (p.value > max) max = p.value
      }
      const span = max - min
      // A flat series has no range to normalise over. Draw it along the
      // bottom rather than dividing by zero.
      if (span === 0) return points.map((p) => ({ ...p, value: 0 }))
      return points.map((p) => ({ ...p, value: (p.value - min) / span }))
    }

    case 'baseline': {
      const cutoff = baselineEndMs ?? points[0]!.t_ms
      const resting = points.filter((p) => p.t_ms <= cutoff)
      // With no pre-leak window (a run whose recording started late), fall
      // back to the first sample so the mode still says something true:
      // change relative to where this sensor started.
      const sample = resting.length > 0 ? resting : [points[0]!]
      const mean = sample.reduce((acc, p) => acc + p.value, 0) / sample.length
      return points.map((p) => ({ ...p, value: p.value - mean }))
    }

    case 'zscore': {
      const n = points.length
      const mean = points.reduce((acc, p) => acc + p.value, 0) / n
      const variance = points.reduce((acc, p) => acc + (p.value - mean) ** 2, 0) / n
      const sd = Math.sqrt(variance)
      if (sd === 0) return points.map((p) => ({ ...p, value: 0 }))
      return points.map((p) => ({ ...p, value: (p.value - mean) / sd }))
    }
  }
}

/** Y-axis unit label for a mode. Normalised modes are genuinely unitless. */
export function unitForMode(mode: PlotMode, rawUnit = 'mA', convertedUnit = '%v/v'): string {
  switch (mode) {
    case 'raw':
      return rawUnit
    case 'converted':
      return convertedUnit
    case 'minmax':
      return '0-1'
    case 'baseline':
      return `Δ ${convertedUnit}`
    case 'zscore':
      return 'SD'
  }
}

/** Y extent across several already-transformed series, with a little padding
 *  so the topmost peak is not clipped by the axis. */
export function yExtent(series: ReadonlyArray<{ points: readonly Point[] }>): [number, number] {
  let min = Infinity
  let max = -Infinity
  for (const s of series) {
    for (const p of s.points) {
      if (p.value < min) min = p.value
      if (p.value > max) max = p.value
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1]
  if (min === max) return [min - 0.5, max + 0.5]
  const pad = (max - min) * 0.06
  return [min - pad, max + pad]
}
