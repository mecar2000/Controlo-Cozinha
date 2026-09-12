/**
 * Header — identity, the three connection dots, and the view toggle.
 *
 * The dots are the only place the green glow survives, because peripheral
 * detection is exactly what a glow is good for: an operator who is not
 * looking at the screen should still notice one going dark. Those dots are
 * SEMANTIC green.
 *
 * Everything else green in this bar is CHROME green (--color-brand-*): the
 * logo and the active tab. The two roles are allowed to share a hue because
 * they never share a glance — see the note at the top of index.css. The
 * active tab is a washed region with an underline rather than a bare rule,
 * so it survives being read at an angle from across the lab.
 *
 * The wordmark is deliberately absent: the mark already says HyLab, and
 * repeating it in type next to itself is the one accessory worth removing.
 * What the mark cannot say is which rig this is, so that is what the type
 * says instead.
 */

import type { PermitStatus, Status } from '@/api/types'
import { formatAge } from '@/lib/format'

export type ViewName = 'control' | 'analysis' | 'display'

/** Labels are sentence case in Archivo, not lowercase mono: these are places
 *  in an application, not values read off a sensor. Mono is reserved for
 *  machine-reported content. */
const VIEWS: Array<{ id: ViewName; label: string }> = [
  { id: 'control', label: 'Control' },
  { id: 'analysis', label: 'Analysis' },
  { id: 'display', label: 'Display' },
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
      <span className="font-sans text-label text-ink-dim">{label}</span>
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
    <header className="relative flex h-14 shrink-0 items-stretch justify-between border-b border-hairline bg-panel pl-4 pr-4">
      <div className="flex items-center gap-2.5">
        <img
          src="/hylab-mark.png"
          alt="HyLab"
          width={26}
          height={26}
          className="shrink-0"
        />
        <span className="font-sans font-medium tracking-tight text-ink">
          Kitchen H₂ Control
        </span>
        {offline && (
          <span className="prose-text ml-1 text-label text-live" role="status">
            server unreachable
          </span>
        )}
      </div>

      <div className="flex items-stretch gap-5">
        <div className="flex items-center gap-4" role="group" aria-label="Connection health">
          <Dot state={permit.state} label="permit" title={permit.title} />
          <Dot state={daq.state} label="daq" title={daq.title} />
          <Dot state={mqtt.state} label="mqtt" title={mqtt.title} />
        </div>

        {/* Tabs run the full height of the bar so the active one's wash
            reads as a region of the header rather than a floating chip. */}
        <nav className="flex items-stretch" aria-label="View">
          {VIEWS.map((v) => {
            const active = v.id === view
            return (
              <button
                key={v.id}
                type="button"
                onClick={() => onViewChange(v.id)}
                aria-current={active ? 'page' : undefined}
                className={`relative cursor-pointer px-3.5 font-sans transition-colors ${
                  active
                    ? 'bg-brand-wash font-medium text-ink'
                    : 'text-ink-dim hover:text-ink'
                }`}
              >
                {v.label}
                {active && (
                  <span
                    className="absolute inset-x-0 bottom-0 h-0.5 bg-brand"
                    aria-hidden
                  />
                )}
              </button>
            )
          })}
        </nav>
      </div>
    </header>
  )
}
