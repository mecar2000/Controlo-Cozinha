// =============================================================================
// test_sensors.cpp — sensor power rules, the role selector, and the
// gas-may-be-present lamp flag.
//
// LOCAL and REMOTE sensor power are NOT lockstep (supersedes plan item 18):
//   LOCAL  : LEAKING (leak-test) OR equipment-test role
//   REMOTE : leak-test leak run only (LEAKING..purge); never equipment-test
//
// The role selector only ACTS in WAITING; anywhere else a flip is inert and
// merely raises roleMisflip() for the LED layer to blink.
// =============================================================================

#include "test_harness.h"

// =============================================================================
// Role selector: only acts in WAITING; inert (log + LED blink) everywhere else
// =============================================================================

TEST(selector_flip_mid_leak_is_inert) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));

  s.isLeakTestRole = false;   // physically flipped away from leak-test mid-run
  core.update(s, t + 10);
  CHECK(core.state() == KitchenState::LEAKING);   // run continues, no abort
  CHECK(!core.ackRequired());
  CHECK(core.roleMisflip());                      // flagged for LED + log
}

TEST(roleMisflip_clears_when_selector_returns_or_state_reaches_waiting) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));

  s.isLeakTestRole = false;
  core.update(s, t + 10);
  CHECK(core.roleMisflip());

  s.isLeakTestRole = true;                        // flipped back
  core.update(s, t + 20);
  CHECK(!core.roleMisflip());
}

TEST(equipment_test_in_waiting_drives_flowmeter_full_open) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.isLeakTestRole = false;                       // equipment-test, still WAITING
  OutputRequest out = core.update(s, 1000);
  CHECK(core.state() == KitchenState::WAITING);
  CHECK(!core.roleMisflip());                     // WAITING => it DOES act
  CHECK(out.gasOpen == false);                    // supply relay stays cut
  CHECK(out.flowSetpointTest == true);
  CHECK(out.gasSetpointPct == 100.0f);            // meter driven full-scale
}

// =============================================================================
// Sensor power — LOCAL and REMOTE split (supersedes the old lockstep)
// =============================================================================

TEST(waiting_leaktest_both_sensor_powers_off) {
  KitchenCore core;
  SensorState s = cleanSensors();
  OutputRequest out = core.update(s, 0);
  CHECK(out.localSensorsOn  == false);
  CHECK(out.remoteSensorsOn == false);
}

TEST(equipment_test_powers_local_but_not_remote) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.isLeakTestRole = false;                 // equipment-test
  OutputRequest out = core.update(s, 1000);
  CHECK(out.localSensorsOn  == true);       // local on immediately, no warm-up
  CHECK(out.remoteSensorsOn == false);      // remote NEVER in equipment-test
  CHECK(core.remoteSensorsOn() == false);
}

TEST(leaktest_leak_run_powers_both) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));
  OutputRequest out = core.update(s, t + 5);
  CHECK(core.state() == KitchenState::LEAKING);
  CHECK(out.localSensorsOn  == true);
  CHECK(out.remoteSensorsOn == true);
}

TEST(remote_stays_on_through_hold_vent_and_purge) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1", /*durationMs=*/100, /*holdMs=*/100);
  spec.ventStop.maxDurationMs = 100;
  uint32_t t = driveToLeaking(core, s, spec);

  core.update(s, t + 200);   // -> HOLD
  CHECK(core.state() == KitchenState::HOLD);
  CHECK(core.update(s, t + 205).remoteSensorsOn == true);

  core.update(s, t + 300);   // -> VENTILATING
  CHECK(core.state() == KitchenState::VENTILATING);
  CHECK(core.update(s, t + 305).remoteSensorsOn == true);

  core.update(s, t + 400);   // -> FULLY_VENTILATING (routine purge)
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  CHECK(core.update(s, t + 405).remoteSensorsOn == true);
}

