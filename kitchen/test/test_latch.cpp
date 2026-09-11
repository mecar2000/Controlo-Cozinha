// =============================================================================
// test_latch.cpp — the FULLY_VENTILATING exit rules: who must acknowledge,
// how long the all-clear must hold, and what resets what. This is the
// replacement for the old SafetyLatch object, so these tests are the whole
// specification of "when may the purge end".
//
// The rules under test (see KitchenCore.h):
//   ackRequired_ PROMOTES but never DEMOTES
//   acked_ survives a re-entry while already latched
//   "5 min clear" means five CONTINUOUS minutes — a re-fire restarts the clock
//   an ack is not a shortcut past the hold; the hold is not a shortcut past the ack
// =============================================================================

#include "test_harness.h"

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
