// =============================================================================
// test_safety.cpp — native desktop test suite for the pure core. No Arduino,
// no hardware. Build/run with:
//
//   export PATH="$PATH:/c/mingw64/bin"
//   g++ -std=c++17 -DKITCHEN_ALLOW_PLACEHOLDER_SCALES -I kitchen \
//       kitchen/test/test_safety.cpp kitchen/KitchenCore.cpp \
//       kitchen/RegisterSequencer.cpp -o test_safety \
//       && ./test_safety
//
// KITCHEN_ALLOW_PLACEHOLDER_SCALES is required until the analog-scale
// constants are measured on the bench (see Kitchen_Settings.h). These tests
// drive counts directly and never exercise a physical-unit conversion, so
// placeholder scales are harmless here — but a FIRMWARE build without real
// values is refused at compile time on purpose.
//
// Minimal hand-rolled harness (no external test framework dependency) — each
// TEST() block prints PASS/FAIL and the run exits nonzero on any failure.
// =============================================================================

#include <cstdio>
#include <cstring>
#include "KitchenCore.h"
#include "RegisterSequencer.h"

static int g_failures = 0;
static int g_total    = 0;

#define CHECK(cond) do { \
  g_total++; \
  if (!(cond)) { \
    g_failures++; \
    printf("  FAIL: %s (%s:%d)\n", #cond, __FILE__, __LINE__); \
  } \
} while (0)

#define TEST(name) static void name(); \
  struct name##_runner { name##_runner() { printf("[TEST] %s\n", #name); name(); } } name##_instance; \
  static void name()

// ---------------------------------------------------------------------------
static SensorState cleanSensors() {
  SensorState s;
  s.localSensorCount = 1;
  s.localSensors[0].present         = true;
  s.localSensors[0].counts           = 0;
  s.localSensors[0].thresholdCounts  = SENSOR_THRESHOLD_DEFAULT_COUNTS;
  s.localSensors[0].stale            = false;
  s.localSensors[0].expectedOn       = true;
  s.isLeakTestRole = true;
  return s;
}

static RunSpec leakSpec(const char* runId, uint32_t durationMs = 5000,
                        uint32_t holdMs = 2000) {
  RunSpec spec;
  strncpy(spec.runId, runId, sizeof(spec.runId) - 1);
  spec.gasSetpointPct    = 50.0f;
  spec.leakStop.maxDurationMs = durationMs;
  // HOLD is a real measurement phase now — a spec with no holdStop would
  // stall here forever. Protocol supplies HOLD_MAX_DURATION_MS when the
  // website omits it; tests set it explicitly.
  spec.holdStop.maxDurationMs = holdMs;
  spec.ventRegisters     = RegisterSet{true, true, true};
  spec.fanSpeedPct       = 60.0f;
  spec.ventStop.maxDurationMs = 5000;
  spec.valid = true;
  return spec;
}

// Drive a fresh core to LEAKING with warm-up already elapsed. Returns the
// timestamp at which LEAKING sequencing actually began.
static uint32_t driveToLeaking(KitchenCore& core, SensorState& s,
                               const RunSpec& spec) {
  core.start(spec, s, 0);
  core.confirm(spec.runId, 0);
  core.update(s, SENSOR_WARMUP_MS);   // warm-up gate releases
  return SENSOR_WARMUP_MS;
}

// =============================================================================
// Danger conditions — each forces gas off + full ventilation, same pass
// =============================================================================

TEST(danger_local_sensor_threshold_forces_gas_off) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.localSensors[0].counts = SENSOR_THRESHOLD_DEFAULT_COUNTS + 1;

  OutputRequest out = core.update(s, 1000);

  CHECK(out.gasOpen == false);
  CHECK(out.gasSetpointPct == 0.0f);
  CHECK(out.registers.allOpen());
  CHECK(out.fanSpeedPct == 100.0f);
  CHECK(out.alarmOn == true);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
}

