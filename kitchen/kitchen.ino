// =============================================================================
// kitchen.ino — Kitchen H2 leak-experiment PLC (Arduino Opta + A0602 + D1608E)
//
// Wires the pieces together; holds no control logic of its own:
//
//   commsLoop()                     -- MQTT service
//   expansionLoop()                 -- OptaController I2C pump
//   sensorStreamTick()              -- 10 ms: 1 I2C txn, all 6 H2 channels (SensorStream.cpp)
//   sensorsPoll()  -> SensorState   -- hardware -> struct, reads the stream's cache (Sensors.cpp)
//   core.update()  -> OutputRequest -- the whole decision  (KitchenCore, pure)
//   outputsDrive()                  -- struct -> pins       (Outputs.cpp, ONLY writer)
//   publish state/ack/alarm/sensors-power on change         (Protocol.cpp + Comms)
//   sensorStreamPublish()           -- fast H2 samples -> DataAcquisition/Kitchen/mainBoard/...
//
// The USER button and the MQTT `ack` command are the two indistinguishable
// callers of core.humanAck() — the button path works with no network.
//
// Upload target: Opta M7.  Requires: PubSubClient, ArduinoJson,
// Arduino_Opta_Blueprint, Ethernet.
// =============================================================================

#include <Arduino.h>
#include <Ethernet.h>

// NOTE: this sketch deliberately does NOT define KITCHEN_ALLOW_PLACEHOLDER_SCALES.
// The five analog-scale constants in Kitchen_Settings.h are BLOCKING BEFORE
// HYDROGEN, and their #error guard is what enforces that. Until they are
// bench-measured (and their _SET flags flipped to 1) this sketch will not
// compile — which is the point: an uncalibrated firmware must not be flashable
// by accident.
//
// For pre-calibration BENCH WORK ONLY (relay wiring checks, LED patterns, DAC
// sweeps — anything with no hydrogen present), add the define below
// temporarily, then remove it. Doing so is a deliberate act, not a default.
//   #define KITCHEN_ALLOW_PLACEHOLDER_SCALES

#include "Kitchen_Settings.h"
#include "KitchenCore.h"
#include "Sensors.h"
#include "Outputs.h"
#include "Protocol.h"
#include "Comms.h"        // Ethernet + MQTT, run-mode logging (was Log.h), and NTP
#include "Expansion.h"
#include "SensorStream.h" // fast local H2 sampling + publishing

// ---------------------------------------------------------------------------
static KitchenCore core;

// Retained topics published, and the command/interlock topics subscribed.
static char TOPIC_STATE[96], TOPIC_ACK[96], TOPIC_ALARM[128],
           TOPIC_SENSORS_POWER[96], TOPIC_ONLINE[96], TOPIC_RUN[128];
static char TOPIC_CMD[96], TOPIC_CONFIG_SET[96], TOPIC_PERMIT[96], TOPIC_MODE[96];
static char TOPIC_CONFIG_ACK[96];
static char TOPIC_LOG[96];
// Peer alarms: status/+/+/alarm/+/hydrogen — PubSubClient wildcards are + and #.
static char TOPIC_PEER_ALARM[64];
// DAQ kitchen sensor samples — subscribed only to detect that the remote CM7
// DAQ is alive after we command its sensors on (DAQ_KITCHEN_TOPIC_PREFIX/<id>/#).
static char TOPIC_DAQ_KITCHEN[96];
// Prefix (no trailing #) used for the strncmp match in onMqttMessage.
static char DAQ_KITCHEN_MATCH[80];

#define KITCHEN_SUB_COUNT 6
static const char* SUBSCRIPTIONS[KITCHEN_SUB_COUNT];

// Publish-on-change snapshots. Most live next to their publish* function
// below; these must stay file-scope:
//   _lastLocalSensorsOn  — sensorsPoll() reads it for LOCAL `expectedOn` each
//                          pass (a stale local sensor only trips danger when it
//                          is supposed to be powered)
//   _lastRemoteSensorsOn — publishSensorsPower() change-detect for the remote
//                          DAQ power command, and the edge that arms the DAQ
//                          liveness check
//   _stateEnteredMs      — written by publishRunTopic(), read by publishState()
static bool     _lastLocalSensorsOn  = false;
static bool     _lastRemoteSensorsOn = false;
static bool     _lastRoleMisflip     = false;   // edge-log the selector misflip
static uint32_t _stateEnteredMs      = 0;

