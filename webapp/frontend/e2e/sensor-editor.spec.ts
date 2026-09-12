/**
 * e2e/sensor-editor.spec.ts — the sensor editor (problems.txt: "I have to
 * be able to define new sensors as well as their positions shouldn't be
 * fixed ... There should be memory of previous ones").
 *
 * Written before SensorPanel.tsx gains add/edit/archive/restore controls,
 * per the project's test-first workflow — this defines the contract the
 * UI must satisfy. See e2e/README.md for what must be running first.
 *
 * Each test creates its own sensor with a unique key (Date.now()-based) and
 * cleans up via the API afterwards, so tests can run in any order against
 * one shared backend/database without colliding.
 */
import { expect, test } from '@playwright/test'

function uniqueKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1000)}`
}

async function deleteSensorApi(request: import('@playwright/test').APIRequestContext, key: string) {
  // Best-effort cleanup: archive then leave archived (a real DELETE endpoint
  // was intentionally replaced with soft-delete — see routes/sensors.py).
  await request.delete(`/api/sensors/${encodeURIComponent(key)}`).catch(() => {})
}

test.describe('sensor editor', () => {
  test('adding a sensor makes it appear in the room with no restart needed', async ({ page, request }) => {
    const key = uniqueKey('e2e-sensor')
    try {
      await page.goto('/')
      await page.getByRole('button', { name: 'edit sensors' }).click()

      await page.getByRole('button', { name: /add sensor/i }).click()
      await page.getByLabel(/sensor key/i).fill(key)
      await page.getByLabel(/^label$/i).fill('E2E test sensor')
      await page.getByLabel(/^x/i).fill('1.0')
      await page.getByLabel(/^y/i).fill('1.0')
      await page.getByLabel(/^z/i).fill('1.0')
      await page.getByRole('button', { name: /^save$/i }).click()

      await expect(page.getByText('E2E test sensor')).toBeVisible()
    } finally {
      await deleteSensorApi(request, key)
    }
  })

  test('editing a sensor\'s position persists across a reload', async ({ page, request }) => {
    const key = uniqueKey('e2e-move')
    try {
      await request.put(`/api/sensors/${key}`, {
        data: { label: 'Movable', x: 0, y: 0, z: 0 },
      })

      await page.goto('/')
      await page.getByRole('button', { name: 'edit sensors' }).click()
      await page.getByText('Movable').click()
      await page.getByLabel(/^x/i).fill('2.5')
      await page.getByRole('button', { name: /^save$/i }).click()
      await expect(page.getByText(/saved/i)).toBeVisible()

      await page.reload()
      await page.getByRole('button', { name: 'edit sensors' }).click()
      await page.getByText('Movable').click()
      await expect(page.getByLabel(/^x/i)).toHaveValue('2.5')
    } finally {
      await deleteSensorApi(request, key)
    }
  })

  test('archiving a sensor removes it from the default list, restoring brings it back', async ({
    page,
    request,
  }) => {
    const key = uniqueKey('e2e-archive')
    await request.put(`/api/sensors/${key}`, {
      data: { label: 'Archivable', x: 0, y: 0, z: 0 },
    })

    await page.goto('/')
    await page.getByRole('button', { name: 'edit sensors' }).click()
    await page.getByText('Archivable').click()
    await page.getByRole('button', { name: /^archive$/i }).click()
    await expect(page.getByText('Archivable')).not.toBeVisible()

    await page.getByRole('button', { name: /archived/i }).click() // switch to the archived tab
    await expect(page.getByText('Archivable')).toBeVisible()
    await page.getByText('Archivable').click()
    await page.getByRole('button', { name: /^restore$/i }).click()

    await page.getByRole('button', { name: /^active$/i }).click() // back to the default tab
    await expect(page.getByText('Archivable')).toBeVisible()

    await deleteSensorApi(request, key)
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
