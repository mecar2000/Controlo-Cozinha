// =============================================================================
// test_states.cpp — the KitchenCore state machine: arming, phase transitions,
// phase clocks, and the HOLD measurement phase with its quorum stop condition.
//
// Danger-driven transitions live in test_danger.cpp; the FULLY_VENTILATING
// exit rules live in test_latch.cpp. This file covers the normal-run path.
// =============================================================================

#include "test_harness.h"

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

// =============================================================================
// start()/confirm() rejection paths (testproblems.txt 2.5) — of six
// StartRejectReason values only NONE and WRONG_ROLE had a test before this.
// =============================================================================

// WRONG_STATE: start() while already running — a real operator double-click.
TEST(start_while_already_running_is_rejected_wrong_state) {
  KitchenCore core;
  SensorState s = cleanSensors();
  CHECK(core.start(leakSpec("run1"), s, 0) == StartRejectReason::NONE);
  CHECK(core.state() == KitchenState::ARMED);

  auto rej = core.start(leakSpec("run2"), s, 10);
  CHECK(rej == StartRejectReason::WRONG_STATE);
  CHECK(core.state() == KitchenState::ARMED);   // first arm undisturbed
}

// INVALID_SPEC: Protocol marks spec.valid = false when its own validate/clamp
// pass rejects it; KitchenCore must refuse to arm on that alone.
TEST(start_with_invalid_spec_is_rejected) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1");
  spec.valid = false;   // as Protocol would leave it on a parse/validate failure

  auto rej = core.start(spec, s, 0);
  CHECK(rej == StartRejectReason::INVALID_SPEC);
  CHECK(core.state() == KitchenState::WAITING);
}

// RUN_ID_MISMATCH: confirm() with a different run id than the one armed — the
// anti-crosstalk guard between two browser sessions.
TEST(confirm_with_wrong_run_id_is_rejected) {
  KitchenCore core;
  SensorState s = cleanSensors();
  CHECK(core.start(leakSpec("run1"), s, 0) == StartRejectReason::NONE);

  auto rej = core.confirm("some-other-run", 10);
  CHECK(rej == StartRejectReason::RUN_ID_MISMATCH);
  CHECK(core.state() == KitchenState::ARMED);   // still armed, not consumed
}

// ARM_TIMED_OUT via confirm() specifically — separate code path from the
// update()-driven timeout (arm_timeout_returns_to_waiting_no_gas above),
// which never calls confirm() at all. This is KitchenCore.cpp:222.
TEST(confirm_after_arm_timeout_is_rejected_arm_timed_out) {
  KitchenCore core;
  SensorState s = cleanSensors();
  CHECK(core.start(leakSpec("run1"), s, 0) == StartRejectReason::NONE);

  auto rej = core.confirm("run1", ARM_TIMEOUT_MS + 1);
  CHECK(rej == StartRejectReason::ARM_TIMED_OUT);
  CHECK(core.state() == KitchenState::WAITING);
}

// confirm() while NOT armed (e.g. already WAITING) is also WRONG_STATE — the
// same reject reason as a redundant start(), covering confirm()'s own guard.
TEST(confirm_while_not_armed_is_rejected_wrong_state) {
  KitchenCore core;
  auto rej = core.confirm("run1", 0);   // never armed
  CHECK(rej == StartRejectReason::WRONG_STATE);
  CHECK(core.state() == KitchenState::WAITING);
}

// =============================================================================
// stop() in WAITING is a no-op (testproblems.txt 2.6) — a spurious stop must
// not launch a 5-minute purge from idle.
// =============================================================================
TEST(stop_in_waiting_is_a_no_op) {
  KitchenCore core;
  SensorState s = cleanSensors();
  CHECK(core.state() == KitchenState::WAITING);

  core.stop(0);
  OutputRequest out = core.update(s, 0);
  CHECK(core.state() == KitchenState::WAITING);   // no purge launched
  CHECK(out.fanSpeedPct == VENT_SPEED_IDLE_PCT);   // not the FULLY_VENTILATING 100%
}