// DAQ liveness: when the remote power command goes OFF->ON we expect the CM7
// DAQ to start publishing under DAQ_KITCHEN_TOPIC_PREFIX within
// REMOTE_SENSOR_LIVENESS_MS. If not, warn once (warn only — no state effect;
// the local sensors and the danger path do not depend on the DAQ).
static uint32_t _remoteArmedMs   = 0;     // millis() at the OFF->ON edge; 0 = not armed
static uint32_t _lastDaqMsgMs    = 0;     // last time a DAQ-kitchen topic message arrived
static bool     _daqLivenessWarned = false;

// pending config/ack to emit from loop() (parsed in the MQTT callback)
static ParsedConfig _cfgParsed;
static bool         _cfgAckPending = false;

// pending ack to emit from loop() (parsed in the MQTT callback)
static bool     _ackPending = false;
static char     _ackRunId[32] = {0};
static bool     _ackAccepted = false;
static StartRejectReason _ackRej = StartRejectReason::NONE;
static RunSpec  _ackSpec;
static float    _ackQuorumPct = 0.0f;
static uint16_t _ackQuorumCounts = 0;

// USER button debounce
static bool     _btnWas = false;
static uint32_t _btnDownMs = 0;
#define BTN_DEBOUNCE_MS  50UL

// self device id string for peer-alarm self-filtering
static String   _selfId;