// Boundary convention: AT-OR-ABOVE. A reading exactly at the threshold TRIPS.
// Both sides checked so a future change from >= back to > fails loudly.
TEST(threshold_boundary_is_at_or_above) {
  SensorState s = cleanSensors();

  s.localSensors[0].counts = SENSOR_THRESHOLD_DEFAULT_COUNTS;   // exactly at
  KitchenCore atCore;
  CHECK(atCore.update(s, 1000).gasOpen == false);

  s.localSensors[0].counts = SENSOR_THRESHOLD_DEFAULT_COUNTS - 1;   // just below
  KitchenCore belowCore;
  CHECK(belowCore.update(s, 1000).gasOpen == false);   // WAITING drives no gas anyway
  CHECK(belowCore.state() == KitchenState::WAITING);   // ...but crucially not latched
}

TEST(danger_flow_over_limit_sustained_forces_gas_off) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.flowOverLimitSustained = true;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
  CHECK(out.registers.allOpen());
}

TEST(danger_inventory_cap_exceeded_forces_gas_off) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.deliveredInventory_mL = INVENTORY_CAP_ML + 1.0f;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
}

TEST(danger_peer_alarm_forces_gas_off_even_in_equipment_test) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.isLeakTestRole = false;   // equipment-test
  s.peerAlarmActive = true;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
  CHECK(out.registers.allOpen());
}

TEST(danger_permit_present_and_false_forces_gas_off) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.permitPresent = true;
  s.permitValue    = false;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
}

TEST(danger_permit_absent_does_not_trip) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.permitPresent = false;   // absence warns, does not trip

  core.update(s, 1000);
  CHECK(core.state() != KitchenState::FULLY_VENTILATING);
}

TEST(danger_estop_forces_gas_off) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.estopPressed = true;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
  CHECK(out.registers.allOpen());
  CHECK(out.fanSpeedPct == 100.0f);
}

TEST(danger_local_sensor_silent_over_10s_when_expected_on_trips) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.localSensors[0].expectedOn = true;
  s.localSensors[0].stale      = true;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
}

TEST(stale_while_intentionally_off_does_not_trip) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.localSensors[0].expectedOn = false;   // sensors intentionally off
  s.localSensors[0].stale      = true;

  core.update(s, 1000);
  CHECK(core.state() != KitchenState::FULLY_VENTILATING);
}

// A run spec requesting gas while a danger condition holds still gets no gas:
// drive to LEAKING (gas actually flowing), then trip a danger and confirm the
// SAME pass cuts it.
TEST(run_requesting_gas_while_danger_holds_still_gets_no_gas) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));
  OutputRequest leaking = core.update(s, t + 5);
  CHECK(leaking.gasOpen == true);   // sanity: gas really is flowing

  s.estopPressed = true;
  OutputRequest out = core.update(s, t + 10);
  CHECK(out.gasOpen == false);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
}

// =============================================================================
// Threshold clamping
// =============================================================================

TEST(threshold_below_firmware_minimum_is_rejected) {
  uint16_t clamped = KitchenCore::clampThreshold(SENSOR_THRESHOLD_FIRMWARE_MIN_COUNTS - 100);
  CHECK(clamped == SENSOR_THRESHOLD_FIRMWARE_MIN_COUNTS);
}

TEST(threshold_above_firmware_minimum_is_accepted) {
  uint16_t requested = SENSOR_THRESHOLD_FIRMWARE_MIN_COUNTS + 500;
  uint16_t clamped   = KitchenCore::clampThreshold(requested);
  CHECK(clamped == requested);
}

TEST(ventilating_fan_speed_is_clamped_to_ceiling) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1", /*durationMs=*/100, /*holdMs=*/100);
  spec.fanSpeedPct = VENT_SPEED_MAX_PCT + 50.0f;   // a spec cannot exceed the ceiling
  uint32_t t = driveToLeaking(core, s, spec);

  core.update(s, t + 200);              // -> HOLD
  OutputRequest out = core.update(s, t + 200 + 100);   // -> VENTILATING
  CHECK(core.state() == KitchenState::VENTILATING);
  CHECK(out.fanSpeedPct == VENT_SPEED_MAX_PCT);
}

