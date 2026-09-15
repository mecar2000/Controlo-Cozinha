/**
 * DevicePane — the device-first main view of the Sensors & devices panel.
 *
 * A sensor only ever exists in Cozinha because DataAcquisition is already
 * publishing it — pin identity, type and calibration are entirely
 * DataAcquisition's data, commissioned in its own dashboard. So there is no
 * "Unassigned" (a sensor with no DAQ device) and no add/edit modal: every
 * device's pane shows one row per pin DataAcquisition CURRENTLY publishes,
 * and the only thing Cozinha ever edits is that row's label and 3D position,
 * inline, autosaved on blur. A row with no position yet renders greyed out —
 * it isn't rendered in the room or converted to %v/v until positioned.
 *
 * There is no soft-delete/archive concept any more: removing a sensor from a
 * device's row is a plain hard delete of its local sensor_config row (label,
 * position, firmware wiring) via a per-row action — it never touches
 * DataAcquisition, so the pin keeps publishing and can be repositioned again
 * any time. Left rail: devices only. Right pane: the selected device's
 * published pins as inline-editable rows, plus a live 3D preview of every
 * active sensor (highlighting whichever row currently has focus) beside the
 * table. Selecting an already-positioned row's name still expands its
 * calibration/threshold detail below the table, inline.
 */
import { useMemo, useRef, useState } from 'react'

import type { ConfigAck, DaqConversion, DaqDevice, LiveReadings, Sensor } from '@/api/types'
import * as api from '@/api/client'
import { ApiError } from '@/api/client'
import { ConfirmPopover } from '@/components/common/ConfirmPopover'
import { RoomScene } from '@/components/room/RoomScene'
import type { LiveSensor } from '@/hooks/useKitchen'
import { pinLabel } from '@/lib/pinLabel'
import { usedFirmwareChannels as computeUsedFirmwareChannels } from '@/lib/usedFirmwareChannels'

import { DeleteDeviceControl } from './DeleteDeviceControl'
import { SensorDetail, type SensorRow } from './SensorDetail'

/** Nav sentinel for the "Orphaned" section — not a real DAQ device id, so it
 *  can share selectedDeviceId's state without a second selection mode. */
const ORPHANED_NAV_ID = '__orphaned__'

function readingKeyFor(sensor: Sensor): string {
  return sensor.daq_device_id
    ? `${sensor.daq_device_id}/${sensor.daq_sensor_name ?? sensor.sensor_key}`
    : (sensor.daq_sensor_name ?? sensor.sensor_key)
}

/** Sensor key for a not-yet-positioned pin, derived from the DAQ name DAQ
 *  already gave it — Cozinha never invents a second identity for a pin. */
function sensorKeyFromDaqName(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return slug || 'sensor'
}

