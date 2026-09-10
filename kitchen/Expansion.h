#ifndef KITCHEN_EXPANSION_H
#define KITCHEN_EXPANSION_H

// =============================================================================
// Expansion.h — Opta expansion support for the kitchen PLC.
//
// Copied from DataAcquisition/CM7/Expansion.h and EXTENDED:
//   - kept:  voltage / current ADC reads on A0602 analog channels (I1-I6),
//            armed at config time (arming-then-immediately-reading returns a
//            stale frozen value — configure once up front, then just read).
//   - added: voltage DAC OUTPUT on A0602 channels (O1/O2 -> fan speed /
//            flowmeter setpoint), and relay set/get on a D1608E digital
//            expansion (the vent-register coils, gas relay, alarm relay).
//
// Channels are addressed with the encoded-pin scheme from Kitchen_Settings.h:
//   base board : pin = 0..7                -> A0..A7 (not handled here)
//   expansion  : pin = 100*(expIdx+1) + ch -> exp0 ch2 = 102
//
// PWM/pulse sensing is base-board only (hardware ISR) and not provided here.
// =============================================================================

#include <Arduino.h>

// Call once in setup() AFTER Serial is up. Begins OptaController and then
// BLOCKS until exactly 2 expansions of the right types are present (A0602 at
// index 0, D1608E at index 1), printing `found N/2` once a second. A missing
// A0602 would make every H2 sensor read 0 counts, so booting past that is
// refused rather than running blind.
void expansionBegin();

// Call every loop() iteration — pumps OptaController I2C and re-runs the
// presence + type health check on a fixed interval (EXP_PROBE_INTERVAL_MS).
// Expansions are DIN-rail mounted and powered with the PLC; this is fault
// detection, NOT hot-plug support.
void expansionLoop();

// Number of expansion shields currently detected (0..MAX_EXPANSIONS).
int  expansionCount();

// True when the last probe saw exactly 2 expansions with correct types
// (A0602 @0, D1608E @1). kitchen.ino feeds this into SensorState so a
// mid-run expansion loss becomes an EXPANSION_FAULT danger condition.
bool expansionHealthy();

// --- Analog ADC inputs (A0602) --------------------------------------------

// Arm the A0602 ADC channels for a set of encoded pins. Call once in setup().
// Base-board pins in the list are ignored.
//   pins[i]      — encoded pin
//   isCurrent[i] — true = 4-20 mA current ADC, false = 0-10 V voltage ADC
void expansionApplyInputConfig(const int* pins, const bool* isCurrent, int count);

// Read a voltage (0-10 V) / current (mA) from an encoded expansion pin. Each
// call is its OWN I2C round-trip (update=true under the hood) — fine for
// callers that read a single channel occasionally. For a fast poll of several
// channels on the SAME expansion, use expansionRefreshAnalogInputs() +
// the *Cached reads below instead: one I2C transaction for every channel,
// not one per channel. Returns 0.0f if the target expansion is absent or the
// channel was not armed for that mode by expansionApplyInputConfig().
float expansionReadVoltage(int encodedPin);
float expansionReadCurrent(int encodedPin);

// One I2C transaction (GET_ALL_ANALOG_INPUT) that refreshes EVERY armed analog
// channel's cached register on expansion `expIdx` — not just one. Call this
// once per sample tick, then read as many channels as needed with the two
// *Cached functions below at zero further I2C cost. No-op if expIdx is out of
// range or the expansion is absent.
void expansionRefreshAnalogInputs(int expIdx);

// Read the cache last filled by expansionRefreshAnalogInputs() — no I2C access
// here (update=false to the underlying library call). Calling this WITHOUT a
// preceding refresh for the same tick returns a stale value, not a fresh one;
// it does not itself trigger a read the way expansionReadVoltage/Current do.
float expansionReadVoltageCached(int encodedPin);
float expansionReadCurrentCached(int encodedPin);

// --- Analog DAC outputs (A0602 O1/O2) -----------------------------------

// Arm the A0602 channels in `pins` as 0-10 V voltage DAC OUTPUTS. Same
// config-time arming rule as the inputs. Call once in setup().
void expansionApplyOutputConfig(const int* pins, int count);

// Drive an armed DAC channel to `volts` (clamped to 0..10). No-op if the
// target expansion is absent or the channel was not armed as an output.
void expansionWriteVoltage(int encodedPin, float volts);

// --- Analog PWM outputs (A0602 OA_CH_8..11) ---------------------------

// Mark A0602 PWM channels in `pins` as PWM outputs. No library begin* step —
// setPwm() is called directly — so this just records the mode for the write
// path to validate. Call once in setup().
void expansionApplyPwmConfig(const int* pins, int count);

// Drive an armed PWM channel to `dutyPct` (0..100) on the fixed carrier
// period (GAS_LAMP_PWM_PERIOD_US). Converts duty% -> pulse-width-us internally
// (the library API takes microseconds, not a percentage). No-op if the A0602
// is absent or the channel was not armed as PWM.
void expansionWritePwm(int encodedPin, float dutyPct);

// --- Digital relay outputs (D1608E) -----------------------------------

// Note which expansion INDEX is the D1608E relay board, so the relay calls
// below know where to route. Call once in setup() (the plan wires it as
// expansion #1, after the A0602 at #0). -1 = no relay board.
void expansionSetRelayExpansion(int expIdx);

// Drive one D1608E relay. `encodedPin` is EXP_ENC(relayExpIdx, channel).
// No-op if the relay board is absent. The caller (Outputs.cpp) still calls
// this for the full desired relay set every pass, but each call is now a
// cached shadow-compare: the actual I2C write is skipped when the requested
// state already matches what was last written, EXCEPT once every
// RELAY_REASSERT_MS, when every coil is force-rewritten regardless of the
// shadow — the self-healing property the old unconditional-write version
// gave for free (a relay that glitched off-shadow gets corrected within one
// re-assert window instead of never).
void expansionSetRelay(int encodedPin, bool closed);

// Call once per loop() pass, AFTER every expansionSetRelay() call that pass
// (i.e. right after outputsDrive() returns). Closes the periodic re-assert
// "sweep" so every relay channel driven this pass gets force-written when a
// re-assert is due — see the comment on _relayReassertSweep in Expansion.cpp
// for why a single flag can't just be time-gated per call.
void expansionEndRelaySweep();

// True once the D1608E relay board has been seen by a probe.
bool expansionRelayBoardPresent();

#endif // KITCHEN_EXPANSION_H
