// =============================================================================
// SensorStream.cpp — see SensorStream.h.
// =============================================================================

#include "SensorStream.h"
#include "Kitchen_Settings.h"
#include "Expansion.h"
#include "Comms.h"
#include "Protocol.h"
#include <Arduino.h>
#include <string.h>
#include <stdio.h>

// A0602 is always expansion index 0 (Kitchen_Settings.h / Expansion.cpp
// convention: A0602 @0, D1608E @1).
#define H2_EXPANSION_INDEX  0

static H2LatestSample _latest[KITCHEN_WIRED_LOCAL_SENSORS];
static uint32_t       _lastSampleMs = 0;

// counts conversion mirrors Sensors.cpp's currentToCounts() EXACTLY (same
// scale constants, same clamp) so the value KitchenCore sees via
// sensorStreamLatest() is identical to what a direct read would have
// produced. Two independent copies of this formula would be a correctness
// bug waiting to happen the day one of the scale constants changes.
static uint16_t currentToCounts(float mA, int sensorIdx) {
  float corrected = mA;
  if (sensorIdx >= 0 && sensorIdx < KITCHEN_MAX_LOCAL_SENSORS) {
    corrected += SENSOR_CALIBRATION_OFFSET_MA[sensorIdx];
  }
  float c = (float)ADC_COUNTS_AT_4MA + (corrected - 4.0f) * ADC_COUNTS_PER_MA;
  if (c < 0.0f) c = 0.0f;
  if (c > ADC_MAX_COUNTS) c = ADC_MAX_COUNTS;
  return (uint16_t)(c + 0.5f);
}

// ---------------------------------------------------------------------------
void sensorStreamBegin() {
  memset(_latest, 0, sizeof(_latest));
  _lastSampleMs = 0;
}

void sensorStreamTick(uint32_t nowMs) {
  if (nowMs - _lastSampleMs < SENSOR_SAMPLE_INTERVAL_MS) return;
  _lastSampleMs = nowMs;

  // ONE I2C transaction refreshes every armed A0602 channel's register cache.
  expansionRefreshAnalogInputs(H2_EXPANSION_INDEX);

  for (int i = 0; i < KITCHEN_WIRED_LOCAL_SENSORS; i++) {
    int pin = KITCHEN_LOCAL_SENSOR_PINS[i];
    // All KITCHEN_WIRED_LOCAL_SENSORS are A0602 current inputs today
    // (KITCHEN_LOCAL_SENSOR_IS_CURRENT is all-true) — see Kitchen_Settings.h.
    // If a future sensor slot is voltage, add the branch here the same way
    // Sensors.cpp's old direct-read loop did.
    float mA = expansionReadCurrentCached(pin);   // cache only, no I2C
    _latest[i].mA       = mA;
    _latest[i].counts   = currentToCounts(mA, i);
    _latest[i].everSeen = true;
  }
}

H2LatestSample sensorStreamLatest(int index) {
  if (index < 0 || index >= KITCHEN_WIRED_LOCAL_SENSORS) return H2LatestSample{};
  return _latest[index];
}

// ---------------------------------------------------------------------------
// Publishing — round-robin one sensor per call, budget-capped so a slow link
// cannot stall the loop that also runs the safety interlock. See
// Kitchen_Settings.h for SENSOR_PUBLISH_INTERVAL_MS / SENSOR_PUBLISH_BUDGET_US
// and docs/FUTURE-sensor-batching.md for the deferred batched version.
// ---------------------------------------------------------------------------
static uint32_t _lastPublishMs = 0;
static int      _nextPublishIdx = 0;

static bool publishOne(int index, uint64_t tsMs) {
  const H2LatestSample& s = _latest[index];
  if (!s.everSeen) return true;   // nothing sampled yet — not a failure

  int pin = KITCHEN_LOCAL_SENSOR_PINS[index];
  char topic[96];
  snprintf(topic, sizeof(topic), "DataAcquisition/%s/%s/%s",
           DAQ_PUBLISH_LOCATION, KITCHEN_DAQ_DEVICE_ID,
           KITCHEN_LOCAL_SENSOR_NAMES[index]);

  char payload[96];
  size_t n = protocolBuildSensorSample(payload, sizeof(payload), pin, s.mA, tsMs);
  if (!n) return false;

  return mqttPublish(topic, payload, /*retain=*/false);
}

void sensorStreamPublish(uint32_t nowMs, uint64_t tsMs) {
  if (nowMs - _lastPublishMs < SENSOR_PUBLISH_INTERVAL_MS) return;
  _lastPublishMs = nowMs;

  if (!commsMqttConnected()) return;   // never attempt a publish on a down link

  // ONE sensor per call — the whole point of round-robin is to spread the 6
  // sensors' blocking publishes across 6 separate loop passes instead of
  // bunching them into one. A single mqttPublish() call cannot be interrupted
  // mid-write from here, so SENSOR_PUBLISH_BUDGET_US cannot cap ITS duration —
  // what it does is make an overrun VISIBLE (once, not every tick) rather than
  // a silently growing loop period. Comms.cpp's own _publishFails counter
  // (Comms.cpp:135) is what actually recovers a wedged-but-"connected" socket.
  uint32_t startUs = micros();
  publishOne(_nextPublishIdx, tsMs);
  _nextPublishIdx = (_nextPublishIdx + 1) % KITCHEN_WIRED_LOCAL_SENSORS;

  uint32_t tookUs = micros() - startUs;
  if (tookUs >= SENSOR_PUBLISH_BUDGET_US) {
    static uint32_t _lastOverrunLogMs = 0;
    if (nowMs - _lastOverrunLogMs >= 5000UL) {   // don't flood the log topic
      _lastOverrunLogMs = nowMs;
      logPrintf(LogLevel::ERROR,
               "[H2PUB] sensor publish took %luus (budget %luus) — link may be wedged",
               (unsigned long)tookUs, (unsigned long)SENSOR_PUBLISH_BUDGET_US);
    }
  }
}
