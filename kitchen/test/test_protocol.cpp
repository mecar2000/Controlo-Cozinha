// =============================================================================
// test_protocol.cpp — Protocol.cpp, the MQTT/JSON boundary
// (testproblems.txt 2.7: the largest coverage gap). Protocol owns spec
// validation and clamping, the %<->counts quorum mapping, and the
// HOLD_MAX_DURATION_MS "absent means the maximum safe hold" rule — all
// safety-relevant per RunSpec.h's own claim that the counts<->% mapping "is
// firmware-owned because this condition cuts gas".
//
// Needs ArduinoJson on the include path (see test_harness.h's build command,
// extended with -I to the locally installed ArduinoJson/src) — this is the
// only test file in kitchen/test/ that requires it, matching Protocol.cpp
// being the only production file that includes <ArduinoJson.h>.
// =============================================================================

#include "test_harness.h"
#include "Protocol.h"
#include <ArduinoJson.h>   // only to inspect outbound builder output below —
                           // Protocol.h itself deliberately hides JSON from
                           // its callers; this file is the one place desktop
                           // tests peek behind that boundary to check shape.
#include <cstring>

// =============================================================================
// %<->counts conversion — the firmware-owned 4-20 mA scale mapping.
// With placeholder scales (ADC_COUNTS_PER_MA=1, ADC_COUNTS_AT_4MA=1,
// SENSOR_FULLSCALE_PCT=1): counts = 1 + 16*pct, clamped to [0, ADC_MAX_COUNTS].
// =============================================================================

TEST(pctToCounts_zero_is_live_zero) {
  CHECK(protocolPctToCounts(0.0f) == ADC_COUNTS_AT_4MA);
}

TEST(pctToCounts_full_scale_is_16ma_span_above_live_zero) {
  uint16_t counts = protocolPctToCounts(SENSOR_FULLSCALE_PCT);
  uint16_t expected = (uint16_t)(ADC_COUNTS_AT_4MA + 16.0f * ADC_COUNTS_PER_MA + 0.5f);
  CHECK(counts == expected);
}

TEST(pctToCounts_negative_clamps_to_live_zero) {
  CHECK(protocolPctToCounts(-5.0f) == ADC_COUNTS_AT_4MA);
}

TEST(pctToCounts_clamps_to_adc_max_counts) {
  CHECK(protocolPctToCounts(1000.0f) <= ADC_MAX_COUNTS);
}

// Round-trip: converting a mid-range % to counts and back should return
// (approximately) the same %.
TEST(countsToPct_round_trips_pctToCounts) {
  float original = 0.5f * SENSOR_FULLSCALE_PCT;
  uint16_t counts = protocolPctToCounts(original);
  float back = protocolCountsToPct(counts);
  float diff = back - original;
  if (diff < 0) diff = -diff;
  CHECK(diff < 0.01f);
}

// =============================================================================
// protocolReasonName() — every DangerReason must have a distinct wire name so
// an e-stop, sensor trip, and peer alarm are distinguishable in the log
// (KitchenCore.h's own stated purpose for DangerReason).
// =============================================================================
TEST(reasonName_estop_and_peer_alarm_are_distinct) {
  CHECK(strcmp(protocolReasonName(DangerReason::ESTOP), "ESTOP") == 0);
  CHECK(strcmp(protocolReasonName(DangerReason::PEER_ALARM), "PEER_ALARM") == 0);
  CHECK(strcmp(protocolReasonName(DangerReason::ESTOP),
              protocolReasonName(DangerReason::PEER_ALARM)) != 0);
}

TEST(reasonName_none_for_the_none_reason) {
  CHECK(strcmp(protocolReasonName(DangerReason::NONE), "NONE") == 0);
}

// =============================================================================
// protocolParseCommand() — retained replay guard, malformed/unknown JSON
// =============================================================================

TEST(retained_cmd_is_always_dropped_regardless_of_content) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{}})", /*retained=*/true, nullptr, nullptr);
  CHECK(pc.kind == CmdKind::NONE);
}

TEST(malformed_json_is_bad_json) {
  ParsedCommand pc = protocolParseCommand("{not valid json", false, nullptr, nullptr);
  CHECK(pc.kind == CmdKind::BAD_JSON);
}

