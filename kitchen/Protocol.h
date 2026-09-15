#ifndef KITCHEN_PROTOCOL_H
#define KITCHEN_PROTOCOL_H

// =============================================================================
// Protocol.h — the MQTT/JSON boundary. The ONLY file that turns wire bytes into
// a RunSpec and RunSpec/state into wire bytes. KitchenCore never sees JSON;
// Comms never sees a RunSpec.
//
// Owns, per docs/implementation-plan.md "Protocol layer":
//   - parse + validate + clamp of `start` specs  (sets RunSpec.valid)
//   - the quorum threshold %<->counts conversion (firmware-owned)
//   - holdStop fallback to HOLD_MAX_DURATION_MS when the website omits it
//   - REJECTING maxInventory_mL on holdStop/ventStop (not silently ignoring)
//   - building the retained `state`, the `ack` echo (+ rejection reason), the
//     `alarm` payload, and the `sensors/power` payload
//   - ignoring retained `cmd` messages (a broker replay must not start a leak)
// =============================================================================

#include <stdint.h>
#include <stddef.h>        // size_t (used by the outbound builder signatures)
#include "RunSpec.h"
#include "KitchenCore.h"   // KitchenState, DangerReason, StartRejectReason

// ---------------------------------------------------------------------------
enum class CmdKind : uint8_t { NONE = 0, START, CONFIRM, STOP, ACK, BAD_JSON };

struct ParsedCommand {
  CmdKind kind = CmdKind::NONE;
  RunSpec spec;                 // valid only when kind == START (spec.valid set by parse)
  char    runId[32] = {0};      // START and CONFIRM
  // When kind == START and spec.valid == false, why:
  const char* rejectDetail = nullptr;
};

// Parse one `cmd` payload. `retained` is the MQTT retained flag — a retained
// cmd is ALWAYS dropped (kind stays NONE) regardless of content.
//   requestedPctOut / interpretedCountsOut — for a START, the raw quorum % the
//   website asked for and the counts Protocol interpreted it as, so kitchen.ino
//   can echo both in the ack. Untouched for other kinds.
ParsedCommand protocolParseCommand(const char* json, bool retained,
                                   float* requestedPctOut,
                                   uint16_t* interpretedCountsOut);

// %<->counts on the firmware-owned 4-20 mA scale (see Kitchen_Settings.h).
uint16_t protocolPctToCounts(float pct);
float    protocolCountsToPct(uint16_t counts);

// ---------------------------------------------------------------------------
// config/set — the per-sensor threshold table.
//
// Wire shape:  {"thresholds":[{"sensor":0,"thresholdPct":1.5}, ...]}
//
// The website sends %; the firmware owns the mapping to counts AND the safety
// ceiling. Each entry is converted once here and clamped by
// KitchenCore::clampThreshold(), so a website value can only make a sensor
// MORE sensitive. The clamp is re-applied at the comparison in dangerActive()
// as well — this one is for the honest echo back to the browser.
// ---------------------------------------------------------------------------
#define PROTOCOL_MAX_CONFIG_ENTRIES  KITCHEN_MAX_LOCAL_SENSORS

struct ParsedConfigEntry {
  int      sensorIndex      = -1;
  float    requestedPct     = 0.0f;   // as the website asked
  uint16_t requestedCounts  = 0;      // converted, pre-clamp
  uint16_t effectiveCounts  = 0;      // post-clamp — what the firmware will use
  bool     clamped          = false;  // true if the ceiling overrode the request
};

struct ParsedConfig {
  bool              ok = false;         // false => malformed, nothing applied
  const char*       rejectDetail = nullptr;
  int               count = 0;
  ParsedConfigEntry entries[PROTOCOL_MAX_CONFIG_ENTRIES];
};

// Parse one `config/set` payload. Rejects (ok=false) on malformed JSON, an
// out-of-range sensor index, or a negative/absent thresholdPct — never applies
// a partial table.
ParsedConfig protocolParseConfig(const char* json);

// Build the `config/ack` echo: every entry with the % requested and the counts
// actually in force, so a clamp or a miscalibration is visible in the browser
// rather than silent.
size_t protocolBuildConfigAck(char* out, size_t cap, const ParsedConfig& cfg);

// ---------------------------------------------------------------------------
// Outbound payload builders. Each writes into `out` (size `cap`) and returns
// the length written, or 0 on overflow.
// ---------------------------------------------------------------------------
size_t protocolBuildState(char* out, size_t cap,
                          KitchenState state, bool isLeakTestRole,
                          uint32_t elapsedMs, float deliveredInventory_mL,
                          bool ackRequired, bool acked, DangerReason reason,
                          bool sensorsOn,
                          float fanSpeedPct, const RegisterSet& registers,
                          float flowRate_mLps);

size_t protocolBuildAck(char* out, size_t cap,
                        const char* runId, bool accepted,
                        StartRejectReason rej,
                        const RunSpec& interpretedSpec,
                        float requestedQuorumPct, uint16_t interpretedQuorumCounts);

size_t protocolBuildAlarm(char* out, size_t cap,
                          bool active, DangerReason reason, const char* runId);

size_t protocolBuildSensorsPower(char* out, size_t cap, bool on);

// One local H2 sensor sample, DataAcquisition-compatible single-message shape
// (matches DataAcquisition/CM7/CM7.ino's publishCurrent() exactly, so the
// existing ingest needs no changes): {"pin":N,"type":"current","raw_ma":X,"ts":T}.
// Hand-built with snprintf, NOT ArduinoJson — unlike the builders above, this
// one runs at sensor-publish rate (SENSOR_PUBLISH_INTERVAL_MS, Kitchen_Settings.h),
// not on a change-gated/heartbeat cadence, so a per-call JSON-library build is
// exactly the cost this path exists to avoid.
size_t protocolBuildSensorSample(char* out, size_t cap,
                                 int encodedPin, float rawMa, uint64_t tsMs);

// Human-readable DangerReason, for the alarm `description` / state `reason`.
const char* protocolReasonName(DangerReason r);

#endif // KITCHEN_PROTOCOL_H
