# kitchen/ — hydrogen leak-test PLC firmware

Arduino PLC firmware for a hydrogen leak-propagation kitchen. The design
splits **pure, desktop-testable control logic** (no `Arduino.h`) from the
hardware shim, so the safety-relevant code can be compiled and tested with
`g++` on a laptop.

## Files

**Pure core — no Arduino headers, `g++`-testable:**

| File | Role |
|---|---|
| [`RunSpec.h`](RunSpec.h) | The shape of a validated, clamped run request. Data only. |
| [`Kitchen_Settings.h`](Kitchen_Settings.h) | Firmware constants: thresholds, timings, ceilings, and (below the `#error` guard) the hardware pin/channel map. |
| [`KitchenCore.h`](KitchenCore.h) / [`.cpp`](KitchenCore.cpp) | The entire control path: one state machine that reads `SensorState` and returns an `OutputRequest`. Also holds `RegisterSequencer` (desired vent-register set → coil states, applying the inlet-open delay) — folded in from its own file since it serves only this. |
| [`test/test_safety.cpp`](test/test_safety.cpp) | Native desktop test suite for the pure core. No Arduino, no hardware. |

**Arduino shim — hardware + network. Compiled by the Arduino IDE, not `g++`:**

| File | Role |
|---|---|
| [`kitchen.ino`](kitchen.ino) | `setup()`/`loop()`. Wires `sensorsPoll → core.update → outputsDrive` and the MQTT publish/subscribe. Holds no control logic. |
| [`Sensors.h`](Sensors.h) / [`.cpp`](Sensors.cpp) | Builds `SensorState`. Owns the one calibrated-read boundary (per-sensor mA offset, mA/V → counts), staleness timers, the flow-over-limit sustain window, selector + e-stop reads. |
| [`Outputs.h`](Outputs.h) / [`.cpp`](Outputs.cpp) | **The only pin writer.** Turns an `OutputRequest` into relay + DAC + PWM + LED writes; drives the `RegisterSequencer` (defined in `KitchenCore.h`). |
| [`Protocol.h`](Protocol.h) / [`.cpp`](Protocol.cpp) | The MQTT/JSON boundary. Parse + validate + clamp of `start` specs (sets `RunSpec.valid`); the quorum `%`↔counts conversion; `state`/`ack`/`alarm`/`sensors-power` payload builders. |
| [`Expansion.h`](Expansion.h) / [`.cpp`](Expansion.cpp) | A0602 ADC reads (from the CM7 DAQ) **plus** added: O1/O2 voltage-DAC output and D1608E relay control. |
| [`Comms.h`](Comms.h) / [`.cpp`](Comms.cpp) | Ethernet + MQTT, from the CM7 DAQ. Three additive hooks: `retain` arg on `mqttPublish`, `commsSetSubscriptions`, `commsSetLwt`. Also holds the run-mode diagnostics (`RunMode`, `logPublish`/`logPrintf`, PROFILING) — folded in from `Log.*` since it publishes via `mqttPublish`. |
| `Kitchen_Secrets.h` | MAC + broker credentials. **Gitignored** — exists only on the build machine; recreate by hand if lost. |

**Deferred** (see `docs/implementation-plan.md` open items): per-zone peer-alarm
tracking table (flat `peerAlarmActive`/`peerAlarmStale` booleans for now),
D1608E discrete button inputs, the CM7-side `sensors/power` subscriber, the
`config/set` per-sensor threshold table (accepted, not yet applied).

## Dataflow — once per loop pass

```
Sensors.cpp  ──> SensorState ──> KitchenCore::update() ──> OutputRequest ──> Outputs::drive() ──> pins
(hardware)                       (the audit target)                          (only pin writer)

Protocol.cpp ──> RunSpec ──────> KitchenCore::start() / confirm() / stop() / humanAck()
(MQTT / JSON)
```

`KitchenCore` only ever **reads** a `RunSpec`; it never mutates one.
`OutputRequest` is a desired **end state**, not a sequence of actions —
actuation ordering (the inlet delay) lives in `RegisterSequencer`, so it
applies identically to spec-driven and danger-forced transitions.

## The state machine

```
WAITING ─start()─> ARMED ─confirm()─> LEAKING ──> HOLD ──> VENTILATING ──> FULLY_VENTILATING ──> WAITING
   ^                  │ (ARM_TIMEOUT_MS)                                          ^
   └──────────────────┘                                                          │
                          danger / operator abort / stop(), from ANY state ──────┘
```

