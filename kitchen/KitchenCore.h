#pragma once

// =============================================================================
// KitchenCore.h — the whole control path. One state machine that reads
// SensorState and produces an OutputRequest. There is no second layer that
// rewrites the request: danger is a TRANSITION, evaluated first, every pass.
//
//     OutputRequest out = core.update(sensors, nowMs);   // the audit target
//     outputs.drive(out, nowMs);                          // the ONLY pin writer
//
// AUDIT TARGET: dangerActive() plus the FULLY_VENTILATING branch of
// outputsFor() (folded into update() below) are the entire safety-relevant
// surface. dangerActive() never reads spec_; the FULLY_VENTILATING output
// block never reads spec_. That is what makes "nothing the website sends can
// open the gas valve when the safety layer says no" a two-function claim.
//
// Invariants enforced by review, not by the compiler:
//   - Outputs::drive() is the only place that calls digitalWrite/analogWrite
//     on control pins.
//   - dangerActive() is the only place danger conditions are evaluated.
//   - The FULLY_VENTILATING output block never reads spec_.
//
// No Arduino headers here — desktop-testable with g++.
// =============================================================================

#include <stdint.h>
#include "RunSpec.h"
#include "Kitchen_Settings.h"

enum class KitchenState : uint8_t {
  WAITING,
  ARMED,
  LEAKING,
  HOLD,
  VENTILATING,
  FULLY_VENTILATING,
};

// -----------------------------------------------------------------------------
// OutputRequest — a desired END STATE, not a sequence of actions. Says WHICH
// registers should be open, never in what order they get there. Actuation
// ordering (inlet delay) belongs to RegisterSequencer/Outputs, so the ordering
// applies identically to danger-forced transitions and to spec-driven ones.
// -----------------------------------------------------------------------------
struct OutputRequest {
  bool         gasOpen        = false;   // gas relay: true = open (flowing)
  float        gasSetpointPct = 0.0f;    // flowmeter setpoint, 0-100%
  RegisterSet  registers;                // desired open/closed set
  float        fanSpeedPct    = 0.0f;    // 0-100%
  bool         alarmOn        = false;

  bool         localSensorsOn  = false;  // this PLC's own H2 sensors
  bool         remoteSensorsOn = false;  // commands remote CM7 DAQ instances

  bool operator==(const OutputRequest& o) const {
    return gasOpen == o.gasOpen && gasSetpointPct == o.gasSetpointPct &&
           registers == o.registers && fanSpeedPct == o.fanSpeedPct &&
           alarmOn == o.alarmOn && localSensorsOn == o.localSensorsOn &&
           remoteSensorsOn == o.remoteSensorsOn;
  }
};

// -----------------------------------------------------------------------------
// One local H2 sensor reading + its (possibly website-raised) threshold.
// -----------------------------------------------------------------------------
struct LocalSensorReading {
  bool     present       = false;   // false = slot unused / not wired
  uint16_t counts         = 0;       // raw ADC counts
  uint16_t thresholdCounts = SENSOR_THRESHOLD_DEFAULT_COUNTS;  // pre-clamp, website value
  bool     stale          = false;   // true if no fresh reading within LOCAL_SENSOR_STALE_MS
  bool     expectedOn     = false;   // sensor is supposed to be powered — staleness only trips if true
};

// -----------------------------------------------------------------------------
// SensorState — everything update() needs to decide. Built each pass by
// Sensors.cpp (hardware) or directly by tests (fake data).
// -----------------------------------------------------------------------------
struct SensorState {
  LocalSensorReading localSensors[KITCHEN_MAX_LOCAL_SENSORS];
  int                localSensorCount = 0;

  uint16_t flowCounts            = 0;      // raw flow feedback ADC
  bool     flowOverLimitSustained = false; // Sensors.cpp tracks the >2s sustain window
  float    deliveredInventory_mL = 0.0f;   // integrated by KitchenCore, read here for the cap check

