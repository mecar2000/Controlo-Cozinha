/**
 * pollingIntervals.test.ts — problems.txt: "Why is the state updated so
 * frequently? Couldn't it be once every 2s-5s? That's an okay delay to have
 * on the website?" — yes: the server-side staleness cutoff is 15s
 * (routes/status.py), so any interval that leaves a comfortable margin below
 * that is safe. These constants are exported from useKitchen.ts specifically
 * so they're checkable without rendering the hook (no component-testing
 * library in this project — see the e2e/ Playwright suite for behavior that
 * needs one).
 */
import { describe, expect, it } from 'vitest'

import { READINGS_INTERVAL_MS, STATUS_INTERVAL_MS } from './useKitchen'

// Mirrors routes/status.py's `"stale": mqtt_age is None or mqtt_age > 15`.
const SERVER_STALE_CUTOFF_S = 15

describe('polling intervals', () => {
  it('are within the 2-5s range the user asked for', () => {
    expect(STATUS_INTERVAL_MS).toBeGreaterThanOrEqual(2000)
    expect(STATUS_INTERVAL_MS).toBeLessThanOrEqual(5000)
    expect(READINGS_INTERVAL_MS).toBeGreaterThanOrEqual(2000)
    expect(READINGS_INTERVAL_MS).toBeLessThanOrEqual(5000)
  })

  it('leave a comfortable margin below the server-side staleness cutoff', () => {
    // At least 3 polls must fit inside the stale window, so a couple of
    // dropped polls in a row (the network hiccups usePolling is built to
    // tolerate) don't ALSO cross into "stale" territory.
    const margin = 3
    expect(STATUS_INTERVAL_MS * margin).toBeLessThan(SERVER_STALE_CUTOFF_S * 1000)
  })
})
