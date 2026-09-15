/**
 * Field — interpolated concentration through the room's air volume.
 *
 * One box, raymarched against a 3D texture of the field. Three earlier
 * approaches were tried and rejected:
 *
 *   instanced cubes, additive blending — the accumulation saturates to white
 *     within a few cells, destroying the colour information the pinned LEL
 *     scale exists to convey.
 *   instanced cubes, alpha blending — a regular lattice of discrete boxes
 *     produces strong moiré starbursts wherever the rows align with the view
 *     axis.
 *   stacked horizontal slices — smooth from above, but a stack of planes has
 *     gaps between the planes, and a grazing ray passes between them. That
 *     showed as horizontal banding edge-on, and raising the slice count
 *     shrinks the gaps without ever closing them. It also meant one draw call
 *     and one texture per slice.
 *
 * Raymarching removes the failure mode instead of tuning around it: hardware
 * trilinear filtering is continuous in all three axes, so there is no lattice
 * and no gap to see through, and the marching step count is decoupled from the
 * data resolution. Absorption is 1 - exp(-density * stepLen), which is correct
 * for every view direction rather than only the vertical one — so the field
 * reads honestly from the side, where the slice stack under-reported.
 *
 * Colour comes from the pinned LEL scale via a lookup texture, never
 * auto-scaled. Density is gated by confidence, so regions the sensors
 * genuinely constrain look solid and guesswork looks thin.
 */

import { useFrame } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'

import { isAtOrAboveLel } from '@/lib/colorScale'
import {
  buildRampLut,
  createFieldBuffer,
  packField,
  RAMP_LUT_SIZE,
  SIGMA_PER_M,
  TEX_D,
  TEX_H,
  TEX_W,
} from '@/lib/fieldTexture'
import { DEFAULT_ANISOTROPY, type SensorSample } from '@/lib/interpolation'
import { ROOM } from '@/lib/roomGeometry'

/** Samples along each ray. Independent of the texture resolution — this is
 *  how finely the ray is integrated, not how finely the field is known. */
const MARCH_STEPS = 96

const vertexShader = /* glsl */ `
varying vec3 vObjPos;

void main() {
  vObjPos = position;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`

/**
 * Everything here works in OBJECT space, which is what makes the scene mirror
 * (RoomScene wraps this in scale={[-1,1,1]}) a non-issue: the mirror lives in
 * modelMatrix and is applied after the shader is done. In world space the
 * mirror flips x, the x slab's tmin/tmax swap, and the box silently renders
 * empty.
 */
const fragmentShader = /* glsl */ `
precision highp sampler3D;

uniform sampler3D uField;
uniform sampler2D uRamp;
uniform vec3 uHalfSize;
uniform float uSigma;
uniform float uOpacity;
uniform vec3 uObjCam;

varying vec3 vObjPos;

const int MARCH_STEPS = ${MARCH_STEPS};

void main() {
  vec3 rayDir = normalize(vObjPos - uObjCam);

  // Slab intersection against the room box. 1/0 gives +-inf here, which is
  // exactly right: IEEE min/max propagate it and the axis drops out.
  vec3 invDir = 1.0 / rayDir;
  vec3 t0 = (-uHalfSize - uObjCam) * invDir;
  vec3 t1 = ( uHalfSize - uObjCam) * invDir;
  vec3 tsmall = min(t0, t1);
  vec3 tbig = max(t0, t1);
  float tmin = max(max(tsmall.x, tsmall.y), tsmall.z);
  float tmax = min(min(tbig.x, tbig.y), tbig.z);

  // OrbitControls allows 1.2m in a ~2.5m room, so the camera really does get
  // inside the box. Starting the march at the camera rather than at a
  // negative t is what keeps the volume from vanishing when it does.
  tmin = max(tmin, 0.0);
  if (tmax <= tmin) discard;

  float stepLen = (tmax - tmin) / float(MARCH_STEPS);
  vec3 p = uObjCam + rayDir * (tmin + 0.5 * stepLen);
  vec3 dp = rayDir * stepLen;

  vec3 accumRgb = vec3(0.0);
  float accumA = 0.0;

  for (int i = 0; i < MARCH_STEPS; i++) {
    if (accumA > 0.99) break;

    // Object space maps to [0,1]^3 directly, so position and texcoord are the
    // same variable — see the texel-centre note in lib/fieldTexture.ts.
    vec3 uvw = p / (2.0 * uHalfSize) + 0.5;
    vec4 s = texture(uField, uvw);

    // confidence * strength * air mask. Any of the three going to zero means
    // nothing to draw here.
    float density = uSigma * s.g * s.b * s.a;

    if (density > 0.0) {
      float a = 1.0 - exp(-density * stepLen);
      vec3 rgb = texture(uRamp, vec2(s.r, 0.5)).rgb;
      // Front to back, premultiplied.
      accumRgb += (1.0 - accumA) * a * rgb;
      accumA += (1.0 - accumA) * a;
    }

    p += dp;
  }

  if (accumA <= 0.0) discard;

  gl_FragColor = vec4(accumRgb, accumA * uOpacity);
}
`