TEST(waiting_drives_idle_speed_not_full) {
  KitchenCore core;
  SensorState s = cleanSensors();
  OutputRequest out = core.update(s, 0);
  CHECK(out.fanSpeedPct == VENT_SPEED_IDLE_PCT);
}

// testproblems.txt 3.4: the spec here must set values that DIFFER from the
// fixed FULLY_VENTILATING output, or "regardless of spec" is never actually
// demonstrated — a spec that happens to ask for the same fixed values would
// pass this test even if the FULLY_VENTILATING block read spec_ after all.
TEST(fully_ventilating_drives_100_regardless_of_spec) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1");
  spec.fanSpeedPct  = 30.0f;                       // differs from the fixed 100%
  spec.ventRegisters = RegisterSet{false, false, false};   // differs from allOpen()
  core.start(spec, s, 0);
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
// Regression: with the clock based at LEAKING entry, the SENSOR_WARMUP_MS
// warm-up gate consumed the whole phase and a 5 s leak spec delivered ZERO gas.
TEST(leak_duration_is_timed_from_gas_flowing_not_state_entry) {
  KitchenCore core;
  SensorState s = cleanSensors();
  core.start(leakSpec("run1", /*durationMs=*/5000), s, 0);
  core.confirm("run1", 0);

  CHECK(core.state() == KitchenState::WARMING_UP);   // gated, no gas yet

  // Gate releases at SENSOR_WARMUP_MS: WARMING_UP -> LEAKING, gas starts here.
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

// quorumMet() ignores expectedOn — pinned either way (testproblems.txt 2.8).
// quorumMet() is private, so exercised the same way as the other quorum
// tests here: through stopConditionMet() via a real HOLD phase transition.
// quorumMet() filters on `present` and `stale` only, never `expectedOn`, so a
// sensor with stale=false/expectedOn=false (present, intentionally marked
// off, but not yet flagged stale) STILL counts toward quorum. This documents
// that as the current, deliberate behaviour: quorum ends a phase early on
// concentration evidence alone, and is a different concern from the
// stale+expectedOn danger check (danger_local_sensor_silent_over_10s_when_
// expected_on_trips) that only cares about the sensor's OWN health.
TEST(quorum_counts_a_present_non_stale_sensor_even_when_not_expected_on) {
  SensorState s = cleanSensors();
  s.localSensors[0].stale      = false;   // NOT stale — still delivering readings
  s.localSensors[0].expectedOn = false;   // ...despite being marked "not expected on"
  s.localSensors[0].counts     = 150;

  KitchenCore core;
  RunSpec spec = leakSpec("run1", /*durationMs=*/100, /*holdMs=*/600000);
  spec.holdStop.sensorQuorum.thresholdCounts = 100;
  spec.holdStop.sensorQuorum.quorumCount     = 1;
  uint32_t t = driveToLeaking(core, s, spec);

  core.update(s, t + 200);   // -> HOLD
  core.update(s, t + 300);   // quorum should fire despite expectedOn == false
  CHECK(core.state() == KitchenState::VENTILATING);
}

TEST(quorum_count_zero_never_trips) {
  SensorState s = cleanSensors();
  // Over quorum's threshold (100) but explicitly below the DANGER threshold,
  // not just one count below it by coincidence of two constants once sharing
  // a value (testproblems.txt section 1) — s.localSensors[0].thresholdCounts
  // is left at SENSOR_THRESHOLD_DEFAULT_COUNTS by cleanSensors().
  s.localSensors[0].counts = SENSOR_THRESHOLD_DEFAULT_COUNTS - 50;

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
// integrateInventory() — testproblems.txt 2.4. With placeholder scales both
// FLOW_COUNTS_PER_VOLT and FLOW_ML_PER_SEC_AT_10V are 1.0f, so
// mL/s = flowCounts * 0.1 — deterministic and exercisable through the real
// integrator without needing bench-calibrated constants.
//
// Regression this pins: integrateInventory() previously treated raw counts
// AS mL/s directly, so INVENTORY_CAP_ML tripped after ~2 s of any flow at
// all and aborted every run (KitchenCore.cpp comment on integrateInventory).
// =============================================================================

TEST(inventory_integrates_from_flow_counts_not_raw_counts) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.flowCounts = 100;   // -> 10 mL/s with placeholder scales
  uint32_t t = driveToLeaking(core, s, leakSpec("run1", /*durationMs=*/200000));

  // driveToLeaking()'s own gate-release update is dispatched on the
  // WARMING_UP branch that same pass (state_ hasn't advanced yet when the
  // switch reads it), so it never calls integrateInventory() at all. The
  // FIRST real LEAKING-branch pass only stamps lastIntegrationMs_ (see
  // inventory_first_pass_after_gate_release_does_not_backdate below) — a
  // THIRD tick is needed before any interval has actually elapsed.
  core.update(s, t + 1);      // first real LEAKING pass: stamps the clock, 0 mL
  core.update(s, t + 1001);   // second: ~1 s of REAL elapsed integration
  // Comparing against the old (broken) behaviour: at flowCounts==100 treated
  // AS mL/s directly, 1 s would deliver 100 mL, not 10 mL. The real
  // conversion (counts -> volts -> mL/s) must deliver far less.
  CHECK(core.deliveredInventory_mL() > 0.0f);
  CHECK(core.deliveredInventory_mL() < 50.0f);   // nowhere near the broken 100 mL/s reading
}

// lastIntegrationMs_ == 0 first-pass skip: the first update() that actually
// runs the LEAKING branch (and so calls integrateInventory() for the first
// time) must not integrate over "0 to now" as if that whole span were
// flowing — it should only start the clock.
TEST(inventory_first_pass_after_gate_release_does_not_backdate) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.flowCounts = 100;
  uint32_t t = driveToLeaking(core, s, leakSpec("run1", /*durationMs=*/200000));
  // t is the instant the gate released; that same update() dispatched on the
  // WARMING_UP branch (state_ advances during it) and never touched the
  // integrator. This is the FIRST pass that actually runs LEAKING's
  // integrateInventory() call, well after t — if lastIntegrationMs_==0 were
  // treated as a real timestamp, this large gap would backdate a huge bogus
  // interval instead of just priming the clock.
  core.update(s, t + 60000);
  CHECK(core.deliveredInventory_mL() == 0.0f);
}

