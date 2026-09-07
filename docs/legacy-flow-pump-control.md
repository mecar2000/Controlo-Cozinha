# Legacy: pulse-flowmeter → pump relay control (from `Old/kitchenold.ino`)

**Why this file exists.** `Old/kitchenold.ino` (the pre-2026 threshold-alarm
monitor) was retired and deleted; it was never committed to git, so this
document is the only surviving record of its flow-driven pump control. The new
`kitchen/` firmware measures flow differently (see *How the new design differs*
below), so this is kept as a design reference, not as code to port verbatim.

Everything below is transcribed from the deleted sketch.

---

## What it did

A **water flowmeter drove a relay** (`RELAY4`) that switched the pump. The
sketch did not command the pump on a timer or from the network — it inferred
"water is actually moving" from flowmeter pulses and closed the relay only
while that held. Flow was the *evidence*, not the *command*.

The flowmeter was a **pulse** device: **1 pulse = 1 mL**, read on an
interrupt, unlike the new design's analog 0–10 V rate signal.

### Hardware

| Channel | Role |
|---|---|
| `I5` (`A4`) | flow sensor input, 1 pulse per mL, interrupt on rising edge |
| `RELAY4` | pump relay — **normally open**, closes while flow is confirmed |
| `LED_RELAY4` | mirrors the relay state; managed by `updateFlow()` alone |

### Tuning constants

```cpp
#define FLOW_TIMEOUT   1000UL  // ms without a pulse before flow is considered stopped
#define FLOW_MIN_ML      10UL  // pulses required before flow is confirmed active
#define FLOW_PIN           A4  // I5 — flow sensor input (1 pulse per mL)
```

### State

```cpp
bool flowActive = false;  // true while flow pulses are being received

// Flow sensor — written in ISR, read in main loop
volatile unsigned long flowLastPulseT   = 0;  // timestamp of last pulse (ms)
volatile unsigned long flowPulseCount   = 0;  // total pulse count (mL, lifetime)
volatile unsigned long flowSessionCount = 0;  // pulses in current flow session (resets on stop)
```

---

## The logic, verbatim

```cpp
// ---------------------------------------------------------------------------
// ISR — called on each rising edge from the flow sensor (I5 / A4).
// Ignores pulses arriving faster than 5ms apart to filter contact bounce.
// ---------------------------------------------------------------------------
static void onFlowPulse() {
  unsigned long now = millis();
  if (now - flowLastPulseT < 5) return;  // debounce: ignore bounce spikes
  flowLastPulseT = now;
  flowPulseCount++;
  flowSessionCount++;
}

// ---------------------------------------------------------------------------
// Manages RELAY4 based on flow sensor pulse timing.
// Requires FLOW_MIN_ML pulses in a session before activating.
// Opens after FLOW_TIMEOUT ms of silence; resets session counter on stop.
// ---------------------------------------------------------------------------
static void updateFlow() {
  unsigned long lastPulse, sessionCount;
  noInterrupts();
  lastPulse    = flowLastPulseT;
  sessionCount = flowSessionCount;
  interrupts();

  bool pulsing  = (lastPulse > 0) && (millis() - lastPulse < FLOW_TIMEOUT);
  bool hasFlow  = pulsing && (sessionCount >= FLOW_MIN_ML);

  if (hasFlow && !flowActive) {
    flowActive = true;
    digitalWrite(RELAY4, HIGH);  // Close relay — flow confirmed
    Serial.println("[FLOW] Flow started");
  } else if (!pulsing && flowActive) {
    flowActive = false;
    digitalWrite(RELAY4, LOW);   // Open relay — no flow
    noInterrupts(); flowSessionCount = 0; interrupts();  // reset session for next burst
    Serial.println("[FLOW] Flow stopped");
  } else if (!pulsing && !flowActive && sessionCount > 0) {
    // Pulses arrived but didn't reach threshold — discard session
    noInterrupts(); flowSessionCount = 0; interrupts();
  }
}
```

Setup wiring (from `setup()`):

```cpp
pinMode(RELAY4, OUTPUT); digitalWrite(RELAY4, LOW);   // Normally open (flow)
// Flow sensor on I5 (A4) — interrupt on rising edge (each pulse = 1 mL)
```

---

## The three design ideas worth keeping

These are the parts that are about *judgement*, not about the old hardware:

1. **A minimum-volume gate before acting.** `FLOW_MIN_ML` (10 pulses = 10 mL)
   means a few stray pulses — vibration, a bubble, contact bounce, a brief
   pressure transient — never start the pump. Flow has to be *sustained* to
   count. There is a deliberate asymmetry here: starting requires 10 mL of
   evidence, but stopping requires only silence.

2. **Asymmetric start/stop conditions.** Turning ON needs
   `pulsing && sessionCount >= FLOW_MIN_ML`. Turning OFF needs only `!pulsing`
   — one second of silence (`FLOW_TIMEOUT`) drops the relay, with no volume
   threshold to clear. **Fail-safe direction: hard to start, easy to stop.**

3. **Session counter reset on every stop.** `flowSessionCount` zeroes when flow
   stops *and* when a sub-threshold burst times out, so the 10 mL evidence must
   be accumulated fresh each time. Ten separate 1 mL blips a minute apart never
   add up to a start — only ten within one continuous session do.

Plus two implementation details that matter on any pulse input:

- **ISR debounce** at 5 ms between accepted pulses, filtering contact bounce
  that would otherwise inflate the volume count.
- **Interrupt-safe reads**: the `volatile` counters are copied inside
  `noInterrupts()`/`interrupts()` before use, so a pulse landing mid-read
  cannot tear a value.

---

## How the new design differs

| | `Old/kitchenold.ino` | `kitchen/` (current) |
|---|---|---|
| Flow signal | **pulse**, 1 pulse = 1 mL, ISR-counted | **analog** 0–10 V rate on base `A1` |
| Volume | counted directly (each pulse *is* a mL) | **integrated** from the rate over time |
| Medium | water (pump control) | hydrogen (gas delivery) |
| Actuation | relay closes *because* flow was detected | gas relay + setpoint DAC command flow; flow feedback *verifies* it |
| Constants | `FLOW_MIN_ML`, `FLOW_TIMEOUT` | `FLOW_COUNTS_PER_VOLT`, `FLOW_ML_PER_SEC_AT_10V`, `FLOW_LIMIT_COUNTS`, `INVENTORY_CAP_ML` |

The causal direction is **reversed**: the old sketch let measured flow decide
the relay; the new firmware commands flow and uses the measurement as a safety
check (`FLOW_LIMIT_COUNTS` sustained over `FLOW_LIMIT_SUSTAINED_MS`, and
`INVENTORY_CAP_ML` on the integrated total).

**If a water pump is ever re-added to the kitchen rig**, this pulse-driven
pattern — minimum-volume gate, asymmetric start/stop, per-session reset — is
the behaviour to reimplement. It would sit in `Outputs`/`Sensors` with the
decision in `KitchenCore`, and would need its own relay channel (the D1608E's
8 are fully allocated today — see `Kitchen_Settings.h`).

**A note on the sustain-window parallel.** `Sensors.cpp` already uses the same
"must hold continuously before it counts" shape for
`flowOverLimitSustained` (`FLOW_LIMIT_SUSTAINED_MS`). That is the modern
analogue of `FLOW_MIN_ML` — evidence has to persist before it drives an action.
