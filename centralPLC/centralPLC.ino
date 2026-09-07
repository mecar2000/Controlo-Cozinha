// Central Safety PLC — Arduino Opta
//
// Role  : Hub for the lab-wide safety system. Reads its own wired sensors
//         (base I1-I8 + A0602 expansion) as independent alarm "zones", each
//         tagged with a labId+hazardType, and publishes them onto the shared
//         status/ MQTT tree — the same tree every experiment PLC publishes
//         to. Also watches that whole tree and drives a local siren/beacon
//         relay whenever ANY zone anywhere is active.
//
//         Central does NOT decide which experiments get stopped — that
//         "permit" computation (lab + mayEmit based) lives in server.py,
//         the only component with Firebase access. Central never touches
//         Firebase, never does HTTPS, never parses routing rules.
//
// Relays : RELAY1 — siren/beacon, closes while any zone (anywhere) is active
// LEDs   : LED_RELAY1 — always on (system running)
//          LED_RELAY2 — Ethernet connected
//          LED_RELAY3 — MQTT connected
//          LEDR+LEDG  — blink orange while any local zone is active
// Network: Ethernet DHCP + MQTT. Local zone sensing works fully without network.

#include <Ethernet.h>
#include <PubSubClient.h>
#include "Secrets.h"

// ---------------------------------------------------------------------------
// Identity — this device appears on the shared topic tree as "Central"
// ---------------------------------------------------------------------------
#define EXPERIMENT_NAME "Central"

// ---------------------------------------------------------------------------
// Local zone config — one entry per wired sensor.
// Threshold is a bench-test value (~2.5V on 0-10V/12-bit) — recalibrate
// against real sensor datasheets before relying on this in production.
// ---------------------------------------------------------------------------
#define TEST_THRESHOLD  1024   // ~2.5V on 0-10V 12-bit (4095 * 2.5 / 10)
#define ZONE_COUNT      8      // base Opta I1-I8 — extend when A0602 wiring is known

static const int   ZONE_THRESHOLD[ZONE_COUNT] = {
  TEST_THRESHOLD, TEST_THRESHOLD, TEST_THRESHOLD, TEST_THRESHOLD,
  TEST_THRESHOLD, TEST_THRESHOLD, TEST_THRESHOLD, TEST_THRESHOLD
};
static const char* ZONE_LAB_ID[ZONE_COUNT] = {
  "lab5", "lab5", "lab3", "lab3", "lab3", "lab3", "lab3", "lab3"
};
static const char* ZONE_HAZARD[ZONE_COUNT] = {
  "leak", "leak", "leak", "leak", "leak", "leak", "leak", "leak"
};

// TODO: A0602 expansion channels — read over the Opta's Modbus expansion bus
// (ArduinoRS485/ArduinoModbus per Arduino's Opta Ext docs). Register addresses
// are hardware-doc dependent and not available in this repo. Once known, add
// a parallel EXPANSION_ZONE_COUNT + tables and an anyExpansionTriggered()
// following the exact same shape as the base-channel code below.

#define PRINT_MS      1000UL  // Serial print interval
#define DEBOUNCE        50UL  // Button debounce (ms) — unused, no reset button on Central
#define NET_RETRY    60000UL  // Ethernet / MQTT retry interval (ms)
#define BLINK_MS       400UL  // blink half-period (ms)
#define MAX_TRACKED_ZONES 32  // upper bound on distinct zones observed system-wide

// ---------------------------------------------------------------------------
// Per-zone runtime state (local sensors)
// ---------------------------------------------------------------------------
struct ZoneState {
  bool active      = false;
  bool pending     = false;  // publish retry queued
  bool pendingVal  = false;
};
static ZoneState zoneState[ZONE_COUNT];

// ---------------------------------------------------------------------------
// System-wide zone tracking (from the shared status/ tree — drives the siren)
// ---------------------------------------------------------------------------
struct TrackedZone {
  String topic;
  bool   active;
  bool   used = false;
};
static TrackedZone tracked[MAX_TRACKED_ZONES];

bool blinkState = false;

unsigned long printT      = 0;
unsigned long pollT       = 0;
unsigned long ethRetryT   = 0;
unsigned long mqttRetryT  = 0;
unsigned long blinkT      = 0;

String deviceId;
String topicOnline;

EthernetClient ethClient;
PubSubClient   mqttClient(ethClient);

// ---------------------------------------------------------------------------
// Forward declarations
// ---------------------------------------------------------------------------
static void   connectEthernet();
static void   reconnectNetwork();
static void   updateStatusLEDs();
static void   updateAlarmBlink();
static String getShortId();
static String zoneTopic(uint8_t ch);
static bool   publishZoneAlarm(uint8_t ch, bool active);
static bool   anyBaseTriggered(uint8_t ch);
static bool   anyLocalActive();
static void   onMqttMessage(char* topic, byte* payload, unsigned int length);
static void   updateSiren();
static bool   anySystemActive();

// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(500);

  analogReadResolution(12);

  // Relay 1 — siren/beacon, closes while any zone (local or remote) is active
  pinMode(RELAY1, OUTPUT); digitalWrite(RELAY1, LOW);

  // LEDs
  pinMode(LEDR,        OUTPUT); digitalWrite(LEDR,        LOW);
  pinMode(LEDG,        OUTPUT); digitalWrite(LEDG,        LOW);
  pinMode(LEDB,        OUTPUT); digitalWrite(LEDB,        LOW);
  pinMode(LED_RELAY1,  OUTPUT); digitalWrite(LED_RELAY1,  HIGH); // Always on = running
  pinMode(LED_RELAY2,  OUTPUT); digitalWrite(LED_RELAY2,  LOW);
  pinMode(LED_RELAY3,  OUTPUT); digitalWrite(LED_RELAY3,  LOW);
  pinMode(LED_RELAY4,  OUTPUT); digitalWrite(LED_RELAY4,  LOW);

  deviceId    = getShortId();
  topicOnline = "status/" EXPERIMENT_NAME "/" + deviceId + "/online";

  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setBufferSize(256);
  mqttClient.setCallback(onMqttMessage);

  connectEthernet();

  Serial.println("[CENTRAL] Ready — monitoring " + String(ZONE_COUNT) + " local zones");
}

void loop() {
  reconnectNetwork();
  mqttClient.loop();
  updateStatusLEDs();
  updateAlarmBlink();
  updateSiren();

  // Retry any queued zone publishes once connection is restored
  if (mqttClient.connected()) {
    for (uint8_t i = 0; i < ZONE_COUNT; i++) {
      if (zoneState[i].pending) publishZoneAlarm(i, zoneState[i].pendingVal);
    }
  }

  if (millis() - pollT < 20) return;
  pollT = millis();

  if (millis() - printT >= PRINT_MS) {
    printT = millis();
    Serial.print("[CENTRAL] ");
    Serial.print(anyLocalActive() ? "ALARM" : "NORMAL");
    Serial.print(Ethernet.linkStatus() == LinkON ? " [ETH:OK]"  : " [ETH:DOWN]");
    Serial.print(mqttClient.connected()           ? " [MQTT:OK]" : " [MQTT:DOWN]");
    Serial.println();
  }

  for (uint8_t i = 0; i < ZONE_COUNT; i++) {
    bool active = anyBaseTriggered(i);
    if (active != zoneState[i].active) {
      zoneState[i].active = active;
      Serial.print("[CENTRAL] Zone "); Serial.print(i);
      Serial.println(active ? " >>> ACTIVE <<<" : " >>> CLEAR <<<");
      publishZoneAlarm(i, active);
    }
  }
}

// ---------------------------------------------------------------------------
// Initial Ethernet connect with a short timeout so setup() never stalls long.
// If it fails, reconnectNetwork() in loop() retries every 5s.
// ---------------------------------------------------------------------------
static void connectEthernet() {
  Serial.println("[Ethernet] Requesting DHCP lease...");
  if (Ethernet.begin(const_cast<uint8_t*>(ETHERNET_MAC), 4000) == 0) {
    Serial.println("[Ethernet] DHCP failed — will retry in loop");
    return;
  }
  Serial.print("[Ethernet] Connected, IP=");
  Serial.println(Ethernet.localIP());
}

// ---------------------------------------------------------------------------
// Non-blocking network reconnect called every loop().
// Retries Ethernet then MQTT independently every NET_RETRY ms.
// Sets an LWT so the broker marks Central offline (retained) on ungraceful drop.
// ---------------------------------------------------------------------------
static void reconnectNetwork() {
  if (Ethernet.linkStatus() == LinkOFF) {
    if (millis() - ethRetryT >= NET_RETRY) {
      ethRetryT = millis();
      Serial.println("[Ethernet] No link — retrying...");
      if (Ethernet.begin(const_cast<uint8_t*>(ETHERNET_MAC), 4000) != 0) {
        Serial.print("[Ethernet] Reconnected, IP=");
        Serial.println(Ethernet.localIP());
      }
    }
    return;  // No MQTT without Ethernet
  }

  Ethernet.maintain();

  if (!mqttClient.connected() && millis() - mqttRetryT >= NET_RETRY) {
    mqttRetryT = millis();
    Serial.print("[MQTT] Connecting...");
    String clientId = "opta-central-" + deviceId;
    bool ok = mqttClient.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD,
                                  topicOnline.c_str(), 0, true, "offline");
    if (ok) {
      Serial.println(" OK");
      mqttClient.publish(topicOnline.c_str(), "online", true);
      mqttClient.subscribe("status/#");
      // Re-announce any zones already known to be active (retained state may
      // have been lost if the broker restarted while we were disconnected).
      for (uint8_t i = 0; i < ZONE_COUNT; i++) {
        if (zoneState[i].active) publishZoneAlarm(i, true);
      }
    } else {
      Serial.print(" failed rc=");
      Serial.println(mqttClient.state());
    }
  }
}

