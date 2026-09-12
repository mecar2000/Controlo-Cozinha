# E2E tests (Playwright)

These drive the REAL running stack in a real browser — chosen over a
component-testing library (React Testing Library etc.) so the sensor editor,
zeroing, and later the Part 4.2/4.3 scenario checks are verified against
actual behaviour end to end, not a simulated DOM.

## One-time setup

```
cd webapp/frontend
npm install
npx playwright install chromium
```

(`npx playwright install` downloads a Chromium build from
`cdn.playwright.dev` — if that times out from a sandboxed/restricted network,
run it from an unrestricted machine/connection instead. This step could not
be completed from inside this session's tool environment, which is why these
tests are written but not yet run.)

## Before every `npm run e2e`

Playwright does **not** start any of these for you — they're long-lived
processes this config has no business owning (a broker/PLC/DAQ stack far
outlives a test run). Start each of these first, in order, each in its own
terminal:

1. **MQTT broker** (any broker works — e.g. Mosquitto on the default port
   1883). Nothing here starts one.
2. **The simulator's GUI**, from `sim/`:
   ```
   python gui_app.py --host localhost --port 1883 --device-id KITCHEN-01
   ```
   Stands in for the real PLC + kitchen H2 sensors + both remote DAQ boxes.
   Its REST API (default `http://localhost:5050`) is what `e2e/sim.ts` uses
   to pin sensor readings deterministically — see `sim/README.md` for the
   full command reference.
3. **DataAcquisition** (the historian), reachable at whatever `DAQ_BASE_URL`
   the webapp's `.env` points to. Calibration writes (zeroing, the
   calibration form) need this reachable; most sensor-editor tests do not.
4. **MySQL**, per the webapp's `.env` (`DB_SERVER`/`DB_NAME`/`DB_USER`/
   `DB_PASSWORD`).
5. **The webapp itself**, from `webapp/`:
   ```
   python server.py
   ```
   Builds the frontend if stale and serves it at `http://localhost:5010` —
   the default `baseURL` in `playwright.config.ts`.

Then, from `webapp/frontend`:

```
npm run e2e         # headless
npm run e2e:ui      # Playwright's interactive UI mode, useful while writing/debugging
```

## Overriding URLs

- `E2E_BASE_URL` — the webapp (default `http://localhost:5010`)
- `E2E_SIM_URL` — the sim GUI's REST API (default `http://localhost:5050`)

## Test data / isolation

Tests create sensors with a unique, timestamped `sensor_key` and archive
them again in a `finally`/`afterEach` block, so the suite is safe to run
repeatedly against one shared database without accumulating junk or
colliding between runs. They do NOT reset the database between tests —
avoid asserting on "the room has exactly N sensors"; assert on the specific
sensor a test created instead.
