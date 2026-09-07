/**
 * StartSheet — the two-step start, mirroring the firmware's own protocol.
 *
 *   1. Compose      pick a saved config, name the run, choose the experiment
 *   2. Review ack   see what the firmware actually agreed to run, then confirm
 *
 * Confirm is impossible until an ack arrives. A rejection shows the
 * firmware's own reason and offers only "back" — there is nothing to confirm
 * when the spec was refused.
 *
 * Recording is coupled to the run by default and decoupled deliberately: the
 * unrecorded-test-run checkbox is off unless someone turns it on, so nobody
 * has to invent an experiment name just to check a sensor is alive.
 */

import { useEffect, useState } from 'react'

import * as api from '@/api/client'
import type { DaqExperiment, RunConfig, StartRunResponse } from '@/api/types'
import { SpecDiff } from './SpecDiff'

type Step = 'compose' | 'review'

export function StartSheet({
  onClose,
  onStarted,
  daqReachable,
}: {
  onClose: () => void
  onStarted: () => void
  daqReachable: boolean | null
}) {
  const [step, setStep] = useState<Step>('compose')
  const [configs, setConfigs] = useState<RunConfig[]>([])
  const [experiments, setExperiments] = useState<DaqExperiment[]>([])

  const [configId, setConfigId] = useState<number | null>(null)
  const [runName, setRunName] = useState('')
  const [experimentId, setExperimentId] = useState<number | null>(null)
  const [newExperimentName, setNewExperimentName] = useState('')
  const [unrecorded, setUnrecorded] = useState(false)
  const [operator, setOperator] = useState('')

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<StartRunResponse | null>(null)

  useEffect(() => {
    void api
      .listConfigs()
      .then((c) => {
        setConfigs(c)
        if (c.length > 0) setConfigId(c[0]!.id)
      })
      .catch((err: Error) => setError(err.message))

    // The experiment picker is only reachable when the historian is up, and
    // a failure here is not fatal — an unrecorded test run needs no
    // experiment at all.
    void api
      .listExperiments()
      .then(setExperiments)
      .catch(() => setExperiments([]))
  }, [])

  const needsExperiment = !unrecorded
  const hasExperiment = experimentId != null || newExperimentName.trim().length > 0
  const canSend =
    configId != null && runName.trim().length > 0 && (!needsExperiment || hasExperiment)

  async function handleSend() {
    setBusy(true)
    setError(null)
    try {
      const resp = await api.startRun({
        config_id: configId,
        run_name: runName.trim(),
        experiment_id: experimentId,
        experiment_name: newExperimentName.trim() || undefined,
        unrecorded_test_run: unrecorded,
        operator: operator.trim() || undefined,
      })
      setResult(resp)
      setStep('review')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the run')
    } finally {
      setBusy(false)
    }
  }

  async function handleConfirm() {
    if (!result?.run || !result.ack?.runId) return
    setBusy(true)
    setError(null)
    try {
      await api.confirmRun(result.run.id, result.ack.runId)
      onStarted()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not confirm the run')
    } finally {
      setBusy(false)
    }
  }

  async function handleBack() {
    // Backing out after an ack releases the pending run rather than leaving
    // the firmware armed until its own 60s timeout.
    if (result?.run) {
      try {
        await api.cancelRun(result.run.id)
      } catch {
        /* the firmware disarms itself after ARM_TIMEOUT_MS regardless */
      }
    }
    setResult(null)
    setStep('compose')
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Start a run"
    >
      <div className="flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-sm border border-hairline bg-panel">
        <div className="flex items-baseline justify-between border-b border-hairline px-5 py-3">
          <h2 className="font-medium text-ink">
            {step === 'compose' ? 'Start a run' : 'Review what will run'}
          </h2>
          <span className="text-ink-faint">step {step === 'compose' ? 1 : 2} of 2</span>
        </div>

        <div className="overflow-y-auto px-5 py-4">
          {step === 'compose' ? (
            <ComposeStep
              configs={configs}
              configId={configId}
              onConfigId={setConfigId}
              runName={runName}
              onRunName={setRunName}
              experiments={experiments}
              experimentId={experimentId}
              onExperimentId={setExperimentId}
              newExperimentName={newExperimentName}
              onNewExperimentName={setNewExperimentName}
              unrecorded={unrecorded}
              onUnrecorded={setUnrecorded}
              operator={operator}
              onOperator={setOperator}
              daqReachable={daqReachable}
            />
          ) : (
            <ReviewStep result={result} />
          )}

          {error && (
            <p className="prose-text mt-4 border-l-2 border-live pl-2 text-live" role="alert">
              {error}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-hairline px-5 py-3">
          {step === 'compose' ? (
            <>
              <button type="button" className="btn btn-quiet" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-neutral"
                onClick={handleSend}
                disabled={!canSend || busy}
              >
                {busy ? 'Sending…' : 'Send to firmware'}
              </button>
            </>
          ) : (
            <>
              <button type="button" className="btn btn-quiet" onClick={handleBack} disabled={busy}>
                Back
              </button>
              {/* Confirm exists only when there is a valid ack to confirm. */}
              {result && !result.rejected && result.ack && (
                <button
                  type="button"
                  onClick={handleConfirm}
                  disabled={busy}
                  className="btn border-live bg-live font-medium text-bg hover:brightness-110"
                >
                  {busy ? 'Confirming…' : 'Confirm — start gas'}
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function ComposeStep({
  configs,
  configId,
  onConfigId,
  runName,
  onRunName,
  experiments,
  experimentId,
  onExperimentId,
  newExperimentName,
  onNewExperimentName,
  unrecorded,
  onUnrecorded,
  operator,
  onOperator,
  daqReachable,
}: {
  configs: RunConfig[]
  configId: number | null
  onConfigId: (v: number | null) => void
  runName: string
  onRunName: (v: string) => void
  experiments: DaqExperiment[]
  experimentId: number | null
  onExperimentId: (v: number | null) => void
  newExperimentName: string
  onNewExperimentName: (v: string) => void
  unrecorded: boolean
  onUnrecorded: (v: boolean) => void
  operator: string
  onOperator: (v: string) => void
  daqReachable: boolean | null
}) {
  return (
    <div className="flex flex-col gap-4">
      {daqReachable === false && !unrecorded && (
        <p className="prose-text border-l-2 border-armed pl-2 text-armed">
          The historian is unreachable, so starting a recorded run is blocked.
          Stop and acknowledge still work.
        </p>
      )}

      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">Config</span>
        {configs.length === 0 ? (
          <span className="prose-text text-ink-faint">
            No saved configs yet. Create one before starting a run.
          </span>
        ) : (
          <select
            className="field-input"
            value={configId ?? ''}
            onChange={(e) => onConfigId(e.target.value ? Number(e.target.value) : null)}
          >
            {configs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">Run name</span>
        <input
          className="field-input"
          value={runName}
          onChange={(e) => onRunName(e.target.value)}
          placeholder="what this run is testing"
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">Operator</span>
        <input
          className="field-input"
          value={operator}
          onChange={(e) => onOperator(e.target.value)}
          placeholder="who is running it"
        />
      </label>

      <fieldset className="flex flex-col gap-2 border-t border-hairline pt-3">
        <legend className="sr-only">Recording</legend>

        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            checked={unrecorded}
            onChange={(e) => onUnrecorded(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            <span className="text-ink">Unrecorded test run</span>
            <span className="prose-text mt-0.5 block text-ink-faint">
              For sensor checks. Nothing is stored in the experiment record.
            </span>
          </span>
        </label>

        {unrecorded && (
          <p className="prose-text border-l-2 border-armed pl-2 text-armed">
            This run will not be recorded. Nothing about it reaches the
            historian, so it cannot be replayed or compared later.
          </p>
        )}

        {!unrecorded && (
          <div className="flex flex-col gap-2 pl-6">
            <label className="flex flex-col gap-1">
              <span className="text-ink-dim">Existing experiment</span>
              <select
                className="field-input"
                value={experimentId ?? ''}
                onChange={(e) => {
                  onExperimentId(e.target.value ? Number(e.target.value) : null)
                  if (e.target.value) onNewExperimentName('')
                }}
              >
                <option value="">— choose one —</option>
                {experiments.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1">
              <span className="text-ink-dim">Or create a new one</span>
              <input
                className="field-input"
                value={newExperimentName}
                onChange={(e) => {
                  onNewExperimentName(e.target.value)
                  if (e.target.value) onExperimentId(null)
                }}
                placeholder="new experiment name"
              />
            </label>
          </div>
        )}
      </fieldset>
    </div>
  )
}

function ReviewStep({ result }: { result: StartRunResponse | null }) {
  if (!result) return null

  const { run, ack, rejected } = result

  if (rejected || !ack) {
    const reason =
      ack?.reason ??
      ack?.rejectReason ??
      run.outcome_detail ??
      'The firmware did not answer in time.'
    return (
      <div>
        <p className="text-lede mb-2 font-bold text-live">Rejected</p>
        <p className="prose-text text-ink">{reason}</p>
        <p className="prose-text mt-3 text-ink-faint">
          Nothing was started. Go back and adjust the config, or check the
          role selector is set to leak-test.
        </p>
      </div>
    )
  }

  const ackedSpec = ack.spec ?? run.acked_spec

  return (
    <div>
      {ackedSpec ? (
        <SpecDiff
          requested={run.requested_spec}
          acked={ackedSpec}
          interpretedQuorumCounts={ack.interpretedQuorumCounts}
        />
      ) : (
        <p className="prose-text text-ink-dim">
          The firmware accepted the run but sent back no spec to compare
          against.
        </p>
      )}

      <p className="prose-text mt-4 border-l-2 border-live pl-2 text-ink">
        Confirming starts the gas. The firmware disarms itself if you wait
        more than 60 seconds.
      </p>
    </div>
  )
}
