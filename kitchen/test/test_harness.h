#pragma once

// =============================================================================
// test_harness.h — the CHECK/TEST macros and the shared fixtures every test
// file uses. No external test framework: TEST() blocks self-register through a
// static-init constructor, so a test file only has to be LINKED to run. There
// is no registry to update and no list to keep in sync.
//
// Build/run — link test_main.cpp plus whichever area files you want, and
// KitchenCore.cpp:
//
//   export PATH="/c/msys64/ucrt64/bin:$PATH"
//   g++ -std=c++17 -DKITCHEN_ALLOW_PLACEHOLDER_SCALES -I kitchen
//       kitchen/test/test_*.cpp kitchen/KitchenCore.cpp -o test_safety
//       && ./test_safety
//
// Run ONE area — same command with just that file (test_main.cpp is always
// needed, it owns the counters and main()):
//
//   g++ -std=c++17 -DKITCHEN_ALLOW_PLACEHOLDER_SCALES -I kitchen
//       kitchen/test/test_main.cpp kitchen/test/test_danger.cpp
//       kitchen/KitchenCore.cpp -o test_danger && ./test_danger
//
// (Line continuations omitted above: a trailing backslash inside a // comment
// is a -Wcomment warning. Join the lines when you paste.)
//
// KITCHEN_ALLOW_PLACEHOLDER_SCALES is required until the analog-scale
// constants are measured on the bench (see Kitchen_Settings.h). These tests
// drive counts directly and never exercise a physical-unit conversion, so
// placeholder scales are harmless here — but a FIRMWARE build without real
// values is refused at compile time on purpose.
// =============================================================================

#include <cstdio>
#include <cstring>
#include "KitchenCore.h"   // also provides RegisterSequencer / CoilStates

// Counters live in test_main.cpp — one definition for the whole link, so the
// tally is across every area file rather than per translation unit.
extern int g_failures;
extern int g_total;

#define CHECK(cond) do { \
  g_total++; \
  if (!(cond)) { \
    g_failures++; \
    printf("  FAIL: %s (%s:%d)\n", #cond, __FILE__, __LINE__); \
  } \
} while (0)

// Each TEST() defines a function and a file-scope object whose constructor
// runs it at static-init time, before main(). Marked `static` so two area
// files may reuse a test name without colliding at link time.
#define TEST(name) static void name(); \
  struct name##_runner { name##_runner() { printf("[TEST] %s\n", #name); name(); } } name##_instance; \
  static void name()

// ---------------------------------------------------------------------------
// Shared fixtures. `inline` so every area file can include this header without
// a duplicate-symbol error at link time.
// ---------------------------------------------------------------------------

// One present, powered, quiet sensor in leak-test role — the "nothing wrong"
// baseline every test starts from and then breaks one thing in.
inline SensorState cleanSensors() {
  SensorState s;
  s.localSensorCount = 1;
  s.localSensors[0].present          = true;
  s.localSensors[0].counts           = 0;
  s.localSensors[0].thresholdCounts  = SENSOR_THRESHOLD_DEFAULT_COUNTS;
  s.localSensors[0].stale            = false;
  s.localSensors[0].expectedOn       = true;
  s.isLeakTestRole = true;
  return s;
}

// A valid leak run spec. HOLD is a real measurement phase, so a spec with no
// holdStop would stall there forever — Protocol supplies HOLD_MAX_DURATION_MS
// when the website omits it; tests set it explicitly.
inline RunSpec leakSpec(const char* runId, uint32_t durationMs = 5000,
                        uint32_t holdMs = 2000) {
  RunSpec spec;
  strncpy(spec.runId, runId, sizeof(spec.runId) - 1);
  spec.gasSetpointPct         = 50.0f;
  spec.leakStop.maxDurationMs = durationMs;
  spec.holdStop.maxDurationMs = holdMs;
  spec.ventRegisters          = RegisterSet{true, true, true};
  spec.fanSpeedPct            = 60.0f;
  spec.ventStop.maxDurationMs = 5000;
  spec.valid = true;
  return spec;
}

// Drive a fresh core to LEAKING with warm-up already elapsed. Returns the
// timestamp at which LEAKING sequencing actually began.
inline uint32_t driveToLeaking(KitchenCore& core, SensorState& s,
                               const RunSpec& spec) {
  core.start(spec, s, 0);
  core.confirm(spec.runId, 0);
  core.update(s, SENSOR_WARMUP_MS);   // warm-up gate releases
  return SENSOR_WARMUP_MS;
}