TEST(remote_off_once_run_returns_to_waiting) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));
  core.stop(t + 10);                             // routine purge
  core.update(s, t + 10);
  // Still purging 1 ms before the hold completes -> remote still on.
  CHECK(core.update(s, t + 10 + FULLY_VENT_MIN_HOLD_MS - 1).remoteSensorsOn == true);
  // Hold met -> WAITING -> remote off.
  OutputRequest done = core.update(s, t + 10 + FULLY_VENT_MIN_HOLD_MS);
  CHECK(core.state() == KitchenState::WAITING);
  CHECK(done.remoteSensorsOn == false);
}

// A mid-leak selector flip is inert for SENSOR POWER too, not just for the
// state machine. remoteOn() gates on STATE ALONE — deliberately, since a leak
// run can only START in leak-test role and the flip no longer aborts. Cutting
// remote power here would blind the DAQ mid-run over a switch the core has
// already decided to ignore.
TEST(selector_flip_mid_leak_does_not_disturb_sensor_power) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));
  CHECK(core.update(s, t + 5).remoteSensorsOn == true);

  s.isLeakTestRole = false;   // flipped mid-leak — inert for the state machine
  OutputRequest out = core.update(s, t + 10);
  CHECK(core.state() == KitchenState::LEAKING);   // run unaffected
  CHECK(out.remoteSensorsOn == true);    // still on — state, not role, decides
  CHECK(out.localSensorsOn  == true);    // local was already on for the run
  CHECK(core.roleMisflip());             // flagged for LED + log
}

TEST(local_70s_warmup_gates_gas_not_remote) {
  KitchenCore core;
  SensorState s = cleanSensors();
  core.start(leakSpec("run1", /*durationMs=*/200000), s, 0);
  core.confirm("run1", 0);

  // 1 ms in: WARMING_UP — gas shut, but sensor-power commands already out so
  // the remote DAQ can run its own warm-up in parallel.
  OutputRequest w = core.update(s, 1);
  CHECK(core.state() == KitchenState::WARMING_UP);
  CHECK(w.gasOpen == false);
  CHECK(w.localSensorsOn  == true);
  CHECK(w.remoteSensorsOn == true);

  // Just before 70 s: still gated.
  CHECK(core.update(s, SENSOR_WARMUP_MS - 1).gasOpen == false);
  // At 70 s: gate releases.
  CHECK(core.update(s, SENSOR_WARMUP_MS).gasOpen == true);
}

// REGRESSION: equipment-test powers the local sensors with no warm-up gate (no
// gas can flow in WAITING, so none is needed THERE). The old confirm() gated on
// `if (!localSensorsOn_)` — "did I just switch them on" — so a leak run started
// straight after a brief equipment-test saw sensors already on, skipped the
// gate entirely, and opened the gas valve onto sensors that were seconds old
// and still blind. The gate is now elapsed POWERED TIME, not a flag.
TEST(equipment_test_does_not_bypass_the_warmup_gate) {
  KitchenCore core;
  SensorState s = cleanSensors();

  // Flip to equipment-test in WAITING: sensors come on, cold.
  s.isLeakTestRole = false;
  CHECK(core.update(s, 0).localSensorsOn == true);

  // Flip straight back and start a leak run 1 s later — sensors are ON but
  // have only been powered 1 s.
  s.isLeakTestRole = true;
  core.start(leakSpec("run1", /*durationMs=*/200000), s, 1000);
  core.confirm("run1", 1000);

  CHECK(core.state() == KitchenState::WARMING_UP);          // gated, not skipped
  CHECK(core.update(s, 1000).gasOpen == false);
  // Still gated just before the sensors are 70 s old (not 70 s after confirm).
  CHECK(core.update(s, SENSOR_WARMUP_MS - 1).gasOpen == false);
  CHECK(core.state() == KitchenState::WARMING_UP);
  // Warm at last: 70 s after they were POWERED.
  CHECK(core.update(s, SENSOR_WARMUP_MS).gasOpen == true);
  CHECK(core.state() == KitchenState::LEAKING);
}

