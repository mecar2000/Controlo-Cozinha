/**
 * api/types — the wire shapes this frontend exchanges with the Flask backend.
 *
 * RunSpec mirrors kitchen/RunSpec.h: the closed set of primitives the website
 * may compose, and nothing else. Values here are the website's REQUEST —
 * the firmware validates and clamps, and the ack is what it actually agreed
 * to run. Both are stored; the diff between them is the forensic record.
 */

// ---------------------------------------------------------------------------
// Run spec — mirrors kitchen/RunSpec.h
// ---------------------------------------------------------------------------

/** The three ventilation registers. Any combination is legal. */
export interface RegisterSet {
  central: boolean
  exhaust: boolean
  inlet: boolean
}

/**
 * "Stop when N sensors read at or above X."
 *
 * UNITS: the website sends %; the firmware converts to counts at the MQTT
 * boundary and owns that mapping, because this condition cuts gas. The ack
 * echoes both, which is why the review screen shows both.
 */
export interface SensorQuorumStop {
  /** Percent %v/v as requested by the website. */
  thresholdPct: number
  /** Sensors required at or above the threshold. 0 = unused. */
  quorumCount: number
}

/** Stop conditions are OR'd: the phase ends on whichever fires first. */
export interface StopCondition {
  /** 0 = no duration cap. */
  maxDurationMs: number
  /** 0 = no inventory cap. LEAKING phase only — the firmware REJECTS this
   *  on holdStop or ventStop rather than silently ignoring it. */
  maxInventory_mL?: number
  sensorQuorum?: SensorQuorumStop
}

export interface RunSpec {
  /** 0-100%, clamped to the firmware ceiling. */
  gasSetpointPct: number
  leakStop: StopCondition
  /** Ventilation DURING the leak. Optional; omitted = sealed room (the
   *  pre-existing behaviour). Clamped to VENT_SPEED_MAX_PCT by firmware. */
  leakRegisters?: RegisterSet
  leakFanSpeedPct?: number
  /** Omitting this falls back to the firmware's HOLD_MAX_DURATION_MS ceiling,
   *  NOT to zero — absent means "the maximum safe hold", not "skip the
   *  measurement". */
  holdStop: StopCondition
  ventRegisters: RegisterSet
  /** 0-100%, clamped to VENT_SPEED_MAX_PCT. */
  fanSpeedPct: number
  ventStop: StopCondition
}

// ---------------------------------------------------------------------------
// Firmware state, as mirrored from the retained MQTT topics
// ---------------------------------------------------------------------------

export type KitchenPhase =
  | 'WAITING'
  | 'ARMED'
  /** Sensors powered, gas hard-closed, waiting out the 70 s sensor warm-up.
   *  Skipped entirely when the sensors are already warm at confirm(). */
  | 'WARMING_UP'
  | 'LEAKING'
  | 'HOLD'
  | 'VENTILATING'
  | 'FULLY_VENTILATING'

/** Mirrors the firmware's DangerReason enum, so "how many runs tripped on
 *  quorum this month" is a query rather than a text search. */
export type LatchCause =
  | 'LOCAL_SENSOR_THRESHOLD'
  | 'LOCAL_SENSOR_STALE'
  | 'FLOW_OVER_LIMIT'
  | 'INVENTORY_CAP_EXCEEDED'
  | 'PEER_ALARM'
  | 'PERMIT_DENIED'
  | 'ESTOP'
  | 'EXTERNAL_TRIP'
  | 'OPERATOR_ABORT'
  // External H2 sensors (base A6/A7): hydrogen outside the kitchen, at the
  // voltage-regulation stage, where there must never be any at all.
  | 'EXTERNAL_H2_THRESHOLD'
  | 'EXTERNAL_H2_SENSOR_FAULT'

export type SelectorRole = 'leak-test' | 'equipment-test'

/** The retained KitchenControl/{deviceId}/state payload, as the backend
 *  mirrors it. Fields are optional because the firmware is the authority on
 *  the payload shape and this app must degrade rather than crash. */
