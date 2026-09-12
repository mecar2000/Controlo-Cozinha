# Kitchen H2 Control — Firmware

Everything about the `kitchen/` firmware in one place: why it's built this
way, how the control path and wire protocol actually work line-by-line, the
legacy design it replaced, and deferred future work. Previously split across
`implementation-plan.md`, `KitchenCore-and-Protocol.md`,
`legacy-flow-pump-control.md` and `FUTURE-sensor-batching.md` — merged
2026-09-12 so the firmware story lives in one file instead of four
overlapping ones. `docs/webapp-design.md` is the companion doc for the
operator-facing web app this firmware talks to over MQTT.

Written/maintained against the code as of the 2026-09-07 review-protocol
changes (sensor-power split, `EXPANSION_FAULT`, run modes, LED word, gas
lamp, file merges) and the 2026-09-12 `WARMING_UP` state change (commit
`a354b82`).

---

# Part 1 — Context and architecture rationale

## Context

The experimental kitchen runs **hydrogen leak-propagation experiments**:
hydrogen is released at a controlled rate, its dispersion is measured by H2
sensors, and ventilation is then applied to measure how fast concentration
decays. The same room is also used to test equipment that needs a live
hydrogen supply.

The kitchen previously ran a standalone threshold-alarm monitor (4 inputs →
relays → latch, formerly `Old/kitchenold.ino`, now deleted — see Part 3 for
what it did). It could not run experiments, had no modes, no MQTT command
interface, and no integration with the lab-wide safety system that
`centralPLC/server.py` implements.

This firmware replaces it with a state-machine control system on one Arduino
Opta (+ A0602 analog expansion + D1608E relay expansion) that:

- runs scripted leak experiments commanded over MQTT from a website
  (`docs/webapp-design.md`),
- enforces **firmware-owned** safety rules that no network message can
  weaken,
- integrates with the existing `status/…` + `safety/permit/…` scheme in
  `server.py`,
- publishes telemetry for browser display, reusing the CM7 DAQ payload
  conventions.

Diagrams live in `diagrams/` (`state_machine`, `safety_dataflow`,
`hardware_io_map`, `mqtt_topic_map` — regenerate with
`diagrams/kitchen_diagrams.py`). Regenerated 2026-09-07 for the single-core
model.

**Intended outcome:** a firmware that can be handed to a safety reviewer with
the claim *"nothing the website sends can open the gas valve when the safety
layer says no"* — and have that claim be verifiable by reading one file and
running one test suite.

## Core architecture: one state machine

**There is exactly one core.** `KitchenCore` holds the state, reads
`SensorState`, and produces an `OutputRequest`. There is no request/veto
split, no second layer that rewrites what the first layer wanted, and no
latch shared as a boundary object between two modules.

```cpp
// The whole control path.
OutputRequest out = core.update(sensors, nowMs);   // the audit target
outputs.drive(out, nowMs);                          // the ONLY pin writer
```

### Why the two-layer model was dropped

The original design had `SafetyCore` + `ExperimentCore` in a request/veto
pair — `ExperimentCore` produced a request and `Safety::apply()` rewrote it,
with a `SafetyLatch` passed between them and an `observeLatch()` call to move
the state machine on the pass *after* a danger fired. It bought a separation
of powers that had no second party to separate: one core, one binary, one
author, one review. What it cost was real:

- **Two sources of truth for "are we in danger"** — the latch and the state
  — which had to be kept in sync by a documented one-pass skew that every
  reader had to be taught, and every test had to assert.
- **A transition that no state could initiate.** Danger changed the pins in
  one place and the state in another, so "what happens on danger" could not
  be read off the transition table.
- **A pure function that was not pure** — `apply()` took `SafetyLatch&`
  inout, so the purity claim needed a paragraph of qualification to stay
  true.

A single state machine keeps the property that actually matters — the
website's request is data, never a command that reaches a pin — because the
run spec is only ever *read* by `update()`, and `update()` decides the
outputs itself.

### What replaces the veto

**Danger is a transition, not a rewrite.** At the top of `update()`, before
any per-state logic, danger conditions are evaluated. If any holds, the
machine transitions to `FULLY_VENTILATING` from whatever state it was in, and
`update()` returns that state's outputs. Same pass, no skew: the state and
the pins agree at every instant, which is exactly what the latch existed to
fake. See Part 2 §5 for the real, current shape of `update()`.

`outputsFor(FULLY_VENTILATING)` is a fixed, unconditional block: gas relay
closed, setpoint 0 V, all registers open, fan 100 %, alarm on. It reads no
run spec and takes no arguments that the website can influence. **That
function plus `dangerActive()` is the entire safety audit target** — two
functions in one file, both pure, both desktop-testable.

**The website cannot reach the pins** because `RunSpec` is only consulted
inside the `LEAKING` / `HOLD` / `VENTILATING` branches, and those branches
are unreachable while a danger condition holds — the danger check runs first
and moves the state every pass.

**Invariants to enforce by review** (stated as comments at the two sites):

1. `Outputs::drive()` is the only place that calls
   `digitalWrite`/`analogWrite` on control pins.
2. `dangerActive()` is the only place danger conditions are evaluated.
3. `outputsFor()`'s `FULLY_VENTILATING` branch never reads `spec_`.

**`OutputRequest` remains a desired end state, not a sequence of actions.**
It says *which* registers should be open, never *in what order* they get
there. Actuation ordering (the inlet delay, Part 2 §11) belongs to
`RegisterSequencer`/`Outputs`, so the ordering applies identically to
danger-forced transitions and to spec-driven ones.

## Danger conditions (firmware-owned, OR'd, from any state)

Evaluated by `dangerActive()`, called at the top of `update()` before any
per-state logic (see Part 2 §4 for the exact current code). Any one of them
transitions to `FULLY_VENTILATING` with `ackRequired_ = true`.