export function DevicePane({
  sensors,
  devices,
  conversions,
  liveReadings,
  liveSensors,
  configAck,
  daqDashboardUrl,
  reloadSensors,
  reloadDevices,
}: {
  sensors: Sensor[]
  devices: DaqDevice[] | null
  conversions: Record<string, DaqConversion>
  liveReadings: LiveReadings
  /** Every active sensor with its live reading, already joined the way
   *  useKitchen.ts does — reused as-is for the 3D position preview, so the
   *  preview never recomputes that join a second time. */
  liveSensors: LiveSensor[]
  configAck: ConfigAck
  daqDashboardUrl: string | null
  reloadSensors: () => void
  reloadDevices: () => void
}) {
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>('')
  const [selectedKey, setSelectedKey] = useState<string | null>(null)

  const usedFirmwareChannels = useMemo(() => computeUsedFirmwareChannels(sensors), [sensors])

  const boundByDevicePin = useMemo(() => {
    const map = new Map<string, Sensor>()
    for (const s of sensors) {
      if (s.daq_device_id && s.daq_pin != null) {
        map.set(`${s.daq_device_id}/${s.daq_pin}`, s)
      }
    }
    return map
  }, [sensors])

  const sensorCountByDevice = useMemo(() => {
    const counts = new Map<string, number>()
    for (const s of sensors) {
      if (!s.daq_device_id) continue
      counts.set(s.daq_device_id, (counts.get(s.daq_device_id) ?? 0) + 1)
    }
    return counts
  }, [sensors])

  // A sensor bound to a daq_device_id/daq_pin that no device CURRENTLY
  // reports (the pin was reconfigured or removed in DataAcquisition, or the
  // whole device was deleted) has no row in any device's pin table — it
  // would otherwise be invisible anywhere in this panel. Checked against the
  // union of every device's current pins, not just the selected one: the
  // device the sensor used to be bound to might not even be the one open.
  const orphanedSensors = useMemo(() => {
    const livePins = new Set<string>()
    for (const d of devices ?? []) {
      for (const p of d.config.sensors) livePins.add(`${d.device_id}/${p.pin}`)
    }
    return sensors.filter(
      (s) => s.daq_device_id && s.daq_pin != null && !livePins.has(`${s.daq_device_id}/${s.daq_pin}`),
    )
  }, [sensors, devices])

  // Default to the first device once devices load, so the pane isn't blank.
  const firstDevice = devices?.[0]
  const effectiveDeviceId = selectedDeviceId === '' && firstDevice ? firstDevice.device_id : selectedDeviceId

  const selectedDevice =
    effectiveDeviceId === ORPHANED_NAV_ID ? null : (devices?.find((d) => d.device_id === effectiveDeviceId) ?? null)
  const showingOrphaned = effectiveDeviceId === ORPHANED_NAV_ID

  const selectedSensor = sensors.find((s) => s.sensor_key === selectedKey) ?? null
  const selectedRow: SensorRow | null = selectedSensor
    ? { sensor: selectedSensor, firmwareIndex: firmwareIndexOfLocal(selectedSensor) }
    : null

  function firmwareIndexOfLocal(sensor: Sensor): number | null {
    if (sensor.firmware_index != null) return sensor.firmware_index
    const m = /^H2-(\d+)$/.exec(sensor.daq_sensor_name ?? '')
    if (!m) return null
    const idx = Number(m[1]) - 1
    return idx >= 0 && idx <= 5 ? idx : null
  }

  function selectSensor(key: string) {
    setSelectedKey(key)
  }

  return (
    <div className="flex min-h-0 flex-1">
      <nav className="w-64 shrink-0 overflow-y-auto border-r border-hairline py-2" aria-label="Devices">
        {!devices ? (
          <p className="prose-text px-4 py-3 text-ink-dim">Loading devices…</p>
        ) : (
          devices.map((d) => {
            const active = effectiveDeviceId === d.device_id
            return (
              <button
                key={d.device_id}
                type="button"
                onClick={() => {
                  setSelectedDeviceId(d.device_id)
                  setSelectedKey(null)
                }}
                className={`flex w-full cursor-pointer flex-col items-start gap-1 px-4 py-3 text-left ${
                  active ? 'bg-raised' : 'hover:bg-raised/50'
                }`}
              >
                <span className="flex items-center gap-2">
                  <span
                    className={`status-dot ${d.status === 'online' ? 'status-dot-safe' : 'status-dot-off'}`}
                    aria-hidden
                  />
                  <span className={active ? 'font-medium text-ink' : 'text-ink'}>{d.device_id}</span>
                </span>
                <span className="prose-text text-ink-faint">
                  {sensorCountByDevice.get(d.device_id) ?? 0} sensor
                  {sensorCountByDevice.get(d.device_id) === 1 ? '' : 's'}
                </span>
              </button>
            )
          })
        )}
        {orphanedSensors.length > 0 && (
          <button
            type="button"
            onClick={() => {
              setSelectedDeviceId(ORPHANED_NAV_ID)
              setSelectedKey(null)
            }}
            className={`flex w-full cursor-pointer flex-col items-start gap-1 border-t border-hairline px-4 py-3 text-left ${
              showingOrphaned ? 'bg-raised' : 'hover:bg-raised/50'
            }`}
          >
            <span className={showingOrphaned ? 'font-medium text-ink' : 'text-ink'}>Orphaned</span>
            <span className="prose-text text-ink-faint">
              {orphanedSensors.length} sensor{orphanedSensors.length === 1 ? '' : 's'}
            </span>
          </button>
        )}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto px-8 py-6">
        {showingOrphaned ? (
          <OrphanedSensorsPanel
            sensors={orphanedSensors}
            onDeleted={() => {
              setSelectedKey(null)
              reloadSensors()
            }}
          />
        ) : selectedDevice ? (
          <DevicePinTable
            device={selectedDevice}
            boundByDevicePin={boundByDevicePin}
            boundSensorCount={sensorCountByDevice.get(selectedDevice.device_id) ?? 0}
            selectedKey={selectedKey}
            onSelectSensor={selectSensor}
            onDeviceDeleted={() => {
              setSelectedDeviceId('')
              setSelectedKey(null)
              reloadDevices()
              reloadSensors()
            }}
            selectedRow={selectedRow}
            conversions={conversions}
            liveReadings={liveReadings}
            liveSensors={liveSensors}
            configAck={configAck}
            usedFirmwareChannels={usedFirmwareChannels}
            daqDashboardUrl={daqDashboardUrl}
            onConversionSaved={() => reloadSensors()}
            onDeleted={() => {
              setSelectedKey(null)
              reloadSensors()
            }}
            onRefresh={reloadSensors}
            onSaved={reloadSensors}
          />
        ) : (
          <p className="prose-text text-ink-dim">No devices reported yet.</p>
        )}
      </div>
    </div>
  )
}

