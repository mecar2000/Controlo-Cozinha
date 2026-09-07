// =============================================================================
// Comms.cpp — Ethernet + MQTT for the kitchen PLC. See Comms.h.
// Based on DataAcquisition/CM7/Comms.cpp (Ethernet branch), NTP removed, with
// the three additive hooks the plan's step 5 calls for.
// =============================================================================

#include "Comms.h"
#include "Kitchen_Settings.h"
#include "Kitchen_Secrets.h"
#include <Arduino.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>

EthernetClient ethClient;
PubSubClient   mqttClient(ethClient);
String         deviceId;

// ---------------------------------------------------------------------------
static void (*_onMqttConnect)() = nullptr;

static const char* const* _subTopics = nullptr;
static int                _subCount  = 0;

static const char* _lwtTopic   = nullptr;
static const char* _lwtPayload = nullptr;

void commsSetMqttCallback(MQTT_CALLBACK_SIGNATURE) { mqttClient.setCallback(callback); }
void commsSetOnConnect(void (*cb)()) { _onMqttConnect = cb; }

void commsSetSubscriptions(const char* const* topics, int count) {
  _subTopics = topics;
  _subCount  = count;
}
void commsSetLwt(const char* topic, const char* payload) {
  _lwtTopic   = topic;
  _lwtPayload = payload;
}

// ---------------------------------------------------------------------------
static void connectNetwork() {
  logPublish(LogLevel::VERBOSE, "[Ethernet] requesting DHCP lease");
  if (Ethernet.begin(const_cast<uint8_t*>(ETHERNET_MAC), 4000) == 0) {
    logPublish(LogLevel::ERROR, "[Ethernet] DHCP failed — will retry in loop");
    return;   // NOT fatal: the state machine's danger path works with no network
  }
  IPAddress ip = Ethernet.localIP();
  logPrintf(LogLevel::VERBOSE, "[Ethernet] connected IP=%d.%d.%d.%d",
            ip[0], ip[1], ip[2], ip[3]);
}

// ---------------------------------------------------------------------------
// MQTT reconnect — non-blocking-ish: one connect attempt per call, with a
// short backoff slice. Never blocks longer than a keep-alive window so the
// loop keeps servicing the state machine and Outputs.
// ---------------------------------------------------------------------------
static unsigned long _backoffMs   = 1000;
static unsigned long _lastAttempt = 0;

// Consecutive mqttPublish() failures. After this many in a row, the socket is
// assumed half-open (link connected() still reports up) and commsForceReconnect()
// is called. Reset on a successful publish and on a fresh MQTT connect.
static int _publishFails = 0;
#define MQTT_PUBLISH_FAIL_LIMIT  5

// Link up/down edge tracking — the link check used to run only when MQTT
// already reported disconnected, so a link drop was invisible until the next
// publish failed. _linkWasUp latches so we log each transition once.
static bool _linkWasUp = false;

static void _noteLink(bool up) {
  if (up == _linkWasUp) return;
  _linkWasUp = up;
  logPublish(up ? LogLevel::VERBOSE : LogLevel::ERROR,
             up ? "ethernet link up" : "ethernet link DOWN");
}

static void reconnect() {
  if (Ethernet.linkStatus() == LinkOFF) {
    _noteLink(false);
    if (millis() - _lastAttempt >= 60000UL) {
      _lastAttempt = millis();
      logPublish(LogLevel::ERROR, "[Ethernet] no link — retrying");
      Ethernet.begin(const_cast<uint8_t*>(ETHERNET_MAC), 4000);
    }
    return;   // no MQTT without a link
  }
  _noteLink(true);
  Ethernet.maintain();

  if (mqttClient.connected()) return;
  if (millis() - _lastAttempt < _backoffMs) return;
  _lastAttempt = millis();

  logPrintf(LogLevel::VERBOSE, "[MQTT] connecting (backoff=%lus)",
            (unsigned long)(_backoffMs / 1000));

  String clientId = "opta-kitchen-" + deviceId;
  bool ok;
  if (_lwtTopic) {
    ok = mqttClient.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD,
                            _lwtTopic, 1, true, _lwtPayload);
  } else {
    ok = mqttClient.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD);
  }

  if (ok) {
    logPublish(LogLevel::VERBOSE, "[MQTT] connected");
    for (int i = 0; i < _subCount; i++) {
      mqttClient.subscribe(_subTopics[i], 1);
      logPrintf(LogLevel::DEBUG, "[MQTT] subscribed %s", _subTopics[i]);
    }
    _backoffMs      = 1000;
    _publishFails   = 0;
    if (_onMqttConnect) _onMqttConnect();
  } else {
    logPrintf(LogLevel::ERROR, "[MQTT] connect failed rc=%d", mqttClient.state());
    _backoffMs = min(_backoffMs * 2, 8000UL);
  }
}