| Condition | Applies in |
|---|---|
| Local sensor **at or above** its threshold | all modes |
| Local sensor silent > 10 s, if `expectedOn` | all modes (this PLC's own sensors) |
| Flow > limit sustained > 2 s | leak-test only |
| Delivered inventory cap exceeded | leak-test only |
| Peer PLC alarm received over MQTT | all modes (incl. equipment-test) |
| Permit present and false | all modes — *but see below: never present outside a run* |
| Physical e-stop button (`A3`, 0–5 V) | all modes — **works with no network** |
| Expansion (A0602/D1608E) missing or wrong type | all modes |
| External trip input | reserved, not implemented |

**Boundary convention: at-or-above (`>=`), everywhere.** A reading exactly at
its threshold trips rather than passes. This applies to the per-sensor danger
check and to the quorum stop-condition alike — they compare the same
quantity for different purposes and must not drift apart. Stated once in
`Kitchen_Settings.h`.

**The permit is structurally inert outside a run — do not read that table
row as coverage that exists.** `server.py` publishes permits only for
devices in `experiment_state` (`server.py:351`), and that dict is populated
only when a device publishes `status/…/run` with `running: true`
(`server.py:280`). So in WAITING, in ARMED, and throughout equipment-test, no
permit is ever sent, `permitPresent` stays false, and this check never
fires.

**Peer alarms are therefore the only live lab-wide interlock outside a run.**
That is a deliberate property, not a gap: permit *absence* warns and never
trips, so the kitchen never depends on `server.py` being alive, and the
retained peer-alarm topic survives broker reconnects with no bridge running.

### Ventilation registers

Three registers, each with an open coil and a close coil:

| Register | Role |
|---|---|
| `CENTRAL` | central register |
| `EXHAUST` | extract air from the room |
| `INLET` | admit make-up air |

**"Ventilation type" in the run spec is any combination of the three** — a
3-bit set, not an enum of named presets. The spec carries the set directly
(`{central, exhaust, inlet}` booleans), so no lookup table is needed and new
combinations need no firmware change. `FULLY_VENTILATING` forces all three
open regardless of the spec.

**Inlet opening delay.** When a transition opens the inlet *together with*
another register, the inlet opens `INLET_OPEN_DELAY_MS` (default 1000 ms, in
`Kitchen_Settings.h`) **after** the others. Rationale: let extraction
establish negative pressure before make-up air is admitted, so the room does
not get pressurised outward.

- **Delay applies to opening only.** Closing is simultaneous — no delay, no
  ordering.
- **The delay is a deferral, never a blocker.** The other registers actuate
  immediately; only the inlet coil waits. Nothing else in the system blocks
  on it.
- **It applies to `FULLY_VENTILATING` too**, including danger-forced entry —
  the purge reaches the same end state 1 s later, which is the intended
  pneumatic behaviour rather than a safety compromise. Gas cut and
  fan-to-100 % are **not** delayed: they happen on the same pass, so the
  hazard is addressed immediately and only the airflow geometry settles a
  second later.
- If the inlet is *already* open, or is opening alone with no other register
  changing, there is nothing to sequence against and it actuates
  immediately.

Implemented in `RegisterSequencer` as a single pending-deadline field checked
each `drive()` call — non-blocking, no `delay()` anywhere in the firmware.
Because `drive()` is called every loop pass, a request that changes again
before the deadline simply supersedes the pending action.

**Superseding a pending open does not restart the timer.** The deadline
restarts only if the inlet was *closed* and is now being opened. If the
inlet is already commanded open with a deadline pending, a request that
merely adds another register (`{exhaust, inlet}` → `{central, exhaust,
inlet}`) **preserves** the existing deadline — an inlet that has already
waited 900 ms does not wait another full second. The delay exists to let
extraction establish negative pressure, which is a property of how long the
exhaust has been open, not of when the most recent request arrived.

### Fan speed by state (firmware constants, `Kitchen_Settings.h`)

| State | Speed | Owner |
|---|---|---|
| WAITING / ARMED | `VENT_SPEED_IDLE_PCT` (low background rate) | firmware constant |
| LEAKING | per run spec (clamped to `VENT_SPEED_MAX_PCT`) | website |
| HOLD | 0 % — fans off, undisturbed propagation | firmware |
| VENTILATING | per run spec | website |
| FULLY_VENTILATING | 100 % | firmware, non-overridable |

Only the two experiment phases take their speed from the run spec. Idle,
hold and purge speeds are firmware-owned so a bad or absent command can
never leave the kitchen under-ventilated at rest or under-ventilated during
a purge. This is enforced structurally: `outputsFor()` reads `spec_` only in
the `LEAKING`/`WARMING_UP`-adjacent and `VENTILATING` branches.

**Threshold rule:** effective threshold =
`min(SENSOR_THRESHOLD_FIRMWARE_MAX_COUNTS, website_value)`. The website can
only make a sensor *more* sensitive, never less. Out-of-range or missing
values fall back to the compiled default. This is how per-sensor calibration
is configurable without letting the network weaken safety.

Counts rise with concentration and the check is `counts >= threshold`, so a
*higher* threshold trips *later* — less sensitive. The constant is a
**ceiling** and the clamp is a `min()`, applied at the point of comparison
inside `dangerActive()` (Part 2 §4), not merely where the value is stored —
so no writer of `LocalSensorReading::thresholdCounts` can weaken a trip
point.

**The quorum threshold is deliberately not clamped**: it ends a phase rather
than cutting gas, a lower value only ends the phase sooner, and a value
above the danger ceiling is unreachable anyway (the single-sensor danger
check trips first).

**Peer sensors** force a Fully-Ventilate while publishing, but going silent
only warns (MQTT warning + distinct LED blink + console) — it does not block
starting an experiment. **This PLC's own sensors going silent does block**,
since that is a hardware fault on the machine running the experiment.

### Units: counts internally, conversion only at the boundaries

**The rule.** Every concentration and flow value inside `KitchenCore` and
`RunSpec` is in **raw ADC counts**. Conversion to physical units happens in
exactly two places — `Sensors.cpp` (inbound, at the read boundary) and
`Protocol.cpp` (at the MQTT boundary, Part 2). No other file converts
anything; the core never sees mA, %, or volts. This is what stops a
three-unit confusion from growing back.

**Two independent analog scales — never crossed.** The mA constants apply
only to the six H2 sensors on the A0602 expansion; the volt constants apply
only to A1's flow feedback on the base board, whose ADC resolution differs.
All five are **blocking before hydrogen** and backed by a compile-time
`#error` guard, so an uncalibrated build cannot be flashed by accident —
only on purpose, via `KITCHEN_ALLOW_PLACEHOLDER_SCALES`.

| Constant | Scale | How to get it |
|---|---|---|
| `ADC_COUNTS_AT_4MA` | 4–20 mA (A0602) | inject 4.00 mA, read the raw count |
| `ADC_COUNTS_PER_MA` | 4–20 mA (A0602) | inject 20.00 mA; `(c20 - c4) / 16` |
| `SENSOR_FULLSCALE_PCT` | 4–20 mA (A0602) | sensor datasheet |
| `FLOW_COUNTS_PER_VOLT` | 0–10 V (base A1) | feed 0 V and 10 V; difference / 10 |
| `FLOW_ML_PER_SEC_AT_10V` | 0–10 V (base A1) | flowmeter datasheet |

Two points are needed for each slope because a slope needs a span to be
measured over. **Do not use a sensor in clean air as the 4 mA source** — its
real output may be 4.05 mA, and that deviation is exactly what the
calibration offset below exists to correct. Calibrate one channel and check
a second: a disagreement of more than a count or two on the 4 mA reading is
the ADC, not the wiring, and is worth knowing before hydrogen.

**Inventory integration depends on the flow constants.** An earlier draft
treated raw counts *as* mL/s, which made `INVENTORY_CAP_ML` meaningless — at
~3000 counts against a 5000 mL cap it tripped after roughly two seconds of
any flow, aborting every run.

### Sensor quorum stop-condition + per-sensor calibration offset

**Quorum stop is a stop-condition primitive, not a danger condition.** "Stop
the phase once N sensors read at-or-above X" is a website-configurable
experiment-design choice (`SensorQuorumStop { thresholdCounts, quorumCount }`
in `RunSpec.h`), evaluated by `KitchenCore` each pass against the live
sensor table. It is independent of and additional to the single-sensor
danger check (any one sensor at or above its own threshold still trips a
full danger response regardless of quorum state). Available on `leakStop`,
`holdStop` and `ventStop`.

**The website sends `%`; the struct carries counts.** `Protocol` converts
once at parse time (Part 2 §3), so the MQTT contract is unchanged but
`KitchenCore` only ever compares counts to counts. The counts↔% mapping is
**firmware-owned** because this condition cuts gas — the website cannot be
the authority on what "4 %" means. The `ack` echoes both the requested % and
the interpreted counts, so a miscalibration is visible from the browser
rather than silent.

**Absent and stale sensors are excluded from the count.** Silence is not
evidence of low concentration. (A stale sensor that is `expectedOn` is
separately a danger condition, so this exclusion never hides a fault — it
just keeps it out of the quorum arithmetic.)

**Calibration offset is a separate, firmware-owned correction — not part of
the quorum condition.** The kitchen's H2 sensors are the same sensor type,
but each one's wiring run back to the PLC is a different length, so each
reads a slightly different current for the same real concentration. This is
compensated with a manual per-sensor offset (e.g. +0.1 mA for sensors 1–2,
+0.2 mA for sensors 3–6), calibrated by hand and stored as a firmware
constant table (`SENSOR_CALIBRATION_OFFSET_MA[]` in `Kitchen_Settings.h`),
indexed the same way `SensorState.localSensors[]` is indexed.

**The table stays in mA** — it is a hand-calibrated physical measurement and
should stay in the unit you read off the meter. `Sensors.cpp` converts it at
the read boundary using `ADC_COUNTS_PER_MA`:

```cpp
raw_counts + (int16_t)(SENSOR_CALIBRATION_OFFSET_MA[i] * ADC_COUNTS_PER_MA)
```

The offset is signed, and the result is clamped to the ADC range — a
negative offset on a near-zero reading must not wrap a `uint16_t`.

**Where the offset is applied — once, at the read boundary.** `Sensors.cpp`
owns a single calibrated-read function that applies each sensor's offset
before the value goes anywhere else. Every downstream consumer — the
per-sensor danger check *and* the quorum stop-condition — only ever sees the
corrected value in `SensorState.localSensors[i].counts`.

## Files

Sketch directory `kitchen/` (sketch name must match folder).

**Pure core — no Arduino headers, desktop-testable:**

| File | Contents |
|---|---|
| `KitchenCore.h/.cpp` | `KitchenState`, `OutputRequest`, `SensorState`, `DangerReason`, `dangerActive()`, `update()`, `outputsFor()`, transitions, stop conditions, inventory integration, arm timeout, sensor power, threshold clamping, plus `RegisterSequencer` and `PeerAlarmTable` |
| `RunSpec.h` | `RunSpec` + stop-condition primitives, incl. the 3-bit register set |
| `Kitchen_Settings.h` | Pin map, thresholds, timings, ceilings, `MQTT_MAX_PAYLOAD` |

**`PeerAlarmTable`** (in `KitchenCore.h/.cpp`) — pure, per-zone peer alarm
tracking, keyed by MQTT topic. Replaced a flat `peerAlarmActive` boolean that
let one zone publishing `{danger:false}` cancel *another* zone's active
alarm — a silent failure of the primary lab-wide interlock. A zone stays
active until that zone reports clear; silence never clears (it warns).
Overflow past `KITCHEN_MAX_PEER_ZONES` latches the interlock safe rather
than dropping the alarm.

**Arduino shim — hardware and network:**

| File | Contents |
|---|---|
| `kitchen.ino` | `setup()`/`loop()`; wires sensors → core → outputs; scheduling |
| `Outputs.h/.cpp` | Relay + analog-out map; `drive(out)`; **only pin writer**; register actuation sequencing (inlet delay); status LEDs |
| `Sensors.h/.cpp` | Local ADC reads (base + expansion), selector, e-stop, flow feedback; peer-alarm and permit state from MQTT; staleness timers; builds `SensorState` |
| `Protocol.h/.cpp` | JSON parse/serialise for `cmd`/`ack`/`state`/`config`; run-spec validation + clamping; alarm publish (Part 2) |
| `Comms.h/.cpp` | Ethernet, NTP, MQTT reconnect with backoff, LWT, `mqttPublish()`, run-mode logging (folded in from `Log.*` 2026-09-07) |
| `Expansion.h/.cpp` | A0602 analog I/O + PWM, D1608E relay expansion, health check |
| `Kitchen_Secrets.h` | MAC + broker credentials — **gitignored**, build machine only |

**Tests:** `kitchen/test/` — native C++, no Arduino. See `kitchen/README.md`
for the build command (needs updating whenever `test_protocol.cpp` is
included — it needs ArduinoJson on the include path).

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
| Relays ×8 (D1608E) | 3 vent registers (2 coils each) · fan on/off · flowmeter cut · alarm |
| A0602 PWM ch. `OA_CH_8` | "Gas may be present" breathing lamp (24 V, via MOSFET) |
| Status LEDs ×4 | 2+2 word: connection (D0/D1) + run-state (D2/D3); alarm = all four fast-blink |

Sensors are a **table**, not six variables — the count must grow without
restructuring.

## MQTT contract

Reuses the existing scheme in `centralPLC/server.py` rather than inventing a
parallel one.

**Consumed:**
- `safety/permit/{deviceId}` → `{permit, seq}`, ~1 s heartbeat, not retained.
  Veto when present; **absence warns, does not trip**.
- `status/{ExperimentName}/{deviceId}/alarm/{labId}/{hazard}` → peer alarms,
  retained. **Primary interlock.** Filters out its own device id.
- `KitchenControl/{deviceId}/cmd` → `start(spec)` · `confirm(runId)` ·
  `stop` · `ack`
- `KitchenControl/{deviceId}/config/set` → per-sensor threshold table
- `KitchenControl/{deviceId}/mode` → run-mode override (CLEAN/VERBOSE/
  DEBUG/PROFILING), not retained
- `DataAcquisition/Kitchen/{id}/#` → remote DAQ liveness check

**Published:**
- `KitchenControl/{deviceId}/state` — retained: mode, phase, selector role,
  elapsed
- `KitchenControl/{deviceId}/sensors/power` — retained: `{on: bool}`, on
  change; commands remote CM7 DAQ instances
- `KitchenControl/{deviceId}/ack` — validated spec echo, or rejection +
  reason
- `KitchenControl/{deviceId}/config/ack` — the clamped threshold table
- `KitchenControl/{deviceId}/log` — run-mode log lines, non-retained JSON
- `status/{ExperimentName}/{deviceId}/alarm/{labId}/hydrogen` — on danger
  entry
- `status/{ExperimentName}/{deviceId}/run` — `{running, runId}` for
  `server.py`
- `status/{ExperimentName}/{deviceId}/online` — LWT, retained
- `DataAcquisition/Kitchen/{deviceId}/*` — sensor samples, CM7 payload shape

**Protocol rules:**
- **Commands are never retained; state is always retained.** Firmware
  ignores any retained message on `cmd` — otherwise a broker replay could
  start a leak on reboot.
- Two-phase start: `start(spec)` → validate/clamp → `ack` with the
  interpreted spec → `confirm(runId)` within 60 s → gas flows. Mismatched or
  stale `runId` is rejected.
- The kitchen needs an `experimentDefs` entry with `mayEmit: true`.
  `compute_permit` (`server.py:225-231`) skips every active alarm whose lab
  matches the experiment's own lab, regardless of which device raised it —
  so the permit offers no protection against a same-lab neighbour either.
  That gap is covered by the peer-alarm interlock, which filters by
  **device id, not lab**. In short: the permit's role is cross-lab; the
  peer-alarm topic covers same-lab and cross-lab both.

## Blocking before hydrogen

These must hold real, measured values before gas flows. Backed by a
compile-time `#error` guard in `Kitchen_Settings.h`; `KITCHEN_ALLOW_PLACEHOLDER_SCALES`
overrides it for desktop tests and pre-calibration bench work.

- **`ADC_COUNTS_AT_4MA`, `ADC_COUNTS_PER_MA`, `SENSOR_FULLSCALE_PCT`** — the
  4–20 mA sensor scale.
- **`FLOW_COUNTS_PER_VOLT`, `FLOW_ML_PER_SEC_AT_10V`** — the 0–10 V flow
  scale. `INVENTORY_CAP_ML` cannot trip meaningfully until these are real.
- **`SENSOR_THRESHOLD_FIRMWARE_MIN_COUNTS`**, **`FLOW_LIMIT_COUNTS`**,
  **`INVENTORY_CAP_ML`** — the numeric trip points themselves.

## Open items

- **`HOLD_MAX_DURATION_MS` (10 min placeholder)** — confirm a realistic
  maximum undisturbed-propagation window before hydrogen.
- **`INLET_OPEN_DELAY_MS` default 1000 ms** — confirm on the bench.
- **`ExperimentName` / `labId` values** — must match an `experimentDefs`
  entry in Firebase with `mayEmit: true`.
- **Purge duration (5 min assumed), fan-speed curve, `VENT_SPEED_IDLE_PCT`**
  — need deliberate review before hydrogen.
- **`SENSOR_WARMUP_MS` (70 s) and `SENSOR_IDLE_TIMEOUT_MS` (10 min)** —
  confirm against the actual sensor datasheet warm-up spec.
- **Remote sensor-power MQTT topic name** — reconcile
  `KitchenControl/{deviceId}/sensors/power` against the CM7 DAQ firmware's
  own subscriptions.
- **Sensor slot → physical pin/channel mapping table** — `Kitchen_Settings.h`
  currently lists three separate blocks of loose `#define`s with no single
  table connecting a `SensorState.localSensors[]` slot index to its physical
  source.
- **D1608E button inputs** — 6 of 16 digital inputs reserved for future
  discrete buttons; nothing defines what they do yet.

## Future: external electronic trip input (not implemented now)

A planned future electronics board should be able to force
`FULLY_VENTILATING` via a hardwired input. The single-core design supports
this as a three-line change:

1. Pin constant in `Kitchen_Settings.h` (a spare base input, A4–A7)
2. One field in `SensorState` — `bool externalTripActive` (already exists,
   unused — `DangerReason::EXTERNAL_TRIP` is already wired to it in
   `dangerActive()`)
3. Nothing else — the clause already exists

**Two things to preserve so that stays true:**
- Keep A4–A7 genuinely spare. Wiring the trip as a **dry contact in series
  with the e-stop** would need *zero* firmware change.
- Give the alarm payload a **reason/source field** (already does — the
  `description` field) so an e-stop, an electronics trip, and a
  concentration trip are distinguishable in the log.

---

# Part 2 — KitchenCore & Protocol, line-by-line

Companion detail to Part 1. Covers the two files that carry the whole
control decision and the whole wire boundary.

## KitchenCore — the entire control path

### 1. What it is for

`KitchenCore` is the **whole decision**. One class, one state machine. It
reads a `SensorState` (everything measurable) and returns an
`OutputRequest` (a desired end-state for every actuator). There is
deliberately **no second layer** — see Part 1 for why the original
`SafetyCore`/`ExperimentCore` split was dropped.

It is **Arduino-free** — includes only `<stdint.h>`, `RunSpec.h`,
`Kitchen_Settings.h`. That is what lets `test/test_states.cpp`/`test_danger.cpp`
compile it with `g++` and drive it with a fake clock. Every timing is a
`uint32_t nowMs` **parameter**, never a `millis()` call.

### 2. The audit claim

Two functions are the entire safety-relevant surface:

- **`dangerActive(const SensorState&, DangerReason&)`** — `static`, takes
  only `SensorState`, **never reads `spec_`**. The only place danger
  conditions are evaluated.
- **The `FULLY_VENTILATING` branch of `outputsFor()`** — a fixed block: gas
  off, all registers open, fan 100%, no `spec_` read.

Because `dangerActive()` cannot see anything the website sent (`spec_` is
private, not a parameter), and because it is checked *first* every pass as a
state transition, the claim "nothing the website sends can open the gas
valve when the safety layer says no" reduces to reading those two
functions. The invariants are enforced *by review*, not the compiler — the
header lists them explicitly (Part 1).

### 3. The state machine

The warm-up gate used to be a boolean (`warmupPending_`) checked inside
`LEAKING`. Since 2026-09-12 (commit `a354b82`) it is its own state,
`WARMING_UP`, inserted between `ARMED` and `LEAKING`.

```
WAITING ──start()──> ARMED ──confirm()──┬─(cold)─> WARMING_UP ──warm──> LEAKING ──stop cond──> HOLD
   ^                   │                └─(warm)──────────────────────────┘                       │
   │            ARM_TIMEOUT_MS                                                                     │ stop cond
   │                   v                                                                           v
   └──────────── WAITING <──────────────────────────── FULLY_VENTILATING <──────────── VENTILATING
                              ^        │                            │
                     any danger│       │ ack + 5min all-clear       │ stop cond
                    (from any state)   └────────────────────────────┘
```

| State | Gas | Fans | Registers | Meaning |
|---|---|---|---|---|
| WAITING | off | idle 10% | closed | nothing running |
| ARMED | off | idle 10% | closed | `start` accepted, waiting for live `confirm` |
| WARMING_UP | off (hard-closed) | 0 | closed | sensors powered, waiting out `SENSOR_WARMUP_MS` before gas may flow |
| LEAKING | **on** | 0 | closed | gas flowing, room sealed |
| HOLD | off | 0 | closed | **the measurement phase** — watch propagation undisturbed |
| VENTILATING | off | spec % | spec set | controlled clear-out |
| FULLY_VENTILATING | off | 100% | **all open** | mandatory 5-min purge / danger response |

#### The two-message arm

`start(spec, s, nowMs)` rejects unless: state is `WAITING`, `spec.valid`
(Protocol set it), role is leak-test. On success it copies the spec, stamps
`armedAtMs_`, → `ARMED`. It **only ever arms** — never releases gas.

`confirm(runId, nowMs)` is a **separate live message** the website sends
after showing the operator the interpreted spec. It rejects unless state is
`ARMED`, within `ARM_TIMEOUT_MS` (60 s — else back to `WAITING` with
`ARM_TIMED_OUT`), and `runId` matches. This is the structural defense
against a **replayed retained `start`**: a broker replay at worst re-arms,
and the 60 s timeout disarms it — releasing gas needs the second,
un-retained `confirm`.

#### The warm-up gate — its own state

`confirm()` powers the local sensors on if they were not already
(`localSensorsOn_ = true`, `sensorsOnSinceMs_ = nowMs`), zeroes the
inventory integrator, then enters **`LEAKING` directly if
`sensorsAreWarm(nowMs)`** (sensors already warm — carried over from a prior
run or from WAITING equipment-test), **or `WARMING_UP` otherwise**:

```cpp
enterState(sensorsAreWarm(nowMs) ? KitchenState::LEAKING
                                 : KitchenState::WARMING_UP, nowMs);
```

`sensorsAreWarm()` gates on **elapsed powered time**, never on "did this
call turn them on" — `localSensorsOn_ && (nowMs - sensorsOnSinceMs_) >=
SENSOR_WARMUP_MS`. This closed a real bug: sensors switched on moments
earlier by equipment-test are not warm yet, and an earlier `if
(!localSensorsOn_)` check let that case slip straight into gas flowing onto
blind sensors.

`WARMING_UP`'s only job in `update()` is to wait: once
`sensorsAreWarm(nowMs)`, it resets `lastIntegrationMs_` (don't integrate
flow across the gate) and enters `LEAKING`, which — via `enterState()` —
re-bases `phaseClockFromMs_` to that instant. Stop conditions time from
`phaseClockFromMs_`, **not** `ARMED` or `WARMING_UP` entry. Without that
re-base, a spec asking "leak for 5 s" would deliver **zero gas**: the phase
clock would have started at `ARMED`/entry, the warm-up gate would hold the
valve shut, and `nowMs - phaseClockFromMs_ >= 5000` would already be true
the instant the gate opened. So the clock re-bases to the moment gas
actually starts flowing — `maxDurationMs` measures *gas delivery*, not
*time since armed*.

Gas is **hard-closed by `outputsFor()`** for the whole of `WARMING_UP` — a
state, not a boolean read inside `LEAKING` — precisely so an observer (LED,
MQTT `state` topic, operator) can see "warming up" as a distinct, named
condition instead of a `LEAKING` that mysteriously delivers no gas yet.
`warmupPending()` (`state_ == KitchenState::WARMING_UP`) is kept as a
convenience accessor.

The danger check still runs unconditionally at the top of every `update()`
pass, so a danger firing during `WARMING_UP` cuts to `FULLY_VENTILATING`
exactly as it would from any other state — the gate never delays it.

### 4. `dangerActive()` — the conditions, in order

```
1. any present sensor:  counts >= thresholdCounts        -> LOCAL_SENSOR_THRESHOLD
2. any present+expectedOn sensor: stale                  -> LOCAL_SENSOR_STALE
3. leak-test role AND flowOverLimitSustained             -> FLOW_OVER_LIMIT
4. leak-test role AND deliveredInventory_mL > CAP        -> INVENTORY_CAP_EXCEEDED
5. peerAlarmActive                                       -> PEER_ALARM
6. permitPresent AND !permitValue                        -> PERMIT_DENIED
7. estopPressed                                          -> ESTOP
8. expansionUnhealthy                                    -> EXPANSION_FAULT
9. externalTripActive                                    -> EXTERNAL_TRIP     (reserved, never set)
   else                                                  -> NONE
```

Design points:

- **`>=` everywhere** — "at or above threshold trips". Matches
  `quorumMet()`. These two must not drift apart.
- **Silence != safety** for the *threshold* check (a stale/absent sensor is
  skipped — it cannot prove low concentration), but silence *is* danger for
  the *staleness* check, but **only when `expectedOn`** — a sensor
  deliberately powered off going quiet is expected, not a fault.
- **Permit absence warns, does not trip** — only an explicit
  `"permit":false` trips. A missing safety-permit message must not halt an
  experiment.
- **`EXPANSION_FAULT`**: a missing A0602 makes all 6 H2 current sensors read
  0 counts — indistinguishable from "0% H2". The threshold and quorum checks
  would be blind, so expansion loss is itself danger. Fed in via
  `SensorState.expansionUnhealthy`, set in `Sensors.cpp` from
  `expansionHealthy()`.
- **`isLeakTestRole` guards flow/inventory** — those are leak-test concepts;
  in equipment-test there is no gas delivery to cap.

### 5. `update()` — the once-per-pass sequence

```cpp
OutputRequest KitchenCore::update(const SensorState& s, uint32_t nowMs) {
  bool danger = dangerActive(s, reason);           // (A) DANGER FIRST

  if (danger) {
    enterFullyVentilating(reason, /*requiresAck=*/true, nowMs);
    clearSinceMs_ = 0;                             // danger this pass = not-clear
  } else if (state_ == FULLY_VENTILATING) {
    if (clearSinceMs_ == 0) clearSinceMs_ = nowMs; // start the all-clear clock
  }

  bool equipTest = !selectorIsLeakTest(s);         // (B) selector bookkeeping —
  roleMisflip_     = equipTest && state_ != WAITING;      // only takes effect in
  equipTestActive_ = equipTest && state_ == WAITING;      // WAITING (see switch)

  switch (state_) {                                // (C) per-state advance
    case WAITING:
      if (equipTest) { if (!localSensorsOn_) { localSensorsOn_ = true; sensorsOnSinceMs_ = nowMs; } }
      else if (localSensorsOn_ && nowMs - waitingIdleSinceMs_ >= SENSOR_IDLE_TIMEOUT_MS) {
        localSensorsOn_ = false;
      }
      break;
    case ARMED: /* arm-timeout check */ break;
    case WARMING_UP: /* wait for sensorsAreWarm(), then -> LEAKING */ break;
    case LEAKING: /* integrate inventory, check leakStop */ break;
    // ...HOLD, VENTILATING similarly...
  }

  return outputsFor(nowMs);                        // (D) state -> desired outputs
}
```

**(A) is the whole safety argument.** Danger is evaluated and acted on
*before* the `switch`. Once `enterFullyVentilating()` has moved `state_`,
the `LEAKING`/`HOLD`/`VENTILATING` cases — the only ones that read `spec_` —
are unreachable this pass. State and outputs always agree on the same pass;
there is no cross-pass skew.

**(B) is selector bookkeeping, not a decision by itself.** `equipTest` is
recomputed every pass from the live selector; `roleMisflip_` and
`equipTestActive_` are just derived flags for other code to read
(`roleMisflip_` for logging/LEDs when the selector is wrong outside WAITING;
`equipTestActive_` for `outputsFor()`'s flowmeter-open behaviour in bench
mode). The selector only actually *changes* sensor power inside the
`WAITING` case of the switch — everywhere else a flip is inert (§8 below).

**`clearSinceMs_`** is the "5 continuous minutes clear" tracker. `0` is a
reserved sentinel meaning "not clear right now". Any pass with danger active
resets it to `0`. So `canLeaveFullyVentilating()` requires `nowMs -
clearSinceMs_ >= FULLY_VENT_MIN_HOLD_MS` — and a danger re-firing 4 minutes
into a 5-minute hold restarts the clock. (`nowMs == 0` is bumped to `1` so
the sentinel is never a real timestamp.)

### 6. `enterFullyVentilating()` — one entry point, PROMOTES never DEMOTES

Every route into the purge — danger, routine end-of-run, operator `stop()`
— goes through here.

```cpp
void enterFullyVentilating(DangerReason reason, bool requiresAck, uint32_t nowMs) {
  bool wasAlready = (state_ == FULLY_VENTILATING);
  if (!wasAlready) {
    enterState(FULLY_VENTILATING, nowMs);
    ackRequired_  = requiresAck;
    acked_        = false;
    clearSinceMs_ = requiresAck ? 0 : (nowMs == 0 ? 1 : nowMs);
  } else if (requiresAck && !ackRequired_) {
    ackRequired_  = true;    // PROMOTE a routine purge to needs-ack
    acked_        = false;
    clearSinceMs_ = 0;
  }
  reason_ = reason;
}
```

- `requiresAck` **promotes** but never demotes. A danger firing
  mid-routine-purge upgrades it to "needs ack"; nothing but *leaving* the
  state clears `ackRequired_`.
- `acked_` is only ever cleared **on the way out**
  (`canLeaveFullyVentilating` path). Re-entering while already latched
  cannot discard an ack a human already gave — otherwise a flickering
  sensor would keep throwing away the operator's acknowledgment.
- **The `clearSinceMs_` init asymmetry**: a `requiresAck` entry has an
  active condition *this pass*, so the clock is not running yet — it starts
  on the next `update()` with the condition gone. A routine entry
  (`stop()`, end-of-run) has no condition — it is clear from the instant it
  enters, so the clock starts *now*. Without this, a routine purge's
  5-minute hold was measured one `update()` call short.

### 7. `humanAck()` — two indistinguishable callers

The physical USER button and the MQTT `ack` command call the identical
function. The button path **must work with no network** — a physical
acknowledgment matters precisely when the network is the broken thing (same
principle as the e-stop). It just sets `acked_ = true`;
`canLeaveFullyVentilating()` still enforces the full 5-minute all-clear hold
on top. The ack is not a shortcut past the hold — acking early still
requires the full continuous-clear time.

### 8. `OPERATOR_ABORT` — reserved, not currently raised

`DangerReason::OPERATOR_ABORT` still exists in the enum — kept as a stable
wire value for the alarm payload — but nothing in `KitchenCore.cpp` raises
it. The header is explicit:

```cpp
// Reserved: an operator abort that requires a human ack to clear. Not
// currently raised (stop() is routine/no-ack; a mid-leak selector flip is
// inert). Kept as a stable wire value for the alarm payload.
OPERATOR_ABORT,
```

A mid-leak selector flip away from leak-test is **inert**: `roleMisflip_` is
recorded each pass purely so the LED layer can blink the real run-state at
whoever flipped it, and so `kitchen.ino` can log the edge — the run itself
is unaffected ("A selector flip mid-leak is inert... Use stop() to abort a
live run."). Aborting a live run requires the explicit in-band `stop()`
command, which is routine/no-ack (§6/§7) — there is no distinct
human-must-ack abort path for a physical selector flip. If this is ever
reinstated, it would again not belong in `dangerActive()`: a one-time edge
has no ongoing condition for the danger check to keep finding true.

### 9. Sensor power — LOCAL vs REMOTE (split 2026-09-07)

Was lockstep (a single `sensorsOn_` drove both) before 2026-09-07; now two
independent signals:

- **`localSensorsOn_`** (member) — the A0602/base H2 sensors. Turned on by
  `confirm()` (gating the leak run into `WARMING_UP` until warm, §3) **or**
  immediately whenever the selector is in equipment-test while in `WAITING`
  (no gate — the `WAITING` case of the switch in §5). Off after
  `SENSOR_IDLE_TIMEOUT_MS` (10 min) idle in `WAITING`.
- **`remoteOn()`** (derived, not stored) — the CM7 DAQ. Gates on **state
  alone**: `localSensorsOn_ && state in {WARMING_UP, LEAKING, HOLD,
  VENTILATING, FULLY_VENTILATING}`. Never equipment-test, never
  WAITING/ARMED. It no longer reads role at all — a leak run can only
  *start* in leak-test role (`start()` rejects otherwise), and a selector
  flip mid-run is now inert (§8), so by the time the machine is in one of
  these states the role can no longer change what `remoteOn()` should
  return. `WARMING_UP` is included because the remote DAQ has its own
  `REMOTE_SENSOR_WARMUP_MS` and must start coming up alongside the local
  sensors, not only once gas starts flowing in `LEAKING`.

`outputsFor()`: `out.localSensorsOn = localSensorsOn_; out.remoteSensorsOn =
remoteOn();`

One source of truth (`localSensorsOn_` + role + state) instead of two bools
to keep synced.

Warm-up numbers: LOCAL H2 sensors `SENSOR_WARMUP_MS` = 70 s (gates entry
into `LEAKING`). REMOTE DAQ `REMOTE_SENSOR_WARMUP_MS` ≈ 10 s (receive MQTT →
close its relay → ~1 s sensor warm-up). `kitchen.ino` runs a warn-only
`REMOTE_SENSOR_LIVENESS_MS` (10 s) check: on the remote-power OFF→ON edge,
if no `DataAcquisition/Kitchen/{id}/#` traffic is seen within the window, it
logs one ERROR line. Warn only — the local sensors and the danger path do
not depend on the DAQ.

### 10. `outputsFor()` — the pure state→output map

`const`, reads `state_` and (for `LEAKING`/`VENTILATING` only) `spec_`. The
`FULLY_VENTILATING` block reads **nothing** from `spec_` — fixed values.
`VENTILATING` re-applies `clampFanSpeedPct()` even though Protocol already
clamped at parse time — a ceiling that holds even if the parse-time clamp
ever drifts, at zero cost. `gasMayBePresent = (state_ != WAITING)` — the
breathing-lamp flag, pure, state-based.

### 11. `RegisterSequencer` (folded in 2026-09-07)

Pure helper, now living at the top of `KitchenCore.h` / bottom of `.cpp`.
Maps `(RegisterSet desired, nowMs) -> CoilStates`. The one rule it encodes:
**opening the inlet is deferred `INLET_OPEN_DELAY_MS` when it opens
alongside another register** (so exhaust establishes airflow first);
closing is simultaneous, no delay; inlet-alone or inlet-already-open is
immediate; a request that changes again before the deadline *supersedes*
the pending open rather than firing it late. It holds one deadline field
and takes `nowMs` as a parameter — fake-clock testable. See Part 1's
"Inlet opening delay" for the design rationale.

## Protocol — the MQTT/JSON boundary

### 1. What it is for

The **only** file that turns wire bytes into a `RunSpec` and turns
`RunSpec`/state into wire bytes. `KitchenCore` never sees JSON; `Comms`
never sees a `RunSpec`. It uses ArduinoJson (`StaticJsonDocument`,
stack-allocated, no heap) and includes `<stddef.h>`.

Responsibilities:

- parse + validate + clamp `start` specs (sets `RunSpec.valid`)
- the quorum threshold **%↔counts** conversion — *firmware-owned*
- `holdStop` fallback to `HOLD_MAX_DURATION_MS` when the website omits it
- **rejecting** `maxInventory_mL` on hold/vent stops (not silently ignoring)
- building the `state` / `ack` / `alarm` / `sensors/power` payloads
- dropping retained `cmd` messages

### 2. `protocolParseCommand()` — inbound

```cpp
ParsedCommand protocolParseCommand(const char* json, bool retained,
                                   float* requestedPctOut, uint16_t* interpretedCountsOut);
```

**First line of defense**: `if (retained) return pc;` — kind stays `NONE`.
A retained `cmd` is a broker replay; never act on it. (`kitchen.ino`
currently passes `retained = false` always because PubSubClient exposes no
retained flag — the real protection is structural in `KitchenCore`, this is
belt-and-braces.)

Then `deserializeJson` — failure → `CmdKind::BAD_JSON`. Then dispatch on
`doc["cmd"]`:

| `cmd` | → | carries |
|---|---|---|
| `"stop"` | `STOP` | - |
| `"ack"` | `ACK` | - |
| `"confirm"` | `CONFIRM` | `runId` |
| `"start"` | `START` | full spec (parsed below) |
| anything else | `BAD_JSON` | - |

#### The `start` spec parse — validate + clamp

```cpp
gasSetpointPct = clampFanSpeedPct(spec["gasSetpointPct"] | 0.0f);   // 0..VENT_SPEED_MAX_PCT
```

Then three `parseStop()` calls, one per phase:

```cpp
parseStop(spec["leakStop"], ..., /*allowInventory=*/true,  ...);   // gas flowing here
parseStop(spec["holdStop"], ..., /*allowInventory=*/false, ...);   // REJECT inventory
parseStop(spec["ventStop"], ..., /*allowInventory=*/false, ...);   // REJECT inventory
```

`parseStop()` reads `maxDurationMs` (negative → reject), `maxInventory_mL`
(negative → reject; **positive on a phase where `allowInventory` is false →
reject**, not ignore — "a spec asking for something impossible is a spec
whose author misunderstood the phase, and a silent no-op hides that"), and
`sensorQuorum` (`quorumCount` 0-255, and if `>0` a `thresholdPct` is
required and converted via `protocolPctToCounts`).

#### The `holdStop` fallback

```cpp
if (pc.spec.holdStop.maxDurationMs == 0)
  pc.spec.holdStop.maxDurationMs = HOLD_MAX_DURATION_MS;      // omitted => ceiling, NOT zero
else if (... > HOLD_MAX_DURATION_MS)
  pc.spec.holdStop.maxDurationMs = HOLD_MAX_DURATION_MS;      // clamp
```

HOLD is the one phase with gas already delivered and fans **off**. An
unbounded or zero-duration HOLD must not be reachable through a
partial/malformed spec — "absent" means "the maximum safe hold", not "skip
the measurement". `leakStop`/`ventStop` omitting a duration is fine (== no
cap), because those phases have other exits.

Finally `pc.spec.valid = true`, and the echo out-params: for the
**leak-phase** quorum (the one that cuts gas), it writes back both the raw
`%` the website asked and the `counts` it interpreted — so a calibration
mismatch is visible from the browser.

### 3. The %↔counts conversion — firmware-owned

```cpp
uint16_t protocolPctToCounts(float pct) {
  float mA = 4.0f;
  if (SENSOR_FULLSCALE_PCT > 0.0f)
    mA = 4.0f + (pct / SENSOR_FULLSCALE_PCT) * 16.0f;         // pct=FS -> 20 mA
  float c = ADC_COUNTS_AT_4MA + (mA - 4.0f) * ADC_COUNTS_PER_MA;
  clamp to [0, ADC_MAX_COUNTS];
  return (uint16_t)(c + 0.5f);
}
```

The website sends the quorum threshold as **"% H2 by volume"**. But this
condition **cuts gas**, so the website cannot be the authority on what "4%"
means in ADC counts — the firmware owns the mapping, using the *same* 4-20
mA scale constants as `Sensors.cpp`. With placeholder scales
(`ADC_COUNTS_PER_MA == 0`) it returns the live-zero for every % — harmless
on the bench, and the `#error` guard blocks a real firmware build until the
constants are measured. `protocolCountsToPct()` is the inverse, returning 0
if the scales are still placeholder (avoids a divide-by-zero).

### 4. Outbound builders

Each writes into a caller buffer, returns bytes written or `0` on overflow
(`n == 0 || n >= cap`).

#### `protocolBuildState()` → retained `KitchenControl/{id}/state`

`{state, role, elapsedMs, inventory_mL, ackRequired, acked, reason,
sensorsOn}`. `StaticJsonDocument<512>`. `reason` and `state` go through
name-mapping `switch`es (`protocolReasonName`, `stateName`).

`kitchen.ino` gates the *call* to this builder: a pre-check on the discrete
fields (state/role/ack/reason/sensorsOn) publishes instantly on any change;
otherwise a 1 Hz heartbeat refreshes `elapsedMs`/`inventory_mL`; a final
`strcmp` guard stops an identical retained payload from being re-sent.
`onMqttConnect()` sets a force flag so a reconnect re-emits immediately.

#### `protocolBuildAck()` → non-retained `KitchenControl/{id}/ack`

The **interpreted spec echoed back** — durations after clamping,
`ventRegisters`, and `quorumInterpreted: {requestedPct, interpretedCounts}`
— or a `rejection` string if `!accepted`. `StaticJsonDocument<768>` (biggest
payload). This is what the browser's review screen shows before the
operator sends `confirm`: "the PLC understood your request as *this*".
Non-retained — a one-time reply to a specific command, not state. Deferred
out of the MQTT callback: `onMqttMessage()` stashes the result and sets
`_ackPending`; `flushPendingAck()` publishes it on the next `loop()` pass
(PubSubClient is not reentrant — cannot publish from inside its receive
callback).

#### `protocolBuildAlarm()` → retained `status/{Exp}/{id}/alarm/{lab}/hydrogen`

`{danger, active, description, runId}` — both `danger` and `active` carry
the same bool because different consumers key on different names. Published
on the danger-latch **edge**, not continuously.

#### `protocolBuildSensorsPower()` → retained `KitchenControl/{id}/sensors/power`

Just `{on: bool}`. Commands the remote CM7 DAQ. `on` = `remoteSensorsOn`
(leak-test leak run only), not the same as local power.

### 5. Why this file exists as a boundary

- `KitchenCore` stays desktop-testable — no ArduinoJson, no wire format.
- The **closed set** of stop-condition primitives (`RunSpec.h`) means the
  website can only *compose* from `maxDuration` / `maxInventory` /
  `sensorQuorum` — it can never inject arbitrary logic. Protocol is where
  that closed set is enforced against the incoming JSON.
- Every gas-authority decision (quorum %→counts, spec clamps, the
  retained-cmd drop) is concentrated here, at the one point wire data
  enters — not scattered through the control code.

---

# Part 3 — Legacy reference: pulse-flowmeter → pump relay control

From `Old/kitchenold.ino`, the pre-2026 threshold-alarm monitor, retired and
deleted (it was never committed to git, so this section is the only
surviving record of its flow-driven pump control). The new `kitchen/`
firmware measures flow differently (see *How the new design differs*
below), so this is a design reference, not code to port verbatim.

## What it did

A **water flowmeter drove a relay** (`RELAY4`) that switched the pump. The
sketch did not command the pump on a timer or from the network — it
inferred "water is actually moving" from flowmeter pulses and closed the
relay only while that held. Flow was the *evidence*, not the *command*.

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

## The three design ideas worth keeping

These are the parts that are about *judgement*, not about the old hardware:

1. **A minimum-volume gate before acting.** `FLOW_MIN_ML` (10 pulses = 10
   mL) means a few stray pulses — vibration, a bubble, contact bounce, a
   brief pressure transient — never start the pump. Flow has to be
   *sustained* to count. There is a deliberate asymmetry here: starting
   requires 10 mL of evidence, but stopping requires only silence.
2. **Asymmetric start/stop conditions.** Turning ON needs `pulsing &&
   sessionCount >= FLOW_MIN_ML`. Turning OFF needs only `!pulsing` — one
   second of silence (`FLOW_TIMEOUT`) drops the relay, with no volume
   threshold to clear. **Fail-safe direction: hard to start, easy to
   stop.**
3. **Session counter reset on every stop.** `flowSessionCount` zeroes when
   flow stops *and* when a sub-threshold burst times out, so the 10 mL
   evidence must be accumulated fresh each time. Ten separate 1 mL blips a
   minute apart never add up to a start — only ten within one continuous
   session do.

Plus two implementation details that matter on any pulse input:

- **ISR debounce** at 5 ms between accepted pulses, filtering contact
  bounce that would otherwise inflate the volume count.
- **Interrupt-safe reads**: the `volatile` counters are copied inside
  `noInterrupts()`/`interrupts()` before use, so a pulse landing mid-read
  cannot tear a value.

## How the new design differs

| | `Old/kitchenold.ino` | `kitchen/` (current) |
|---|---|---|
| Flow signal | **pulse**, 1 pulse = 1 mL, ISR-counted | **analog** 0–10 V rate on base `A1` |
| Volume | counted directly (each pulse *is* a mL) | **integrated** from the rate over time |
| Medium | water (pump control) | hydrogen (gas delivery) |
| Actuation | relay closes *because* flow was detected | gas relay + setpoint DAC command flow; flow feedback *verifies* it |
| Constants | `FLOW_MIN_ML`, `FLOW_TIMEOUT` | `FLOW_COUNTS_PER_VOLT`, `FLOW_ML_PER_SEC_AT_10V`, `FLOW_LIMIT_COUNTS`, `INVENTORY_CAP_ML` |

The causal direction is **reversed**: the old sketch let measured flow
decide the relay; the new firmware commands flow and uses the measurement
as a safety check (`FLOW_LIMIT_COUNTS` sustained over
`FLOW_LIMIT_SUSTAINED_MS`, and `INVENTORY_CAP_ML` on the integrated total).

**If a water pump is ever re-added to the kitchen rig**, this pulse-driven
pattern — minimum-volume gate, asymmetric start/stop, per-session reset —
is the behaviour to reimplement. It would sit in `Outputs`/`Sensors` with
the decision in `KitchenCore`, and would need its own relay channel (the
D1608E's 8 are fully allocated today).

**A note on the sustain-window parallel.** `Sensors.cpp` already uses the
same "must hold continuously before it counts" shape for
`flowOverLimitSustained` (`FLOW_LIMIT_SUSTAINED_MS`). That is the modern
analogue of `FLOW_MIN_ML` — evidence has to persist before it drives an
action.

---

# Part 4 — Future work: batched 10 ms sensor publishing

**Status: deferred, not implemented.** Fully-researched design, kept so it
can be picked up later without redoing the investigation.

## Why this exists

The kitchen PLC can *sample* its 6 local H2 sensors at 10 ms cheaply (one
I2C transaction per tick — see the implemented
`expansionRefreshAnalogInputs()` work). What it cannot cheaply do is
*publish* at that rate.

Publishing one MQTT message per sample per sensor at 10 ms means **~600
blocking `mqttClient.publish()` calls per second** on `EthernetClient`.
`PubSubClient::publish()` blocks on the TCP write; a single retransmit or a
slow broker stalls the loop that also runs the safety interlock. That is
the reason batching exists as an idea, and the reason the near-term
implementation decimates the publish rate instead.

Batching gets 10 ms **resolution** into the historian at ~5 msg/s/sensor.

## The historian accepts batches — DONE

`DataAcquisition/dashboard/app/ingest.py` accepts `{"samples":[...]}`:
`_on_message` dispatches each entry through `_process_reading()`, so a batch
behaves exactly like N single messages (dedupe, routing, recording gate,
SSE, DB enqueue and derived tick all per sample). `_MAX_BATCH_SAMPLES = 100`
caps one message, and `app/state.py::_SEEN_TS_WINDOW` was raised 32 → 256 so
a batch cannot evict its own dedup entries. Equivalence and backward
compatibility are covered by
`DataAcquisition/tests/recording/test_ingest_batch.py`.

A payload with no `samples` key takes the original path unchanged, so
existing publishers (CM7 firmware, Turbine PLCs, `cm7_simulator.py`) are
unaffected — verified against all three legacy wire shapes.

## Second constraint: timestamps must be real epoch-ms

`ingest.py:192`:
```python
if isinstance(fw_ts_ms, (int, float)) and fw_ts_ms >= _MIN_REAL_EPOCH_MS:  # 1_500_000_000_000
    ts_ms = int(fw_ts_ms)
else:
    ts_ms = int(wall_ts * 1000)   # server receipt time
```
`millis()`-based timestamps are below that floor, so the historian
**replaces them with receipt time** — collapsing 10 ms spacing into network
jitter. Batching is pointless without a real clock.

## Third constraint: de-dup is keyed on `(key, ts_ms)`

`dashboard/app/state.py::_is_duplicate` keeps a per-key deque of recently
seen `ts_ms`. Every sample in a batch must carry a **distinct** `ts`. A
batch that stamps all samples with one timestamp collapses to a single
stored row. The 10 ms cadence gives distinct millisecond values naturally —
but the batch builder must stamp each sample at its own sample time, not at
drain time. Also check `_SEEN_TS_WINDOW` is larger than one batch, or a
batch evicts its own entries.

## Design

### Wire format

One topic per sensor (matching the existing tree), batched samples:

```
topic: DataAcquisition/Kitchen/mainBoard/<sensorName>      (non-retained)

{"pin":100,"type":"current",
 "samples":[{"raw_ma":12.345,"ts":1737000000801},
            {"raw_ma":12.350,"ts":1737000000811},
            ...]}
```

- `pin` and `type` stay **top-level** — they are per-sensor constants. Only
  varying fields go per-sample.
- **Non-retained.** Retaining a 5 Hz stream would hammer broker disk.
- Build with `snprintf`, **not ArduinoJson**. `Protocol.cpp` already builds
  payloads this way, and the existing `publishState()` pre-gate exists
  precisely because per-pass ArduinoJson builds were too expensive.
- Size: 20 samples × ~40 B ≈ 800 B, within `MQTT_MAX_PAYLOAD` (2048). The
  builder must return 0 on overflow and the caller must **split the batch,
  never truncate**.

### Firmware pieces

`kitchen/Protocol.h/.cpp`:
```cpp
size_t protocolBuildSensorBatch(char* out, size_t cap,
                                int sensorIdx, int encodedPin,
                                const H2Sample* samples, int count);
```

`kitchen/SensorStream.h/.cpp` — ring buffer feeding the batch:
```cpp
struct H2Sample { uint64_t tsMs; uint16_t counts[...]; float mA[...]; };
void sensorStreamBegin();
void sensorStreamTick(uint32_t nowMs);   // 10 ms sampler
bool sensorStreamDrain(...);             // hand batches to the publisher
```
- Ring of 32 samples = 320 ms at 10 ms, comfortably more than one 200 ms
  publish window.
- 8 + 6×2 + 6×4 = 44 B per sample; 32 of them ≈ 1.4 KB. Acceptable.
- Overwrite-oldest on overflow **and count drops** — silent data loss must
  be visible.
- Store the **calibrated mA float** alongside counts. `Sensors.cpp` already
  applies `SENSOR_CALIBRATION_OFFSET_MA` at the read boundary; do **not**
  invert counts→mA at publish time.

Settings:
```c
#define SENSOR_SAMPLE_INTERVAL_MS    10UL
#define SENSOR_PUBLISH_INTERVAL_MS   200UL
#define H2_STREAM_RING_SAMPLES       32
```

### Timestamps — NTP is required, and the CM7's version does NOT work here

`DataAcquisition/CM7/Comms.cpp:204` `syncClock()` calls `WiFi.getTime()` and
is wrapped in `#ifndef USE_ETHERNET` — **it is a no-op on Ethernet.** The
kitchen is Ethernet-only, so it cannot be copied.

Replacement: `NTPClient` (present at
`~/Documents/Arduino/libraries/NTPClient`, takes any `UDP&`) +
`EthernetUDP`. API: `begin()`, `update()`, `forceUpdate()`, `getEpochTime()`.

Reuse the CM7's proven anchoring shape (`CM7/Comms.cpp:229 currentTsMs()`):
```cpp
_ntpEpoch = ntp.getEpochTime(); _ntpMs = millis();
// commsNowMs() = _ntpEpoch*1000 + (millis() - _ntpMs)
```
- Bounded sync attempt in `setup()`; non-blocking `ntp.update()`
  thereafter. **NTP must never block the control loop.**
- Explicit fallback: if NTP never succeeds, return `millis()`, let the
  historian substitute receipt time, and **log a warning once** so degraded
  resolution is visible rather than silent.
- NTP on a lab network may be firewalled — this fallback is the whole
  reason it's explicit.

## Consumer changes required (do not skip)

### `DataAcquisition/dashboard/app/ingest.py` — DONE

The wire format the historian now accepts:

```json
{"pin":100,"type":"current","samples":[{"raw_ma":12.34,"ts":1737000000000},
                                       {"raw_ma":12.36,"ts":1737000000010}]}
```

Top-level keys are shared across the batch; per-sample keys override them.
Max 100 samples per message.

Settled while implementing:
- `_count(_RECEIVED)` moved inside `_process_reading`, so counters stay
  per-reading.
- `_SEEN_TS_WINDOW` 32 → 256 (> `_MAX_BATCH_SAMPLES`), asserted by a test.
- **Throughput is a non-issue.** 600 rows/s against `db_writer.py`'s
  benchmarked 13,400–16,900 rows/s — ~4% of capacity.
- A non-numeric raw field used to raise out of `_on_message` and be
  swallowed by paho; it is now caught and logged, so one bad sample costs
  only that sample.

Still open, deliberately: `derived.py:~117` debounces derived-sensor emits
on the **reading clock**, so a batch of 10 ms-spaced samples gives `elapsed
= 10 ms` for all but the first. Correct for the default 500 ms interval,
but **re-verify before making a batched device an input to a
short-interval derived sensor.** No existing derived sensor has a batched
input today.

### `Controlo-Cozinha/webapp/app/mqtt.py`

`_handle_sensor_sample` must handle a `samples[]` batch by taking the
**last** sample — it only stores a latest-value-per-sensor view, so newest
wins. Apply the conversion (`app/conversion.py`) to that one sample only.

(The non-batch format fix — reading `raw_ma`/`raw_v`/`ts` instead of the
never-published `value`/`ts_ms` — is **done**, along with the mA→%v/v
conversion that uses DataAcquisition's stored calibration.)

## Verification when this is picked up

- **Equivalence test:** a batch of N samples must produce exactly the same
  N records as N single messages. That is the one test that matters most.
- `protocolBuildSensorBatch` unit test: exact expected JSON for 2–3 samples;
  returns 0 (not truncated output) when `cap` is too small.
- Ring buffer test: FIFO order, overflow overwrites oldest AND increments
  the drop counter, drained timestamps strictly increasing.
- On hardware: `mosquitto_sub -t 'DataAcquisition/Kitchen/mainBoard/#' -v`
  — confirm ~5 msg/s/sensor, `ts` 10 ms apart and **13-digit real epoch**,
  not small millis.
- Confirm the DAQ overload banner does not fire at 600 rows/s.

## Related ceiling (not solved by batching)

`Controller::wait_for_device_answer`
(`Arduino_Opta_Blueprint/src/OptaController.cpp:1075`) is a **busy-wait
spin with a 50 ms timeout**. One unresponsive expansion stalls a 10 ms loop
for five full periods. Batching does not help this; it needs its own
decision about how that failure should behave.
