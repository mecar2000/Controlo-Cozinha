# Frontend design pass — state, decisions, and what's left

Working doc for the visual redesign of the Kitchen H₂ Control frontend.
Written 2026-09-12, paused mid-pass at the user's request.

Nothing here is half-applied: `npx tsc --noEmit` is clean and all 82 vitest
tests pass as of the pause. The unfinished work is *additional* changes, not
repairs to what landed.

---

## The brief

> Evaluate frontend of webapp. It should be clean simple for lab environment
> but visually appealing. Use images in assets, doesn't need to use both,
> they are the company logos. Use `#00C853` as main color to accentuate.

Two decisions the user made when asked:

1. **Green policy — "split by intensity".** Full-chroma `#00C853` stays
   exclusive to live safety state. Chrome gets the same hue at lower chroma.
2. **Scope — "also rework the views"**, not just tokens and chrome.

## The central design problem

`index.css` already carried a written rationale *rejecting* `#00C853` as
chrome: "Green means safe and NOTHING else." That rule is sound — the safe
cue has to read across a room — and it collided head-on with the brief.

Resolution: green appears in two roles that **never share a glance**.

| Role | Tokens | Where it lives |
|---|---|---|
| Semantic (safe state) | `--color-safe` | rail, status dots, 3D field |
| Chrome (identity/affordance) | `--color-brand`, `--color-brand-dim`, `--color-brand-wash` | header bar, focus rings, toggles, form buttons |

`--color-brand` and `--color-safe` are the same hex on purpose. They are never
adjacent, so the operator is never asked to disambiguate two greens.

---

## What landed

### Tokens (`src/index.css`)
- Added `--color-brand` / `-dim` (`#1f7d4c`) / `-wash` (`#0c2a1b`), with the
  two-roles rationale written into the file header.
- Surfaces darkened one step (`--color-bg: #080a0c`, `--color-panel: #11151a`,
  `--color-raised: #1a2029`, `--color-hairline: #252d38`) so the 3D canvas
  floats in front of the chrome instead of sitting in its plane.
- **Inter → Archivo.** Two families with strict roles: *mono = machine
  reported* (any value off a sensor or out of the firmware), *Archivo =
  language* (prose, labels, navigation, typed input). The face tells you the
  provenance.
- New `--text-label: 11px` step, so a label can genuinely recede below the
  value it names rather than only being greyer.
- Focus ring: `--color-ink` → `--color-brand-dim`. Ink collided with active
  text; this separates from both ink and the full-chroma safe cue.
- New utilities: `.seg-on` (selected segment — wash fill *and* border, a
  region rather than a hairline, because a 2px line disappears when a
  wall-mounted screen is read at an angle), `.field-label`, `.rail-heading`,
  `.btn-primary`.
- `.rail-label` now Archivo at `--text-label`; prose `input`/`select` take
  Archivo while `.field-num` keeps tabular mono for digits.
- `.btn-primary` is deliberately **outlined + washed, never solid-filled** —
  nothing in the interface may compete with the one solid red STOP block.

### Assets
- `public/hylab-mark.png` (96px, 10KB) and `public/favicon.ico`
  (multi-resolution 16→256), both generated from
  `webapp/assets/hylab-logo_circle_up.png`.
- Chose the **circular mark over the full wordmark**: at header size the
  wordmark's text renders ~9px and goes muddy, while the mark stays crisp at
  16px and its H-in-a-circle motif happens to echo the H₂ subject.
- `index.html` gained the favicon, `apple-touch-icon`, `theme-color`, and a
  real title (`Kitchen H₂ Control — HyLab`).
- An SVG trace of the mark was attempted and **abandoned** — the geometry
  (ring gap, node hole, arc depth) didn't match the original closely enough
  to be worth the fidelity loss. Use the PNG.

### Components
- **`Header.tsx`** — logo mark + "Kitchen H₂ Control" lockup replaces the
  bold-mono `HYLAB` text. Wordmark deliberately dropped: the mark already
  says HyLab, so repeating it in type is the accessory worth removing. Tabs
  are full-bar height, sentence case in Archivo, active one carrying a wash
  region plus a `--color-brand` underline. Dot labels at `--text-label`.
- **`ViewModeToggle.tsx`** — labels changed `sensors|field` → **`Points|Cloud`**.
  "Sensors" collided with the adjacent *Sensors* button that opens the editor,
  and the new names say what the operator will actually see. `aria-label={m}`
  retains the old accessible names so existing E2E matchers still pass.
- **`ControlView.tsx`** — `Sensors`/`Configs` sentence case with brand-dim
  hover; a `w-3` spacer separates the draw-mode toggle from the two buttons
  that open editors, so the row stops reading as one four-item switch.
- **`NumericRail.tsx`** — labels recede to Archivo 11px, values stay mono;
  run label selectable; register pips labelled in Archivo.
- **`DisplayView.tsx`** — labels to Archivo `--text-label`, units via `Unit`.
- **`AnalysisView.tsx`** — `Run` label, plot-mode buttons use `.seg-on`, and
  the `timestamp · outcome` middle-dot join was split into two spans (a
  middle-dot meta string is a templated tell, and it was joining two
  different kinds of thing).
