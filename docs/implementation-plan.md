# Kitchen H2 Control System — Implementation Plan

## Context

The experimental kitchen runs **hydrogen leak-propagation experiments**: hydrogen is
released at a controlled rate, its dispersion is measured by H2 sensors, and
ventilation is then applied to measure how fast concentration decays. The same room
is also used to test equipment that needs a live hydrogen supply.

Today `Cozinha/Old/kitchen.ino` is a standalone threshold-alarm monitor (4 inputs →
relays → latch). It cannot run experiments, has no modes, no MQTT command interface,
and no integration with the lab-wide safety system that `centralPLC/server.py`
implements.

This plan replaces it with a state-machine control system on one Arduino Opta
(+ A0602 analog expansion + D1608E relay expansion) that:

- runs scripted leak experiments commanded over MQTT from a website (not yet built),
- enforces **firmware-owned** safety rules that no network message can weaken,
- integrates with the existing `status/…` + `safety/permit/…` scheme in `server.py`,
- publishes telemetry for browser display, reusing the CM7 DAQ payload conventions.

Diagrams live in [Cozinha/diagrams/](../diagrams/) — `state_machine`,
`safety_dataflow`, `hardware_io_map`, `mqtt_topic_map` (regenerate with
`diagrams/kitchen_diagrams.py`). **The `safety_dataflow` diagram and the
`state_machine` diagram both predate this revision and need regenerating** — see
Sequencing step 0.

**Intended outcome:** a firmware that can be handed to a safety reviewer with the claim
*"nothing the website sends can open the gas valve when the safety layer says no"* —
and have that claim be verifiable by reading one file and running one test suite.

---

## Core architecture: one state machine

**There is exactly one core.** `KitchenCore` holds the state, reads `SensorState`,
and produces an `OutputRequest`. There is no request/veto split, no second layer that
rewrites what the first layer wanted, and no latch shared as a boundary object between
two modules.

```cpp
// The whole control path.
OutputRequest out = core.update(sensors, nowMs);   // the audit target
outputs.drive(out, nowMs);                          // the ONLY pin writer
```

### Why the two-layer model was dropped

The previous design had `ExperimentCore` produce a request and `Safety::apply()`
rewrite it, with a `SafetyLatch` passed between them and an `observeLatch()` call to
move the state machine on the pass *after* a danger fired. It bought a separation of
powers that had no second party to separate: one core, one binary, one author, one
review. What it cost was real:

- **Two sources of truth for "are we in danger"** — the latch and the state — which
  had to be kept in sync by a documented one-pass skew that every reader had to be
  taught, and every test had to assert.
- **A transition that no state could initiate.** Danger changed the pins in one place
  and the state in another, so "what happens on danger" could not be read off the
  transition table.
- **A pure function that was not pure** — `apply()` took `SafetyLatch&` inout, so the
  purity claim needed a paragraph of qualification to stay true.

A single state machine keeps the property that actually matters — the website's
request is data, never a command that reaches a pin — because the run spec is only
ever *read* by `update()`, and `update()` decides the outputs itself.

### What replaces the veto

**Danger is a transition, not a rewrite.** At the top of `update()`, before any
per-state logic, danger conditions are evaluated. If any holds, the machine
transitions to `FULLY_VENTILATING` from whatever state it was in, and `update()`
returns that state's outputs. Same pass, no skew: the state and the pins agree at
every instant, which is exactly what the latch existed to fake.

```cpp
OutputRequest KitchenCore::update(const SensorState& s, uint32_t nowMs) {
  DangerReason r;
  if (dangerActive(s, r)) enterFullyVentilating(r, nowMs);  // from ANY state
  // ...per-state logic, which can only run when no danger is active...
  return outputsFor(state_, nowMs);
}
```

`outputsFor(FULLY_VENTILATING)` is a fixed, unconditional block: gas relay closed,
setpoint 0 V, all registers open, fan 100 %, alarm on. It reads no run spec and takes
no arguments that the website can influence. **That function plus `dangerActive()` is
the entire safety audit target** — two functions in one file, both pure, both
desktop-testable.

**The website cannot reach the pins** because `RunSpec` is only consulted inside the
`LEAKING` / `HOLD` / `VENTILATING` branches, and those branches are unreachable while
a danger condition holds — the danger check runs first and moves the state every pass.

**Invariants to enforce by review** (stated as comments at the two sites):

1. `Outputs::drive()` is the only place that calls `digitalWrite`/`analogWrite` on
   control pins.
2. `dangerActive()` is the only place danger conditions are evaluated.
3. `outputsFor()`'s `FULLY_VENTILATING` branch never reads `spec_`.

**`OutputRequest` remains a desired end state, not a sequence of actions.** It says
*which* registers should be open, never *in what order* they get there. Actuation
ordering (the inlet delay below) belongs to `RegisterSequencer`/`Outputs`, so the
ordering applies identically to danger-forced transitions and to spec-driven ones.

---

## States

`WAITING → ARMED → LEAKING → HOLD → VENTILATING → FULLY_VENTILATING → WAITING`

- **WAITING** — idle. A physical selector on `A2` sets the role:
  `≤2.5 V` = leak-test (flowmeter sealed), `>2.5 V` = equipment-test (flowmeter armed,
  ventilation open+running as a precondition). One mode, not two; the breaker decides
  whether gas may pass. Role is reported in the retained state message.
  **Fan speed in WAITING is `VENT_SPEED_IDLE_PCT`, a firmware constant — not 100 %.**
  Waiting is the steady state the kitchen sits in for hours, so the fan runs at a low
  background rate: enough to keep the room turning over (and to satisfy the
  equipment-test precondition that ventilation is open and running), without the noise
  and wear of a full purge. Full speed is reserved for FULLY_VENTILATING. The website
  cannot change this value; it is not part of the run spec.
- **ARMED** — run spec validated + clamped, awaiting `confirm(runId)`.
  **Disarms back to WAITING after 60 s** (`ARM_TIMEOUT_MS`).