TEST(unknown_cmd_value_is_bad_json) {
  ParsedCommand pc = protocolParseCommand(R"({"cmd":"frobnicate"})", false, nullptr, nullptr);
  CHECK(pc.kind == CmdKind::BAD_JSON);
}

TEST(stop_and_ack_parse_with_no_spec_needed) {
  ParsedCommand stop = protocolParseCommand(R"({"cmd":"stop"})", false, nullptr, nullptr);
  CHECK(stop.kind == CmdKind::STOP);

  ParsedCommand ack = protocolParseCommand(R"({"cmd":"ack"})", false, nullptr, nullptr);
  CHECK(ack.kind == CmdKind::ACK);
}

TEST(confirm_parses_runId) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"confirm","runId":"abc123"})", false, nullptr, nullptr);
  CHECK(pc.kind == CmdKind::CONFIRM);
  CHECK(strcmp(pc.runId, "abc123") == 0);
}

TEST(start_missing_spec_is_invalid) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1"})", false, nullptr, nullptr);
  CHECK(pc.kind == CmdKind::START);
  CHECK(pc.spec.valid == false);
  CHECK(pc.rejectDetail != nullptr);
}

// =============================================================================
// start(spec) — gasSetpointPct / fanSpeedPct clamping at the JSON boundary
// =============================================================================

TEST(start_clamps_gasSetpointPct_to_ceiling) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"gasSetpointPct":500}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == true);
  CHECK(pc.spec.gasSetpointPct == VENT_SPEED_MAX_PCT);
}

TEST(start_clamps_fanSpeedPct_to_ceiling) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"fanSpeedPct":500}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == true);
  CHECK(pc.spec.fanSpeedPct == VENT_SPEED_MAX_PCT);
}

TEST(start_negative_gasSetpointPct_clamps_to_zero) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"gasSetpointPct":-10}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == true);
  CHECK(pc.spec.gasSetpointPct == 0.0f);
}

// =============================================================================
// holdStop — HOLD_MAX_DURATION_MS fallback + ceiling (testproblems.txt 2.7's
// headline example: "absent means the maximum safe hold, not skip the
// measurement").
// =============================================================================

TEST(holdStop_omitted_falls_back_to_hold_max_duration_not_zero) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{}})", false, nullptr, nullptr);
  CHECK(pc.spec.valid == true);
  CHECK(pc.spec.holdStop.maxDurationMs == HOLD_MAX_DURATION_MS);
}

TEST(holdStop_above_ceiling_is_clamped_down) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"holdStop":{"maxDurationMs":99999999}}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == true);
  CHECK(pc.spec.holdStop.maxDurationMs == HOLD_MAX_DURATION_MS);
}

TEST(holdStop_below_ceiling_is_accepted_as_requested) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"holdStop":{"maxDurationMs":1234}}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == true);
  CHECK(pc.spec.holdStop.maxDurationMs == 1234UL);
}

// =============================================================================
// maxInventory_mL — REJECTED on holdStop/ventStop, allowed on leakStop
// (Protocol rejects rather than silently ignoring — RunSpec.h's stated
// rationale: a spec asking for something impossible is a spec whose author
// misunderstood the phase).
// =============================================================================

TEST(maxInventory_on_leakStop_is_allowed) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"leakStop":{"maxInventory_mL":500}}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == true);
  CHECK(pc.spec.leakStop.maxInventory_mL == 500.0f);
}

TEST(maxInventory_on_holdStop_is_rejected) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"holdStop":{"maxInventory_mL":500}}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == false);
  CHECK(pc.rejectDetail != nullptr);
}

TEST(maxInventory_on_ventStop_is_rejected) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"ventStop":{"maxInventory_mL":500}}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == false);
  CHECK(pc.rejectDetail != nullptr);
}

TEST(negative_maxDurationMs_is_rejected) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"leakStop":{"maxDurationMs":-5}}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == false);
}

// =============================================================================
// ventRegisters — a 3-bit set, any combination legal
// =============================================================================
TEST(ventRegisters_parses_each_bit_independently) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"ventRegisters":{"central":true,"exhaust":false,"inlet":true}}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == true);
  CHECK(pc.spec.ventRegisters.central == true);
  CHECK(pc.spec.ventRegisters.exhaust == false);
  CHECK(pc.spec.ventRegisters.inlet   == true);
}

