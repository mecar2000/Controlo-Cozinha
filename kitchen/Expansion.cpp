// =============================================================================
// Expansion.cpp — Opta expansion implementation for the kitchen PLC.
// See Expansion.h. Based on DataAcquisition/CM7/Expansion.cpp, with DAC output
// and D1608E relay support added.
//
// Uses Arduino_Opta_Blueprint (OptaBlue.h) — the same library the CM7 DAQ and
// the Backbone/Controlo project use.
//
// ARMING MODEL (unchanged from CM7): analog channels are armed
// (beginChannelAs*Adc / beginChannelAsVoltageDac) at CONFIG time, NOT lazily in
// the read/write path. Arming and reading in the same breath returns a stale
// register that then stays frozen because the mode cache marks it "armed".
// Configure once in setup(), then in the loop just OptaController.update() +
// read/write.
// =============================================================================

#include "Expansion.h"
#include "Kitchen_Settings.h"

#include <OptaBlue.h>   // OptaController, AnalogExpansion, DigitalExpansion, OA_*

// ---------------------------------------------------------------------------
// Detection state
// ---------------------------------------------------------------------------
static int           _expCount    = 0;
static unsigned long _lastProbeMs = 0;
#define EXP_PROBE_INTERVAL_MS  3000UL

// Health: set by _probe() — exactly 2 expansions present AND types correct
// (A0602 at index 0, D1608E at index 1). expansionHealthy() exposes it.
static bool _expOk = false;

static int _relayExpIdx = -1;   // which expansion index is the D1608E, -1 = none

// ---------------------------------------------------------------------------
// Per-channel armed mode, indexed [expIdx][channel]. Sized A0602_TOTAL_CHANNELS
// (12) so the A0602 PWM channels (OA_CH_8..11) are addressable — the gas-lamp
// PWM lives on OA_CH_8. CH_PWM channels are not "armed" the way ADC/DAC are
// (the library's setPwm() needs no beginChannelAs* call); the slot is still
// tracked so _decode() and the write path can validate the mode.
// ---------------------------------------------------------------------------
enum ChMode : uint8_t {
  CH_NONE = 0, CH_VOLT_ADC = 1, CH_CURR_ADC = 2, CH_VOLT_DAC = 3, CH_PWM = 4
};
static ChMode _chMode[MAX_EXPANSIONS][A0602_TOTAL_CHANNELS];
static ChMode _wantMode[MAX_EXPANSIONS][A0602_TOTAL_CHANNELS];

static void _clearArmed() {
  for (int e = 0; e < MAX_EXPANSIONS; e++)
    for (int c = 0; c < A0602_TOTAL_CHANNELS; c++)
      _chMode[e][c] = CH_NONE;
}

// ---------------------------------------------------------------------------
// Arm a single analog channel in the requested mode and record it. Pumps
// OptaController.update() so the config message lands before any access.
// ---------------------------------------------------------------------------
static void _armChannel(int expIdx, int channel, ChMode want) {
  if (expIdx < 0 || expIdx >= _expCount)                return;
  if (channel < 0 || channel >= A0602_TOTAL_CHANNELS)   return;

  switch (want) {
    case CH_VOLT_ADC:
      AnalogExpansion::beginChannelAsVoltageAdc(OptaController, expIdx, (uint8_t)channel);
      break;
    case CH_CURR_ADC:
      AnalogExpansion::beginChannelAsCurrentAdc(OptaController, expIdx, (uint8_t)channel);
      break;
    case CH_VOLT_DAC:
      // 0-10 V DAC output. Args mirror the ADC begin* calls in this library.
      AnalogExpansion::beginChannelAsVoltageDac(OptaController, expIdx, (uint8_t)channel);
      break;
    case CH_PWM:
      // A0602 dedicated PWM channel (OA_CH_8..11). No beginChannelAs* step in
      // this library — setPwm(ch, period, pulse) is called directly. Recording
      // the mode is all the "arm" this needs; the first setPwm() with period 0
      // leaves the output low until outputsDrive() sets a real duty.
      break;
    default:
      _chMode[expIdx][channel] = CH_NONE;
      return;
  }
  _chMode[expIdx][channel] = want;
  OptaController.update();
}