// ---------------------------------------------------------------------------
bool mqttPublish(const char* topic, const char* payload, bool retain) {
  bool ok = mqttClient.publish(topic, (const uint8_t*)payload,
                               (unsigned int)strlen(payload), retain);
  if (ok) {
    _publishFails = 0;
    return true;
  }

  // NOTE: no logPublish() here for the failure — this IS the publish path, and
  // a log line would just fail the same way. Serial only.
  Serial.print("[MQTT] publish failed for "); Serial.println(topic);

  if (++_publishFails >= MQTT_PUBLISH_FAIL_LIMIT && mqttClient.connected()) {
    // Link still "up", socket wedged (classic half-open after a cable pull).
    Serial.println("[MQTT] publish fail limit reached — forcing reconnect");
    commsForceReconnect();
    _publishFails = 0;
  }
  return false;
}

bool commsMqttConnected() { return mqttClient.connected(); }
bool commsLinkUp()        { return Ethernet.linkStatus() != LinkOFF; }

// ---------------------------------------------------------------------------
void commsBegin() {
  deviceId = String(KITCHEN_DEVICE_ID);

  connectNetwork();

  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setBufferSize(MQTT_MAX_PAYLOAD);

  // One blocking-ish pass so we usually enter loop() already connected — but
  // bounded, so a dead broker never stalls boot past a few seconds.
  unsigned long start = millis();
  while (!mqttClient.connected() && millis() - start < 6000UL) {
    reconnect();
    delay(50);
  }
}

void commsLoop() {
  if (!mqttClient.connected()) {
    reconnect();
  }
  mqttClient.loop();
}

void commsForceReconnect() {
  Serial.println("[MQTT] forcing reconnect");
  mqttClient.disconnect();
  _backoffMs    = 1000;
  _lastAttempt  = 0;
  _publishFails = 0;
}

// =============================================================================
// Diagnostics / run modes (was Log.cpp — see the contract in Comms.h). Lives
// in this file so logPublish() can call mqttPublish() directly; kitchen.ino
// still injects the `log` topic + sink via logSetPublish().
// =============================================================================
static RunMode      _mode      = KITCHEN_DEFAULT_MODE;
static LogPublishFn _publish   = nullptr;
static const char*  _logTopic  = nullptr;

// ---------------------------------------------------------------------------
void logSetPublish(LogPublishFn fn, const char* logTopic) {
  _publish  = fn;
  _logTopic = logTopic;
}

void logSetMode(RunMode m) {
#ifndef KITCHEN_ENABLE_DEBUG
  // Compile ceiling: DEBUG/PROFILING are not built. A request for either
  // lands on VERBOSE rather than being silently ignored.
  if (m == RunMode::DEBUG || m == RunMode::PROFILING) m = RunMode::VERBOSE;
#endif
  _mode = m;
}

RunMode logMode() { return _mode; }

bool logAtLeast(RunMode want) {
  return (uint8_t)_mode >= (uint8_t)want;
}

// ---------------------------------------------------------------------------
static const char* levelName(LogLevel l) {
  switch (l) {
    case LogLevel::ERROR:   return "error";
    case LogLevel::VERBOSE: return "verbose";
    case LogLevel::DEBUG:   return "debug";
    default:                return "?";
  }
}