// =============================================================================
// sensorQuorum — % on the wire, firmware converts to counts
// =============================================================================
TEST(leakStop_quorum_missing_thresholdPct_is_rejected) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"leakStop":{"sensorQuorum":{"quorumCount":2}}}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == false);
}

TEST(leakStop_quorum_zero_count_does_not_require_thresholdPct) {
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"leakStop":{"sensorQuorum":{"quorumCount":0}}}})",
    false, nullptr, nullptr);
  CHECK(pc.spec.valid == true);
}

TEST(leakStop_quorum_converts_pct_to_counts_and_echoes_it) {
  float requestedPct = 0.0f;
  uint16_t interpretedCounts = 0;
  ParsedCommand pc = protocolParseCommand(
    R"({"cmd":"start","runId":"r1","spec":{"leakStop":{"sensorQuorum":{"quorumCount":2,"thresholdPct":0.5}}}})",
    false, &requestedPct, &interpretedCounts);
  CHECK(pc.spec.valid == true);
  CHECK(pc.spec.leakStop.sensorQuorum.quorumCount == 2);
  CHECK(pc.spec.leakStop.sensorQuorum.thresholdCounts == protocolPctToCounts(0.5f));
  CHECK(requestedPct == 0.5f);
  CHECK(interpretedCounts == protocolPctToCounts(0.5f));
}

// =============================================================================
// config/set — per-sensor threshold table (protocolParseConfig)
// =============================================================================

TEST(config_malformed_json_is_rejected) {
  ParsedConfig cfg = protocolParseConfig("{not json");
  CHECK(cfg.ok == false);
}

TEST(config_missing_thresholds_array_is_rejected) {
  ParsedConfig cfg = protocolParseConfig(R"({"foo":1})");
  CHECK(cfg.ok == false);
}

TEST(config_out_of_range_sensor_index_is_rejected) {
  char json[128];
  snprintf(json, sizeof(json), R"({"thresholds":[{"sensor":%d,"thresholdPct":1.0}]})",
          KITCHEN_MAX_LOCAL_SENSORS);   // one past the valid range
  ParsedConfig cfg = protocolParseConfig(json);
  CHECK(cfg.ok == false);
}

TEST(config_negative_thresholdPct_is_rejected) {
  ParsedConfig cfg = protocolParseConfig(
    R"({"thresholds":[{"sensor":0,"thresholdPct":-1}]})");
  CHECK(cfg.ok == false);
}

// The website may only make a sensor MORE sensitive: a config entry
// requesting a threshold above the firmware ceiling is clamped, and the
// clamp is reported back (not applied silently) so a miscalibration is
// visible in the browser.
TEST(config_entry_above_ceiling_is_clamped_and_flagged) {
  // pct high enough that protocolPctToCounts() exceeds the firmware ceiling —
  // derived from the ceiling itself (not a fixed guess) so this stays valid
  // if the placeholder scale constants ever change.
  float highPct = protocolCountsToPct(SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS) + 1000.0f;
  char json[160];
  snprintf(json, sizeof(json), R"({"thresholds":[{"sensor":0,"thresholdPct":%f}]})",
          (double)highPct);
  ParsedConfig cfg = protocolParseConfig(json);
  CHECK(cfg.ok == true);
  CHECK(cfg.count == 1);
  CHECK(cfg.entries[0].requestedCounts > SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS);   // sanity
  CHECK(cfg.entries[0].clamped == true);
  CHECK(cfg.entries[0].effectiveCounts == SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS);
}

TEST(config_entry_within_ceiling_is_not_flagged_clamped) {
  ParsedConfig cfg = protocolParseConfig(
    R"({"thresholds":[{"sensor":0,"thresholdPct":0.01}]})");
  CHECK(cfg.ok == true);
  CHECK(cfg.entries[0].clamped == false);
  CHECK(cfg.entries[0].effectiveCounts == cfg.entries[0].requestedCounts);
}

