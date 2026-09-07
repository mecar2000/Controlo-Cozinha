# KitchenCore & Protocol — in-depth explanation

Companion to `kitchen/README.md`. Covers the two files that carry the whole
control decision and the whole wire boundary. Written against the code as of
the 2026-09-07 review-protocol changes (sensor-power split, `EXPANSION_FAULT`,
run modes, LED word, gas lamp, file merges).

---

# KitchenCore — the entire control path

## 1. What it is for

`KitchenCore` is the **whole decision**. One class, one state machine. It reads
a `SensorState` (everything measurable) and returns an `OutputRequest` (a
desired end-state for every actuator). There is deliberately **no second
layer** — no separate "safety veto" that rewrites the request. The original
design had `SafetyCore` + `ExperimentCore` in a request/veto pair; that was
merged into this one class because two layers meant two places to keep in sync
and two places to audit.

It is **Arduino-free** — includes only `<stdint.h>`, `RunSpec.h`,
`Kitchen_Settings.h`. That is what lets `test_safety.cpp` compile it with
`g++` and drive it with a fake clock. Every timing is a `uint32_t nowMs`
**parameter**, never a `millis()` call.

## 2. The audit claim

Two functions are the entire safety-relevant surface:

- **`dangerActive(const SensorState&, DangerReason&)`** — `static`, takes only
  `SensorState`, **never reads `spec_`**. The only place danger conditions are
  evaluated.
- **The `FULLY_VENTILATING` branch of `outputsFor()`** — a fixed block: gas
  off, all registers open, fan 100%, no `spec_` read.

Because `dangerActive()` cannot see anything the website sent (`spec_` is
private, not a parameter), and because it is checked *first* every pass as a
state transition, the claim "nothing the website sends can open the gas valve
when the safety layer says no" reduces to reading those two functions. The
invariants are enforced *by review*, not the compiler — the header lists them
explicitly:

- `Outputs::drive()` is the only place that calls `digitalWrite`/`analogWrite`
  on control pins.
- `dangerActive()` is the only place danger conditions are evaluated.
- The `FULLY_VENTILATING` output block never reads `spec_`.

## 3. The state machine

```
WAITING ──start()──> ARMED ──confirm()──> LEAKING ──stop cond──> HOLD
   ^                   │                     │                      │
   │            ARM_TIMEOUT_MS               │ selector flip        │ stop cond
   │                   v                     v (OPERATOR_ABORT)     v
   └──────────── WAITING <── FULLY_VENTILATING <──────────── VENTILATING
                              ^        │                            │
                     any danger│       │ ack + 5min all-clear       │ stop cond
                    (from any state)   └────────────────────────────┘
```

| State | Gas | Fans | Registers | Meaning |
|---|---|---|---|---|
| WAITING | off | idle 10% | closed | nothing running |
| ARMED | off | idle 10% | closed | `start` accepted, waiting for live `confirm` |
| LEAKING | **on** (after 70 s warm-up) | 0 | closed | gas flowing, room sealed |
| HOLD | off | 0 | closed | **the measurement phase** — watch propagation undisturbed |
| VENTILATING | off | spec % | spec set | controlled clear-out |
| FULLY_VENTILATING | off | 100% | **all open** | mandatory 5-min purge / danger response |

### The two-message arm

`start(spec, s, nowMs)` rejects unless: state is `WAITING`, `spec.valid`
(Protocol set it), role is leak-test. On success it copies the spec, stamps
`armedAtMs_`, → `ARMED`. It **only ever arms** — never releases gas.

`confirm(runId, nowMs)` is a **separate live message** the website sends after
showing the operator the interpreted spec. It rejects unless state is `ARMED`,
within `ARM_TIMEOUT_MS` (60 s — else back to `WAITING` with `ARM_TIMED_OUT`),
and `runId` matches. This is the structural defense against a **replayed
retained `start`**: a broker replay at worst re-arms, and the 60 s timeout
disarms it — releasing gas needs the second, un-retained `confirm`.

### The warm-up gate (the subtle one)

`confirm()` → `LEAKING`, and if local sensors were not already on, sets
`warmupPending_ = true`, `warmupStartedMs_ = nowMs`.

In the `LEAKING` case of `update()`:

