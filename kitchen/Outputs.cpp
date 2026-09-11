// =============================================================================
// Outputs.cpp — see Outputs.h. The only pin writer.
// =============================================================================

#include "Outputs.h"
#include "Kitchen_Settings.h"
#include "Expansion.h"
#include "Comms.h"
#include <Arduino.h>

// ---------------------------------------------------------------------------
static RegisterSequencer _seq;

#define D1608E_EXP_INDEX  1    // plan wiring: A0602 at #0, D1608E relays at #1

// ---------------------------------------------------------------------------
// The four green LEDs as ONE 2+2 status word (review row 173). outputsDrive()
// is the sole writer of all four — Comms no longer touches them.
//
//   D1 D0  connection : 00 no link · 01 link, no MQTT · 11 MQTT up
//          a bit BLINKING instead of solid = that layer WAS up and dropped
//          (needs a "was up" latch per layer): D0 blink = lost link,
//          D1 blink + D0 solid = lost MQTT.
//   D3 D2  run-state  : 00 WAITING/ARMED · 01 LEAKING/EQUIPMENT_TEST ·
//          10 HOLD · 11 VENTILATING (+ FULLY_VENTILATING folds into 11)
//   all 4  ALARM       : fast-blink together (200 ms), overrides everything
//          peer-stale warn: D3/D2 slow-blink (800 ms) only, connection normal
// ---------------------------------------------------------------------------
#define ALARM_BLINK_MS      200UL   // fast — active alarm, whole bank
#define PEERSTALE_BLINK_MS  800UL   // slow — peer alarms went silent (warn only)
#define DROPPED_BLINK_MS    400UL   // a connection layer that dropped

static uint32_t _alarmBlinkMs = 0;   static bool _alarmBlinkOn = false;
static uint32_t _warnBlinkMs  = 0;   static bool _warnBlinkOn  = false;
static uint32_t _dropBlinkMs  = 0;   static bool _dropBlinkOn  = false;

// "Was up" latches for the two connection layers — set once each layer has
// been seen up, so a later drop shows as blink-not-dark rather than 00.
static bool _linkWasUp = false;
static bool _mqttWasUp = false;

// Gas-lamp breathe phase anchor.
static uint32_t _breatheAnchorMs = 0;
static bool     _breatheRunning  = false;

// ---------------------------------------------------------------------------
static float pctToVolts(float pct) {
  if (pct < 0.0f)   pct = 0.0f;
  if (pct > 100.0f) pct = 100.0f;
  return pct * 0.1f;          // 0-100 % -> 0-10 V
}

// Drive the six register coils for one desired RegisterSet, via the sequencer
// (which applies the inlet-open delay).
static void driveRegisters(const RegisterSet& desired, uint32_t nowMs) {
  CoilStates c = _seq.step(desired, nowMs);
  expansionSetRelay(RELAY_CENTRAL_OPEN,  c.centralOpen);
  expansionSetRelay(RELAY_CENTRAL_CLOSE, c.centralClose);
  expansionSetRelay(RELAY_EXHAUST_OPEN,  c.exhaustOpen);
  expansionSetRelay(RELAY_EXHAUST_CLOSE, c.exhaustClose);
  expansionSetRelay(RELAY_INLET_OPEN,    c.inletOpen);
  expansionSetRelay(RELAY_INLET_CLOSE,   c.inletClose);
}

// ---------------------------------------------------------------------------
// The 2+2 LED status word. Called every pass from outputsDrive().
// ---------------------------------------------------------------------------
static void setLed(int pin, bool on) { digitalWrite(pin, on ? HIGH : LOW); }

static bool blinkTick(uint32_t nowMs, uint32_t periodMs,
                      uint32_t& lastMs, bool& phase) {
  if (nowMs - lastMs >= periodMs) { lastMs = nowMs; phase = !phase; }
  return phase;
}

