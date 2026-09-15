/**
 * e2e/device-pin-editor.spec.ts — the device pane's inline pin editor.
 *
 * Replaces plc-commissioning.spec.ts entirely. Pin commissioning (choosing a
 * signal type for a brand-new DAQ pin, staging it, "Send to PLC") is GONE:
 * DataAcquisition already owns pin identity/type for every pin it publishes,
 * and a sensor only ever exists in Cozinha because DataAcquisition is
 * already publishing it — there is no "Unassigned" any more either. Every
 * device's pane now shows one row per pin DataAcquisition CURRENTLY
 * publishes, and the only thing Cozinha ever edits inline in that row is
 * label + x/y/z, autosaved on blur. A row with no position yet renders
 * greyed out.
 *
 * Every test uses a device DataAcquisition already reports in the test
 * environment (skipping if none exists, the same convention the file it
 * replaces used), and cleans up any sensor it created via the API.
 */
import { expect, test } from '@playwright/test'

function uniquePosition(): { x: number; y: number; z: number } {
  return { x: Math.random() * 100, y: Math.random() * 100, z: Math.random() * 100 }
}

async function deleteSensorApi(request: import('@playwright/test').APIRequestContext, key: string) {
  await request.delete(`/api/sensors/${encodeURIComponent(key)}`).catch(() => {})
}

async function openDevice(page: import('@playwright/test').Page, deviceId: string) {
  await page.goto('/')
  await page.getByRole('button', { name: 'edit sensors' }).click()
  await page.getByRole('navigation', { name: 'Devices' }).getByRole('button', { name: deviceId }).click()
}

/** The row for a given pin, identified by its pin-label cell text (mirrors
 *  src/lib/pinLabel.ts's encoding: base pins "A{n}", expansion pins
 *  "E{idx}:CH{ch}"). */
function pinRow(page: import('@playwright/test').Page, pinLabel: string) {
  return page.getByRole('row', { name: new RegExp(`^${pinLabel}\\b`) })
}

