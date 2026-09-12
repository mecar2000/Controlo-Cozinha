#pragma once

// Kitchen_Settings.h — firmware constants: pin map, thresholds, timings,
// ceilings. PLACEHOLDER values must be confirmed on the bench before hydrogen.
// KitchenCore / RegisterSequencer use only these constants, never Arduino
// headers, so they stay desktop-testable.

#include <stdint.h>

// Sensor table sizing: 6 A0602 current sensors (I1-I6) currently wired
// (KITCHEN_WIRED_LOCAL_SENSORS below); 5 base-spare slots (A0, A4-A7) are
// reserved but NOT YET POPULATED, plus a little more headroom. The old
// ceiling of 21 reserved 10 slots for D1608E-analog sensors that are not
// planned; every unused slot is a zeroed LocalSensorReading, set once at
// sensorsBegin() (not re-zeroed every pass — see Sensors.cpp).
#define KITCHEN_MAX_LOCAL_SENSORS   15
#define KITCHEN_MAX_PEER_ZONES      16   // peer alarm zones tracked over MQTT,
                                         // one row each (PeerAlarmTable). An
                                         // alarming zone past this latches the
                                         // interlock safe rather than dropping.

// UNIT RULE: every concentration/flow value inside KitchenCore and RunSpec is
// in RAW ADC COUNTS. Conversion happens only in Sensors.cpp (inbound) and
// Protocol.cpp (MQTT boundary). BOUNDARY CONVENTION: all comparisons are
// AT-OR-ABOVE (>=) — a reading exactly at threshold trips.

// Danger threshold — firmware CEILING. Effective = min(firmware_max, website).
//
// DIRECTION MATTERS: counts rise with concentration and the danger check trips
// on `counts >= threshold`, so a HIGHER threshold trips LATER = LESS sensitive.
// The website may therefore only LOWER the threshold (more sensitive); a
// request to raise it above this ceiling is clamped back down. An earlier
// version used max() against a "minimum", which let the website delay the trip
// — the opposite of the intended safety property.
// PLACEHOLDER — calibrate before hydrogen. BLOCKING BEFORE HYDROGEN.
#define SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS   1024   // ~2.5V on 0-10V/12-bit
#define SENSOR_THRESHOLD_DEFAULT_COUNTS         900   // used when website sends nothing
// DEFAULT must stay strictly below MAX: DEFAULT is what a fresh/reset sensor
// trips at, MAX is the ceiling the website's requested threshold is clamped
// to. Equal values made clampThreshold() a no-op on the default fixture,
// silently gutting several desktop tests (see testproblems.txt section 1) —
// this keeps the two from drifting back together unnoticed.
static_assert(SENSOR_THRESHOLD_DEFAULT_COUNTS < SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS,
             "SENSOR_THRESHOLD_DEFAULT_COUNTS must stay strictly below "
             "SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS or the clamp becomes "
             "untestable on the default fixture");

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
#define ADC_COUNTS_PER_MA            1.0f    // PLACEHOLDER — counts per 1 mA
#define ADC_COUNTS_PER_MA_SET           0

#define ADC_COUNTS_AT_4MA               1    // PLACEHOLDER — live-zero of the 4-20mA loop
#define ADC_COUNTS_AT_4MA_SET           0

#define SENSOR_FULLSCALE_PCT         1.0f    // PLACEHOLDER — 4-20mA maps to 0-<FS>% H2 by volume
#define SENSOR_FULLSCALE_PCT_SET        0

#define FLOW_COUNTS_PER_VOLT         1.0f    // PLACEHOLDER — counts per 1 V, BASE board ADC
#define FLOW_COUNTS_PER_VOLT_SET        0

#define FLOW_ML_PER_SEC_AT_10V       1.0f    // PLACEHOLDER — flowmeter full-scale rate at 10 V
#define FLOW_ML_PER_SEC_AT_10V_SET      0

// This PLC's own sensors going silent this long while expected ON is a danger condition.
#define LOCAL_SENSOR_STALE_MS                  10000UL   // 10 s

