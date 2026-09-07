/**
 * SensorMarkers — one marker per sensor, coloured by its own reading.
 *
 * This is the trustworthy baseline: no interpolation, no invention, just
 * what each sensor actually reports at the place it actually sits. It stays
 * visible on top of the field, so if the field ever looks implausible it can
 * be checked against raw values immediately.
 *
 * A sensor with no fresh reading is drawn hollow and grey, never as zero.
 * Silence is not evidence of low concentration.
 */

import { Billboard, Text } from '@react-three/drei'

import type { LiveSensor } from '@/hooks/useKitchen'
import { concentrationToHex, isAtOrAboveLel } from '@/lib/colorScale'

const MARKER_RADIUS = 0.055

/**
 * WebGL materials cannot resolve CSS custom properties, so the scene carries
 * its own copies of the ink tokens. Kept together and named after the tokens
 * they mirror (--color-ink-faint, --color-ink, --color-bg) so a palette
 * change has one obvious place to follow through to.
 */
const ABSENT_COLOR = 0x5a6272
const LABEL_INK = '#e8ebf0'
const LABEL_INK_FAINT = '#5a6272'
const LABEL_OUTLINE = '#0a0c0f'

export function SensorMarkers({
  sensors,
  showLabels = true,
  stale = false,
}: {
  sensors: LiveSensor[]
  showLabels?: boolean
  /** Broker link is stale: every value is suspect, so dim the whole set
   *  rather than presenting last-known readings as current. */
  stale?: boolean
}) {
  return (
    <group>
      {sensors.map((s) => {
        const live = s.hasReading && !stale
        const color = live ? concentrationToHex(s.value) : ABSENT_COLOR
        const alarming = live && isAtOrAboveLel(s.value)

        return (
          <group key={s.key} position={[s.x, s.y, s.z]}>
            <mesh>
              <sphereGeometry args={[MARKER_RADIUS, 20, 20]} />
              <meshStandardMaterial
                color={color}
                emissive={color}
                // Past LEL the marker carries its own light, so it stands
                // out from the field even where the field is dense.
                emissiveIntensity={alarming ? 1.4 : live ? 0.5 : 0.12}
                transparent={!live}
                opacity={live ? 1 : 0.5}
                roughness={0.4}
              />
            </mesh>

            {/* A hollow ring marks a sensor that is present but silent, so
                absent reads differently from zero at a glance. */}
            {!live && (
              <mesh>
                <ringGeometry args={[MARKER_RADIUS * 1.6, MARKER_RADIUS * 1.9, 24]} />
                <meshBasicMaterial color={ABSENT_COLOR} transparent opacity={0.7} />
              </mesh>
            )}

            {showLabels && (
              <Billboard position={[0, 0, MARKER_RADIUS + 0.11]}>
                <Text
                  fontSize={0.075}
                  color={live ? LABEL_INK : LABEL_INK_FAINT}
                  anchorX="center"
                  anchorY="middle"
                  outlineWidth={0.006}
                  outlineColor={LABEL_OUTLINE}
                >
                  {live ? `${s.value.toFixed(2)}` : '--'}
                </Text>
              </Billboard>
            )}
          </group>
        )
      })}
    </group>
  )
}