// =============================================================================
// KitchenCore state machine
// =============================================================================

TEST(arm_timeout_returns_to_waiting_no_gas) {
  KitchenCore core;
  SensorState s = cleanSensors();

  auto rej = core.start(leakSpec("run1"), s, 0);
  CHECK(rej == StartRejectReason::NONE);
  CHECK(core.state() == KitchenState::ARMED);

  OutputRequest out = core.update(s, ARM_TIMEOUT_MS + 1);
  CHECK(core.state() == KitchenState::WAITING);
  CHECK(out.gasOpen == false);
}

TEST(selector_equipment_test_rejects_leak_run) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.isLeakTestRole = false;

  auto rej = core.start(leakSpec("run1"), s, 0);
  CHECK(rej == StartRejectReason::WRONG_ROLE);
  CHECK(core.state() == KitchenState::WAITING);
}

TEST(waiting_drives_idle_speed_not_full) {
  KitchenCore core;
  SensorState s = cleanSensors();
  OutputRequest out = core.update(s, 0);
  CHECK(out.fanSpeedPct == VENT_SPEED_IDLE_PCT);
}

TEST(fully_ventilating_drives_100_regardless_of_spec) {
  KitchenCore core;
  SensorState s = cleanSensors();
  core.start(leakSpec("run1"), s, 0);
  core.confirm("run1", 0);
  core.stop(SENSOR_WARMUP_MS + 1);   // abort into purge
  OutputRequest out = core.update(s, SENSOR_WARMUP_MS + 1);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  CHECK(out.fanSpeedPct == 100.0f);
  CHECK(out.registers.allOpen());
}

TEST(hold_drives_zero_fan_speed) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1", /*durationMs=*/100);
  uint32_t t = driveToLeaking(core, s, spec);

  OutputRequest out = core.update(s, t + 200);   // past the 100 ms leak duration
  CHECK(core.state() == KitchenState::HOLD);
  CHECK(out.fanSpeedPct == 0.0f);
}

// The leak duration measures GAS FLOWING, not time-since-LEAKING-entry.
// Regression: with the clock based at LEAKING entry, the 30 s warm-up gate
// consumed the whole phase and a 5 s leak spec delivered ZERO gas.
TEST(leak_duration_is_timed_from_gas_flowing_not_state_entry) {
  KitchenCore core;
  SensorState s = cleanSensors();
  core.start(leakSpec("run1", /*durationMs=*/5000), s, 0);
  core.confirm("run1", 0);

  // Warm-up gate releases at SENSOR_WARMUP_MS; gas starts flowing there.
  OutputRequest w = core.update(s, SENSOR_WARMUP_MS);
  CHECK(core.state() == KitchenState::LEAKING);   // NOT already fallen through
  CHECK(w.gasOpen == true);

  // Still leaking 4999 ms after the gate, not 4999 ms after entry.
  core.update(s, SENSOR_WARMUP_MS + 4999);
  CHECK(core.state() == KitchenState::LEAKING);

  core.update(s, SENSOR_WARMUP_MS + 5000);
  CHECK(core.state() == KitchenState::HOLD);
}

// =============================================================================
// HOLD is a real measurement phase (was instantaneous)
// =============================================================================

TEST(hold_runs_for_its_duration_then_ventilates) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1", /*durationMs=*/100, /*holdMs=*/2000);
  uint32_t t = driveToLeaking(core, s, spec);

  core.update(s, t + 200);                     // -> HOLD at t+200
  CHECK(core.state() == KitchenState::HOLD);

  core.update(s, t + 200 + 1999);              // still holding, 1 ms short
  CHECK(core.state() == KitchenState::HOLD);

  core.update(s, t + 200 + 2000);              // duration met
  CHECK(core.state() == KitchenState::VENTILATING);
}

