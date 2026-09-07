/**
 * NumericRail — the phase, the clock, and the handful of numbers that matter.
 *
 * Always in the same place, so the eye learns one location per value. Labels
 * left, values right-aligned in their own column so magnitudes line up. No
 * card chrome per value: one surface, hairline dividers, nothing decorative
 * between the operator and the number.
 *
 * The phase name is rendered exactly as the firmware sends it —
 * FULLY_VENTILATING, not "Fully ventilating" — because that string is what
 * appears in the logs, the MQTT topic and the run record. One vocabulary.
 */

import type { KitchenPhase, KitchenState, RegisterSet } from '@/api/types'
import { isAtOrAboveLel } from '@/lib/colorScale'
import { formatElapsed, formatNumber } from '@/lib/format'

/** Phase to semantic colour. Amber is ARMED — genuinely neither safe nor
 *  live: validated, awaiting confirm, nothing flowing. */
function phaseColor(phase: KitchenPhase | undefined): string {
  switch (phase) {
    case 'LEAKING':
      return 'text-live'
    case 'ARMED':
      return 'text-armed'
    case 'FULLY_VENTILATING':
      return 'text-armed'
    case 'HOLD':
    case 'VENTILATING':
      return 'text-ink'
    case 'WAITING':
      return 'text-safe'
    default:
      return 'text-ink-dim'
  }
}

function Row({
  label,
  children,
  emphasis = false,
}: {
  label: string
  children: React.ReactNode
  emphasis?: boolean
}) {
  return (
    <div className="rail-row border-b border-hairline last:border-b-0">
      <span className="rail-label">{label}</span>
      <span className={`rail-value ${emphasis ? 'text-live' : 'text-ink'}`}>{children}</span>
    </div>
  )
}

/** Registers as three filled/hollow pips — a set, not a number. */
function RegisterPips({ registers }: { registers: RegisterSet | undefined }) {
  const items: Array<[keyof RegisterSet, string]> = [
    ['central', 'central'],
    ['exhaust', 'exhaust'],
    ['inlet', 'inlet'],
  ]
  return (
    <span className="flex items-center gap-1.5">
      {items.map(([key, name]) => {
        const open = registers?.[key] ?? false
        return (
          <span
            key={key}
            title={`${name}: ${open ? 'open' : 'closed'}`}
            className={`inline-block h-3 w-3 rounded-[2px] border ${
              open ? 'border-ink bg-ink' : 'border-ink-faint bg-transparent'
            }`}
          />
        )
      })}
    </span>
  )
}

export function NumericRail({
  kitchenState,
  runLabel,
  peakPctVv,
  stale,
}: {
  kitchenState: KitchenState | undefined
  /** e.g. "run 12 · standard 5s". Null when nothing is running. */
  runLabel: string | null
  peakPctVv: number
  stale: boolean
}) {
  const phase = kitchenState?.phase ?? kitchenState?.state
  const elapsed = kitchenState?.elapsedMs
  const peakAlarming = isAtOrAboveLel(peakPctVv)

  return (
    <div className="flex flex-col">
      {/* Phase and clock: the two things read from across the room. */}
      <div className="flex items-baseline justify-between gap-3 pb-1">
        <span className={`text-lede font-bold tracking-tight ${phaseColor(phase)}`}>
          {phase ?? 'NO SIGNAL'}
        </span>
        <span className="text-lede tabular-nums text-ink">{formatElapsed(elapsed)}</span>
      </div>

      <div className="min-h-5 pb-3 text-ink-dim">{runLabel ?? 'no run in progress'}</div>

      {stale && (
        <p className="prose-text mb-3 border-l-2 border-armed pl-2 text-armed" role="status">
          Live data is stale. These are last-known values, not current
          readings.
        </p>
      )}

      <div className="border-t border-hairline">
        <Row label="peak" emphasis={peakAlarming && !stale}>
          {formatNumber(peakPctVv, 2)} <span className="text-ink-dim">%v/v</span>
        </Row>
        <Row label="delivered">
          {formatNumber(kitchenState?.deliveredInventory_mL, 0)}{' '}
          <span className="text-ink-dim">mL</span>
        </Row>
        <Row label="flow">
          {formatNumber(kitchenState?.flowRate_mLps, 1)}{' '}
          <span className="text-ink-dim">mL/s</span>
        </Row>
        <Row label="gas setpoint">
          {formatNumber(kitchenState?.gasSetpointPct, 0)}{' '}
          <span className="text-ink-dim">%</span>
        </Row>
        <Row label="fan">
          {formatNumber(kitchenState?.fanSpeedPct, 0)} <span className="text-ink-dim">%</span>
        </Row>
        <Row label="registers">
          <RegisterPips registers={kitchenState?.registers} />
        </Row>
        <Row label="role">
          <span className="text-ink-dim">{kitchenState?.role ?? '—'}</span>
        </Row>
      </div>
    </div>
  )
}
