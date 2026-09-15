import { useState } from 'react'

import * as api from '@/api/client'
import type { ConfigAckEntry } from '@/api/types'

export function ThresholdForm({
  sensorKey,
  ackEntry,
}: {
  sensorKey: string
  ackEntry: ConfigAckEntry | undefined
}) {
  const [pct, setPct] = useState(ackEntry ? String(ackEntry.requestedPct) : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSend() {
    setBusy(true)
    setError(null)
    try {
      const value = Number(pct)
      if (Number.isNaN(value) || value < 0) throw new Error('threshold must be a number ≥ 0')
      await api.setThresholds([{ sensor_key: sensorKey, thresholdPct: value }])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send the threshold')
    } finally {
      setBusy(false)
    }
  }

  return (
    <fieldset className="flex flex-col gap-3 border-t border-hairline pt-4">
      <legend className="prose-text mb-1 text-ink-dim">
        Danger threshold — the firmware owns the safety ceiling; this can only ask
      </legend>

      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">threshold, %v/v</span>
        <input
          className="field-input"
          inputMode="decimal"
          value={pct}
          onChange={(e) => setPct(e.target.value)}
          placeholder="1.5"
        />
      </label>

      {error && (
        <p className="prose-text border-l-2 border-live pl-2 text-live" role="alert">
          {error}
        </p>
      )}

      {ackEntry && (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-hairline text-ink-dim">
              <th className="py-1.5 text-left font-normal">last accepted</th>
              <th className="py-1.5 text-right font-normal">requested</th>
              <th className="py-1.5 text-right font-normal">took effect</th>
            </tr>
          </thead>
          <tbody>
            <tr className={`border-b border-hairline/60 ${ackEntry.clamped ? 'bg-armed/10' : ''}`}>
              <td className="py-1.5 text-ink-dim">%v/v</td>
              <td
                className={`py-1.5 text-right tabular-nums ${
                  ackEntry.clamped ? 'text-ink-faint line-through' : 'text-ink'
                }`}
              >
                {ackEntry.requestedPct}
              </td>
              <td
                className={`py-1.5 text-right tabular-nums ${
                  ackEntry.clamped ? 'font-medium text-armed' : 'text-ink'
                }`}
              >
                {ackEntry.effectivePct}
              </td>
            </tr>
          </tbody>
        </table>
      )}
      {ackEntry?.clamped && (
        <p className="prose-text border-l-2 border-armed pl-2 text-armed">
          The firmware's safety ceiling refused this — it can only make a
          sensor MORE sensitive, never less. {ackEntry.effectivePct} %v/v is
          what actually took effect.
        </p>
      )}

      <div className="flex justify-end">
        <button type="button" className="btn btn-neutral" onClick={handleSend} disabled={busy || !pct}>
          {busy ? 'Sending…' : 'Send threshold'}
        </button>
      </div>
    </fieldset>
  )
}
