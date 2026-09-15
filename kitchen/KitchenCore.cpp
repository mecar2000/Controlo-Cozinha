#include "KitchenCore.h"
#include <string.h>

// =============================================================================
// KitchenCore.cpp — see KitchenCore.h for the contract. Every danger
// condition added here also needs: a row in the plan's danger-condition
// table, a test in test/test_danger.cpp, and — if it's a genuinely new hazard
// class — a DangerReason enumerator. Every state transition should have a
// matching test in test/test_states.cpp; every sensor-power rule should map to
// one of the numbered cases in the plan's "DAQ sensor power — dedicated test
// list" and be pinned in test/test_sensors.cpp.
// =============================================================================

static bool runIdEquals(const char* a, const char* b) {
  return strncmp(a, b, sizeof(RunSpec::runId)) == 0;
}

// ---------------------------------------------------------------------------
// dangerActive() — the ONLY place danger conditions are evaluated. Never
// reads spec_: it is a static function of SensorState alone, which is what
// keeps the website out of the decision entirely.
// ---------------------------------------------------------------------------
bool KitchenCore::dangerActive(const SensorState& s, DangerReason& reasonOut) {
  for (int i = 0; i < s.localSensorCount; i++) {
    const LocalSensorReading& r = s.localSensors[i];
    if (!r.present) continue;
    // AT-OR-ABOVE (>=): a reading exactly at its threshold TRIPS. Same
    // convention as SensorQuorumStop — see the boundary note in
    // Kitchen_Settings.h. These two must not drift apart.
    //
    // The clamp is applied HERE, at the comparison, not merely where the
    // website's value is stored. thresholdCounts is a website-supplied field;
    // clamping at the point of use means no present or future writer of it
    // can weaken the trip point, even by mistake.
    if (r.counts >= clampThreshold(r.thresholdCounts)) {
      reasonOut = DangerReason::LOCAL_SENSOR_THRESHOLD;
      return true;
    }
  }
  for (int i = 0; i < s.localSensorCount; i++) {
    const LocalSensorReading& r = s.localSensors[i];
    if (!r.present || !r.expectedOn) continue;   // stale-while-intentionally-off is expected
    if (r.stale) {
      reasonOut = DangerReason::LOCAL_SENSOR_STALE;
      return true;
    }
  }
  if (s.isLeakTestRole && s.flowOverLimitSustained) {
    reasonOut = DangerReason::FLOW_OVER_LIMIT;
    return true;
  }
  if (s.isLeakTestRole && s.deliveredInventory_mL > INVENTORY_CAP_ML) {
    reasonOut = DangerReason::INVENTORY_CAP_EXCEEDED;
    return true;
  }
  if (s.peerAlarmActive) {
    reasonOut = DangerReason::PEER_ALARM;
    return true;
  }
  // Permit ABSENCE warns, does not trip — only an explicit false trips.
  if (s.permitPresent && !s.permitValue) {
    reasonOut = DangerReason::PERMIT_DENIED;
    return true;
  }
  if (s.estopPressed) {
    reasonOut = DangerReason::ESTOP;
    return true;
  }
  // A missing/unpowered A0602 makes all 6 H2 current sensors read 0 counts —
  // indistinguishable from "0% H2" — so the threshold and quorum checks above
  // are blind. Treat expansion loss itself as danger: gas off, full purge,
  // ack required. Sensors.cpp sets this from expansionHealthy().
  if (s.expansionUnhealthy) {
    reasonOut = DangerReason::EXPANSION_FAULT;
    return true;
  }
  if (s.externalTripActive) {
    reasonOut = DangerReason::EXTERNAL_TRIP;
    return true;
  }
  reasonOut = DangerReason::NONE;
  return false;
}

// ---------------------------------------------------------------------------
uint16_t KitchenCore::clampThreshold(uint16_t requestedCounts) {
  // min(), not max(): counts rise with concentration, so a LOWER threshold is
  // a MORE sensitive sensor. The website may only lower it.
  uint16_t maxC = SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS;
  return requestedCounts < maxC ? requestedCounts : maxC;
}