TEST(hold_ends_on_sensor_quorum) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.localSensorCount = 3;
  for (int i = 0; i < 3; i++) {
    s.localSensors[i].present         = true;
    s.localSensors[i].counts          = 0;
    s.localSensors[i].thresholdCounts = SENSOR_THRESHOLD_DEFAULT_COUNTS;
    s.localSensors[i].expectedOn      = true;
  }
  RunSpec spec = leakSpec("run1", /*durationMs=*/100, /*holdMs=*/600000);
  // Quorum threshold well BELOW the danger threshold, so the quorum fires
  // without tripping the danger check — they are independent conditions.
  spec.holdStop.sensorQuorum.thresholdCounts = 100;
  spec.holdStop.sensorQuorum.quorumCount     = 2;
  uint32_t t = driveToLeaking(core, s, spec);

  core.update(s, t + 200);
  CHECK(core.state() == KitchenState::HOLD);

  s.localSensors[0].counts = 150;           // one sensor over — not yet quorum
  core.update(s, t + 300);
  CHECK(core.state() == KitchenState::HOLD);

  s.localSensors[1].counts = 100;           // second sensor, exactly AT -> quorum
  core.update(s, t + 400);
  CHECK(core.state() == KitchenState::VENTILATING);
}

// Quorum threshold (100) is kept well below the danger threshold
// (SENSOR_THRESHOLD_DEFAULT_COUNTS) throughout, so the danger check never
// fires and the test isolates quorum logic specifically — see
// hold_ends_on_sensor_quorum above for the same convention.
TEST(quorum_ignores_absent_and_stale_sensors) {
  SensorState s = cleanSensors();
  s.localSensorCount = 3;
  for (int i = 0; i < 3; i++) {
    s.localSensors[i].present         = true;
    s.localSensors[i].counts          = 150;
    s.localSensors[i].thresholdCounts = SENSOR_THRESHOLD_DEFAULT_COUNTS;
    s.localSensors[i].expectedOn      = true;
  }
  s.localSensors[1].present = false;   // absent
  s.localSensors[2].stale       = true;    // silent — not evidence of concentration
  s.localSensors[2].expectedOn  = false;   // ...and not itself a danger condition here;
                                            // that check is covered separately by
                                            // danger_local_sensor_silent_over_10s_when_expected_on_trips

  KitchenCore core;
  RunSpec spec = leakSpec("run1", /*durationMs=*/100, /*holdMs=*/600000);
  spec.holdStop.sensorQuorum.thresholdCounts = 100;
  spec.holdStop.sensorQuorum.quorumCount     = 2;   // only 1 sensor really counts
  uint32_t t = driveToLeaking(core, s, spec);

  core.update(s, t + 200);
  CHECK(core.state() == KitchenState::HOLD);
  core.update(s, t + 5000);
  CHECK(core.state() == KitchenState::HOLD);   // never reaches quorum
}

TEST(quorum_count_zero_never_trips) {
  SensorState s = cleanSensors();
  s.localSensors[0].counts = SENSOR_THRESHOLD_DEFAULT_COUNTS - 1;   // over quorum's
                                                                     // threshold, but
                                                                     // below the danger
                                                                     // threshold

  KitchenCore core;
  RunSpec spec = leakSpec("run1", /*durationMs=*/100, /*holdMs=*/600000);
  spec.holdStop.sensorQuorum.thresholdCounts = 100;
  spec.holdStop.sensorQuorum.quorumCount     = 0;   // unused
  uint32_t t = driveToLeaking(core, s, spec);

  core.update(s, t + 200);
  core.update(s, t + 5000);
  CHECK(core.state() == KitchenState::HOLD);
}

// =============================================================================
// FULLY_VENTILATING exit — ack + all-clear, same pass as the danger itself
// =============================================================================