TEST(config_too_many_entries_is_rejected) {
  char json[4096];
  int n = snprintf(json, sizeof(json), R"({"thresholds":[)");
  for (int i = 0; i <= PROTOCOL_MAX_CONFIG_ENTRIES; i++) {   // one past the cap
    n += snprintf(json + n, sizeof(json) - n, "%s{\"sensor\":0,\"thresholdPct\":1.0}",
                 i == 0 ? "" : ",");
  }
  snprintf(json + n, sizeof(json) - n, "]}");
  ParsedConfig cfg = protocolParseConfig(json);
  CHECK(cfg.ok == false);
}

// =============================================================================
// Outbound builders — buffer overflow reports 0; a normal buffer round-trips
// through the real deserializer so the shape (not just non-zero length) is
// checked.
// =============================================================================

TEST(buildConfigAck_overflow_returns_zero) {
  ParsedConfig cfg = protocolParseConfig(
    R"({"thresholds":[{"sensor":0,"thresholdPct":1.0}]})");
  char tiny[4];
  size_t n = protocolBuildConfigAck(tiny, sizeof(tiny), cfg);
  CHECK(n == 0);
}

TEST(buildState_produces_parseable_json_with_expected_fields) {
  char out[512];
  RegisterSet regs{/*central=*/true, /*exhaust=*/false, /*inlet=*/true};
  size_t n = protocolBuildState(out, sizeof(out), KitchenState::LEAKING,
                                /*isLeakTestRole=*/true, /*elapsedMs=*/1234,
                                /*deliveredInventory_mL=*/56.0f,
                                /*ackRequired=*/false, /*acked=*/false,
                                DangerReason::NONE, /*sensorsOn=*/true,
                                /*fanSpeedPct=*/42.0f, regs,
                                /*flowRate_mLps=*/7.5f);
  CHECK(n > 0);
  JsonDocument doc;
  auto err = deserializeJson(doc, out, n);
  CHECK(err == DeserializationError::Ok);
  CHECK(strcmp(doc["state"], "LEAKING") == 0);
  CHECK(strcmp(doc["role"], "leak-test") == 0);
  CHECK((uint32_t)doc["elapsedMs"] == 1234UL);
  CHECK(doc["sensorsOn"] == true);
  CHECK((float)doc["fanSpeedPct"] == 42.0f);
  CHECK(doc["registers"]["central"] == true);
  CHECK(doc["registers"]["exhaust"] == false);
  CHECK(doc["registers"]["inlet"] == true);
  CHECK((float)doc["flowRate_mLps"] == 7.5f);
}

TEST(buildAck_echoes_rejection_reason_when_not_accepted) {
  char out[768];
  RunSpec spec = RunSpec{};
  size_t n = protocolBuildAck(out, sizeof(out), "run1", /*accepted=*/false,
                              StartRejectReason::WRONG_ROLE, spec, 0.0f, 0);
  CHECK(n > 0);
  JsonDocument doc;
  auto err = deserializeJson(doc, out, n);
  CHECK(err == DeserializationError::Ok);
  CHECK(doc["valid"] == false);
  CHECK(strcmp(doc["rejection"], "WRONG_ROLE") == 0);
}

TEST(buildAlarm_reports_active_and_reason) {
  char out[256];
  size_t n = protocolBuildAlarm(out, sizeof(out), /*active=*/true,
                                DangerReason::ESTOP, "run1");
  CHECK(n > 0);
  JsonDocument doc;
  auto err = deserializeJson(doc, out, n);
  CHECK(err == DeserializationError::Ok);
  CHECK(doc["danger"] == true);
  CHECK(strcmp(doc["description"], "ESTOP") == 0);
}

TEST(buildSensorSample_matches_daq_current_shape) {
  char out[128];
  size_t n = protocolBuildSensorSample(out, sizeof(out), /*encodedPin=*/EXP_ENC(0, 0),
                                       /*rawMa=*/8.5f, /*tsMs=*/1000ULL);
  CHECK(n > 0);
  JsonDocument doc;
  auto err = deserializeJson(doc, out, n);
  CHECK(err == DeserializationError::Ok);
  CHECK(strcmp(doc["type"], "current") == 0);
  float rawMa = doc["raw_ma"];
  CHECK(rawMa > 8.4f && rawMa < 8.6f);
}
