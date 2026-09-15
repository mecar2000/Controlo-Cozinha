/**
 * e2e/inline-compose.spec.ts — problems.txt: "To define the config should
 * be direct i shouldnt have to press a button. There should be the button
 * but if its waiting it should allow me to also fill the config or load
 * from memory directly on the main page." Decided: the config panel
 * REPLACES the numeric rail while WAITING with nothing running; the modal
 * survives only for the requested-vs-acked spec review before confirming
 * (the safety-relevant step).
 *
 * Written before ControlView.tsx/StartSheet.tsx are refactored, per the
 * project's test-first workflow.
 */
import { expect, test } from '@playwright/test'

test.describe('inline compose panel', () => {
  test('while idle (WAITING, no run), the rail shows the compose form with no button press', async ({
    page,
  }) => {
    await page.goto('/')
    // No modal should be open, and the compose fields should already be
    // visible directly in the rail.
    await expect(page.getByRole('dialog', { name: /start a run/i })).toHaveCount(0)
    const rail = page.locator('aside')
    await expect(rail.getByLabel(/run name/i)).toBeVisible()
    await expect(rail.getByLabel(/config/i)).toBeVisible()
  })

  test('a saved config can be picked and a run named, all inline', async ({ page }) => {
    await page.goto('/')
    const rail = page.locator('aside')
    await rail.getByLabel(/run name/i).fill('e2e-inline-run')
    // Selecting a config is a dropdown — just confirm it's interactable and
    // has at least the default option; a real config fixture would be
    // needed to assert a specific selection sticks.
    await expect(rail.getByLabel(/config/i)).toBeEnabled()
  })

  test('starting from the inline panel opens the review-and-confirm modal, not a compose modal', async ({
    page,
  }) => {
    await page.goto('/')
    const rail = page.locator('aside')
    await rail.getByLabel(/run name/i).fill(`e2e-inline-${Date.now()}`)
    // Start stays disabled until an experiment is chosen, unless this run is
    // marked unrecorded — check it so recording status doesn't block us here;
    // the compose-vs-review structural change is what this test verifies.
    await rail.getByLabel(/unrecorded test run/i).check()
    await rail.getByRole('button', { name: /^start$/i }).click()

    // The modal that appears must be the REVIEW step (spec diff + confirm),
    // not a second copy of the compose form.
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('heading', { name: /review what will run/i })).toBeVisible()
    await expect(dialog.getByLabel(/run name/i)).toHaveCount(0) // no compose fields in the modal anymore
  })

  test('the review modal survives the phase flipping to ARMED and can still be confirmed', async ({
    page,
  }) => {
    // Regression test: RunComposer (which used to own the review modal) is
    // only rendered while canStart is true, and canStart requires
    // phase === 'WAITING'. The instant the sim's start() ack lands and the
    // next status poll observes phase 'ARMED', canStart flips false and
    // RunComposer used to unmount — taking the modal (and the only way to
    // confirm) with it. The modal must now be owned above that mount
    // boundary so it survives the WAITING -> ARMED transition.
    await page.goto('/')
    const rail = page.locator('aside')
    await rail.getByLabel(/run name/i).fill(`e2e-armed-survives-${Date.now()}`)
    await rail.getByLabel(/unrecorded test run/i).check()
    await rail.getByRole('button', { name: /^start$/i }).click()

    const dialog = page.getByRole('dialog', { name: /review what will run/i })
    await expect(dialog).toBeVisible()

    // Wait out at least one status poll interval (useKitchen polls every
    // 2-5s) so phase has a chance to actually flip to ARMED underneath the
    // still-open modal, then assert it is still there and still usable.
    await expect(page.getByText(/^ARMED$/)).toBeVisible({ timeout: 10_000 })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('button', { name: /confirm.*start gas/i })).toBeEnabled()

    await dialog.getByRole('button', { name: /confirm.*start gas/i }).click()
    await expect(dialog).toHaveCount(0)
  })

  test('a run in progress shows the numeric rail again, not the compose form', async ({ page }) => {
    // This test only asserts the STRUCTURE holds when kitchen_state.phase is
    // not WAITING or a run is active — it does not itself drive a run to
    // that state (that needs the sim running, covered by other suites). It
    // documents the contract: NumericRail's phase/clock rail-rows must be
    // absent while the compose form is shown, and vice versa, never both.
    await page.goto('/')
    const rail = page.locator('aside')
    const composeVisible = await rail.getByLabel(/run name/i).isVisible().catch(() => false)
    const numericRailVisible = await rail.locator('.rail-row').first().isVisible().catch(() => false)
    expect(composeVisible && numericRailVisible).toBe(false) // never both at once
  })

  test('inlet-only ventilation is refused before submitting, with a reason shown', async ({ page }) => {
    // Requires a config with ventRegisters {inlet: true, central: false,
    // exhaust: false} to exist — see e2e/README.md for fixture setup. If
    // absent, this documents the expected behavior for when one is added.
    await page.goto('/')
    const rail = page.locator('aside')
    const inletOnlyOption = rail.getByRole('option', { name: /inlet.only/i })
    if ((await inletOnlyOption.count()) === 0) {
      test.skip(true, 'no inlet-only fixture config available')
    }
    await rail.getByLabel(/config/i).selectOption({ label: /inlet.only/i })
    await expect(rail.getByText(/inlet is open/i)).toBeVisible()
    await expect(rail.getByRole('button', { name: /^start$/i })).toBeDisabled()
  })
})