TEST(emergency_entry_requires_human_ack_routine_does_not) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.estopPressed = true;   // danger condition present from the start

  core.update(s, 0);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  CHECK(core.ackRequired());
  CHECK(!core.acked());

  // Condition clears at t=1000. The all-clear clock starts HERE, not at the
  // moment of the danger — so release needs FULLY_VENT_MIN_HOLD_MS measured
  // from this instant, AND an ack. Both, not either.
  s.estopPressed = false;
  uint32_t clearedAt = 1000;
  core.update(s, clearedAt);

  uint32_t held = clearedAt + FULLY_VENT_MIN_HOLD_MS;
  core.update(s, held);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);   // hold satisfied, but no ack yet

  core.humanAck(held);
  core.update(s, held);
  CHECK(core.state() == KitchenState::WAITING);             // ack + hold -> released
}

// The ack is not a shortcut past the all-clear hold: acking early still
// requires the full FULLY_VENT_MIN_HOLD_MS of continuously-clear time.
TEST(ack_does_not_shortcut_the_all_clear_hold) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.estopPressed = true;

  core.update(s, 0);
  s.estopPressed = false;
  core.update(s, 1000);   // all-clear clock starts at 1000

  core.humanAck(1000);    // acked immediately
  core.update(s, 1000);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);

  core.update(s, 1000 + FULLY_VENT_MIN_HOLD_MS - 1);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);

  core.update(s, 1000 + FULLY_VENT_MIN_HOLD_MS);
  CHECK(core.state() == KitchenState::WAITING);
}

// A danger firing part-way through an all-clear hold restarts the 5-minute
// clock — "5 min clear" means five CONTINUOUS minutes.
TEST(danger_during_all_clear_hold_restarts_the_clock) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.estopPressed = true;
  core.update(s, 0);
  core.humanAck(0);

  s.estopPressed = false;
  core.update(s, 1000);                                  // all-clear starts at 1000
  core.update(s, 1000 + FULLY_VENT_MIN_HOLD_MS - 10);     // almost there

  s.estopPressed = true;                                  // fires again, late
  core.update(s, 1000 + FULLY_VENT_MIN_HOLD_MS - 5);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);

  s.estopPressed = false;
  uint32_t restartedAt = 1000 + FULLY_VENT_MIN_HOLD_MS - 5;
  core.update(s, restartedAt);
  core.update(s, restartedAt + FULLY_VENT_MIN_HOLD_MS - 1);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);   // not yet — clock restarted

  core.update(s, restartedAt + FULLY_VENT_MIN_HOLD_MS);
  CHECK(core.state() == KitchenState::WAITING);   // ack from before survives re-arming
}

TEST(danger_clearing_does_not_resume_leaking) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));

  s.estopPressed = true;
  core.update(s, t + 10);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);

  s.estopPressed = false;   // condition clears — must NOT resume the run
  for (uint32_t dt = 20; dt < 200; dt += 10) {
    core.update(s, t + dt);
  }
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
}

TEST(danger_exit_needs_ack_and_all_clear_then_resets_for_next_episode) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));

  s.estopPressed = true;
  core.update(s, t + 10);
  s.estopPressed = false;

  // All-clear elapses but no ack given -> still purging.
  uint32_t late = t + 10 + FULLY_VENT_MIN_HOLD_MS + 1;
  core.update(s, late);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);

  // Ack, then let the all-clear hold elapse from the ack onward.
  core.humanAck(late);
  uint32_t done = late + FULLY_VENT_MIN_HOLD_MS + 1;
  core.update(s, done);
  CHECK(core.state() == KitchenState::WAITING);
  CHECK(core.ackRequired() == false);            // reset for the next episode
  CHECK(core.reason() == DangerReason::NONE);
}

TEST(routine_purge_auto_returns_without_ack) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));

  core.update(s, t + 5);
  core.stop(t + 10);   // operator stop — routine, no ack required
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  CHECK(core.ackRequired() == false);

  core.update(s, t + 10 + FULLY_VENT_MIN_HOLD_MS - 1);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);   // purge not skippable

  core.update(s, t + 10 + FULLY_VENT_MIN_HOLD_MS);
  CHECK(core.state() == KitchenState::WAITING);             // no ack needed
}