// Which run-state code (D3 D2) the current KitchenState maps to.
static void runStateBits(KitchenState st, bool isLeakTestRole,
                         bool& d3, bool& d2) {
  switch (st) {
    case KitchenState::WAITING:
    case KitchenState::ARMED:
      d3 = false; d2 = false; break;                 // 00
    case KitchenState::WARMING_UP:
    case KitchenState::LEAKING:
      d3 = false; d2 = true;  break;                 // 01 (also EQUIPMENT_TEST)
    case KitchenState::HOLD:
      d3 = true;  d2 = false; break;                 // 10
    case KitchenState::VENTILATING:
    case KitchenState::FULLY_VENTILATING:
      d3 = true;  d2 = true;  break;                 // 11
  }
  // Equipment-test (a role, not a state) shares the 01 code with LEAKING.
  if (!isLeakTestRole && st == KitchenState::WAITING) { d3 = false; d2 = true; }
}

static void driveStatusLeds(const OutputRequest& req, uint32_t nowMs,
                            KitchenState st, bool isLeakTestRole,
                            bool peerAlarmStale, bool roleMisflip) {
  // --- ALARM: whole bank fast-blink, overrides everything ----------
  if (req.alarmOn) {
    bool on = blinkTick(nowMs, ALARM_BLINK_MS, _alarmBlinkMs, _alarmBlinkOn);
    setLed(LED_D0, on); setLed(LED_D1, on);
    setLed(LED_D2, on); setLed(LED_D3, on);
    return;
  }

  // --- D1 D0: connection word -------------------------------------
  bool linkUp = commsLinkUp();
  bool mqttUp = commsMqttConnected();
  if (linkUp) _linkWasUp = true;
  if (mqttUp) _mqttWasUp = true;

  bool dropBlink = blinkTick(nowMs, DROPPED_BLINK_MS, _dropBlinkMs, _dropBlinkOn);

  bool d0, d1;
  if (mqttUp) {            // 11 — full stack up
    d0 = true;  d1 = true;
  } else if (linkUp) {     // 01 — link, no MQTT; D1 blinks if MQTT had been up
    d0 = true;
    d1 = _mqttWasUp ? dropBlink : false;
  } else {                 // 00 — no link; D0 blinks if link had been up
    d1 = false;
    d0 = _linkWasUp ? dropBlink : false;
  }
  setLed(LED_D0, d0);
  setLed(LED_D1, d1);

  // --- D3 D2: run-state word (or peer-stale warn slow-blink) ------
  bool d3, d2;
  // On a role misflip, force the code to the REAL run state (leak-test view),
  // not the equipment-test remap — that is the whole point of the blink: show
  // whoever flipped the switch which state the rig is actually in.
  runStateBits(st, /*isLeakTestRole=*/roleMisflip ? true : isLeakTestRole, d3, d2);
  if (peerAlarmStale) {
    bool on = blinkTick(nowMs, PEERSTALE_BLINK_MS, _warnBlinkMs, _warnBlinkOn);
    d3 = on; d2 = on;
  } else if (roleMisflip) {
    // Blink ONLY the two run-state LEDs at their real-state code; connection
    // word (D1/D0) untouched. Gated OFF the code bits so a 00-state still
    // blinks visibly (both off would be indistinguishable from solid).
    bool on = blinkTick(nowMs, PEERSTALE_BLINK_MS, _warnBlinkMs, _warnBlinkOn);
    d3 = d3 && on;
    d2 = d2 && on;
    if (!d3 && !d2) { d3 = on; d2 = on; }   // WAITING/ARMED code 00 -> blink both
  }
  setLed(LED_D2, d2);
  setLed(LED_D3, d3);
}

