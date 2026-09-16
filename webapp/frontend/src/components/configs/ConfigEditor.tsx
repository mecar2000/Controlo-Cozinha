/**
 * ConfigEditor — create and edit RunConfigs: gas setpoint, fan speed, vent
 * registers, and the three stop conditions (leak/hold/vent).
 *
 * This is the form RunComposer's config picker has always assumed exists.
 * Until now a RunConfig could only be authored by calling POST /api/configs
 * directly — every field here mirrors kitchen/RunSpec.h exactly (see
 * api/types.ts's RunSpec), because that struct is the closed set of
 * primitives the website may compose, nothing else.
 *
 * This form does NOT validate against the firmware's own ceilings (e.g.
 * VENT_SPEED_MAX_PCT, HOLD_MAX_DURATION_MS) — the firmware is the one
 * validator/clamp authority by design, and what it actually accepted is
 * what ReviewModal's SpecDiff shows after Start. A saved config here is a
 * REQUEST, same as typing it by hand ever was.
 */

import { useEffect, useState } from 'react'

import * as api from '@/api/client'
import type { RegisterSet, RunConfig, RunSpec, StopCondition } from '@/api/types'

const emptyQuorum = { thresholdPct: 0, quorumCount: 0 }

function emptySpec(): RunSpec {
  return {
    gasSetpointPct: 0,
    leakStop: { maxDurationMs: 0, maxInventory_mL: 0, sensorQuorum: { ...emptyQuorum } },
    leakRegisters: { central: false, exhaust: false, inlet: false },
    leakFanSpeedPct: 0,
    holdStop: { maxDurationMs: 0 },
    ventRegisters: { central: false, exhaust: false, inlet: false },
    fanSpeedPct: 0,
    ventStop: { maxDurationMs: 0 },
  }
}