float KitchenCore::clampFanSpeedPct(float requestedPct) {
  if (requestedPct < 0.0f) return 0.0f;
  if (requestedPct > VENT_SPEED_MAX_PCT) return VENT_SPEED_MAX_PCT;
  return requestedPct;
}

// ---------------------------------------------------------------------------
void KitchenCore::enterState(KitchenState next, uint32_t nowMs) {
  state_            = next;
  stateEnteredAtMs_ = nowMs;
  // Phases time from entry by default; LEAKING re-bases this when its
  // warm-up gate releases, so the leak duration measures GAS FLOWING rather
  // than time-since-entry. See phaseClockFromMs_ in the header.
  phaseClockFromMs_ = nowMs;

  if (next == KitchenState::WAITING) {
    waitingIdleSinceMs_ = nowMs;   // idle timer restarts fresh on every WAITING entry
  }
}

// Entry point for EVERY route into FULLY_VENTILATING: danger, operator abort,
// routine purge/proof/stop. requiresAck PROMOTES but never DEMOTES ackRequired_,
// and acked_ is only ever cleared on the way OUT (canLeaveFullyVentilating),
// so re-entering while already latched cannot discard an ack already given.
void KitchenCore::enterFullyVentilating(DangerReason reason, bool requiresAck, uint32_t nowMs) {
  bool wasAlready = (state_ == KitchenState::FULLY_VENTILATING);
  if (!wasAlready) {
    enterState(KitchenState::FULLY_VENTILATING, nowMs);
    ackRequired_  = requiresAck;
    acked_        = false;
    // A requiresAck entry (danger, operator abort) has an ongoing condition
    // THIS pass, so the all-clear clock is not running yet — the next
    // update() with the condition gone starts it, same as any other latched
    // episode. A routine entry (end-of-run purge, clean-air proof, operator
    // stop()) has no such condition — it is clear from the instant it
    // enters, so the clock starts NOW. Without this, a routine purge's hold
    // was measured from the first update() call AFTER entry rather than from
    // entry itself, one call short of the real FULLY_VENT_MIN_HOLD_MS.
    clearSinceMs_ = requiresAck ? 0 : (nowMs == 0 ? 1 : nowMs);
  } else if (requiresAck && !ackRequired_) {
    // Promote a routine purge in progress to "needs ack" — a danger firing
    // mid-purge must not let the purge complete unacknowledged, and it has
    // an ongoing condition this pass, so the clock resets to not-clear.
    ackRequired_  = true;
    acked_        = false;
    clearSinceMs_ = 0;
  }
  reason_ = reason;
}

// ---------------------------------------------------------------------------
bool KitchenCore::quorumMet(const SensorQuorumStop& q, const SensorState& s) {
  if (q.quorumCount == 0) return false;   // unused

  uint8_t n = 0;
  for (int i = 0; i < s.localSensorCount; i++) {
    const LocalSensorReading& r = s.localSensors[i];
    if (!r.present) continue;
    if (r.stale)   continue;   // silence is not evidence of low concentration
    // AT-OR-ABOVE, the same boundary convention as dangerActive(). NOT
    // clamped, and deliberately so: this threshold ends a PHASE, it does not
    // cut gas, and a lower value only ends the phase sooner. A quorum
    // threshold set above the danger ceiling is simply unreachable — the
    // danger check trips first, from any state, on a single sensor.
    if (r.counts >= q.thresholdCounts) n++;
  }
  return n >= q.quorumCount;
}

