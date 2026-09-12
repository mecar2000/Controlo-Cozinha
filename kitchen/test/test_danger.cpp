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
  CHECK(core.reason() == DangerReason::LOCAL_SENSOR_THRESHOLD);
}

// Boundary convention: AT-OR-ABOVE. A reading exactly at the threshold TRIPS.
// Both sides checked so a future change from >= back to > fails loudly.
// state() is the only assertion that actually distinguishes trip vs no-trip
// here — gasOpen is false either way since WAITING drives no gas regardless
// (testproblems.txt 3.2: the former gasOpen checks read like real assertions
// but carried no information).
TEST(threshold_boundary_is_at_or_above) {
  SensorState s = cleanSensors();

  s.localSensors[0].counts = SENSOR_THRESHOLD_DEFAULT_COUNTS;   // exactly at
  KitchenCore atCore;
  atCore.update(s, 1000);
  CHECK(atCore.state() == KitchenState::FULLY_VENTILATING);   // trips

  s.localSensors[0].counts = SENSOR_THRESHOLD_DEFAULT_COUNTS - 1;   // just below
  KitchenCore belowCore;
  belowCore.update(s, 1000);
  CHECK(belowCore.state() == KitchenState::WAITING);   // does not trip
}

TEST(danger_flow_over_limit_sustained_forces_gas_off) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.flowOverLimitSustained = true;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
  CHECK(out.registers.allOpen());
  CHECK(core.reason() == DangerReason::FLOW_OVER_LIMIT);
}

// Role-gate carve-out (testproblems.txt 2.3): FLOW_OVER_LIMIT is guarded by
// `s.isLeakTestRole &&` in dangerActive() — a deliberate safety carve-out
// (flow limits are meaningless outside a leak-test run) that had zero
// pinning either way.
TEST(flow_over_limit_does_not_trip_in_equipment_test) {
  SensorState s = cleanSensors();
  s.isLeakTestRole         = false;   // equipment-test
  s.flowOverLimitSustained = true;

  DangerReason r = DangerReason::NONE;
  CHECK(KitchenCore::dangerActive(s, r) == false);
}

TEST(danger_inventory_cap_exceeded_forces_gas_off) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.deliveredInventory_mL = INVENTORY_CAP_ML + 1.0f;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
  CHECK(core.reason() == DangerReason::INVENTORY_CAP_EXCEEDED);
}

// Role-gate carve-out (testproblems.txt 2.3): INVENTORY_CAP_EXCEEDED is
// likewise guarded by `s.isLeakTestRole &&` — an inventory cap is meaningless
// outside a leak-test run, since equipment-test never opens the gas relay.
TEST(inventory_cap_exceeded_does_not_trip_in_equipment_test) {
  SensorState s = cleanSensors();
  s.isLeakTestRole        = false;   // equipment-test
  s.deliveredInventory_mL = INVENTORY_CAP_ML + 1.0f;

  DangerReason r = DangerReason::NONE;
  CHECK(KitchenCore::dangerActive(s, r) == false);
}

TEST(danger_peer_alarm_forces_gas_off_even_in_equipment_test) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.isLeakTestRole = false;   // equipment-test
  s.peerAlarmActive = true;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
  CHECK(out.registers.allOpen());
  CHECK(core.reason() == DangerReason::PEER_ALARM);
}

TEST(danger_permit_present_and_false_forces_gas_off) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.permitPresent = true;
  s.permitValue    = false;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
  CHECK(core.reason() == DangerReason::PERMIT_DENIED);
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
  CHECK(core.reason() == DangerReason::ESTOP);
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

// (expansion_healthy_does_not_trip removed — testproblems.txt 3.1: it only
// asserted that cleanSensors() is clean, already established by every other
// test in this file running with expansionUnhealthy == false by default.)

// Gap 2.2: EXTERNAL_TRIP had no test at all. Reserved/not wired to real
// hardware yet, but dangerActive() already has the branch and its own header
// comment says a new danger condition belongs in this file.
TEST(danger_external_trip_forces_gas_off) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.externalTripActive = true;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
  CHECK(out.registers.allOpen());
  CHECK(core.reason() == DangerReason::EXTERNAL_TRIP);
}