// ---------------------------------------------------------------------------
static void updateStatusLEDs() {
  if (!anyLocalActive()) {
    digitalWrite(LED_RELAY2, Ethernet.linkStatus() == LinkON ? HIGH : LOW);
    digitalWrite(LED_RELAY3, mqttClient.connected()           ? HIGH : LOW);
  }
}

// ---------------------------------------------------------------------------
// Derive a short device ID from the last 3 MAC bytes
// ---------------------------------------------------------------------------
static String getShortId() {
  char buf[7];
  snprintf(buf, sizeof(buf), "%02X%02X%02X",
           ETHERNET_MAC[3], ETHERNET_MAC[4], ETHERNET_MAC[5]);
  return String(buf);
}

static String zoneTopic(uint8_t ch) {
  return "status/" EXPERIMENT_NAME "/" + deviceId + "/alarm/" +
         ZONE_LAB_ID[ch] + "/" + ZONE_HAZARD[ch];
}

// ---------------------------------------------------------------------------
// Publish one zone's alarm JSON to MQTT. Queues retry if not connected.
// ---------------------------------------------------------------------------
static bool publishZoneAlarm(uint8_t ch, bool active) {
  if (!mqttClient.connected()) {
    zoneState[ch].pending    = true;
    zoneState[ch].pendingVal = active;
    return false;
  }

  char payload[96];
  snprintf(payload, sizeof(payload),
           "{\"active\":%s,\"locationDetail\":\"\",\"description\":\"Central zone %d\"}",
           active ? "true" : "false", ch);

  String topic = zoneTopic(ch);
  bool ok = mqttClient.publish(topic.c_str(), payload, /*retain=*/true);
  if (ok) {
    Serial.print("[MQTT] Published "); Serial.print(topic); Serial.print(": "); Serial.println(payload);
    zoneState[ch].pending = false;
  } else {
    Serial.println("[MQTT] Publish failed — queuing retry");
    zoneState[ch].pending    = true;
    zoneState[ch].pendingVal = active;
  }
  return ok;
}

// ---------------------------------------------------------------------------
static bool anyBaseTriggered(uint8_t ch) {
  return analogRead(A0 + ch) > ZONE_THRESHOLD[ch];
}

static bool anyLocalActive() {
  for (uint8_t i = 0; i < ZONE_COUNT; i++) if (zoneState[i].active) return true;
  return false;
}

// ---------------------------------------------------------------------------
// Blinks LEDR+LEDG (orange) while any LOCAL zone is active.
// ---------------------------------------------------------------------------
static void updateAlarmBlink() {
  if (!anyLocalActive()) {
    digitalWrite(LEDR, LOW);
    digitalWrite(LEDG, LOW);
    blinkState = false;
    return;
  }
  if (millis() - blinkT < BLINK_MS) return;
  blinkT     = millis();
  blinkState = !blinkState;
  uint8_t v = blinkState ? HIGH : LOW;
  digitalWrite(LEDR, v);
  digitalWrite(LEDG, v);
  digitalWrite(LEDB, LOW);
}

// ---------------------------------------------------------------------------
// Tracks every zone seen on the shared status/ tree (local + every other
// device) so the siren reacts to ANY active alarm system-wide, not just
// Central's own sensors. Linear scan over a small fixed table — fine for a
// handful of devices; not meant to scale past a small lab.
// ---------------------------------------------------------------------------
static void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  String t(topic);
  if (t.indexOf("/alarm/") < 0) return;  // only care about alarm zone topics

  char buf[96];
  unsigned int n = min(length, (unsigned int)sizeof(buf) - 1);
  memcpy(buf, payload, n);
  buf[n] = '\0';
  bool active = (strstr(buf, "\"active\":true") != nullptr);

  for (uint8_t i = 0; i < MAX_TRACKED_ZONES; i++) {
    if (tracked[i].used && tracked[i].topic == t) {
      tracked[i].active = active;
      return;
    }
  }
  for (uint8_t i = 0; i < MAX_TRACKED_ZONES; i++) {
    if (!tracked[i].used) {
      tracked[i] = { t, active, true };
      return;
    }
  }
  Serial.println("[CENTRAL] MAX_TRACKED_ZONES exceeded — zone not tracked: " + t);
}

static bool anySystemActive() {
  for (uint8_t i = 0; i < MAX_TRACKED_ZONES; i++) {
    if (tracked[i].used && tracked[i].active) return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Drives RELAY1 (siren/beacon) while any zone, anywhere, is active.
// ---------------------------------------------------------------------------
static void updateSiren() {
  digitalWrite(RELAY1, anySystemActive() ? HIGH : LOW);
}