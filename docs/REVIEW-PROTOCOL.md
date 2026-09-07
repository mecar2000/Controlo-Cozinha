# Review Protocol — full-repo walkthrough before further development

**Status: ACTIVE. Started 2026-09-04.**

Run it with the `/review-implementation` skill
(`.claude/skills/review-implementation/SKILL.md`), which holds the procedure.
**This document holds the state** — the order, the progress table, the in-progress
note, and the decisions log below are the source of truth, and the skill reads and
updates them.

## The rule

Development is **paused**. Before any new feature work, refactor, or file is added,
every existing file gets a thorough, one-at-a-time review with the user.

Do not skip ahead. Do not batch. Do not "quickly fix while we're here" unless the
user says so during that file's review.

## Order

Files are reviewed **in order of implementation**, which follows the dependency
graph from the bottom up — not alphabetically, not by directory listing, and not by
file timestamp. A file is reviewed only after everything it includes has been
reviewed, so each explanation can lean on ground already covered.

Dependency layers in `kitchen/` (re-derived 2026-09-07 from the `#include` graph
after the firmware was split into subsystem modules):

**File merge 2026-09-07 (managing 20 files was unwieldy):** `RegisterSequencer.*`
folded into `KitchenCore.*` (pure helper used only by the core + tests);
`Log.*` folded into `Comms.*` (run-mode logging that publishes via
`mqttPublish`). 20 source files → 16. The layers/order below are updated to
match.

- **L0** — no project includes: `RunSpec.h`, `Kitchen_Settings.h`, `Kitchen_Secrets.h`
- **L1** — L0 + system/library headers only: `KitchenCore.h` (now also
  `RegisterSequencer`), `Expansion.h`, `Comms.h` (now also the run-mode logging)
- **L2** — headers that pull an L1 project header, plus the L1 `.cpp`s:
  `KitchenCore.cpp`, `Expansion.cpp`, `Comms.cpp`,
  `Sensors.h`, `Outputs.h`, `Protocol.h`
- **L3** — the L2 subsystem `.cpp`s: `Sensors.cpp`, `Outputs.cpp`, `Protocol.cpp`
- **L4** — `kitchen.ino`, the top-level wiring (includes everything)
- **L5** — `test/test_safety.cpp`, host-side test binary

Review order for `kitchen/`:

1. `kitchen/RunSpec.h` — root, no project includes
2. `kitchen/Kitchen_Settings.h`
3. `kitchen/Kitchen_Secrets.h`
4. `kitchen/KitchenCore.h`  (includes `RegisterSequencer`)
5. `kitchen/Expansion.h`
6. `kitchen/Comms.h`  (includes the run-mode logging API)
7. `kitchen/KitchenCore.cpp`  (includes `RegisterSequencer::step`)
8. `kitchen/Expansion.cpp`
9. `kitchen/Comms.cpp`  (includes the run-mode logging impl)
10. `kitchen/Sensors.h`
11. `kitchen/Outputs.h`
12. `kitchen/Protocol.h`
13. `kitchen/Sensors.cpp`
14. `kitchen/Outputs.cpp`
15. `kitchen/Protocol.cpp`
16. `kitchen/kitchen.ino`
17. `kitchen/test/test_safety.cpp`

**Architecture change 2026-09-07:** `SafetyCore` + `ExperimentCore` (the
request/veto two-layer design) were merged into a single `KitchenCore` — see
`docs/implementation-plan.md` "Core architecture: one state machine". This
happened after items 1-2 below were signed off but before `SafetyCore.h` was
reviewed, so nothing from row 3 onward was ever reviewed under the old design.

**Firmware split 2026-09-07 (also re-derived above):** the monolithic sketch was
broken into `Sensors` / `Outputs` / `Protocol` / `Comms` / `Expansion` modules
plus a thin `kitchen.ino`. All new, all untracked in git, none previously in this
table. `README.md` is prose, not reviewed as code.

(then `centralPLC/`, then anything remaining — order to be confirmed with the user
when we get there.)

## What each review must deliver

For the file under review, produce a **detailed explanation**, covering:

1. **What it is for** — the responsibility of this file in one paragraph. What
   problem it exists to solve.
2. **Where it sits** — its place in the architecture. What includes it, what it
   includes, what depends on it, what it depends on. Where it runs (compile time,
   setup, loop, ISR) and on which side of the kitchen/centralPLC split.
