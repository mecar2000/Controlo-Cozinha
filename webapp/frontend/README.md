# frontend/ — Kitchen H2 Control operator interface

React + TypeScript + Vite + Tailwind v4 + Three.js. Implements the interface
specified in
[`docs/superpowers/specs/2026-09-07-kitchen-webapp-design.md`](../../docs/superpowers/specs/2026-09-07-kitchen-webapp-design.md).

## Running it

```sh
npm install

npm run dev      # dev server on :5173, proxies /api to Flask on :5010
npm run build    # production build into dist/, which Flask serves at :5010
npm test         # unit tests for the four lib/ modules
npm run typecheck
```

For `npm run dev` you also need the backend running (`python server.py` in
`webapp/`). Set `VITE_BACKEND` to point at a different origin, and
`VITE_DASHBOARD_TOKEN` if the backend has `DASHBOARD_TOKEN` set.

## The three views

| View | Proportions | Purpose |
|---|---|---|
| **Control** | Room 2/3, numeric rail fixed 340px, plot across the bottom | Run a leak and watch the room |
| **Analysis** | Plots dominant, room secondary and scrubbable | Replay a past run, compare decay curves |
| **Display** | Room near-fullscreen, values oversized, no chrome | Wall-mounted or standing screen |

**Stop never moves, never scrolls out of view, and is never disabled while
gas can flow.** It is the only large element in the interface and sits
outside every scroll container. Display mode disables *starting* a run
server-side; stop and acknowledge stay available in all three views.

## Design decisions worth knowing

These depart from `docs/webapp-style-guide.txt` (Industrial Dark) on purpose,
per the design spec:

- **Green means safe and nothing else.** The style guide uses `#00C853` for
  every active tab, primary button and heading; here that would dilute the
  one cue that must read across a room. Chrome is neutral. The semantic set
  is `--safe` / `--armed` (validated, awaiting confirm, nothing flowing) /
  `--live` (gas flowing or danger).
- **The green glow halo survives only on status dots**, where a glow
  genuinely aids peripheral detection.
- **JetBrains Mono is the primary face**, not an accent for numbers. Inter is
  used for prose only (`.prose-text`). Tabular figures throughout, so a
  ticking counter never reflows its layout.
- **No universal uppercase.** `FULLY_VENTILATING` must be legible in a
  quarter-second, and tracked-out caps are slow to read. State names render
  exactly as the firmware sends them — one vocabulary across the UI, the
  logs, and the run record.
- **The entire motion budget is one alarm.** When any sensor reads at or
  above LEL, the field breathes. Nothing else moves — no card hovers, no
  section fades, no page transitions — so peripheral vision catches that and
  only that. `prefers-reduced-motion` is respected.

## Layout

```
src/
  api/          typed client + wire types for every backend route
  hooks/        polling, and the live sensor/status join
  lib/          the tested logic (see below)
  components/
    room/       3D scene, geometry, sensor markers, field, legend
    control/    numeric rail, stop, latch panel
    start/      two-step start sheet and the ack diff
    plot/       live rolling plot, analysis time series, scrubber
  views/        ControlView, AnalysisView, DisplayView
```

### `lib/` — the part with tests (74 of them)

| Module | Why it is tested |
|---|---|
| `colorScale.ts` | Pinned to absolute concentration, never auto-scaled. Boundaries at 0, LEL (4 %v/v), 20 %v/v and above. |
| `interpolation.ts` | Anisotropic weighting — vertical distance compressed, so the field does not draw buoyant H2 pooling low. Plus the confidence fade. |
| `resample.ts` | LTTB returns each sensor on its own timestamps; the heatmap needs one instant. Covers gaps and runs shorter than one grid step. |
| `transforms.ts` | The five plot modes. |
| `roomGeometry.ts` | Real measured geometry (below). No tests of its own; asserted through `interpolation.test.ts`. |

## Room geometry

`lib/roomGeometry.ts` holds the kitchen's real measured dimensions (tape
measure, 2026-09-07) — 2.859 × 2.341 × 2.560 m, with the lower cabinet, top
cabinet, extraction hood, water heater, door and window all positioned
exactly. Room geometry is hardcoded because it changes only on renovation;
sensor positions live in the database because sensors churn.

**Coordinate frame:** origin at the floor corner where the back wall (the
2.859 m equipment wall) meets the left wall. `x` runs along the back wall,
`y` away from it into the room, `z` up. Sensor X/Y/Z in `sensor_config` use
this same frame, and the room's own frame is Z-up while Three.js is Y-up —
`RoomScene` rotates once so every child works in measured metres.

`isInsideSolid()` is the check to run before accepting a sensor position: a
sensor inside a cabinet has no air around it and would never read anything.

## Field rendering

The field is drawn as a stack of 64 horizontal textured slices, not a voxel
grid. Two earlier approaches were tried and rejected, and are worth not
repeating:

- **Instanced cubes with additive blending** — the accumulation saturates to
  white within a few cells, destroying the colour information the pinned LEL
  scale exists to convey.
- **Instanced cubes with alpha blending** — a regular lattice of discrete
  boxes produces strong moiré starbursts wherever the rows align with the
  view axis. On a safety display that is a rendering artifact reading as
  structure in the data.

Horizontal slices suit the problem: hydrogen stratifies, so the interesting
gradient is vertical, and bilinear texture filtering gives a smooth
continuous field for free.

## Known gaps

- **The anisotropy constant (0.35) is an unverified estimate**, as is
  `CONFIDENCE_RANGE_M` (2.2 m, sized against this room's sensor spacing).
  Both need a real run to calibrate against — see the design spec's open
  question 2.
- **Placeholder sensor positions.** The six seeded positions in
  `app/db/sensor_config.py` sit in air beside the fixtures they are named
  for, but they are guesses. Replace them with tape-measure values.
- **No sensor-position editor yet.** `PUT /api/sensors/<key>` exists and the
  client wraps it; the UI for numeric X/Y/Z entry with live preview is not
  built.
- **No per-sensor threshold config screen.** The backend mirrors the
  firmware's clamped threshold table into `state.get_last_config_ack()` but
  exposes no route for it, so `SpecDiff` shows interpreted quorum counts only
  when an ack carries them.
- **Live rewind is deliberately absent.** Control is strictly a live view.
  Replay of past runs lives in Analysis.