/** Sensors whose daq_device_id/daq_pin no longer matches any pin any device
 *  currently reports — the device was deleted, or the pin was reassigned in
 *  DataAcquisition. Not shown in any device's own pin table (it's not one of
 *  that device's current pins any more), so without this section they were
 *  invisible anywhere in the panel. Delete is the only action: there is no
 *  pin left to reposition them onto here. */
function OrphanedSensorsPanel({ sensors, onDeleted }: { sensors: Sensor[]; onDeleted: () => void }) {
  const [confirmingKey, setConfirmingKey] = useState<string | null>(null)
  const [deletingKey, setDeletingKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleDelete(key: string) {
    setDeletingKey(key)
    setError(null)
    try {
      await api.deleteSensor(key)
      setConfirmingKey(null)
      onDeleted()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete')
    } finally {
      setDeletingKey(null)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="text-lede font-medium text-ink">Orphaned sensors</p>
        <p className="prose-text mt-1 text-ink-faint">
          These sensors are bound to a pin that no device currently reports — the pin was reassigned or the device was
          removed in DataAcquisition. Nothing to reposition them onto here; delete to clean up, or reassign a working
          pin to a fresh row on the right device.
        </p>
      </div>
      {error && (
        <p className="prose-text text-live" role="alert">
          {error}
        </p>
      )}
      <ul className="flex flex-col divide-y divide-hairline/60 border-y border-hairline">
        {sensors.map((s) => (
          <li key={s.sensor_key} className="flex items-center justify-between gap-4 py-3">
            <span>
              <span className="text-ink">{s.label}</span>
              <span className="prose-text ml-2 text-ink-faint">
                {s.daq_device_id}/{s.daq_pin}
              </span>
            </span>
            {confirmingKey === s.sensor_key ? (
              <ConfirmPopover
                message={`Permanently delete "${s.label}"? This cannot be undone.`}
                confirmLabel="Delete"
                busyLabel="Deleting…"
                busy={deletingKey === s.sensor_key}
                onCancel={() => setConfirmingKey(null)}
                onConfirm={() => void handleDelete(s.sensor_key)}
              />
            ) : (
              <button
                type="button"
                className="btn btn-quiet text-live"
                onClick={() => setConfirmingKey(s.sensor_key)}
              >
                Delete
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Local editable draft for one pin's row — kept separate from the saved
 *  Sensor so in-progress typing survives an unrelated poll/reload and so the
 *  3D preview can reflect a value before it's actually saved. */
interface RowDraft {
  label: string
  x: string
  y: string
  z: string
}

function draftFromSensor(sensor: Sensor | undefined): RowDraft {
  return {
    label: sensor?.label ?? '',
    x: sensor ? String(sensor.x) : '',
    y: sensor ? String(sensor.y) : '',
    z: sensor ? String(sensor.z) : '',
  }
}

/** The selected device's PUBLISHED pins as rows — DAQ name/type read-only,
 *  label/x/y/z inline-editable and autosaved on blur, plus a small "Clear"
 *  action that hard-deletes the row's local sensor_config entry (never
 *  touches DataAcquisition — the pin keeps publishing and can be
 *  repositioned later). A pin with no sensor_config row yet renders greyed
 *  out. Selecting an already-positioned row's name expands its
 *  calibration/threshold detail below the table. */
function DevicePinTable({
  device,
  boundByDevicePin,
  boundSensorCount,
  selectedKey,
  onSelectSensor,
  onDeviceDeleted,
  selectedRow,
  conversions,
  liveReadings,
  liveSensors,
  configAck,
  usedFirmwareChannels,
  daqDashboardUrl,
  onConversionSaved,
  onDeleted,
  onRefresh,
  onSaved,
}: {
  device: DaqDevice
  boundByDevicePin: Map<string, Sensor>
  boundSensorCount: number
  selectedKey: string | null
  onSelectSensor: (key: string) => void
  onDeviceDeleted: () => void
  selectedRow: SensorRow | null
  conversions: Record<string, DaqConversion>
  liveReadings: LiveReadings
  liveSensors: LiveSensor[]
  configAck: ConfigAck
  usedFirmwareChannels: Set<number>
  daqDashboardUrl: string | null
  onConversionSaved: (conv: DaqConversion) => void
  onDeleted: () => void
  onRefresh: () => void
  onSaved: () => void
}) {
  const pins = device.config.sensors
  const selectedSensorBoundHere =
    selectedRow && selectedRow.sensor.daq_device_id === device.device_id ? selectedRow.sensor : null

  const [drafts, setDrafts] = useState<Record<number, RowDraft>>({})
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({})
  const [focusedPin, setFocusedPin] = useState<number | null>(null)
  const [confirmingClearPin, setConfirmingClearPin] = useState<number | null>(null)
  const [clearingPin, setClearingPin] = useState<number | null>(null)
  const [savingPin, setSavingPin] = useState<number | null>(null)
  // Keyed by pin: the in-flight saveRow() promise, if any. clearRow() awaits
  // this before issuing its DELETE, so a blur-triggered save that was
  // already in flight can never resolve AFTER a clear and silently
  // re-create the row the operator just removed.
  const saveInFlight = useRef<Record<number, Promise<void> | undefined>>({})

  function draftFor(pin: number): RowDraft {
    const existing = drafts[pin]
    if (existing) return existing
    return draftFromSensor(boundByDevicePin.get(`${device.device_id}/${pin}`))
  }

  function updateDraft(pin: number, patch: Partial<RowDraft>) {
    setDrafts((prev) => ({
      ...prev,
      [pin]: {
        ...(prev[pin] ?? draftFromSensor(boundByDevicePin.get(`${device.device_id}/${pin}`))),
        ...patch,
      },
    }))
  }

  async function saveRow(pin: number, sensorName: string) {
    // A clear already started (or completed) for this pin takes priority —
    // a save queued by an earlier blur must not resurrect a row the
    // operator just told us to remove.
    if (clearingPin === pin || confirmingClearPin === pin) return

    const draft = draftFor(pin)
    // Number('') is 0, not NaN — an empty field must not read as "filled
    // in with zero", or a blur while x/y are set and z is still untouched
    // would save prematurely (and disable the row mid-edit, stealing focus
    // from whichever field the operator just clicked into).
    if ([draft.x, draft.y, draft.z].some((v) => v.trim() === '')) return
    const nx = Number(draft.x)
    const ny = Number(draft.y)
    const nz = Number(draft.z)
    if (![nx, ny, nz].every(Number.isFinite)) return // not a valid number — nothing to save

    const bound = boundByDevicePin.get(`${device.device_id}/${pin}`)
    const sensorKey = bound?.sensor_key ?? sensorKeyFromDaqName(sensorName)
    const label = draft.label.trim() || sensorName

    setRowErrors((prev) => {
      const next = { ...prev }
      delete next[pin]
      return next
    })
    setSavingPin(pin)
    async function run() {
      try {
        await api.upsertSensor(sensorKey, {
          label,
          x: nx,
          y: ny,
          z: nz,
          daq_device_id: device.device_id,
          daq_pin: pin,
        })
        onSaved()
      } catch (err) {
        const message = err instanceof ApiError ? err.message : 'Could not save'
        setRowErrors((prev) => ({ ...prev, [pin]: message }))
      } finally {
        setSavingPin((cur) => (cur === pin ? null : cur))
        if (saveInFlight.current[pin] === ownPromise) delete saveInFlight.current[pin]
      }
    }
    const ownPromise = run()
    saveInFlight.current[pin] = ownPromise
    await ownPromise
  }

  /** "Clear" — hard-deletes this pin's local sensor_config row (label,
   *  position, firmware wiring). Never touches DataAcquisition: the pin
   *  keeps publishing and the row simply reverts to greyed-out/unpositioned,
   *  ready to be positioned again.
   *
   *  Awaits any save already in flight for this pin FIRST, so the DELETE
   *  always lands after the PUT rather than racing it — otherwise a blur-
   *  triggered save landing after this DELETE would silently re-create the
   *  row via upsert-by-key, right after the operator just cleared it. */
  async function clearRow(pin: number) {
    const bound = boundByDevicePin.get(`${device.device_id}/${pin}`)
    if (!bound) return
    setClearingPin(pin)
    setRowErrors((prev) => {
      const next = { ...prev }
      delete next[pin]
      return next
    })
    try {
      await saveInFlight.current[pin]
      await api.deleteSensor(bound.sensor_key)
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[pin]
        return next
      })
      setConfirmingClearPin(null)
      onDeleted()
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Could not clear'
      setRowErrors((prev) => ({ ...prev, [pin]: message }))
      setConfirmingClearPin(null)
    } finally {
      setClearingPin(null)
    }
  }

  // The 3D preview: every active sensor, with the row currently being
  // edited overlaid at its DRAFT position (so the marker moves as the
  // operator types, not just after blur/save) and highlighted.
  const focusedBound = focusedPin != null ? boundByDevicePin.get(`${device.device_id}/${focusedPin}`) : undefined
  const focusedDraft = focusedPin != null ? draftFor(focusedPin) : null
  const focusedDraftPos =
    focusedDraft && [focusedDraft.x, focusedDraft.y, focusedDraft.z].every((v) => Number.isFinite(Number(v)) && v !== '')
      ? { x: Number(focusedDraft.x), y: Number(focusedDraft.y), z: Number(focusedDraft.z) }
      : null
  const focusedSensorKey = focusedBound?.sensor_key ?? (focusedPin != null ? `__preview-${device.device_id}-${focusedPin}` : null)

  const previewSensors: LiveSensor[] = useMemo(() => {
    const base = liveSensors.filter((s) => s.key !== focusedBound?.sensor_key)
    if (focusedPin == null || !focusedDraftPos || !focusedSensorKey) return liveSensors
    const focusedPinInfo = pins.find((p) => p.pin === focusedPin)
    return [
      ...base,
      {
        key: focusedSensorKey,
        label: focusedBound?.label ?? focusedDraft?.label ?? focusedPinInfo?.name ?? 'preview',
        x: focusedDraftPos.x,
        y: focusedDraftPos.y,
        z: focusedDraftPos.z,
        value: focusedBound ? (liveSensors.find((s) => s.key === focusedBound.sensor_key)?.value ?? 0) : 0,
        hasReading: focusedBound ? (liveSensors.find((s) => s.key === focusedBound.sensor_key)?.hasReading ?? false) : false,
        ageS: null,
        unit: '%v/v',
      },
    ]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveSensors, focusedPin, focusedDraftPos?.x, focusedDraftPos?.y, focusedDraftPos?.z])

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-lede font-medium text-ink">{device.device_id}</p>
          <p className="prose-text mt-1 text-ink-faint">
            {device.location ?? 'no location'} · {device.status} · {pins.length} published pin
            {pins.length === 1 ? '' : 's'}
          </p>
        </div>
        <div className="flex shrink-0 items-start gap-2">
          <DeleteDeviceControl
            deviceId={device.device_id}
            boundSensorCount={boundSensorCount}
            onDeleted={onDeviceDeleted}
          />
        </div>
      </div>

      <div className="flex gap-8">
        <table className="flex-1 basis-1/2 border-collapse" style={{ borderSpacing: 0 }}>
          <thead>
            <tr className="border-b border-hairline text-ink-dim">
              <th className="w-1 py-2.5 pr-4 text-center font-normal">pin</th>
              <th className="py-2.5 pr-4 text-center font-normal">DAQ name</th>
              <th className="py-2.5 pr-6 text-center font-normal">label</th>
              <th className="w-1 py-2.5 pr-1.5 text-center font-normal">x</th>
              <th className="w-1 py-2.5 pr-1.5 text-center font-normal">y</th>
              <th className="w-1 py-2.5 pr-4 text-center font-normal">z</th>
              <th className="w-1 py-2.5 text-center font-normal"></th>
            </tr>
          </thead>
          <tbody>
            {pins.map((p) => {
              const bound = boundByDevicePin.get(`${device.device_id}/${p.pin}`)
              const positioned = bound != null
              const active = bound?.sensor_key === selectedKey
              const draft = draftFor(p.pin)
              const rowError = rowErrors[p.pin]
              const handleBlur = () => {
                setFocusedPin((cur) => (cur === p.pin ? null : cur))
                void saveRow(p.pin, p.name)
              }
              const handleFocus = () => setFocusedPin(p.pin)

              return (
                <tr
                  key={p.pin}
                  data-positioned={positioned}
                  className={`border-b border-hairline/60 ${active ? 'bg-raised' : ''} ${
                    positioned ? '' : 'opacity-50'
                  }`}
                >
                  <td className="whitespace-nowrap py-3 pr-4 font-mono text-ink-dim">{pinLabel(p.pin)}</td>
                  <td className="py-3 pr-4">
                    {bound ? (
                      <button
                        type="button"
                        onClick={() => onSelectSensor(bound.sensor_key)}
                        className={`cursor-pointer text-left ${active ? 'font-medium text-ink' : 'text-ink hover:underline'}`}
                      >
                        {p.name}
                      </button>
                    ) : (
                      <span className="text-ink-faint">{p.name}</span>
                    )}
                    <div className="prose-text text-ink-faint">{p.type}</div>
                  </td>
                  <td className="py-3 pr-6">
                    <input
                      aria-label="label"
                      className="field-input w-36"
                      placeholder={p.name}
                      value={draft.label}
                      onFocus={handleFocus}
                      onChange={(e) => updateDraft(p.pin, { label: e.target.value })}
                      onBlur={handleBlur}
                    />
                  </td>
                  <td className="py-3 pr-1.5">
                    <input
                      aria-label="x"
                      className="field-input field-num w-16 text-center"
                      inputMode="decimal"
                      value={draft.x}
                      onFocus={handleFocus}
                      onChange={(e) => updateDraft(p.pin, { x: e.target.value })}
                      onBlur={handleBlur}
                    />
                  </td>
                  <td className="py-3 pr-1.5">
                    <input
                      aria-label="y"
                      className="field-input field-num w-16 text-center"
                      inputMode="decimal"
                      value={draft.y}
                      onFocus={handleFocus}
                      onChange={(e) => updateDraft(p.pin, { y: e.target.value })}
                      onBlur={handleBlur}
                    />
                  </td>
                  <td className="py-3 pr-4">
                    <input
                      aria-label="z"
                      className="field-input field-num w-16 text-center"
                      inputMode="decimal"
                      value={draft.z}
                      onFocus={handleFocus}
                      onChange={(e) => updateDraft(p.pin, { z: e.target.value })}
                      onBlur={handleBlur}
                    />
                    {rowError && (
                      <p className="prose-text mt-1 whitespace-nowrap text-live" role="alert">
                        {rowError}
                      </p>
                    )}
                  </td>
                  <td className="whitespace-nowrap py-3">
                    {positioned && (
                      confirmingClearPin === p.pin ? (
                        <ConfirmPopover
                          message="Remove this sensor's position & label here — the pin stays published in DataAcquisition and can be positioned again later."
                          confirmLabel="Clear"
                          busyLabel="Clearing…"
                          busy={clearingPin === p.pin}
                          onCancel={() => setConfirmingClearPin(null)}
                          onConfirm={() => void clearRow(p.pin)}
                        />
                      ) : (
                        <button
                          type="button"
                          className="btn btn-quiet text-live"
                          onClick={() => setConfirmingClearPin(p.pin)}
                          disabled={savingPin === p.pin}
                        >
                          Clear
                        </button>
                      )
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        <div
          className="relative flex-1 basis-1/2 self-stretch overflow-hidden rounded-sm border border-hairline"
          aria-label="Sensor position preview"
        >
          <RoomScene sensors={previewSensors} samples={[]} mode="sensors" highlightedKey={focusedSensorKey} />
        </div>
      </div>

      {selectedSensorBoundHere && selectedRow && (
        <div className="border-t border-hairline pt-6">
          <SensorDetail
            row={selectedRow}
            conversion={conversions[readingKeyFor(selectedSensorBoundHere)]}
            liveReading={liveReadings[readingKeyFor(selectedSensorBoundHere)]}
            configAck={configAck}
            usedFirmwareChannels={usedFirmwareChannels}
            daqDashboardUrl={daqDashboardUrl}
            onConversionSaved={onConversionSaved}
            onRefresh={onRefresh}
          />
        </div>
      )}
    </div>
  )
}