// Shared implementation of expansionApplyInputConfig / expansionApplyOutputConfig:
// re-apply every desired channel mode. NOT a hot-plug re-arm — expansions are
// DIN-rail mounted and powered with the PLC, never hot-plugged, so _probe() no
// longer calls this on a topology change.
static void _rearmFromWant() {
  _clearArmed();
  for (int e = 0; e < _expCount && e < MAX_EXPANSIONS; e++)
    for (int c = 0; c < A0602_TOTAL_CHANNELS; c++)
      if (_wantMode[e][c] != CH_NONE)
        _armChannel(e, c, _wantMode[e][c]);
}

// Presence + type health check. Runs at boot and on the periodic re-probe.
// Healthy == exactly 2 expansions, A0602 at index 0, D1608E at index 1.
static void _probe() {
  OptaController.update();
  int found = OptaController.getExpansionNum();
  if (found > MAX_EXPANSIONS) found = MAX_EXPANSIONS;
  _expCount = found;

  _expOk = (found == 2) &&
           (OptaController.getExpansionType(0) == EXPANSION_OPTA_ANALOG) &&
           (OptaController.getExpansionType(1) == EXPANSION_OPTA_DIGITAL_MEC);
}

void expansionBegin() {
  _clearArmed();
  for (int e = 0; e < MAX_EXPANSIONS; e++)
    for (int c = 0; c < A0602_TOTAL_CHANNELS; c++)
      _wantMode[e][c] = CH_NONE;

  OptaController.begin();
  OptaController.update();

  // BOOT VERIFICATION — hold here until exactly 2 expansions of the right
  // types are present. A missing/unpowered A0602 makes all 6 H2 sensors read
  // 0 counts ("0% H2") and the system would run blind; refuse to proceed.
  unsigned long lastPrint = 0;
  bool firstPass = true;
  for (;;) {
    _probe();
    if (_expOk) break;
    if (firstPass || millis() - lastPrint >= 1000) {
      firstPass = false;
      lastPrint = millis();
      Serial.print("[EXP] waiting for expansions: found ");
      Serial.print(_expCount);
      Serial.println("/2 (need A0602 @0, D1608E @1)");
    }
    OptaController.update();
    delay(50);
  }
  Serial.println("[EXP] both expansions present, types OK");
  _lastProbeMs = millis();
}

void expansionLoop() {
  OptaController.update();
  if (millis() - _lastProbeMs >= EXP_PROBE_INTERVAL_MS) {
    _lastProbeMs = millis();
    _probe();
  }
}

int  expansionCount()    { return _expCount; }
bool expansionHealthy()  { return _expOk; }

// ---------------------------------------------------------------------------
static bool _decode(int encodedPin, int& expIdx, int& channel) {
  if (!PIN_IS_EXPANSION(encodedPin)) return false;
  expIdx  = PIN_EXP_INDEX(encodedPin);
  channel = PIN_EXP_CHANNEL(encodedPin);
  if (expIdx < 0 || expIdx >= _expCount)               return false;
  if (channel < 0 || channel >= A0602_TOTAL_CHANNELS)  return false;
  return true;
}

// Record a set of encoded pins into _wantMode with the given mode, WITHOUT
// clearing modes that other apply*Config calls set — the kitchen arms inputs,
// outputs and relays from three separate calls in setup().
static void _wantPins(const int* pins, int count, ChMode mode,
                      const bool* isCurrent /* may be null */) {
  for (int i = 0; i < count; i++) {
    if (!PIN_IS_EXPANSION(pins[i])) continue;
    int e = PIN_EXP_INDEX(pins[i]);
    int c = PIN_EXP_CHANNEL(pins[i]);
    if (e < 0 || e >= MAX_EXPANSIONS) continue;
    if (c < 0 || c >= A0602_TOTAL_CHANNELS) continue;
    ChMode m = mode;
    if (mode == CH_VOLT_ADC && isCurrent && isCurrent[i]) m = CH_CURR_ADC;
    _wantMode[e][c] = m;
  }
}

void expansionApplyInputConfig(const int* pins, const bool* isCurrent, int count) {
  _wantPins(pins, count, CH_VOLT_ADC, isCurrent);
  _rearmFromWant();
}

void expansionApplyOutputConfig(const int* pins, int count) {
  _wantPins(pins, count, CH_VOLT_DAC, nullptr);
  _rearmFromWant();
}