TEST(danger_local_sensor_silent_over_10s_when_expected_on_trips) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.localSensors[0].expectedOn = true;
  s.localSensors[0].stale      = true;

  OutputRequest out = core.update(s, 1000);
  CHECK(out.gasOpen == false);
  CHECK(core.reason() == DangerReason::LOCAL_SENSOR_STALE);
}

TEST(stale_while_intentionally_off_does_not_trip) {
  KitchenCore core;
  SensorState s = cleanSensors();
  s.localSensors[0].expectedOn = false;   // sensors intentionally off
  s.localSensors[0].stale      = true;

  core.update(s, 1000);
  CHECK(core.state() != KitchenState::FULLY_VENTILATING);
}

// Parameterized "each danger condition cuts gas mid-leak" test
// (testproblems.txt 3.3): merges the former danger_expansion_fault_cuts_gas_
// mid_leak and run_requesting_gas_while_danger_holds_still_gets_no_gas, which
// were identical in structure and assertions apart from the trigger field.
// One case per condition also closes gap 2.1: reason() is now checked on
// every one of dangerActive()'s branches when tripped mid-run, not just two.
struct MidLeakDangerCase {
  const char*  name;
  void       (*arm)(SensorState&);
  DangerReason expectedReason;
};

static void armEstop(SensorState& s)            { s.estopPressed = true; }
static void armExpansionFault(SensorState& s)    { s.expansionUnhealthy = true; }
static void armLocalThreshold(SensorState& s)    { s.localSensors[0].counts = SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS + 1000; }
static void armLocalStale(SensorState& s)        { s.localSensors[0].stale = true; }
static void armFlowOverLimit(SensorState& s)     { s.flowOverLimitSustained = true; }
static void armInventoryCap(SensorState& s)      { s.deliveredInventory_mL = INVENTORY_CAP_ML + 1.0f; }
static void armPeerAlarm(SensorState& s)         { s.peerAlarmActive = true; }
static void armPermitDenied(SensorState& s)      { s.permitPresent = true; s.permitValue = false; }
static void armExternalTrip(SensorState& s)      { s.externalTripActive = true; }

static const MidLeakDangerCase kMidLeakDangerCases[] = {
  {"estop",             armEstop,            DangerReason::ESTOP},
  {"expansion_fault",   armExpansionFault,   DangerReason::EXPANSION_FAULT},
  {"local_threshold",   armLocalThreshold,   DangerReason::LOCAL_SENSOR_THRESHOLD},
  {"local_stale",       armLocalStale,       DangerReason::LOCAL_SENSOR_STALE},
  {"flow_over_limit",   armFlowOverLimit,    DangerReason::FLOW_OVER_LIMIT},
  {"inventory_cap",     armInventoryCap,     DangerReason::INVENTORY_CAP_EXCEEDED},
  {"peer_alarm",        armPeerAlarm,        DangerReason::PEER_ALARM},
  {"permit_denied",     armPermitDenied,     DangerReason::PERMIT_DENIED},
  {"external_trip",     armExternalTrip,     DangerReason::EXTERNAL_TRIP},
};

// Gap 2.1 (second half): dangerActive()'s evaluation order is fixed and
// first-match-wins, but nothing pinned that ordering — a reordering that made
// e-stop report PEER_ALARM (say) would previously pass the entire suite.
// Arms two conditions from adjacent points in the list simultaneously and
// checks the earlier one in source order is what gets reported.
TEST(estop_takes_priority_over_expansion_fault_when_both_active) {
  SensorState s = cleanSensors();
  s.estopPressed        = true;   // checked before expansion fault in dangerActive()
  s.expansionUnhealthy  = true;

  DangerReason r = DangerReason::NONE;
  CHECK(KitchenCore::dangerActive(s, r) == true);
  CHECK(r == DangerReason::ESTOP);
}