// ---------------------------------------------------------------------------
bool KitchenCore::stopConditionMet(const StopCondition& sc, const SensorState& s,
                                    uint32_t nowMs) const {
  // OR'd: the phase ends on whichever fires first.
  // Timed from phaseClockFromMs_, not stateEnteredAtMs_ — see the header. For
  // HOLD/VENTILATING these are identical; for LEAKING the clock starts when
  // the warm-up gate releases and gas actually begins to flow.
  if (sc.maxDurationMs > 0 && (nowMs - phaseClockFromMs_) >= sc.maxDurationMs) {
    return true;
  }
  // Inventory is only meaningful while gas is flowing; Protocol rejects a
  // spec setting it on holdStop/ventStop, so this is LEAKING in practice.
  if (sc.maxInventory_mL > 0.0f && deliveredInventory_mL_ >= sc.maxInventory_mL) {
    return true;
  }
  if (quorumMet(sc.sensorQuorum, s)) {
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
void KitchenCore::integrateInventory(const SensorState& s, uint32_t nowMs) {
  if (lastIntegrationMs_ == 0) {
    lastIntegrationMs_ = nowMs;
    return;
  }
  uint32_t dtMs = nowMs - lastIntegrationMs_;
  lastIntegrationMs_ = nowMs;
  if (dtMs == 0) return;

  // Flow feedback is a 0-10 V signal on BASE board A1 (not a 4-20 mA loop —
  // see the two-independent-scales note in Kitchen_Settings.h). Convert
  // counts -> volts -> mL/s.
  //
  // This previously treated raw counts AS mL/s, which made INVENTORY_CAP_ML
  // meaningless: at ~3000 counts against a 5000 mL cap it tripped after
  // roughly two seconds of any flow at all, aborting every run.
  //
  // Both constants are BLOCKING BEFORE HYDROGEN — the cap cannot trip
  // meaningfully until they are measured.
  float volts    = (float)s.flowCounts / FLOW_COUNTS_PER_VOLT;
  float mlPerSec = volts * (FLOW_ML_PER_SEC_AT_10V / 10.0f);
  deliveredInventory_mL_ += mlPerSec * (dtMs / 1000.0f);
  flowRate_mLps_ = mlPerSec;
}

// ---------------------------------------------------------------------------
StartRejectReason KitchenCore::start(const RunSpec& spec, const SensorState& s, uint32_t nowMs) {
  if (state_ != KitchenState::WAITING) return StartRejectReason::WRONG_STATE;
  if (!spec.valid) return StartRejectReason::INVALID_SPEC;
  if (!selectorIsLeakTest(s)) return StartRejectReason::WRONG_ROLE;

  spec_     = spec;
  armedAtMs_ = nowMs;
  enterState(KitchenState::ARMED, nowMs);
  return StartRejectReason::NONE;
}

StartRejectReason KitchenCore::confirm(const char* runId, uint32_t nowMs) {
  if (state_ != KitchenState::ARMED) return StartRejectReason::WRONG_STATE;
  if (nowMs - armedAtMs_ > ARM_TIMEOUT_MS) {
    enterState(KitchenState::WAITING, nowMs);
    return StartRejectReason::ARM_TIMED_OUT;
  }
  if (!runIdEquals(spec_.runId, runId)) return StartRejectReason::RUN_ID_MISMATCH;

  deliveredInventory_mL_ = 0.0f;
  lastIntegrationMs_     = 0;

  if (!localSensorsOn_) {
    localSensorsOn_    = true;
    sensorsOnSinceMs_  = nowMs;
  }
  // Gate on ELAPSED POWERED TIME, never on "did this function turn them on".
  // Sensors carried over already-warm (a previous run, or WAITING
  // equipment-test) skip straight to LEAKING — plan item 7, no second gate.
  // But sensors that were only just switched on by equipment-test are NOT warm
  // yet, and that case previously slipped through the old `if (!localSensorsOn_)`
  // check and opened the gas valve onto blind sensors.
  enterState(sensorsAreWarm(nowMs) ? KitchenState::LEAKING
                                   : KitchenState::WARMING_UP, nowMs);

  return StartRejectReason::NONE;
}

void KitchenCore::stop(uint32_t nowMs) {
  if (state_ == KitchenState::WAITING) return;
  // ROUTINE — no ack required. An operator stop is an in-band command from
  // the interface that runs experiments, sent by someone who knows a run is
  // live, so it needs no ack to clear. The full 5 min purge still runs and is
  // NOT skippable: "routine" governs the ACK only, never the purge.
  enterFullyVentilating(DangerReason::NONE, /*requiresAck=*/false, nowMs);
}

void KitchenCore::humanAck(uint32_t nowMs) {
  // Two callers, deliberately indistinguishable from here: the physical USER
  // button and the MQTT ack command. The BUTTON path must work with no
  // network, like the e-stop — a physical ack matters precisely when the
  // network is the thing that is broken. kitchen.ino wires both.
  (void)nowMs;
  acked_ = true;
}

// ---------------------------------------------------------------------------
bool KitchenCore::canLeaveFullyVentilating(uint32_t nowMs) const {
  if (ackRequired_ && !acked_) return false;
  if (clearSinceMs_ == 0) return false;   // condition not currently clear
  uint32_t heldMs = nowMs - clearSinceMs_;
  return heldMs >= FULLY_VENT_MIN_HOLD_MS;
}

// ---------------------------------------------------------------------------
// The whole control path. Danger is evaluated FIRST, every pass, as a
// transition — not a rewrite of what the per-state logic below decides. That
// ordering is the entire safety argument: the LEAKING/HOLD/VENTILATING
// branches (the only ones that read spec_) are unreachable once this has
// moved the state to FULLY_VENTILATING.
// ---------------------------------------------------------------------------
OutputRequest KitchenCore::update(const SensorState& s, uint32_t nowMs) {
  // Reset every pass; only the LEAKING branch below (via integrateInventory())
  // sets it non-zero, so any other state — including a danger short-circuit
  // straight to FULLY_VENTILATING — correctly reports no flow.
  flowRate_mLps_ = 0.0f;

  DangerReason reason;
  bool danger = dangerActive(s, reason);

  if (danger) {
    enterFullyVentilating(reason, /*requiresAck=*/true, nowMs);
    // Danger active THIS pass means not-clear, full stop — regardless of
    // whether this is a fresh entry, a promotion, or a re-fire while already
    // latched with ackRequired_ already true (in which case
    // enterFullyVentilating()'s branches above don't touch clearSinceMs_ at
    // all). This is what makes "5 min clear" mean five CONTINUOUS minutes: a
    // danger re-firing near the end of an all-clear hold must restart it.
    clearSinceMs_ = 0;
  } else if (state_ == KitchenState::FULLY_VENTILATING) {
    // Track how long the condition has been continuously clear. Any pass
    // with danger active (handled above) resets this to 0, so "5 min clear"
    // means five CONTINUOUS minutes, not five minutes since entry.
    if (clearSinceMs_ == 0) {
      clearSinceMs_ = (nowMs == 0) ? 1 : nowMs;   // 0 is reserved as "not clear yet"
    }
  }

  // Role selector ONLY takes effect in WAITING (below). In any other state a
  // flip to equipment-test is inert — recorded here so the LED layer can blink
  // the real run-state at whoever flipped it, and so kitchen.ino can log the
  // edge (the core stays Arduino-free, so it cannot log itself).
  bool equipTest = !selectorIsLeakTest(s);
  roleMisflip_       = equipTest && state_ != KitchenState::WAITING;
  // Distinct from localSensorsOn_, which lingers true after a leak run until the
  // idle timeout: this is the live "equipment-test bench mode" flag, and it is
  // what gates the flowmeter-open in outputsFor().
  equipTestActive_ = equipTest && state_ == KitchenState::WAITING;

  switch (state_) {
    case KitchenState::WAITING: {
      if (equipTest) {
        // Equipment-test in WAITING: local H2 sensors ON, idle-to-OFF
        // disabled, flowmeter driven fully open (outputsFor()). No gate is
        // needed HERE because no gas can flow in WAITING — but the clock is
        // still stamped, so a leak run confirmed straight after a brief
        // equipment-test correctly sees cold sensors and gates in WARMING_UP.
        if (!localSensorsOn_) { localSensorsOn_ = true; sensorsOnSinceMs_ = nowMs; }
      } else if (localSensorsOn_ &&
                 nowMs - waitingIdleSinceMs_ >= SENSOR_IDLE_TIMEOUT_MS) {
        localSensorsOn_ = false;   // powering down discards the warm-up
      }
      break;
    }

    case KitchenState::ARMED: {
      if (nowMs - armedAtMs_ > ARM_TIMEOUT_MS) {
        enterState(KitchenState::WAITING, nowMs);
      }
      break;
    }

    case KitchenState::WARMING_UP: {
      // Gas is hard-closed by outputsFor() for the whole of this state, so the
      // only job here is to wait out the sensor warm-up. Entering LEAKING is
      // what starts gas, and enterState() re-bases phaseClockFromMs_ to NOW —
      // so a 5 s leak spec delivers a full 5 s of gas rather than being eaten
      // by the 70 s gate.
      if (sensorsAreWarm(nowMs)) {
        lastIntegrationMs_ = 0;   // don't integrate flow across the gate
        enterState(KitchenState::LEAKING, nowMs);
      }
      break;
    }

    case KitchenState::LEAKING: {
      integrateInventory(s, nowMs);

      if (stopConditionMet(spec_.leakStop, s, nowMs)) {
        enterState(KitchenState::HOLD, nowMs);
      }
      // A selector flip mid-leak is inert (see roleMisflip_ above): the LED
      // layer blinks the real run-state at whoever flipped it; the run is
      // unaffected. Use stop() to abort a live run.
      break;
    }

    case KitchenState::HOLD: {
      // Gas off, fans off; watch propagation undisturbed. This is the actual
      // MEASUREMENT phase of a leak-propagation experiment, so it runs for a
      // real duration — it used to fall straight through to VENTILATING,
      // making it instantaneous.
      //
      // Protocol guarantees holdStop.maxDurationMs is non-zero and clamped to
      // HOLD_MAX_DURATION_MS: a spec omitting holdStop falls back to that
      // ceiling rather than to zero, so this phase cannot be skipped or run
      // unbounded via a malformed spec.
      if (stopConditionMet(spec_.holdStop, s, nowMs)) {
        enterState(KitchenState::VENTILATING, nowMs);
      }
      break;
    }

    case KitchenState::VENTILATING: {
      // Single exit on the spec's stop condition, proceeding to the mandatory
      // purge. There is deliberately no "escalate to full ventilation past a
      // grace deadline" branch: it would do the same thing as the normal exit
      // and could never fire without the normal exit having fired first.
      // Ventilation adequacy is proven in FULLY_VENTILATING by the all-clear
      // hold — by MEASUREMENT — not by a deadline heuristic.
      if (stopConditionMet(spec_.ventStop, s, nowMs)) {
        enterFullyVentilating(DangerReason::NONE, /*requiresAck=*/false, nowMs);
      }
      break;
    }

    case KitchenState::FULLY_VENTILATING: {
      // canLeaveFullyVentilating() is the SOLE arbiter of when this state may
      // end:
      //   ackRequired_ -> needs humanAck() AND 5 min all-clear
      //   !ackRequired_ -> routine purge: 5 min hold, no ack
      // Routine covers the end-of-run purge, the pre-run clean-air proof, and
      // an operator stop(). ackRequired_ covers every danger condition and
      // the mid-leak selector flip.
      if (canLeaveFullyVentilating(nowMs)) {
        ackRequired_  = false;
        acked_        = false;
        reason_       = DangerReason::NONE;
        clearSinceMs_ = 0;
        enterState(KitchenState::WAITING, nowMs);
      }
      break;
    }
  }

  return outputsFor(nowMs);
}

// ---------------------------------------------------------------------------
// outputsFor() — reads state_ and, for LEAKING/VENTILATING only, spec_. The
// FULLY_VENTILATING branch is a fixed block: no spec_ read, no argument the
// website can influence. That is half of the audit target (the other half is
// dangerActive()).
// ---------------------------------------------------------------------------
OutputRequest KitchenCore::outputsFor(uint32_t /*nowMs*/) const {
  OutputRequest out;
  switch (state_) {
    case KitchenState::WAITING:
      // Equipment-test in WAITING: gas SUPPLY relay stays cut (no hydrogen),
      // but the flowmeter setpoint DAC is driven to full-scale so the flow
      // controller can be range-checked on the bench. flowSetpointTest tells
      // Outputs to pass this setpoint through despite gasOpen=false — the one
      // sanctioned exception to "gasOpen false => setpoint 0 V".
      out.gasOpen          = false;
      out.gasSetpointPct   = equipTestActive_ ? 100.0f : 0.0f;
      out.flowSetpointTest = equipTestActive_;
      out.registers        = RegisterSet{};
      out.fanSpeedPct      = VENT_SPEED_IDLE_PCT;
      out.alarmOn          = false;
      break;

    case KitchenState::ARMED:
      out.gasOpen        = false;
      out.gasSetpointPct = 0.0f;
      out.registers       = RegisterSet{};
      out.fanSpeedPct     = VENT_SPEED_IDLE_PCT;
      out.alarmOn         = false;
      break;

    case KitchenState::WARMING_UP:
      // Gas hard-closed, unconditionally and with no spec_ read. Sensors are
      // powered (localSensorsOn_ below) and warming; nothing else moves.
      out.gasOpen        = false;
      out.gasSetpointPct = 0.0f;
      out.registers       = RegisterSet{};
      out.fanSpeedPct     = 0.0f;
      out.alarmOn         = false;
      break;

    case KitchenState::LEAKING:
      // Reached only via WARMING_UP or an already-warm confirm(), so gas is
      // unconditionally open here — the gate is the state, not a flag.
      out.gasOpen        = true;
      out.gasSetpointPct = spec_.gasSetpointPct;
      out.registers       = RegisterSet{};
      out.fanSpeedPct     = 0.0f;
      out.alarmOn         = false;
      break;

    case KitchenState::HOLD:
      out.gasOpen        = false;
      out.gasSetpointPct = 0.0f;
      out.registers       = RegisterSet{};
      out.fanSpeedPct     = 0.0f;
      out.alarmOn         = false;
      break;

    case KitchenState::VENTILATING:
      out.gasOpen        = false;
      out.gasSetpointPct = 0.0f;
      out.registers       = spec_.ventRegisters;
      // Ceiling, not a floor: the website may only ask for LESS than max,
      // never more. clampFanSpeedPct() is also applied by Protocol at parse
      // time; re-applying here means the ceiling holds even if that ever
      // drifts, at zero cost (spec_.fanSpeedPct is already <= the ceiling in
      // the normal path).
      out.fanSpeedPct     = clampFanSpeedPct(spec_.fanSpeedPct);
      out.alarmOn         = false;
      break;

    case KitchenState::FULLY_VENTILATING:
      // Fixed, unconditional. Reads NOTHING from spec_.
      out.gasOpen        = false;
      out.gasSetpointPct = 0.0f;
      out.registers       = RegisterSet{true, true, true};
      out.fanSpeedPct     = 100.0f;
      out.alarmOn         = ackRequired_;
      break;
  }

  // LOCAL: A0602/base H2 sensors — LEAKING or equipment-test (set above).
  // REMOTE: CM7 DAQ — leak-test leak run only; remoteOn() derives it from
  // localSensorsOn_ + role + state (NOT lockstep — supersedes plan item 18).
  out.localSensorsOn  = localSensorsOn_;
  out.remoteSensorsOn = remoteOn();

  // State-based hydrogen-may-be-present indicator: anything but WAITING. Pure —
  // just a bool off state_, no spec_ read.
  out.gasMayBePresent = (state_ != KitchenState::WAITING);

  return out;
}

// =============================================================================
// PeerAlarmTable — see the contract in KitchenCore.h.
// =============================================================================
bool PeerAlarmTable::update(const char* topic, bool active, uint32_t nowMs) {
  if (topic == nullptr || topic[0] == '\0') return false;

  for (int i = 0; i < used_; i++) {
    if (strncmp(zones_[i].topic, topic, KITCHEN_PEER_TOPIC_MAXLEN) == 0) {
      zones_[i].active    = active;      // only THIS zone's state moves
      zones_[i].lastMsgMs = nowMs;
      return true;
    }
  }

  // Unknown zone. A "clear" from a zone we have never tracked tells us
  // nothing we don't already assume, so it needs no row — this keeps a noisy
  // lab full of quiet peers from exhausting the table.
  if (!active) return true;

  if (used_ >= KITCHEN_MAX_PEER_ZONES) {
    // Fail safe: we cannot track it, so we must not ignore it either.
    overflow_ = true;
    return false;
  }

  Zone& z = zones_[used_++];
  strncpy(z.topic, topic, KITCHEN_PEER_TOPIC_MAXLEN - 1);
  z.topic[KITCHEN_PEER_TOPIC_MAXLEN - 1] = '\0';
  z.active    = true;
  z.lastMsgMs = nowMs;
  return true;
}

bool PeerAlarmTable::anyActive() const {
  if (overflow_) return true;
  for (int i = 0; i < used_; i++) {
    if (zones_[i].active) return true;
  }
  return false;
}

bool PeerAlarmTable::anyStale(uint32_t nowMs, uint32_t staleMs) const {
  for (int i = 0; i < used_; i++) {
    if ((uint32_t)(nowMs - zones_[i].lastMsgMs) >= staleMs) return true;
  }
  return false;
}

void PeerAlarmTable::reset() {
  used_     = 0;
  overflow_ = false;
}

// =============================================================================
// RegisterSequencer — see the contract in KitchenCore.h. Was RegisterSequencer.cpp.
// =============================================================================
CoilStates RegisterSequencer::step(const RegisterSet& desired, uint32_t nowMs) {
  CoilStates out;

  // Central / exhaust: no sequencing, always immediate.
  out.centralOpen  = desired.central;
  out.centralClose = !desired.central;
  out.exhaustOpen  = desired.exhaust;
  out.exhaustClose = !desired.exhaust;

  bool desiredChanged = !initialized_ || desired != appliedDesired_;

  if (desiredChanged) {
    appliedDesired_ = desired;
    initialized_    = true;

    if (!desired.inlet) {
      // Closing is simultaneous — no delay, cancel any pending open.
      inletPending_       = false;
      inletCurrentlyOpen_ = false;
    } else if (inletCurrentlyOpen_) {
      // Already open — nothing to sequence against.
      inletPending_ = false;
    } else if (!desired.central && !desired.exhaust) {
      // Inlet opening alone, nothing else changing — immediate.
      inletPending_       = false;
      inletCurrentlyOpen_ = true;
    } else {
      // Inlet opening together with another register — defer.
      inletPending_    = true;
      inletDeadlineMs_ = nowMs + INLET_OPEN_DELAY_MS;
    }
  }

  if (desired.inlet && inletPending_ && nowMs >= inletDeadlineMs_) {
    inletPending_       = false;
    inletCurrentlyOpen_ = true;
  }

  bool inletOpenNow = desired.inlet && !inletPending_;
  if (!desired.inlet) inletCurrentlyOpen_ = false;

  out.inletOpen  = inletOpenNow;
  out.inletClose = !inletOpenNow;

  return out;
}
