// =============================================================================
// Sensors.cpp — see Sensors.h.
// =============================================================================

#include "Sensors.h"
#include "Kitchen_Settings.h"
#include "Expansion.h"
#include <Arduino.h>
#include <string.h>

// ---------------------------------------------------------------------------
static SensorState _state;

// Per-sensor last-fresh timestamp (ms). A reading is "stale" when nothing has
// moved it within LOCAL_SENSOR_STALE_MS. We treat "value changed OR first read"
// as fresh; a genuinely dead channel reads a frozen count and goes stale.
static uint32_t _lastFreshMs[KITCHEN_MAX_LOCAL_SENSORS];
static uint16_t _lastCounts[KITCHEN_MAX_LOCAL_SENSORS];
static bool     _seenOnce[KITCHEN_MAX_LOCAL_SENSORS];

// Flow-over-limit sustain window: set when flow first exceeds the limit, cleared
// when it drops back. flowOverLimitSustained is true once it has been over for
// FLOW_LIMIT_SUSTAINED_MS continuously.
static uint32_t _flowOverSinceMs = 0;

// Per-sensor danger thresholds, seeded to the compiled default and overridden
// by the website via config/set. Values are stored ALREADY CLAMPED, and
// dangerActive() clamps again at the comparison — the website can only make a
// sensor more sensitive.
static uint16_t _thresholdCounts[KITCHEN_MAX_LOCAL_SENSORS];

// Peer alarms — tracked PER ZONE (see PeerAlarmTable in KitchenCore.h). A flat
// boolean here was a real bug: zone B publishing {danger:false} cleared zone
// A's active alarm, silently releasing the primary lab-wide interlock.
static PeerAlarmTable _peerAlarms;
#define PEER_ALARM_STALE_MS  30000UL   // peer alarms silent this long -> warn (not trip)

// ---------------------------------------------------------------------------
// counts helpers. Base spares are 0-10 V; A0602 I1-I6 are 4-20 mA. Both are
// reduced to a common raw-ADC-counts scale so KitchenCore compares like with
// like (see Kitchen_Settings.h UNIT RULE).
// ---------------------------------------------------------------------------
static uint16_t voltsToCounts(float volts) {
  float c = volts * (ADC_MAX_COUNTS / ADC_FULL_SCALE_V);
  if (c < 0.0f) c = 0.0f;
  if (c > ADC_MAX_COUNTS) c = ADC_MAX_COUNTS;
  return (uint16_t)(c + 0.5f);
}

// mA -> counts on the A0602 4-20 mA scale. The per-sensor calibration offset
// (wiring-length current loss) is applied HERE, once, and nowhere downstream.
static uint16_t currentToCounts(float mA, int sensorIdx) {
  float corrected = mA;
  if (sensorIdx >= 0 && sensorIdx < KITCHEN_MAX_LOCAL_SENSORS) {
    corrected += SENSOR_CALIBRATION_OFFSET_MA[sensorIdx];
  }
  // counts = live-zero + (mA - 4) * counts-per-mA. With placeholder scales
  // (ADC_COUNTS_PER_MA == 0) this collapses to the live-zero, which is fine for
  // desktop/bench work — the #error guard blocks a real firmware build until
  // the scale constants are measured.
  float c = (float)ADC_COUNTS_AT_4MA + (corrected - 4.0f) * ADC_COUNTS_PER_MA;
  if (c < 0.0f) c = 0.0f;
  if (c > ADC_MAX_COUNTS) c = ADC_MAX_COUNTS;
  return (uint16_t)(c + 0.5f);
}

static uint16_t readBaseCounts(int encodedPin) {
  // encodedPin here is a base pin 0..7 -> A0..A7
  static const int A_MAP[8] = { A0, A1, A2, A3, A4, A5, A6, A7 };
  if (encodedPin < 0 || encodedPin > 7) return 0;
  return (uint16_t)analogRead(A_MAP[encodedPin]);
}

// ---------------------------------------------------------------------------
void sensorsBegin() {
  analogReadResolution(BIT_RESOLUTION);

  // Arm the A0602 H2 current-input channels (base spares need no arming).
  expansionApplyInputConfig(KITCHEN_LOCAL_SENSOR_PINS,
                            KITCHEN_LOCAL_SENSOR_IS_CURRENT,
                            KITCHEN_WIRED_LOCAL_SENSORS);

  uint32_t now = millis();
  for (int i = 0; i < KITCHEN_MAX_LOCAL_SENSORS; i++) {
    _lastFreshMs[i]     = now;
    _lastCounts[i]      = 0;
    _seenOnce[i]        = false;
    _thresholdCounts[i] = SENSOR_THRESHOLD_DEFAULT_COUNTS;
  }
  _flowOverSinceMs = 0;
  _peerAlarms.reset();

  memset(&_state, 0, sizeof(_state));
  _state.isLeakTestRole = true;
}

