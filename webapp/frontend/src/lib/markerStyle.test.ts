/**
 * markerStyle.test.ts — markerStyleFor decides one sensor marker's radius
 * and colour given whether it is the one currently being edited elsewhere on
 * screen (the kitchen device pane's position editor). Kept as a pure
 * function so the highlight logic is unit-testable without a WebGL renderer
 * — SensorMarkers.tsx just calls it per sensor.
 */
import { describe, expect, it } from 'vitest'

import { concentrationToHex } from './colorScale'
import { BASE_MARKER_RADIUS, HIGHLIGHT_COLOR_HEX, markerStyleFor } from './markerStyle'

function sensor(overrides: Partial<Parameters<typeof markerStyleFor>[0]> = {}) {
  return {
    key: 'h2-1',
    value: 1.5,
    hasReading: true,
    ...overrides,
  }
}

describe('markerStyleFor', () => {
  it('gives an ordinary, un-highlighted live sensor its concentration colour at the base radius', () => {
    const style = markerStyleFor(sensor({ key: 'h2-1', value: 2, hasReading: true }), null)
    expect(style.radius).toBe(BASE_MARKER_RADIUS)
    expect(style.color).toBe(concentrationToHex(2))
    expect(style.highlighted).toBe(false)
  })

  it('enlarges and recolors the sensor matching highlightedKey', () => {
    const style = markerStyleFor(sensor({ key: 'h2-3' }), 'h2-3')
    expect(style.radius).toBeGreaterThan(BASE_MARKER_RADIUS)
    expect(style.color).toBe(HIGHLIGHT_COLOR_HEX)
    expect(style.highlighted).toBe(true)
  })

  it('leaves every other marker unaffected when a different key is highlighted', () => {
    const style = markerStyleFor(sensor({ key: 'h2-1', value: 2 }), 'h2-3')
    expect(style.radius).toBe(BASE_MARKER_RADIUS)
    expect(style.color).toBe(concentrationToHex(2))
    expect(style.highlighted).toBe(false)
  })

  it('the highlight colour overrides the absent-sensor grey too', () => {
    const style = markerStyleFor(sensor({ key: 'h2-5', hasReading: false }), 'h2-5')
    expect(style.color).toBe(HIGHLIGHT_COLOR_HEX)
    expect(style.highlighted).toBe(true)
  })

  it('an absent (no reading) sensor still gets the plain grey colour when not highlighted', () => {
    const style = markerStyleFor(sensor({ key: 'h2-5', hasReading: false }), null)
    expect(style.highlighted).toBe(false)
    expect(style.color).not.toBe(concentrationToHex(0))
  })
})