```cpp
bool gateOpen = !warmupPending_ || (nowMs - warmupStartedMs_ >= SENSOR_WARMUP_MS);  // 70 s
if (warmupPending_ && gateOpen) {
  warmupPending_ = false;
  phaseClockFromMs_  = nowMs;   // <-- re-base the leak clock HERE
  lastIntegrationMs_ = 0;       // don't integrate flow across the gate
}
```

`phaseClockFromMs_` is why this matters. Stop conditions time from
`phaseClockFromMs_`, **not** state entry. Without the re-base, a spec asking
"leak for 5 s" would deliver **zero gas**: the phase clock would start at
`LEAKING` entry, the 70 s gate would hold the valve shut, and
`nowMs - phaseClockFromMs_ >= 5000` would already be true when the gate finally
opened. So the clock is re-based to the instant gas actually starts flowing —
`maxDurationMs` measures *gas delivery*, not *time since entry*.

`outputsFor()` for `LEAKING`: `gasOpen = !warmupPending_` — the gate directly
gates the valve.

## 4. `dangerActive()` — the conditions, in order

```
1. any present sensor:  counts >= thresholdCounts        -> LOCAL_SENSOR_THRESHOLD
2. any present+expectedOn sensor: stale                  -> LOCAL_SENSOR_STALE
3. leak-test role AND flowOverLimitSustained             -> FLOW_OVER_LIMIT
4. leak-test role AND deliveredInventory_mL > CAP        -> INVENTORY_CAP_EXCEEDED
5. peerAlarmActive                                       -> PEER_ALARM
6. permitPresent AND !permitValue                        -> PERMIT_DENIED
7. estopPressed                                          -> ESTOP
8. expansionUnhealthy                                    -> EXPANSION_FAULT   (added 2026-09-07)
9. externalTripActive                                    -> EXTERNAL_TRIP     (reserved, never set)
   else                                                  -> NONE
```

Design points:

- **`>=` everywhere** — "at or above threshold trips". Matches `quorumMet()`.
  The header says these two must not drift apart.
- **Silence != safety** for the *threshold* check (a stale/absent sensor is
  skipped — it cannot prove low concentration), but silence *is* danger for
  the *staleness* check, but **only when `expectedOn`** — a sensor deliberately
  powered off going quiet is expected, not a fault.
- **Permit absence warns, does not trip** — only an explicit `"permit":false`
  trips. A missing safety-permit message must not halt an experiment.
- **`EXPANSION_FAULT`** (new): a missing A0602 makes all 6 H2 current sensors
  read 0 counts — indistinguishable from "0% H2". The threshold and quorum
  checks would be blind, so expansion loss is itself danger. Fed in via
  `SensorState.expansionUnhealthy`, set in `Sensors.cpp` from
  `expansionHealthy()`.
- **`isLeakTestRole` guards flow/inventory** — those are leak-test concepts; in
  equipment-test there is no gas delivery to cap.

## 5. `update()` — the once-per-pass sequence

```cpp
OutputRequest update(const SensorState& s, uint32_t nowMs) {
  roleIsLeakTest_ = selectorIsLeakTest(s);       // cached for remoteOn()

  bool danger = dangerActive(s, reason);         // (A) DANGER FIRST
  if (danger) {
    enterFullyVentilating(reason, /*requiresAck=*/true, nowMs);
    clearSinceMs_ = 0;                            // danger this pass = not-clear
  } else if (state_ == FULLY_VENTILATING) {
    if (clearSinceMs_ == 0) clearSinceMs_ = nowMs;  // start the all-clear clock
  }

  if (!selectorIsLeakTest(s) && !localSensorsOn_) {   // (B) equipment-test:
    localSensorsOn_ = true; warmupPending_ = false;   //     local sensors on, no gate
  }

  switch (state_) { ... }                          // (C) per-state advance

  return outputsFor(nowMs);                        // (D) state -> desired outputs
}
```

**(A) is the whole safety argument.** Danger is evaluated and acted on *before*
the `switch`. Once `enterFullyVentilating()` has moved `state_`, the
`LEAKING`/`HOLD`/`VENTILATING` cases — the only ones that read `spec_` — are
unreachable this pass. State and outputs always agree on the same pass; there
is no cross-pass skew.

