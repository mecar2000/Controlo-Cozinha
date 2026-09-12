/**
 * e2e/cursor.spec.ts — problems.txt: "The insert text icon still shows up a
 * lot on the website ... It shows up a lot in the control panel." Written
 * before the CSS fix, per the project's test-first workflow.
 *
 * There is no I-beam-visibility assertion available in Playwright directly
 * (it's a browser-rendered cursor icon, not a DOM attribute) — what IS
 * checkable, and is exactly what CAUSES the I-beam, is computed
 * `cursor` and `user-select` on the elements that showed it: by default a
 * div/span is `cursor: auto` + `user-select: auto` (auto -> text over
 * text-shaped content -> I-beam). This suite asserts those computed values
 * directly instead.
 */
import { expect, test } from '@playwright/test'

test.describe('cursor / text-selection', () => {
  test('body sets a non-text default cursor and disables selection', async ({ page }) => {
    await page.goto('/')
    const body = page.locator('body')
    await expect(body).toHaveCSS('cursor', 'default')
    await expect(body).toHaveCSS('user-select', 'none')
  })

  test('the numeric rail rows do not show an I-beam', async ({ page }) => {
    await page.goto('/')
    // NumericRail's rail-row/rail-label/rail-value — phase name, clock,
    // peak/delivered/flow/etc. These inherit from body once the fix lands;
    // this test would fail on the pre-fix DOM (auto, not default).
    const row = page.locator('.rail-row').first()
    await expect(row).toHaveCSS('cursor', 'default')
  })

  test('the sensors and configs buttons over the room canvas are pointer-cursor', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('button', { name: 'edit sensors' })).toHaveCSS('cursor', 'pointer')
    await expect(page.getByRole('button', { name: 'configs' })).toHaveCSS('cursor', 'pointer')
  })

  test('the view-mode toggle and header tabs are pointer-cursor', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('button', { name: /field|sensors/i }).first()).toHaveCSS('cursor', 'pointer')
    await expect(page.getByRole('button', { name: 'control', exact: true })).toHaveCSS('cursor', 'pointer')
  })

  test('prose/error text remains selectable', async ({ page }) => {
    await page.goto('/')
    // .prose-text is the escape hatch for copyable content (error banners,
    // explanatory copy) — it must NOT inherit body's user-select: none.
    const prose = page.locator('.prose-text').first()
    if (await prose.count()) {
      await expect(prose).toHaveCSS('user-select', 'text')
    }
  })

  test('a run number/name in the rail stays selectable (an operator copies it into a report)', async ({
    page,
  }) => {
    await page.goto('/')
    const selectable = page.locator('.selectable').first()
    if (await selectable.count()) {
      await expect(selectable).toHaveCSS('user-select', 'text')
    }
  })
})