// ---------------------------------------------------------------------------
// MQTT message handler — parses, applies immediately what is safe to apply
// here (permit, peer alarm, ack/stop/confirm), defers the ack PUBLISH to loop().
// ---------------------------------------------------------------------------
static void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  static char buf[MQTT_MAX_PAYLOAD];
  unsigned int n = (len < sizeof(buf) - 1) ? len : sizeof(buf) - 1;
  memcpy(buf, payload, n);
  buf[n] = '\0';
  uint32_t now = millis();

  // NOTE: PubSubClient's callback gives no retained flag. The firmware's
  // protection against a replayed `start` is instead structural: `start` is
  // only accepted in WAITING and only ever ARMS — `confirm` (a second,
  // separate message the website sends live) is what releases gas. A replayed
  // retained `start` on reconnect would at worst re-arm; ARM_TIMEOUT_MS
  // disarms it. The webapp also never retains cmd (commands.py).
  const bool retained = false;

  if (strcmp(topic, TOPIC_CMD) == 0) {
    ParsedCommand pc = protocolParseCommand(buf, retained,
                                            &_ackQuorumPct, &_ackQuorumCounts);
    switch (pc.kind) {
      case CmdKind::START: {
        StartRejectReason rej =
            core.start(pc.spec, sensorsState(), now);
        _ackAccepted = (rej == StartRejectReason::NONE);
        _ackRej      = rej;
        _ackSpec     = core.armedSpec();
        strncpy(_ackRunId, pc.runId, sizeof(_ackRunId) - 1);
        _ackPending  = true;
        break;
      }
      case CmdKind::CONFIRM: {
        StartRejectReason rej = core.confirm(pc.runId, now);
        _ackAccepted = (rej == StartRejectReason::NONE);
        _ackRej      = rej;
        _ackSpec     = core.armedSpec();
        strncpy(_ackRunId, pc.runId, sizeof(_ackRunId) - 1);
        _ackPending  = true;
        break;
      }
      case CmdKind::STOP:  core.stop(now);      break;
      case CmdKind::ACK:   core.humanAck(now);  break;
      case CmdKind::BAD_JSON:
        logPublish(LogLevel::ERROR, "[KITCHEN] bad cmd JSON, ignored");
        break;
      default: break;   // NONE (retained / unknown)
    }
    return;
  }

  if (strcmp(topic, TOPIC_PERMIT) == 0) {
    // Any payload present == permit present. Absence (no message) is handled by
    // permitPresent staying false — it warns, never trips.
    bool present = (n > 0);
    bool value   = (strstr(buf, "\"permit\":true") != nullptr) ||
                   (strstr(buf, "\"permit\": true") != nullptr);
    sensorsSetPermit(present, value);
    return;
  }

  // Peer alarm: status/{Exp}/{device}/alarm/{lab}/hydrogen. Self-filter by id.
  // Tracked PER ZONE (keyed by topic) so a clear from one zone cannot cancel
  // another zone's alarm — this is the primary lab-wide interlock.
  if (strstr(topic, "/alarm/") != nullptr && strstr(topic, "/hydrogen") != nullptr) {
    if (_selfId.length() && strstr(topic, _selfId.c_str()) != nullptr) return; // own
    bool active = (strstr(buf, "\"danger\":true") != nullptr) ||
                  (strstr(buf, "\"active\":true") != nullptr);
    if (!sensorsSetPeerAlarm(topic, active, now)) {
      // Table full and an untracked zone is alarming. The interlock latches
      // safe on its own; this tells someone why it will not release.
      logPrintf(LogLevel::ERROR,
                "[KITCHEN] peer zone table full (%d), alarm latched: %s",
                KITCHEN_MAX_PEER_ZONES, topic);
    }
    return;
  }

  if (strcmp(topic, TOPIC_MODE) == 0) {
    // Non-retained run-mode override. Payload is a bare word or {"mode":"..."}.
    // A reboot drops this and returns to KITCHEN_DEFAULT_MODE.
    RunMode m = logMode();
    if      (strstr(buf, "CLEAN"))     m = RunMode::CLEAN;
    else if (strstr(buf, "VERBOSE"))   m = RunMode::VERBOSE;
    else if (strstr(buf, "PROFILING")) m = RunMode::PROFILING;
    else if (strstr(buf, "DEBUG"))     m = RunMode::DEBUG;
    logSetMode(m);   // clamps to the compile ceiling
    logPrintf(LogLevel::ERROR, "run mode set to ordinal %d", (int)logMode());
    return;
  }

  if (strcmp(topic, TOPIC_CONFIG_SET) == 0) {
    // Per-sensor threshold table. Protocol converts % -> counts and applies
    // the firmware ceiling; nothing is applied unless the WHOLE table parses,
    // so a malformed entry cannot leave a half-configured sensor set.
    _cfgParsed = protocolParseConfig(buf);
    if (_cfgParsed.ok) {
      for (int i = 0; i < _cfgParsed.count; i++) {
        sensorsSetThreshold(_cfgParsed.entries[i].sensorIndex,
                            _cfgParsed.entries[i].effectiveCounts);
      }
      logPrintf(LogLevel::VERBOSE, "[KITCHEN] config/set applied, %d threshold(s)",
                _cfgParsed.count);
    } else {
      logPrintf(LogLevel::ERROR, "[KITCHEN] config/set rejected: %s",
                _cfgParsed.rejectDetail ? _cfgParsed.rejectDetail : "invalid");
    }
    _cfgAckPending = true;
    return;
  }

  // DAQ kitchen sensor sample — we don't parse it, only note that the remote
  // DAQ is publishing (liveness of the CM7 after we command its sensors on).
  if (strncmp(topic, DAQ_KITCHEN_MATCH, strlen(DAQ_KITCHEN_MATCH)) == 0) {
    _lastDaqMsgMs = now;
    return;
  }
}

// ---------------------------------------------------------------------------
// Last published `state` payload + a force flag — file-scope because
// onMqttConnect() must be able to force publishState() to re-emit on every
// (re)connect (the pre-gate would otherwise sit on it until the next 1 Hz
// heartbeat). publishState() (defined lower) owns both otherwise.
static char _lastStatePayload[512] = {0};
static bool _forceStatePublish     = false;

static void onMqttConnect() {
  // Re-assert retained state on every (re)connect so a broker restart self-heals.
  mqttPublish(TOPIC_ONLINE, "online", /*retain=*/true);
  _lastStatePayload[0]  = '\0';   // make the strcmp guard miss
  _forceStatePublish    = true;   // and bypass the discrete/heartbeat pre-gate
}