- **LEAKING** — setpoint per spec; integrating delivered inventory. Ends on
  `leakStop` (duration OR inventory OR sensor quorum, whichever fires first).
  **The duration is timed from when gas actually starts flowing** — i.e. from the
  release of the `SENSOR_WARMUP_MS` gate, not from state entry. Timing from entry would
  let the 30 s gate consume the whole phase: a 5 s leak spec would deliver *zero* gas.
- **HOLD** — gas off, fans off; watch propagation undisturbed. **This is the actual
  measurement phase**, so it runs for a real duration: it ends on `holdStop` (duration
  OR quorum). `Protocol` clamps the duration to `HOLD_MAX_DURATION_MS`, and a spec that
  omits `holdStop` falls back to that **ceiling, not zero** — absent means "the maximum
  safe hold", not "skip the measurement". HOLD has gas already delivered and fans off,
  so an unbounded hold must not be reachable via a malformed spec.
- **VENTILATING** — registers/speed per spec, ending on `ventStop`, then straight to the
  mandatory purge.
- **FULLY_VENTILATING** — all registers open, fan 100 %, gas cut two independent ways
  (setpoint 0 V **and** relay open). Serves three roles: mandatory end-of-run purge,
  emergency response, and the pre-experiment clean-air proof.

### Exit from FULLY_VENTILATING

The state carries **one field**, `ackRequired_`, set at entry by whichever transition
brought it there. There is no separate latch object and no second flag to disagree
with it.

| Entry into FULLY_VENTILATING | `ackRequired_` | Exit |
|---|---|---|
| End-of-run purge (after VENTILATING) | false | Auto after 5 min all-clear |
| Pre-run clean-air proof | false | Auto after 5 min all-clear |
| Operator `stop()` mid-run | false | Auto after 5 min all-clear |
| Any danger condition | **true** | **Human ack** + 5 min all-clear |
| Selector flipped off leak-test mid-leak | **true** | **Human ack** + 5 min all-clear |

Exit requires: no danger condition currently active, **and** the all-clear has held
continuously for `FULLY_VENT_MIN_HOLD_MS` (5 min), **and** `!ackRequired_ || acked_`.
The all-clear timer resets to zero on any pass where a danger condition is active, so
"5 min clear" means five continuous minutes, not five minutes since entry.

**Re-entry while already in FULLY_VENTILATING promotes but never demotes.** A danger
condition firing during a routine purge sets `ackRequired_ = true`; nothing sets it
back to false except leaving the state. Entering while already there does **not**
reset `acked_` — otherwise a flickering sensor would discard an ack the operator
already gave.

The ack is a **USER button or an MQTT command** — both call the same `humanAck()`. The
button path must work with no network, like the e-stop: a physical ack matters precisely
when the network is the thing that is broken. The ack is not a shortcut past the hold —
release needs the ack **and** 5 min of continuously-clear sensors, whichever completes
last. `ackRequired_` governs the ack only: the full purge always runs.

**A leak run requires the selector in leak-test position** — a run spec arriving while
the selector says equipment-test is rejected with a reason. **Flipping the selector
*during* a leak run aborts into a purge that requires an ack** (`OPERATOR_ABORT`).
Unlike `stop()`, which is an in-band command from someone who knows a run is live, the
selector is a physical switch that can be flipped by someone who walked into the room
with no idea hydrogen is flowing — that warrants a human closing the loop.

`OPERATOR_ABORT` is not a `dangerActive()` condition — it has no ongoing state to
clear, only a human to acknowledge. It is checked in the `LEAKING` branch and calls the
same `enterFullyVentilating(OPERATOR_ABORT)` the danger check does. Because entry is
idempotent with respect to `acked_`, calling it every pass the selector stays wrong
does not discard an ack already given.

**Why there is no "escalate to full ventilation" step.** An earlier draft had
VENTILATING escalate past a grace deadline. It was removed: both branches did the same
thing, and the escalation could never fire without the normal exit having fired first.
FULLY_VENTILATING is unconditional and holds until sensors read clear for 5 min, so a
room that failed to vent as specified is caught there by **measurement** rather than by
a deadline heuristic.

---

## DAQ sensor power command

Continuous H2-sensor operation is not required — sensors are expensive to keep always
armed (warm-up drift, element life) — so the kitchen PLC commands **when** data
acquisition instances should power their sensing element on/off, rather than leaving it
always-on.

**Scope — two separate targets, not one flag:**
- **Remote CM7 DAQ instances** (`DataAcquisition/CM7/…`) — commanded over MQTT.
- **This kitchen PLC's own local sensors** — controlled locally in firmware.

These are tracked as two independent booleans/outputs even though they follow the same
state logic below, because it is not yet decided whether this PLC's own sensors should
ever be blocked the same way remote ones are — keeping them separate means that can be
changed without touching the remote-command path.

**On/off rule:**
- **On boot (`setup()`)**: sensors OFF. This is the only unconditional-off entry point;
  every other transition below is a state-driven on/off.
- **Entering LEAKING for the first time from a sensors-off state**: command sensors ON,
  then hold for `SENSOR_WARMUP_MS` (default 30000 ms) **before** proceeding into the
  rest of the LEAKING sequencing — the sensing element needs to warm up before its
  reading is trustworthy. This warm-up is a non-blocking timed gate (same pattern as
  the inlet-open delay: a deadline checked each `loop()` pass, no `delay()`), so the
  danger check still runs every pass and still cuts gas immediately even while warm-up
  is pending.
- **Once ON, stay ON** through LEAKING → HOLD → VENTILATING → FULLY_VENTILATING → back
  into WAITING. Sensors do not cycle off mid-run.
- **In WAITING**: sensors stay ON if they were already on (carried over from a run that
  just ended). If WAITING is idle for more than `SENSOR_IDLE_TIMEOUT_MS` (default 10
  minutes / 600000 ms) with no new run started, command sensors OFF. The idle timer
  starts counting from the moment WAITING is (re-)entered after a
  VENTILATING/FULLY_VENTILATING purge.