**`clearSinceMs_`** is the "5 continuous minutes clear" tracker. `0` is a
reserved sentinel meaning "not clear right now". Any pass with danger active
resets it to `0`. So `canLeaveFullyVentilating()` requires
`nowMs - clearSinceMs_ >= FULLY_VENT_MIN_HOLD_MS` — and a danger re-firing 4
minutes into a 5-minute hold restarts the clock. (`nowMs == 0` is bumped to
`1` so the sentinel is never a real timestamp.)

## 6. `enterFullyVentilating()` — one entry point, PROMOTES never DEMOTES

Every route into the purge — danger, operator abort, routine end-of-run,
operator `stop()` — goes through here.

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

- `requiresAck` **promotes** but never demotes. A danger firing mid-routine-
  purge upgrades it to "needs ack"; nothing but *leaving* the state clears
  `ackRequired_`.
- `acked_` is only ever cleared **on the way out** (`canLeaveFullyVentilating`
  path). Re-entering while already latched cannot discard an ack a human
  already gave — otherwise a flickering sensor would keep throwing away the
  operator's acknowledgment.
- **The `clearSinceMs_` init asymmetry**: a `requiresAck` entry has an active
  condition *this pass*, so the clock is not running yet — it starts on the
  next `update()` with the condition gone. A routine entry (`stop()`,
  end-of-run) has no condition — it is clear from the instant it enters, so the
  clock starts *now*. Without this, a routine purge's 5-minute hold was
  measured one `update()` call short.

## 7. `humanAck()` — two indistinguishable callers

The physical USER button and the MQTT `ack` command call the identical
function. The button path **must work with no network** — a physical
acknowledgment matters precisely when the network is the broken thing (same
principle as the e-stop). It just sets `acked_ = true`;
`canLeaveFullyVentilating()` still enforces the full 5-minute all-clear hold on
top. The ack is not a shortcut past the hold — acking early still requires the
full continuous-clear time.

## 8. `OPERATOR_ABORT` — the one danger not in `dangerActive()`

In the `LEAKING` case:
`if (!selectorIsLeakTest(s)) enterFullyVentilating(DangerReason::OPERATOR_ABORT, /*requiresAck=*/true, nowMs);`

The role selector physically flipped away from leak-test *while gas was
flowing*. This is not in `dangerActive()` because there is **no ongoing
condition to clear** — only a human to acknowledge. It is latched directly by
the state branch. It requires an ack, *unlike* `stop()`: `stop()` is an
in-band command from someone who knows a run is live; the selector is a
physical switch someone could flip walking into the room with no idea what is
happening. `enterFullyVentilating` being idempotent w.r.t. `acked_` means
calling it every pass the selector stays wrong will not discard the ack.

## 9. Sensor power — LOCAL vs REMOTE (changed 2026-09-07)

Was lockstep (`sensorsOn_` drove both). Now split:

- **`localSensorsOn_`** (member) — the A0602/base H2 sensors. On in `LEAKING`
  (via `confirm()` + 70 s warm-up) **or** whenever equipment-test role
  (immediately, no gate — step (B) of `update()`). Off after
  `SENSOR_IDLE_TIMEOUT_MS` (10 min) idle in `WAITING`.
- **`remoteOn()`** (derived, not stored) — the CM7 DAQ. Returns
  `localSensorsOn_ && roleIsLeakTest_ && state in {LEAKING, HOLD, VENTILATING,
  FULLY_VENTILATING}`. Never equipment-test, never WAITING/ARMED.
  `roleIsLeakTest_` is cached at the top of `update()` because `remoteOn()` is
  `const` and has no `SensorState` to read.

`outputsFor()`:
`out.localSensorsOn = localSensorsOn_; out.remoteSensorsOn = remoteOn();`

One source of truth (`localSensorsOn_` + role + state) instead of two bools to
keep synced.

Warm-up numbers: LOCAL H2 sensors `SENSOR_WARMUP_MS` = 70 s (gates gas in
LEAKING). REMOTE DAQ `REMOTE_SENSOR_WARMUP_MS` ~= 10 s (receive MQTT -> close
its relay -> ~1 s sensor warm-up). kitchen.ino runs a warn-only
`REMOTE_SENSOR_LIVENESS_MS` (10 s) check: on the remote-power OFF->ON edge, if
no `DataAcquisition/Kitchen/{id}/#` traffic is seen within the window, it logs
one ERROR line. Warn only — the local sensors and the danger path do not depend
on the DAQ.

