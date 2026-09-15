import { useEffect, useState } from 'react'

import * as api from '@/api/client'
import type { DaqConversion, ZeroingStatus } from '@/api/types'

/** "Zero in clean air" — averages 100 live raw samples and writes the
 *  result as the calibration's raw_min. Polls the session's status every
 *  500ms while active; the backend itself pulls in new samples on each
 *  status check (see app.zeroing.collect()). */
export function ZeroingControl({
  sensorKey,
  onApplied,
}: {
  sensorKey: string
  onApplied: (conv: DaqConversion) => void
}) {
  const [session, setSession] = useState<ZeroingStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ previous: number; next: number } | null>(null)

  useEffect(() => {
    if (!session || session.done) return
    const id = setInterval(() => {
      void api
        .getZeroingStatus(sensorKey)
        .then(setSession)
        .catch(() => {
          /* a stale/cancelled session elsewhere — stop polling quietly */
          setSession(null)
        })
    }, 500)
    return () => clearInterval(id)
  }, [session, sensorKey])

  async function handleStart() {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const s = await api.startZeroing(sensorKey, 100)
      setSession(s)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the capture')
    } finally {
      setBusy(false)
    }
  }

  async function handleApply() {
    setBusy(true)
    setError(null)
    try {
      const r = await api.applyZeroing(sensorKey)
      setResult({ previous: r.previous_raw_min, next: r.new_raw_min })
      setSession(null)
      // Refresh the calibration form's displayed raw_min from the applied
      // result rather than re-fetching the whole conversion table.
      onApplied({ method: 'linear', params: { raw_min: r.new_raw_min }, unit_symbol: '', conv_id: null })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not apply — keep the air clean and try again')
    } finally {
      setBusy(false)
    }
  }

  async function handleCancel() {
    setBusy(true)
    setError(null)
    try {
      await api.cancelZeroing(sensorKey)
    } finally {
      setSession(null)
      setBusy(false)
    }
  }

  return (
    <fieldset className="flex flex-col gap-3 border-t border-hairline pt-4">
      <legend className="prose-text mb-1 text-ink-dim">
        Zero in clean air — averages 100 readings and uses them as the wiring-loss
        offset. Starting a run is blocked until this finishes or is cancelled.
      </legend>

      {!session ? (
        <div className="flex items-center gap-3">
          <button type="button" className="btn btn-neutral" onClick={handleStart} disabled={busy}>
            {busy ? 'Starting…' : 'Zero in clean air'}
          </button>
          {result && (
            <p className="prose-text text-safe">
              {result.previous.toFixed(3)} → {result.next.toFixed(3)}
            </p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="rail-row">
            <span className="rail-label">{session.done ? 'ready to apply' : 'collecting'}</span>
            <span className="rail-value">
              {session.collected} / {session.target}
            </span>
          </div>
          <div
            role="progressbar"
            aria-valuenow={session.collected}
            aria-valuemin={0}
            aria-valuemax={session.target}
            className="h-1.5 w-full overflow-hidden rounded-full bg-raised"
          >
            <div
              className="h-full bg-safe transition-[width] duration-500"
              style={{ width: `${Math.min(100, (session.collected / session.target) * 100)}%` }}
            />
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" className="btn btn-quiet" onClick={handleCancel} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="btn btn-neutral" onClick={handleApply} disabled={busy || !session.done}>
              {busy ? 'Applying…' : 'Apply'}
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="prose-text border-l-2 border-live pl-2 text-live" role="alert">
          {error}
        </p>
      )}
    </fieldset>
  )
}
