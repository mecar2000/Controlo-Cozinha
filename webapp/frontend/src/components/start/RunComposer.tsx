/**
 * RunComposer — the config, inline in the rail (problems.txt: "To define
 * the config should be direct i shouldnt have to press a button. There
 * should be the button but if its waiting it should allow me to also fill
 * the config or load from memory directly on the main page").
 *
 * Replaces the numeric rail while WAITING with nothing running — there is
 * nothing running yet for NumericRail to usefully show. Submitting sends
 * start(spec) and hands the result to a review-only modal (ReviewModal,
 * below) for the requested-vs-acked diff and confirm — that stays a modal
 * deliberately: it's the one safety-relevant step, where a clamped spec
 * must be seen before gas flows, not skimmed inline.
 *
 * "load from memory" is the config picker itself: run_configs persists and
 * is only ever soft-archived (db/configs.py), so every saved config IS the
 * memory of previous ones — no separate "recent configs" list needed.
 */

import { useEffect, useState } from 'react'

import * as api from '@/api/client'
import { ApiError } from '@/api/client'
import type { DaqExperiment, RunConfig, StartRunResponse } from '@/api/types'
import { ReviewModal } from './ReviewModal'

export function RunComposer({
  daqReachable,
  onStarted,
}: {
  daqReachable: boolean | null
  onStarted: () => void
}) {
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
  const [nameConflict, setNameConflict] = useState(false)
  const [result, setResult] = useState<StartRunResponse | null>(null)

  const selectedConfig = configs.find((c) => c.id === configId) ?? null
  const inletOnly = selectedConfig
    ? Boolean(selectedConfig.spec.ventRegisters?.inlet) &&
      !selectedConfig.spec.ventRegisters?.central &&
      !selectedConfig.spec.ventRegisters?.exhaust
    : false

  useEffect(() => {
    void api
      .listConfigs()
      .then((c) => {
        setConfigs(c)
        if (c.length > 0) setConfigId((id) => id ?? c[0]!.id)
      })
      .catch((err: Error) => setError(err.message))

    // Failure here is not fatal — an unrecorded test run needs no experiment
    // at all, and this list is only reachable when the historian is up.
    void api
      .listExperiments()
      .then((x) => setExperiments(Array.isArray(x) ? x : []))
      .catch(() => setExperiments([]))
  }, [])

  const needsExperiment = !unrecorded
  const hasExperiment = experimentId != null || newExperimentName.trim().length > 0
  const canSend =
    configId != null &&
    runName.trim().length > 0 &&
    (!needsExperiment || hasExperiment) &&
    !inletOnly

  async function handleStart(onNameConflict: 'reject' | 'suffix' = 'reject') {
    setBusy(true)
    setError(null)
    setNameConflict(false)
    try {
      const resp = await api.startRun({
        config_id: configId,
        run_name: runName.trim(),
        experiment_id: experimentId,
        experiment_name: newExperimentName.trim() || undefined,
        unrecorded_test_run: unrecorded,
        operator: operator.trim() || undefined,
        on_name_conflict: onNameConflict,
      })
      setResult(resp)
    } catch (err) {
      if (err instanceof ApiError && /already exists/i.test(err.message)) {
        setNameConflict(true)
        setError(err.message)
      } else {
        setError(err instanceof Error ? err.message : 'Could not start the run')
      }
    } finally {
      setBusy(false)
    }
  }

  async function handleModalClose() {
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
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="rail-heading">Start a run</p>

      {daqReachable === false && !unrecorded && (
        <p className="prose-text border-l-2 border-armed pl-2 text-armed">
          The historian is unreachable, so starting a recorded run is
          blocked. Stop and acknowledge still work.
        </p>
      )}

      <label className="flex flex-col gap-1">
        <span className="field-label">Config</span>
        {configs.length === 0 ? (
          <span className="prose-text text-ink-faint">
            No saved configs yet — use the configs button above the room to
            create one.
          </span>
        ) : (
          <select
            className="field-input"
            value={configId ?? ''}
            onChange={(e) => setConfigId(e.target.value ? Number(e.target.value) : null)}
          >
            {configs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
      </label>

      {inletOnly && (
        <p className="prose-text border-l-2 border-armed pl-2 text-armed">
          Inlet is open with no central or exhaust register in this config —
          open at least one of them too, or turn the inlet off, before
          starting.
        </p>
      )}

      <label className="flex flex-col gap-1">
        <span className="field-label">Run name</span>
        <input
          className="field-input"
          value={runName}
          onChange={(e) => {
            setRunName(e.target.value)
            setNameConflict(false)
          }}
          placeholder="what this run is testing"
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="field-label">Operator</span>
        <input
          className="field-input"
          value={operator}
          onChange={(e) => setOperator(e.target.value)}
          placeholder="who is running it"
        />
      </label>

      <fieldset className="flex flex-col gap-2 border-t border-hairline pt-3">
        <legend className="sr-only">Recording</legend>

        <label className="flex cursor-pointer items-start gap-2">
          <input
            type="checkbox"
            checked={unrecorded}
            onChange={(e) => setUnrecorded(e.target.checked)}
            className="mt-0.5 cursor-pointer"
          />
          <span>
            <span className="font-sans">Unrecorded test run</span>
            <span className="prose-text mt-0.5 block text-ink-faint">
              For sensor checks. Nothing is stored in the experiment record.
            </span>
          </span>
        </label>

        {!unrecorded && (
          <div className="flex flex-col gap-2 pl-6">
            <label className="flex flex-col gap-1">
              <span className="field-label">Existing experiment</span>
              <select
                className="field-input"
                value={experimentId ?? ''}
                onChange={(e) => {
                  setExperimentId(e.target.value ? Number(e.target.value) : null)
                  if (e.target.value) setNewExperimentName('')
                }}
              >
                <option value="">Choose an experiment</option>
                {experiments.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1">
              <span className="field-label">Or create a new one</span>
              <input
                className="field-input"
                value={newExperimentName}
                onChange={(e) => {
                  setNewExperimentName(e.target.value)
                  if (e.target.value) setExperimentId(null)
                }}
                placeholder="new experiment name"
              />
            </label>
          </div>
        )}
      </fieldset>

      {error && (
        <div className="prose-text border-l-2 border-live pl-2 text-live" role="alert">
          <p>{error}</p>
          {nameConflict && (
            <button
              type="button"
              className="btn btn-quiet mt-1 px-0 underline"
              onClick={() => void handleStart('suffix')}
              disabled={busy}
            >
              Use "{runName.trim()}-2" instead
            </button>
          )}
        </div>
      )}

      <button
        type="button"
        className="btn btn-primary w-full py-2.5"
        onClick={() => void handleStart('reject')}
        disabled={!canSend || busy}
      >
        {busy ? 'Sending…' : 'Start'}
      </button>

      {result && <ReviewModal result={result} onClose={handleModalClose} onConfirmed={onStarted} />}
    </div>
  )
}