## 10. `outputsFor()` — the pure state->output map

`const`, reads `state_` and (for `LEAKING`/`VENTILATING` only) `spec_`. The
`FULLY_VENTILATING` block reads **nothing** from `spec_` — fixed values.
`VENTILATING` re-applies `clampFanSpeedPct()` even though Protocol already
clamped at parse time — a ceiling that holds even if the parse-time clamp ever
drifts, at zero cost. `gasMayBePresent = (state_ != WAITING)` — the
breathing-lamp flag, pure, state-based (added 2026-09-07).

## 11. `RegisterSequencer` (folded in 2026-09-07)

Pure helper, now living at the top of `KitchenCore.h` / bottom of `.cpp`. Maps
`(RegisterSet desired, nowMs) -> CoilStates`. The one rule it encodes:
**opening the inlet is deferred `INLET_OPEN_DELAY_MS` when it opens alongside
another register** (so exhaust establishes airflow first); closing is
simultaneous, no delay; inlet-alone or inlet-already-open is immediate; a
request that changes again before the deadline *supersedes* the pending open
rather than firing it late. It holds one deadline field and takes `nowMs` as a
parameter — fake-clock testable.

---

# Protocol — the MQTT/JSON boundary

## 1. What it is for

The **only** file that turns wire bytes into a `RunSpec` and turns
`RunSpec`/state into wire bytes. `KitchenCore` never sees JSON; `Comms` never
sees a `RunSpec`. It uses ArduinoJson (`StaticJsonDocument`, stack-allocated,
no heap) and includes `<stddef.h>` (added 2026-09-07 — it uses `size_t` in the
builder signatures but only had `<stdint.h>`).

Responsibilities:

- parse + validate + clamp `start` specs (sets `RunSpec.valid`)
- the quorum threshold **%<->counts** conversion — *firmware-owned*
- `holdStop` fallback to `HOLD_MAX_DURATION_MS` when the website omits it
- **rejecting** `maxInventory_mL` on hold/vent stops (not silently ignoring)
- building the `state` / `ack` / `alarm` / `sensors/power` payloads
- dropping retained `cmd` messages

## 2. `protocolParseCommand()` — inbound

```cpp
ParsedCommand protocolParseCommand(const char* json, bool retained,
                                   float* requestedPctOut, uint16_t* interpretedCountsOut);
```

**First line of defense**: `if (retained) return pc;` — kind stays `NONE`. A
retained `cmd` is a broker replay; never act on it. (kitchen.ino currently
passes `retained = false` always because PubSubClient exposes no retained flag
— the real protection is structural in `KitchenCore`, this is
belt-and-braces.)

Then `deserializeJson` — failure -> `CmdKind::BAD_JSON`. Then dispatch on
`doc["cmd"]`:

| `cmd` | -> | carries |
|---|---|---|
| `"stop"` | `STOP` | - |
| `"ack"` | `ACK` | - |
| `"confirm"` | `CONFIRM` | `runId` |
| `"start"` | `START` | full spec (parsed below) |
| anything else | `BAD_JSON` | - |

### The `start` spec parse — validate + clamp

```cpp
gasSetpointPct = clampFanSpeedPct(spec["gasSetpointPct"] | 0.0f);   // 0..VENT_SPEED_MAX_PCT
```

Then three `parseStop()` calls, one per phase:

```cpp
parseStop(spec["leakStop"], ..., /*allowInventory=*/true,  ...);   // gas flowing here
parseStop(spec["holdStop"], ..., /*allowInventory=*/false, ...);   // REJECT inventory
parseStop(spec["ventStop"], ..., /*allowInventory=*/false, ...);   // REJECT inventory
```