- **Role selector at equipment-test**: sensors ON, unconditionally, as long as
  equipment-test role is selected — this is on top of (not instead of) the LEAKING/idle
  rule above, so switching into equipment-test re-arms sensors immediately without a
  30 s warm-up gate blocking the equipment-test precondition.
- Sensors going OFF is never itself a danger condition — it is a normal power-saving
  state — but **local sensor silent > 10 s is** a danger condition (see below) whenever
  sensors are supposed to be ON; a stale reading while intentionally OFF is expected and
  must not trip that check.

Sensor power is decided in `KitchenCore` alongside the rest of the state logic and
travels out through `OutputRequest` as two fields (`bool localSensorsOn`,
`bool remoteSensorsOn`), so `Outputs`/`Protocol` remain the only places that touch
hardware pins and MQTT respectively. Because there is no longer a veto layer, nothing
rewrites these values between decision and drive.

**MQTT:** remote sensor power is a new retained command, e.g.
`KitchenControl/{deviceId}/sensors/power` → `{on: bool}`, published whenever the
computed `remoteSensorsOn` value changes (not on every loop pass). CM7 DAQ instances
subscribe and gate their own sensor-read/publish loop on it. Exact topic name to be
reconciled with the CM7 firmware's existing subscription set before implementation.

---

## Danger conditions (firmware-owned, OR'd, from any state)

Evaluated by `dangerActive()`, called at the top of `update()` before any per-state
logic. Any one of them transitions to FULLY_VENTILATING with `ackRequired_ = true`.