// lastIntegrationMs_ resets across the warm-up gate (KitchenCore.cpp:343,
// "don't integrate flow across the gate"): flow present during WARMING_UP
// must not be integrated once gas actually starts in LEAKING.
TEST(inventory_does_not_integrate_flow_during_warmup_gate) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.flowCounts = 100;   // flow feedback present even before gas is flowing
  core.start(leakSpec("run1", /*durationMs=*/200000), s, 0);
  core.confirm("run1", 0);
  CHECK(core.state() == KitchenState::WARMING_UP);

  // Time passes during the gate with flow "present" (e.g. a stuck-open valve
  // upstream) — none of this should be integrated once LEAKING starts.
  core.update(s, SENSOR_WARMUP_MS / 2);
  core.update(s, SENSOR_WARMUP_MS - 1);
  CHECK(core.deliveredInventory_mL() == 0.0f);   // nothing integrated yet — correct, no gas

  // Gate releases into LEAKING; the very next tick must start the clock
  // fresh rather than integrating "SENSOR_WARMUP_MS/2 to now" as if all of
  // it were flow.
  core.update(s, SENSOR_WARMUP_MS);
  CHECK(core.state() == KitchenState::LEAKING);
  core.update(s, SENSOR_WARMUP_MS + 100);   // ~100 ms of real flow
  // 100 ms at 10 mL/s = 1 mL. If the gate reset had NOT happened, the
  // (SENSOR_WARMUP_MS/2)-long backdated interval would deliver orders of
  // magnitude more.
  CHECK(core.deliveredInventory_mL() < 5.0f);
}