// A call at LogLevel L is shown when: L == ERROR (always), or the active mode
// is high enough — VERBOSE lines need mode >= VERBOSE, DEBUG lines need
// mode >= DEBUG.
static bool levelVisible(LogLevel l) {
  switch (l) {
    case LogLevel::ERROR:   return true;
    case LogLevel::VERBOSE: return logAtLeast(RunMode::VERBOSE);
    case LogLevel::DEBUG:   return logAtLeast(RunMode::DEBUG);
    default:                return false;
  }
}

void logPublish(LogLevel level, const char* msg) {
  if (!levelVisible(level)) return;

  Serial.print("[");
  Serial.print(levelName(level));
  Serial.print("] ");
  Serial.println(msg);

  // MQTT mirror only at >= VERBOSE and only for lines the mode shows.
  if (_publish && _logTopic && logAtLeast(RunMode::VERBOSE)) {
    char buf[256];
    // Minimal hand-built JSON — msg is firmware-authored, no untrusted quotes.
    int n = snprintf(buf, sizeof(buf),
                     "{\"level\":\"%s\",\"msg\":\"%s\",\"t\":%lu}",
                     levelName(level), msg, (unsigned long)millis());
    if (n > 0 && n < (int)sizeof(buf)) {
      _publish(_logTopic, buf, /*retain=*/false);
    }
  }
}

void logPrintf(LogLevel level, const char* fmt, ...) {
  if (!levelVisible(level)) return;
  char buf[200];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  logPublish(level, buf);
}

// ---------------------------------------------------------------------------
// PROFILING — per-loop-stage micros() deltas with a ~5 s rolling summary.
// ---------------------------------------------------------------------------
#define PROFILE_MAX_STAGES  8
#define PROFILE_SUMMARY_MS  5000UL

static uint32_t     _profLast          = 0;
static const char*  _profNames[PROFILE_MAX_STAGES]  = {0};
static uint32_t     _profAccum[PROFILE_MAX_STAGES]  = {0};
static uint16_t     _profCount[PROFILE_MAX_STAGES]  = {0};
static uint8_t      _profN             = 0;   // stages seen this pass
static uint8_t      _profStagesKnown   = 0;   // distinct stage names ever seen
static uint32_t     _profSummaryDueMs  = 0;

void profileLoopStart() {
  if (_mode != RunMode::PROFILING) return;
  _profLast = micros();
  _profN    = 0;
  if (_profSummaryDueMs == 0) _profSummaryDueMs = millis() + PROFILE_SUMMARY_MS;
}

void profileStage(const char* name) {
  if (_mode != RunMode::PROFILING) return;
  uint32_t nowUs = micros();
  uint32_t dt    = nowUs - _profLast;
  _profLast = nowUs;

  // Match by pointer identity — call sites pass string literals, so the same
  // stage is always the same pointer. New name => new slot.
  int slot = -1;
  for (uint8_t i = 0; i < _profStagesKnown; i++) {
    if (_profNames[i] == name) { slot = i; break; }
  }
  if (slot < 0 && _profStagesKnown < PROFILE_MAX_STAGES) {
    slot = _profStagesKnown++;
    _profNames[slot] = name;
  }
  if (slot >= 0) {
    _profAccum[slot] += dt;
    _profCount[slot] += 1;
  }
  if (_profN < PROFILE_MAX_STAGES) _profN++;
}

void profileLoopEnd() {
  if (_mode != RunMode::PROFILING) return;
  if ((int32_t)(millis() - _profSummaryDueMs) < 0) return;
  _profSummaryDueMs = millis() + PROFILE_SUMMARY_MS;

  char line[256];
  size_t off = 0;
  off += snprintf(line + off, sizeof(line) - off, "profile avg us:");
  for (uint8_t i = 0; i < _profStagesKnown && off < sizeof(line); i++) {
    uint32_t avg = _profCount[i] ? _profAccum[i] / _profCount[i] : 0;
    off += snprintf(line + off, sizeof(line) - off, " %s=%lu",
                    _profNames[i], (unsigned long)avg);
    _profAccum[i] = 0;
    _profCount[i] = 0;
  }
  logPublish(LogLevel::VERBOSE, line);
}