`parseStop()` reads `maxDurationMs` (negative -> reject), `maxInventory_mL`
(negative -> reject; **positive on a phase where `allowInventory` is false ->
reject**, not ignore — "a spec asking for something impossible is a spec whose
author misunderstood the phase, and a silent no-op hides that"), and
`sensorQuorum` (`quorumCount` 0-255, and if `>0` a `thresholdPct` is required
and converted via `protocolPctToCounts`).

### The `holdStop` fallback

```cpp
if (pc.spec.holdStop.maxDurationMs == 0)
  pc.spec.holdStop.maxDurationMs = HOLD_MAX_DURATION_MS;      // omitted => ceiling, NOT zero
else if (... > HOLD_MAX_DURATION_MS)
  pc.spec.holdStop.maxDurationMs = HOLD_MAX_DURATION_MS;      // clamp
```

HOLD is the one phase with gas already delivered and fans **off**. An unbounded
or zero-duration HOLD must not be reachable through a partial/malformed spec —
"absent" means "the maximum safe hold", not "skip the measurement".
`leakStop`/`ventStop` omitting a duration is fine (== no cap), because those
phases have other exits.

Finally `pc.spec.valid = true`, and the echo out-params: for the **leak-phase**
quorum (the one that cuts gas), it writes back both the raw `%` the website
asked and the `counts` it interpreted — so a calibration mismatch is visible
from the browser.

## 3. The %<->counts conversion — firmware-owned

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
means in ADC counts — the firmware owns the mapping, using the *same* 4-20 mA
scale constants as `Sensors.cpp`. With placeholder scales
(`ADC_COUNTS_PER_MA == 0`) it returns the live-zero for every % — harmless on
the bench, and the `#error` guard blocks a real firmware build until the
constants are measured. `protocolCountsToPct()` is the inverse, returning 0 if
the scales are still placeholder (avoids a divide-by-zero).

## 4. Outbound builders

Each writes into a caller buffer, returns bytes written or `0` on overflow
(`n == 0 || n >= cap`).

### `protocolBuildState()` -> retained `KitchenControl/{id}/state`

`{state, role, elapsedMs, inventory_mL, ackRequired, acked, reason, sensorsOn}`.
`StaticJsonDocument<512>`. `reason` and `state` go through name-mapping
`switch`es (`protocolReasonName`, `stateName`). `EXPANSION_FAULT` was added to
`protocolReasonName` on 2026-09-07.

kitchen.ino gates the *call* to this builder: a pre-check on the discrete
fields (state/role/ack/reason/sensorsOn) publishes instantly on any change;
otherwise a 1 Hz heartbeat refreshes `elapsedMs`/`inventory_mL`; a final
`strcmp` guard stops an identical retained payload from being re-sent.
`onMqttConnect()` sets a force flag so a reconnect re-emits immediately.

### `protocolBuildAck()` -> non-retained `KitchenControl/{id}/ack`

The **interpreted spec echoed back** — durations after clamping,
`ventRegisters`, and `quorumInterpreted: {requestedPct, interpretedCounts}` —
or a `rejection` string if `!accepted`. `StaticJsonDocument<768>` (biggest
payload). This is what the browser's review screen shows before the operator
sends `confirm`: "the PLC understood your request as *this*". Non-retained — a
one-time reply to a specific command, not state. Deferred out of the MQTT
callback: `onMqttMessage()` stashes the result and sets `_ackPending`;
`flushPendingAck()` publishes it on the next `loop()` pass (PubSubClient is not
reentrant — cannot publish from inside its receive callback).

### `protocolBuildAlarm()` -> retained `status/{Exp}/{id}/alarm/{lab}/hydrogen`

`{danger, active, description, runId}` — both `danger` and `active` carry the
same bool because different consumers key on different names. Published on the
danger-latch **edge**, not continuously.

### `protocolBuildSensorsPower()` -> retained `KitchenControl/{id}/sensors/power`

Just `{on: bool}`. Commands the remote CM7 DAQ. Per the 2026-09-07 change,
`on` = `remoteSensorsOn` (leak-test leak run only), no longer the same as
local power.

## 5. Why this file exists as a boundary

- `KitchenCore` stays desktop-testable — no ArduinoJson, no wire format.
- The **closed set** of stop-condition primitives (`RunSpec.h`) means the
  website can only *compose* from `maxDuration` / `maxInventory` /
  `sensorQuorum` — it can never inject arbitrary logic. Protocol is where that
  closed set is enforced against the incoming JSON.
- Every gas-authority decision (quorum %->counts, spec clamps, the
  retained-cmd drop) is concentrated here, at the one point wire data enters —
  not scattered through the control code.