export interface KitchenState {
  phase?: KitchenPhase
  state?: KitchenPhase
  role?: SelectorRole
  elapsedMs?: number
  ackRequired?: boolean
  acked?: boolean
  /** Continuous clear-air time so far, against clearRequiredMs. */
  clearForMs?: number
  /** The hold this instance actually requires (sim/kitchen_core_sim.py's
   *  fully_vent_min_hold_ms, or the firmware's real FULLY_VENT_MIN_HOLD_MS)
   *  — read this rather than hardcoding 5 minutes, so a shortened test
   *  timing (problems.txt: "make fixed timings small for tests") is
   *  reflected on screen instead of showing a countdown against the wrong
   *  total. */
  clearRequiredMs?: number
  dangerReason?: LatchCause
  latchCause?: LatchCause
  reasonDetail?: string
  description?: string
  /** Live actuator readback. */
  fanSpeedPct?: number
  gasSetpointPct?: number
  registers?: RegisterSet
  deliveredInventory_mL?: number
  flowRate_mLps?: number
  localSensorsOn?: boolean
  remoteSensorsOn?: boolean
}

export interface PermitStatus {
  /** null = never seen. Absence warns; it never trips. */
  ok: boolean | null
  last_seen_age_s: number | null
}

export interface PeerAlarmDetail {
  experiment_name?: string
  device_id?: string
  lab_id?: string
  description?: string
}

export interface PeerAlarmZone {
  active: boolean
  detail: PeerAlarmDetail
  received_at: number
}

export interface PeerAlarm {
  /** OR across all tracked zones. */
  active: boolean
  /** One currently-active zone, for the single-line banner. Null when clear. */
  detail: PeerAlarmDetail | null
  /** How many zones are alarming right now. */
  active_count: number
  /** Every zone seen, keyed "experiment/device/lab" — including cleared ones.
   *  Zones are tracked independently: one zone clearing never cancels another. */
  zones: Record<string, PeerAlarmZone>
}

export interface Status {
  mqtt: {
    connected: boolean
    last_message_age_s: number | null
    /** Past this, the live view must be flagged stale and must never show
     *  last-known values as current. */
    stale: boolean
  }
  daq: {
    reachable: boolean | null
    recording_lost: boolean
  }
  permit: PermitStatus
  peer_alarm: PeerAlarm
  kitchen_state: KitchenState
  /** How old `kitchen_state` already was when the server built the response,
   *  in seconds. Null when no state payload has ever arrived. Time-shaped
   *  fields inside kitchen_state (elapsedMs, clearForMs) are frozen between
   *  the publisher's heartbeats, so interpolating them locally must start
   *  from this age — not from when the response was received. Sent as an age
   *  rather than a timestamp so no client/server clock offset is involved. */
  kitchen_state_age_s: number | null
  display_mode: boolean
  /** The firmware's echo of the last config/set (per-sensor threshold
   *  table) it accepted. Empty until a threshold has ever been sent. */
  config_ack: ConfigAck
}

export interface LiveReading {
  value: number
  unit: string
  ts_ms: number
  /** False when DataAcquisition had no calibration for this sensor and
   *  `value` is a RAW hardware reading (mA), not a concentration. Such a
   *  reading must never be plotted as %v/v — see useKitchen.ts. */
  converted?: boolean
  received_at: number
}

export type LiveReadings = Record<string, LiveReading>

// ---------------------------------------------------------------------------
// Persisted records
// ---------------------------------------------------------------------------

export type RunOutcome =
  | 'pending'
  | 'completed'
  | 'stopped'
  | 'latched'
  | 'rejected'
  | 'aborted'
  | 'expired'

export interface Run {
  id: number
  run_number: number
  /** The runId sent on the MQTT cmd topic. The server verifies confirm()
   *  against this, so it is also what a reloaded page reads to resume a
   *  pending confirmation rather than inventing one. */
  wire_run_id: string | null
  name: string
  config_id: number | null
  config_snapshot: Record<string, unknown>
  daq_experiment_id: number | null
  daq_experiment_name: string | null
  requested_spec: RunSpec
  /** What the firmware agreed to run. Null until the ack arrives. */
  acked_spec: RunSpec | null
  started_at: string
  confirmed_at: string | null
  ended_at: string | null
  outcome: RunOutcome
  outcome_detail: string | null
  latch_cause: LatchCause | null
  recorded: boolean
  operator: string | null
}