3. **The choices made** — the design decisions embedded in the code and *why*:
   data structures picked, constants and their units, `constexpr` vs runtime,
   ownership and lifetime, error handling strategy, what was deliberately left
   out, and any spots where a different choice was plausible.

Flag anything that looks wrong, inconsistent, or unfinished as we go — but keep it
as a note for the user's decision rather than editing unprompted.

## In progress

Where the last session stopped, if it stopped mid-file. Read this first; report it
to the user before doing anything else. Cleared when the file is signed off.

**Review order override (2026-09-07, user's choice):** the user wants to read the
code in **execution order** — follow the program as it runs — rather than
dependency order, for usability/understanding reasons. So we review `kitchen.ino`
first (entry point), then descend into each subsystem at the point `setup()` /
`loop()` first calls it. The dependency-ordered table below still tracks
sign-off state; only the *sequence* changes. Rough execution sequence:
`kitchen.ino` → `Expansion` → `Sensors` → `KitchenCore` (+ `RegisterSequencer`)
→ `Outputs` → `Protocol` → `Comms` (+ run-mode logging, + `Kitchen_Secrets.h`)
→ `test_safety.cpp`. `Kitchen_Settings.h` / `RunSpec.h` already signed off.

- **File:** `kitchen/kitchen.ino` — IN PROGRESS (Mode 1, first real review)
- **Reached:** orientation map delivered; starting section-by-section review.
- **Open:** none yet.

Format when set:

- **File:** `path/to/file`
- **Reached:** how far through the explanation we got
- **Open:** any question left hanging, or "none"

## Progress

| # | File | Status |
|---|------|--------|
| 1 | `kitchen/RunSpec.h` | ✅ reviewed — comments touched 2026-09-07 for the merge (struct itself unchanged; not yet re-confirmed with the user) |
| 2 | `kitchen/Kitchen_Settings.h` | ✅ reviewed — comments touched 2026-09-07 for the merge (constants themselves unchanged; not yet re-confirmed with the user) |
| 3 | `kitchen/Kitchen_Secrets.h` | ⬜ next |
| 4 | `kitchen/KitchenCore.h` (incl. `RegisterSequencer`) | ⬜ |
| 5 | `kitchen/Expansion.h` | ⬜ |
| 6 | `kitchen/Comms.h` (incl. run-mode logging API) | ⬜ |
| 7 | `kitchen/KitchenCore.cpp` (incl. `RegisterSequencer::step`) | ⬜ |
| 8 | `kitchen/Expansion.cpp` | ⬜ |
| 9 | `kitchen/Comms.cpp` (incl. run-mode logging impl) | ⬜ |
| 10 | `kitchen/Sensors.h` | ⬜ |
| 11 | `kitchen/Outputs.h` | ⬜ |
| 12 | `kitchen/Protocol.h` | ⬜ |
| 13 | `kitchen/Sensors.cpp` | ⬜ |
| 14 | `kitchen/Outputs.cpp` | ⬜ |
| 15 | `kitchen/Protocol.cpp` | ⬜ |
| 16 | `kitchen/kitchen.ino` | ⬜ |
| 17 | `kitchen/test/test_safety.cpp` | ⬜ |
|   | `centralPLC/centralPLC.ino` | ⬜ order TBD |
|   | `centralPLC/server.py` | ⬜ order TBD |

### Open questions on ordering

- `centralPLC/centralPLC.ino` includes `"Secrets.h"`, which is not present in the
  repo. Untracked/gitignored, or not yet written? Its absence means the `.ino`
  cannot currently be placed by dependency alone.
- `centralPLC/server.py` is Python and sits outside the C++ include graph. It
  needs its own placement decision — likely reviewed alongside the `.ino` it
  talks to, since they form one client/server pair.
- ~~`kitchenold.ino` and `Old/` appear to be superseded.~~ Resolved: deleted
  2026-09-07, out of review scope. Its flow-driven pump logic lives on in
  [legacy-flow-pump-control.md](legacy-flow-pump-control.md).

Status values: `⬜` not yet reviewed · `⬜ next` up next · `✅ reviewed` signed off
· `🔄 re-reviewed <date>` changed after review and re-explained.

Update this table as each file is signed off.

## Decisions

Conclusions reached during reviews. Logged here, **not applied** — an `open` row is
edited into the code only when the user explicitly says so in that moment, and the
row then flips to `applied`. `declined` rows are kept so the same flag is not
raised again next session.

| Date | File | Decision | Rationale | Status |
|---|---|---|---|---|
| 2026-09-07 | `Expansion.*` / `Kitchen_Settings.h` | Rename/split `EXP_CHANNELS_PER_EXP` (8) — it is analog-only but named universally; D1608E has 16 inputs / 8 relays. Fix the stray comment at Expansion.cpp:92. | Name lies; low risk today (no D1608E digital inputs wired, future sensors are base-board/MQTT), but confusing. | **applied 2026-09-07** — split into `A0602_ANALOG_CHANNELS` (8) / `A0602_PWM_CHANNELS` (4) / `A0602_TOTAL_CHANNELS` (12) / `D1608E_DIGITAL_INPUTS` (16). `_chMode`/`_wantMode` resized to 12 so PWM ch 8-11 are addressable; all bounds checks now `>= A0602_TOTAL_CHANNELS`. Stray comment deleted. |
| 2026-09-07 | `Expansion.cpp` | Cut the hot-plug re-arm machinery (`_wantMode`+`_rearmFromWant` as hot-plug support). Keep a periodic `getExpansionNum()`/type check but repurpose it as a health check, not hot-plug. Keep the encoded-pin scheme and the 4-mode arm switch. | Expansions are DIN-rail mounted, powered with the PLC, never hot-plugged. ~30 lines of machinery for an impossible scenario. Periodic presence check is still wanted for fault detection (Q6). | **applied 2026-09-07** — `_probe()` no longer calls `_rearmFromWant()` on a topology change; `_rearmFromWant()` kept only as the shared impl of `expansionApplyInput/OutputConfig`. `_probe()` repurposed as the presence+type health check. `ChMode` gained `CH_PWM`. |
| 2026-09-07 | `Expansion.cpp` / `kitchen.ino` | Expansion verification, boot + periodic (user confirmed both). BOOT: wait until exactly 2 expansions present AND types correct (A0602 idx 0, D1608E idx 1), printing `found N/2` each pass. PERIODIC: the existing 3 s re-probe in `expansionLoop()` gains a `_expOk = (count==2 && types ok)` flag + `bool expansionHealthy()`; `kitchen.ino` checks it each loop → publish fault + force safe outputs / hold WAITING if unhealthy. | Today a missing/unpowered A0602 makes all 6 H2 sensors read 0 counts = "0% H2" and the system runs blind. `stale` only catches it after 10 s and only if expectedOn. Safety hole. 3 s interval is fine — don't go faster, I2C `getExpansionNum()` isn't free. | **applied 2026-09-07** — `expansionBegin()` blocks until `_expOk`. `expansionHealthy()` added, 3 s interval kept. Routed through the core per its own single-evaluation rule: `SensorState.expansionUnhealthy` (set in `Sensors.cpp`) → new `DangerReason::EXPANSION_FAULT` branch in `dangerActive()`. 3 new tests in `test_safety.cpp`. |
| 2026-09-07 | firmware-wide (new) | **Run modes: CLEAN / VERBOSE / DEBUG / PROFILING** (user request, supersedes the old "log topic" + "profiling `#define`" rows). `enum class RunMode`. Firmware default via `#define KITCHEN_DEFAULT_MODE` in `Kitchen_Settings.h`. MQTT override via new subscription `KitchenControl/KITCHEN-01/mode` — NOT retained (reboot = known-clean default). Ordered `>=` ladder (PROFILING implies VERBOSE). Compile-time `#define KITCHEN_ENABLE_DEBUG` strips DEBUG/PROFILING from production builds entirely (two layers: compile ceiling + runtime selection). CLEAN=errors only; VERBOSE=+transitions/connect; DEBUG=+every MQTT msg, sensor counts, decode; PROFILING=VERBOSE+per-loop-stage `micros()` deltas, ~5 s summary. All levels ≥VERBOSE also emit to a new MQTT log topic `KitchenControl/KITCHEN-01/log` (non-retained JSON `{level,msg,t}`) via `logPublish()` that mirrors to Serial. | Only diagnostics today are `Serial.println`, invisible without USB. User wants firmware- and MQTT-selectable verbosity plus loop profiling. | **applied 2026-09-07** — first as `Log.h`/`Log.cpp`, then **folded into `Comms.h`/`Comms.cpp`** in the 2026-09-07 file merge (it publishes via `mqttPublish`; no cycle). `KITCHEN_DEFAULT_MODE_ORDINAL` + `KITCHEN_ENABLE_DEBUG` in settings. `mode` topic added (later `SUBSCRIPTIONS[4]`; the DAQ-liveness row pushed the count to 6). A `LogPublishFn` pointer that `kitchen.ino` sets to `mqttPublish` + the `log` topic. 17 Serial sites migrated. PROFILING wired into `loop()` via `profileStage()`. |
| 2026-09-07 | `kitchen.ino` | Extract the inline publish-on-change block (lines ~238-291) into functions (`publishRunTopic` / `publishState` / `publishSensorsPower` / `publishAlarm` / `flushPendingAck`), moving each block's `static` snapshot var next to it. Pure refactor, no behavior change. | `loop()` should read as the 7-step pipeline its header comment promises; snapshot statics are currently scattered. | **applied 2026-09-07** — all five functions extracted; `_lastStatePayload` / `_lastSensorsOn` / `_stateEnteredMs` kept file-scope (cross-function reasons documented at their declarations), the rest moved local. |
| 2026-09-07 | `Outputs.cpp` / `Comms.cpp` | **4 on-board LEDs as a 2+2 word.** D1 D0 = connection: `00` no link · `01` link, no MQTT · `11` MQTT up; a bit **blinking = that layer dropped** (needs a "was up" latch). D3 D2 = run-state: `00` WAITING/ARMED · `01` LEAKING/equipment-test · `10` HOLD · `11` VENTILATING (FULLY_VENTILATING folds into `11`). **Alarm: ALL FOUR fast-blink together (200 ms)**, overrides both words. Peer-stale warn = D3/D2 slow-blink (800 ms) only. `outputsDrive()` owns all 4 LEDs; Comms stops driving any LED and exposes `commsLinkUp()` + `commsMqttConnected()`. Mode is NOT on the LEDs. No hardware. **Row's original text said Comms drives D0/D1 — it actually passed `commsSetLedPins(LED_D1, LED_D2)` and Outputs owned D0/D3; user confirmed the row's mapping wins, so LEDs were re-assigned as above.** | User-requested compact status at a glance; 4 LEDs, no spare expansion outputs. | **applied 2026-09-07** — `commsSetLedPins` / `_setLed` / `_blinkLed` removed from Comms (load-bearing: `_blinkLed`'s `static lastToggle[4]` + read-modify-write would fight a second writer, and its `pin & 3` fallback bug goes with it). `outputsDrive()` signature gained `KitchenState state, bool isLeakTestRole`. |
| 2026-09-07 | `KitchenCore.*` / `Outputs.cpp` / `Kitchen_Settings.h` + HARDWARE | **"Gas may be present" breathing lamp.** 24 V LED, breathing (duty 0→100→0 over ~3 s, fast PWM carrier). ON in every state except WAITING (state-based, NOT sensor-based). HARDWARE: logic-level MOSFET (or fast DC SSR) + gate resistor + flyback diode, switching the 24 V rail. FIRMWARE: `OutputRequest.gasMayBePresent = state()!=WAITING`; `PIN_GAS_LAMP`; breathe ramp in `outputsDrive()`. Outputs stays the only writer. | User wants a passive "hydrogen may be in the room" indicator, unambiguous and independent of sensor health. | **applied 2026-09-07** — routed via the **A0602 dedicated PWM channel `OA_CH_8`** (not a base-board pin — row 175 resolved), reachable because Phase 1 lifted the channel ceiling to 12. `expansionApplyPwmConfig()` / `expansionWritePwm(pin, dutyPct)` added (converts duty% → pulse-µs internally). `gasMayBePresent` in `OutputRequest` + `operator==`. 6 new tests. **Hardware still owed: confirm the A0602 PWM channel's output electrical spec against the datasheet before wiring the MOSFET.** |
| 2026-09-07 | `Expansion.*` / `Kitchen_Settings.h` | **PWM pin for `PIN_GAS_LAMP`** (was: "is there a free base-board PWM pin, or does it need a 2nd board?"). | — | **resolved 2026-09-07** — the A0602 has 4 dedicated PWM channels (`OA_CH_8`–`OA_CH_11`) via `AnalogExpansion::setPwm(ch, period_us, pulse_us)`, separate from the 8 analog I/O channels. No second board, no base-board pin. `EXP_CHANNELS_PER_EXP` (8) made ch 8-11 unreachable, so the Phase 1 rename was a hard prerequisite. Verified in `Arduino_Opta_Blueprint/src/AnalogCommonCfg.h` + `AnalogExpansion.h:238` + `examples/Analog/PWM/PWM.ino`. Folded into the gas-lamp row above. |
| 2026-09-07 | `Comms.cpp` | Wire `commsForceReconnect()` to a trigger (N consecutive `mqttPublish` failures) so half-open sockets after a cable pull are detected; log link up→down transitions. Consider a static IP instead of DHCP. | `commsForceReconnect()` exists for exactly the half-open-socket case but nothing calls it. Link check only runs when MQTT already reports disconnected. Not a safety issue (danger path is offline-capable) but should be visible. | **applied 2026-09-07** — `mqttPublish()` counts consecutive failures; after 5, with `mqttClient.connected()` still true, it calls `commsForceReconnect()` and resets. `_noteLink()` logs each link up/down edge (checked every `reconnect()`, not only when MQTT is already down). **DHCP left in place** — static IP not adopted. |
| 2026-09-07 | `Kitchen_Settings.h` | (Minor) Consider dropping `KITCHEN_MAX_LOCAL_SENSORS` 21→~15 and trimming `KITCHEN_MAX_PEER_ZONES` sizing until the peer-zone table is built. | Slots 12-21 are zeroed every pass and reserved for D1608E-analog sensors the user does not plan to add. Reclaims RAM in every `SensorState` copy. Low priority. | **applied 2026-09-07** — `KITCHEN_MAX_LOCAL_SENSORS` 21→15, `KITCHEN_MAX_PEER_ZONES` 32→16, `SENSOR_CALIBRATION_OFFSET_MA[]` initializer trimmed to 15 in step. |
| 2026-09-07 | `KitchenCore.*` / `kitchen.ino` / `Kitchen_Settings.h` | **Split LOCAL and REMOTE sensor power** (supersedes plan item 18 "local + remote move in lockstep"). LOCAL (A0602/base H2): ON in LEAKING (leak-test) OR whenever equipment-test; 70 s warm-up (was 30 s); idle-off after 10 min WAITING. REMOTE (CM7 DAQ, MQTT `sensors/power`): ON only during a leak-test leak run (LEAKING→purge) — never equipment-test, never WAITING/ARMED; DAQ owns its own power relay, ~10 s to receive→relay→power-on. New **DAQ liveness check** in `kitchen.ino`: on the remote OFF→ON edge, warn once (warn-only, no state effect) if no `DataAcquisition/Kitchen/{id}/#` traffic within `REMOTE_SENSOR_LIVENESS_MS` (10 s). | User: remote sensors are leak-test-only; local runs in both roles; local H2 sensors need 70 s not 30 s; the PLC should confirm the DAQ actually came alive after commanding it on. | **applied 2026-09-07** — `KitchenCore`: `sensorsOn_` → `localSensorsOn_`, `remoteOn()` derives the remote flag from role+state (one source of truth). `OutputRequest.remoteSensorsOn` now `!=` `localSensorsOn`. `SENSOR_WARMUP_MS` 30 s→70 s; `REMOTE_SENSOR_WARMUP_MS` / `REMOTE_SENSOR_LIVENESS_MS` / `DAQ_KITCHEN_TOPIC_PREFIX` added. `kitchen.ino`: subscribes `DataAcquisition/Kitchen/{id}/#` (`SUBSCRIPTIONS[5]`, count→6), `sensorsPoll()` now fed LOCAL power for `expectedOn` (was fed remote — a latent gap once split: a stale local sensor in equipment-test would not have tripped). 8 new core tests. **`DAQ_KITCHEN_TOPIC_PREFIX` is a placeholder — confirm the CM7's actual publish topic.** |
| 2026-09-07 | file layout | **Merge `RegisterSequencer.*` → `KitchenCore.*` and `Log.*` → `Comms.*`.** 20 source files was hard to manage. Keep every file < 600 lines and stay on one side of the g++/Arduino boundary. | `RegisterSequencer` is a pure helper used only by the core + tests; `Log` is run-mode logging that publishes via `mqttPublish` (belongs with Comms, no cycle). | **applied 2026-09-07** — 20 → 16 source files. `KitchenCore.cpp` ~525 lines, `Comms.cpp` ~415, both under 600. Include sites, the `g++` test command (`README.md` + `test_safety.cpp` header), and this doc's layer list / review table updated. `RunSpec.h` kept separate (shared contract between core and Protocol). `Sensors`/`Outputs` kept separate (opposite directions; `Outputs.h` carries the "ONLY PIN WRITER" invariant). |
| — | — | _(none yet)_ | — | — |
