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
//   D2     run-state  : see runStatePeriod() — one LED, four codes by rate
//   D0-D2  ALARM       : fast-blink together (200 ms), overrides everything
//          peer-stale warn: D2 slow-blink (800 ms) only, connection normal
//
// LED_D3 IS NO LONGER OURS. The Opta BSP pairs each base relay with a status
// LED (LED_RELAY4 == LED_D3), and the alarm beacon now lives on base RELAY4
// — so D3 is the beacon's own indicator, driven by the relay itself. Writing
// it here would fight that. The run-state word lost its high bit as a result:
// it was D3 D2 (4 codes), it is now D2 alone (2 codes), which is why
// runStatePeriod() encodes the old 4 codes as blink RATES on D2 instead, so
// no state distinction is lost. The full state is on the webapp; these LEDs
// are a glanceable summary, not the authority.
// ---------------------------------------------------------------------------
#define ALARM_BLINK_MS      200UL   // fast — active alarm, whole bank
#define PEERSTALE_BLINK_MS  800UL   // slow — peer alarms went silent (warn only)
#define DROPPED_BLINK_MS    400UL   // a connection layer that dropped

static uint32_t _alarmBlinkMs = 0;   static bool _alarmBlinkOn = false;
static uint32_t _warnBlinkMs  = 0;   static bool _warnBlinkOn  = false;
static uint32_t _dropBlinkMs  = 0;   static bool _dropBlinkOn  = false;
static uint32_t _runBlinkMs   = 0;   static bool _runBlinkOn   = false;

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

// Run-state on the single remaining LED (D2), since D3 now belongs to the
// alarm beacon's relay. One LED still carries all four old codes by using
// its blink RATE as the second bit:
//
//   dark        WAITING / ARMED        (was 00)
//   solid       WARMING_UP / LEAKING   (was 01, also equipment-test)
//   slow blink  HOLD                   (was 10)
//   fast blink  VENTILATING / FULLY_VENTILATING (was 11)
//
// Returned as a period rather than a level so the caller owns the blink
// timing: 0 = solid-on, UINT32_MAX = off.
#define RUNSTATE_OFF     0xFFFFFFFFUL
#define RUNSTATE_SOLID   0UL
#define RUNSTATE_SLOW_MS 800UL
#define RUNSTATE_FAST_MS 250UL

static uint32_t runStatePeriod(KitchenState st, bool isLeakTestRole) {
  // Equipment-test (a role, not a state) shares the solid code with LEAKING.
  if (!isLeakTestRole && st == KitchenState::WAITING) return RUNSTATE_SOLID;
  switch (st) {
    case KitchenState::WAITING:
    case KitchenState::ARMED:              return RUNSTATE_OFF;
    case KitchenState::WARMING_UP:
    case KitchenState::LEAKING:            return RUNSTATE_SOLID;
    case KitchenState::HOLD:               return RUNSTATE_SLOW_MS;
    case KitchenState::VENTILATING:
    case KitchenState::FULLY_VENTILATING:  return RUNSTATE_FAST_MS;
  }
  return RUNSTATE_OFF;
}

static void driveStatusLeds(const OutputRequest& req, uint32_t nowMs,
                            KitchenState st, bool isLeakTestRole,
                            bool peerAlarmStale, bool roleMisflip) {
  // --- ALARM: fast-blink, overrides everything ---------------------
  // D3 excluded: it is the beacon relay's own status LED and is already solid
  // whenever the beacon is energised, which is exactly when alarmOn is true.
  if (req.alarmOn) {
    bool on = blinkTick(nowMs, ALARM_BLINK_MS, _alarmBlinkMs, _alarmBlinkOn);
    setLed(LED_D0, on); setLed(LED_D1, on);
    setLed(LED_D2, on);
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

  // --- D2: run-state (or peer-stale warn slow-blink) --------------
  // On a role misflip, force the code to the REAL run state (leak-test view),
  // not the equipment-test remap — that is the whole point: show whoever
  // flipped the switch which state the rig is actually in.
  bool d2;
  if (peerAlarmStale) {
    d2 = blinkTick(nowMs, PEERSTALE_BLINK_MS, _warnBlinkMs, _warnBlinkOn);
  } else {
    uint32_t period =
        runStatePeriod(st, /*isLeakTestRole=*/roleMisflip ? true : isLeakTestRole);
    if (period == RUNSTATE_OFF)        d2 = false;
    else if (period == RUNSTATE_SOLID) d2 = true;
    else                               d2 = blinkTick(nowMs, period,
                                                      _runBlinkMs, _runBlinkOn);
    // A misflip in WAITING/ARMED would otherwise show as a dark LED — blink it
    // so the flip is visible at all.
    if (roleMisflip && period == RUNSTATE_OFF) {
      d2 = blinkTick(nowMs, PEERSTALE_BLINK_MS, _warnBlinkMs, _warnBlinkOn);
    }
  }
  setLed(LED_D2, d2);
  // LED_D3 deliberately untouched — the alarm beacon relay owns it.
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
  // LED_D3 is NOT claimed: it is RELAY4's paired status LED (LED_RELAY4), and
  // RELAY4 is now the alarm beacon. Driving it here would fight the relay.

  // Base-board alarm beacon relay. Claimed here (and only here) so the beacon
  // is driven low before anything else can energise it.
  pinMode(RELAY_ALARM, OUTPUT);
  digitalWrite(RELAY_ALARM, LOW);

  expansionSetRelayExpansion(D1608E_EXP_INDEX);

  const int dacPins[2] = { PIN_FAN_SETPOINT, PIN_FLOW_SETPOINT };
  expansionApplyOutputConfig(dacPins, 2);

  const int pwmPins[1] = { PIN_GAS_LAMP };
  expansionApplyPwmConfig(pwmPins, 1);

  // Safe rest state: gas relay open (no flow), flowmeter setpoint 0 V, fan
  // speed 0, alarm off (above, base board), equipment-test indicator off, all
  // registers commanded closed, gas lamp dark.
  expansionSetRelay(RELAY_GAS,        false);
  expansionSetRelay(RELAY_EQUIP_TEST, false);
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

  // --- alarm relay (Opta BASE board, not the expansion) -------
  // digitalWrite, not expansionSetRelay: the base relays are on-chip GPIO and
  // expansionSetRelay() rejects non-expansion pins outright. Outputs.cpp is
  // still the sole pin writer, which is the invariant that matters.
  digitalWrite(RELAY_ALARM, req.alarmOn ? HIGH : LOW);

  // --- equipment-test indicator relay -------------------------
  // Closed ONLY in equipment-test bench mode (selector in equipment-test AND
  // state WAITING); open in every other state, including a mid-run selector
  // flip, which the core treats as inert.
  expansionSetRelay(RELAY_EQUIP_TEST, req.equipmentTestOn);

  // --- gas-may-be-present breathing lamp ---------------------
  driveGasLamp(req.gasMayBePresent, nowMs);

  // --- 4-LED status word ------------------------------------
  driveStatusLeds(req, nowMs, state, isLeakTestRole, peerAlarmStale, roleMisflip);
}