// Inventory resets to zero on confirm() (KitchenCore.cpp:228): a run started
// after a previous run's inventory accumulated must not carry that total
// into the new run.
TEST(inventory_resets_to_zero_on_confirm) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.flowCounts = 100;
  uint32_t t1 = driveToLeaking(core, s, leakSpec("run1", /*durationMs=*/200000));
  core.update(s, t1 + 1);      // first real LEAKING pass: stamps the clock
  core.update(s, t1 + 1001);   // ~1 s of real integration
  CHECK(core.deliveredInventory_mL() > 0.0f);   // first run accumulated something

  core.stop(t1 + 1001);                                    // abort into purge
  core.update(s, t1 + 1001 + FULLY_VENT_MIN_HOLD_MS + 1);   // -> WAITING

  s.flowCounts = 0;   // no flow for the second run
  core.start(leakSpec("run2", /*durationMs=*/200000), s,
            t1 + 1001 + FULLY_VENT_MIN_HOLD_MS + 1);
  core.confirm("run2", t1 + 1001 + FULLY_VENT_MIN_HOLD_MS + 1);
  CHECK(core.deliveredInventory_mL() == 0.0f);   // reset, not carried over
}

// maxInventory_mL as a STOP CONDITION: stopConditionMet()'s inventory branch
// (KitchenCore.cpp:174) had no test on any path. Drives real flow through
// the real integrator until the leakStop inventory cap ends the phase.
TEST(leak_phase_ends_on_inventory_cap_via_real_integrator) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.flowCounts = 100;   // -> 10 mL/s with placeholder scales
  RunSpec spec = leakSpec("run1", /*durationMs=*/200000);
  spec.leakStop.maxInventory_mL = 5.0f;   // small cap, reached in ~0.5 s of flow
  uint32_t t = driveToLeaking(core, s, spec);

  core.update(s, t + 100);   // priming tick
  CHECK(core.state() == KitchenState::LEAKING);

  // Keep ticking until the cap is met or a generous timeout — proves the
  // phase actually exits via inventory, not via the (huge) duration cap.
  uint32_t tick = 100;
  while (core.state() == KitchenState::LEAKING && tick < 5000) {
    tick += 100;
    core.update(s, t + tick);
  }
  CHECK(core.state() == KitchenState::HOLD);
  CHECK(core.deliveredInventory_mL() >= 5.0f);
}

// =============================================================================
// Ventilation DURING the leak. Gas flowing and dampers/fan running are not
// mutually exclusive — a run may leak into a partially-vented room. The
// default spec must stay sealed so existing runs are unaffected.
// =============================================================================

TEST(leak_phase_defaults_to_sealed_when_spec_omits_vent) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1");   // sets no leakRegisters/leakFanSpeedPct
  uint32_t t = driveToLeaking(core, s, spec);
  OutputRequest out = core.update(s, t + 10);

  CHECK(core.state() == KitchenState::LEAKING);
  CHECK(out.gasOpen == true);
  // Unchanged pre-existing behaviour: sealed room while leaking.
  CHECK(out.registers.anyOpen() == false);
  CHECK(out.fanSpeedPct == 0.0f);
}

TEST(leak_phase_drives_requested_registers_and_fan_while_gas_flows) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1");
  spec.leakRegisters   = RegisterSet{/*central=*/false, /*exhaust=*/true,
                                     /*inlet=*/false};
  spec.leakFanSpeedPct = 20.0f;
  uint32_t t = driveToLeaking(core, s, spec);
  OutputRequest out = core.update(s, t + 10);

  CHECK(core.state() == KitchenState::LEAKING);
  // The whole point: gas open AND ventilation running at the same time.
  CHECK(out.gasOpen == true);
  CHECK(out.registers.central == false);
  CHECK(out.registers.exhaust == true);
  CHECK(out.registers.inlet   == false);
  CHECK(out.fanSpeedPct == 20.0f);
}

