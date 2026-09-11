#ifndef KITCHEN_SENSOR_STREAM_H
#define KITCHEN_SENSOR_STREAM_H

// =============================================================================
// SensorStream.h — fast local H2 sensor sampling + publishing, decoupled from
// each other on purpose:
//
//   sensorStreamTick(now)    — every SENSOR_SAMPLE_INTERVAL_MS (10 ms): ONE I2C
//                               transaction (expansionRefreshAnalogInputs)
//                               refreshes all 6 A0602 channels, then 6 cached
//                               reads (no further I2C) update the "latest
//                               value" slot per sensor.
//   sensorStreamPublish(now) — every SENSOR_PUBLISH_INTERVAL_MS: emits the
//                               latest slot for ONE sensor (round-robin across
//                               calls) as a non-retained MQTT publish in the
//                               existing single-sample DataAcquisition format.
//
// Sampling is cheap (I2C only); publishing is a blocking MQTT/TCP write on
// the same loop that runs the safety interlock, so it is intentionally rarer
// and round-robined rather than firing all 6 sensors back to back. See
// Kitchen_Settings.h (SENSOR_PUBLISH_BUDGET_US) and
// docs/FUTURE-sensor-batching.md for the batched version that would let the
// publish rate matter less.
//
// This module reads Expansion.h (I2C) and calls mqttPublish() (Comms.h)
// directly — it is a hardware-facing peer of Sensors.cpp, not part of
// KitchenCore's pure decision path. KitchenCore never sees any of this.
// =============================================================================

#include <stdint.h>
#include "Kitchen_Settings.h"   // KITCHEN_WIRED_LOCAL_SENSORS

// One sensor's latest sample: calibrated mA (SENSOR_CALIBRATION_OFFSET_MA
// already applied, same as Sensors.cpp's currentToCounts() boundary) plus the
// counts conversion Sensors.cpp needs for dangerActive(), so BOTH consumers —
// the MQTT publisher (wants mA) and Sensors.cpp (wants counts, for
// KitchenCore) — read the SAME sample rather than each re-deriving it.
struct H2LatestSample {
  uint16_t counts  = 0;
  float    mA      = 0.0f;
  bool     everSeen = false;   // false until the first refresh
};

// Call once in setup() AFTER expansionApplyInputConfig() has armed the H2
// channels (same ordering requirement as sensorsBegin()).
void sensorStreamBegin();

// Call every loop() iteration; internally rate-gated to SENSOR_SAMPLE_INTERVAL_MS.
// The ONE I2C transaction per tick that refreshes all KITCHEN_WIRED_LOCAL_SENSORS
// channels lives here — Sensors.cpp no longer reads the A0602 itself; it reads
// sensorStreamLatest() instead. nowMs is millis().
void sensorStreamTick(uint32_t nowMs);

// The most recent sample for local H2 sensor `index` (0..KITCHEN_WIRED_LOCAL_SENSORS-1).
// Read-only, no I2C access — Sensors.cpp calls this every sensorsPoll() instead
// of reading the expansion directly. Returns a zeroed/everSeen=false sample for
// an out-of-range index.
H2LatestSample sensorStreamLatest(int index);

// Call every loop() iteration; internally rate-gated to SENSOR_PUBLISH_INTERVAL_MS.
// Publishes ONE sensor's latest sample per call (round-robin across calls,
// one full 6-sensor cycle every 6 publish ticks) — never all 6 in one loop
// pass. A publish that overruns SENSOR_PUBLISH_BUDGET_US is logged (rate
// limited) rather than silently absorbed; recovering an actually wedged link
// is Comms.cpp's existing _publishFails path. tsMs is the current wall-clock
// estimate from commsNowMs() (real epoch once NTP has synced, millis()
// otherwise — see Comms.h).
void sensorStreamPublish(uint32_t nowMs, uint64_t tsMs);

#endif // KITCHEN_SENSOR_STREAM_H
