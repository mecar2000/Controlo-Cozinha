/**
 * Header — identity, the three connection dots, and the view toggle.
 *
 * The dots are the only place the green glow survives, because peripheral
 * detection is exactly what a glow is good for: an operator who is not
 * looking at the screen should still notice one going dark.
 *
 * Chrome here is neutral. Green means safe and nothing else, so the active
 * view tab is marked by weight and a rule, not by colour.
 */

import type { PermitStatus, Status } from '@/api/types'
import { formatAge } from '@/lib/format'

export type ViewName = 'control' | 'analysis' | 'display'

const VIEWS: Array<{ id: ViewName; label: string }> = [
  { id: 'control', label: 'control' },
  { id: 'analysis', label: 'analysis' },
  { id: 'display', label: 'display' },
]

type DotState = 'safe' | 'armed' | 'live' | 'off'

function Dot({
  state,
  label,
  title,
}: {
  state: DotState
  label: string
  title: string
}) {
  return (
    <span className="flex items-center gap-1.5" title={title}>
      <span className={`status-dot status-dot-${state}`} aria-hidden />
      <span className="text-ink-dim">{label}</span>
      <span className="sr-only">{title}</span>
    </span>
  )
}

/**
 * Permit absence is deliberately distinct from permit denial.
 *
 * The firmware's own rule: an absent permit warns and never trips, because
 * no permit is published outside a run at all. Showing "denied" for a permit
 * that was simply never sent would train operators to ignore the dot.
 */
function permitDot(permit: PermitStatus): { state: DotState; title: string } {
  if (permit.ok === null) {
    return { state: 'off', title: 'Permit: never seen. Absence warns; it never stops a run.' }
  }
  if (permit.ok === false) {
    return { state: 'live', title: 'Permit withdrawn by the lab safety server' }
  }
  const age = permit.last_seen_age_s
  if (age != null && age > 10) {
    return { state: 'armed', title: `Permit: last heartbeat ${formatAge(age)}` }
  }
  return { state: 'safe', title: 'Permit granted' }
}

export function Header({
  status,
  view,
  onViewChange,
  offline,
}: {
  status: Status | null
  view: ViewName
  onViewChange: (v: ViewName) => void
  offline: boolean
}) {
  const permit = status ? permitDot(status.permit) : { state: 'off' as DotState, title: 'Permit: unknown' }

  const mqtt: { state: DotState; title: string } = !status
    ? { state: 'off', title: 'Broker: unknown' }
    : !status.mqtt.connected
      ? { state: 'live', title: 'Broker disconnected — no commands can be sent' }
      : status.mqtt.stale
        ? {
            state: 'armed',
            title: `Broker connected but quiet — last message ${formatAge(status.mqtt.last_message_age_s)}`,
          }
        : { state: 'safe', title: 'Broker connected' }

  const daq: { state: DotState; title: string } = !status
    ? { state: 'off', title: 'Historian: unknown' }
    : status.daq.recording_lost
      ? { state: 'live', title: 'Recording lost — the run continues but is not being stored' }
      : status.daq.reachable === false
        ? { state: 'armed', title: 'Historian unreachable — starting a run is blocked' }
        : status.daq.reachable === null
          ? { state: 'off', title: 'Historian: not yet checked' }
          : { state: 'safe', title: 'Historian reachable' }

  return (
    <header className="relative flex h-14 shrink-0 items-center justify-between border-b border-hairline bg-panel pl-4 pr-4">
      {/* Left accent bar, the one piece of the style guide's chrome kept —
          it marks the app's identity without spending a semantic colour. */}
      <span className="absolute left-0 top-0 h-full w-0.5 bg-ink-faint" aria-hidden />

      <div className="flex items-baseline gap-3">
        <span className="font-bold tracking-tight text-ink">HYLAB</span>
        <span className="text-ink-dim">Kitchen</span>
        {offline && (
          <span className="text-live" role="status">
            server unreachable
          </span>
        )}
      </div>

      <div className="flex items-center gap-5">
        <div className="flex items-center gap-4" role="group" aria-label="Connection health">
          <Dot state={permit.state} label="permit" title={permit.title} />
          <Dot state={daq.state} label="daq" title={daq.title} />
          <Dot state={mqtt.state} label="mqtt" title={mqtt.title} />
        </div>

        <nav className="flex items-center" aria-label="View">
          {VIEWS.map((v) => {
            const active = v.id === view
            return (
              <button
                key={v.id}
                type="button"
                onClick={() => onViewChange(v.id)}
                aria-current={active ? 'page' : undefined}
                className={`border-b-2 px-3 py-1.5 transition-colors ${
                  active
                    ? 'border-ink font-medium text-ink'
                    : 'border-transparent text-ink-dim hover:text-ink'
                }`}
              >
                {v.label}
              </button>
            )
          })}
        </nav>
      </div>
    </header>
  )
}
