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

// =============================================================================
// RegisterSequencer — pure: maps (desired register set, now_ms) -> coil states,
// applying the inlet-open delay. Was its own file; folded in here because it is
// used only by KitchenCore's caller (Outputs) and the desktop tests, and has
// no independent life. Takes time as a parameter so tests drive it with a fake
// clock.
//
// Rules (see docs/implementation-plan.md "Ventilation registers"):
// - Delay applies to OPENING only. Closing is simultaneous, no delay.
// - The delay is a deferral, never a blocker — other registers actuate
//   immediately; only the inlet coil waits.
// - If the inlet is already open, or is opening alone with nothing else
//   changing, it actuates immediately.
// - A request that changes again before the deadline supersedes the pending
//   action rather than firing it late.
// =============================================================================

// CoilStates — what Outputs should actually drive right now, per register.
struct CoilStates {
  bool centralOpen = false, centralClose = false;
  bool exhaustOpen = false, exhaustClose = false;
  bool inletOpen   = false, inletClose   = false;
};

class RegisterSequencer {
public:
  RegisterSequencer() = default;

  // Call every drive() pass with the desired end-state register set and the
  // current time. Returns the coil states to actually write this pass.
  CoilStates step(const RegisterSet& desired, uint32_t nowMs);

private:
  RegisterSet appliedDesired_;     // last desired set this sequencer has seen
  bool        initialized_ = false;

  bool        inletPending_  = false;   // true while inlet-open is deferred
  uint32_t    inletDeadlineMs_ = 0;
  bool        inletCurrentlyOpen_ = false;  // last commanded inlet state (for "already open" check)
};

// =============================================================================
// PeerAlarmTable — pure: tracks each peer alarm ZONE separately, so one zone
// publishing "clear" cannot cancel another zone's active alarm.
//
// Peer alarms are the plan's PRIMARY lab-wide interlock (the permit is
// structurally inert outside a run), so a flat "any peer alarming" boolean is
// not enough: zones publish independently on retained topics, and a
// {danger:false} from zone B must not clear zone A.
//
// Rules:
// - A zone is keyed by its full MQTT topic. Rows are claimed on first sight.
// - A zone stays ACTIVE until THAT zone publishes clear. Silence never
//   clears an alarm — a peer that dies mid-alarm keeps the interlock held.
//   (Staleness is surfaced separately as a warn, per the plan.)
// - anyActive() is the OR across rows, and is what feeds SensorState.
// - OVERFLOW FAILS SAFE: if every row is claimed and an unknown zone reports
//   ACTIVE, anyActive() latches true rather than dropping the alarm. The
//   overflow latch clears only on reset(), since we cannot know when an
//   untracked zone stood down.
//
// Time is a parameter, so this is desktop-testable with a fake clock.
// =============================================================================
#define KITCHEN_PEER_TOPIC_MAXLEN  64

class PeerAlarmTable {
public:
  PeerAlarmTable() = default;

  // Record one peer alarm message. `topic` identifies the zone; `active` is
  // the parsed danger state. Returns false if the message could not be
  // tracked (table full) — the caller should log it; the interlock itself is
  // already held safe by the overflow latch.
  bool update(const char* topic, bool active, uint32_t nowMs);

  // True if ANY tracked zone is currently alarming, or the overflow latch is
  // set. This is what SensorState.peerAlarmActive carries into dangerActive().
  bool anyActive() const;

  // True if any CLAIMED zone has been silent for staleMs. Warn only — never
  // trips a danger condition (plan: "peer sensors going silent only warns").
  bool anyStale(uint32_t nowMs, uint32_t staleMs) const;

  // True once any zone has ever been seen (staleness is meaningless before).
  bool everSeen() const { return used_ > 0; }

  int  trackedZones() const { return used_; }
  bool overflowed() const { return overflow_; }

  void reset();

private:
  struct Zone {
    char     topic[KITCHEN_PEER_TOPIC_MAXLEN] = {0};
    bool     active     = false;
    uint32_t lastMsgMs  = 0;
  };
  Zone zones_[KITCHEN_MAX_PEER_ZONES];
  int  used_     = 0;
  bool overflow_ = false;   // an untracked zone reported active — fail safe
};

enum class KitchenState : uint8_t {
  WAITING,
  ARMED,
  // Sensors powered, waiting out SENSOR_WARMUP_MS before any gas flows. Gas is
  // hard-closed here by outputsFor(), so the warm-up gate is a STATE rather
  // than a boolean read inside LEAKING — an observer (LED, MQTT, operator)
  // can see "warming up" instead of a LEAKING that mysteriously delivers no
  // gas. confirm() skips straight to LEAKING when the sensors are already warm.
  WARMING_UP,
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
  bool         flowSetpointTest = false; // WAITING equip-test only: drive the
                                          // setpoint DAC even though gasOpen is
                                          // false (bench range check, relay cut)
  RegisterSet  registers;                // desired open/closed set
  float        fanSpeedPct    = 0.0f;    // 0-100%
  bool         alarmOn        = false;

  bool         localSensorsOn  = false;  // this PLC's own H2 sensors
  bool         remoteSensorsOn = false;  // commands remote CM7 DAQ instances