  bool     peerAlarmActive = false;   // any peer PLC alarm currently active (self excluded)
  bool     peerAlarmStale  = false;   // peer alarms went silent — warns, does not trip

  bool     permitPresent = false;     // safety/permit/{deviceId} has been received at all
  bool     permitValue   = false;     // last received permit value

  bool     estopPressed = false;      // physical e-stop, A3 — works with no network

  bool     externalTripActive = false; // reserved for future hardwired trip input (not implemented)

  bool     isLeakTestRole = true;     // role selector: true = leak-test, false = equipment-test
};

// -----------------------------------------------------------------------------
// Why FULLY_VENTILATING is (or was) forced — carried through to the alarm
// payload's reason/source field so an e-stop, sensor trip, and peer alarm are
// distinguishable in the log rather than all reading as "alarm".
// -----------------------------------------------------------------------------
enum class DangerReason : uint8_t {
  NONE = 0,
  LOCAL_SENSOR_THRESHOLD,
  LOCAL_SENSOR_STALE,
  FLOW_OVER_LIMIT,
  INVENTORY_CAP_EXCEEDED,
  PEER_ALARM,
  PERMIT_DENIED,
  ESTOP,
  EXTERNAL_TRIP,

  // Not a dangerActive() condition: the role selector was physically flipped
  // away from leak-test while hydrogen was flowing. There is no ongoing
  // condition to clear — only a human to acknowledge — so it is latched
  // directly by the LEAKING branch rather than detected by dangerActive().
  OPERATOR_ABORT,
};

// -----------------------------------------------------------------------------
// Rejection reasons for a `start`/`confirm` command — Protocol turns these
// into the `ack` payload's rejection reason string.
// -----------------------------------------------------------------------------
enum class StartRejectReason : uint8_t {
  NONE = 0,
  WRONG_STATE,           // not in WAITING
  WRONG_ROLE,             // selector is equipment-test, leak run requested
  INVALID_SPEC,           // Protocol's validate/clamp marked spec.valid = false
  RUN_ID_MISMATCH,        // confirm() runId doesn't match the armed spec
  ARM_TIMED_OUT,          // confirm() arrived after ARM_TIMEOUT_MS
};

// -----------------------------------------------------------------------------
// KitchenCore — the whole control path in one class.
// -----------------------------------------------------------------------------
class KitchenCore {
public:
  KitchenCore() = default;

  // Call once per loop pass. Evaluates danger FIRST (a transition, not a
  // rewrite), then advances whatever state that leaves us in, and returns
  // what should be driven to the pins this pass. State and outputs always
  // agree on the same pass — there is no cross-pass skew to reason about.
  OutputRequest update(const SensorState& s, uint32_t nowMs);

  // Commands from Protocol (already parsed/validated at the JSON layer).
  StartRejectReason start(const RunSpec& spec, const SensorState& s, uint32_t nowMs);
  StartRejectReason confirm(const char* runId, uint32_t nowMs);
  void              stop(uint32_t nowMs);         // abort current run into a purge
  void              humanAck(uint32_t nowMs);     // USER button or MQTT ack

  KitchenState state() const { return state_; }
  const RunSpec& armedSpec() const { return spec_; }
  float deliveredInventory_mL() const { return deliveredInventory_mL_; }
  bool  sensorsOn() const { return sensorsOn_; }
  bool  warmupPending() const { return warmupPending_; }

  bool         ackRequired() const { return ackRequired_; }
  bool         acked() const       { return acked_; }
  DangerReason reason() const      { return reason_; }

  // The instantaneous check state/output logic is built from. Exposed so
  // tests (and, if ever needed, diagnostics) can assert on it directly.
  static bool dangerActive(const SensorState& s, DangerReason& reasonOut);

