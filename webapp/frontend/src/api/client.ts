/**
 * api/client — every call this frontend makes to the Flask backend.
 *
 * The browser never talks to DataAcquisition directly: the DAQ calls below
 * go to this app's own /api/daq/* proxy routes, which hold the DAQ token
 * server-side. One place decides authorization for a hydrogen command.
 */

import type {
  Ack,
  BatchZeroingStatus,
  DaqConversion,
  DaqDevice,
  DaqDeviceSensor,
  DaqExperiment,
  DaqHistory,
  DaqStage,
  LayoutForRun,
  LiveReadings,
  Meta,
  Run,
  RunConfig,
  RunSpec,
  Sensor,
  SetConversionRequest,
  StartRunRequest,
  StartRunResponse,
  Status,
  ZeroingResult,
  ZeroingStatus,
} from './types'

/** Bearer token, when the backend has DASHBOARD_TOKEN set. Empty disables
 *  auth entirely, matching the backend's own model. */
const TOKEN = import.meta.env.VITE_DASHBOARD_TOKEN ?? ''

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** True when the backend refused before anything reached the firmware —
     *  a rejection the operator can act on, not a transport failure. */
    readonly rejected = false,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) }
  if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`
  if (init?.body) headers['Content-Type'] = 'application/json'

  let resp: Response
  try {
    resp = await fetch(path, { ...init, headers })
  } catch (cause) {
    throw new ApiError(
      'Cannot reach the kitchen server. Check that it is running.',
      0,
    )
  }

  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`
    let rejected = false
    try {
      const body = await resp.json()
      if (body?.error) detail = body.error
      rejected = Boolean(body?.rejected)
    } catch {
      /* non-JSON error body; the status line is all we have */
    }
    throw new ApiError(detail, resp.status, rejected)
  }

  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
}

// --- Status and live data -------------------------------------------------

export const getStatus = () => request<Status>('/api/status')
export const getLiveReadings = () => request<LiveReadings>('/api/live-readings')

/** Backend-derived settings the frontend needs but must not hardcode a
 *  second copy of — see routes/meta.py. Currently just the two kitchen
 *  device ids (control topics vs. the DAQ sensor namespace). */
export const getMeta = () => request<Meta>('/api/meta')

/**
 * Takes, renews, or releases the display-mode lease.
 *
 * `enabled: true` both takes and renews, so the display screen just calls
 * this on a heartbeat. The server expires the lease on its own if the
 * heartbeat stops — a closed tab or a dropped network must not leave Start
 * disabled for the next operator.
 */
export const setDisplayMode = (enabled: boolean) =>
  post<{ display_mode: boolean; lease_s: number }>('/api/display-mode', { enabled })

// --- Run lifecycle --------------------------------------------------------

export const listRuns = (limit = 100, offset = 0) =>
  request<Run[]>(`/api/runs?limit=${limit}&offset=${offset}`)
export const getCurrentRun = () => request<Run | null>('/api/runs/current')
export const getRun = (id: number) => request<Run>(`/api/runs/${id}`)

/**
 * Step 1 and the send-half of step 2: sends start(spec) and returns once the
 * ack has arrived. Confirming is a separate, explicit call so a human sees
 * the ack diff before gas flows.
 */
export const startRun = (body: StartRunRequest) =>
  post<StartRunResponse>('/api/runs/start', body)

export const confirmRun = (runId: number, wireRunId: string) =>
  post<Run>(`/api/runs/${runId}/confirm`, { wire_run_id: wireRunId })

export const cancelRun = (runId: number) => post<Run>(`/api/runs/${runId}/cancel`)

export const overrideStage = (runId: number, label: string) =>
  post<{ ok: true }>(`/api/runs/${runId}/stage`, { label })

/**
 * Stop and acknowledge go straight to MQTT. They are never gated on DAQ,
 * display mode, or the database — the one thing that must keep working when
 * everything else is on fire.
 */
export const stop = () => post<{ ok: true }>('/api/control/stop')
export const acknowledge = () => post<{ ok: true }>('/api/control/ack')

// --- Configs --------------------------------------------------------------

export const listConfigs = (includeArchived = false) =>
  request<RunConfig[]>(`/api/configs${includeArchived ? '?include_archived=1' : ''}`)
export const getConfig = (id: number) => request<RunConfig>(`/api/configs/${id}`)
export const createConfig = (name: string, spec: RunSpec) =>
  post<RunConfig>('/api/configs', { name, spec })
