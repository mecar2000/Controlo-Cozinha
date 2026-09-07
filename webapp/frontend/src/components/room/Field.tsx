/**
 * Field — interpolated concentration through the room's air volume.
 *
 * Drawn as a stack of horizontal slices, each a textured plane sampling the
 * field at its own height. Two earlier approaches were tried and rejected:
 *
 *   instanced cubes, additive blending — the accumulation saturates to white
 *     within a few cells, destroying the colour information the pinned LEL
 *     scale exists to convey.
 *   instanced cubes, alpha blending — a regular lattice of discrete boxes
 *     produces strong moiré starbursts wherever the rows align with the view
 *     axis. On a safety display that is a rendering artifact reading as
 *     structure in the data, which is worse than a coarse field.
 *
 * Horizontal slices suit this particular problem: hydrogen stratifies, so
 * the interesting gradient is vertical, and slicing along it means each
 * plane is nearly uniform in the direction it is sampled. Bilinear texture
 * filtering then gives a smooth field for free.
 *
 * Each slice carries:
 *   colour   from the pinned LEL scale, never auto-scaled
 *   alpha    from CONFIDENCE and concentration, so regions the sensors
 *            genuinely constrain look solid and guesswork looks thin
 */

import { useFrame } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'

import { concentrationToRgb, isAtOrAboveLel } from '@/lib/colorScale'
import { DEFAULT_ANISOTROPY, sampleField, type SensorSample } from '@/lib/interpolation'
import { isInsideSolid, ROOM } from '@/lib/roomGeometry'

/**
 * Horizontal slices through the room.
 *
 * Enough that they overlap visually rather than reading as separate sheets:
 * at 26 the gaps between slices were visible as horizontal banding wherever
 * the stack is seen edge-on.
 */
const SLICE_COUNT = 64
/** Texels per slice. 48x48 per slice across SLICE_COUNT slices, rebuilt
 *  whenever the readings change. */
const TEX_W = 48
const TEX_H = 48

/** Concentrations below this contribute nothing. */
const VISIBILITY_FLOOR_PCT_VV = 0.04
/** Concentration at which a slice reaches its full alpha. */
const FULL_ALPHA_AT_PCT_VV = 3.0
/** Per-slice alpha ceiling. Low, because all SLICE_COUNT slices composite
 *  along any line of sight through the room — raise this and the field
 *  saturates to a flat wash that hides the room. */
const MAX_SLICE_ALPHA = 0.17

export function Field({
  samples,
  anisotropy = DEFAULT_ANISOTROPY,
  visible = true,
}: {
  samples: SensorSample[]
  anisotropy?: number
  visible?: boolean
}) {
  const groupRef = useRef<THREE.Group>(null)

  // One RGBA texture per slice, rebuilt when the readings change.
  const { textures, materials } = useMemo(() => {
    const textures: THREE.DataTexture[] = []
    const materials: THREE.MeshBasicMaterial[] = []

    for (let s = 0; s < SLICE_COUNT; s++) {
      const data = new Uint8Array(TEX_W * TEX_H * 4)
      const tex = new THREE.DataTexture(data, TEX_W, TEX_H, THREE.RGBAFormat)
      // Linear filtering is what turns discrete samples into a continuous
      // field — the reason this approach has no lattice artifacts.
      tex.minFilter = THREE.LinearFilter
      tex.magFilter = THREE.LinearFilter
      tex.needsUpdate = true
      textures.push(tex)

      materials.push(
        new THREE.MeshBasicMaterial({
          map: tex,
          transparent: true,
          depthWrite: false,
          side: THREE.DoubleSide,
          toneMapped: false,
        }),
      )
    }
    return { textures, materials }
  }, [])

  // Dispose GPU resources when this component goes away.
  useEffect(() => {
    return () => {
      for (const t of textures) t.dispose()
      for (const m of materials) m.dispose()
    }
  }, [textures, materials])

  // Resample the field into the slice textures.
  useEffect(() => {
    for (let s = 0; s < SLICE_COUNT; s++) {
      const z = ((s + 0.5) / SLICE_COUNT) * ROOM.height
      const tex = textures[s]!
      const data = tex.image.data as Uint8Array

      for (let iy = 0; iy < TEX_H; iy++) {
        const y = ((iy + 0.5) / TEX_H) * ROOM.depth
        for (let ix = 0; ix < TEX_W; ix++) {
          const x = ((ix + 0.5) / TEX_W) * ROOM.width
          const o = (iy * TEX_W + ix) * 4

          // No air inside furniture, so no concentration to draw.
          if (isInsideSolid(x, y, z)) {
            data[o] = 0
            data[o + 1] = 0
            data[o + 2] = 0
            data[o + 3] = 0
            continue
          }

          const { value, confidence } = sampleField(x, y, z, samples, anisotropy)

          if (value < VISIBILITY_FLOOR_PCT_VV || confidence <= 0) {
            data[o + 3] = 0
            continue
          }

          const rgb = concentrationToRgb(value)
          const strength = Math.min(1, value / FULL_ALPHA_AT_PCT_VV)
          // Confidence and concentration both gate alpha: a doubtful region
          // and an empty one both look thin, and only a well-measured,
          // genuinely gassy region looks solid.
          const alpha = MAX_SLICE_ALPHA * confidence * strength

          data[o] = rgb.r
          data[o + 1] = rgb.g
          data[o + 2] = rgb.b
          data[o + 3] = Math.round(alpha * 255)
        }
      }
      tex.needsUpdate = true
    }
  }, [samples, anisotropy, textures])

  // The single motion in the whole interface: when any sensor reads at or
  // above LEL, the field breathes. Nothing else moves, so peripheral vision
  // catches this and only this.
  const alarming = useMemo(() => samples.some((s) => isAtOrAboveLel(s.value)), [samples])

  // Tracked live rather than sampled once at mount. The CSS side of the alarm
  // already responds to the setting immediately; reading it once here meant
  // the field kept pulsing until reload for someone who had just turned the
  // preference on — the moment they are most likely to want it respected.
  const [reducedMotion, setReducedMotion] = useState(
    () =>
      typeof window !== 'undefined' &&
      (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false),
  )

  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!mq) return
    const onChange = (e: MediaQueryListEvent) => setReducedMotion(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  useFrame(({ clock }) => {
    const base = 1
    const o =
      alarming && !reducedMotion
        ? base * (0.72 + 0.42 * Math.sin(clock.elapsedTime * 5.2))
        : base
    for (const m of materials) m.opacity = o
  })

  if (!visible) return null

  return (
    <group ref={groupRef}>
      {materials.map((mat, s) => {
        const z = ((s + 0.5) / SLICE_COUNT) * ROOM.height
        return (
          // renderOrder above the room's own geometry (which renders at the
          // default 0) so the slices composite over solid furniture rather
          // than fighting it for draw order. depthTest stays ON: a slice
          // behind a cabinet must be hidden by it, or the room stops
          // occluding the gas and the cabinets read as transparent.
          <mesh
            key={s}
            position={[ROOM.width / 2, ROOM.depth / 2, z]}
            material={mat}
            renderOrder={10 + s}
          >
            <planeGeometry args={[ROOM.width, ROOM.depth]} />
          </mesh>
        )
      })}
    </group>
  )
}
