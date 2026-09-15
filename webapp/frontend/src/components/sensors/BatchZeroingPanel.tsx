import { useEffect, useState } from 'react'

import * as api from '@/api/client'
import type { BatchZeroingStatus } from '@/api/types'

/** "Zero all in clean air" — the 30-sensor answer to ZeroingControl's
 *  one-sensor-at-a-time flow: one click zeros every sensor with a DAQ
 *  identity, one after another. Unlike the per-sensor flow there is no
 *  separate Apply step — the backend auto-applies each sensor's capture and
 *  advances on its own (app.zeroing.status_all) — so this only ever polls
 *  and shows progress. */
export function BatchZeroingPanel({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<BatchZeroingStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!status || status.done) return
    const id = setInterval(() => {
      void api
        .getZeroingStatusAll()
        .then(setStatus)
        .catch(() => setStatus(null))
    }, 500)
    return () => clearInterval(id)
  }, [status])

  async function handleStart() {
    setBusy(true)
    setError(null)
    try {
      const s = await api.startZeroingAll()
      setStatus(s)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the batch')
    } finally {
      setBusy(false)
    }
  }

  async function handleCancel() {
    setBusy(true)
    setError(null)
    try {
      await api.cancelZeroingAll()
    } finally {
      setStatus(null)
      setBusy(false)
    }
  }

  return (
    <fieldset className="flex max-w-md flex-col gap-3">
      <legend className="prose-text mb-1 text-ink-dim">
        Zero all in clean air — captures and applies every sensor with a DAQ identity,
        one after another. Starting a run is blocked until this finishes or is
        cancelled, same as zeroing a single sensor.
      </legend>

      {!status ? (
        <div className="flex items-center gap-3">
          <button type="button" className="btn btn-neutral" onClick={handleStart} disabled={busy}>
            {busy ? 'Starting…' : 'Start'}
          </button>
          <button type="button" className="btn btn-quiet" onClick={onClose} disabled={busy}>
            Close
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="rail-row">
            <span className="rail-label">{status.done ? 'done' : 'zeroing'}</span>
            <span className="rail-value">
              {status.index} / {status.total}
            </span>
          </div>
          <div
            role="progressbar"
            aria-valuenow={status.index}
            aria-valuemin={0}
            aria-valuemax={status.total}
            className="h-1.5 w-full overflow-hidden rounded-full bg-raised"
          >
            <div
              className="h-full bg-safe transition-[width] duration-500"
              style={{ width: `${status.total ? Math.min(100, (status.index / status.total) * 100) : 100}%` }}
            />
          </div>

          {status.current && (
            <p className="prose-text text-ink-dim">
              Capturing {status.current.sensor_key} — {status.current.collected} /{' '}
              {status.current.target}
            </p>
          )}

          {status.results.length > 0 && (
            <ul className="prose-text flex flex-col gap-0.5 text-ink-faint">
              {status.results.map((r) => (
                <li key={r.sensor_key}>
                  {r.sensor_key}: {r.previous_raw_min.toFixed(3)} → {r.new_raw_min.toFixed(3)}
                </li>
              ))}
            </ul>
          )}

          {status.failures.length > 0 && (
            <ul className="prose-text flex flex-col gap-0.5 text-armed">
              {status.failures.map((f) => (
                <li key={f.sensor_key}>
                  {f.sensor_key}: {f.error}
                </li>
              ))}
            </ul>
          )}

          <div className="flex justify-end gap-2">
            {!status.done && (
              <button type="button" className="btn btn-quiet" onClick={handleCancel} disabled={busy}>
                Cancel
              </button>
            )}
            {status.done && (
              <button type="button" className="btn btn-neutral" onClick={onClose}>
                Done
              </button>
            )}
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
