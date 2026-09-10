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
// Regression: with the clock based at LEAKING entry, the SENSOR_WARMUP_MS
// warm-up gate consumed the whole phase and a 5 s leak spec delivered ZERO gas.
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
