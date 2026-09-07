#pragma once

// =============================================================================
// RegisterSequencer.h — pure: maps (desired register set, now_ms) -> coil
// states, applying the inlet-open delay. Holds the one pending-deadline field
// this needs. Takes time as a parameter so tests drive it with a fake clock
// instead of waiting a real second. No Arduino headers — desktop-testable.
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

#include <stdint.h>
#include "RunSpec.h"
#include "Kitchen_Settings.h"

// -----------------------------------------------------------------------------
// CoilStates — what Outputs should actually drive right now, per register.
// -----------------------------------------------------------------------------
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
