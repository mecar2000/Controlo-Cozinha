/**
 * format — how numbers and durations are rendered.
 *
 * Every value in this interface is monospaced with tabular figures, so a
 * ticking counter never reflows the layout around it. Fixed decimal places
 * matter for the same reason: 1.8 -> 1.84 must not change the string's
 * width.
 */

/** mm:ss, or h:mm:ss past an hour. */
export function formatElapsed(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '--:--'
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

/** A duration as an operator would write it in a spec: "5s", "2m 30s". */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return '—'
  const total = Math.round(ms / 1000)
  if (total < 60) return `${total}s`
  const m = Math.floor(total / 60)
  const s = total % 60
  if (m < 60) return s === 0 ? `${m}m` : `${m}m ${s}s`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return rm === 0 ? `${h}h` : `${h}h ${rm}m`
}

/** Fixed decimals, so the string width never changes as the value ticks. */
export function formatNumber(
  value: number | null | undefined,
  decimals = 2,
): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value.toFixed(decimals)
}

export function formatPct(value: number | null | undefined, decimals = 0): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value.toFixed(decimals)}`
}

/** Wall-clock time, for the "as of" stamps beside live values. */
export function formatClock(ts: number | Date | null | undefined): string {
  if (ts == null) return '—'
  const d = typeof ts === 'number' ? new Date(ts) : ts
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/** Date and time, for run records. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/** "4 minutes ago" for staleness, where the exact second does not matter. */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return 'never'
  if (seconds < 2) return 'just now'
  if (seconds < 60) return `${Math.floor(seconds)}s ago`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  return `${h}h ago`
}

/**
 * Latch causes as an operator reads them.
 *
 * The enum stays the firmware's own vocabulary in the record — it is what
 * makes "how many runs tripped on quorum this month" a query. This is only
 * the display string.
 */
export const LATCH_CAUSE_LABELS: Record<string, string> = {
  LOCAL_SENSOR_THRESHOLD: 'A kitchen sensor read at or above its threshold',
  LOCAL_SENSOR_STALE: 'A kitchen sensor stopped reporting',
  FLOW_OVER_LIMIT: 'Gas flow exceeded the limit',
  INVENTORY_CAP_EXCEEDED: 'Delivered gas passed the inventory cap',
  PEER_ALARM: 'Another lab PLC raised a hydrogen alarm',
  PERMIT_DENIED: 'The lab safety permit was withdrawn',
  ESTOP: 'The emergency stop was pressed',
  EXTERNAL_TRIP: 'An external trip input fired',
  OPERATOR_ABORT: 'The role selector was moved away from leak-test mid-run',
  EXTERNAL_H2_THRESHOLD: 'Hydrogen detected outside the kitchen (voltage regulation stage)',
  EXTERNAL_H2_SENSOR_FAULT: 'An external hydrogen sensor stopped reporting or is disconnected',
}

export function latchCauseLabel(cause: string | null | undefined): string {
  if (!cause) return 'Unknown cause'
  return LATCH_CAUSE_LABELS[cause] ?? cause
}

/** Run outcomes as prose. */
export const OUTCOME_LABELS: Record<string, string> = {
  pending: 'Running',
  completed: 'Completed',
  stopped: 'Stopped by operator',
  latched: 'Stopped on a safety trip',
  rejected: 'Rejected by the firmware',
  aborted: 'Cancelled before confirm',
  expired: 'Armed but never confirmed',
}

export function outcomeLabel(outcome: string | null | undefined): string {
  if (!outcome) return '—'
  return OUTCOME_LABELS[outcome] ?? outcome
}
