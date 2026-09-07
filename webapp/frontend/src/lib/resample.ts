/**
 * resample — LTTB series onto a common time grid, for replay.
 *
 * DataAcquisition downsamples each sensor independently with LTTB, so the
 * points it returns land on DIFFERENT timestamps per sensor. That is correct
 * for plotting — LTTB preserves each series' visual shape — but unusable for
 * a heatmap, which needs every sensor's value at ONE instant.
 *
 * So replay resamples every sensor onto a shared grid (linear interpolation
 * between bracketing samples, held flat at the ends) and the scrubber indexes
 * that grid. The plot keeps using the original points; only the room needs
 * the grid.
 */

export interface SeriesPoint {
  t_ms: number
  value: number
}

export interface ResampledSeries {
  key: string
  /** One value per grid instant, aligned with `TimeGrid.times`. */
  values: Float64Array
}

export interface TimeGrid {
  times: Float64Array
  startMs: number
  endMs: number
  stepMs: number
}

/**
 * Grid resolution adapts to run length: a 30-second sensor check and a
 * two-hour ventilation tail should both end up with a scrubber that moves
 * usefully, without allocating a million frames for the long one.
 */
export function chooseGridStepMs(durationMs: number, targetFrames = 600): number {
  if (durationMs <= 0) return 1000
  const raw = durationMs / targetFrames
  // Snap to a readable cadence so the scrubber's timestamps land on round
  // numbers rather than 237ms intervals.
  const candidates = [50, 100, 200, 250, 500, 1000, 2000, 5000, 10_000, 30_000, 60_000]
  for (const c of candidates) if (raw <= c) return c
  return 120_000
}

export function buildTimeGrid(startMs: number, endMs: number, stepMs?: number): TimeGrid {
  const duration = Math.max(0, endMs - startMs)
  const step = stepMs ?? chooseGridStepMs(duration)
  // A run shorter than one grid step still gets a single frame, so replay of
  // a very short run shows something rather than an empty scrubber.
  const count = Math.max(1, Math.floor(duration / step) + 1)
  const times = new Float64Array(count)
  for (let i = 0; i < count; i++) times[i] = startMs + i * step
  return { times, startMs, endMs, stepMs: step }
}

/**
 * Value of one series at an arbitrary instant.
 *
 * Linear interpolation between the bracketing samples; held flat before the
 * first and after the last. Holding rather than extrapolating matters: a
 * sensor that stopped reporting must not have a trend projected past its
 * last real measurement.
 */
export function valueAt(points: readonly SeriesPoint[], tMs: number): number {
  if (points.length === 0) return 0
  const first = points[0]!
  const last = points[points.length - 1]!
  if (tMs <= first.t_ms) return first.value
  if (tMs >= last.t_ms) return last.value

  // Binary search for the first point at or after t.
  let lo = 0
  let hi = points.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (points[mid]!.t_ms < tMs) lo = mid + 1
    else hi = mid
  }

  const b = points[lo]!
  if (b.t_ms === tMs || lo === 0) return b.value
  const a = points[lo - 1]!

  const span = b.t_ms - a.t_ms
  if (span <= 0) return b.value
  const f = (tMs - a.t_ms) / span
  return a.value + (b.value - a.value) * f
}

/** Resamples one series onto a grid. */
export function resampleSeries(
  key: string,
  points: readonly SeriesPoint[],
  grid: TimeGrid,
): ResampledSeries {
  const values = new Float64Array(grid.times.length)
  for (let i = 0; i < grid.times.length; i++) {
    values[i] = valueAt(points, grid.times[i]!)
  }
  return { key, values }
}

/**
 * Resamples every sensor onto one shared grid — the operation the heatmap
 * needs and LTTB cannot provide.
 */
export function resampleAll(
  series: ReadonlyArray<{ key: string; points: readonly SeriesPoint[] }>,
  grid: TimeGrid,
): ResampledSeries[] {
  return series.map((s) => resampleSeries(s.key, s.points, grid))
}

/** Grid index nearest an instant — what the scrubber maps a drag onto. */
export function indexForTime(grid: TimeGrid, tMs: number): number {
  if (grid.times.length === 0) return 0
  const i = Math.round((tMs - grid.startMs) / grid.stepMs)
  return Math.min(grid.times.length - 1, Math.max(0, i))
}

/** Earliest and latest timestamps across every series. */
export function seriesExtent(
  series: ReadonlyArray<{ points: readonly SeriesPoint[] }>,
): { startMs: number; endMs: number } | null {
  let start = Infinity
  let end = -Infinity
  for (const s of series) {
    if (s.points.length === 0) continue
    const first = s.points[0]!
    const last = s.points[s.points.length - 1]!
    if (first.t_ms < start) start = first.t_ms
    if (last.t_ms > end) end = last.t_ms
  }
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null
  return { startMs: start, endMs: end }
}
