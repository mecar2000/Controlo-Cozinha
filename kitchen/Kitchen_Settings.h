#pragma once

// Kitchen_Settings.h — firmware constants: pin map, thresholds, timings,
// ceilings. PLACEHOLDER values must be confirmed on the bench before hydrogen.
// KitchenCore / RegisterSequencer use only these constants, never Arduino
// headers, so they stay desktop-testable.

#include <stdint.h>

// Sensor table sizing: 5 base spare (A0, A4-A7) + 6 A0602 (I1-I6) +
// 10 D1608E-as-analog (6 of its 16 reserved for future buttons).
#define KITCHEN_MAX_LOCAL_SENSORS   21
#define KITCHEN_MAX_PEER_ZONES      32   // peer alarm zones tracked over MQTT

// UNIT RULE: every concentration/flow value inside KitchenCore and RunSpec is
// in RAW ADC COUNTS. Conversion happens only in Sensors.cpp (inbound) and
// Protocol.cpp (MQTT boundary). BOUNDARY CONVENTION: all comparisons are
// AT-OR-ABOVE (>=) — a reading exactly at threshold trips.

// Danger threshold — firmware MINIMUM. Effective = max(firmware_min, website).
// PLACEHOLDER — calibrate before hydrogen. BLOCKING BEFORE HYDROGEN.
#define SENSOR_THRESHOLD_FIRMWARE_MIN_COUNTS   1024   // ~2.5V on 0-10V/12-bit
#define SENSOR_THRESHOLD_DEFAULT_COUNTS        1024   // used when website sends nothing

// Analog scale conversions — TWO INDEPENDENT SCALES, do not cross them: mA
// constants are the A0602 H2 sensors only; volt constants are base-board A1
// flow feedback only (different ADC resolution). ALL FIVE ARE BLOCKING
// BEFORE HYDROGEN (see #error guard below). Each has a companion _SET flag
// (0 = placeholder, 1 = measured) since the guard is an integer #if and
// can't sentinel-check a float directly.
//
// Concentration: pct = (counts - ADC_COUNTS_AT_4MA) / ADC_COUNTS_PER_MA / 16.0
//                      * SENSOR_FULLSCALE_PCT
// Flow:          mL/s = (flowCounts / FLOW_COUNTS_PER_VOLT)
//                      * (FLOW_ML_PER_SEC_AT_10V / 10.0)
#define ADC_COUNTS_PER_MA            0.0f    // PLACEHOLDER — counts per 1 mA
#define ADC_COUNTS_PER_MA_SET           0

#define ADC_COUNTS_AT_4MA               0    // PLACEHOLDER — live-zero of the 4-20mA loop
#define ADC_COUNTS_AT_4MA_SET           0

#define SENSOR_FULLSCALE_PCT         0.0f    // PLACEHOLDER — 4-20mA maps to 0-<FS>% H2 by volume
#define SENSOR_FULLSCALE_PCT_SET        0

#define FLOW_COUNTS_PER_VOLT         0.0f    // PLACEHOLDER — counts per 1 V, BASE board ADC
#define FLOW_COUNTS_PER_VOLT_SET        0

#define FLOW_ML_PER_SEC_AT_10V       0.0f    // PLACEHOLDER — flowmeter full-scale rate at 10 V
#define FLOW_ML_PER_SEC_AT_10V_SET      0

// This PLC's own sensors going silent this long while expected ON is a danger condition.
#define LOCAL_SENSOR_STALE_MS                  10000UL   // 10 s

// Per-sensor calibration offset (mA) for wiring-length current loss — same
// sensor type, different wire run per channel. Applied once at the read
// boundary in Sensors.cpp; indexed like SensorState.localSensors[].
static const float SENSOR_CALIBRATION_OFFSET_MA[KITCHEN_MAX_LOCAL_SENSORS] = {
  0.0f, 0.0f,                   // sensors 1-2   (A0602 I1-I2)
  0.0f, 0.0f, 0.0f, 0.0f,       // sensors 3-6   (A0602 I3-I6)
  0.0f, 0.0f, 0.0f, 0.0f, 0.0f, // sensors 7-11  — PLACEHOLDER, base board spare (A0, A4-A7)
  0.0f, 0.0f, 0.0f, 0.0f, 0.0f, // sensors 12-16 — PLACEHOLDER, D1608E analog-in slot 1-5
  0.0f, 0.0f, 0.0f, 0.0f, 0.0f, // sensors 17-21 — PLACEHOLDER, D1608E analog-in slot 6-10
};

// Flow / inventory limits (leak-test only). Both BLOCKING BEFORE HYDROGEN.
// INVENTORY_CAP_ML needs FLOW_COUNTS_PER_VOLT / FLOW_ML_PER_SEC_AT_10V to be
// real before it can trip meaningfully.
#define FLOW_LIMIT_SUSTAINED_MS                2000UL    // flow > limit for this long trips
#define FLOW_LIMIT_COUNTS                      3000      // PLACEHOLDER — bench-calibrate
#define INVENTORY_CAP_ML                       5000.0f   // PLACEHOLDER — delivered inventory cap

// Role selector (base A2): <=2.5V leak-test, >2.5V equipment-test.
#define SELECTOR_THRESHOLD_COUNTS              1024   // ~2.5V on 0-10V/12-bit

// State timings
#define ARM_TIMEOUT_MS                  60000UL    // ARMED -> WAITING if not confirmed
#define FULLY_VENT_MIN_HOLD_MS         300000UL    // 5 min all-clear hold before auto-exit