export interface RunConfig {
  id: number
  name: string
  spec: RunSpec
  created_at: string
  updated_at: string
  archived: boolean
}

export interface Sensor {
  id: number
  sensor_key: string
  label: string
  /** Metres, against the frame documented in lib/roomGeometry.ts. */
  x: number
  y: number
  z: number
  enabled: boolean
  daq_device_id: string | null
  /** DERIVED from daq_pin server-side (routes/sensors.py), never accepted
   *  as typed input — see the DaqDevice section below for why. */
  daq_sensor_name: string | null
  /** The encoded DataAcquisition pin (app.daq.pin_label's input) this
   *  sensor is bound to (Part 5). Setting this is how a sensor's DAQ
   *  identity is chosen; daq_sensor_name follows automatically. */
  daq_pin: number | null
  /** 0-5, which firmware channel this sensor's danger threshold goes to.
   *  Null falls back to daq_sensor_name matching "H2-N" — see
   *  routes/thresholds.py. Lets a sensor named anything still receive a
   *  threshold, once wired to a real firmware channel. */
  firmware_index: number | null
  updated_at: string
}

export interface LayoutForRun {
  layout: Sensor[]
  /** True when the run predates snapshotting and replay is drawing against
   *  today's layout. Must be surfaced, never silently applied. */
  is_current_fallback: boolean
}

// ---------------------------------------------------------------------------
// Two-phase start
// ---------------------------------------------------------------------------

/** The firmware's echo of a start(spec): what it will actually run, or why
 *  it refused. */
export interface Ack {
  runId?: string
  valid?: boolean
  accepted?: boolean
  reason?: string
  rejectReason?: string
  rejection?: string
  spec?: RunSpec
  /** The quorum threshold as the firmware interpreted it. Shown alongside
   *  the requested % so a miscalibration is visible from the browser. */
  interpretedQuorumCounts?: Record<string, number>
}

export interface StartRunResponse {
  run: Run
  ack: Ack | null
  rejected: boolean
}

export interface StartRunRequest {
  config_id?: number | null
  spec?: RunSpec
  experiment_id?: number | null
  experiment_name?: string
  run_name: string
  unrecorded_test_run?: boolean
  operator?: string
  /** "reject" (default) refuses a name already used by an earlier run;
   *  "suffix" appends the lowest free "-2", "-3", ... instead — see
   *  app.runs.start_run. */
  on_name_conflict?: 'reject' | 'suffix'
}

// ---------------------------------------------------------------------------
// DataAcquisition, proxied through this backend (the browser never talks to
// DataAcquisition directly)
// ---------------------------------------------------------------------------

export interface DaqExperiment {
  id: number
  name: string
  created_at?: string
}

/** One entry in a device's `config.sensors` list, as DataAcquisition's
 *  GET /devices actually returns it (dashboard/db/devices.py:
 *  get_all_devices). `pin` is the raw encoded integer — see
 *  src/lib/pinLabel.ts for the display encoding. */
export interface DaqDeviceSensor {
  pin: number
  name: string
  type: string
}

/** One device DataAcquisition has seen (Part 5: PLC/sensor commissioning
 *  wizard, Stage 1) — dashboard/app/routes/core.py::list_devices /
 *  app.daq.list_devices. `expansions` and `base_pins` describe reported
 *  hardware capability (which pins can legally be configured); `config` is
 *  the device's current pin map, the same list POST /config replaces
 *  wholesale. */
export interface DaqDevice {
  device_id: string
  location: string | null
  status: string
  expansions: number
  base_pins: number
  interval_ms: number
  config: { sensors: DaqDeviceSensor[] }
}