export function Field({
  samples,
  anisotropy = DEFAULT_ANISOTROPY,
  visible = true,
}: {
  samples: SensorSample[]
  anisotropy?: number
  visible?: boolean
}) {
  const meshRef = useRef<THREE.Mesh>(null)
  // Scratch, so the per-frame inverse does not allocate a matrix every frame.
  const objToWorld = useMemo(() => new THREE.Matrix4(), [])

  // The field itself, and the colour ramp it is coloured through. Both are
  // allocated once; only the field's contents change.
  const { fieldTexture, rampTexture, fieldData } = useMemo(() => {
    const fieldData = createFieldBuffer()
    const fieldTexture = new THREE.Data3DTexture(fieldData, TEX_W, TEX_H, TEX_D)
    fieldTexture.format = THREE.RGBAFormat
    // Data3DTexture defaults to NearestFilter on BOTH min and mag. Linear
    // filtering in all three axes is the entire reason this approach has no
    // banding — leave these at the default and you get blocky voxels.
    fieldTexture.minFilter = THREE.LinearFilter
    fieldTexture.magFilter = THREE.LinearFilter
    fieldTexture.needsUpdate = true

    // RGBAFormat, not RGB: WebGL2's texStorage2D upload requires a sized
    // internal format, which plain 3-channel RGBFormat is not. See the note
    // on buildRampLut for what that looked like when it was wrong (solid
    // black volume, GL_INVALID_ENUM on texStorage2D).
    const rampTexture = new THREE.DataTexture(buildRampLut(), RAMP_LUT_SIZE, 1, THREE.RGBAFormat)
    rampTexture.minFilter = THREE.LinearFilter
    rampTexture.magFilter = THREE.LinearFilter
    rampTexture.needsUpdate = true

    return { fieldTexture, rampTexture, fieldData }
  }, [])

  // Held separately from the material so the two written every frame can be
  // reached without re-proving to the typechecker that they exist.
  const uniforms = useMemo(
    () => ({
      uField: { value: fieldTexture },
      uRamp: { value: rampTexture },
      uHalfSize: {
        value: new THREE.Vector3(ROOM.width / 2, ROOM.depth / 2, ROOM.height / 2),
      },
      uSigma: { value: SIGMA_PER_M },
      uOpacity: { value: 1 },
      uObjCam: { value: new THREE.Vector3() },
    }),
    [fieldTexture, rampTexture],
  )

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        uniforms,
        transparent: true,
        depthWrite: false,
        // BackSide: from inside the box the front faces are behind the camera
        // and get near-clipped, which would make the volume vanish exactly
        // when someone leans in to look at it.
        side: THREE.BackSide,
      }),
    [uniforms],
  )

  useEffect(() => {
    return () => {
      fieldTexture.dispose()
      rampTexture.dispose()
      material.dispose()
    }
  }, [fieldTexture, rampTexture, material])

  // A cheap identity for "the field would come out the same". `samples` is
  // rebuilt from the poll response every few seconds, so it is a new array
  // on every tick even when no sensor moved and no reading changed — and
  // resampling is the single most expensive thing this view does. Comparing
  // the contents instead of the reference means a steady room costs nothing
  // between genuine changes.
  const samplesKey = useMemo(
    () => samples.map((s) => `${s.x},${s.y},${s.z},${s.value}`).join('|'),
    [samples],
  )

  useEffect(() => {
    packField(fieldData, samples, anisotropy)
    fieldTexture.needsUpdate = true
    // samplesKey, not samples: see the note on samplesKey above. The effect
    // reads `samples` but only needs to re-run when its CONTENTS change, and
    // samplesKey changes exactly then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [samplesKey, anisotropy, fieldData, fieldTexture])

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

  useFrame(({ clock, camera }) => {
    const mesh = meshRef.current
    if (!mesh) return

    // The camera in the box's own space. Recomputed per frame because the
    // camera orbits; the mesh's matrix is static but cheap to invert against.
    uniforms.uObjCam.value
      .copy(camera.position)
      .applyMatrix4(objToWorld.copy(mesh.matrixWorld).invert())

    uniforms.uOpacity.value =
      alarming && !reducedMotion ? 0.72 + 0.42 * Math.sin(clock.elapsedTime * 5.2) : 1
  })

  if (!visible) return null

  return (
    // renderOrder above the room's own geometry (which renders at the default
    // 0, its shell at -1) so the field composites over solid furniture rather
    // than fighting it for draw order.
    //
    // ponytail: occlusion is the baked air mask only — gas BEHIND a cabinet
    // still draws over it, where the old per-slice depthTest hid it. Upgrade
    // path: useFrame(cb, -1) filling a WebGLRenderTarget with a DepthTexture,
    // then clamp the march at scene depth. Negative priority keeps R3F's
    // auto-render, so that is ~30 lines and a resize handler, not a
    // render-loop takeover.
    <mesh
      ref={meshRef}
      position={[ROOM.width / 2, ROOM.depth / 2, ROOM.height / 2]}
      material={material}
      renderOrder={10}
    >
      <boxGeometry args={[ROOM.width, ROOM.depth, ROOM.height]} />
    </mesh>
  )
}