- **WAITING** — idle. Fans at `VENT_SPEED_IDLE_PCT`, no gas. Sensors power off after `SENSOR_IDLE_TIMEOUT_MS`.
- **ARMED** — spec accepted, awaiting `confirm()`. Reverts to WAITING after `ARM_TIMEOUT_MS`.
- **LEAKING** — gas flows at `spec.gasSetpointPct` after a `SENSOR_WARMUP_MS` gate. Ends on `spec.leakStop`.
- **HOLD** — gas off, fans off; propagation measured undisturbed. Ends on `spec.holdStop`. A real duration — an omitted `holdStop` falls back to `HOLD_MAX_DURATION_MS`, **not** zero.
- **VENTILATING** — `spec.ventRegisters` open, fans at `spec.fanSpeedPct` (clamped). Ends on `spec.ventStop`, into the mandatory purge.
- **FULLY_VENTILATING** — all registers open, fans 100%, gas off, `spec_` not read at all. Exits only via `canLeaveFullyVentilating()`: 5 continuous minutes all-clear (`FULLY_VENT_MIN_HOLD_MS`), plus a `humanAck()` if the entry required one.

## The safety argument

Every `update()` pass:

1. **`dangerActive()` runs FIRST.** It is a `static` function of `SensorState`
   alone and **never reads `spec_`**. If it returns true, the state moves to
   `FULLY_VENTILATING` before any per-state logic runs.
2. The `switch (state_)` then advances whatever state that left us in.
3. `outputsFor()` produces the `OutputRequest`.

Because danger is a **transition evaluated first**, the LEAKING / HOLD /
VENTILATING branches — the only code that reads `spec_` — are unreachable
once danger has fired. Nothing the website sends can open the gas valve when
the safety layer says no.

**Audit target:** `dangerActive()` + the `FULLY_VENTILATING` case of
`outputsFor()`. That is the entire safety-relevant surface.

**Invariants enforced by review, not the compiler:**
- `Outputs::drive()` is the only caller of `digitalWrite` / `analogWrite` on control pins.
- `dangerActive()` is the only place danger conditions are evaluated.
- The `FULLY_VENTILATING` output block never reads `spec_`.

### Danger conditions (`DangerReason`)

Local sensor at/above threshold · stale sensor that is `expectedOn` · flow
over limit (leak role) · inventory cap exceeded (leak role) · peer alarm ·
permit explicitly `false` (*absence* only warns) · e-stop · external trip.

`OPERATOR_ABORT` is **not** a `dangerActive()` condition — it is latched
directly by the LEAKING branch when the role selector is physically flipped
away from leak-test mid-run. It requires a human ack.

## Units and thresholds

- **Everything inside the core is raw ADC counts.** Conversion happens only at
  the two boundaries: `Sensors.cpp` (inbound) and `Protocol.cpp` (MQTT). The
  website sends %; firmware owns the counts↔% mapping because these conditions
  cut gas.
- **All comparisons are AT-OR-ABOVE (`>=`)** — a reading exactly at threshold trips.
- Effective danger threshold = `max(firmware_min, website_value)`. The website
  can only make a sensor *more* sensitive, never less.
- Two **independent** analog scales in `Kitchen_Settings.h` — do not cross them:
  mA constants are the A0602 4–20 mA H2 sensors; volt constants are the
  base-board 0–10 V flow feedback (different ADC resolution).

## Two subtle mechanisms

- **`phaseClockFromMs_`** — stop conditions time from this, not from state
  entry. For HOLD/VENTILATING they are equal; for LEAKING it re-bases to the
  instant the warm-up gate releases (gas actually flowing). Without it a
  "leak for 5 s" spec delivers zero gas — the 30 s gate outlasts the phase.
- **`ackRequired_` promotes, never demotes** — a danger firing during a routine
  purge upgrades it to needing an ack; only *leaving* `FULLY_VENTILATING`
  clears it. `acked_` is cleared only on the way out, so a flickering sensor
  cannot discard an ack already given. `clearSinceMs_ == 0` means "not clear";
  it resets on every pass a condition is active, so "5 min clear" means five
  *continuous* minutes.

## Building the tests

```sh
export PATH="$PATH:/c/mingw64/bin"
g++ -std=c++17 -DKITCHEN_ALLOW_PLACEHOLDER_SCALES -I kitchen \
    kitchen/test/test_safety.cpp kitchen/KitchenCore.cpp -o test_safety \
    && ./test_safety
```