// Promotion, not demotion: a danger firing during a routine purge upgrades it
// to requiring an ack; re-entering while already ack-required never clears it.
TEST(danger_during_routine_purge_promotes_to_ack_required) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));

  core.update(s, t + 5);
  core.stop(t + 10);   // routine purge begins
  CHECK(core.ackRequired() == false);

  s.estopPressed = true;
  core.update(s, t + 20);   // danger fires mid-purge
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  CHECK(core.ackRequired() == true);   // promoted

  s.estopPressed = false;
  uint32_t late = t + 20 + FULLY_VENT_MIN_HOLD_MS + 1;
  core.update(s, late);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);   // all-clear alone isn't enough now

  core.humanAck(late);
  core.update(s, late);
  core.update(s, late + FULLY_VENT_MIN_HOLD_MS + 1);
  CHECK(core.state() == KitchenState::WAITING);
}

// =============================================================================
// Operator abort: selector flipped mid-leak
// =============================================================================

TEST(selector_flip_mid_leak_latches_and_requires_ack) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));

  s.isLeakTestRole = false;   // physically flipped away from leak-test
  core.update(s, t + 10);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  CHECK(core.ackRequired());
  CHECK(core.reason() == DangerReason::OPERATOR_ABORT);

  // All-clear alone is not enough — this needs a human.
  uint32_t late = t + 10 + FULLY_VENT_MIN_HOLD_MS + 1;
  core.update(s, late);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
}

TEST(operator_abort_is_idempotent_and_preserves_ack) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));

  s.isLeakTestRole = false;
  core.update(s, t + 10);
  core.humanAck(t + 20);
  CHECK(core.acked() == true);

  // Called again every pass the selector stays wrong — must NOT discard the ack.
  core.update(s, t + 30);
  CHECK(core.acked() == true);
  CHECK(core.ackRequired() == true);
}

// =============================================================================
// Register sequencing (fake clock)
// =============================================================================

TEST(opening_exhaust_and_inlet_together_delays_inlet) {
  RegisterSequencer seq;
  RegisterSet desired{false, true, true};   // exhaust + inlet

  CoilStates c0 = seq.step(desired, 0);
  CHECK(c0.exhaustOpen == true);
  CHECK(c0.inletOpen == false);

  CoilStates c1 = seq.step(desired, INLET_OPEN_DELAY_MS - 1);
  CHECK(c1.inletOpen == false);

  CoilStates c2 = seq.step(desired, INLET_OPEN_DELAY_MS);
  CHECK(c2.inletOpen == true);
}

TEST(closing_all_three_is_simultaneous) {
  RegisterSequencer seq;
  RegisterSet openAll{true, true, true};
  seq.step(openAll, 0);
  seq.step(openAll, INLET_OPEN_DELAY_MS);   // let inlet actually open first

  RegisterSet closeAll{false, false, false};
  CoilStates c = seq.step(closeAll, INLET_OPEN_DELAY_MS + 10);
  CHECK(c.centralClose == true);
  CHECK(c.exhaustClose == true);
  CHECK(c.inletClose == true);
}

TEST(inlet_opening_alone_is_immediate) {
  RegisterSequencer seq;
  RegisterSet inletOnly{false, false, true};
  CoilStates c = seq.step(inletOnly, 0);
  CHECK(c.inletOpen == true);
}

TEST(request_change_before_deadline_supersedes_pending_inlet) {
  RegisterSequencer seq;
  RegisterSet openExhaustInlet{false, true, true};
  seq.step(openExhaustInlet, 0);   // inlet now pending, deadline = 1000

  RegisterSet closeAll{false, false, false};
  seq.step(closeAll, 500);   // supersede before the deadline fires

  CoilStates c = seq.step(closeAll, INLET_OPEN_DELAY_MS + 1);
  CHECK(c.inletOpen == false);
  CHECK(c.inletClose == true);
}

// =============================================================================
int main() {
  printf("\n%d/%d checks passed\n", g_total - g_failures, g_total);
  if (g_failures > 0) {
    printf("%d FAILURES\n", g_failures);
    return 1;
  }
  printf("ALL PASS\n");
  return 0;
}