TEST(leak_phase_fan_is_clamped_to_ceiling) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1");
  spec.leakFanSpeedPct = 100.0f;   // above VENT_SPEED_MAX_PCT
  uint32_t t = driveToLeaking(core, s, spec);
  OutputRequest out = core.update(s, t + 10);

  CHECK(out.fanSpeedPct == KitchenCore::clampFanSpeedPct(100.0f));
  CHECK(out.fanSpeedPct <= VENT_SPEED_MAX_PCT);
}

// A leak-phase vent request must never weaken the danger response: a trip
// still forces ALL registers open and the fan to 100%.
TEST(danger_during_vented_leak_still_forces_full_ventilation) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1");
  spec.leakRegisters   = RegisterSet{false, true, false};
  spec.leakFanSpeedPct = 20.0f;
  uint32_t t = driveToLeaking(core, s, spec);
  core.update(s, t + 10);

  s.localSensors[0].counts = SENSOR_THRESHOLD_DEFAULT_COUNTS;   // trip
  OutputRequest out = core.update(s, t + 20);

  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  CHECK(out.gasOpen == false);
  CHECK(out.registers.allOpen() == true);
  CHECK(out.fanSpeedPct == 100.0f);
}

// =============================================================================
// Equipment-test indicator relay — closed ONLY in bench mode (equipment-test
// selector AND WAITING), open in every other state.
// =============================================================================

TEST(equipment_test_relay_closes_only_in_bench_mode) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.isLeakTestRole = false;            // equipment-test selector
  OutputRequest out = core.update(s, 0);

  CHECK(core.state() == KitchenState::WAITING);
  CHECK(out.equipmentTestOn == true);
  CHECK(core.equipmentTestActive() == true);
  CHECK(out.gasOpen == false);         // still no gas in WAITING
}

TEST(equipment_test_relay_open_in_leak_test_role) {
  KitchenCore core;
  SensorState s = cleanSensors();      // leak-test role
  OutputRequest out = core.update(s, 0);
  CHECK(out.equipmentTestOn == false);
}

// A mid-run selector flip is inert (roleMisflip), so the relay must NOT close
// — otherwise the indicator would claim bench mode during a live leak.
TEST(equipment_test_relay_stays_open_on_midrun_selector_flip) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));

  s.isLeakTestRole = false;            // flip mid-leak
  OutputRequest out = core.update(s, t + 10);

  CHECK(core.state() == KitchenState::LEAKING);
  CHECK(core.roleMisflip() == true);
  CHECK(out.equipmentTestOn == false);
  CHECK(core.equipmentTestActive() == false);
}

// =============================================================================
// clearForMs — the all-clear hold progress the browser renders as a countdown.
// =============================================================================

TEST(clear_for_ms_advances_while_clear_and_resets_on_a_retrip) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));

  s.localSensors[0].counts = SENSOR_THRESHOLD_DEFAULT_COUNTS;   // trip
  core.update(s, t + 10);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  // Danger active this pass => not clear yet.
  CHECK(core.clearForMs(t + 10) == 0);

  s.localSensors[0].counts = 0;        // condition clears
  core.update(s, t + 20);
  CHECK(core.clearForMs(t + 1020) == 1000);   // 1 s after the clear instant

  // A re-trip restarts the hold — "5 min clear" means 5 CONTINUOUS minutes.
  s.localSensors[0].counts = SENSOR_THRESHOLD_DEFAULT_COUNTS;
  core.update(s, t + 2000);
  CHECK(core.clearForMs(t + 2000) == 0);
}

TEST(clear_required_ms_reports_the_builds_real_hold) {
  CHECK(KitchenCore::clearRequiredMs() == FULLY_VENT_MIN_HOLD_MS);
}
