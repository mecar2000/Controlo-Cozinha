/**
 * e2e/zeroing.spec.ts — "Zero in clean air" (problems.txt: "cant i just see
 * it from when there is no hydrogen what's the baseline for each sensor and
 * get the adjustment from there?"). Requires the sim GUI running (pins a
 * channel to a stable value via its REST API — see e2e/sim.ts) so the
 * capture averages a KNOWN value instead of the random walk's drift.
 *
 * Written before SensorPanel.tsx gains the zero-in-clean-air control, per
 * the project's test-first workflow.
 */
import { expect, request, test } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'

import { simClear, simSpike } from './sim'

const DAQ_NUM = 1 // KITCHEN-DAQ-1
const CHANNEL = 0 // H2-1
const STABLE_MA = 4.05 // a plausible clean-air reading with a slight offset

// Part 1 removed auto-seeded placeholder sensors, so H2-1 no longer exists
// by default — create it here wired to the sim's KITCHEN-DAQ-1/H2-1 identity
// (matching DAQ_NUM/CHANNEL above) and archive it again once the suite ends.
const SENSOR_KEY = 'e2e-zeroing-h2-1'

test.describe('zero in clean air', () => {
  let api: APIRequestContext

  test.beforeAll(async () => {
    api = await request.newContext({
      baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5010',
    })
    const sensorData = {
      label: 'H2-1',
      x: 0,
      y: 0,
      z: 0,
      daq_device_id: 'KITCHEN-DAQ-1',
      daq_sensor_name: 'H2-1',
    }
    const res = await api.put(`/api/sensors/${SENSOR_KEY}`, { data: sensorData })
    if (res.status() === 409) {
      // Left archived by a previous run's afterAll — upsert refuses to
      // revive an archived key directly, so restore first, then retry.
      await api.post(`/api/sensors/${SENSOR_KEY}/restore`)
      await api.put(`/api/sensors/${SENSOR_KEY}`, { data: sensorData })
    }
  })
  test.afterAll(async () => {
    await api.delete(`/api/sensors/${SENSOR_KEY}`).catch(() => {})
    await api.dispose()
  })

  test.beforeEach(async () => {
    await simSpike(DAQ_NUM, CHANNEL, STABLE_MA) // pin the channel so the capture is deterministic
  })
  test.afterEach(async () => {
    await simClear(DAQ_NUM, CHANNEL)
  })

  test('capturing 100 samples and applying writes the averaged value as the new offset', async ({
    page,
  }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'edit sensors' }).click()
    await page.getByText('H2-1', { exact: false }).first().click()

    await page.getByRole('button', { name: /zero in clean air/i }).click()
    // Progress UI: some indication of N/100 collected, then a completion state.
    await expect(page.getByText(/100\s*\/\s*100|done|ready to apply/i)).toBeVisible({
      timeout: 60_000, // 100 samples at 2 Hz sim publish rate is ~50s
    })
    await page.getByRole('button', { name: /^apply$/i }).click()

    await expect(page.getByText(new RegExp(STABLE_MA.toFixed(2)))).toBeVisible()
  })

  test('starting a run is blocked while a zeroing capture is in progress', async ({ page, request }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'edit sensors' }).click()
    await page.getByText('H2-1', { exact: false }).first().click()
    await page.getByRole('button', { name: /zero in clean air/i }).click()

    // The Start button (or an equivalent inline control, per the Part 3
    // inline-config decision) must be disabled or explain why while active.
    const startResp = await request.post('/api/runs/start', {
      data: { config_id: 1, run_name: `e2e-blocked-${Date.now()}` },
      failOnStatusCode: false,
    })
    expect(startResp.status()).toBe(409)
    const body = await startResp.json()
    expect(body.error.toLowerCase()).toContain('zero')

    await page.getByRole('button', { name: /^cancel$/i }).click() // clean up the session
  })

  test('an implausibly wide spread is refused rather than silently averaged', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'edit sensors' }).click()
    await page.getByText('H2-1', { exact: false }).first().click()
    await page.getByRole('button', { name: /zero in clean air/i }).click()

    // Inject a spike mid-capture to widen the spread past what clean air
    // should ever produce.
    await page.waitForTimeout(2_000)
    await simSpike(DAQ_NUM, CHANNEL, 12.0)
    await page.waitForTimeout(2_000)
    await simSpike(DAQ_NUM, CHANNEL, STABLE_MA)

    await expect(page.getByText(/100\s*\/\s*100|done|ready to apply/i)).toBeVisible({ timeout: 60_000 })
    await page.getByRole('button', { name: /^apply$/i }).click()
    await expect(page.getByText(/spread|too wide|not.*clean/i)).toBeVisible()
  })

  test('manual override is still available without running a capture', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'edit sensors' }).click()
    await page.getByText('H2-1', { exact: false }).first().click()

    await page.getByLabel(/raw min/i).fill('4.3')
    await page.getByRole('button', { name: /save calibration/i }).click()
    await expect(page.getByText(/saved/i)).toBeVisible()
  })
})
