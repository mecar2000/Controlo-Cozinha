// =============================================================================
// test_registers.cpp — RegisterSequencer: the inlet-open delay.
//
// Rules under test (see KitchenCore.h):
//   - The delay applies to OPENING only; closing is simultaneous.
//   - The delay is a deferral, never a blocker — only the inlet coil waits.
//   - Inlet opening alone, or already open, actuates immediately.
//   - A request that changes before the deadline SUPERSEDES the pending action
//     rather than firing it late.
//
// Pure and clock-injected, so these need no KitchenCore at all.
// =============================================================================

#include "test_harness.h"

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