// Per-sensor calibration offset (mA) for wiring-length current loss — same
// sensor type, different wire run per channel. Applied once at the read
// boundary in Sensors.cpp; indexed like SensorState.localSensors[].
static const float SENSOR_CALIBRATION_OFFSET_MA[KITCHEN_MAX_LOCAL_SENSORS] = {
  0.0f, 0.0f,                               // sensors 1-2  (A0602 I1-I2)
  0.0f, 0.0f, 0.0f, 0.0f,                   // sensors 3-6  (A0602 I3-I6)
  0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, // sensors 7-13 — headroom, unused
  0.0f, 0.0f,                               // sensors 14-15 — headroom, unused
  // Base-board spares (A0, A4-A7) are not wired yet (KITCHEN_WIRED_LOCAL_SENSORS
  // == 6); when they are, re-index them in here with real offsets.
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

// Sensor power — LOCAL and REMOTE are driven independently (see KitchenCore):
//   LOCAL  (this PLC's A0602/base H2 sensors): ON in LEAKING (leak-test) AND
//          whenever the role selector is equipment-test. 70 s warm-up gate
//          before gas can flow in LEAKING.
//   REMOTE (CM7 DAQ instances, commanded over MQTT): ON only during a
//          leak-test LEAKING run — never in equipment-test. The DAQ side owns
//          the relay that powers its sensors; it needs ~10 s to receive the
//          command, close that relay and power on (its own sensor warm-up is
//          ~1 s on top). We warn if no DataAcquisition/Kitchen traffic is seen
//          within REMOTE_SENSOR_LIVENESS_MS of commanding it on.
// Both fall back to OFF after SENSOR_IDLE_TIMEOUT_MS idle in WAITING.
#define SENSOR_WARMUP_MS                 70000UL   // LOCAL off->on gate before LEAKING flows gas
#define REMOTE_SENSOR_WARMUP_MS         10000UL   // REMOTE receive -> relay -> power-on
#define REMOTE_SENSOR_LIVENESS_MS      10000UL   // no DAQ traffic within this of power-on cmd -> warn
#define SENSOR_IDLE_TIMEOUT_MS           600000UL  // 10 min idle in WAITING -> sensors OFF (both)

// Topic prefix the CM7 DAQ publishes kitchen sensor samples under. The
// liveness check matches any topic beginning "<prefix>/<deviceId>/".
#define DAQ_KITCHEN_TOPIC_PREFIX  "DataAcquisition/Kitchen"

// MQTT payload sizing
#define MQTT_MAX_PAYLOAD                  2048

// --- Diagnostics / run modes (see Comms.h, "Diagnostics / run modes") ----
// Firmware default verbosity, as a RunMode ordinal (0=CLEAN 1=VERBOSE
// 2=DEBUG 3=PROFILING). Comms.h maps this to the RunMode enum. NOT retained
// over MQTT, so a reboot always returns here.
#define KITCHEN_DEFAULT_MODE_ORDINAL     1     // VERBOSE

// Compile ceiling. Comment this out for production builds: DEBUG and
// PROFILING are then compiled to no-ops and cannot be selected at runtime.
#define KITCHEN_ENABLE_DEBUG

// Identity — must match an experimentDefs entry in Firebase with mayEmit:true.
#define EXPERIMENT_NAME   "KitchenLeaks"   // PLACEHOLDER — confirm against experimentDefs
#define LAB_ID             "lab5"   // PLACEHOLDER — confirm against experimentDefs

// MQTT device id — the {deviceId} in every KitchenControl/{deviceId}/... topic.
// Must match the webapp's KITCHEN_DEVICE_ID (webapp/app/config.py, default
// "KITCHEN-01"). A fixed string, not MAC-derived, so the two sides agree with no
// configuration. Change here + in the webapp's .env together if it ever needs to.
#define KITCHEN_DEVICE_ID   "KITCHEN-01"

// Quorum threshold %<->counts conversion is firmware-owned (see RunSpec.h). The
// website sends the quorum threshold as a % of H2 by volume; Protocol converts
// it to counts once at the MQTT boundary using the same 4-20mA scale constants
// as Sensors.cpp. pctToCounts()/countsToPct() live in Protocol.cpp.

// %<->counts for the danger/quorum threshold uses the 4-20mA sensor scale:
//   counts = ADC_COUNTS_AT_4MA + (pct / SENSOR_FULLSCALE_PCT) * 16.0 * ADC_COUNTS_PER_MA
// i.e. pct 0 -> 4mA live-zero, pct == SENSOR_FULLSCALE_PCT -> 20mA.

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

// Below this line: hardware pin map. Arduino shim only (Outputs, Sensors,
// Expansion) — KitchenCore/RegisterSequencer never include pins.

// ---------------------------------------------------------------------------
// Encoded-pin scheme (shared with Expansion.h, same as the CM7 DAQ):
//   Base board : pin = 0..7                  -> A0..A7  (analogRead)
//   Expansion  : pin = 100*(expIdx+1) + ch   -> exp0 ch2 = 102
// One integer addresses any base or expansion channel. Sensors.cpp and
// Outputs.cpp pass these straight to Expansion.h; base pins it handles itself.
// ---------------------------------------------------------------------------
#define EXP_PIN_BASE           100
#define EXP_PIN_STRIDE         100
#define PIN_IS_EXPANSION(p)    ((p) >= EXP_PIN_BASE)
#define PIN_EXP_INDEX(p)       (((p) / EXP_PIN_STRIDE) - 1)
#define PIN_EXP_CHANNEL(p)     ((p) % EXP_PIN_STRIDE)
#define EXP_ENC(expIdx, ch)    (EXP_PIN_BASE + (EXP_PIN_STRIDE) * (expIdx) + (ch))

#define MAX_EXPANSIONS         2

// A0602 channel map — three honest constants replacing the old universal
// EXP_CHANNELS_PER_EXP (which was A0602-analog-only but named as if generic).
//   OA_CH_0..OA_CH_7  — 8 analog I/O channels (ADC in / voltage DAC out)
//   OA_CH_8..OA_CH_11 — 4 dedicated PWM channels, independent of the analog set
// The mode-cache arrays in Expansion.cpp are sized A0602_TOTAL_CHANNELS so the
// PWM channels (8..11) are addressable — the gas-lamp PWM needs OA_CH_8.
#define A0602_ANALOG_CHANNELS   8    // OA_CH_0..OA_CH_7 — analog I/O
#define A0602_PWM_CHANNELS      4    // OA_CH_8..OA_CH_11 — dedicated PWM
#define A0602_TOTAL_CHANNELS    12   // array sizing: analog + PWM
#define D1608E_DIGITAL_INPUTS   16   // D1608E has 16 digital inputs (none wired here)
#define D1608E_RELAY_COUNT      8    // D1608E has 8 relays (see relay map note below)

// ADC — Opta base analog inputs are 0-10 V, 12-bit.
#define BIT_RESOLUTION         12
#define ADC_MAX_COUNTS         ((1 << BIT_RESOLUTION) - 1)   // 4095
#define ADC_FULL_SCALE_V       10.0f

// Opta status LED pins — BSP usually provides these; fall back if not.
#ifndef LED_D0
  #define LED_D0  73
#endif
#ifndef LED_D1
  #define LED_D1  74
#endif
#ifndef LED_D2
  #define LED_D2  75
#endif
#ifndef LED_D3
  #define LED_D3  76
#endif

// Base board analog inputs (encoded pins 0..7 -> A0..A7)
#define PIN_H2_BASE_SPARE_0   0   // A0 — base-board spare H2 sensor slot (sensor 7)
#define PIN_FLOW_FEEDBACK     1   // A1 — 0-10V, rate direct / inventory by integration
#define PIN_ROLE_SELECTOR     2   // A2 — <=2.5V leak-test, >2.5V equipment-test
#define PIN_ESTOP             3   // A3 — 0-5V physical e-stop, works with no network
#define PIN_H2_BASE_SPARE_4   4   // A4 — base-board spare H2 sensor slot (sensor 8)
#define PIN_H2_BASE_SPARE_5   5   // A5 — base-board spare H2 sensor slot (sensor 9)
#define PIN_H2_BASE_SPARE_6   6   // A6 — base-board spare H2 sensor slot (sensor 10)
#define PIN_H2_BASE_SPARE_7   7   // A7 — base-board spare H2 sensor slot (sensor 11)

// Analog expansion (A0602) #0 — encoded pins. I1-I6 -> 6 H2 CURRENT sensors
// (4-20 mA). O1/O2 -> fan speed / flowmeter setpoint VOLTAGE DACs (0-10 V).
// A0602 channel layout: I1..I6 = OA_CH_0,1,2,3,5,6 ; O1 = OA_CH_4 ; O2 = OA_CH_7.
#define PIN_H2_1          EXP_ENC(0, 0)   // exp0 OA_CH_0
#define PIN_H2_2          EXP_ENC(0, 1)   // exp0 OA_CH_1
#define PIN_H2_3          EXP_ENC(0, 2)   // exp0 OA_CH_2
#define PIN_H2_4          EXP_ENC(0, 3)   // exp0 OA_CH_3
#define PIN_H2_5          EXP_ENC(0, 5)   // exp0 OA_CH_5
#define PIN_H2_6          EXP_ENC(0, 6)   // exp0 OA_CH_6
#define PIN_FAN_SETPOINT  EXP_ENC(0, 4)   // exp0 OA_CH_4 — O1, fan speed DAC
#define PIN_FLOW_SETPOINT EXP_ENC(0, 7)   // exp0 OA_CH_7 — O2, flowmeter setpoint DAC

// D1608E relay expansion #1 — encoded pins. 8 relays: 3 vent registers
// (2 coils each = 6) + fan on/off + alarm. NOTE: the plan listed 9 relays
// (adding a separate FLOWMETER_CUT) but the D1608E has only 8 — the flowmeter
// is cut by driving PIN_FLOW_SETPOINT to 0 V (the DAC), which is the second
// independent gas cut the plan requires alongside the gas relay. A dedicated
// hardware flowmeter-cut relay, if wired later, goes on a 2nd D1608E.
// PLACEHOLDER channel numbers — confirm against the physical wiring.
#define RELAY_CENTRAL_OPEN    EXP_ENC(1, 0)
#define RELAY_CENTRAL_CLOSE   EXP_ENC(1, 1)
#define RELAY_EXHAUST_OPEN    EXP_ENC(1, 2)
#define RELAY_EXHAUST_CLOSE   EXP_ENC(1, 3)
#define RELAY_INLET_OPEN      EXP_ENC(1, 4)
#define RELAY_INLET_CLOSE     EXP_ENC(1, 5)
#define RELAY_GAS             EXP_ENC(1, 6)   // gas supply relay: closed = gas can flow
#define RELAY_ALARM           EXP_ENC(1, 7)   // alarm beacon/siren

// "Gas may be present" breathing lamp — driven off an A0602 dedicated PWM
// channel (OA_CH_8), switching a logic-level MOSFET on the 24 V rail. The
// A0602's 8 analog channels are all in use (I1-I6 + O1/O2); the 4 PWM
// channels are separate hardware. The lamp is STATE-based, not sensor-based:
// lit whenever the core is not in WAITING.
#define PIN_GAS_LAMP          EXP_ENC(0, 8)   // exp0 OA_CH_8 — dedicated PWM
#define GAS_LAMP_BREATHE_MS   3000UL          // one full 0->100->0 duty sweep
#define GAS_LAMP_PWM_PERIOD_US 1000U          // 1 kHz carrier — smooth dim, no flicker

// Which base-board encoded pins carry H2 sensors, in SensorState.localSensors[]
// order. Sensors 1-6 are the A0602 current inputs (I1-I6). The five base-spare
// slots (A0, A4-A7) are NOT YET POPULATED with sensors — wiring them in as
// unconnected floating inputs let them participate in dangerActive() with a
// meaningless reading, so they are left out here rather than wired blind.
// When sensors are actually landed on those pins, re-add them (and give each
// a real SENSOR_CALIBRATION_OFFSET_MA / name) rather than restoring blindly.
#define KITCHEN_WIRED_LOCAL_SENSORS  6
static const int KITCHEN_LOCAL_SENSOR_PINS[KITCHEN_WIRED_LOCAL_SENSORS] = {
  PIN_H2_1, PIN_H2_2, PIN_H2_3, PIN_H2_4, PIN_H2_5, PIN_H2_6,
};
// true = read as 4-20 mA current (A0602 I1-I6). Same index order as
// KITCHEN_LOCAL_SENSOR_PINS.
static const bool KITCHEN_LOCAL_SENSOR_IS_CURRENT[KITCHEN_WIRED_LOCAL_SENSORS] = {
  true, true, true, true, true, true,
};
// Topic leaf name for each wired H2 sensor, same index order as
// KITCHEN_LOCAL_SENSOR_PINS — published under
// DataAcquisition/<DAQ_PUBLISH_LOCATION>/<KITCHEN_DAQ_DEVICE_ID>/<name>.
static const char* const KITCHEN_LOCAL_SENSOR_NAMES[KITCHEN_WIRED_LOCAL_SENSORS] = {
  "H2-1", "H2-2", "H2-3", "H2-4", "H2-5", "H2-6",
};

// ---------------------------------------------------------------------------
// Fast local H2 sensor sampling + publishing (see docs/FUTURE-sensor-batching.md
// for the deferred batched version). Sampling is decoupled from publishing:
// sample fast into a per-sensor "latest value" slot (SensorStream.cpp), then
// publish that slot on its own, slower, tunable cadence — publishing is a
// blocking MQTT/TCP write, and this loop also runs the safety interlock.
// ---------------------------------------------------------------------------
#define SENSOR_SAMPLE_INTERVAL_MS    10UL     // 1 I2C txn (all 6 channels) + cache
// Per-sensor publish interval. Lower = fresher data, more blocking TCP writes
// sharing the loop with the interlock. Tune this one constant; if raising the
// rate stops helping (profiler shows loop period growing), the next step is
// batching (docs/FUTURE-sensor-batching.md), not shrinking this further.
#define SENSOR_PUBLISH_INTERVAL_MS   20UL
// One publish tick writes ONE sensor's sample (round-robin — see
// SensorStream.cpp). A single mqttPublish() call cannot be interrupted
// mid-write, so this cannot cap that call's duration; it makes an overrun
// VISIBLE (a rate-limited log line) instead of a silently growing loop
// period. Actual recovery from a wedged-but-"connected" socket is
// Comms.cpp's existing _publishFails / commsForceReconnect() path.
#define SENSOR_PUBLISH_BUDGET_US     2000UL

// Relay writes are cached (Expansion.cpp): a write matching the last-written
// state is skipped rather than re-sent over I2C. Every RELAY_REASSERT_MS, the
// full 8-coil set is force-rewritten regardless of the cache, so a relay that
// somehow drifted off the shadow (bench interference, a manual toggle) is
// corrected on a bounded schedule rather than never.
#define RELAY_REASSERT_MS            1000UL

// DataAcquisition identity for this board's OWN sensor publishes — distinct
// from KITCHEN_DEVICE_ID (below), which names the KitchenControl/... control
// topics. Two separate namespaces; do not conflate them.
#define KITCHEN_DAQ_DEVICE_ID   "mainBoard"
#define DAQ_PUBLISH_LOCATION    "Kitchen"

// NTP — real wall-clock timestamps on sensor publishes (the historian discards
// any ts below a real-epoch floor and substitutes receipt time otherwise).
// The kitchen is Ethernet-only; DataAcquisition/CM7's syncClock() is WiFi-only
// (#ifndef USE_ETHERNET, calls WiFi.getTime()) and cannot be reused as-is —
// this uses NTPClient + EthernetUDP instead. If sync never succeeds (e.g. a
// firewalled lab network), timestamps fall back to millis() and a one-shot
// warning is logged — degraded resolution stays visible, never silent.
#define NTP_SERVER                "pool.ntp.org"
#define NTP_RESYNC_INTERVAL_MS    300000UL   // 5 min