export function ConfigEditor({
  onClose,
  onConfigsChanged,
}: {
  onClose: () => void
  /** Called after every save/archive, so RunComposer's own config list (fetched
   *  once on its own mount) picks up the change immediately instead of only
   *  after a page reload. */
  onConfigsChanged: () => void
}) {
  const [configs, setConfigs] = useState<RunConfig[] | null>(null)
  const [selectedId, setSelectedId] = useState<number | 'new' | null>(null)
  const [error, setError] = useState<string | null>(null)

  function reload() {
    void api
      .listConfigs()
      .then(setConfigs)
      .catch((err: Error) => setError(err.message))
  }

  useEffect(reload, [])

  const editing =
    selectedId === 'new'
      ? { id: null, name: '', spec: emptySpec() }
      : (configs ?? []).find((c) => c.id === selectedId) ?? null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Run configs"
    >
      <div className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-sm border border-hairline bg-panel">
        <div className="flex items-baseline justify-between border-b border-hairline px-5 py-3">
          <h2 className="font-medium text-ink">Run configs</h2>
          <button type="button" className="btn btn-quiet" onClick={onClose}>
            Close
          </button>
        </div>

        {error && (
          <p className="prose-text border-b border-hairline px-5 py-2 text-live" role="alert">
            {error}
          </p>
        )}

        <div className="flex min-h-0 flex-1">
          <nav className="w-56 shrink-0 overflow-y-auto border-r border-hairline" aria-label="Config list">
            <button
              type="button"
              onClick={() => setSelectedId('new')}
              className={`flex w-full cursor-pointer items-center gap-2 border-b border-hairline/60 px-3 py-2 text-left ${
                selectedId === 'new' ? 'bg-raised text-ink' : 'text-ink-dim hover:text-ink'
              }`}
            >
              + new config
            </button>
            {!configs ? (
              <p className="prose-text px-3 py-2 text-ink-dim">Loading…</p>
            ) : configs.length === 0 ? (
              <p className="prose-text px-3 py-2 text-ink-dim">No saved configs yet.</p>
            ) : (
              configs.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => setSelectedId(c.id)}
                  className={`flex w-full cursor-pointer flex-col items-start gap-0.5 border-b border-hairline/60 px-3 py-2 text-left ${
                    selectedId === c.id ? 'bg-raised' : 'hover:bg-raised/50'
                  }`}
                >
                  <span className={selectedId === c.id ? 'font-medium text-ink' : 'text-ink'}>
                    {c.name}
                  </span>
                  <span className="prose-text text-ink-faint">
                    gas {c.spec.gasSetpointPct}% · fan {c.spec.fanSpeedPct}%
                  </span>
                </button>
              ))
            )}
          </nav>

          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            {editing ? (
              <ConfigForm
                key={editing.id ?? 'new'}
                initial={editing}
                onSaved={(saved) => {
                  reload()
                  setSelectedId(saved.id)
                  onConfigsChanged()
                }}
                onArchived={() => {
                  reload()
                  setSelectedId(null)
                  onConfigsChanged()
                }}
              />
            ) : (
              <p className="prose-text text-ink-dim">Choose a config, or start a new one.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function NumberField({
  label,
  suffix,
  value,
  onChange,
  min = 0,
}: {
  label: string
  suffix?: string
  value: number
  onChange: (v: number) => void
  min?: number
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-ink-dim">
        {label}
        {suffix ? ` (${suffix})` : ''}
      </span>
      <input
        className="field-input"
        type="number"
        min={min}
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  )
}

function RegisterCheckboxes({
  value,
  onChange,
  label = 'vent registers',
}: {
  value: RegisterSet
  onChange: (v: RegisterSet) => void
  label?: string
}) {
  const items: Array<[keyof RegisterSet, string]> = [
    ['central', 'central'],
    ['exhaust', 'exhaust'],
    ['inlet', 'inlet'],
  ]
  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className="text-ink-dim">{label}</legend>
      <div className="flex gap-4">
        {items.map(([key, name]) => (
          <label key={key} className="flex cursor-pointer items-center gap-1.5">
            <input
              type="checkbox"
              checked={value[key]}
              onChange={(e) => onChange({ ...value, [key]: e.target.checked })}
              className="cursor-pointer"
            />
            <span className="text-ink">{name}</span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}

/** One stop condition — duration and, for LEAKING only, inventory cap and
 *  sensor quorum. Duration is entered in seconds; the wire unit is ms
 *  (kitchen/RunSpec.h), converted at the edges so nobody has to type
 *  "300000". */
function StopConditionFields({
  phase,
  value,
  onChange,
  allowInventory = false,
  allowQuorum = false,
}: {
  phase: string
  value: StopCondition
  onChange: (v: StopCondition) => void
  allowInventory?: boolean
  allowQuorum?: boolean
}) {
  return (
    <fieldset className="flex flex-col gap-3 border-t border-hairline pt-4">
      <legend className="prose-text mb-1 text-ink-dim">{phase} stop condition</legend>

      <NumberField
        label="max duration"
        suffix="s, 0 = no cap"
        value={Math.round(value.maxDurationMs / 1000)}
        onChange={(v) => onChange({ ...value, maxDurationMs: v * 1000 })}
      />

      {allowInventory && (
        <NumberField
          label="max inventory"
          suffix="mL, 0 = no cap"
          value={value.maxInventory_mL ?? 0}
          onChange={(v) => onChange({ ...value, maxInventory_mL: v })}
        />
      )}

      {allowQuorum && (
        <div className="grid grid-cols-2 gap-3">
          <NumberField
            label="quorum threshold"
            suffix="%v/v"
            value={value.sensorQuorum?.thresholdPct ?? 0}
            onChange={(v) =>
              onChange({
                ...value,
                sensorQuorum: { thresholdPct: v, quorumCount: value.sensorQuorum?.quorumCount ?? 0 },
              })
            }
          />
          <NumberField
            label="quorum count"
            suffix="sensors, 0 = unused"
            value={value.sensorQuorum?.quorumCount ?? 0}
            onChange={(v) =>
              onChange({
                ...value,
                sensorQuorum: { thresholdPct: value.sensorQuorum?.thresholdPct ?? 0, quorumCount: v },
              })
            }
          />
        </div>
      )}
    </fieldset>
  )
}

function ConfigForm({
  initial,
  onSaved,
  onArchived,
}: {
  initial: { id: number | null; name: string; spec: RunSpec }
  onSaved: (config: RunConfig) => void
  onArchived: () => void
}) {
  const [name, setName] = useState(initial.name)
  const [spec, setSpec] = useState<RunSpec>(initial.spec)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  async function handleSave() {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      if (!name.trim()) throw new Error('name is required')
      const result =
        initial.id == null
          ? await api.createConfig(name.trim(), spec)
          : await api.updateConfig(initial.id, name.trim(), spec)
      onSaved(result)
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the config')
    } finally {
      setBusy(false)
    }
  }

  async function handleArchive() {
    if (initial.id == null) return
    setBusy(true)
    setError(null)
    try {
      await api.archiveConfig(initial.id)
      onArchived()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not archive the config')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">name</span>
        <input
          className="field-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="what this config is for"
        />
      </label>

      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label="gas setpoint"
          suffix="%"
          value={spec.gasSetpointPct}
          onChange={(v) => setSpec((s) => ({ ...s, gasSetpointPct: v }))}
        />
        <NumberField
          label="fan speed"
          suffix="%"
          value={spec.fanSpeedPct}
          onChange={(v) => setSpec((s) => ({ ...s, fanSpeedPct: v }))}
        />
      </div>

      <RegisterCheckboxes
        value={spec.ventRegisters}
        onChange={(v) => setSpec((s) => ({ ...s, ventRegisters: v }))}
      />

      <StopConditionFields
        phase="leak"
        value={spec.leakStop}
        onChange={(v) => setSpec((s) => ({ ...s, leakStop: v }))}
        allowInventory
        allowQuorum
      />

      <p className="prose-text -mt-2 text-ink-faint">
        Ventilation during the leak phase. Omitted/sealed (all closed, fan 0%)
        is the pre-existing behaviour.
      </p>
      <NumberField
        label="leak-phase fan speed"
        suffix="%"
        value={spec.leakFanSpeedPct ?? 0}
        onChange={(v) => setSpec((s) => ({ ...s, leakFanSpeedPct: v }))}
      />
      <RegisterCheckboxes
        label="leak-phase vent registers"
        value={spec.leakRegisters ?? { central: false, exhaust: false, inlet: false }}
        onChange={(v) => setSpec((s) => ({ ...s, leakRegisters: v }))}
      />
      <StopConditionFields
        phase="hold"
        value={spec.holdStop}
        onChange={(v) => setSpec((s) => ({ ...s, holdStop: v }))}
      />
      <p className="prose-text -mt-2 text-ink-faint">
        0 s on hold falls back to the firmware's own maximum safe hold, not to
        skipping the measurement.
      </p>
      <StopConditionFields
        phase="vent"
        value={spec.ventStop}
        onChange={(v) => setSpec((s) => ({ ...s, ventStop: v }))}
      />

      {error && (
        <p className="prose-text border-l-2 border-live pl-2 text-live" role="alert">
          {error}
        </p>
      )}
      {saved && !error && <p className="prose-text text-safe">Saved.</p>}

      <div className="flex justify-between border-t border-hairline pt-4">
        {initial.id != null ? (
          <button type="button" className="btn btn-quiet" onClick={handleArchive} disabled={busy}>
            Archive
          </button>
        ) : (
          <span />
        )}
        <button type="button" className="btn btn-neutral" onClick={handleSave} disabled={busy}>
          {busy ? 'Saving…' : 'Save config'}
        </button>
      </div>
    </div>
  )
}
