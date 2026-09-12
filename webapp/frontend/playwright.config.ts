/**
 * playwright.config.ts — E2E tests against the REAL running stack, per the
 * decision to use Playwright for visual/functional verification (the
 * sensor editor, zeroing, and later the Part 4.2/4.3 scenario checks)
 * instead of a component-testing library.
 *
 * Unlike `npm run dev`, this does NOT start the webapp for you — the webapp
 * needs an MQTT broker + (for anything sensor/run related) sim/ + MySQL to
 * behave like a real session, and those are separate long-lived processes
 * this config has no business owning. See e2e/README.md for the one-time
 * setup and the exact commands to start each piece before running
 * `npm run e2e`.
 *
 * webServer below is scoped to just the KNOWN-cheap step (serving already-
 * built frontend assets via `npm run preview` if the webapp itself is
 * already up on BASE_URL) — see e2e/README.md for why the webapp itself is
 * NOT auto-started here.
 */
import { defineConfig, devices } from '@playwright/test'

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:5010'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // tests share one running webapp + one sim instance's state
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: 'list',
  timeout: 30_000,
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
