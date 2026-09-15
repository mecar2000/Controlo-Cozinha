// =============================================================================
// Protocol.cpp — see Protocol.h.
// =============================================================================

#include "Protocol.h"
#include "Kitchen_Settings.h"
#include <ArduinoJson.h>
#include <string.h>
#include <math.h>
#include <stdio.h>

// ---------------------------------------------------------------------------
// %<->counts on the 4-20 mA sensor scale. pct 0 -> 4 mA live-zero;
// pct == SENSOR_FULLSCALE_PCT -> 20 mA (full scale = 16 mA span).
// With placeholder scales this returns the live-zero for every %, which is
// harmless on the bench — a real firmware build is blocked by the #error guard
// until the scale constants are measured.
// ---------------------------------------------------------------------------
uint16_t protocolPctToCounts(float pct) {
  if (pct < 0.0f) pct = 0.0f;
  float mA = 4.0f;
  if (SENSOR_FULLSCALE_PCT > 0.0f) {
    mA = 4.0f + (pct / SENSOR_FULLSCALE_PCT) * 16.0f;
  }
  float c = (float)ADC_COUNTS_AT_4MA + (mA - 4.0f) * ADC_COUNTS_PER_MA;
  if (c < 0.0f) c = 0.0f;
  if (c > ADC_MAX_COUNTS) c = ADC_MAX_COUNTS;
  return (uint16_t)(c + 0.5f);
}

float protocolCountsToPct(uint16_t counts) {
  if (ADC_COUNTS_PER_MA <= 0.0f || SENSOR_FULLSCALE_PCT <= 0.0f) return 0.0f;
  float mA  = 4.0f + ((float)counts - (float)ADC_COUNTS_AT_4MA) / ADC_COUNTS_PER_MA;
  float pct = (mA - 4.0f) / 16.0f * SENSOR_FULLSCALE_PCT;
  return pct < 0.0f ? 0.0f : pct;
}

// ---------------------------------------------------------------------------
const char* protocolReasonName(DangerReason r) {
  switch (r) {
    case DangerReason::LOCAL_SENSOR_THRESHOLD:  return "LOCAL_SENSOR_THRESHOLD";
    case DangerReason::LOCAL_SENSOR_STALE:      return "LOCAL_SENSOR_STALE";
    case DangerReason::FLOW_OVER_LIMIT:         return "FLOW_OVER_LIMIT";
    case DangerReason::INVENTORY_CAP_EXCEEDED:  return "INVENTORY_CAP_EXCEEDED";
    case DangerReason::PEER_ALARM:              return "PEER_ALARM";
    case DangerReason::PERMIT_DENIED:           return "PERMIT_DENIED";
    case DangerReason::ESTOP:                   return "ESTOP";
    case DangerReason::EXPANSION_FAULT:         return "EXPANSION_FAULT";
    case DangerReason::EXTERNAL_TRIP:           return "EXTERNAL_TRIP";
    case DangerReason::OPERATOR_ABORT:          return "OPERATOR_ABORT";
    default:                                    return "NONE";
  }
}

static const char* stateName(KitchenState s) {
  switch (s) {
    case KitchenState::WAITING:            return "WAITING";
    case KitchenState::ARMED:              return "ARMED";
    case KitchenState::WARMING_UP:         return "WARMING_UP";
    case KitchenState::LEAKING:            return "LEAKING";
    case KitchenState::HOLD:               return "HOLD";
    case KitchenState::VENTILATING:        return "VENTILATING";
    case KitchenState::FULLY_VENTILATING:  return "FULLY_VENTILATING";
    default:                               return "?";
  }
}

static const char* rejectName(StartRejectReason r) {
  switch (r) {
    case StartRejectReason::WRONG_STATE:      return "WRONG_STATE";
    case StartRejectReason::WRONG_ROLE:       return "WRONG_ROLE";
    case StartRejectReason::INVALID_SPEC:     return "INVALID_SPEC";
    case StartRejectReason::RUN_ID_MISMATCH:  return "RUN_ID_MISMATCH";
    case StartRejectReason::ARM_TIMED_OUT:    return "ARM_TIMED_OUT";
    default:                                  return "NONE";
  }
}

