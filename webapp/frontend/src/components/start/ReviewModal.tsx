/**
 * ReviewModal — what the firmware actually agreed to run, then confirm.
 *
 * The one piece of the old two-step StartSheet that stays a modal
 * deliberately (see RunComposer's module docstring): a clamped spec must be
 * SEEN before gas flows, not skimmed inline in the rail alongside everything
 * else.
 */

import { useState } from 'react'

import * as api from '@/api/client'
import type { StartRunResponse } from '@/api/types'
import { SpecDiff } from './SpecDiff'

export function ReviewModal({
  result,
  onClose,
  onConfirmed,
}: {
  result: StartRunResponse
  onClose: () => void
  onConfirmed: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { run, ack, rejected } = result

  async function handleConfirm() {
    if (!ack?.runId) return
    setBusy(true)
    setError(null)
    try {
      await api.confirmRun(run.id, ack.runId)
      onConfirmed()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not confirm the run')
    } finally {
      setBusy(false)
    }
  }

  const ackedSpec = ack?.spec ?? run.acked_spec

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Review what will run"
    >
      <div className="flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-sm border border-hairline bg-panel">
        <div className="flex items-baseline justify-between border-b border-hairline px-5 py-3">
          <h2 className="font-medium text-ink">Review what will run</h2>
          <span className="text-ink-faint">run {run.run_number}</span>
        </div>

        <div className="overflow-y-auto px-5 py-4">
          {rejected || !ack ? (
            <div>
              <p className="text-lede mb-2 font-bold text-live">Rejected</p>
              <p className="prose-text text-ink">
                {ack?.reason ??
                  ack?.rejectReason ??
                  run.outcome_detail ??
                  'The firmware did not answer in time.'}
              </p>
              <p className="prose-text mt-3 text-ink-faint">
                Nothing was started. Go back and adjust the config, or check
                the role selector is set to leak-test.
              </p>
            </div>
          ) : (
            <div>
              {ackedSpec ? (
                <SpecDiff
                  requested={run.requested_spec}
                  acked={ackedSpec}
                  interpretedQuorumCounts={ack.interpretedQuorumCounts}
                />
              ) : (
                <p className="prose-text text-ink-dim">
                  The firmware accepted the run but sent back no spec to
                  compare against.
                </p>
              )}
              <p className="prose-text mt-4 border-l-2 border-live pl-2 text-ink">
                Confirming starts the gas. The firmware disarms itself if you
                wait more than 60 seconds.
              </p>
            </div>
          )}

          {error && (
            <p className="prose-text mt-4 border-l-2 border-live pl-2 text-live" role="alert">
              {error}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-hairline px-5 py-3">
          <button type="button" className="btn btn-quiet" onClick={onClose} disabled={busy}>
            {rejected || !ack ? 'Back' : 'Cancel leak test'}
          </button>
          {!rejected && ack && (
            <button
              type="button"
              onClick={() => void handleConfirm()}
              disabled={busy}
              className="btn cursor-pointer border-live bg-live font-medium text-bg hover:brightness-110"
            >
              {busy ? 'Confirming…' : 'Confirm — start gas'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
