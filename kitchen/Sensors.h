#ifndef KITCHEN_SENSORS_H
#define KITCHEN_SENSORS_H

// =============================================================================
// Sensors.h — builds the SensorState that KitchenCore::update() reads.
//
// Owns, per the plan's "Units" and "Danger conditions" sections:
//   - base A0-A7 + A0602 I1-I6 reads
//   - the ONE calibrated-read boundary: per-sensor mA offset applied here and
//     nowhere else, then mA/volts -> raw ADC counts (KitchenCore is counts-only)
//   - role selector (A2), physical e-stop (A3), flow feedback (A1)
//   - the >2 s flow-over-limit sustain window
//   - local-sensor staleness timers (stale only trips danger when expectedOn)
//
// Peer-alarm and permit state arrive over MQTT — kitchen.ino pushes them in
// with the setters below rather than Sensors touching MQTT. Peer alarms are
// tracked PER ZONE (PeerAlarmTable, KitchenCore.h) so one zone publishing
// "clear" cannot cancel another zone's alarm; SensorState carries the OR.
// =============================================================================

#include <stdint.h>
#include "KitchenCore.h"   // SensorState

// Call once in setup() AFTER expansionBegin(). Arms the A0602 input channels
// and seeds staleness/flow timers.
void sensorsBegin();

// Call every loop() iteration. Reads all wired sensors, updates the flow
// sustain window and staleness timers.
//   nowMs           — millis()
//   localSensorsPowered — OutputRequest.localSensorsOn from the PREVIOUS pass;
//                     drives `expectedOn` so a stale reading only trips danger
//                     when the sensor is supposed to be powered.
void sensorsPoll(uint32_t nowMs, bool localSensorsPowered);

// The SensorState assembled from the most recent sensorsPoll(). Cheap copy.
const SensorState& sensorsState();

// --- pushed in from kitchen.ino's MQTT handler ------------------------------
// One peer-alarm message, keyed by its full topic so each zone is tracked
// independently. Self-filtering (ignoring our own alarm topic) stays in
// kitchen.ino, which knows the device id. Returns false if the zone table was
// full and the zone could not be tracked — caller should log that; the
// interlock itself is already held safe by the table's overflow latch.
bool sensorsSetPeerAlarm(const char* topic, bool active, uint32_t nowMs);

void sensorsSetPermit(bool present, bool value);

// Diagnostics for the log/state payload.
int  sensorsPeerZoneCount();
bool sensorsPeerTableOverflow();

// --- per-sensor danger thresholds (from the MQTT config/set table) ----------
// Set one sensor's threshold in COUNTS (Protocol owns the %->counts conversion
// and applies the safety clamp before calling this). Returns false on an
// out-of-range index. Seeded to SENSOR_THRESHOLD_DEFAULT_COUNTS at begin().
// Not persisted: a reboot returns every sensor to the compiled default.
bool     sensorsSetThreshold(int sensorIndex, uint16_t counts);
uint16_t sensorsThreshold(int sensorIndex);

// KitchenCore integrates delivered inventory itself but reads the running total
// back off SensorState for its INVENTORY_CAP danger check. kitchen.ino mirrors
// core.deliveredInventory_mL() into the state via this setter BEFORE each
// core.update() so the danger check sees the current figure, not last pass's.
void sensorsSetDeliveredInventory(float mL);

#endif // KITCHEN_SENSORS_H
