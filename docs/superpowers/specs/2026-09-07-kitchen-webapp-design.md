# Kitchen H2 Control Webapp — Design

> Design spec for the operator-facing web application that commands the kitchen
> hydrogen leak-experiment rig and visualises its sensors in 3D.
> Companion to [`docs/implementation-plan.md`](../../implementation-plan.md),
> which specifies the firmware this app talks to.

## Context

The experimental kitchen runs hydrogen leak-propagation experiments: hydrogen is
released at a controlled rate, dispersion is measured by H2 sensors, then
ventilation is applied to measure decay. The firmware (`Cozinha/kitchen/`) owns the
state machine and every safety interlock. It exposes an MQTT command interface but
has no user interface.

This app is that interface. It does two jobs:

1. **Control** — compose run specs, start/stop runs, acknowledge latched stops.
2. **Visualise** — a 3D model of the kitchen with sensor positions, rendering
   hydrogen concentration as a heatmap, live and in replay.

`DataAcquisition` (`HylabCode/DataAcquisition`) already ingests, converts and stores
sensor readings for the whole lab. It is reused unmodified as the historian.

**Intended outcome:** an operator can run a hydrogen experiment, watch the cloud
develop in the room, stop it instantly, and afterwards replay any past run against
the sensor layout as it was at the time.

---

## Architecture

Two servers on one machine, one local SQL Server instance, two databases.

```
 H2 sensors + Kitchen Opta
        │ MQTT
        ▼
   ┌─────────┐
   │ broker  │
   └────┬────┘
        ├────────────────────────────┐
        │ DataAcquisition/Kitchen/…   │ KitchenControl/… + status/… + safety/permit/…
        ▼                            ▼
 ┌────────────────┐          ┌──────────────────┐
 │ DataAcquisition │◄─ REST ──│   Kitchen app    │
 │   (historian)   │          │ (control + view) │
 └────────┬────────┘          └────────┬─────────┘
          ▼                            ▼
  DataAcquisition DB           KitchenControl DB
  (readings, experiments)      (configs, runs, layouts)
```

### The boundary with DataAcquisition

**DataAcquisition is the historian. The kitchen app is the control surface.**

This split exists because the two systems have different failure semantics. The
historian is read-only with respect to the physical world; its worst case is lost
samples. The kitchen app commands a hydrogen release; its worst case is not lost
samples. The firmware's entire architecture rests on a small, auditable command
path, and that argument does not survive the command path being embedded in a
3,700-line historian.

**Rules:**

1. **DataAcquisition is used through its public REST API only.** No code changes, no
   shared modules, no direct SQL against its database — including for history reads.
   Going around the API would couple this app to a schema that auto-migrates itself.
2. **If something requires modifying DataAcquisition, stop and revisit this
   boundary** rather than quietly forking it. Nothing found during design does.
3. **Live sensor data is read from MQTT directly**, not proxied through the
   historian. Two consumers of one broker stream, not a chain — heatmap latency does
   not depend on the historian, and the historian is not in the safety path.

### Division of responsibility

| Concern | Owner |
|---|---|
| Sensor ingest, unit conversion, reading storage | DataAcquisition |
| Experiment/stage registry, recording state | DataAcquisition |
| History queries, LTTB downsampling, export | DataAcquisition |
| Conversion definitions (V→ppm, mA→%v/v) | DataAcquisition |
| Run specs, run commands, two-phase confirm | Kitchen app |
| Run records, outcomes, audit trail | Kitchen app |
| Sensor positions, room geometry, layout snapshots | Kitchen app |
| 3D rendering, heatmap, replay | Kitchen app |

Calibrating a kitchen sensor means opening DataAcquisition. Accepted: one
calibration authority, automatic `conv_id` provenance. The kitchen app deep-links to
it.

### Modules

