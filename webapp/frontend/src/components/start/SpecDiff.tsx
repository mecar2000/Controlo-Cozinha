/**
 * SpecDiff — what was asked against what the firmware agreed to run.
 *
 * This is the whole reason the two-phase start exists. A clamped hold or a
 * raised threshold has to be visible BEFORE confirming, not afterwards in
 * the record. So every changed value is called out against what was
 * requested, and unchanged values are quiet.
 *
 * The quorum threshold shows both the requested % and the counts the
 * firmware interpreted it as. The counts/% mapping is firmware-owned because
 * this condition cuts gas — the website is not the authority on what "4%"
 * means, and a miscalibration should be visible from the browser rather than
 * silent.
 */

import type { RunSpec, StopCondition } from '@/api/types'
import { formatDuration } from '@/lib/format'

interface Line {
  label: string
  requested: string
  acked: string
  changed: boolean
}

function stopConditionLines(
  phase: string,
  requested: StopCondition | undefined,
  acked: StopCondition | undefined,
): Line[] {
  const lines: Line[] = []

  const rDur = requested?.maxDurationMs ?? 0
  const aDur = acked?.maxDurationMs ?? 0
  if (rDur || aDur) {
    lines.push({
      label: `${phase} · duration`,
      requested: formatDuration(rDur),
      acked: formatDuration(aDur),
      changed: rDur !== aDur,
    })
  }

  const rInv = requested?.maxInventory_mL ?? 0
  const aInv = acked?.maxInventory_mL ?? 0
  if (rInv || aInv) {
    lines.push({
      label: `${phase} · inventory cap`,
      requested: `${rInv} mL`,
      acked: `${aInv} mL`,
      changed: rInv !== aInv,
    })
  }

  const rq = requested?.sensorQuorum
  const aq = acked?.sensorQuorum
  if ((rq?.quorumCount ?? 0) > 0 || (aq?.quorumCount ?? 0) > 0) {
    lines.push({
      label: `${phase} · quorum`,
      requested: `${rq?.quorumCount ?? 0} sensors at ${rq?.thresholdPct ?? 0} %v/v`,
      acked: `${aq?.quorumCount ?? 0} sensors at ${aq?.thresholdPct ?? 0} %v/v`,
      changed:
        rq?.quorumCount !== aq?.quorumCount || rq?.thresholdPct !== aq?.thresholdPct,
    })
  }

  return lines
}

function buildLines(requested: RunSpec, acked: RunSpec): Line[] {
  const lines: Line[] = [
    {
      label: 'gas setpoint',
      requested: `${requested.gasSetpointPct} %`,
      acked: `${acked.gasSetpointPct} %`,
      changed: requested.gasSetpointPct !== acked.gasSetpointPct,
    },
    ...stopConditionLines('leak', requested.leakStop, acked.leakStop),
    ...stopConditionLines('hold', requested.holdStop, acked.holdStop),
    {
      label: 'vent registers',
      requested: registerLabel(requested),
      acked: registerLabel(acked),
      changed: registerLabel(requested) !== registerLabel(acked),
    },
    {
      label: 'fan speed',
      requested: `${requested.fanSpeedPct} %`,
      acked: `${acked.fanSpeedPct} %`,
      changed: requested.fanSpeedPct !== acked.fanSpeedPct,
    },
    ...stopConditionLines('vent', requested.ventStop, acked.ventStop),
  ]
  return lines
}

function registerLabel(spec: RunSpec): string {
  const r = spec.ventRegisters
  if (!r) return '—'
  const open = [r.central && 'central', r.exhaust && 'exhaust', r.inlet && 'inlet'].filter(
    Boolean,
  )
  return open.length ? open.join(' + ') : 'none'
}

export function SpecDiff({
  requested,
  acked,
  interpretedQuorumCounts,
}: {
  requested: RunSpec
  acked: RunSpec
  interpretedQuorumCounts?: Record<string, number>
}) {
  const lines = buildLines(requested, acked)
  const changes = lines.filter((l) => l.changed)

  return (
    <div>
      {changes.length > 0 ? (
        <p className="prose-text mb-3 border-l-2 border-armed pl-2 text-armed">
          The firmware changed {changes.length}{' '}
          {changes.length === 1 ? 'value' : 'values'}. Check them before
          confirming.
        </p>
      ) : (
        <p className="prose-text mb-3 text-ink-dim">
          The firmware accepted the spec exactly as requested.
        </p>
      )}

      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-hairline text-ink-dim">
            <th className="py-1.5 text-left font-normal">value</th>
            <th className="py-1.5 text-right font-normal">requested</th>
            <th className="py-1.5 text-right font-normal">will run</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr
              key={line.label}
              className={`border-b border-hairline/60 ${line.changed ? 'bg-armed/10' : ''}`}
            >
              <td className="py-1.5 text-ink-dim">{line.label}</td>
              <td
                className={`py-1.5 text-right tabular-nums ${
                  line.changed ? 'text-ink-faint line-through' : 'text-ink'
                }`}
              >
                {line.requested}
              </td>
              <td
                className={`py-1.5 text-right tabular-nums ${
                  line.changed ? 'font-medium text-armed' : 'text-ink'
                }`}
              >
                {line.acked}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {interpretedQuorumCounts && Object.keys(interpretedQuorumCounts).length > 0 && (
        <div className="mt-4 border-t border-hairline pt-3">
          <p className="prose-text mb-2 text-ink-dim">
            The firmware owns the percent-to-counts mapping, because this
            condition cuts gas. These are the raw counts it will actually
            compare against.
          </p>
          {Object.entries(interpretedQuorumCounts).map(([phase, counts]) => (
            <div key={phase} className="rail-row border-b border-hairline last:border-b-0">
              <span className="rail-label">{phase} threshold</span>
              <span className="rail-value">{counts} counts</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