  // "Gas may be present" breathing lamp. STATE-based, not sensor-based: true
  // whenever the core is not in WAITING, so it stays lit through
  // VENTILATING/FULLY_VENTILATING until the core returns to WAITING. Outputs
  // turns this into a PWM breathe ramp on PIN_GAS_LAMP.
  bool         gasMayBePresent = false;

  bool operator==(const OutputRequest& o) const {
    return gasOpen == o.gasOpen && gasSetpointPct == o.gasSetpointPct &&
           flowSetpointTest == o.flowSetpointTest &&
           registers == o.registers && fanSpeedPct == o.fanSpeedPct &&
           alarmOn == o.alarmOn && localSensorsOn == o.localSensorsOn &&
           remoteSensorsOn == o.remoteSensorsOn &&
           gasMayBePresent == o.gasMayBePresent;
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

  bool     expansionUnhealthy = false; // A0602/D1608E missing or wrong type — set from
                                       // expansionHealthy() in Sensors.cpp. A missing
                                       // A0602 makes every H2 sensor read 0 counts, so
                                       // this is a danger condition, not just a warn.

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
  EXPANSION_FAULT,
  EXTERNAL_TRIP,

  // Reserved: an operator abort that requires a human ack to clear. Not
  // currently raised (stop() is routine/no-ack; a mid-leak selector flip is
  // inert). Kept as a stable wire value for the alarm payload.
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
  // LOCAL sensor power — on in LEAKING (leak-test) or whenever equipment-test.
  bool  sensorsOn() const { return localSensorsOn_; }
  // REMOTE (CM7 DAQ) sensor power — leak-test run only, never equipment-test.
  bool  remoteSensorsOn() const { return remoteOn(); }
  bool  warmupPending() const { return state_ == KitchenState::WARMING_UP; }

  bool         ackRequired() const { return ackRequired_; }
  bool         acked() const       { return acked_; }
  DangerReason reason() const      { return reason_; }

  // True when the role selector is in equipment-test but the core is NOT in
  // WAITING, so the flip is being ignored. Outputs uses it to blink the real
  // run-state LEDs; kitchen.ino logs the rising edge.
  bool         roleMisflip() const { return roleMisflip_; }

  // The instantaneous check state/output logic is built from. Exposed so
  // tests (and, if ever needed, diagnostics) can assert on it directly.
  static bool dangerActive(const SensorState& s, DangerReason& reasonOut);

  // Effective threshold = min(firmware_ceiling, requestedCounts). The website
  // can only make a sensor MORE sensitive, never less — and since counts rise
  // with concentration, "more sensitive" means a LOWER threshold, so the
  // firmware constant is a CEILING and this is a min(). Out-of-range or
  // missing values fall back to SENSOR_THRESHOLD_DEFAULT_COUNTS by the caller
  // (Protocol), not here — this function only enforces the ceiling.
  //
  // Applied at the point of comparison in dangerActive(), not just where the
  // value is stored, so no writer of LocalSensorReading::thresholdCounts can
  // weaken a trip point.
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
  // for SENSOR_WARMUP_MS (70 s), and the stop condition has already expired by
  // the time the gate releases. Delivered gas would be
  // (maxDurationMs - SENSOR_WARMUP_MS), floored at zero.
  uint32_t phaseClockFromMs_ = 0;

  float    deliveredInventory_mL_ = 0.0f;
  uint32_t lastIntegrationMs_     = 0;

  // Sensor power. LOCAL and REMOTE are NO LONGER lockstep (superseded plan
  // item 18): the local A0602/base H2 sensors run in LEAKING *and* in WAITING
  // equipment-test; the remote CM7 DAQ instances run only while a leak run is
  // live (LEAKING..purge). A leak run can only START in leak-test role, and a
  // mid-run selector flip is now inert, so remoteOn() gates on STATE alone.
  bool     localSensorsOn_  = false;
  // When localSensorsOn_ last went false->true, from ANY path (leak-run
  // confirm() or WAITING equipment-test). Warmth is a property of how long the
  // hardware has been powered, not of which code path powered it, so this is
  // the single source of truth for the gate — see sensorsAreWarm().
  uint32_t sensorsOnSinceMs_ = 0;
  uint32_t waitingIdleSinceMs_ = 0;
  bool     roleMisflip_      = false;   // equipment-test selected outside WAITING
  bool     equipTestActive_  = false;   // equipment-test AND in WAITING (bench mode)

  // True once the local H2 sensors have been powered for SENSOR_WARMUP_MS.
  // Sensors that are OFF are never warm — powering them down discards the
  // warm-up, so a run confirmed after an idle timeout gates again.
  bool sensorsAreWarm(uint32_t nowMs) const {
    return localSensorsOn_ && (nowMs - sensorsOnSinceMs_) >= SENSOR_WARMUP_MS;
  }

  bool remoteOn() const {
    // Remote DAQ sensors: only while a leak run is live (LEAKING through the
    // purge). State alone — see the note above.
    switch (state_) {
      // WARMING_UP is part of a live leak run: the remote DAQ has its own
      // REMOTE_SENSOR_WARMUP_MS, so it must come up alongside the local
      // sensors rather than at the instant gas starts flowing.
      case KitchenState::WARMING_UP:
      case KitchenState::LEAKING:
      case KitchenState::HOLD:
      case KitchenState::VENTILATING:
      case KitchenState::FULLY_VENTILATING:
        return localSensorsOn_;
      default:
        return false;
    }
  }

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
