/**
 * SensorPanel — define sensors, position them, calibrate them, set their
 * danger threshold, and archive/restore them. Previously this could only
 * edit calibration/threshold on sensors that already existed in the
 * database (problems.txt: "I have to be able to define new sensors as
 * well as their positions shouldn't be fixed").
 *
 * Two tabs — active / archived — because "removed" here means soft-deleted
 * (app.db.sensor_config.archive_sensor): a sensor stays in the database and
 * can be brought back, rather than vanishing (problems.txt: "There should
 * be memory of previous ones").
 *
 * Calibration still lives in DataAcquisition (this writes through to
 * api.setConversion, same as before) — only sensor IDENTITY/POSITION is
 * local. "Zero in clean air" (ZeroingControl below) answers problems.txt's
 * "cant i just see it from when there is no hydrogen what's the baseline"
 * by averaging the live raw reading and writing it as the calibration's
 * raw_min, so no separate measuring rig or calibration concept is needed.
 */

import { useEffect, useMemo, useState } from 'react'

import * as api from '@/api/client'
import type {
  ConfigAck,
  ConfigAckEntry,
  ConversionMethod,
  DaqConversion,
  LiveReadings,
  Sensor,
  ZeroingStatus,
} from '@/api/types'

const METHODS: ConversionMethod[] = ['raw', 'linear', 'ax_b']

const METHOD_HELP: Record<ConversionMethod, string> = {
  raw: 'Report the raw signal as-is, in its own unit.',
  linear: 'Map a raw span onto a value span — the usual 4-20 mA or 0-10 V calibration.',
  ax_b: 'value = a × raw + b',
}

/** The two real sensor types this app actually has (problems.txt: "sensors
 *  theere will be simply either voltage 0.5-4.5V meaning 0-4% vol or
 *  4-20ma meaning 0-100%"). Picking one fills in the whole linear
 *  calibration in one click; "Custom" drops to the free-form method picker
 *  underneath for anything else. */
type CalibrationPreset = 'voltage-0-4' | 'current-0-100' | 'custom'

const PRESETS: Array<{
  id: CalibrationPreset
  label: string
  params?: { raw_min: number; raw_max: number; min_value: number; max_value: number }
  unit?: string
}> = [
  {
    id: 'voltage-0-4',
    label: '0.5–4.5 V → 0–4 %vol',
    params: { raw_min: 0.5, raw_max: 4.5, min_value: 0, max_value: 4 },
    unit: '%v/v',
  },
  {
    id: 'current-0-100',
    label: '4–20 mA → 0–100 %',
    params: { raw_min: 4, raw_max: 20, min_value: 0, max_value: 100 },
    unit: '%v/v',
  },
  { id: 'custom', label: 'Custom…' },
]

function presetFor(conv: DaqConversion | undefined): CalibrationPreset {
  if (!conv || conv.method !== 'linear') return 'custom'
  const p = conv.params ?? {}
  const num = (v: unknown): number => (typeof v === 'number' ? v : Number(v ?? NaN))
  const matches = (preset: (typeof PRESETS)[number]) =>
    !!preset.params &&
    Math.abs(num(p.raw_min) - preset.params.raw_min) < 1e-6 &&
    Math.abs(num(p.raw_max) - preset.params.raw_max) < 1e-6 &&
    Math.abs(num(p.min_value) - preset.params.min_value) < 1e-6 &&
    Math.abs(num(p.max_value) - preset.params.max_value) < 1e-6
  return PRESETS.find((preset) => preset.id !== 'custom' && matches(preset))?.id ?? 'custom'
}

interface Row {
  sensor: Sensor
  /** sensor.firmware_index if set, else H2-N -> N-1 as a fallback (see
   *  routes/thresholds.py). Null when neither resolves — no threshold can
   *  be sent for it, only calibration. */
  firmwareIndex: number | null
}

function firmwareIndexOf(sensor: Sensor): number | null {
  if (sensor.firmware_index != null) return sensor.firmware_index
  const m = /^H2-(\d+)$/.exec(sensor.daq_sensor_name ?? '')
  if (!m) return null
  const idx = Number(m[1]) - 1
  return idx >= 0 && idx <= 5 ? idx : null
}

function readingKeyFor(sensor: Sensor): string {
  return sensor.daq_device_id
    ? `${sensor.daq_device_id}/${sensor.daq_sensor_name ?? sensor.sensor_key}`
    : (sensor.daq_sensor_name ?? sensor.sensor_key)
}

