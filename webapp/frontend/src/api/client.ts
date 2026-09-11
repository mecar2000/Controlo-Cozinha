/**
 * api/client — every call this frontend makes to the Flask backend.
 *
 * The browser never talks to DataAcquisition directly: the DAQ calls below
 * go to this app's own /api/daq/* proxy routes, which hold the DAQ token
 * server-side. One place decides authorization for a hydrogen command.
 */

import type {
  Ack,
  DaqConversion,
  DaqExperiment,
  DaqHistory,
  DaqStage,
  LayoutForRun,
  LiveReadings,
  Run,
  RunConfig,
  RunSpec,
  Sensor,
  StartRunRequest,
  StartRunResponse,
  Status,
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

export const listSensors = (enabledOnly = false) =>
  request<Sensor[]>(`/api/sensors${enabledOnly ? '?enabled_only=1' : ''}`)

export const upsertSensor = (
  sensorKey: string,
  body: {
    label: string
    x: number
    y: number
    z: number
    enabled?: boolean
    daq_device_id?: string | null
    daq_sensor_name?: string | null
  },
) =>
  request<Sensor>(`/api/sensors/${encodeURIComponent(sensorKey)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })

export const deleteSensor = (sensorKey: string) =>
  request<{ deleted: true }>(`/api/sensors/${encodeURIComponent(sensorKey)}`, {
    method: 'DELETE',
  })

export const getLayoutForRun = (runId: number) =>
  request<LayoutForRun>(`/api/runs/${runId}/layout`)

// --- DataAcquisition, proxied ---------------------------------------------

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

export const getHistoryStages = (experimentId: number) =>
  request<DaqStage[]>(`/api/daq/history/experiment/stage?experiment_id=${experimentId}`)

/** Calibration provenance for a device's sensors, keyed by sensor_name.
 *  Per-device because that is the only conversion READ endpoint
 *  DataAcquisition exposes. */
export const getConversions = (deviceId: string) =>
  request<Record<string, DaqConversion>>(
    `/api/daq/conversions/${encodeURIComponent(deviceId)}`,
  )

export type { Ack }
