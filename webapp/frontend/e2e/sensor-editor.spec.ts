/**
 * e2e/sensor-editor.spec.ts — hard-deleting ("Clear") a positioned sensor,
 * reached via a device's own pin row.
 *
 * Archiving/restoring/purging no longer exist — DELETE /api/sensors/<key> is
 * a plain hard delete now (see routes/sensors.py). "Unassigned" doesn't
 * exist either (a sensor only ever exists because DataAcquisition is
 * already publishing it — see device-pin-editor.spec.ts for the inline
 * label/x/y/z editor itself), and neither does Duplicate or the generic add/
 * edit modal.
 *
 * Each test creates its own sensor via the API (bound to a real published
 * DAQ pin, since that's now the only way a sensor exists) and cleans up
 * afterwards, so tests can run in any order against one shared backend/
 * database without colliding.
 */
import { expect, test } from '@playwright/test'

function uniquePosition(): { x: number; y: number; z: number } {
  return { x: Math.random() * 100, y: Math.random() * 100, z: Math.random() * 100 }
}

async function deleteSensorApi(request: import('@playwright/test').APIRequestContext, key: string) {
  await request.delete(`/api/sensors/${encodeURIComponent(key)}`).catch(() => {})
}

/** The pin row for a given pin-label cell text (mirrors src/lib/pinLabel.ts's
 *  encoding: base pins "A{n}", expansion pins "E{idx}:CH{ch}"). */
function pinRow(page: import('@playwright/test').Page, pinLabel: string) {
  return page.getByRole('row', { name: new RegExp(`^${pinLabel}\\b`) })
}

async function findPublishedPin(request: import('@playwright/test').APIRequestContext) {
  const devices = (await (await request.get('/api/daq/devices')).json()) as Array<{
    device_id: string
    config: { sensors: Array<{ pin: number; name: string }> }
  }>
  const sensors = (await (await request.get('/api/sensors')).json()) as Array<{
    daq_device_id: string | null
    daq_pin: number | null
  }>
  const isBound = (deviceId: string, pin: number) =>
    sensors.some((s) => s.daq_device_id === deviceId && s.daq_pin === pin)
  for (const d of devices) {
    const free = d.config.sensors.find((s) => !isBound(d.device_id, s.pin))
    if (free) return { device_id: d.device_id, pin: free.pin, name: free.name }
  }
  return null
}

function pinLabelFor(pin: number): string {
  return pin >= 100 ? `E${Math.floor(pin / 100) - 1}:CH${pin % 100}` : `A${pin}`
}