// HOLD ceiling. Protocol clamps holdStop.maxDurationMs to this; an omitted
// holdStop falls back to this ceiling, not zero.
#define HOLD_MAX_DURATION_MS           600000UL    // PLACEHOLDER — 10 min, confirm before hydrogen

// Ventilation register actuation
#define INLET_OPEN_DELAY_MS              1000UL    // inlet opens this long after co-opened registers

// Fan speed by state — firmware-owned, not overridable by the website.
#define VENT_SPEED_IDLE_PCT                 10.0f  // PLACEHOLDER — WAITING/ARMED background rate;
                                                     // must satisfy equipment-test "ventilation running" precondition
#define VENT_SPEED_MAX_PCT                 100.0f  // ceiling clamp for website-requested speed

// DAQ sensor power command
#define SENSOR_WARMUP_MS                 30000UL   // off->on gate before LEAKING sequencing proceeds
#define SENSOR_IDLE_TIMEOUT_MS           600000UL  // 10 min idle in WAITING -> sensors OFF

// MQTT payload sizing
#define MQTT_MAX_PAYLOAD                  2048

// Identity — must match an experimentDefs entry in Firebase with mayEmit:true.
#define EXPERIMENT_NAME   "KitchenLeaks"   // PLACEHOLDER — confirm against experimentDefs
#define LAB_ID             "lab5"   // PLACEHOLDER — confirm against experimentDefs

// BLOCKING-BEFORE-HYDROGEN GUARD — refuses a firmware build (not desktop
// tests) with any analog-scale constant still a placeholder. Define
// KITCHEN_ALLOW_PLACEHOLDER_SCALES to build anyway (tests, pre-calibration bench work).
#ifndef KITCHEN_ALLOW_PLACEHOLDER_SCALES
  #if !ADC_COUNTS_PER_MA_SET
    #error "ADC_COUNTS_PER_MA is a placeholder. Measure it (see Kitchen_Settings.h), set ADC_COUNTS_PER_MA_SET to 1, or define KITCHEN_ALLOW_PLACEHOLDER_SCALES."
  #endif
  #if !ADC_COUNTS_AT_4MA_SET
    #error "ADC_COUNTS_AT_4MA is a placeholder. Measure it (see Kitchen_Settings.h), set ADC_COUNTS_AT_4MA_SET to 1, or define KITCHEN_ALLOW_PLACEHOLDER_SCALES."
  #endif
  #if !SENSOR_FULLSCALE_PCT_SET
    #error "SENSOR_FULLSCALE_PCT is a placeholder. Set it from the sensor datasheet, set SENSOR_FULLSCALE_PCT_SET to 1, or define KITCHEN_ALLOW_PLACEHOLDER_SCALES."
  #endif
  #if !FLOW_COUNTS_PER_VOLT_SET
    #error "FLOW_COUNTS_PER_VOLT is a placeholder. Measure it (see Kitchen_Settings.h), set FLOW_COUNTS_PER_VOLT_SET to 1, or define KITCHEN_ALLOW_PLACEHOLDER_SCALES."
  #endif
  #if !FLOW_ML_PER_SEC_AT_10V_SET
    #error "FLOW_ML_PER_SEC_AT_10V is a placeholder. Set it from the flowmeter datasheet, set FLOW_ML_PER_SEC_AT_10V_SET to 1, or define KITCHEN_ALLOW_PLACEHOLDER_SCALES."
  #endif
#endif

// Below this line: hardware pin map. Arduino shim only (Outputs, Sensors) —
// KitchenCore/RegisterSequencer never include pins.

// Base board analog inputs
#define PIN_FLOW_FEEDBACK   A1   // 0-10V, rate direct / inventory by integration
#define PIN_ROLE_SELECTOR   A2   // <=2.5V leak-test, >2.5V equipment-test
#define PIN_ESTOP            A3   // 0-5V physical e-stop, works with no network
// A0, A4-A7 spare / future H2 sensors

// Analog expansion (A0602) channels — encoded pin scheme, see Expansion.h.
// I1-I6 -> 6 H2 current sensors. O1/O2 -> fan speed / flowmeter setpoint (0-10V).
#define EXP_CH_H2_1          0   // OA_CH_0
#define EXP_CH_H2_2          1   // OA_CH_1
#define EXP_CH_H2_3          2   // OA_CH_2
#define EXP_CH_H2_4          3   // OA_CH_3
#define EXP_CH_H2_5          5   // OA_CH_5
#define EXP_CH_H2_6          6   // OA_CH_6
#define EXP_CH_FAN_OUT       4   // OA_CH_4 — O1, fan speed setpoint DAC
#define EXP_CH_FLOW_OUT      7   // OA_CH_7 — O2, flowmeter setpoint DAC

// D1608E relay expansion — 9 relays: 3 vent registers (2 coils each) + fan
// on/off + flowmeter cut + alarm. PLACEHOLDER channel numbers.
#define RELAY_CH_CENTRAL_OPEN    0
#define RELAY_CH_CENTRAL_CLOSE   1
#define RELAY_CH_EXHAUST_OPEN    2
#define RELAY_CH_EXHAUST_CLOSE   3
#define RELAY_CH_INLET_OPEN      4
#define RELAY_CH_INLET_CLOSE     5
#define RELAY_CH_FAN_ONOFF       6
#define RELAY_CH_FLOWMETER_CUT   7
#define RELAY_CH_ALARM           8