- **`RunComposer.tsx`** — `.rail-heading`, `.field-label`, `.btn-primary` on
  Start, and `"— choose one —"` → `"Choose an experiment"`.
- **`Legend.tsx`** — Archivo caption with mono spans for the numerals.
- **New `components/Unit.tsx`** — one component for unit suffixes, since
  three hand-copied `text-label text-ink-dim` pairs would drift apart.

---

## Screenshot harness (works — this was hard-won)

`scratchpad/shoot.py` drives the app through five states with a fully stubbed
API. **No broker, no MySQL, no DataAcquisition needed.** Run:

```bash
cd webapp/frontend && npx vite --port 5199 --strictPort   # in one shell
./.venv/Scripts/python.exe <scratchpad>/shoot.py <outdir>  # in another
```

Playwright browsers **are** installed and Chromium launches fine (this
contradicts an older note claiming the download was blocked; memory updated).

Three traps it now documents in-file, each of which silently produced a blank
or crashed page:

1. **Never use a bare `**/api/**` route pattern.** It also matches Vite's own
   module graph (`/src/api/client.ts`), which then gets served `{}` as
   `application/json` — the app never boots. Root-anchor everything:
   `f"{BASE}/api/..."`.
2. **Playwright consults routes in reverse registration order** (last
   registered wins), so broad patterns must be registered *first* and specific
   ones *last*. Getting this backwards made `/api/**` shadow `/api/sensors`.
3. **A single `*` doesn't match across `?`.** `/api/sensors*` missed the real
   `/api/sensors?enabled_only=1`, which fell through to the catch-all and took
   the App down with `positions.map is not a function`
   ([useKitchen.ts:82-86](src/hooks/useKitchen.ts#L82-L86)). Use `**` for
   anything query-stringed.
4. Chromium needs `--use-gl=angle --use-angle=swiftshader
   --enable-unsafe-swiftshader`, or the WebGL room composites as a black
   rectangle over the entire viewport.

---

## What's left

### 1. E2E specs need updating for renamed labels (REQUIRED — will fail)
Three assertions break on copy I deliberately changed. The specs are correct
to assert on names; the names moved.

| File | Assertion | Fix |
|---|---|---|
| [e2e/cursor.spec.ts:36](e2e/cursor.spec.ts#L36) | `name: 'configs'` | → `'Configs'` |
| [e2e/cursor.spec.ts:42](e2e/cursor.spec.ts#L42) | `name: 'control', exact: true` | → `'Control'` |
| [e2e/sensor-editor.spec.ts](e2e/sensor-editor.spec.ts), [zeroing.spec.ts](e2e/zeroing.spec.ts) | `name: 'edit sensors'` | unchanged — `aria-label="edit sensors"` was kept |

`ViewModeToggle`'s `name: /field|sensors/i` still passes via the retained
`aria-label`. **These specs have still never been run** — they need the live
infrastructure in `e2e/README.md`.

### 2. Unreviewed screenshots
`control-leaking`, `control-latched`, `display-leaking`, `analysis-empty` and
`control-narrow` were all generated successfully but only `control-waiting`
was actually examined. Look at the rest before calling the pass done —
especially `control-narrow` (900px) for header/rail crowding.

### 3. Known open design issues
- **The legend ramp is a full rainbow** (blue→green→yellow→red→magenta). Its
  low end is blue-green, which competes with the semantic safe cue, and five
  hues is a lot of colour for a lab instrument. Worth reducing to a
  single-hue-plus-alarm ramp, but it's a real safety-affecting decision
  (`lib/colorScale.ts` is unit-tested and the LEL boundary is pinned to it) —
  ask before touching.
- **Empty plot area**: the 180px `LivePlot` strip shows only "Waiting for
  sensor readings." while WAITING. Consider collapsing it, or showing the
  axis frame so it reads as ready rather than broken.
- **The room renders small** in a large empty canvas at 1600px. Camera
  framing in `RoomScene` could bring it forward; not attempted.
- **Components not yet touched** in this pass: `SensorPanel.tsx`,
  `ConfigEditor.tsx`, `ReviewModal.tsx`, `SpecDiff.tsx`, `Scrubber.tsx`,
  `TimeSeries.tsx`, `LatchPanel.tsx` (partially — its heading should become
  `.rail-heading`), `StopButton.tsx` (intentionally left alone).

### 4. Not done, deliberately
- No `ALL-CAPS` tracked eyebrow labels — the existing rationale against them
  (`FULLY_VENTILATING` must read in a quarter-second) is sound.
- No new motion. The whole motion budget stays spent on the LEL alarm.
- Phase names still render exactly as the firmware sends them
  (`FULLY_VENTILATING`), matching logs, MQTT topics and run records.

### 5. Verify before shipping
```bash
cd webapp/frontend
npx tsc --noEmit      # clean at pause
npx vitest run        # 82 passing at pause
npm run build         # NOT re-run since the Archivo switch — do this
```
The Google Fonts import changed (Inter → Archivo); confirm the production
build still resolves it, and consider self-hosting the font for a lab machine
that may be offline.