export function SensorPanel({
  onClose,
  liveReadings,
  configAck,
}: {
  onClose: () => void
  liveReadings: LiveReadings
  configAck: ConfigAck
}) {
  const [tab, setTab] = useState<'active' | 'archived'>('active')
  const [sensors, setSensors] = useState<Sensor[] | null>(null)
  const [conversions, setConversions] = useState<Record<string, DaqConversion>>({})
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function reload() {
    void api
      .listSensors(false, true) // fetch both; split into tabs client-side so switching tabs needs no refetch
      .then((list) => {
        setSensors(list)
        setSelectedKey((k) => {
          const stillVisible = list.some(
            (s) => s.sensor_key === k && s.archived === (tab === 'archived'),
          )
          if (stillVisible) return k
          const firstInTab = list.find((s) => s.archived === (tab === 'archived'))
          return firstInTab?.sensor_key ?? null
        })
      })
      .catch((err: Error) => setError(err.message))
  }

  useEffect(reload, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Calibration lives in DataAcquisition, per device. Sensors here can span
  // more than one device once a remote acquisition PLC is configured, so
  // fetch each device's table once rather than one HTTP call per sensor.
  useEffect(() => {
    if (!sensors) return
    const deviceIds = [...new Set(sensors.map((s) => s.daq_device_id).filter((d): d is string => Boolean(d)))]
    void Promise.all(deviceIds.map((id) => api.getConversions(id).then((c) => [id, c] as const)))
      .then((pairs) => {
        const merged: Record<string, DaqConversion> = {}
        for (const [deviceId, table] of pairs) {
          for (const [sensorName, conv] of Object.entries(table)) {
            merged[`${deviceId}/${sensorName}`] = conv
          }
        }
        setConversions(merged)
      })
      .catch(() => setConversions({}))
  }, [sensors])

  const visible = useMemo(
    () => (sensors ?? []).filter((s) => s.archived === (tab === 'archived')),
    [sensors, tab],
  )
  const rows = useMemo<Row[]>(
    () => visible.map((sensor) => ({ sensor, firmwareIndex: firmwareIndexOf(sensor) })),
    [visible],
  )
  const selected = rows.find((r) => r.sensor.sensor_key === selectedKey) ?? null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Sensors"
    >
      <div className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-sm border border-hairline bg-panel">
        <div className="flex items-baseline justify-between border-b border-hairline px-5 py-3">
          <h2 className="font-medium text-ink">Sensors</h2>
          <button type="button" className="btn btn-quiet" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="flex items-center justify-between border-b border-hairline px-5 py-2">
          <div className="flex gap-1" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'active'}
              onClick={() => {
                setTab('active')
                setSelectedKey(null)
              }}
              className={`cursor-pointer rounded-sm px-2.5 py-1 ${tab === 'active' ? 'bg-raised text-ink' : 'text-ink-dim hover:text-ink'}`}
            >
              Active
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'archived'}
              onClick={() => {
                setTab('archived')
                setSelectedKey(null)
              }}
              className={`cursor-pointer rounded-sm px-2.5 py-1 ${tab === 'archived' ? 'bg-raised text-ink' : 'text-ink-dim hover:text-ink'}`}
            >
              Archived
            </button>
          </div>
          {tab === 'active' && (
            <button type="button" className="btn btn-neutral" onClick={() => setAdding(true)}>
              + Add sensor
            </button>
          )}
        </div>

        {error && (
          <p className="prose-text border-b border-hairline px-5 py-2 text-live" role="alert">
            {error}
          </p>
        )}

        {adding ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <NewSensorForm
              onCancel={() => setAdding(false)}
              onCreated={(sensor) => {
                setAdding(false)
                reload()
                setSelectedKey(sensor.sensor_key)
              }}
            />
          </div>
        ) : !sensors ? (
          <p className="prose-text px-5 py-4 text-ink-dim">Loading…</p>
        ) : visible.length === 0 ? (
          <p className="prose-text px-5 py-4 text-ink-dim">
            {tab === 'active'
              ? 'No sensors configured yet. Add one to start seeing readings on the room view.'
              : 'No archived sensors.'}
          </p>
        ) : (
          <div className="flex min-h-0 flex-1">
            <nav className="w-56 shrink-0 overflow-y-auto border-r border-hairline" aria-label="Sensor list">
              {rows.map(({ sensor }) => {
                const reading = liveReadings[readingKeyFor(sensor)]
                const active = sensor.sensor_key === selectedKey
                return (
                  <button
                    key={sensor.sensor_key}
                    type="button"
                    onClick={() => setSelectedKey(sensor.sensor_key)}
                    className={`flex w-full cursor-pointer flex-col items-start gap-0.5 border-b border-hairline/60 px-3 py-2 text-left ${
                      active ? 'bg-raised' : 'hover:bg-raised/50'
                    }`}
                  >
                    <span className={active ? 'font-medium text-ink' : 'text-ink'}>{sensor.label}</span>
                    <span className="prose-text text-ink-faint">
                      {sensor.daq_sensor_name ?? 'no DAQ identity'}
                      {reading && reading.converted !== false
                        ? ` · ${reading.value.toFixed(2)} ${reading.unit}`
                        : reading
                          ? ' · unconverted'
                          : ''}
                    </span>
                  </button>
                )
              })}
            </nav>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              {selected ? (
                <SensorDetail
                  row={selected}
                  conversion={conversions[readingKeyFor(selected.sensor)]}
                  liveReading={liveReadings[readingKeyFor(selected.sensor)]}
                  configAck={configAck}
                  onConversionSaved={(conv) => {
                    setConversions((prev) => ({ ...prev, [readingKeyFor(selected.sensor)]: conv }))
                  }}
                  onArchived={() => {
                    reload()
                  }}
                  onRestored={() => {
                    reload()
                  }}
                  onMoved={() => {
                    reload()
                  }}
                />
              ) : (
                <p className="prose-text text-ink-dim">Choose a sensor.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function NewSensorForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void
  onCreated: (sensor: Sensor) => void
}) {
  const [sensorKey, setSensorKey] = useState('')
  const [label, setLabel] = useState('')
  const [x, setX] = useState('')
  const [y, setY] = useState('')
  const [z, setZ] = useState('')
  const [daqDeviceId, setDaqDeviceId] = useState('')
  const [daqSensorName, setDaqSensorName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canSave = sensorKey.trim() && label.trim() && x !== '' && y !== '' && z !== ''

  async function handleSave() {
    setBusy(true)
    setError(null)
    try {
      const nx = Number(x)
      const ny = Number(y)
      const nz = Number(z)
      if ([nx, ny, nz].some((n) => Number.isNaN(n))) {
        throw new Error('x, y, z must all be numbers')
      }
      const sensor = await api.upsertSensor(sensorKey.trim(), {
        label: label.trim(),
        x: nx,
        y: ny,
        z: nz,
        daq_device_id: daqDeviceId.trim() || null,
        daq_sensor_name: daqSensorName.trim() || null,
      })
      onCreated(sensor)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the sensor')
    } finally {
      setBusy(false)
    }
  }

  return (
    <fieldset className="flex max-w-md flex-col gap-3">
      <legend className="prose-text mb-1 text-ink-dim">
        New sensor — position entered by hand, off a tape measure
      </legend>

      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">sensor key</span>
        <input
          className="field-input"
          value={sensorKey}
          onChange={(e) => setSensorKey(e.target.value)}
          placeholder="sensor-7"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">label</span>
        <input
          className="field-input"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Above the stove"
        />
      </label>

      <div className="grid grid-cols-3 gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-ink-dim">x (m)</span>
          <input className="field-input" inputMode="decimal" value={x} onChange={(e) => setX(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-ink-dim">y (m)</span>
          <input className="field-input" inputMode="decimal" value={y} onChange={(e) => setY(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-ink-dim">z (m)</span>
          <input className="field-input" inputMode="decimal" value={z} onChange={(e) => setZ(e.target.value)} />
        </label>
      </div>

      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">DAQ device id (optional — set later if unknown)</span>
        <input
          className="field-input"
          value={daqDeviceId}
          onChange={(e) => setDaqDeviceId(e.target.value)}
          placeholder="mainBoard"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">DAQ sensor name</span>
        <input
          className="field-input"
          value={daqSensorName}
          onChange={(e) => setDaqSensorName(e.target.value)}
          placeholder="H2-1"
        />
      </label>

      {error && (
        <p className="prose-text border-l-2 border-live pl-2 text-live" role="alert">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <button type="button" className="btn btn-quiet" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="btn btn-neutral" onClick={handleSave} disabled={busy || !canSave}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </fieldset>
  )
}

function SensorDetail({
  row,
  conversion,
  liveReading,
  configAck,
  onConversionSaved,
  onArchived,
  onRestored,
  onMoved,
}: {
  row: Row
  conversion: DaqConversion | undefined
  liveReading: LiveReadings[string] | undefined
  configAck: ConfigAck
  onConversionSaved: (conv: DaqConversion) => void
  onArchived: () => void
  onRestored: () => void
  onMoved: () => void
}) {
  const { sensor, firmwareIndex } = row
  const ackEntry = configAck.thresholds?.find((t) => t.sensor === firmwareIndex)

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-lede font-medium text-ink">{sensor.label}</p>
          <p className="prose-text text-ink-faint">
            {sensor.daq_device_id && sensor.daq_sensor_name
              ? `${sensor.daq_device_id} / ${sensor.daq_sensor_name}`
              : 'No DAQ identity configured — this sensor cannot receive live readings.'}
          </p>
        </div>
        <ArchiveControl sensor={sensor} onArchived={onArchived} onRestored={onRestored} />
      </div>

      {liveReading && (
        <div className="rail-row border-b border-hairline">
          <span className="rail-label">live reading</span>
          <span className="rail-value">
            {liveReading.value.toFixed(3)} {liveReading.unit}
            {liveReading.converted === false && (
              <span className="prose-text ml-2 text-armed">unconverted</span>
            )}
          </span>
        </div>
      )}

      {!sensor.archived && <PositionForm sensor={sensor} onSaved={onMoved} />}

      {sensor.daq_device_id && sensor.daq_sensor_name ? (
        <>
          <CalibrationForm
            deviceId={sensor.daq_device_id}
            sensorName={sensor.daq_sensor_name}
            conversion={conversion}
            rawUnit={liveReading?.converted === false ? liveReading.unit : undefined}
            onSaved={onConversionSaved}
          />
          {!sensor.archived && (
            <ZeroingControl
              sensorKey={sensor.sensor_key}
              onApplied={(conv) => onConversionSaved(conv)}
            />
          )}
        </>
      ) : (
        <p className="prose-text border-l-2 border-armed pl-2 text-armed">
          Set a DAQ device and sensor name for this sensor before calibrating it.
        </p>
      )}

      {firmwareIndex != null ? (
        <ThresholdForm sensorKey={sensor.sensor_key} ackEntry={ackEntry} />
      ) : (
        <FirmwareIndexForm sensorKey={sensor.sensor_key} onSet={onMoved} />
      )}
    </div>
  )
}

/** Wires a sensor to a real firmware channel (0-5) so it can receive a
 *  danger threshold, with no requirement that it be named "H2-N" —
 *  previously the ONLY way to get a threshold at all (problems.txt). */
function FirmwareIndexForm({ sensorKey, onSet }: { sensorKey: string; onSet: () => void }) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSet() {
    setBusy(true)
    setError(null)
    try {
      const n = Number(value)
      if (!Number.isInteger(n) || n < 0 || n > 5) {
        throw new Error('firmware channel must be an integer 0-5')
      }
      await api.setFirmwareIndex(sensorKey, n)
      onSet()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not set the firmware channel')
    } finally {
      setBusy(false)
    }
  }

  return (
    <fieldset className="flex flex-col gap-3 border-t border-hairline pt-4">
      <legend className="prose-text mb-1 text-ink-dim">
        This sensor has no firmware danger-threshold channel yet. If it's wired to
        one of the kitchen PLC's own H2 sensor inputs, set which one (0-5) to
        enable a threshold — no naming requirement.
      </legend>
      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">firmware channel (0-5)</span>
        <input
          className="field-input max-w-24"
          inputMode="numeric"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="0"
        />
      </label>
      {error && (
        <p className="prose-text border-l-2 border-live pl-2 text-live" role="alert">
          {error}
        </p>
      )}
      <div className="flex justify-end">
        <button type="button" className="btn btn-neutral" onClick={handleSet} disabled={busy || value === ''}>
          {busy ? 'Setting…' : 'Set channel'}
        </button>
      </div>
    </fieldset>
  )
}

function ArchiveControl({
  sensor,
  onArchived,
  onRestored,
}: {
  sensor: Sensor
  onArchived: () => void
  onRestored: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleArchive() {
    setBusy(true)
    setError(null)
    try {
      await api.archiveSensor(sensor.sensor_key)
      onArchived()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not archive')
    } finally {
      setBusy(false)
    }
  }

  async function handleRestore() {
    setBusy(true)
    setError(null)
    try {
      await api.restoreSensor(sensor.sensor_key)
      onRestored()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not restore')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex shrink-0 flex-col items-end gap-1">
      {sensor.archived ? (
        <button type="button" className="btn btn-neutral" onClick={handleRestore} disabled={busy}>
          {busy ? 'Restoring…' : 'Restore'}
        </button>
      ) : (
        <button type="button" className="btn btn-quiet text-live" onClick={handleArchive} disabled={busy}>
          {busy ? 'Archiving…' : 'Archive'}
        </button>
      )}
      {error && (
        <p className="prose-text text-live" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

function PositionForm({ sensor, onSaved }: { sensor: Sensor; onSaved: () => void }) {
  const [x, setX] = useState(String(sensor.x))
  const [y, setY] = useState(String(sensor.y))
  const [z, setZ] = useState(String(sensor.z))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  // A different sensor was selected — reset the fields rather than keep
  // showing the previous sensor's coordinates in this one's inputs.
  useEffect(() => {
    setX(String(sensor.x))
    setY(String(sensor.y))
    setZ(String(sensor.z))
    setSaved(false)
  }, [sensor.sensor_key, sensor.x, sensor.y, sensor.z])

  async function handleSave() {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      const nx = Number(x)
      const ny = Number(y)
      const nz = Number(z)
      if ([nx, ny, nz].some((n) => Number.isNaN(n))) throw new Error('x, y, z must all be numbers')
      await api.upsertSensor(sensor.sensor_key, {
        label: sensor.label,
        x: nx,
        y: ny,
        z: nz,
        enabled: sensor.enabled,
        daq_device_id: sensor.daq_device_id,
        daq_sensor_name: sensor.daq_sensor_name,
      })
      setSaved(true)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the position')
    } finally {
      setBusy(false)
    }
  }

  return (
    <fieldset className="flex flex-col gap-3 border-t border-hairline pt-4">
      <legend className="prose-text mb-1 text-ink-dim">Position, metres — off a tape measure, never drag-and-drop</legend>
      <div className="grid grid-cols-3 gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-ink-dim">x</span>
          <input className="field-input" inputMode="decimal" value={x} onChange={(e) => setX(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-ink-dim">y</span>
          <input className="field-input" inputMode="decimal" value={y} onChange={(e) => setY(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-ink-dim">z</span>
          <input className="field-input" inputMode="decimal" value={z} onChange={(e) => setZ(e.target.value)} />
        </label>
      </div>
      {error && (
        <p className="prose-text border-l-2 border-live pl-2 text-live" role="alert">
          {error}
        </p>
      )}
      {saved && !error && <p className="prose-text text-safe">Saved.</p>}
      <div className="flex justify-end">
        <button type="button" className="btn btn-neutral" onClick={handleSave} disabled={busy}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </fieldset>
  )
}

function CalibrationForm({
  deviceId,
  sensorName,
  conversion,
  rawUnit,
  onSaved,
}: {
  deviceId: string
  sensorName: string
  conversion: DaqConversion | undefined
  rawUnit: string | undefined
  onSaved: (conv: DaqConversion) => void
}) {
  const [preset, setPreset] = useState<CalibrationPreset>(() => presetFor(conversion))
  const initialMethod = (conversion?.method as ConversionMethod) ?? 'linear'
  const [method, setMethod] = useState<ConversionMethod>(initialMethod)
  const [params, setParams] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(conversion?.params ?? {}).map(([k, v]) => [k, String(v)]),
    ),
  )
  const [unitSymbol, setUnitSymbol] = useState(conversion?.unit_symbol ?? '%v/v')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  function applyPreset(id: CalibrationPreset) {
    setPreset(id)
    const found = PRESETS.find((p) => p.id === id)
    if (found?.params) {
      setMethod('linear')
      setParams(Object.fromEntries(Object.entries(found.params).map(([k, v]) => [k, String(v)])))
      if (found.unit) setUnitSymbol(found.unit)
    }
  }

  function paramField(name: string, label: string, placeholder: string) {
    return (
      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">{label}</span>
        <input
          className="field-input"
          inputMode="decimal"
          aria-label={label}
          value={params[name] ?? ''}
          onChange={(e) => {
            setParams((p) => ({ ...p, [name]: e.target.value }))
            setPreset('custom')
          }}
          placeholder={placeholder}
        />
      </label>
    )
  }

  async function handleSave() {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      const numericParams: Record<string, number> = {}
      for (const [k, v] of Object.entries(params)) {
        if (v.trim() === '') continue
        const n = Number(v)
        if (Number.isNaN(n)) throw new Error(`${k} must be a number`)
        numericParams[k] = n
      }
      await api.setConversion(deviceId, sensorName, {
        method,
        params: numericParams,
        unit_symbol: unitSymbol.trim(),
      })
      onSaved({ method, params: numericParams, unit_symbol: unitSymbol.trim(), conv_id: null })
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the calibration')
    } finally {
      setBusy(false)
    }
  }

  async function handleClear() {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await api.deleteConversion(deviceId, sensorName)
      setParams({})
      setUnitSymbol('%v/v')
      setMethod('linear')
      setPreset('custom')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not clear the calibration')
    } finally {
      setBusy(false)
    }
  }

  return (
    <fieldset className="flex flex-col gap-3 border-t border-hairline pt-4">
      <legend className="prose-text mb-1 text-ink-dim">
        Calibration — stored in DataAcquisition, applied here and in the historian
      </legend>

      <div className="flex flex-col gap-1" role="group" aria-label="Calibration preset">
        {PRESETS.map((p) => (
          <label key={p.id} className="flex cursor-pointer items-center gap-2">
            <input
              type="radio"
              name={`preset-${deviceId}-${sensorName}`}
              checked={preset === p.id}
              onChange={() => applyPreset(p.id)}
              className="cursor-pointer"
            />
            <span className="text-ink">{p.label}</span>
          </label>
        ))}
      </div>

      {preset === 'custom' && (
        <>
          <div className="flex gap-1" role="group" aria-label="Method">
            {METHODS.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMethod(m)}
                aria-pressed={method === m}
                className={`cursor-pointer rounded-sm border px-2.5 py-1 ${
                  method === m ? 'border-hairline bg-raised text-ink' : 'border-transparent text-ink-dim hover:text-ink'
                }`}
              >
                {m}
              </button>
            ))}
          </div>
          <p className="prose-text text-ink-faint">{METHOD_HELP[method]}</p>
        </>
      )}

      {method === 'linear' && (
        <div className="grid grid-cols-2 gap-3">
          {paramField('raw_min', 'raw min', rawUnit === 'V' ? '0' : '4')}
          {paramField('raw_max', 'raw max', rawUnit === 'V' ? '10' : '20')}
          {paramField('min_value', 'value at raw min', '0')}
          {paramField('max_value', 'value at raw max', '100')}
        </div>
      )}
      {preset === 'custom' && method === 'ax_b' && (
        <div className="grid grid-cols-2 gap-3">
          {paramField('a', 'a', '1.0')}
          {paramField('b', 'b', '0.0')}
        </div>
      )}

      <label className="flex flex-col gap-1">
        <span className="text-ink-dim">unit</span>
        <input
          className="field-input"
          value={unitSymbol}
          onChange={(e) => setUnitSymbol(e.target.value)}
          placeholder="%v/v"
        />
      </label>

      {error && (
        <p className="prose-text border-l-2 border-live pl-2 text-live" role="alert">
          {error}
        </p>
      )}
      {saved && !error && <p className="prose-text text-safe">Saved.</p>}

      <div className="flex justify-end gap-2">
        <button type="button" className="btn btn-quiet" onClick={handleClear} disabled={busy}>
          Clear calibration
        </button>
        <button type="button" className="btn btn-neutral" onClick={handleSave} disabled={busy}>
          {busy ? 'Saving…' : 'Save calibration'}
        </button>
      </div>
    </fieldset>
  )
}

/** "Zero in clean air" — averages 100 live raw samples and writes the
 *  result as the calibration's raw_min. Polls the session's status every
 *  500ms while active; the backend itself pulls in new samples on each
 *  status check (see app.zeroing.collect()). */
function ZeroingControl({
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

function ThresholdForm({
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
