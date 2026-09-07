#include "RegisterSequencer.h"

// =============================================================================
// RegisterSequencer.cpp — see RegisterSequencer.h for the contract.
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