Backend (Python/Flask, matching DataAcquisition's stack):

| Module | Responsibility |
|---|---|
| `mqtt.py` | Broker connection; subscribes live state + sensor topics |
| `commands.py` | **The only module that publishes to `KitchenControl/…/cmd`.** Two-phase start, stop, ack |
| `runs.py` | `start_run(config_id, experiment_id, run_name)` — the single entry point; orchestrates DAQ recording + firmware confirm |
| `daq.py` | HTTP client for DataAcquisition |
| `db.py` | KitchenControl SQL Server layer |
| `layout.py` | Sensor config, layout snapshots |
| `routes/` | Endpoints for the frontend |

`commands.py` as sole publisher to `cmd` mirrors the firmware's own
`Outputs::drive()` invariant — one auditable choke point per layer. Stated in a
comment at the top of the module.

Frontend: React + Tailwind CSS v4 + lucide-react + motion/react + Three.js, per
[`docs/webapp-style-guide.txt`](../../webapp-style-guide.txt).

### Availability

The kitchen app and DataAcquisition run on the same machine, so DAQ absence is a
fault, not a routine condition.

| Condition | Behaviour |
|---|---|
| DAQ unreachable | Starting a run is **blocked**, with the reason shown |
| DAQ dies mid-run | Run **continues**; recording marked lost; persistent banner |
| Broker unreachable | No commands possible; live view flagged stale, never shows last-known values as current |
| Read-only display mode | Start refused **server-side**; stop and ack still permitted |

The blocking rule applies to *starting*, never to a run in flight. Stop and ack go
straight to MQTT and must not depend on a storage service.

### Auth

Token-based, matching DataAcquisition. The kitchen app holds a DAQ token
server-side; **the browser never talks to DataAcquisition directly**. One place
decides authorization for a hydrogen command.

---

## MQTT contract

Consumed (as specified in `implementation-plan.md`):

- `KitchenControl/{deviceId}/state` — retained: state, selector role, elapsed
- `KitchenControl/{deviceId}/ack` — validated spec echo, or rejection + reason
- `KitchenControl/{deviceId}/config/ack` — clamped threshold table
- `DataAcquisition/Kitchen/{deviceId}/*` — sensor samples (also ingested by the DAQ)
- `status/{ExperimentName}/{deviceId}/alarm/{labId}/hydrogen` — danger, retained
- `safety/permit/{deviceId}` — permit heartbeat

Published — **only** by `commands.py`:

- `KitchenControl/{deviceId}/cmd` — `start(spec)` · `confirm(runId)` · `stop` · `ack`

Commands are **never retained** (firmware ignores retained `cmd` messages).

### Units

H2 sensors are 4-20 mA. **Per-sensor calibration offsets are firmware-owned**
(`SENSOR_CALIBRATION_OFFSET_MA[]` in `Kitchen_Settings.h`), applied once at the read
boundary in `Sensors.cpp`. Every mA value the app sees is already corrected — **the
app must never apply a second offset.** The DataAcquisition conversion is mA→%v/v
only.

The quorum threshold is sent as `%` on the wire and converted to counts by
`Protocol`; the counts↔% mapping is firmware-owned. The ack echoes both the
requested % and the interpreted counts, and the ack-review screen shows both.

---

## Data model

New `KitchenControl` database on the same local SQL Server instance. DataAcquisition's
schema is untouched.

```sql
run_configs      id, name, spec_json, created_at, updated_at, archived

runs             id, run_number, name, config_id, config_snapshot_json,
                 daq_experiment_id, daq_experiment_name,
                 requested_spec_json, acked_spec_json,
                 started_at, confirmed_at, ended_at,
                 outcome, outcome_detail, latch_cause,
                 recorded, operator

sensor_config    id, sensor_key, label, x, y, z, enabled,
                 daq_device_id, daq_sensor_name, updated_at

layout_snapshots id, daq_experiment_id, run_id, layout_json, captured_at
```

**KitchenControl stores what was commanded and what happened, never what was
measured.** Readings live in DataAcquisition. This split is what keeps the boundary
from eroding.

### Key fields

**`spec_json`** mirrors the firmware's `RunSpec` (`kitchen/RunSpec.h`) — the closed
set of primitives the website may compose: `gasSetpointPct`, `leakStop`, `holdStop`,
`ventRegisters`, `fanSpeedPct`, `ventStop`, each `StopCondition` carrying
`maxDurationMs`, `maxInventory_mL` and `sensorQuorum`. The app composes from this set
and nothing else.

**`requested_spec_json` and `acked_spec_json` are both stored.** The difference
between what was asked and what the firmware agreed to run is the most useful
forensic field in the table — a clamped hold or a raised threshold stays visible
forever.

**`config_snapshot_json` alongside `config_id`.** Configs are editable; runs are
historical fact. Editing a config next month must not retroactively change what run
12 did.

**`run_number`** is a real column, giving stage names (`run3-leak`) a stable source
and letting future batch repeats number themselves without collision.

**`outcome`** — `completed` · `stopped` · `latched` · `rejected` · `aborted`.
The enum you filter on.

**`outcome_detail`** — prose, for reading. Mostly carries what the firmware already
reports (ack rejection reason, alarm `description`). E.g. `"Sensor quorum: 3 of 5
sensors above 2.0 %v/v"`, `"Peer alarm from OPTA-Turbine, lab 2, hydrogen"`.

**`latch_cause`** — structured, for counting. Mirrors the firmware's `DangerReason`
enum rather than inventing a vocabulary: `LOCAL_SENSOR_THRESHOLD` ·
`LOCAL_SENSOR_STALE` · `FLOW_OVER_LIMIT` · `INVENTORY_CAP_EXCEEDED` · `PEER_ALARM` ·
`PERMIT_DENIED` · `ESTOP` · `EXTERNAL_TRIP` · `OPERATOR_ABORT`. Makes "how many runs
tripped on quorum this month" a query, not a text search.

### Layout snapshots

Captured at run start, stored per run, so replay reconstructs the room as it was.
Sensors get added, removed and moved between experiments; without a snapshot, an old
replay would draw against today's layout and be quietly wrong.

If a snapshot is missing (a run predating this feature, or a failed write), replay
draws with the current layout **and says so** rather than silently misplacing sensors.

---

## Recording integration

Run start is **coupled to recording by default, decoupled deliberately.**

Starting a run automatically starts DAQ recording using the experiment named in the
compose form. An **unrecorded test run** checkbox, off by default, warns when
checked — for sensor checks that shouldn't pollute the experiment record. Recording
can also be started standalone.

The safe path is the default; nobody has to invent an experiment name to check a
sensor is alive.

### Stage mapping

**Both automatic and manual.** The app watches the retained `state` topic and calls
`POST /stage` on each phase transition, naming stages `run{N}-leak`, `run{N}-hold`,
`run{N}-vent`, `run{N}-purge`. The operator can override or add a label mid-phase.

Explicit run numbering (rather than plain `leak`, letting DataAcquisition's collision
logic append `(2)`, `(3)`) makes run identity unambiguous in stage names.

Phase boundaries become directly queryable via `/history/experiment/stage` — which is
what makes "compare the decay curve across every run" a single query. The firmware is
the authority on phase boundaries, so auto-mapping is also more accurate than a
human typing what the firmware already knows.

### DataAcquisition endpoints used

| Endpoint | Use |
|---|---|
| `POST /experiments` | Create experiment |
| `POST /experiments/active` | Set active experiment for Kitchen |
| `POST /stage` | Set stage label on phase transition |
| `POST /recording` | Start/stop recording (`location: "Kitchen"`) |
| `GET /experiments`, `/experiments/{id}/stages` | Pickers |
| `GET /history/experiment` | Replay load (LTTB) |
| `GET /history/experiment/window` | Full-resolution zoom |
| `GET /conversions/{device}/{sensor}` | Display calibration provenance |
| `POST /locations` | Register `Kitchen` if absent (first-run setup) |

`Kitchen` is not currently a configured location in DataAcquisition. It is created on
first use via `POST /locations`; the kitchen app checks for it at startup. Until the
kitchen devices publish to `DataAcquisition/Kitchen/…`, there is nothing for the DAQ
to route, so this is a prerequisite for any recording or history work.

---

## Visualisation

### Room and sensors

Room geometry **hardcoded** as Three.js primitives — fixed civil geometry, changes
only on renovation. Sensor positions **config-driven** from `sensor_config` —
sensors are what actually churn. Both feed the same scene graph; rendering is
identical.

Sensor positions are entered numerically (X/Y/Z against the room's coordinate frame)
with a live preview. Not drag-and-drop: positions come off a tape measure, and
numeric entry is what preserves that precision.

### View modes

Two, toggled:

- **Sensors only** — each sensor a marker at its position, coloured by
  concentration. No interpolation, no invention. The trustworthy baseline.
- **Field** — interpolated concentration over a 3D grid, **with confidence fade**:
  opacity falls off with distance from the nearest sensor, so regions the sensors
  genuinely constrain look solid and guesswork looks thin. Sensor markers stay
  visible on top.

Keeping sensors-only one click away is itself a safeguard: if the field ever looks
implausible, it can be checked against raw values immediately.

**Hydrogen is buoyant and stratifies at the ceiling.** Interpolation weighting is
anisotropic (vertical distance compressed relative to horizontal) so the field does
not draw H2 pooling low. The anisotropy constant is a named tunable to be
sanity-checked against real runs.

### Colour scale

Piecewise, pinned to **absolute concentration — never auto-scaled** to current
min/max. Auto-scaling would paint a safe 0.2% room bright red, which trains people
to ignore the display.

| Range | % of ramp | Colours |
|---|---|---|
| 0–4 %v/v (0–100% LEL) | ~70% | `#0B3D91` → `#1B9AAA` → `#7FD858` → `#F2E635` → `#FF8A1F` |
| 4–20 %v/v | ~30% | `#FF8A1F` → `#E01B1B` → `#8E0B52` → `#F0A6FF` |
| ≥20 %v/v | — | saturates |

Fine resolution below LEL where the experiment lives; compressed but still readable
above, so 4%, 10% and 20% stay distinct. The ramp ends in pale magenta rather than
deeper red — past LEL the field should stop reading as "hot" and start reading as
*wrong*, in a colour with no other job in the interface.

Legend shows both %v/v and % LEL.

**The heatmap is always %v/v**, regardless of the plot's unit toggle, because its
scale is pinned to LEL.

### Plot modes

Time series supports, over the same fetched data:

- **Raw** — V / mA as measured (`raw_value`, `raw_unit`)
- **Converted** — ppm or %v/v (`value`, unit-toggled via the DAQ's `gas` scale)
- **Normalized (min-max)** — 0–1 over the visible window; shapes directly comparable
- **Baseline-relative** — each sensor minus its own pre-leak resting value
- **Z-score** — mean 0, SD 1 over the window

All five are a few lines each over one fetched series. DataAcquisition already stores
raw and converted per reading with `conv_id` provenance, so this needs no new
storage.

### Replay

Scrubber plus speed control, **0.05× to 5×**. Slow speeds are the interesting ones —
a cloud crossing LEL does so in seconds, and 0.05× makes that readable. Fast speeds
skim the ventilation tail.

**Resampling.** LTTB downsamples each sensor independently, so returned points land
on different timestamps per sensor. Correct for plotting, unusable for a heatmap,
which needs all sensors at one instant. Replay therefore resamples every sensor onto
a **common time grid** (linear interpolation between bracketing samples, held at the
ends) and the scrubber indexes that grid. Grid resolution adapts to run length. At
high zoom the app re-fetches the window at full resolution via
`/history/experiment/window`, matching DataAcquisition's own analysis behaviour.

**Only the room animates.** The time-series plot shows the whole run statically with
a moving playhead, so position is always seen in context.

---

## Interface

Three views, one small toggle in the header. Display mode is set once per screen, not
flipped during a run — small, but not buried in settings, because someone will need
to undo it on an unfamiliar machine.

### Design language

Derived from `webapp-style-guide.txt` (Industrial Dark), with deliberate departures
for a control room:

- **Palette** — the guide's surface ramp, plus a three-state semantic set:
  `#00C853` safe/permitted/online · `#FFB020` armed (validated, awaiting confirm,
  nothing flowing) · `#FF4D3D` gas flowing or danger. Amber earns its place because
  ARMED is genuinely neither safe nor live.
- **Green means safe and nothing else.** The guide uses `#00C853` for every active
  tab, primary button and heading. Here that would dilute the one cue that must read
  across a room. Chrome uses neutrals; green is reserved for status.
- **The green glow halo is dropped** from buttons and headings, kept **only** on
  status dots where a glow genuinely aids peripheral detection.
- **Typography** — JetBrains Mono promoted to the primary instrument face (every
  number, state name and timer; tabular figures keep a ticking counter from shifting
  layout). Inter for conversational text. Outfit dropped — three families is one too
  many here.
- **No universal uppercase.** The guide's tracked-out caps are slow to read;
  `FULLY_VENTILATING` must be legible in a quarter-second.
- **Flat type scale.** Two sizes carry almost everything: 13px interface text,
  `clamp(28px, 5vw, 44px)` for the few live values that matter. Hierarchy comes from
  position and colour, not six font sizes.
- **Motion budget spent entirely on one alarm.** Everything else is instant — no
  fade-ins, no card hovers, no page transitions. When concentration crosses LEL the
  field boundary pulses, and because nothing else moves, peripheral vision catches it.

### Control

```
┌───────────────────────────────────────────────────────────────────┐
│ ▌HYLAB  Kitchen          ● permit  ● daq  ● mqtt   [control ▾]    │
├──────────────────────────────────┬────────────────────────────────┤
│                                  │  LEAKING                 02:14 │
│                                  │  run 12 · standard 5s          │
│                                  │ ─────────────────────────────  │
│          3D ROOM                 │  peak        1.84 %v/v         │
│                                  │  delivered     312 mL          │
│      ○ sensors  ◈ field          │  fan            45 %           │
│                                  │  registers   ▣ ▣ ▢ ▣           │
│                                  │  ┌──────────────────────────┐  │
│  ░░▓▓██ 0 ─── 4 ─────── 20 %v/v  │  │          STOP            │  │
├──────────────────────────────────┴──┴──────────────────────────┴──┤
│  time series · all sensors · playhead                             │
└───────────────────────────────────────────────────────────────────┘
```

Three zones, never rearranged. The room fills the left two-thirds because spatial
truth is the point. The numeric rail is always in the same place, so the eye learns
one location per value. The plot spans the bottom as the time axis under the spatial
view.

**The stop button never moves, never scrolls out of view, and is never disabled while
gas can flow.** It is the only large element in the interface.

Left-aligned throughout; numbers right-aligned in their columns so magnitudes line up.

### Starting a run

A two-step sheet mirroring the firmware's own protocol:

1. **Compose** — pick a saved config (or edit one), name the run, choose or create
   the DAQ experiment. Unrecorded-test-run checkbox, off by default.
2. **Review the ack** — sends `start(spec)` and shows what the firmware sent back:
   the interpreted spec with **every clamped value highlighted against what was
   asked**, including the quorum threshold as both requested % and interpreted
   counts. Confirm is disabled until an ack arrives. A rejection shows the firmware's
   `StartRejectReason` and offers only "back".

That diff is the reason the ack exists. A raised threshold or capped hold is seen
before confirming, not afterwards in the record.

### Latched state

The numeric rail is replaced by the latch cause, the clear-air countdown, and an
**Acknowledge** button — disabled until sensors have read clear for the required
5 minutes, with remaining time shown so it does not look broken. Acknowledging is not
a shortcut past the hold: release needs the ack **and** the clear-air period.

### Live rewind

The Control view can be scrubbed back during a live run. Because a past moment must
never be mistaken for the present:

- Amber hatched border around the room — a pattern used nowhere else, so it cannot be
  confused with a status colour
- Persistent bar: `REPLAY −0:34` plus the absolute timestamp
- **Return to live** as a solid button, not a small link
- The numeric rail keeps showing **live** values, visually separated and labelled
- **Auto-return to live on any danger latch or phase change** — rewinding is a
  convenience; a hydrogen event revokes it

Stop stays live and unchanged throughout; it commands the present regardless of what
the room displays.

### Analysis

Inverted proportions — plots dominant, room secondary but scrubbable.

```
┌───────────────────────────────────────────────────────────────────┐
│ experiment ▾   run ▾   [raw|converted|min-max|baseline|z-score]   │
├─────────────────────────────────────────┬─────────────────────────┤
│              PLOTS                      │      3D ROOM            │
│      (sensors, stage bands, playhead)   │    (follows playhead)   │
├─────────────────────────────────────────┴─────────────────────────┤
│ ├────────●──────────────────┤  00:47/04:12   0.05× … 1× … 5×      │
│  leak  │  hold      │  vent                                       │
└───────────────────────────────────────────────────────────────────┘
```

Stage bands under the timeline turn the firmware's phases into direct navigation —
click `hold` to jump there.

### Display

For a wall-mounted or standing screen. Room nearly fullscreen, values oversized, no
chrome.

**Display mode genuinely disables the command path, not just the buttons.** Start is
refused server-side, so a stray touch or a tab-to-invisible-button cannot begin a
leak. **Stop and Acknowledge remain available** — a screen in the room is exactly
where someone standing next to the hazard would reach for stop. Display mode blocks
*starting* things, never *stopping* them. Both are sized for deliberate use, not
accidental brush.

---

## Testing

- **Unit** — colour-scale mapping at range boundaries (0, 4%, 20%, above);
  interpolation weighting including the anisotropy; replay resampling onto a common
  grid, including sensors with gaps and runs shorter than one grid step; the five
  plot-mode transforms.
- **Integration** — against a fake broker and a stubbed DataAcquisition: two-phase
  start including ack-mismatch and rejection paths; DAQ-down blocks start; DAQ dying
  mid-run does not interrupt; display mode refuses start server-side but permits
  stop; stage transitions produce the expected `POST /stage` calls.
- **Manual** — the visual claims (does the field look right, is the stop button
  reachable, does the alarm pulse catch peripheral vision) are judged on hardware.

---

## Deliberately deferred

**Batch pipelines** — running "5 of config A, then 10 of config B" unattended, as
`Backbone/WebApp` does (`backend/pipeline.py`, `src/components/MetaPipelinePanel.tsx`).

Deferred because it raises a safety-policy question that does not need answering yet:
**does the operator confirm each run, or does starting a pipeline authorize all of
them?** The firmware's two-phase confirm exists so a human sees the interpreted spec
before gas flows; a pipeline that auto-confirms 15 runs is one authorization for 15
hydrogen releases over hours, possibly with nobody in the building.

This design makes the addition cheap without deciding that question:

- **Run configs are first-class saved objects** from day one, not form state
- **`start_run(config_id, experiment_id, run_name)` is a single entry point** — a
  sequencer would be a loop around the same call the UI makes
- **Run records store how a run ended** (`outcome`, `latch_cause`), so a sequencer
  can distinguish "finished cleanly" from "tripped" — Backbone's runner only waits
  for idle, which is insufficient here because a latched stop also ends in
  FULLY_VENTILATING and holds pending human ack
- **`run_number` is a real field**, so repeats number themselves without collision

When added, it becomes two tables (`pipelines`, `pipeline_steps`), a background
sequencer modelled on Backbone's, a status endpoint and one panel, without touching
the run path. Notes for that work:

- A latch must **halt the queue**, not be waited out
- Any permit loss, DAQ recording failure, or **ack differing from what was queued**
  should stop the queue and ask — the automated equivalent of a human noticing the
  ack looks wrong
- One DAQ experiment per pipeline (so "compare all 15 decay curves" is one query)

**Also deferred:** export from this app (DataAcquisition's xlsx export covers it),
and multi-room support (this app is the kitchen's).

---

## Open questions

1. **Room dimensions and sensor count** — needed before the 3D model can be built.
   Blocks the visualisation work only; control and data layers can proceed.
2. **Anisotropy constant** for buoyancy-weighted interpolation — needs a real run to
   calibrate against. Ships with a documented estimate and a settings entry.
3. **`KitchenControl/{deviceId}/sensors/power`** — the firmware plan lists this
   retained topic as still needing its name reconciled against the CM7 DAQ firmware.
   If the app is to expose sensor power, that name must settle first.
4. **Operator identity** — `runs.operator` assumes some notion of who is logged in.
   If auth is a single shared token, this becomes a free-text field on the compose
   form instead.