  // Effective threshold = max(firmware_minimum, requestedCounts). The website
  // can only make a sensor MORE sensitive, never less. Out-of-range or
  // missing values fall back to SENSOR_THRESHOLD_DEFAULT_COUNTS by the caller
  // (Protocol), not here — this function only enforces the floor.
  static uint16_t clampThreshold(uint16_t requestedCounts);

  // Effective fan/gas setpoint clamp — sensitivity/authority ceilings the
  // website cannot exceed regardless of what a run spec asks for.
  static float clampFanSpeedPct(float requestedPct);

private:
  KitchenState state_ = KitchenState::WAITING;
  RunSpec      spec_;

  uint32_t armedAtMs_        = 0;
  uint32_t stateEnteredAtMs_ = 0;

  // When the CURRENT phase's stop-condition clock started. Normally equal to
  // stateEnteredAtMs_, but LEAKING re-bases it to the instant the sensor
  // warm-up gate releases — i.e. when gas actually starts flowing.
  //
  // Without this, a spec asking to leak for 5 s delivers ZERO gas: the phase
  // clock would start at LEAKING entry, the warm-up gate holds the valve shut
  // for SENSOR_WARMUP_MS (30 s), and the stop condition has already expired by
  // the time the gate releases. Delivered gas would be
  // (maxDurationMs - SENSOR_WARMUP_MS), floored at zero.
  uint32_t phaseClockFromMs_ = 0;

  float    deliveredInventory_mL_ = 0.0f;
  uint32_t lastIntegrationMs_     = 0;

  // Sensor power (local + remote move in lockstep — see plan, item 18).
  bool     sensorsOn_        = false;
  bool     warmupPending_    = false;
  uint32_t warmupStartedMs_  = 0;
  uint32_t waitingIdleSinceMs_ = 0;

  // -------------------------------------------------------------------------
  // FULLY_VENTILATING exit state — the entire replacement for the old
  // SafetyLatch object. One state, three fields, no second module to keep in
  // sync with this one:
  //   ackRequired_ - set true at entry by a danger condition or an operator
  //                  abort; false for a routine purge. PROMOTES, never
  //                  DEMOTES: a danger firing mid-routine-purge sets it true
  //                  and nothing but leaving the state sets it back.
  //   acked_       - latched true by humanAck(). Re-entering FULLY_VENTILATING
  //                  while already there does NOT reset this — otherwise a
  //                  flickering sensor would discard an ack already given.
  //   reason_      - which condition (most recently) caused entry, carried
  //                  into the alarm payload.
  // clearSinceMs_ tracks how long the danger condition has been continuously
  // absent; it resets to 0 (not-clear) on every pass a condition is active,
  // so "5 min clear" means five continuous minutes.
  // -------------------------------------------------------------------------
  bool         ackRequired_   = false;
  bool         acked_         = false;
  DangerReason reason_        = DangerReason::NONE;
  uint32_t     clearSinceMs_  = 0;     // 0 = not currently clear

  void enterState(KitchenState next, uint32_t nowMs);
  void enterFullyVentilating(DangerReason reason, bool requiresAck, uint32_t nowMs);
  void integrateInventory(const SensorState& s, uint32_t nowMs);
  bool selectorIsLeakTest(const SensorState& s) const { return s.isLeakTestRole; }
  bool canLeaveFullyVentilating(uint32_t nowMs) const;

  // Evaluate a phase's stop condition (duration OR inventory OR quorum,
  // whichever fires first). Split out so LEAKING, HOLD and VENTILATING all
  // evaluate stop conditions identically instead of each rolling their own.
  bool stopConditionMet(const StopCondition& sc, const SensorState& s,
                        uint32_t nowMs) const;

  // "N sensors at-or-above threshold." Absent AND stale sensors are excluded
  // from the count — silence is not evidence of low concentration. (A stale
  // sensor that is expectedOn is separately a danger condition.)
  static bool quorumMet(const SensorQuorumStop& q, const SensorState& s);

  OutputRequest outputsFor(uint32_t nowMs) const;
};