// ---------------------------------------------------------------------------
static void buildTopics() {
  const char* id = KITCHEN_DEVICE_ID;
  snprintf(TOPIC_STATE,          sizeof(TOPIC_STATE),          "KitchenControl/%s/state", id);
  snprintf(TOPIC_ACK,            sizeof(TOPIC_ACK),            "KitchenControl/%s/ack", id);
  snprintf(TOPIC_SENSORS_POWER,  sizeof(TOPIC_SENSORS_POWER),  "KitchenControl/%s/sensors/power", id);
  snprintf(TOPIC_CMD,            sizeof(TOPIC_CMD),            "KitchenControl/%s/cmd", id);
  snprintf(TOPIC_CONFIG_SET,     sizeof(TOPIC_CONFIG_SET),     "KitchenControl/%s/config/set", id);
  snprintf(TOPIC_CONFIG_ACK,     sizeof(TOPIC_CONFIG_ACK),     "KitchenControl/%s/config/ack", id);
  snprintf(TOPIC_MODE,          sizeof(TOPIC_MODE),           "KitchenControl/%s/mode", id);
  snprintf(TOPIC_LOG,           sizeof(TOPIC_LOG),            "KitchenControl/%s/log", id);
  snprintf(TOPIC_PERMIT,         sizeof(TOPIC_PERMIT),         "safety/permit/%s", id);
  snprintf(TOPIC_ALARM,          sizeof(TOPIC_ALARM),
           "status/%s/%s/alarm/%s/hydrogen", EXPERIMENT_NAME, id, LAB_ID);
  snprintf(TOPIC_ONLINE,         sizeof(TOPIC_ONLINE),
           "status/%s/%s/online", EXPERIMENT_NAME, id);
  snprintf(TOPIC_RUN,            sizeof(TOPIC_RUN),
           "status/%s/%s/run", EXPERIMENT_NAME, id);
  snprintf(TOPIC_PEER_ALARM,     sizeof(TOPIC_PEER_ALARM), "status/+/+/alarm/+/hydrogen");
  snprintf(DAQ_KITCHEN_MATCH,    sizeof(DAQ_KITCHEN_MATCH),
           "%s/%s/", DAQ_KITCHEN_TOPIC_PREFIX, id);
  snprintf(TOPIC_DAQ_KITCHEN,    sizeof(TOPIC_DAQ_KITCHEN),
           "%s/%s/#", DAQ_KITCHEN_TOPIC_PREFIX, id);

  SUBSCRIPTIONS[0] = TOPIC_CMD;
  SUBSCRIPTIONS[1] = TOPIC_CONFIG_SET;
  SUBSCRIPTIONS[2] = TOPIC_PERMIT;
  SUBSCRIPTIONS[3] = TOPIC_PEER_ALARM;
  SUBSCRIPTIONS[4] = TOPIC_MODE;
  SUBSCRIPTIONS[5] = TOPIC_DAQ_KITCHEN;
}

// ---------------------------------------------------------------------------
static void serviceButton(uint32_t now) {
  bool down = (digitalRead(BTN_USER) == LOW);   // Opta USER button is active-low
  if (down && !_btnWas) { _btnWas = true; _btnDownMs = now; }
  if (!down && _btnWas) {
    _btnWas = false;
    if (now - _btnDownMs >= BTN_DEBOUNCE_MS) {
      core.humanAck(now);          // same call as the MQTT `ack` — works offline
      logPublish(LogLevel::VERBOSE, "[KITCHEN] USER button -> humanAck");
    }
  }
}

// ---------------------------------------------------------------------------
// Publish-on-change helpers — one per retained topic. Each owns its own
// snapshot `static` so loop() reads as the 7-step pipeline its header promises.
// _lastStatePayload / _lastLocalSensorsOn / _lastRemoteSensorsOn /
// _stateEnteredMs are file-scope for the reasons noted at their declarations;
// the rest live here.
// ---------------------------------------------------------------------------

