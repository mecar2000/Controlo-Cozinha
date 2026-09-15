import { useState } from 'react'

import * as api from '@/api/client'

/** Wires a sensor to one of the kitchen PLC's 6 hardwired H2 inputs so it can
 *  receive a danger threshold, with no requirement that it be named "H2-N"
 *  (problems.txt: previously the ONLY way to get a threshold at all).
 *
 *  Renamed from "firmware channel" (unexplained in the UI) to say plainly
 *  what it is and who needs it: the kitchen PLC's own H2 inputs are a fixed
 *  hardware fact (6 of them, numbered 0-5) — most CM7/DataAcquisition
 *  sensors never touch this at all, only ones physically wired into the
 *  kitchen PLC do. `usedChannels` (computed from every other active
 *  sensor's firmware_index / H2-N fallback — see lib/usedFirmwareChannels)
 *  warns before creating two sensors that would silently compete for one
 *  threshold; nothing on the backend refuses this outright, so the warning
 *  is the only thing that catches it. */
export function FirmwareIndexForm({
  sensorKey,
  usedChannels,
  onSet,
}: {
  sensorKey: string
  usedChannels: Set<number>
  onSet: () => void
}) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const parsed = value === '' ? null : Number(value)
  const wouldCollide = parsed != null && Number.isInteger(parsed) && usedChannels.has(parsed)

  async function handleSet() {
    setBusy(true)
    setError(null)
    try {
      const n = Number(value)
      if (!Number.isInteger(n) || n < 0 || n > 5) {
        throw new Error('the kitchen PLC input must be an integer 0-5')
      }
      await api.setFirmwareIndex(sensorKey, n)
      onSet()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not set the kitchen PLC input')
    } finally {
      setBusy(false)
    }
  }

  return (
    <fieldset className="flex flex-col gap-3 border-t border-hairline pt-4">
      <legend className="prose-text mb-1 text-ink-dim">
        Kitchen PLC input — this sensor has no danger threshold yet. The kitchen PLC
        has 6 hardwired H2 inputs (0-5); wiring a sensor to one here is what lets it
        receive a numeric danger threshold below. Most other sensors don't need this
        — only ones physically wired into the kitchen PLC.
      </legend>
      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">kitchen PLC input (0-5)</span>
        <input
          className="field-input max-w-24"
          inputMode="numeric"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="0"
        />
      </label>
      {wouldCollide && (
        <p className="prose-text border-l-2 border-armed pl-2 text-armed">
          Input {value} is already used by another sensor — two sensors on the same
          input would silently compete for one threshold.
        </p>
      )}
      {error && (
        <p className="prose-text border-l-2 border-live pl-2 text-live" role="alert">
          {error}
        </p>
      )}
      <div className="flex justify-end">
        <button type="button" className="btn btn-neutral" onClick={handleSet} disabled={busy || value === ''}>
          {busy ? 'Setting…' : 'Set input'}
        </button>
      </div>
    </fieldset>
  )
}