`KITCHEN_ALLOW_PLACEHOLDER_SCALES` is required until the five analog-scale
constants in `Kitchen_Settings.h` are measured on the bench. The tests drive
counts directly and never exercise a physical-unit conversion, so placeholder
scales are harmless here — but a **firmware** build with any of them still a
placeholder is refused at compile time by the `#error` guard (see the
"BLOCKING BEFORE HYDROGEN" markers).

## Wiring (what the shim assumes)

The plan's hardware map, resolved to encoded pins in `Kitchen_Settings.h`. The
encoding: base pins are `0..7` → `A0..A7`; an expansion channel is
`100*(expIdx+1) + ch` (so `exp0 ch2 = 102`). `Expansion.h` takes these directly.

| Signal | Where | Notes |
|---|---|---|
| H2 sensors 1–6 | A0602 **#0** I1–I6 (`OA_CH_0,1,2,3,5,6`) | 4–20 mA current ADC |
| H2 sensors 7–11 | base `A0`, `A4`–`A7` | 0–10 V voltage ADC (spare slots) |
| Flow feedback | base `A1` | 0–10 V, integrated to inventory |
| Role selector | base `A2` | ≤2.5 V leak-test, >2.5 V equipment-test |
| E-stop | base `A3` | pressed = HIGH (invert in `Sensors.cpp` if the real button is active-low) |
| Fan speed setpoint | A0602 #0 **O1** (`OA_CH_4`) | 0–10 V voltage DAC |
| Flowmeter setpoint | A0602 #0 **O2** (`OA_CH_7`) | 0–10 V voltage DAC — driven to 0 V is the 2nd independent gas cut |
| Vent register coils ×6 | D1608E **#1** ch 0–5 | central/exhaust/inlet × open/close |
| Gas relay | D1608E #1 ch 6 | closed = gas may flow (1st gas cut) |
| Alarm relay | D1608E #1 ch 7 | beacon/siren |
| Gas-may-be-present lamp | A0602 #0 **PWM** (`OA_CH_8`) | breathing LED via a logic-level MOSFET on the 24 V rail; lit in every state but WAITING |
| Status LEDs D0–D3 | base board | one 2+2 word — see "Status LEDs" below; `Outputs.cpp` is the sole writer |

Two deviations from the plan's prose, both noted at the `#define`s:

- **8 relays, not 9.** The D1608E has 8. The plan's separate `FLOWMETER_CUT`
  relay is dropped — the flowmeter is cut by driving its setpoint DAC to 0 V,
  which is the second independent gas cut anyway. A dedicated cut relay, if
  ever wired, goes on a second D1608E.
- **No fan on/off relay.** The fan contactor follows the speed DAC (0 V = off).
  Add `RELAY_FAN` and drive it from `req.fanSpeedPct > 0` if one is wired later.

`expansionSetRelayExpansion(1)` in `outputsBegin()` tells `Expansion.cpp` which
detected expansion index is the relay board. If the physical order differs
(D1608E enumerates before the A0602), change that index and the `EXP_ENC(...)`
expansion indices in `Kitchen_Settings.h` together.

`expansionBegin()` **blocks at boot** until both expansions are present with
the right types (A0602 @0, D1608E @1), printing `found N/2`. A missing A0602
would make every H2 current sensor read 0 counts, so booting past it is
refused. At runtime, a lost expansion becomes an `EXPANSION_FAULT` danger
condition (via `expansionHealthy()` → `SensorState.expansionUnhealthy` →
`dangerActive()`): gas off, full purge, ack required.

### Status LEDs (the 2+2 word)

`Outputs.cpp` owns all four on-board LEDs; Comms drives none (two writers on
one pin fight). It reads `commsLinkUp()` / `commsMqttConnected()` each pass.

| LEDs | Carries |
|---|---|
| D1 D0 | connection: `00` no link · `01` link, no MQTT · `11` MQTT up. A bit **blinking** (400 ms) instead of solid = that layer *was* up and dropped. |
| D3 D2 | run-state: `00` WAITING/ARMED · `01` LEAKING / equipment-test · `10` HOLD · `11` VENTILATING (+ FULLY_VENTILATING) |
| all 4 | fast-blink together (200 ms) = **ALARM** — overrides both words |
| D3 D2 | slow-blink (800 ms) = peer-alarm-stale warn (connection LEDs stay normal) |

Run mode (CLEAN/VERBOSE/DEBUG/PROFILING) is **not** on the LEDs — it lives on
the MQTT `mode` / `log` topics.