// ---------------------------------------------------------------------------
// StopCondition parse. `phase` names it for the reject detail. `allowInventory`
// is false for hold/vent — a maxInventory_mL there is a REJECT, not an ignore.
// Returns true on success; on failure sets *detail and leaves sc partially set.
// ---------------------------------------------------------------------------
static bool parseStop(JsonObjectConst o, StopCondition& sc, bool allowInventory,
                      const char* phase, const char** detail) {
  sc = StopCondition{};
  if (o.isNull()) return true;   // absent is legal for leak/vent (== no cap)

  if (o.containsKey("maxDurationMs")) {
    long v = o["maxDurationMs"] | 0;
    if (v < 0) { *detail = "negative maxDurationMs"; return false; }
    sc.maxDurationMs = (uint32_t)v;
  }
  if (o.containsKey("maxInventory_mL")) {
    float v = o["maxInventory_mL"] | 0.0f;
    if (v < 0.0f) { *detail = "negative maxInventory_mL"; return false; }
    if (v > 0.0f && !allowInventory) {
      *detail = phase;   // caller-facing: "maxInventory_mL set on <phase>"
      return false;
    }
    sc.maxInventory_mL = v;
  }
  JsonObjectConst q = o["sensorQuorum"];
  if (!q.isNull()) {
    long cnt = q["quorumCount"] | 0;
    if (cnt < 0 || cnt > 255) { *detail = "quorumCount out of range"; return false; }
    sc.sensorQuorum.quorumCount = (uint8_t)cnt;
    if (cnt > 0) {
      // Website sends % ; firmware owns the mapping to counts.
      float pct = q["thresholdPct"] | -1.0f;
      if (pct < 0.0f) { *detail = "sensorQuorum missing thresholdPct"; return false; }
      sc.sensorQuorum.thresholdCounts = protocolPctToCounts(pct);
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
ParsedCommand protocolParseCommand(const char* json, bool retained,
                                   float* requestedPctOut,
                                   uint16_t* interpretedCountsOut) {
  ParsedCommand pc;

  // A retained cmd is a broker replay — never act on it.
  if (retained) return pc;   // kind == NONE

  StaticJsonDocument<MQTT_MAX_PAYLOAD> doc;
  if (deserializeJson(doc, json) != DeserializationError::Ok) {
    pc.kind = CmdKind::BAD_JSON;
    return pc;
  }

  const char* cmd = doc["cmd"] | "";
  if (strcmp(cmd, "stop") == 0) { pc.kind = CmdKind::STOP;  return pc; }
  if (strcmp(cmd, "ack")  == 0) { pc.kind = CmdKind::ACK;   return pc; }

  if (strcmp(cmd, "confirm") == 0) {
    pc.kind = CmdKind::CONFIRM;
    const char* rid = doc["runId"] | "";
    strncpy(pc.runId, rid, sizeof(pc.runId) - 1);
    return pc;
  }

  if (strcmp(cmd, "start") != 0) {
    pc.kind = CmdKind::BAD_JSON;
    return pc;
  }

  // --- start(spec): parse + validate + clamp -----------------------
  pc.kind = CmdKind::START;
  const char* rid = doc["runId"] | "";
  strncpy(pc.runId, rid, sizeof(pc.runId) - 1);
  strncpy(pc.spec.runId, rid, sizeof(pc.spec.runId) - 1);

  JsonObjectConst spec = doc["spec"];
  if (spec.isNull()) { pc.spec.valid = false; pc.rejectDetail = "missing spec"; return pc; }

  const char* detail = nullptr;

  // gasSetpointPct — clamp to the firmware fan/gas ceiling (0..VENT_SPEED_MAX_PCT).
  float gas = spec["gasSetpointPct"] | 0.0f;
  pc.spec.gasSetpointPct = KitchenCore::clampFanSpeedPct(gas);

  // leakStop — inventory allowed (gas is flowing here).
  if (!parseStop(spec["leakStop"], pc.spec.leakStop, /*allowInventory=*/true,
                 "maxInventory_mL on leakStop", &detail)) {
    pc.spec.valid = false; pc.rejectDetail = detail; return pc;
  }

  // holdStop — NO inventory. Omitted => HOLD_MAX_DURATION_MS (not zero).
  if (!parseStop(spec["holdStop"], pc.spec.holdStop, /*allowInventory=*/false,
                 "maxInventory_mL on holdStop", &detail)) {
    pc.spec.valid = false; pc.rejectDetail = detail; return pc;
  }
  if (pc.spec.holdStop.maxDurationMs == 0) {
    pc.spec.holdStop.maxDurationMs = HOLD_MAX_DURATION_MS;
  } else if (pc.spec.holdStop.maxDurationMs > HOLD_MAX_DURATION_MS) {
    pc.spec.holdStop.maxDurationMs = HOLD_MAX_DURATION_MS;   // clamp to ceiling
  }

  // ventStop — NO inventory.
  if (!parseStop(spec["ventStop"], pc.spec.ventStop, /*allowInventory=*/false,
                 "maxInventory_mL on ventStop", &detail)) {
    pc.spec.valid = false; pc.rejectDetail = detail; return pc;
  }

  // ventRegisters — a 3-bit set, any combination legal.
  JsonObjectConst vr = spec["ventRegisters"];
  pc.spec.ventRegisters.central = vr["central"] | false;
  pc.spec.ventRegisters.exhaust = vr["exhaust"] | false;
  pc.spec.ventRegisters.inlet   = vr["inlet"]   | false;

  // fanSpeedPct — clamp to VENT_SPEED_MAX_PCT.
  float fan = spec["fanSpeedPct"] | 0.0f;
  pc.spec.fanSpeedPct = KitchenCore::clampFanSpeedPct(fan);

  pc.spec.valid = true;

  // Echo figures for the ack: the leak-phase quorum is the one that cuts gas,
  // so that is the % <-> counts pair the review screen shows.
  if (requestedPctOut && interpretedCountsOut) {
    JsonObjectConst lq = spec["leakStop"]["sensorQuorum"];
    if (!lq.isNull() && (long)(lq["quorumCount"] | 0) > 0) {
      *requestedPctOut      = lq["thresholdPct"] | 0.0f;
      *interpretedCountsOut = pc.spec.leakStop.sensorQuorum.thresholdCounts;
    } else {
      *requestedPctOut      = 0.0f;
      *interpretedCountsOut = 0;
    }
  }
  return pc;
}

// ---------------------------------------------------------------------------
// config/set — per-sensor threshold table. See the contract in Protocol.h.
// ---------------------------------------------------------------------------
ParsedConfig protocolParseConfig(const char* json) {
  ParsedConfig cfg;

  StaticJsonDocument<MQTT_MAX_PAYLOAD> doc;
  if (deserializeJson(doc, json) != DeserializationError::Ok) {
    cfg.rejectDetail = "bad JSON";
    return cfg;
  }

  JsonArrayConst arr = doc["thresholds"];
  if (arr.isNull()) {
    cfg.rejectDetail = "missing thresholds array";
    return cfg;
  }

  for (JsonObjectConst e : arr) {
    if (cfg.count >= PROTOCOL_MAX_CONFIG_ENTRIES) {
      cfg.rejectDetail = "too many threshold entries";
      return cfg;
    }
    long idx = e["sensor"] | -1L;
    if (idx < 0 || idx >= KITCHEN_MAX_LOCAL_SENSORS) {
      cfg.rejectDetail = "sensor index out of range";
      return cfg;
    }
    float pct = e["thresholdPct"] | -1.0f;
    if (pct < 0.0f) {
      cfg.rejectDetail = "missing or negative thresholdPct";
      return cfg;
    }

    ParsedConfigEntry& out = cfg.entries[cfg.count++];
    out.sensorIndex     = (int)idx;
    out.requestedPct    = pct;
    out.requestedCounts = protocolPctToCounts(pct);
    // Firmware owns the ceiling: a request to trip LATER is clamped back.
    out.effectiveCounts = KitchenCore::clampThreshold(out.requestedCounts);
    out.clamped         = (out.effectiveCounts != out.requestedCounts);
  }

  cfg.ok = true;
  return cfg;
}

size_t protocolBuildConfigAck(char* out, size_t cap, const ParsedConfig& cfg) {
  StaticJsonDocument<1024> d;
  d["valid"] = cfg.ok;
  if (!cfg.ok) {
    d["rejection"] = cfg.rejectDetail ? cfg.rejectDetail : "invalid";
  }
  JsonArray arr = d.createNestedArray("thresholds");
  for (int i = 0; i < cfg.count; i++) {
    const ParsedConfigEntry& e = cfg.entries[i];
    JsonObject o = arr.createNestedObject();
    o["sensor"]           = e.sensorIndex;
    o["requestedPct"]     = e.requestedPct;
    o["requestedCounts"]  = e.requestedCounts;
    o["effectiveCounts"]  = e.effectiveCounts;
    o["effectivePct"]     = protocolCountsToPct(e.effectiveCounts);
    o["clamped"]          = e.clamped;
  }
  size_t n = serializeJson(d, out, cap);
  return (n == 0 || n >= cap) ? 0 : n;
}

// ---------------------------------------------------------------------------
// Outbound builders
// ---------------------------------------------------------------------------
size_t protocolBuildState(char* out, size_t cap,
                          KitchenState state, bool isLeakTestRole,
                          uint32_t elapsedMs, float deliveredInventory_mL,
                          bool ackRequired, bool acked, DangerReason reason,
                          bool sensorsOn,
                          float fanSpeedPct, const RegisterSet& registers,
                          float flowRate_mLps) {
  StaticJsonDocument<512> d;
  d["state"]        = stateName(state);
  d["role"]         = isLeakTestRole ? "leak-test" : "equipment-test";
  d["elapsedMs"]    = elapsedMs;
  d["inventory_mL"] = deliveredInventory_mL;
  d["ackRequired"]  = ackRequired;
  d["acked"]        = acked;
  d["reason"]       = protocolReasonName(reason);
  d["sensorsOn"]    = sensorsOn;
  d["fanSpeedPct"]  = fanSpeedPct;
  d["flowRate_mLps"] = flowRate_mLps;
  JsonObject r = d.createNestedObject("registers");
  r["central"] = registers.central;
  r["exhaust"] = registers.exhaust;
  r["inlet"]   = registers.inlet;
  size_t n = serializeJson(d, out, cap);
  return (n == 0 || n >= cap) ? 0 : n;
}

size_t protocolBuildAck(char* out, size_t cap,
                        const char* runId, bool accepted,
                        StartRejectReason rej,
                        const RunSpec& s,
                        float requestedQuorumPct, uint16_t interpretedQuorumCounts) {
  StaticJsonDocument<768> d;
  d["runId"]    = runId;
  d["valid"]    = accepted;
  if (!accepted) {
    d["rejection"] = rejectName(rej);
  }
  JsonObject spec = d.createNestedObject("spec");
  spec["gasSetpointPct"] = s.gasSetpointPct;
  spec["fanSpeedPct"]    = s.fanSpeedPct;
  JsonObject ls = spec.createNestedObject("leakStop");
  ls["maxDurationMs"]   = s.leakStop.maxDurationMs;
  ls["maxInventory_mL"] = s.leakStop.maxInventory_mL;
  JsonObject hs = spec.createNestedObject("holdStop");
  hs["maxDurationMs"]   = s.holdStop.maxDurationMs;
  JsonObject vs = spec.createNestedObject("ventStop");
  vs["maxDurationMs"]   = s.ventStop.maxDurationMs;
  JsonObject vr = spec.createNestedObject("ventRegisters");
  vr["central"] = s.ventRegisters.central;
  vr["exhaust"] = s.ventRegisters.exhaust;
  vr["inlet"]   = s.ventRegisters.inlet;
  // The gas-cutting quorum, both as asked and as interpreted.
  JsonObject q = d.createNestedObject("quorumInterpreted");
  q["requestedPct"]     = requestedQuorumPct;
  q["interpretedCounts"] = interpretedQuorumCounts;
  size_t n = serializeJson(d, out, cap);
  return (n == 0 || n >= cap) ? 0 : n;
}

size_t protocolBuildAlarm(char* out, size_t cap,
                          bool active, DangerReason reason, const char* runId) {
  StaticJsonDocument<256> d;
  d["danger"]      = active;
  d["active"]      = active;   // some consumers key on "active"
  d["description"] = protocolReasonName(reason);
  d["runId"]       = runId;
  size_t n = serializeJson(d, out, cap);
  return (n == 0 || n >= cap) ? 0 : n;
}

size_t protocolBuildSensorsPower(char* out, size_t cap, bool on) {
  StaticJsonDocument<64> d;
  d["on"] = on;
  size_t n = serializeJson(d, out, cap);
  return (n == 0 || n >= cap) ? 0 : n;
}

size_t protocolBuildSensorSample(char* out, size_t cap,
                                 int encodedPin, float rawMa, uint64_t tsMs) {
  int n = snprintf(out, cap,
                   "{\"pin\":%d,\"type\":\"current\",\"raw_ma\":%.5f,\"ts\":%llu}",
                   encodedPin, (double)rawMa, (unsigned long long)tsMs);
  return (n <= 0 || (size_t)n >= cap) ? 0 : (size_t)n;
}