// ---------------------------------------------------------------------------
// A0602 PWM output (OA_CH_8..11). Mark the channel as CH_PWM so the write path
// validates it; there is no library begin* step for PWM. Call once in setup().
// ---------------------------------------------------------------------------
void expansionApplyPwmConfig(const int* pins, int count) {
  _wantPins(pins, count, CH_PWM, nullptr);
  _rearmFromWant();
}

// Drive an armed PWM channel to `dutyPct` (0..100). The library API takes
// pulse WIDTH in microseconds, not a duty percentage — convert here against
// the fixed carrier period. No-op if the A0602 is absent or the channel was
// not armed as PWM.
void expansionWritePwm(int encodedPin, float dutyPct) {
  int e, c;
  if (!_decode(encodedPin, e, c)) return;
  if (_chMode[e][c] != CH_PWM) return;
  if (dutyPct < 0.0f)   dutyPct = 0.0f;
  if (dutyPct > 100.0f) dutyPct = 100.0f;
  AnalogExpansion exp = OptaController.getExpansion(e);
  if (!exp) return;
  uint32_t period = GAS_LAMP_PWM_PERIOD_US;
  uint32_t pulse  = (uint32_t)((dutyPct / 100.0f) * (float)period + 0.5f);
  exp.setPwm((uint8_t)c, period, pulse);
}

// ---------------------------------------------------------------------------
// Analog reads — channel assumed already armed by expansionApplyInputConfig().
// ---------------------------------------------------------------------------
float expansionReadVoltage(int encodedPin) {
  int e, c;
  if (!_decode(encodedPin, e, c)) return 0.0f;
  if (_chMode[e][c] != CH_VOLT_ADC) return 0.0f;
  AnalogExpansion exp = OptaController.getExpansion(e);
  if (!exp) return 0.0f;
  return exp.pinVoltage((uint8_t)c);
}

float expansionReadCurrent(int encodedPin) {
  int e, c;
  if (!_decode(encodedPin, e, c)) return 0.0f;
  if (_chMode[e][c] != CH_CURR_ADC) return 0.0f;
  AnalogExpansion exp = OptaController.getExpansion(e);
  if (!exp) return 0.0f;
  return exp.pinCurrent((uint8_t)c);
}

// ---------------------------------------------------------------------------
// Analog DAC write — channel assumed already armed as CH_VOLT_DAC.
// ---------------------------------------------------------------------------
void expansionWriteVoltage(int encodedPin, float volts) {
  int e, c;
  if (!_decode(encodedPin, e, c)) return;
  if (_chMode[e][c] != CH_VOLT_DAC) return;
  if (volts < 0.0f)  volts = 0.0f;
  if (volts > 10.0f) volts = 10.0f;
  AnalogExpansion exp = OptaController.getExpansion(e);
  if (!exp) return;
  // setDac name / signature per Arduino_Opta_Blueprint's AnalogExpansion.
  exp.pinVoltage((uint8_t)c, volts);
}

// ---------------------------------------------------------------------------
// D1608E relay control. No arming step — a DigitalExpansion relay is written
// directly. The kitchen drives the whole desired relay set every pass, so
// these writes are frequent and idempotent.
// ---------------------------------------------------------------------------
void expansionSetRelayExpansion(int expIdx) { _relayExpIdx = expIdx; }

bool expansionRelayBoardPresent() {
  return _relayExpIdx >= 0 && _relayExpIdx < _expCount;
}

void expansionSetRelay(int encodedPin, bool closed) {
  if (!PIN_IS_EXPANSION(encodedPin)) return;
  int e = PIN_EXP_INDEX(encodedPin);
  int c = PIN_EXP_CHANNEL(encodedPin);
  if (e != _relayExpIdx) return;                 // not the relay board
  if (e < 0 || e >= _expCount) return;           // board absent
  if (c < 0 || c >= D1608E_RELAY_COUNT) return;
  DigitalExpansion exp = OptaController.getExpansion(e);
  if (!exp) return;
  // update=true so the write reaches the relay immediately rather than being
  // buffered until updateDigitalOutputs(). The kitchen changes relays rarely,
  // so the per-write I2C cost is not a concern.
  exp.digitalWrite((int)c, closed ? HIGH : LOW, /*update=*/true);
}