/** GET /api/meta (routes/meta.py) — settings this backend owns that the
 *  frontend needs to read rather than hardcode a second copy of.
 *  kitchen_daq_device_id is the id the kitchen PLC's own H2 sensors publish
 *  under in DataAcquisition (app.config.KITCHEN_DAQ_DEVICE_ID, "mainBoard"
 *  by default) — distinct from kitchen_device_id, which names the
 *  KitchenControl/{id}/... control topics (app.config.KITCHEN_DEVICE_ID,
 *  "KITCHEN-01" by default). Two separate namespaces; do not conflate. */
export interface Meta {
  kitchen_daq_device_id: string
  kitchen_device_id: string
  /** DataAcquisition's own dashboard base URL (DAQ_BASE_URL — its dashboard
   *  UI and REST API are the same Flask app on the same port) — used to
   *  link out to its own calibration editor rather than duplicating one
   *  here. See routes/meta.py. */
  daq_dashboard_url: string
}

/** One stage label present in an experiment's readings, as DataAcquisition's
 *  GET /experiments/<id>/stages actually returns it (dashboard/app/routes/
 *  experiments.py:get_experiment_stages / db/experiments.py:
 *  get_stage_counts_for_experiment). Note there is no per-stage END time —
 *  DataAcquisition tracks only each stage's first reading (started_at), so a
 *  stage's extent on the replay timeline can only be inferred as "from this
 *  stage's started_at until the next stage's started_at (or run end)". A
 *  previous version of this type claimed start_ms/end_ms, which no
 *  DataAcquisition endpoint has ever produced. */
export interface DaqStage {
  stage: string
  count: number
  started_at: string | null
}

export interface DaqSeries {
  device_id: string
  sensor_name: string
  /** Converted value in `unit`. */
  points: Array<{ t_ms: number; value: number; raw_value?: number }>
  unit: string
  raw_unit?: string
  conv_id?: number | null
}

export interface DaqHistory {
  series: DaqSeries[]
  start_ms?: number
  end_ms?: number
}

/** Calibration method this app's own applier (app.conversion) supports.
 *  "custom" deliberately excluded — see app/routes/daq_proxy.py. */
export type ConversionMethod = 'raw' | 'linear' | 'ax_b'

export interface DaqConversion {
  conv_id: number | null
  conversion_type?: string
  method?: ConversionMethod | string
  params?: Record<string, number | string>
  unit_symbol?: string
  formula?: string
  updated_at?: string
}

/** Body for PUT /api/daq/conversions/{device}/{sensor} — this app's
 *  calibration editor, writing through to DataAcquisition's store. */
export interface SetConversionRequest {
  type?: string
  method: ConversionMethod
  params: Record<string, number>
  unit_symbol: string
}

/** The firmware's echo of the last config/set it accepted
 *  (kitchen/Protocol.cpp protocolBuildConfigAck), mirrored into /api/status. */
export interface ConfigAckEntry {
  sensor: number
  requestedPct: number
  requestedCounts: number
  effectiveCounts: number
  effectivePct: number
  /** True when the firmware's safety ceiling refused to trip as late as
   *  requested — the operator must see this, not assume their value took
   *  effect. */
  clamped: boolean
}

export interface ConfigAck {
  valid?: boolean
  rejection?: string
  thresholds?: ConfigAckEntry[]
}

/** app.zeroing's session status — see routes/sensor_zero.py. */
export interface ZeroingStatus {
  sensor_key: string
  target: number
  collected: number
  done: boolean
}

export interface ZeroingResult {
  sensor_key: string
  previous_raw_min: number
  new_raw_min: number
  sample_count: number
}

/** app.zeroing's batch status — see routes/sensor_zero.py's zero-all
 *  endpoints. `current` mirrors ZeroingStatus for whichever sensor is
 *  capturing right now; null once `done`. */
export interface BatchZeroingStatus {
  total: number
  index: number
  done: boolean
  current: ZeroingStatus | null
  results: ZeroingResult[]
  failures: Array<{ sensor_key: string; error: string }>
}