static void publishRunTopic(uint32_t now) {
  static KitchenState _lastState = KitchenState::WAITING;
  if (core.state() == _lastState) return;
  _lastState      = core.state();
  _stateEnteredMs = now;
  // run topic for server.py: running true from LEAKING through the purge
  bool running = (core.state() != KitchenState::WAITING &&
                  core.state() != KitchenState::ARMED);
  char runbuf[96];
  snprintf(runbuf, sizeof(runbuf), "{\"running\":%s,\"runId\":\"%s\"}",
           running ? "true" : "false", core.armedSpec().runId);
  mqttPublish(TOPIC_RUN, runbuf, /*retain=*/true);
}

// state payload heartbeat: elapsedMs / inventory_mL tick every pass, so an
// unconditional build+strcmp would run ArduinoJson hundreds of times a second.
// Pre-gate on the DISCRETE fields (state/role/ack/reason/sensorsOn) — publish
// those instantly — and otherwise fall back to at most one refresh per second
// so the browser's elapsed/inventory readouts stay live without the churn.
#define STATE_HEARTBEAT_MS  1000UL

static void publishState(const OutputRequest& out, uint32_t now) {
  static KitchenState _psState   = KitchenState::WAITING;
  static bool         _psLeak    = true;
  static bool         _psAckReq  = false;
  static bool         _psAcked   = false;
  static DangerReason _psReason  = DangerReason::NONE;
  static bool         _psLocalSensors  = false;
  static bool         _psRemoteSensors = false;
  static float        _psFanSpeed = -1.0f;
  static float        _psGasSetpoint = -1.0f;
  static RegisterSet  _psRegisters{};
  static uint32_t     _psLastPub = 0;
  static bool         _psInit    = false;

  bool discreteChange =
      !_psInit ||
      core.state()                 != _psState  ||
      sensorsState().isLeakTestRole != _psLeak   ||
      core.ackRequired()           != _psAckReq ||
      core.acked()                 != _psAcked  ||
      core.reason()                != _psReason ||
      out.localSensorsOn           != _psLocalSensors  ||
      out.remoteSensorsOn          != _psRemoteSensors ||
      out.fanSpeedPct              != _psFanSpeed ||
      out.gasSetpointPct           != _psGasSetpoint ||
      out.registers                != _psRegisters;

  bool heartbeatDue = (now - _psLastPub) >= STATE_HEARTBEAT_MS;

  if (!_forceStatePublish && !discreteChange && !heartbeatDue) return;

  // clearForMs advances continuously while the all-clear hold runs, so it is
  // deliberately NOT in the discrete-change set above — it rides along on the
  // 1 Hz heartbeat, which is exactly the cadence the browser interpolates from.
  char stbuf[512];
  size_t sn = protocolBuildState(stbuf, sizeof(stbuf), core.state(),
                                 sensorsState().isLeakTestRole,
                                 now - _stateEnteredMs,
                                 core.deliveredInventory_mL(),
                                 core.ackRequired(), core.acked(),
                                 core.reason(),
                                 out.localSensorsOn, out.remoteSensorsOn,
                                 out.fanSpeedPct, out.registers,
                                 core.flowRate_mLps(), out.gasSetpointPct,
                                 core.clearForMs(now),
                                 KitchenCore::clearRequiredMs());
  if (!sn) return;

  // Still strcmp-guard the actual publish: on a heartbeat with a frozen
  // elapsed/inventory (e.g. idle in WAITING) the payload is byte-identical and
  // there is no reason to re-send it.
  if (strcmp(stbuf, _lastStatePayload) == 0) {
    _psLastPub = now;   // don't re-enter the build loop until the next beat
    return;
  }

  strncpy(_lastStatePayload, stbuf, sizeof(_lastStatePayload) - 1);
  mqttPublish(TOPIC_STATE, stbuf, /*retain=*/true);
  _forceStatePublish = false;

  _psState = core.state();  _psLeak = sensorsState().isLeakTestRole;
  _psAckReq = core.ackRequired();  _psAcked = core.acked();
  _psReason = core.reason();
  _psLocalSensors = out.localSensorsOn;  _psRemoteSensors = out.remoteSensorsOn;
  _psFanSpeed = out.fanSpeedPct;  _psGasSetpoint = out.gasSetpointPct;
  _psRegisters = out.registers;
  _psLastPub = now;  _psInit = true;
}