| Condition | Applies in |
|---|---|
| Local sensor **at or above** its threshold | all modes |
| Flow > limit sustained > 2 s | leak-test only |
| Delivered inventory cap exceeded | leak-test only |
| Peer PLC alarm received over MQTT | all modes (incl. equipment-test) |
| Permit present and false | all modes — *but see below: never present outside a run* |
| Physical e-stop button (`A3`, 0–5 V) | all modes — **works with no network** |
| Local sensor silent > 10 s | all modes (this PLC's own sensors) |

**Boundary convention: at-or-above (`>=`), everywhere.** A reading exactly at its
threshold trips rather than passes. This applies to the per-sensor danger check and to
the quorum stop-condition alike — they compare the same quantity for different purposes
and must not drift apart. Stated once in `Kitchen_Settings.h`.

**The permit is structurally inert outside a run — do not read that table row as
coverage that exists.** `server.py` publishes permits only for devices in
`experiment_state` ([server.py:351](../centralPLC/server.py#L351)), and that dict is
populated only when a device publishes `status/…/run` with `running: true`
([server.py:280](../centralPLC/server.py#L280)). So in WAITING, in ARMED, and
throughout equipment-test, no permit is ever sent, `permitPresent` stays false, and
this check never fires.

**Peer alarms are therefore the only live lab-wide interlock outside a run.** That is a
deliberate property, not a gap: permit *absence* warns and never trips, so the kitchen
never depends on `server.py` being alive, and the retained peer-alarm topic survives
broker reconnects with no bridge running.

### Ventilation registers

Three registers, each with an open coil and a close coil:

| Register | Role |
|---|---|
| `CENTRAL` | central register |
| `EXHAUST` | extract air from the room |
| `INLET` | admit make-up air |

**"Ventilation type" in the run spec is any combination of the three** — a 3-bit set,
not an enum of named presets. The spec carries the set directly (`{central, exhaust,
inlet}` booleans), so no lookup table is needed and new combinations need no firmware
change. FULLY_VENTILATING forces all three open regardless of the spec.

**Inlet opening delay.** When a transition opens the inlet *together with* another
register, the inlet opens `INLET_OPEN_DELAY_MS` (default 1000 ms, in
`Kitchen_Settings.h`) **after** the others. Rationale: let extraction establish negative
pressure before make-up air is admitted, so the room does not get pressurised outward.

- **Delay applies to opening only.** Closing is simultaneous — no delay, no ordering.
- **The delay is a deferral, never a blocker.** The other registers actuate immediately;
  only the inlet coil waits. Nothing else in the system blocks on it.
- **It applies to FULLY_VENTILATING too**, including danger-forced entry — the purge
  reaches the same end state 1 s later, which is the intended pneumatic behaviour rather
  than a safety compromise. Gas cut and fan-to-100 % are **not** delayed: they happen on
  the same pass, so the hazard is addressed immediately and only the airflow geometry
  settles a second later.
- If the inlet is *already* open, or is opening alone with no other register changing,
  there is nothing to sequence against and it actuates immediately.

Implemented in `RegisterSequencer` as a single pending-deadline field checked each
`drive()` call — non-blocking, no `delay()` anywhere in the firmware. Because `drive()`
is called every loop pass, a request that changes again before the deadline simply
supersedes the pending action.

**Superseding a pending open does not restart the timer.** The deadline restarts only if
the inlet was *closed* and is now being opened. If the inlet is already commanded open
with a deadline pending, a request that merely adds another register
(`{exhaust, inlet}` → `{central, exhaust, inlet}`) **preserves** the existing deadline —
an inlet that has already waited 900 ms does not wait another full second. The delay
exists to let extraction establish negative pressure, which is a property of how long
the exhaust has been open, not of when the most recent request arrived.

### Fan speed by state (firmware constants, `Kitchen_Settings.h`)

| State | Speed | Owner |
|---|---|---|
| WAITING / ARMED | `VENT_SPEED_IDLE_PCT` (low background rate) | firmware constant |
| LEAKING | per run spec (clamped to `VENT_SPEED_MAX_PCT`) | website |
| HOLD | 0 % — fans off, undisturbed propagation | firmware |
| VENTILATING | per run spec | website |
| FULLY_VENTILATING | 100 % | firmware, non-overridable |

Only the two experiment phases take their speed from the run spec. Idle, hold and purge
speeds are firmware-owned so a bad or absent command can never leave the kitchen
under-ventilated at rest or under-ventilated during a purge. This is enforced
structurally: `outputsFor()` reads `spec_` only in the LEAKING and VENTILATING branches.

**Threshold rule:** effective threshold = `max(firmware_minimum, website_value)`.
The website can only make a sensor *more* sensitive, never less. Every incoming
threshold is clamped against a compiled-in ceiling; out-of-range or missing values fall
back to the compiled default. This is how per-sensor calibration is configurable
without letting the network weaken safety.

**Peer sensors** force a Fully-Ventilate while publishing, but going silent only warns
(MQTT warning + distinct LED blink + console) — it does not block starting an
experiment. **This PLC's own sensors going silent does block**, since that is a
hardware fault on the machine running the experiment.

### Units: counts internally, conversion only at the boundaries

**The rule.** Every concentration and flow value inside `KitchenCore` and `RunSpec` is
in **raw ADC counts**. Conversion to physical units happens in exactly two places —
`Sensors.cpp` (inbound, at the read boundary) and `Protocol.cpp` (at the MQTT
boundary). No other file converts anything; the core never sees mA, %, or volts. This
is what stops the three-unit confusion from growing back.

**Two independent analog scales — never crossed.** The mA constants apply only to the
six H2 sensors on the A0602 expansion; the volt constants apply only to A1's flow
feedback on the base board, whose ADC resolution differs. All five are **blocking before
hydrogen** and backed by a compile-time `#error` guard, so an uncalibrated build cannot
be flashed by accident — only on purpose, via `KITCHEN_ALLOW_PLACEHOLDER_SCALES`.

| Constant | Scale | How to get it |
|---|---|---|
| `ADC_COUNTS_AT_4MA` | 4–20 mA (A0602) | inject 4.00 mA, read the raw count |
| `ADC_COUNTS_PER_MA` | 4–20 mA (A0602) | inject 20.00 mA; `(c20 - c4) / 16` |
| `SENSOR_FULLSCALE_PCT` | 4–20 mA (A0602) | sensor datasheet |
| `FLOW_COUNTS_PER_VOLT` | 0–10 V (base A1) | feed 0 V and 10 V; difference / 10 |
| `FLOW_ML_PER_SEC_AT_10V` | 0–10 V (base A1) | flowmeter datasheet |

Two points are needed for each slope because a slope needs a span to be measured over.
**Do not use a sensor in clean air as the 4 mA source** — its real output may be 4.05 mA,
and that deviation is exactly what the calibration offset below exists to correct.
Calibrate one channel and check a second: a disagreement of more than a count or two on
the 4 mA reading is the ADC, not the wiring, and is worth knowing before hydrogen.

**Inventory integration depends on the flow constants.** An earlier draft treated raw
counts *as* mL/s, which made `INVENTORY_CAP_ML` meaningless — at ~3000 counts against a
5000 mL cap it tripped after roughly two seconds of any flow, aborting every run.

### Sensor quorum stop-condition + per-sensor calibration offset

**Quorum stop is a stop-condition primitive, not a danger condition.** "Stop the phase
once N sensors read at-or-above X" is a website-configurable experiment-design choice
(`SensorQuorumStop { thresholdCounts, quorumCount }` in `RunSpec.h`), evaluated by
`KitchenCore` each pass against the live sensor table. It is independent of and
additional to the single-sensor danger check (any one sensor at or above its own
threshold still trips a full danger response regardless of quorum state). Available on
`leakStop`, `holdStop` and `ventStop`.

Both checks now live in the same file, which makes the distinction between them easier
to keep straight, not harder: the quorum ends a *phase* and reads `spec_`; the danger
check ends the *run* and never does.

**The website sends `%`; the struct carries counts.** `Protocol` converts once at parse
time, so the MQTT contract is unchanged but `KitchenCore` only ever compares counts to
counts. The counts↔% mapping is **firmware-owned** because this condition cuts gas —
the website cannot be the authority on what "4 %" means. The `ack` echoes both the
requested % and the interpreted counts, so a miscalibration is visible from the browser
rather than silent.

**Absent and stale sensors are excluded from the count.** Silence is not evidence of low
concentration. (A stale sensor that is `expectedOn` is separately a danger condition, so
this exclusion never hides a fault — it just keeps it out of the quorum arithmetic.)

**Calibration offset is a separate, firmware-owned correction — not part of the
quorum condition.** The kitchen's H2 sensors are the same sensor type, but each one's
wiring run back to the PLC is a different length, so each reads a slightly different
current for the same real concentration. This is compensated with a manual per-sensor
offset (e.g. +0.1 mA for sensors 1–2, +0.2 mA for sensors 3–6), calibrated by hand and
stored as a firmware constant table (`SENSOR_CALIBRATION_OFFSET_MA[]` in
`Kitchen_Settings.h`), indexed the same way `SensorState.localSensors[]` is indexed.

**The table stays in mA** — it is a hand-calibrated physical measurement and should stay
in the unit you read off the meter. `Sensors.cpp` converts it at the read boundary using
`ADC_COUNTS_PER_MA`:

```cpp
raw_counts + (int16_t)(SENSOR_CALIBRATION_OFFSET_MA[i] * ADC_COUNTS_PER_MA)
```

The offset is signed, and the result is clamped to the ADC range — a negative offset on
a near-zero reading must not wrap a `uint16_t`.

**Where the offset is applied — once, at the read boundary.** `Sensors.cpp` owns a
single calibrated-read function that applies each sensor's offset before the value goes
anywhere else. Every downstream consumer — the per-sensor danger check *and* the quorum
stop-condition — only ever sees the corrected value in
`SensorState.localSensors[i].counts`. Neither check re-applies the offset or converts
units; this is the contract stated on `LocalSensorReading` and on `SensorQuorumStop`.

---

## Files

Sketch directory `Cozinha/kitchen/` (sketch name must match folder).

**Pure core — no Arduino headers, desktop-testable:**

| File | Contents | Status |
|---|---|---|
| `KitchenCore.h/.cpp` | `KitchenState`, `OutputRequest`, `SensorState`, `DangerReason`, `dangerActive()`, `update()`, `outputsFor()`, transitions, stop conditions, inventory integration, arm timeout, sensor power, threshold clamping | **done — replaces `SafetyCore` + `ExperimentCore`, 96/96 tests pass** |
| `RunSpec.h` | `RunSpec` + stop-condition primitives, incl. the 3-bit register set | exists, unchanged |
| `RegisterSequencer.h/.cpp` | Pure: (desired register set, `now_ms`) → coil states, applying the inlet open delay. Fake-clock testable. | exists, unchanged |
| `Kitchen_Settings.h` | Pin map, thresholds, timings, ceilings, `MQTT_MAX_PAYLOAD` | exists, unchanged |

**Deleted:** `SafetyCore.h/.cpp` (207 + 174 lines) and `ExperimentCore.h/.cpp`
(127 + 320 lines) merged into `KitchenCore.h/.cpp`. The merge was mostly deletion — the
latch class, `observeLatch()`, and the request-rewriting path are gone; the danger
conditions, transitions and stop-condition logic moved across essentially as-is.

**One real bug surfaced by the merge, fixed during it:** the all-clear clock for a
*routine* FULLY_VENTILATING entry (end-of-run purge, `stop()`) used to start on the
first `update()` call *after* entry rather than at entry itself — one call short of
the real `FULLY_VENT_MIN_HOLD_MS`. A second: a danger re-firing while already latched
with an ack already pending did not restart the all-clear clock, so a condition that
flickered near the end of the hold could let the hold complete without measuring five
*continuous* clear minutes. Both are fixed in `KitchenCore::update()`/
`enterFullyVentilating()` and covered by
`danger_during_all_clear_hold_restarts_the_clock` and
`routine_purge_auto_returns_without_ack` in `test_safety.cpp`.

**Arduino shim — hardware and network:**

| File | Contents |
|---|---|
| `kitchen.ino` | `setup()`/`loop()`; wires sensors → core → outputs; scheduling |
| `Outputs.h/.cpp` | Relay + analog-out map; `drive(out)`; **only pin writer**; register actuation sequencing (inlet delay); status LEDs |
| `Sensors.h/.cpp` | Local ADC reads (base + expansion), selector, e-stop, flow feedback; peer-alarm and permit state from MQTT; staleness timers; builds `SensorState` |
| `Protocol.h/.cpp` | JSON parse/serialise for `cmd`/`ack`/`state`/`config`; run-spec validation + clamping; alarm publish |
| `Kitchen_Secrets.h` | Copied from existing `Old/Secrets.h` — **gitignored** |

**Reused from `DataAcquisition/CM7/` (copy in, don't fork behaviour):**

- `Comms.h/.cpp` — Ethernet/WiFi, NTP, MQTT reconnect with backoff, LWT,
  `mqttPublish()`, already decoupled from sensors (injects callbacks via
  `commsSetMqttCallback` / `commsSetOnConnect`). **Needs modification — it cannot be
  used as-is.** See step 5 in Sequencing.
- `Expansion.h/.cpp` — A0602 **ADC input** support (`expansionReadVoltage`,
  `expansionReadCurrent`, `expansionApplyConfig`). Extend with analog **output**
  (DAC) support, which it currently lacks.

**Genuinely new hardware code:** analog outputs (O1 fan speed, O2 flowmeter setpoint)
and D1608E relay-expansion control. Neither exists in `Expansion.cpp` today — both need
writing against `OptaBlue.h` (`AnalogExpansion`, `DigitalExpansion`).

**Tests:** `Cozinha/kitchen/test/` — native C++, no Arduino.

---

## Hardware map

| Channel | Use |
|---|---|
| Exp. I1–I6 | 6 H2 current sensors |
| Exp. O1 / O2 | Fan speed setpoint / flowmeter setpoint (0–10 V) |
| Base A0 | Spare / future H2 sensor |
| Base A1 | Flow feedback (0–10 V) — rate direct, inventory by integration |
| Base A2 | Role selector (≤2.5 V leak-test / >2.5 V equipment-test) |
| Base A3 | E-stop button (0–5 V) |
| Base A4–A7 | Spare (H2 sensors expandable to 14 total) |
| Relays ×9 | 3 vent registers (2 coils each) · fan on/off · flowmeter cut · alarm |
| Status LEDs | Mode · alarm blink · **separate** stale-peer blink pattern |

Sensors are a **table**, not six variables — the count must grow without restructuring.

---

## MQTT contract

Reuses the existing scheme in `centralPLC/server.py` rather than inventing a parallel
one. **Unchanged by this revision** — the architecture simplification is internal.

**Consumed:**
- `safety/permit/{deviceId}` → `{permit, seq}`, ~1 s heartbeat, not retained.
  Veto when present; **absence warns, does not trip** (peer alarms are the real
  interlock, so the kitchen never depends on `server.py` being alive).
- `status/{ExperimentName}/{deviceId}/alarm/{labId}/{hazard}` → peer alarms, retained.
  **Primary interlock.** Must filter out its own device id to avoid self-latching.
- `KitchenControl/{deviceId}/cmd` → `start(spec)` · `confirm(runId)` · `stop` · `ack`
- `KitchenControl/{deviceId}/config/set` → per-sensor threshold table

**Published:**
- `KitchenControl/{deviceId}/state` — **retained**: mode, phase, selector role, elapsed
- `KitchenControl/{deviceId}/sensors/power` — **retained**: `{on: bool}`, published on
  change; commands remote CM7 DAQ instances to power their sensing element on/off
- `KitchenControl/{deviceId}/ack` — validated spec echo, or rejection + reason
- `KitchenControl/{deviceId}/config/ack` — the clamped threshold table
- `status/{ExperimentName}/{deviceId}/alarm/{labId}/hydrogen` — on danger entry;
  this is what makes a kitchen emergency stop experiments lab-wide
- `status/{ExperimentName}/{deviceId}/run` — `{running, runId}` for `server.py`
- `status/{ExperimentName}/{deviceId}/online` — LWT, retained
- `DataAcquisition/Kitchen/{deviceId}/*` — sensor samples, CM7 payload shape

**Protocol rules (must be in the spec the website is built against):**
- **Commands are never retained; state is always retained.** Firmware **ignores any
  retained message on `cmd`** — otherwise a broker replay could start a leak on reboot.
- Two-phase start: `start(spec)` → validate/clamp → `ack` with the interpreted spec →
  `confirm(runId)` within 60 s → gas flows. Mismatched or stale `runId` is rejected.
- The kitchen needs an `experimentDefs` entry with `mayEmit: true`. **This is broader
  than "its own alarm does not revoke its own permit."** `compute_permit`
  ([server.py:225-231](../centralPLC/server.py#L225-L231)) skips **every** active alarm
  whose lab matches the experiment's own lab, regardless of which device raised it — so
  the permit offers *no* protection against a same-lab neighbour either.

  That gap is covered by the peer-alarm interlock, which filters by **device id, not
  lab**: a neighbour's alarm is not the kitchen's own id, so it sets `peerAlarmActive`
  and forces FULLY_VENTILATING. In short: **the permit's role is cross-lab; the
  peer-alarm topic covers same-lab and cross-lab both.**

---

## Sequencing

0. **Regenerate the diagrams.** `diagrams/safety_dataflow.py` output depicts the
   request→veto→pins path that no longer exists, and `state_machine` shows the
   latch-mediated danger transition. Update `kitchen_diagrams.py` to the single-core
   model before anyone reads them as current.
1. **Toolchain — done.** `g++ 16.2.0` (MinGW-W64 x86_64-ucrt-posix-seh) at
   **`C:\mingw64\bin\g++.exe`**. Not on the shell's default PATH, so the test command
   below prepends it.
2. **Merge the core — done.** `SafetyCore` + `ExperimentCore` → `KitchenCore`; the
   test suite is ported onto the single-core API (96/96 pass). Most tests survived
   verbatim (they assert outputs for a given sensor state); the ones asserting the
   old one-pass skew were **deleted, not ported** — that behaviour is gone on
   purpose, replaced by same-pass assertions (`danger_local_sensor_threshold_forces_gas_off`
   et al. now assert `core.state()` and the returned `OutputRequest` from a single
   `update()` call).
3. **Hardware layer** — `Outputs`, `Sensors`; extend `Expansion.cpp` with
   analog-output and relay-expansion support. `Sensors.cpp` owns both unit conversions
   (mA offset, counts→mL/s) and the slot→channel mapping table.
4. **Protocol layer** — `Protocol.cpp`: parse/validate/clamp run specs and threshold
   tables, build state/ack/alarm payloads. Reject-with-reason on every malformed input.
   Owns the %↔counts conversion, the `holdStop` fallback to `HOLD_MAX_DURATION_MS`, and
   rejection of `maxInventory_mL` on a non-LEAKING phase.
5. **Generalise `Comms`** — three backward-compatible changes so CM7 still compiles
   unchanged (see below).
6. **Integration** — `kitchen.ino` wiring it together; copy in `Comms`/`Expansion`.
7. **Bench verification** — on hardware.

**Step 5 in detail.** The kitchen needs retained publishes on `state`, `sensors/power`,
`online` and the alarm topic, plus subscriptions to `cmd`, `config/set`,
`safety/permit/…` and the peer-alarm wildcard. Today `mqttPublish()` hardcodes
`retain=false` (`Comms.cpp:308`), the subscribe set is hardcoded to CM7's two config
topics (`:269-270`), and the LWT topic is hardcoded (`:266`). Three additive changes:

1. `mqttPublish` gains a defaulted `bool retain = false` — existing call sites keep
   compiling with current semantics.
2. `commsSetSubscriptions(const char** topics, int count)`, called before
   `commsBegin()`, defaulting to the existing config-topic subscribes.
3. `commsSetLwt(const char* topic, const char* payload)`, defaulting to current
   behaviour.

**Copy, don't share.** `Comms` is copied into `kitchen/` rather than shared with CM7.
Arduino sketch directories don't share files across folders without symlinks or build
hackery, and CM7 runs DAQ hardware today — changing its comms layer to serve the kitchen
is a change to a working system for an unrelated reason.

---

## Verification

**Desktop (runs here, every change):**
```
export PATH="$PATH:/c/mingw64/bin"
g++ -std=c++17 -DKITCHEN_ALLOW_PLACEHOLDER_SCALES -I kitchen \
    kitchen/test/test_safety.cpp kitchen/KitchenCore.cpp \
    kitchen/RegisterSequencer.cpp -o test_safety \
    && ./test_safety
```

`KITCHEN_ALLOW_PLACEHOLDER_SCALES` is required until the analog-scale constants are
measured on the bench. The tests drive counts directly and never exercise a
physical-unit conversion, so placeholder scales are harmless there, but a **firmware**
build without real values is refused at compile time on purpose.

The suite must cover, at minimum:
- each danger condition individually forces gas off + full ventilation
- a run spec requesting gas while a danger condition holds → gas still off
- threshold clamping: a website value *below* the firmware minimum is rejected;
  a value above it is accepted (sensitivity may only increase)
- **threshold boundary is at-or-above**: a reading exactly at threshold trips, one count
  below does not (both sides, so a change back to `>` fails loudly)
- arm timeout expires → back to WAITING, no gas
- selector in equipment-test → leak run rejected
- full happy path LEAKING → HOLD → VENTILATING → FULLY_VENTILATING → WAITING
- WAITING drives `VENT_SPEED_IDLE_PCT`, not 100 %; FULLY_VENTILATING drives 100 %
  regardless of what the run spec asked for; HOLD drives 0 %
- register sequencing, with a fake clock: opening exhaust+inlet together energises
  exhaust at `t=0` and inlet only at `t≥1000 ms`; closing all three is simultaneous;
  inlet opening alone is immediate; a request that changes before the deadline
  supersedes the pending inlet action rather than firing it late

**Single-core contract** (replaces the old `observeLatch()` skew tests):
- danger during LEAKING → **on the same pass**, `state() == FULLY_VENTILATING` *and*
  the returned `OutputRequest` has gas off / fan 100 % / all registers open. Assert
  both in one call, so a reintroduced two-pass split fails loudly.
- the condition clearing does **not** resume LEAKING
- ack + 5 min all-clear → WAITING, with `ackRequired_`/`acked_` reset for the next
  episode
- the ack is not a shortcut past the hold: acking early still requires the full
  all-clear duration
- a danger firing part-way through the all-clear hold **restarts** the 5 min clock
- routine purge (operator `stop()`) auto-returns with no ack, and the purge is not
  skippable
- **promotion, not demotion**: a danger during a routine (`ackRequired_ == false`)
  purge sets `ackRequired_ = true`; a routine re-entry while `ackRequired_ == true`
  does not clear it
- re-entering FULLY_VENTILATING while already there does not discard an existing
  `acked_`

**Operator abort:**
- selector flipped mid-leak → FULLY_VENTILATING, `ackRequired_`,
  `reason() == OPERATOR_ABORT`, and all-clear alone does not release it
- entry is idempotent: calling it every pass the selector stays wrong does not discard
  an ack already given

**Phases:**
- leak duration is timed from **gas flowing**, not from LEAKING entry (regression: a 5 s
  leak spec must not be consumed by the 30 s warm-up gate and deliver zero gas)
- HOLD runs for its duration then ventilates; HOLD also ends on quorum
- quorum ignores absent **and stale** sensors; `quorumCount == 0` never trips

**DAQ sensor power — dedicated test list** (both `localSensorsOn` and
`remoteSensorsOn` outputs, exercised with a fake clock; each bullet is one test case):

1. On construction / immediately after `setup()`, before any state transition: both
   sensor outputs are OFF.
2. WAITING, sensors OFF, selector at leak-test, `start`+`confirm` drives the machine
   into LEAKING for the first time: both sensor outputs flip to ON on the same step
   that LEAKING is entered.
3. Immediately after that first ON transition (`t=0` relative to the ON edge), the
   machine has **not** yet proceeded to the rest of the LEAKING sequencing (e.g. gas
   setpoint/inventory integration has not started) — it is holding in the warm-up gate.
4. At `t = SENSOR_WARMUP_MS - 1`, still gated: sequencing still has not proceeded.
5. At `t = SENSOR_WARMUP_MS` (30000 ms), the gate releases and LEAKING sequencing
   proceeds normally (setpoint applied, inventory integration begins).
6. While the warm-up gate is pending, a danger condition (e.g. local sensor above
   threshold, e-stop) still forces gas off + full ventilation immediately — the
   warm-up gate never delays the danger check.
7. Sensors already ON (carried over from a prior run) and a new LEAKING entry starts:
   **no** second warm-up gate — the 30 s gate is only for the off→on transition.
8. Sensors stay ON, unchanged, across LEAKING → HOLD → VENTILATING →
   FULLY_VENTILATING — no bullet in this range ever commands them OFF.
9. On FULLY_VENTILATING → WAITING (routine purge exit), sensors remain ON at the
   moment WAITING is entered.
10. WAITING idle timer starts counting from the step WAITING is entered post-purge —
    confirm it does not carry over stale elapsed time from a previous WAITING stay.
11. At `t = SENSOR_IDLE_TIMEOUT_MS - 1` still in WAITING with no new run: sensors ON.
12. At `t = SENSOR_IDLE_TIMEOUT_MS` still in WAITING with no new run: both OFF.
13. A new run beginning before the idle timeout: timer cancelled, sensors stay ON
    (case 7 applies, no warm-up gate).
14. A new run beginning at or after the idle timeout (sensors now OFF): entering
    LEAKING re-triggers the full off→on warm-up gate from cases 2–5.
15. Selector flips to equipment-test while in WAITING with sensors OFF: sensors flip ON
    immediately, with **no** 30 s warm-up gate and no dependency on the idle timer.
16. Selector at equipment-test continuously: sensors stay ON regardless of how long
    WAITING is held — the idle-timeout-to-OFF path never fires.
17. Selector flips from equipment-test back to leak-test while sensors are ON and
    WAITING is otherwise idle: sensors remain ON and the idle timer starts fresh from
    that step.
18. `localSensorsOn` and `remoteSensorsOn` change together for every case above —
    assert both move in lockstep so a future split is a deliberate, visible change to
    this test list rather than a silent divergence.
19. A danger condition does not force sensor power in either direction as a side
    effect — FULLY_VENTILATING inherits whatever sensor-power state was in force.

**Compile:** Arduino IDE (`Arduino_Opta_Blueprint` and `AlPlc_Opta` are installed;
the mbed_opta **board core** still needs installing via Boards Manager). No
`arduino-cli` on this machine, so compilation happens on your side — I write to
minimise round-trips and fix reported errors.

**Bench (hardware, before any hydrogen):**
- Each relay actuates the right register/fan/valve; verify against the I/O map.
- Inlet delay: command exhaust+inlet open and confirm by ear/eye that the inlet damper
  starts ~1 s after the exhaust; confirm a close command moves all three at once.
- Analog outs produce expected voltages across 0–100 %.
- Selector at both positions reports the right role in the retained state message.
- **E-stop with the Ethernet cable unplugged** → full ventilation + gas cut.
  This is the headline no-network safety claim; test it explicitly.
- **Human ack with the Ethernet cable unplugged** — trip a danger, unplug, press the
  USER button, and confirm the kitchen exits after the all-clear hold.
- Kill `server.py` mid-run → permit stops → warning only, run continues (by design).
- Publish a fake peer alarm → kitchen goes to FULLY_VENTILATING.
- Publish a retained `start` on `cmd` → **must be ignored**.
- Subscribe to `KitchenControl/{deviceId}/sensors/power`: confirm it is retained,
  starts `{on: false}` after boot, and flips to `{on: true}` the moment a leak run
  starts or the selector is moved to equipment-test.
- Start a leak run and confirm the ~30 s warm-up gate is real time on the bench, not
  just in the fake-clock test: gas does not begin flowing until the gate releases.
- Leave the kitchen idle in WAITING after a purge for slightly over 10 minutes (or a
  temporarily shortened `SENSOR_IDLE_TIMEOUT_MS`) and confirm `sensors/power` flips to
  `{on: false}` unprompted, with no MQTT command sent.
- With the CM7 DAQ firmware's subscription wired up (once implemented there): confirm
  it actually stops/starts sampling in response to this topic.

**Live (with hydrogen, staged):** leak at minimum rate with a short timeout stop
condition; confirm inventory integration matches expectation, ventilation decay is
recorded, and the mandatory purge runs before WAITING is re-entered.

---

## Blocking before hydrogen

These must hold real, measured values before gas flows. They are backed by a
compile-time `#error` guard in `Kitchen_Settings.h`, so a firmware build with any of them
still at its placeholder is refused; `KITCHEN_ALLOW_PLACEHOLDER_SCALES` overrides it for
desktop tests and pre-calibration bench work, which are deliberate acts.

- **`ADC_COUNTS_AT_4MA`, `ADC_COUNTS_PER_MA`, `SENSOR_FULLSCALE_PCT`** — the 4–20 mA
  sensor scale. Measure per the procedure in the units section above.
- **`FLOW_COUNTS_PER_VOLT`, `FLOW_ML_PER_SEC_AT_10V`** — the 0–10 V flow scale.
  `INVENTORY_CAP_ML` is a danger condition that cannot trip meaningfully until these are
  real, since delivered inventory is integrated from them.
- **`SENSOR_THRESHOLD_FIRMWARE_MIN_COUNTS`**, **`FLOW_LIMIT_COUNTS`**,
  **`INVENTORY_CAP_ML`** — the numeric trip points themselves.

## Open items

- **`HOLD_MAX_DURATION_MS` (10 min placeholder)** — the ceiling a `holdStop` is clamped
  to, and the fallback when a spec omits `holdStop`. Confirm what a realistic maximum
  undisturbed-propagation window is before hydrogen.
- **`INLET_OPEN_DELAY_MS` default 1000 ms** — confirm on the bench that 1 s is enough
  for the exhaust damper to travel before make-up air is admitted.
- **`ExperimentName` / `labId` values** — must match an `experimentDefs` entry in
  Firebase with `mayEmit: true`, or `server.py` logs "no match" and publishes no
  permits ([server.py:270](../centralPLC/server.py#L270)).
- **Remaining numeric values** — purge duration (5 min assumed), fan-speed curve, and
  `VENT_SPEED_IDLE_PCT` (the WAITING background rate — high enough to satisfy the
  equipment-test "ventilation running" precondition). The safety-critical ones are
  listed under *Blocking before hydrogen* and are compiler-enforced; these are not, so
  they need a deliberate review before hydrogen.
- **Whether this PLC's own local sensors should ever be blocked by the same on/off
  logic as remote CM7 DAQ instances** is not yet decided. Tracked as a separate output
  so the answer can change without touching the remote-command path.
- **`SENSOR_WARMUP_MS` (30 s) and `SENSOR_IDLE_TIMEOUT_MS` (10 min)** — placeholders;
  confirm against the actual sensor datasheet warm-up spec before hydrogen.
- **Remote sensor-power MQTT topic name** — reconcile
  `KitchenControl/{deviceId}/sensors/power` against whatever topic the CM7 DAQ firmware
  already subscribes to, or add the subscription there if none exists yet.
- **Sensor slot → physical pin/channel mapping table** — `KITCHEN_MAX_LOCAL_SENSORS`
  (21) spans three physically different sources (5 base spare, 6 on the A0602, 10 on
  the D1608E's analog-capable digital inputs — 6 of its 16 reserved for future discrete
  buttons). `Kitchen_Settings.h` currently lists these as three separate blocks of loose
  `#define`s with no single table connecting a `SensorState.localSensors[]` slot index
  to its physical source. Deferred to `Sensors.cpp`, which will consume the mapping.
- **D1608E button inputs** — 6 of the 16 digital inputs are reserved for future discrete
  buttons, but nothing yet defines what they do or how they surface in `SensorState`.
- **Peer sensor tracking storage** — `SensorState.peerAlarmActive`/`peerAlarmStale` are
  flat summary booleans, not a store. Nothing in `kitchen/` yet builds the per-zone
  tracking table this summarizes — `centralPLC.ino`'s `TrackedZone tracked[]` pattern
  (topic + active bool, populated by subscribing to `status/#`) is the precedent, and
  `KITCHEN_MAX_PEER_ZONES` (32) is already sized to match. `Sensors.cpp`'s
  responsibility once written.
- **`kitchenold.ino` and `Old/`** appear superseded. Decide whether to retire them.

## Future: external electronic trip input (not implemented now)

A planned future electronics board should be able to force FULLY_VENTILATING via a
hardwired input. The single-core design supports this as a three-line change:

1. Pin constant in `Kitchen_Settings.h` (use a spare base input, A4–A7)
2. One field in `SensorState` — e.g. `bool externalTripActive`
3. One clause in `dangerActive()` — `|| s.externalTripActive`

The state machine, `Outputs`, and the MQTT contract are all unaffected, because danger
conditions are an open-ended list in a single function.

**Two things to preserve now so that stays true:**
- Keep A4–A7 genuinely spare (already reserved in the I/O map). Note that wiring the
  trip as a **dry contact in series with the e-stop** would need *zero* firmware
  change — worth considering if it is a pure safety trip.
- Give the alarm payload a **reason/source field** from the start (the existing alarm
  schema's `description`), so an e-stop, an electronics trip, and a concentration trip
  are distinguishable in the Firebase log rather than all reading as "alarm".