test.describe('device pin editor', () => {
  test('there is no Unassigned nav item any more', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'edit sensors' }).click()
    await expect(page.getByRole('button', { name: 'Unassigned' })).toHaveCount(0)
  })

  test('a published pin with no position yet renders greyed out with empty fields', async ({
    page,
    request,
  }) => {
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
    let target: { device_id: string; pin: number; name: string } | null = null
    for (const d of devices) {
      const free = d.config.sensors.find((s) => !isBound(d.device_id, s.pin))
      if (free) {
        target = { device_id: d.device_id, pin: free.pin, name: free.name }
        break
      }
    }
    test.skip(!target, 'no DataAcquisition device with an unpositioned published pin in this environment')

    const pinLabel = target!.pin >= 100
      ? `E${Math.floor(target!.pin / 100) - 1}:CH${target!.pin % 100}`
      : `A${target!.pin}`

    await openDevice(page, target!.device_id)
    const row = pinRow(page, pinLabel)
    await expect(row).toHaveAttribute('data-positioned', 'false')
    await expect(row.getByLabel(/^x/i)).toHaveValue('')
    await expect(row.getByText(target!.name)).toBeVisible()
  })

  test('filling in x/y/z on an unpositioned pin creates the sensor, defaulting the label to the DAQ name', async ({
    page,
    request,
  }) => {
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
    let target: { device_id: string; pin: number; name: string } | null = null
    for (const d of devices) {
      const free = d.config.sensors.find((s) => !isBound(d.device_id, s.pin))
      if (free) {
        target = { device_id: d.device_id, pin: free.pin, name: free.name }
        break
      }
    }
    test.skip(!target, 'no DataAcquisition device with an unpositioned published pin in this environment')

    const pinLabel = target!.pin >= 100
      ? `E${Math.floor(target!.pin / 100) - 1}:CH${target!.pin % 100}`
      : `A${target!.pin}`
    const pos = uniquePosition()

    try {
      await openDevice(page, target!.device_id)
      const row = pinRow(page, pinLabel)
      await row.getByLabel(/^x/i).fill(String(pos.x))
      await row.getByLabel(/^y/i).fill(String(pos.y))
      await row.getByLabel(/^z/i).fill(String(pos.z))
      await row.getByLabel(/^z/i).blur()

      await expect(row).toHaveAttribute('data-positioned', 'true', { timeout: 5000 })

      const saved = (await (await request.get('/api/sensors')).json()) as Array<{
        daq_device_id: string | null
        daq_pin: number | null
        label: string
        x: number
      }>
      const created = saved.find((s) => s.daq_device_id === target!.device_id && s.daq_pin === target!.pin)
      expect(created?.label).toBe(target!.name)
      expect(created?.x).toBeCloseTo(pos.x)
    } finally {
      const after = (await (await request.get('/api/sensors')).json()) as Array<{
        sensor_key: string
        daq_device_id: string | null
        daq_pin: number | null
      }>
      const created = after.find((s) => s.daq_device_id === target!.device_id && s.daq_pin === target!.pin)
      if (created) await deleteSensorApi(request, created.sensor_key)
    }
  })

  test("editing an already-positioned row's x and blurring updates it in place", async ({ page, request }) => {
    const devices = (await (await request.get('/api/daq/devices')).json()) as Array<{
      device_id: string
      config: { sensors: Array<{ pin: number; name: string }> }
    }>
    const device = devices.find((d) => d.config.sensors.length > 0)
    test.skip(!device, 'no DataAcquisition device with a published pin in this environment')
    const target = device!.config.sensors[0]!

    const pos = uniquePosition()
    await request.put(`/api/sensors/e2e-existing-${Date.now()}`, {
      data: { label: target.name, ...pos, daq_device_id: device!.device_id, daq_pin: target.pin },
    })

    const movedX = 1000 + Math.random() * 100

    try {
      const pinLabel = target.pin >= 100
        ? `E${Math.floor(target.pin / 100) - 1}:CH${target.pin % 100}`
        : `A${target.pin}`

      await openDevice(page, device!.device_id)
      const row = pinRow(page, pinLabel)
      await expect(row).toHaveAttribute('data-positioned', 'true')
      const xField = row.getByLabel(/^x/i)
      await xField.fill(String(movedX))
      await xField.blur()

      await expect(async () => {
        const saved = (await (await request.get('/api/sensors')).json()) as Array<{
          daq_device_id: string | null
          daq_pin: number | null
          x: number
        }>
        const updated = saved.find((s) => s.daq_device_id === device!.device_id && s.daq_pin === target.pin)
        expect(updated?.x).toBeCloseTo(movedX)
      }).toPass({ timeout: 5000 })
    } finally {
      const after = (await (await request.get('/api/sensors')).json()) as Array<{
        sensor_key: string
        daq_device_id: string | null
        daq_pin: number | null
      }>
      const created = after.find((s) => s.daq_device_id === device!.device_id && s.daq_pin === target.pin)
      if (created) await deleteSensorApi(request, created.sensor_key)
    }
  })

  test('saving an exact-duplicate position is refused inline, without clearing the typed value', async ({
    page,
    request,
  }) => {
    const devices = (await (await request.get('/api/daq/devices')).json()) as Array<{
      device_id: string
      config: { sensors: Array<{ pin: number; name: string }> }
    }>
    const device = devices.find((d) => d.config.sensors.length > 1)
    test.skip(!device, 'no DataAcquisition device with two published pins in this environment')
    const [existingPin, editingPin] = device!.config.sensors

    const takenPos = uniquePosition()
    await request.put(`/api/sensors/e2e-taken-${Date.now()}`, {
      data: { label: existingPin!.name, ...takenPos, daq_device_id: device!.device_id, daq_pin: existingPin!.pin },
    })

    try {
      const pinLabel = editingPin!.pin >= 100
        ? `E${Math.floor(editingPin!.pin / 100) - 1}:CH${editingPin!.pin % 100}`
        : `A${editingPin!.pin}`

      await openDevice(page, device!.device_id)
      const row = pinRow(page, pinLabel)
      await row.getByLabel(/^x/i).fill(String(takenPos.x))
      await row.getByLabel(/^y/i).fill(String(takenPos.y))
      const zField = row.getByLabel(/^z/i)
      await zField.fill(String(takenPos.z))
      await zField.blur()

      await expect(row.getByRole('alert')).toContainText(/already exists/i)
      await expect(row.getByLabel(/^x/i)).toHaveValue(String(takenPos.x))
    } finally {
      const after = (await (await request.get('/api/sensors')).json()) as Array<{
        sensor_key: string
        daq_device_id: string | null
        daq_pin: number | null
      }>
      for (const s of after) {
        if (s.daq_device_id === device!.device_id && [existingPin!.pin, editingPin!.pin].includes(s.daq_pin as number)) {
          await deleteSensorApi(request, s.sensor_key)
        }
      }
    }
  })

  test('a live 3D preview renders next to the pin table', async ({ page, request }) => {
    const devices = (await (await request.get('/api/daq/devices')).json()) as Array<{ device_id: string }>
    test.skip(devices.length === 0, 'no DataAcquisition device discovered in this environment')

    await openDevice(page, devices[0]!.device_id)
    await expect(page.getByLabel('Sensor position preview')).toBeVisible()
  })

  test('a sensor bound to a pin no device reports any more shows up under Orphaned', async ({
    page,
    request,
  }) => {
    const devices = (await (await request.get('/api/daq/devices')).json()) as Array<{ device_id: string }>
    test.skip(devices.length === 0, 'no DataAcquisition device discovered in this environment')

    // A pin number no discovered device actually reports — guaranteed not
    // to appear in any device's config.sensors, so the sensor bound to it
    // is orphaned from the moment it's created.
    const key = `e2e-orphan-${Date.now()}`
    const label = `Orphaned-${Date.now()}`
    const pos = uniquePosition()
    const bogusPin = 99999
    try {
      await request.put(`/api/sensors/${key}`, {
        data: { label, ...pos, daq_device_id: devices[0]!.device_id, daq_pin: bogusPin },
      })

      await page.goto('/')
      await page.getByRole('button', { name: 'edit sensors' }).click()

      const nav = page.getByRole('navigation', { name: 'Devices' })
      await expect(nav.getByRole('button', { name: /^Orphaned/ })).toBeVisible()
      await nav.getByRole('button', { name: /^Orphaned/ }).click()

      await expect(page.getByText(label)).toBeVisible()
      await page.getByRole('button', { name: /^delete$/i }).click()
      await page.getByRole('button', { name: /^delete$/i }).click()

      await expect(page.getByText(label)).toHaveCount(0)
      const sensors = (await (await request.get('/api/sensors')).json()) as Array<{ sensor_key: string }>
      expect(sensors.some((s) => s.sensor_key === key)).toBe(false)
    } finally {
      await deleteSensorApi(request, key)
    }
  })
})