// ---------------------------------------------------------------------------
// Gas-lamp breathe ramp on the A0602 PWM channel. Duty sweeps 0->100->0 over
// GAS_LAMP_BREATHE_MS on a fast carrier so it dims smoothly. Off when clear.
// ---------------------------------------------------------------------------
static void driveGasLamp(bool on, uint32_t nowMs) {
  if (!on) {
    if (_breatheRunning) { expansionWritePwm(PIN_GAS_LAMP, 0.0f); _breatheRunning = false; }
    return;
  }
  if (!_breatheRunning) { _breatheAnchorMs = nowMs; _breatheRunning = true; }

  uint32_t phase = (nowMs - _breatheAnchorMs) % GAS_LAMP_BREATHE_MS;
  float frac = (float)phase / (float)GAS_LAMP_BREATHE_MS;   // 0..1
  // Triangle 0->1->0, then squared for a softer visual ramp.
  float tri  = frac < 0.5f ? (frac * 2.0f) : (2.0f - frac * 2.0f);
  float duty = tri * tri * 100.0f;
  expansionWritePwm(PIN_GAS_LAMP, duty);
}

// ---------------------------------------------------------------------------
void outputsBegin() {
  pinMode(LED_D0, OUTPUT); setLed(LED_D0, false);
  pinMode(LED_D1, OUTPUT); setLed(LED_D1, false);
  pinMode(LED_D2, OUTPUT); setLed(LED_D2, false);
  pinMode(LED_D3, OUTPUT); setLed(LED_D3, false);

  expansionSetRelayExpansion(D1608E_EXP_INDEX);

  const int dacPins[2] = { PIN_FAN_SETPOINT, PIN_FLOW_SETPOINT };
  expansionApplyOutputConfig(dacPins, 2);

  const int pwmPins[1] = { PIN_GAS_LAMP };
  expansionApplyPwmConfig(pwmPins, 1);

  // Safe rest state: gas relay open (no flow), flowmeter setpoint 0 V, fan
  // speed 0, alarm off, all registers commanded closed, gas lamp dark.
  expansionSetRelay(RELAY_GAS,   false);
  expansionSetRelay(RELAY_ALARM, false);
  expansionWriteVoltage(PIN_FLOW_SETPOINT, 0.0f);
  expansionWriteVoltage(PIN_FAN_SETPOINT,  0.0f);
  expansionWritePwm(PIN_GAS_LAMP, 0.0f);
  driveRegisters(RegisterSet{}, millis());
}

// ---------------------------------------------------------------------------
void outputsDrive(const OutputRequest& req, uint32_t nowMs, bool peerAlarmStale,
                  KitchenState state, bool isLeakTestRole, bool roleMisflip) {
  // --- gas: two independent cuts ------------------------------------
  // RELAY_GAS closed == gas may flow; PIN_FLOW_SETPOINT carries the rate. When
  // gasOpen is false BOTH go safe — the "gas cut two ways" the plan requires in
  // FULLY_VENTILATING, applied here for every non-LEAKING state too.
  expansionSetRelay(RELAY_GAS, req.gasOpen);
  // Setpoint normally follows gasOpen (both go safe together). The ONE
  // exception: WAITING equipment-test drives the setpoint DAC for a bench
  // range-check with the supply relay still cut (req.flowSetpointTest).
  bool driveSetpoint = req.gasOpen || req.flowSetpointTest;
  expansionWriteVoltage(PIN_FLOW_SETPOINT,
                        driveSetpoint ? pctToVolts(req.gasSetpointPct) : 0.0f);

  // --- ventilation registers (sequenced) --------------------------
  driveRegisters(req.registers, nowMs);

  // --- fan speed --------------------------------------------------
  // This 8-relay build has no dedicated fan on/off relay — the fan contactor
  // follows the speed DAC (0 V = off). If an on/off relay is wired on a 2nd
  // D1608E later, add RELAY_FAN to Kitchen_Settings.h and drive it here from
  // (req.fanSpeedPct > 0.0f).
  expansionWriteVoltage(PIN_FAN_SETPOINT, pctToVolts(req.fanSpeedPct));

  // --- alarm relay --------------------------------------------
  expansionSetRelay(RELAY_ALARM, req.alarmOn);

  // --- gas-may-be-present breathing lamp ---------------------
  driveGasLamp(req.gasMayBePresent, nowMs);

  // --- 4-LED status word ------------------------------------
  driveStatusLeds(req, nowMs, state, isLeakTestRole, peerAlarmStale, roleMisflip);
}