## MQTT contract (what `Protocol.cpp` speaks)

Matches `docs/superpowers/specs/2026-09-07-kitchen-webapp-design.md` and the
webapp's `app/mqtt.py` / `app/commands.py`. `deviceId` = `KITCHEN_DEVICE_ID`
(`"KITCHEN-01"`, must equal the webapp's `KITCHEN_DEVICE_ID`).

| Direction | Topic | Retained | Payload |
|---|---|---|---|
| sub | `KitchenControl/{id}/cmd` | no | `start(spec)` · `confirm(runId)` · `stop` · `ack` |
| sub | `KitchenControl/{id}/config/set` | — | per-sensor thresholds (accepted, not yet applied) |
| sub | `KitchenControl/{id}/mode` | **no** | `CLEAN`/`VERBOSE`/`DEBUG`/`PROFILING` — live verbosity; reboot returns to `KITCHEN_DEFAULT_MODE` |
| sub | `safety/permit/{id}` | — | `{permit,seq}` — absence warns, only explicit `false` trips |
| sub | `status/+/+/alarm/+/hydrogen` | retained | peer alarms; self-filtered by id |
| sub | `DataAcquisition/Kitchen/{id}/#` | — | not parsed — presence only, to check the remote CM7 DAQ is alive after `sensors/power` goes on (warn-only if silent > 10 s) |
| pub | `KitchenControl/{id}/state` | **yes** | state, role, elapsed, inventory, ack flags, reason, sensorsOn |
| pub | `KitchenControl/{id}/ack` | no | interpreted spec echo + `quorumInterpreted:{requestedPct,interpretedCounts}`, or `rejection` |
| pub | `KitchenControl/{id}/sensors/power` | **yes**, on change | `{on:bool}` — commands the remote CM7 DAQ. `on` = `remoteSensorsOn`: true only during a **leak-test** leak run (LEAKING→purge), never in equipment-test. The local A0602/base H2 sensors are a separate thing — on in LEAKING *and* whenever the selector is equipment-test, 70 s warm-up, no MQTT (no local power pin in this build). |
| pub | `status/{Exp}/{id}/alarm/{lab}/hydrogen` | **yes**, on latch edge | `{danger,active,description,runId}` |
| pub | `status/{Exp}/{id}/run` | **yes**, on state change | `{running,runId}` for `server.py` |
| pub | `status/{Exp}/{id}/online` | **yes** (LWT `offline`) | `online` |
| pub | `KitchenControl/{id}/log` | no | `{level,msg,t}` — mirror of Serial diagnostics at ≥ VERBOSE |

**Retained `cmd`:** PubSubClient's callback exposes no retained flag, so the
protection is structural instead — `start` only ever *arms* (from `WAITING`),
and a separate live `confirm` is what releases gas; a replayed `start` at worst
re-arms and `ARM_TIMEOUT_MS` disarms it. The webapp also never retains `cmd`.

## Building the tests

```sh
export PATH="$PATH:/c/mingw64/bin"
g++ -std=c++17 -DKITCHEN_ALLOW_PLACEHOLDER_SCALES -I kitchen \
    kitchen/test/test_safety.cpp kitchen/KitchenCore.cpp -o test_safety \
    && ./test_safety
```

`KITCHEN_ALLOW_PLACEHOLDER_SCALES` is required until the five analog-scale
constants in `Kitchen_Settings.h` are measured on the bench. The tests drive
counts directly and never exercise a physical-unit conversion, so placeholder
scales are harmless here — but a **firmware** build with any of them still a
placeholder is refused at compile time by the `#error` guard.

The shim (`kitchen.ino`, `Sensors/Outputs/Protocol/Expansion/Comms`) is
**not** part of the `g++` build — it needs the Arduino IDE with
`Arduino_Opta_Blueprint`, `PubSubClient`, `ArduinoJson`, `Ethernet`, and the
mbed_opta board core.

## Before hydrogen

Every `PLACEHOLDER` in `Kitchen_Settings.h` must be confirmed on the bench.
The `#error` guard covers the five analog-scale constants; the rest
(relay/expansion channel numbers, `FLOW_LIMIT_COUNTS`, `INVENTORY_CAP_ML`,
timings marked PLACEHOLDER, `EXPERIMENT_NAME` / `LAB_ID`, the e-stop polarity
in `Sensors.cpp`, the D1608E-vs-A0602 expansion order) are on you to verify.

Bench checklist is in `docs/implementation-plan.md` → "Verification → Bench".