// ---------------------------------------------------------------------------
void sensorsPoll(uint32_t nowMs, bool localSensorsPowered) {
  // --- local H2 sensors -------------------------------------------------
  _state.localSensorCount = KITCHEN_WIRED_LOCAL_SENSORS;
  for (int i = 0; i < KITCHEN_WIRED_LOCAL_SENSORS; i++) {
    int   pin  = KITCHEN_LOCAL_SENSOR_PINS[i];
    bool  isI  = KITCHEN_LOCAL_SENSOR_IS_CURRENT[i];

    uint16_t counts;
    if (PIN_IS_EXPANSION(pin)) {
      counts = isI ? currentToCounts(expansionReadCurrent(pin), i)
                   : voltsToCounts(expansionReadVoltage(pin));
    } else {
      counts = readBaseCounts(pin);   // base spare, raw 0-10 V ADC counts
    }

    // Staleness: fresh if the value moved, or on the first read. A dead
    // channel returns a frozen value and ages into stale.
    if (!_seenOnce[i] || counts != _lastCounts[i]) {
      _lastFreshMs[i] = nowMs;
      _seenOnce[i]    = true;
    }
    _lastCounts[i] = counts;
    bool stale = (nowMs - _lastFreshMs[i]) >= LOCAL_SENSOR_STALE_MS;

    LocalSensorReading& r = _state.localSensors[i];
    r.present         = true;
    r.counts          = counts;
    // Per-sensor threshold: the compiled default until the website sends a
    // config/set table. Stored clamped; dangerActive() clamps again at the
    // comparison, so this value can only ever make the sensor MORE sensitive.
    r.thresholdCounts = _thresholdCounts[i];
    r.stale           = stale;
    r.expectedOn      = localSensorsPowered;
  }
  for (int i = KITCHEN_WIRED_LOCAL_SENSORS; i < KITCHEN_MAX_LOCAL_SENSORS; i++) {
    _state.localSensors[i] = LocalSensorReading{};   // present = false
  }

  // --- flow feedback + sustain window --------------------------------
  uint16_t flowCounts = readBaseCounts(PIN_FLOW_FEEDBACK);
  _state.flowCounts = flowCounts;
  if (flowCounts >= FLOW_LIMIT_COUNTS) {
    if (_flowOverSinceMs == 0) _flowOverSinceMs = (nowMs == 0) ? 1 : nowMs;
  } else {
    _flowOverSinceMs = 0;
  }
  _state.flowOverLimitSustained =
      (_flowOverSinceMs != 0) &&
      (nowMs - _flowOverSinceMs >= FLOW_LIMIT_SUSTAINED_MS);

  // deliveredInventory_mL is integrated by KitchenCore itself; it only reads it
  // back off SensorState for the cap check, so we mirror the core's value in
  // kitchen.ino after update(). Leave whatever was last set here.

  // --- role selector + e-stop --------------------------------------
  uint16_t selCounts = readBaseCounts(PIN_ROLE_SELECTOR);
  _state.isLeakTestRole = (selCounts <= SELECTOR_THRESHOLD_COUNTS);

  uint16_t estopCounts = readBaseCounts(PIN_ESTOP);
  // E-stop wiring: pressed pulls the input HIGH (>2.5 V). Matches the plan's
  // "0-5V physical e-stop"; invert here if the real button is active-low.
  _state.estopPressed = (estopCounts >= SELECTOR_THRESHOLD_COUNTS);

  // --- expansion health (danger condition, not a warn) -------------
  // A missing A0602 makes every H2 current sensor read 0 counts; dangerActive()
  // turns this into an EXPANSION_FAULT so the run does not proceed blind.
  _state.expansionUnhealthy = !expansionHealthy();

  // --- peer alarms: OR across zones, staleness warn-only ----------
  // Recomputed every pass from the per-zone table so a cleared zone releases
  // the interlock only when EVERY zone is clear.
  _state.peerAlarmActive = _peerAlarms.anyActive();
  _state.peerAlarmStale  = _peerAlarms.everSeen() &&
                           _peerAlarms.anyStale(nowMs, PEER_ALARM_STALE_MS);

  // externalTripActive stays false — reserved, not wired (plan "Future").
}

// ---------------------------------------------------------------------------
const SensorState& sensorsState() { return _state; }

bool sensorsSetPeerAlarm(const char* topic, bool active, uint32_t nowMs) {
  bool tracked = _peerAlarms.update(topic, active, nowMs);
  // Reflect immediately rather than waiting for the next poll — a peer alarm
  // arriving between polls should not sit undetected for a whole pass.
  _state.peerAlarmActive = _peerAlarms.anyActive();
  return tracked;
}

int  sensorsPeerZoneCount()    { return _peerAlarms.trackedZones(); }
bool sensorsPeerTableOverflow() { return _peerAlarms.overflowed(); }

bool sensorsSetThreshold(int sensorIndex, uint16_t counts) {
  if (sensorIndex < 0 || sensorIndex >= KITCHEN_MAX_LOCAL_SENSORS) return false;
  _thresholdCounts[sensorIndex] = counts;
  return true;
}

uint16_t sensorsThreshold(int sensorIndex) {
  if (sensorIndex < 0 || sensorIndex >= KITCHEN_MAX_LOCAL_SENSORS) {
    return SENSOR_THRESHOLD_DEFAULT_COUNTS;
  }
  return _thresholdCounts[sensorIndex];
}
void sensorsSetPermit(bool present, bool value) {
  _state.permitPresent = present;
  _state.permitValue   = value;
}
void sensorsSetDeliveredInventory(float mL) {
  _state.deliveredInventory_mL = mL;
}
