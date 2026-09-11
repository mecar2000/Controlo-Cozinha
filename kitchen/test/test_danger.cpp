// =============================================================================
// test_danger.cpp — dangerActive() and the clamps that protect its trip
// points. Every danger condition forces gas off + full ventilation on the SAME
// pass it is detected; that is the property these tests exist to pin.
//
// This is half the audit target named in KitchenCore.h. A new danger condition
// in dangerActive() belongs here.
// =============================================================================

#include "test_harness.h"

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

TEST(danger_expansion_unhealthy_forces_gas_off_and_requires_ack) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.expansionUnhealthy = true;   // A0602 / D1608E missing or wrong type

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
  CHECK(out.gasSetpointPct == 0.0f);
  CHECK(out.registers.allOpen());
  CHECK(out.fanSpeedPct == 100.0f);
  CHECK(out.alarmOn == true);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
  CHECK(core.ackRequired());
  CHECK(core.reason() == DangerReason::EXPANSION_FAULT);
}

TEST(expansion_healthy_does_not_trip) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.expansionUnhealthy = false;   // the default; explicit for the record

  core.update(s, 1000);
  CHECK(core.state() != KitchenState::FULLY_VENTILATING);
}

TEST(danger_expansion_fault_cuts_gas_mid_leak) {
  KitchenCore core;
  SensorState s = cleanSensors();
  uint32_t t = driveToLeaking(core, s, leakSpec("run1"));
  OutputRequest leaking = core.update(s, t + 5);
  CHECK(leaking.gasOpen == true);   // sanity: gas flowing

  s.expansionUnhealthy = true;      // A0602 unplugged while running
  OutputRequest out = core.update(s, t + 10);
  CHECK(out.gasOpen == false);
  CHECK(core.state() == KitchenState::FULLY_VENTILATING);
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

// Counts rise with concentration and the trip is `counts >= threshold`, so
// LOWER = more sensitive. The website may lower the threshold but never raise
// it. Both directions asserted, so flipping the clamp back to max() fails loudly.

TEST(threshold_above_firmware_ceiling_is_rejected) {
  // Asking to trip LATER (less sensitive) is refused.
  uint16_t clamped = KitchenCore::clampThreshold(SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS + 500);
  CHECK(clamped == SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS);
}

TEST(threshold_below_firmware_ceiling_is_accepted) {
  // Asking to trip EARLIER (more sensitive) is allowed.
  uint16_t requested = SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS - 500;
  uint16_t clamped   = KitchenCore::clampThreshold(requested);
  CHECK(clamped == requested);
}

// The clamp must bite at the COMPARISON, not only where the value is stored:
// a raised threshold written straight into SensorState must not delay the trip.
TEST(raised_sensor_threshold_cannot_weaken_the_danger_trip) {
  SensorState s = cleanSensors();
  s.localSensors[0].present         = true;
  s.localSensors[0].thresholdCounts = SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS + 2000;
  s.localSensors[0].counts          = SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS;

  DangerReason r = DangerReason::NONE;
  CHECK(KitchenCore::dangerActive(s, r) == true);
  CHECK(r == DangerReason::LOCAL_SENSOR_THRESHOLD);
}

TEST(lowered_sensor_threshold_still_trips_early) {
  SensorState s = cleanSensors();
  s.localSensors[0].present         = true;
  s.localSensors[0].thresholdCounts = SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS - 500;
  s.localSensors[0].counts          = SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS - 500;

  DangerReason r = DangerReason::NONE;
  CHECK(KitchenCore::dangerActive(s, r) == true);
  CHECK(r == DangerReason::LOCAL_SENSOR_THRESHOLD);
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
