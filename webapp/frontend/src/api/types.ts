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
  /** Continuous clear-air time so far, against FULLY_VENT_MIN_HOLD_MS. */
  clearForMs?: number
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
  display_mode: boolean
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
  daq_sensor_name: string | null
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

export interface DaqStage {
  stage: string
  start_ms: number
  end_ms: number | null
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

export interface DaqConversion {
  conv_id: number | null
  formula?: string
  unit?: string
  updated_at?: string
}