// The flip side, and the reason WARMING_UP is skippable at all: sensors that
// have been powered for the full warm-up already go straight to LEAKING with
// no second gate (plan item 7).
TEST(already_warm_sensors_skip_warming_up_entirely) {
  KitchenCore core;
  SensorState s = cleanSensors();

  // Equipment-test holds the sensors on well past the warm-up.
  s.isLeakTestRole = false;
  core.update(s, 0);
  core.update(s, SENSOR_WARMUP_MS + 1000);

  s.isLeakTestRole = true;
  uint32_t t = SENSOR_WARMUP_MS + 1000;
  core.start(leakSpec("run1", /*durationMs=*/5000), s, t);
  core.confirm("run1", t);

  CHECK(core.state() == KitchenState::LEAKING);   // no gate — already warm
  CHECK(core.update(s, t).gasOpen == true);       // gas flows immediately
}

// Powering the sensors down discards the warm-up: the idle timeout in WAITING
// turns them off, so the next run must gate again rather than treating the
// stale "was warm once" as still valid.
TEST(idle_power_down_discards_warmup) {
  KitchenCore core;
  SensorState s = cleanSensors();

  // Warm them via equipment-test, then return to leak-test and let WAITING
  // idle them back off.
  s.isLeakTestRole = false;
  core.update(s, 0);
  s.isLeakTestRole = true;
  uint32_t off = SENSOR_IDLE_TIMEOUT_MS + 1;
  CHECK(core.update(s, off).localSensorsOn == false);

  core.start(leakSpec("run1", /*durationMs=*/200000), s, off);
  core.confirm("run1", off);
  CHECK(core.state() == KitchenState::WARMING_UP);   // cold again, gated
  CHECK(core.update(s, off).gasOpen == false);
}

// =============================================================================
// gasMayBePresent — state-based lamp flag, true in every state but WAITING
// =============================================================================

TEST(gas_may_be_present_false_in_waiting) {
  KitchenCore core;
  SensorState s = cleanSensors();
  OutputRequest out = core.update(s, 0);
  CHECK(core.state() == KitchenState::WAITING);
  CHECK(out.gasMayBePresent == false);
}

TEST(gas_may_be_present_true_in_armed) {
  KitchenCore core;
  SensorState s = cleanSensors();
  core.start(leakSpec("run1"), s, 0);
  OutputRequest out = core.update(s, 1);
  CHECK(core.state() == KitchenState::ARMED);
  CHECK(out.gasMayBePresent == true);
}

TEST(gas_may_be_present_true_through_leak_hold_vent_and_purge) {
  KitchenCore core;
  SensorState s = cleanSensors();
  RunSpec spec = leakSpec("run1", /*durationMs=*/100, /*holdMs=*/100);
  spec.ventStop.maxDurationMs = 100;
  uint32_t t = driveToLeaking(core, s, spec);

  OutputRequest leak = core.update(s, t + 5);
  CHECK(core.state() == KitchenState::LEAKING);
  CHECK(leak.gasMayBePresent == true);

  OutputRequest hold = core.update(s, t + 200);
  CHECK(core.state() == KitchenState::HOLD);
  CHECK(hold.gasMayBePresent == true);

  OutputRequest vent = core.update(s, t + 300);
  CHECK(core.state() == KitchenState::VENTILATING);
  CHECK(vent.gasMayBePresent == true);

  OutputRequest purge = core.update(s, t + 400);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  CHECK(purge.gasMayBePresent == true);
}

TEST(gas_may_be_present_true_during_danger_purge) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.estopPressed = true;
  OutputRequest out = core.update(s, 0);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  CHECK(out.gasMayBePresent == true);
}

TEST(gas_may_be_present_clears_when_core_returns_to_waiting) {
  KitchenCore core;
  SensorState s = cleanSensors();
  core.start(leakSpec("run1"), s, 0);
  // ARM timeout drops back to WAITING with no gas ever delivered.
  OutputRequest out = core.update(s, ARM_TIMEOUT_MS + 1);
  CHECK(core.state() == KitchenState::WAITING);
  CHECK(out.gasMayBePresent == false);
}