TEST(local_sensor_threshold_takes_priority_over_estop_when_both_active) {
  SensorState s = cleanSensors();
  s.localSensors[0].counts = SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS + 1000;   // checked FIRST
  s.estopPressed           = true;

  DangerReason r = DangerReason::NONE;
  CHECK(KitchenCore::dangerActive(s, r) == true);
  CHECK(r == DangerReason::LOCAL_SENSOR_THRESHOLD);
}

TEST(each_danger_condition_cuts_gas_mid_leak) {
  for (const auto& c : kMidLeakDangerCases) {
    KitchenCore core;
    SensorState s = cleanSensors();
    uint32_t t = driveToLeaking(core, s, leakSpec("run1"));
    OutputRequest leaking = core.update(s, t + 5);
    CHECK(leaking.gasOpen == true);   // sanity: gas really is flowing

    c.arm(s);
    OutputRequest out = core.update(s, t + 10);
    if (out.gasOpen != false) {
      printf("  (case: %s)\n", c.name);
    }
    CHECK(out.gasOpen == false);
    CHECK(core.state() == KitchenState::FULLY_VENTILATING);
    CHECK(core.reason() == c.expectedReason);
  }
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
// a raised threshold written straight into SensorState must not delay the
// trip. Made independent of the default fixture (testproblems.txt section 1):
// threshold strictly ABOVE the ceiling, counts strictly BELOW the requested
// threshold but AT the ceiling — this fails loudly if clampThreshold() is
// ever stubbed out to return its argument unchanged.
TEST(raised_sensor_threshold_cannot_weaken_the_danger_trip) {
  SensorState s = cleanSensors();
  s.localSensors[0].present         = true;
  s.localSensors[0].thresholdCounts = SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS + 2000;
  s.localSensors[0].counts          = SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS + 1000;

  DangerReason r = DangerReason::NONE;
  CHECK(KitchenCore::dangerActive(s, r) == true);
  CHECK(r == DangerReason::LOCAL_SENSOR_THRESHOLD);
}

// Distinct from the boundary test above: a LOWERED (more sensitive) request is
// passed through unclamped, so the trip point actually moves with the
// request rather than the clamp silently rewriting every value to the
// ceiling. Uses SENSOR_THRESHOLD_DEFAULT_COUNTS (now < MAX) as the requested
// value specifically so this is NOT the same boundary as
// threshold_boundary_is_at_or_above (which uses the ceiling).
TEST(lowered_sensor_threshold_still_trips_early) {
  SensorState s = cleanSensors();
  s.localSensors[0].present         = true;
  s.localSensors[0].thresholdCounts = SENSOR_THRESHOLD_DEFAULT_COUNTS;
  s.localSensors[0].counts          = SENSOR_THRESHOLD_DEFAULT_COUNTS;

  DangerReason r = DangerReason::NONE;
  CHECK(KitchenCore::dangerActive(s, r) == true);
  CHECK(r == DangerReason::LOCAL_SENSOR_THRESHOLD);

  // And one below that lowered value must NOT trip — proves the request,
  // not just the ceiling, controls where the trip actually sits.
  SensorState s2 = cleanSensors();
  s2.localSensors[0].thresholdCounts = SENSOR_THRESHOLD_DEFAULT_COUNTS;
  s2.localSensors[0].counts          = SENSOR_THRESHOLD_DEFAULT_COUNTS - 1;
  DangerReason r2 = DangerReason::NONE;
  CHECK(KitchenCore::dangerActive(s2, r2) == false);
}

// Direct unit tests for clampFanSpeedPct() — previously only exercised
// indirectly through ventilating_fan_speed_is_clamped_to_ceiling, which never
// covered the negative branch at all (testproblems.txt section 1).
TEST(clampFanSpeedPct_negative_clamps_to_zero) {
  CHECK(KitchenCore::clampFanSpeedPct(-5.0f) == 0.0f);
}

TEST(clampFanSpeedPct_above_ceiling_clamps_to_ceiling) {
  CHECK(KitchenCore::clampFanSpeedPct(VENT_SPEED_MAX_PCT + 50.0f) == VENT_SPEED_MAX_PCT);
}

TEST(clampFanSpeedPct_within_range_passes_through) {
  CHECK(KitchenCore::clampFanSpeedPct(30.0f) == 30.0f);
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