static void publishSensorsPower(const OutputRequest& out, uint32_t now) {
  if (out.remoteSensorsOn == _lastRemoteSensorsOn) return;
  _lastRemoteSensorsOn = out.remoteSensorsOn;
  char pbuf[64];
  if (protocolBuildSensorsPower(pbuf, sizeof(pbuf), out.remoteSensorsOn))
    mqttPublish(TOPIC_SENSORS_POWER, pbuf, /*retain=*/true);

  if (out.remoteSensorsOn) {
    // OFF->ON: arm the DAQ liveness window. We expect DataAcquisition/Kitchen
    // traffic within REMOTE_SENSOR_LIVENESS_MS (receive cmd -> close relay ->
    // ~1 s sensor warm-up -> first sample).
    _remoteArmedMs     = (now == 0) ? 1 : now;
    _daqLivenessWarned = false;
  } else {
    _remoteArmedMs = 0;   // ON->OFF: disarm
  }
}

// Warn (once) if the remote DAQ never started publishing after we powered it
// on. Warn-only: local sensors and the danger path do not depend on the DAQ.
static void checkDaqLiveness(uint32_t now) {
  if (_remoteArmedMs == 0 || _daqLivenessWarned) return;
  if (_lastDaqMsgMs != 0 &&
      (int32_t)(_lastDaqMsgMs - _remoteArmedMs) >= 0) {
    // Saw a DAQ message since arming — healthy, stop checking this episode.
    _remoteArmedMs = 0;
    return;
  }
  if (now - _remoteArmedMs >= REMOTE_SENSOR_LIVENESS_MS) {
    logPrintf(LogLevel::ERROR,
              "[KITCHEN] remote DAQ silent %lus after sensors-on command",
              (unsigned long)(REMOTE_SENSOR_LIVENESS_MS / 1000));
    _daqLivenessWarned = true;
  }
}

static void publishAlarm(const OutputRequest& out) {
  static bool _lastAlarmActive = false;
  if (out.alarmOn == _lastAlarmActive) return;
  _lastAlarmActive = out.alarmOn;
  char abuf[256];
  if (protocolBuildAlarm(abuf, sizeof(abuf), out.alarmOn, core.reason(),
                         core.armedSpec().runId))
    mqttPublish(TOPIC_ALARM, abuf, /*retain=*/true);
}

static void flushPendingAck() {
  if (!_ackPending) return;
  _ackPending = false;
  char abuf[768];
  size_t an = protocolBuildAck(abuf, sizeof(abuf), _ackRunId, _ackAccepted,
                               _ackRej, _ackSpec, _ackQuorumPct, _ackQuorumCounts);
  if (an) mqttPublish(TOPIC_ACK, abuf, /*retain=*/false);
}

// config/ack is RETAINED: it is the current threshold table, not an event, so
// a browser connecting later should see what is actually in force.
static void flushPendingConfigAck() {
  if (!_cfgAckPending) return;
  _cfgAckPending = false;
  char cbuf[1024];
  size_t cn = protocolBuildConfigAck(cbuf, sizeof(cbuf), _cfgParsed);
  if (cn) mqttPublish(TOPIC_CONFIG_ACK, cbuf, /*retain=*/true);
}

// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("[KITCHEN] Booting...");

  pinMode(BTN_USER, INPUT_PULLUP);
  // All four LEDs are claimed by outputsBegin() — Outputs is the sole writer
  // of the 2+2 status word (review row 173). Comms drives none.
  analogReadResolution(BIT_RESOLUTION);

  expansionBegin();      // detect A0602 + D1608E before anything reads/writes them
  sensorsBegin();        // arms A0602 input channels
  outputsBegin();        // notes D1608E index, arms O1/O2 DACs, drives safe rest
  sensorStreamBegin();   // fast H2 sample/publish state — after sensorsBegin() has armed the channels

  buildTopics();
  _selfId = String(KITCHEN_DEVICE_ID);

  logSetMode(KITCHEN_DEFAULT_MODE);
  logSetPublish(mqttPublish, TOPIC_LOG);   // Serial + MQTT `log` mirror

  commsSetMqttCallback(onMqttMessage);
  commsSetOnConnect(onMqttConnect);
  commsSetSubscriptions(SUBSCRIPTIONS, KITCHEN_SUB_COUNT);
  commsSetLwt(TOPIC_ONLINE, "offline");
  commsBegin();          // bounded blocking connect

  // Best-effort wall-clock sync so fast-sensor publish timestamps reflect real
  // read time (DataAcquisition/dashboard/app/ingest.py discards anything below
  // a real-epoch floor and substitutes receipt time otherwise). Bounded and
  // non-fatal: commsNowMs() falls back to millis() if this never succeeds
  // (e.g. NTP firewalled on a lab network) — see Comms.h.
  commsSyncClock(5, 500);

  _stateEnteredMs = millis();
  logPublish(LogLevel::VERBOSE, "[KITCHEN] setup complete");
}

// ---------------------------------------------------------------------------
void loop() {
  profileLoopStart();     // no-op unless PROFILING mode

  commsLoop();
  expansionLoop();
  commsPumpClock();       // cheap unless a resync is due (~5 min) — see Comms.h
  profileStage("comms+exp");

  uint32_t now = millis();

  // 0. fast H2 sample: internally rate-gated to SENSOR_SAMPLE_INTERVAL_MS (10 ms).
  //    One I2C transaction refreshes all 6 channels; sensorsPoll() below reads
  //    the cache this fills rather than touching the expansion itself.
  sensorStreamTick(now);
  profileStage("h2sample");

  // 1. hardware -> SensorState. LOCAL expectedOn tracks LAST pass's LOCAL
  //    sensor power (a stale local sensor only trips danger when it should be
  //    powered — and in equipment-test local is on while remote is not).
  sensorsPoll(now, _lastLocalSensorsOn);
  // mirror the core's own integrated inventory into the state for the cap check
  sensorsSetDeliveredInventory(core.deliveredInventory_mL());
  profileStage("sensors");

  // 2. the whole decision (pure). Danger is evaluated first, inside here.
  OutputRequest out = core.update(sensorsState(), now);
  profileStage("core");

  // 2b. role selector flipped to equipment-test outside WAITING — inert, but
  //     log the rising edge (the core is Arduino-free and can't log itself).
  bool misflip = core.roleMisflip();
  if (misflip && !_lastRoleMisflip) {
    logPrintf(LogLevel::ERROR,
              "[ROLE] equipment-test selected in state %d — ignored (only acts in WAITING)",
              (int)core.state());
  }
  _lastRoleMisflip = misflip;

  // 3. pins. Only writer.
  outputsDrive(out, now, sensorsState().peerAlarmStale,
               core.state(), sensorsState().isLeakTestRole, misflip);
  // Close this pass's relay re-assert sweep (see Expansion.h/.cpp) — must run
  // AFTER every expansionSetRelay() call outputsDrive() made this pass.
  expansionEndRelaySweep();
  profileStage("outputs");

  // 4. physical ack button (offline-capable)
  serviceButton(now);

  // 4b. snapshot LOCAL sensor power for next pass's expectedOn
  _lastLocalSensorsOn = out.localSensorsOn;

  // 5. publishes on change ------------------------------------------
  publishRunTopic(now);
  publishState(out, now);
  publishSensorsPower(out, now);
  publishAlarm(out);
  flushPendingAck();
  flushPendingConfigAck();
  checkDaqLiveness(now);
  profileStage("publish");

  // 6. fast H2 publish: internally rate-gated to SENSOR_PUBLISH_INTERVAL_MS,
  //    round-robin one sensor/call, budget-capped — see SensorStream.cpp.
  //    Deliberately AFTER every safety-relevant step above (core.update(),
  //    outputsDrive(), the retained-topic publishes) so a slow/blocking MQTT
  //    write here can never delay them.
  sensorStreamPublish(now, commsNowMs());
  profileStage("h2publish");

  profileLoopEnd();
}