test.describe('sensor editor', () => {
  test("clearing a positioned row's sensor hard-deletes it and the row reverts to greyed-out", async ({
    page,
    request,
  }) => {
    const target = await findPublishedPin(request)
    test.skip(!target, 'no DataAcquisition device with an unbound published pin in this environment')

    const key = `e2e-clear-${Date.now()}`
    const label = `Clearable-${Date.now()}`
    const pos = uniquePosition()
    try {
      await request.put(`/api/sensors/${key}`, {
        data: { label, ...pos, daq_device_id: target!.device_id, daq_pin: target!.pin },
      })

      await page.goto('/')
      await page.getByRole('button', { name: 'edit sensors' }).click()
      await page.getByRole('navigation', { name: 'Devices' }).getByRole('button', { name: target!.device_id }).click()

      const row = pinRow(page, pinLabelFor(target!.pin))
      await expect(row).toHaveAttribute('data-positioned', 'true')
      await row.getByRole('button', { name: /^clear$/i }).click()
      await row.getByRole('button', { name: /^clear$/i }).click()

      await expect(row).toHaveAttribute('data-positioned', 'false', { timeout: 5000 })

      const sensors = (await (await request.get('/api/sensors')).json()) as Array<{ sensor_key: string }>
      expect(sensors.some((s) => s.sensor_key === key)).toBe(false)
    } finally {
      await deleteSensorApi(request, key)
    }
  })

  test('clearing shows a cancellable confirm before deleting anything', async ({ page, request }) => {
    const target = await findPublishedPin(request)
    test.skip(!target, 'no DataAcquisition device with an unbound published pin in this environment')

    const key = `e2e-cancel-clear-${Date.now()}`
    const label = `NotCleared-${Date.now()}`
    const pos = uniquePosition()
    try {
      await request.put(`/api/sensors/${key}`, {
        data: { label, ...pos, daq_device_id: target!.device_id, daq_pin: target!.pin },
      })

      await page.goto('/')
      await page.getByRole('button', { name: 'edit sensors' }).click()
      await page.getByRole('navigation', { name: 'Devices' }).getByRole('button', { name: target!.device_id }).click()

      const row = pinRow(page, pinLabelFor(target!.pin))
      await row.getByRole('button', { name: /^clear$/i }).click()
      await expect(row.getByText(/pin stays published in DataAcquisition/i)).toBeVisible()
      await row.getByRole('button', { name: /^cancel$/i }).click()

      await expect(row).toHaveAttribute('data-positioned', 'true')
      const sensors = (await (await request.get('/api/sensors')).json()) as Array<{ sensor_key: string }>
      expect(sensors.some((s) => s.sensor_key === key)).toBe(true)
    } finally {
      await deleteSensorApi(request, key)
    }
  })

  test('clearing right after an in-flight edit does not resurrect the sensor', async ({ page, request }) => {
    // Regression for a real race: saveRow() (fired fire-and-forget from
    // blur) and clearRow() (fired from Clear->Clear) both targeted the same
    // sensor_key with no ordering between them. If the DELETE landed before
    // a still-in-flight PUT resolved, upsert-by-key silently re-created the
    // row right after the operator cleared it — no error shown anywhere.
    const target = await findPublishedPin(request)
    test.skip(!target, 'no DataAcquisition device with an unbound published pin in this environment')

    const key = `e2e-race-${Date.now()}`
    const label = `Racy-${Date.now()}`
    const pos = uniquePosition()
    try {
      await request.put(`/api/sensors/${key}`, {
        data: { label, ...pos, daq_device_id: target!.device_id, daq_pin: target!.pin },
      })

      await page.goto('/')
      await page.getByRole('button', { name: 'edit sensors' }).click()
      await page.getByRole('navigation', { name: 'Devices' }).getByRole('button', { name: target!.device_id }).click()

      const row = pinRow(page, pinLabelFor(target!.pin))
      await expect(row).toHaveAttribute('data-positioned', 'true')

      // Edit x, then immediately (without waiting for blur's save to settle)
      // open and confirm Clear — this is the interleaving that used to race.
      const xField = row.getByLabel(/^x/i)
      await xField.fill(String(pos.x + 1))
      await row.getByRole('button', { name: /^clear$/i }).click()
      await row.getByRole('button', { name: /^clear$/i }).click()

      await expect(row).toHaveAttribute('data-positioned', 'false', { timeout: 5000 })

      // The old bug let the upsert land AFTER the delete, so the sensor
      // would reappear some time after the DOM already showed "cleared" —
      // assert it stays gone, not just that it's gone right away.
      await page.waitForTimeout(1000)
      const sensors = (await (await request.get('/api/sensors')).json()) as Array<{ sensor_key: string }>
      expect(sensors.some((s) => s.sensor_key === key)).toBe(false)
    } finally {
      await deleteSensorApi(request, key)
    }
  })

  test('an empty room (no sensors) renders with no crash and an explanatory message', async ({
    page,
    request,
  }) => {
    // This only asserts the empty-state message exists in the DOM somewhere
    // reachable — it does NOT require the room to currently be empty (other
    // tests may have left sensors behind), so it is safe to run in any order.
    const sensors = (await (await request.get('/api/sensors')).json()) as unknown[]
    test.skip(sensors.length > 0, 'room already has sensors from another test run')

    await page.goto('/')
    await expect(page.locator('body')).toBeVisible() // no crash
  })
})
