#pragma once

// =============================================================================
// RunSpec.h — the closed set of run-spec primitives shared by KitchenCore
// and Protocol. No Arduino headers here — desktop-testable.
//
// This struct is what a validated+clamped `start` command becomes. Protocol
// builds it from JSON; KitchenCore only ever reads it.
// =============================================================================

#include <stdint.h>

// -----------------------------------------------------------------------------
// Ventilation register set — a 3-bit combination, not an enum of presets.
// Any combination is legal; FULLY_VENTILATING forces all three true regardless
// of what a spec asked for.
// -----------------------------------------------------------------------------
struct RegisterSet {
  bool central = false;
  bool exhaust = false;
  bool inlet   = false;

  bool anyOpen() const { return central || exhaust || inlet; }
  bool allOpen() const { return central && exhaust && inlet; }

  bool operator==(const RegisterSet& o) const {
    return central == o.central && exhaust == o.exhaust && inlet == o.inlet;
  }
  bool operator!=(const RegisterSet& o) const { return !(*this == o); }
};

// -----------------------------------------------------------------------------
// Sensor quorum stop — "stop when N sensors read at-or-above X counts".
//
// UNITS: counts, not %. The website sends % on the wire; Protocol converts
// once at the MQTT boundary (see countsToPct/pctToCounts and the conversion
// constants in Kitchen_Settings.h). The counts<->% mapping is firmware-owned
// because this condition cuts gas — the website cannot be the authority on
// what "4%" means. The `ack` echoes both the requested % and the interpreted
// counts so a miscalibration is visible from the browser.
//
// The threshold is the website's shared baseline, applied uniformly across
// whichever sensors are present. Per-sensor calibration offsets (compensating
// for wiring-distance current loss) are NOT here — they are firmware-owned,
// applied once at the sensor read boundary (see Sensors.cpp), so every
// reading this struct's threshold is compared against is already corrected.
//
// Comparison is AT-OR-ABOVE (>=), matching KitchenCore's danger check. See the
// boundary-convention note in Kitchen_Settings.h.
// -----------------------------------------------------------------------------
struct SensorQuorumStop {
  uint16_t thresholdCounts = 0;   // calibration-corrected counts; ignored when quorumCount == 0
  uint8_t  quorumCount     = 0;   // sensors required at-or-above thresholdCounts; 0 = unused
};

// -----------------------------------------------------------------------------
// Type-B stop-condition primitives for LEAKING / VENTILATING phases.
// A closed set on purpose — the website can only compose from these, never
// inject arbitrary logic.
// -----------------------------------------------------------------------------
struct StopCondition {
  uint32_t          maxDurationMs   = 0;      // 0 = no duration cap
  float              maxInventory_mL = 0.0f;   // 0 = no inventory cap (LEAKING phase only)
  SensorQuorumStop   sensorQuorum;             // quorumCount == 0 = unused

  // Conditions are OR'd: the phase ends on whichever fires first.
  // maxInventory_mL is only meaningful in LEAKING (gas flowing). Protocol
  // REJECTS a spec that sets it on holdStop or ventStop rather than silently
  // ignoring it — a spec asking for something impossible is a spec whose
  // author misunderstood the phase, and a silent no-op hides that.
};

// -----------------------------------------------------------------------------
// RunSpec — a validated, clamped run request. Everything here has already
// been through Protocol's validate/clamp pass by the time KitchenCore sees
// it. Values are the website's *request*; the FULLY_VENTILATING output block
// never reads this struct at all, and the LEAKING/VENTILATING blocks apply
// firmware ceilings (e.g. clampFanSpeedPct()) on top of it.
// -----------------------------------------------------------------------------
struct RunSpec {
  char     runId[32] = {0};

  // LEAKING phase
  float    gasSetpointPct = 0.0f;    // 0-100%, clamped to firmware ceiling
  StopCondition leakStop;

  // Ventilation DURING the leak. Gas flowing and dampers/fan running are not
  // mutually exclusive: a run may leak into a partially-vented room (one
  // damper cracked, fan at 20%) to study propagation under real kitchen
  // conditions. Both default to "sealed" (all registers closed, fan 0), so a
  // spec that omits them behaves exactly as before this was added.
  //
  // These are a REQUEST, not an override: dangerActive() still forces
  // FULLY_VENTILATING (all three registers, 100% fan) on any trip, and
  // leakFanSpeedPct is clamped by clampFanSpeedPct() like every other fan
  // figure. Venting during a leak can only ever make the room safer.
  RegisterSet leakRegisters;
  float       leakFanSpeedPct = 0.0f;   // 0-100%, clamped to VENT_SPEED_MAX_PCT

  // HOLD phase — gas off, fans off, propagation watched undisturbed. This is
  // the actual measurement phase of a leak-propagation experiment, so it has
  // a real duration; without one it would be instantaneous.
  //
  // maxDurationMs is clamped by Protocol against HOLD_MAX_DURATION_MS. A spec
  // that omits holdStop entirely falls back to that ceiling, NOT to zero:
  // absent means "the maximum safe hold", not "skip the measurement". HOLD is
  // the one phase with gas already delivered and fans OFF, so an unbounded
  // hold must not be reachable through a malformed or partial spec.
  StopCondition holdStop;

  // VENTILATING phase
  RegisterSet ventRegisters;
  float       fanSpeedPct = 0.0f;    // 0-100%, clamped to VENT_SPEED_MAX_PCT
  StopCondition ventStop;

  bool valid = false;                // set by Protocol after validate+clamp
};