export const updateConfig = (id: number, name: string, spec: RunSpec) =>
  request<RunConfig>(`/api/configs/${id}`, {
    method: 'PUT',
    body: JSON.stringify({ name, spec }),
  })
export const archiveConfig = (id: number) =>
  request<{ archived: true }>(`/api/configs/${id}`, { method: 'DELETE' })

// --- Sensors and layout ---------------------------------------------------

export const listSensors = (enabledOnly = false) => {
  const params = new URLSearchParams()
  if (enabledOnly) params.set('enabled_only', '1')
  const qs = params.toString()
  return request<Sensor[]>(`/api/sensors${qs ? `?${qs}` : ''}`)
}

/** daq_sensor_name is deliberately NOT part of this body — it is DERIVED
 *  server-side from daq_pin (routes/sensors.py::_derive_daq_sensor_name), so
 *  a typed value here can never take effect and isn't offered as if it
 *  could. Set daq_pin to choose a sensor's DAQ identity. */
export const upsertSensor = (
  sensorKey: string,
  body: {
    label: string
    x: number
    y: number
    z: number
    enabled?: boolean
    daq_device_id?: string | null
    daq_pin?: number | null
  },
) =>
  request<Sensor>(`/api/sensors/${encodeURIComponent(sensorKey)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })

/** Hard delete — permanent, no undo. */
export const deleteSensor = (sensorKey: string) =>
  request<{ deleted: true }>(`/api/sensors/${encodeURIComponent(sensorKey)}`, {
    method: 'DELETE',
  })

export const setFirmwareIndex = (sensorKey: string, firmwareIndex: number | null) =>
  request<{ sensor_key: string; firmware_index: number | null }>(
    `/api/sensors/${encodeURIComponent(sensorKey)}/firmware-index`,
    { method: 'PUT', body: JSON.stringify({ firmware_index: firmwareIndex }) },
  )

export const getLayoutForRun = (runId: number) =>
  request<LayoutForRun>(`/api/runs/${runId}/layout`)

// --- Zero in clean air ------------------------------------------------------

export const startZeroing = (sensorKey: string, targetSamples?: number) =>
  request<ZeroingStatus>(
    `/api/sensors/${encodeURIComponent(sensorKey)}/zero/start${
      targetSamples ? `?target_samples=${targetSamples}` : ''
    }`,
    { method: 'POST' },
  )

export const getZeroingStatus = (sensorKey: string) =>
  request<ZeroingStatus>(`/api/sensors/${encodeURIComponent(sensorKey)}/zero/status`)

export const applyZeroing = (sensorKey: string) =>
  request<ZeroingResult>(`/api/sensors/${encodeURIComponent(sensorKey)}/zero/apply`, { method: 'POST' })

export const cancelZeroing = (sensorKey: string) =>
  request<{ ok: true }>(`/api/sensors/${encodeURIComponent(sensorKey)}/zero/cancel`, { method: 'POST' })

/** "Zero all in clean air" — one capture per sensor with a DAQ identity,
 *  run sequentially server-side; the caller just polls getZeroingStatusAll
 *  until done, same shape as the per-sensor start/status/cancel above. */
export const startZeroingAll = (targetSamples?: number) =>
  request<BatchZeroingStatus>(
    `/api/sensors/zero-all/start${targetSamples ? `?target_samples=${targetSamples}` : ''}`,
    { method: 'POST' },
  )

export const getZeroingStatusAll = () => request<BatchZeroingStatus>('/api/sensors/zero-all/status')

export const cancelZeroingAll = () =>
  request<{ ok: true }>('/api/sensors/zero-all/cancel', { method: 'POST' })

export const setRawMinManually = (sensorKey: string, rawMin: number) =>
  request<ZeroingResult>(`/api/sensors/${encodeURIComponent(sensorKey)}/zero/manual`, {
    method: 'PUT',
    body: JSON.stringify({ raw_min: rawMin }),
  })

// --- DataAcquisition, proxied ---------------------------------------------

/** Read-only device/pin viewer (Part 5, Stage 1) — every device
 *  DataAcquisition has seen, with its capabilities and current pin
 *  configuration. See app.daq.list_devices. */
export const listDaqDevices = () => request<DaqDevice[]>('/api/daq/devices')

/** Commissioning wizard (Part 5, Stage 2) — replaces a device's whole pin
 *  map. `POST /config` is a FULL REPLACE in DataAcquisition (see
 *  app.daq.push_config): always send the COMPLETE sensor list, re-fetched
 *  via listDaqDevices() immediately before calling this — there is no
 *  version/etag, so a stale list here silently drops every pin left out. */
export const pushDaqDeviceConfig = (
  deviceId: string,
  body: { sensors: DaqDeviceSensor[]; interval_ms?: number },
) =>
  request<{ ok: boolean }>(`/api/daq/devices/${encodeURIComponent(deviceId)}/config`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

/** Permanently removes a device from DataAcquisition — its config, sensor
 *  list, and stored conversions. See app.daq.delete_device: does not touch
 *  historical readings, and does not cascade into this app's own
 *  sensor_config (a sensor still pointing at the deleted device becomes an
 *  ordinary unbound-pin case, same as a single pin being dropped). Callers
 *  should warn the operator first if sensors are currently bound to this
 *  device — nothing on the backend does that on their behalf. */
export const deleteDaqDevice = (deviceId: string) =>
  request<{ ok: boolean }>(`/api/daq/devices/${encodeURIComponent(deviceId)}`, {
    method: 'DELETE',
  })

export const listExperiments = () => request<DaqExperiment[]>('/api/daq/experiments')
export const createExperiment = (name: string) =>
  post<DaqExperiment>('/api/daq/experiments', { name })
export const listStages = (experimentId: number) =>
  request<DaqStage[]>(`/api/daq/experiments/${experimentId}/stages`)

export const getHistory = (experimentId: number, params: Record<string, string> = {}) =>
  request<DaqHistory>(
    `/api/daq/history/experiment?${new URLSearchParams({
      experiment_id: String(experimentId),
      ...params,
    })}`,
  )

/** Full-resolution zoom, used when the replay window is small enough that
 *  LTTB downsampling would hide detail. */
export const getHistoryWindow = (experimentId: number, startMs: number, endMs: number) =>
  request<DaqHistory>(
    `/api/daq/history/experiment/window?${new URLSearchParams({
      experiment_id: String(experimentId),
      start_ms: String(startMs),
      end_ms: String(endMs),
    })}`,
  )

/** Readings within ONE named stage (e.g. just the "hold" phase) — not the
 *  stage list. For "what stages exist in this run", use listStages above;
 *  this was previously called with no `stage`, which 400s at DataAcquisition
 *  (it requires both `id` and `stage` — problems.txt's "id param required"
 *  was this same boundary, hit via a different missing/renamed param). */
export const getHistoryForStage = (experimentId: number, stage: string) =>
  request<DaqHistory>(
    `/api/daq/history/experiment/stage?${new URLSearchParams({
      experiment_id: String(experimentId),
      stage,
    })}`,
  )

/** Calibration provenance for a device's sensors, keyed by sensor_name.
 *  Per-device because that is the only conversion READ endpoint
 *  DataAcquisition exposes. */
export const getConversions = (deviceId: string) =>
  request<Record<string, DaqConversion>>(
    `/api/daq/conversions/${encodeURIComponent(deviceId)}`,
  )

/** This app's calibration editor: writes THROUGH to DataAcquisition's own
 *  conversion store (app/routes/daq_proxy.py::set_conversion) rather than
 *  keeping a second copy, so live view and historian never disagree about
 *  what a number means. Rejected (400) if `method` is "custom". */
export const setConversion = (deviceId: string, sensorName: string, body: SetConversionRequest) =>
  request<{ ok: boolean }>(
    `/api/daq/conversions/${encodeURIComponent(deviceId)}/${encodeURIComponent(sensorName)}`,
    { method: 'PUT', body: JSON.stringify(body) },
  )

export const deleteConversion = (deviceId: string, sensorName: string) =>
  request<{ deleted: true }>(
    `/api/daq/conversions/${encodeURIComponent(deviceId)}/${encodeURIComponent(sensorName)}`,
    { method: 'DELETE' },
  )

// --- Per-sensor danger thresholds ------------------------------------------

/** Sends the per-sensor threshold table over config/set (app.commands).
 *  The firmware owns the %->counts mapping AND the safety ceiling — this
 *  can only ask; read back Status.config_ack for what actually took effect. */
export const setThresholds = (thresholds: Array<{ sensor_key: string; thresholdPct: number }>) =>
  request<{ sent: Array<{ sensor: number; thresholdPct: number }> }>('/api/thresholds', {
    method: 'PUT',
    body: JSON.stringify({ thresholds }),
  })

export type { Ack }
