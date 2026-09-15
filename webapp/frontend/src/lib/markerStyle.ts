/**
 * markerStyle — per-marker radius/colour for SensorMarkers.tsx, factored out
 * as a pure function so the highlight behaviour (used by the kitchen device
 * pane's live position preview — see DevicePane.tsx) is unit-testable
 * without a WebGL renderer.
 */
import { concentrationToHex } from './colorScale'

/** Mirrors SensorMarkers.tsx's own un-highlighted marker radius. */
export const BASE_MARKER_RADIUS = 0.055

/** How much larger the highlighted marker renders, so it reads as "the one
 *  being edited" at a glance regardless of its own reading. */
const HIGHLIGHT_SCALE = 1.6

/** Mirrors SensorMarkers.tsx's ABSENT_COLOR for a silent sensor. */
const ABSENT_COLOR = 0x5a6272

/** Fixed accent, distinct from any concentration colour on the ramp, so the
 *  highlighted marker is unambiguous no matter what it's currently reading. */
export const HIGHLIGHT_COLOR_HEX = 0x4da3ff

export interface MarkerStyle {
  radius: number
  color: number
  emissiveIntensity: number
  highlighted: boolean
}

export function markerStyleFor(
  sensor: { key: string; value: number; hasReading: boolean },
  highlightedKey: string | null,
): MarkerStyle {
  const highlighted = highlightedKey != null && sensor.key === highlightedKey
  const live = sensor.hasReading

  if (highlighted) {
    return {
      radius: BASE_MARKER_RADIUS * HIGHLIGHT_SCALE,
      color: HIGHLIGHT_COLOR_HEX,
      emissiveIntensity: 1.4,
      highlighted: true,
    }
  }

  return {
    radius: BASE_MARKER_RADIUS,
    color: live ? concentrationToHex(sensor.value) : ABSENT_COLOR,
    emissiveIntensity: live ? 0.5 : 0.12,
    highlighted: false,
  }
}
