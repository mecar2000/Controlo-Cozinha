/**
 * Equipment — moving parts layered over the static RoomGeometry: the three
 * dampers (exhaust, central, inlet) and the leak tube's flow animation.
 *
 * Rendered by both RoomScene (real control-system state, live) and
 * SandboxScene (fake state from DevModePanel, for iterating on the
 * animations with hot reload and no broker/DAQ). Each piece takes plain
 * props rather than reading from useKitchen itself, so the two callers only
 * differ in what feeds the props — never in the geometry or animation code.
 *
 * Positioned against EXHAUST/ROOM/LEAK_TUBE from roomGeometry.ts so it
 * lines up with the real hood/ceiling/water heater the moment this is
 * folded into RoomGeometry.tsx.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'

import { CENTRAL_DAMPER, EXHAUST, LEAK_TUBE, ROOM, WALL_DAMPER } from '@/lib/roomGeometry'

const FURNITURE = '#4A5665'

/** How long a damper takes to swing fully open or fully closed. */
const DAMPER_SWING_SECONDS = 2

/**
 * Tracks a damper's swing angle (0 = closed .. PI/2 = open) and eases it
 * toward whatever `open` (0-1) currently asks for, at a constant angular
 * speed sized so a full 0->1 swing takes DAMPER_SWING_SECONDS. Used by both
 * CircularFlapDamper and WallDamper so a checkbox flip reads as the flap
 * physically moving rather than teleporting to its new position.
 */
function useDamperSwing(open: number) {
  const angleRef = useRef(THREE.MathUtils.clamp(open, 0, 1) * (Math.PI / 2))
  // Quantised copy of the angle, and the ONLY thing that goes through React
  // state. The raw angle changes every frame of a swing; re-rendering the
  // component tree for each of those was ~120 React renders per swing per
  // damper, for a flap whose visible position is driven straight from the ref
  // below anyway. Rounding to SWING_STEPS buckets cuts that to at most
  // SWING_STEPS renders while staying far finer than the eye can resolve on
  // a rotating flap.
  const [, setQuantised] = useState(0)

  useFrame((_, delta) => {
    const target = THREE.MathUtils.clamp(open, 0, 1) * (Math.PI / 2)
    const maxStep = (Math.PI / 2 / DAMPER_SWING_SECONDS) * delta
    const next = THREE.MathUtils.clamp(
      angleRef.current + THREE.MathUtils.clamp(target - angleRef.current, -maxStep, maxStep),
      0,
      Math.PI / 2,
    )
    if (next !== angleRef.current) {
      angleRef.current = next
      const bucket = Math.round((next / (Math.PI / 2)) * SWING_STEPS)
      setQuantised((prev) => (prev === bucket ? prev : bucket))
    }
  })

  return angleRef
}

/** Buckets a damper's 0..90 degree swing is quantised into for re-render
 *  purposes. 24 is a step of under 4 degrees — indistinguishable on a flap
 *  this size, and a fifth of the renders a per-frame update caused. */
const SWING_STEPS = 24
/**
 * Ventilation flow animation: drawn above the ceiling dampers and outside
 * the wall damper, showing air movement when ventilating. It goes outward
 * (up) at the ceiling dampers and inward (-x) at the wall inlet.
 *
 * Two things drive how much air is shown, and they multiply:
 *
 *   ventilationRate (0-100)  how hard the fan is being asked to run
 *   openness (0-1)           how far this damper's flap has actually swung
 *
 * The product is the "effectiveness" below. A flap that is still swinging
 * passes proportionally less air, so the stream ramps in over the same
 * DAMPER_SWING_SECONDS the flap takes rather than snapping to full flow the
 * instant the damper is commanded open.
 */
const AIR_COLOR = '#88CCFF'

/**
 * Effectiveness -> how many of the AIR_CAPSULE_COUNT slots are actually
 * drawn. Volumetric flow really is "how much air per second", so density
 * scales with it: a 20% stream is visibly sparse, not merely slower. Below
 * MIN_EFFECTIVENESS nothing is drawn at all — a stalled-but-visible stream
 * reads as a broken render rather than as "no flow".
 */
const MIN_EFFECTIVENESS = 0.02

/**
 * Speed floor, as a fraction of full speed. Density carries most of the
 * "how much" signal (see airCapsuleCount), so speed only needs to vary
 * enough to read as slower — dropping it to zero would freeze the stream.
 */
const MIN_SPEED_FRACTION = 0.3

function airCapsuleCount(effectiveness: number) {
  if (effectiveness < MIN_EFFECTIVENESS) return 0
  return Math.max(1, Math.ceil(AIR_CAPSULE_COUNT * effectiveness))
}

function airSpeedFraction(effectiveness: number) {
  return MIN_SPEED_FRACTION + (1 - MIN_SPEED_FRACTION) * effectiveness *1.5
}

/**
 * Ceiling of the `spread` value used for CAPSULE SIZE only — scatter radius
 * still uses the vent's true spread, so the wider vents keep their wider,
 * funnelling plume.
 *
 * Without this the 20cm vents (central ⌀0.20, wall inlet 0.2x0.2) draw
 * capsules 33% fatter than the 15cm exhaust duct, which read as chunky
 * rather than as "more air". Sized just above the exhaust's own spread
 * (0.075) so that duct is unaffected and only the larger two are reined in.
 */
const MAX_CAPSULE_SPREAD = 0.08

/**
 * Two perpendicular unit vectors spanning the plane through `origin`
 * perpendicular to `direction` — used to scatter each particle's start point
 * across the vent's opening instead of every particle leaving from the same
 * single point on the axis.
 */
function planeBasis(direction: THREE.Vector3) {
  const arbitrary =
    Math.abs(direction.y) < 0.99 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0)
  const u = new THREE.Vector3().crossVectors(direction, arbitrary).normalize()
  const v = new THREE.Vector3().crossVectors(direction, u).normalize()
  return { u, v }
}

/** How many capsules fill the vent's opening at once — denser than the old
 *  small-sphere look, matching how LeakFlowAnimation reads as an obvious,
 *  visible flow rather than a faint mist. */
const AIR_CAPSULE_COUNT = 24

export function ExhaustAirflow({
  position,
  direction = new THREE.Vector3(0, 0, 1),
  openness = 0,
  ventilationRate,
  spread = 0.08,
  inward = false,
}: {
  position: [number, number, number]
  direction?: THREE.Vector3
  /**
   * How far this damper's flap has actually swung, 0 (sealed) to 1 (fully
   * open) — NOT the commanded state. Passed down from the parent's
   * useDamperSwing so the stream ramps in as the flap moves; see the
   * effectiveness note above AIR_COLOR.
   */
  openness?: number
  ventilationRate: number
  /** Radius (metres) of the vent opening — capsules scatter across this
   *  whole area (not just a single centreline), so a bigger vent shows a
   *  wider plume. Capsule SIZE also scales with it, but only up to
   *  MAX_CAPSULE_SPREAD, so the larger vents read as wider rather than as
   *  chunkier. */
  spread?: number
  /**
   * false (default): air being pulled OUT — capsules start at `position`
   * (the vent) and travel outward along `direction`, fading as they leave.
   * Used by the two ceiling dampers.
   *
   * true: air being drawn IN — capsules start further out along
   * `direction` and travel back toward `position` (the vent), fading as
   * they ARRIVE rather than as they leave, so nothing ever crosses past the
   * vent into the room. Used by the wall inlet, so the moving air only
   * ever appears outside the wall, never inside the kitchen.
   */
  inward?: boolean
}) {
  const meshRef = useRef<THREE.InstancedMesh>(null)
  const progressRef = useRef(0)
  const dummy = useMemo(() => new THREE.Object3D(), [])
  const { u: basisU, v: basisV } = useMemo(() => planeBasis(direction), [direction])
  // Scratch objects reused across every capsule and every frame instead of
  // being allocated per-capsule inside the loop below — at up to 24
  // capsules this was ~24 Vector3/quaternion-axis allocations per frame per
  // vent, pure GC churn for no visual difference.
  const scratchPos = useMemo(() => new THREE.Vector3(), [])
  const upAxis = useMemo(() => new THREE.Vector3(0, 1, 0), [])
  // Each capsule keeps its own fixed offset across the opening (polar
  // coordinates within the vent's radius), own phase along the travel
  // path, and own wobble phases, so at any moment capsules are staggered
  // across the whole area instead of all riding the same single line.
  const layout = useMemo(
    () =>
      Array.from({ length: AIR_CAPSULE_COUNT }, () => {
        const angle = Math.random() * Math.PI * 2
        // sqrt() for a uniform scatter over the disc's AREA; without it
        // capsules bunch toward the centre.
        const rFraction = Math.sqrt(Math.random())
        const phase = Math.random()
        const wobblePhaseU = Math.random() * Math.PI * 2
        const wobblePhaseV = Math.random() * Math.PI * 2
        return { angle, rFraction, phase, wobblePhaseU, wobblePhaseV }
      }),
    [],
  )

  // Size comes from the CAPPED spread (see MAX_CAPSULE_SPREAD) while the
  // scatter radius below still uses the true `spread` — so a wide vent
  // keeps its wide, funnelling plume without also inflating every capsule
  // in it.
  const sizeSpread = Math.min(spread, MAX_CAPSULE_SPREAD)
  const capsuleLength = Math.max(0.02, sizeSpread * 0.35)
  const capsuleRadius = Math.max(0.006, sizeSpread * 0.12)

  const effectiveness =
    THREE.MathUtils.clamp(ventilationRate / 100, 0, 1) * THREE.MathUtils.clamp(openness, 0, 1)
  const visibleCount = airCapsuleCount(effectiveness)

  useFrame((_, delta) => {
    const mesh = meshRef.current
    // Hooks must run every render regardless of visibility (React's rule),
    // so the "don't animate" guard lives here, inside the callback,
    // instead of an early return above the hooks — an early return there
    // made the hook count itself differ between renders, which is what
    // threw "Expected static flag was missing" and corrupted this
    // component's state whenever a damper opened/closed.
    if (!mesh || visibleCount === 0) return

    // Density (visibleCount) carries most of the "how much air" signal;
    // speed varies over a narrower range on top of it, so a weak stream is
    // sparse AND slow but never frozen.
    progressRef.current += delta * 1.5 * airSpeedFraction(effectiveness)

    // A harder pull throws air further before it disperses, so the visible
    // run grows with effectiveness rather than always ending at a fixed
    // distance.
    const travelDistance = 0.25 + 0.25 * effectiveness
    const base = new THREE.Vector3(...position)
    const elapsed = progressRef.current

    for (let i = 0; i < visibleCount; i++) {
      const { angle, rFraction, phase, wobblePhaseU, wobblePhaseV } = layout[i]!

      // Velocity profile: real duct flow is fastest on the axis and slows
      // toward the walls, so a capsule's own speed depends on how far off
      // -axis it sits. Without this every capsule moves in lockstep and the
      // stream reads as a conveyor belt.
      const profile = 1 - 0.45 * rFraction * rFraction
      const u = (((phase + elapsed * profile) % 1) + 1) % 1

      // Outward (exhaust): start at the vent (0), travel out to the far end
      // (1) as u increases. Inward (inlet): start at the far end (1),
      // travel back toward the vent (0) as u increases — so the capsule
      // arrives at, and never passes, `position`.
      const alongDirection = inward ? (1 - u) * travelDistance : u * travelDistance

      // Converging/diverging cone: this is what makes "air out" and "air
      // in" distinguishable from an arbitrary camera angle, where travel
      // direction alone is ambiguous. Exhaust capsules squeeze toward the
      // duct axis as they approach the vent (suction drawing air in from a
      // wide area into a narrow duct); inlet capsules spread apart as they
      // come through the opening and fan out. Both are expressed as a
      // radius multiplier that depends on distance from the vent.
      const distanceFromVent = inward ? 1 - u : u
      const radiusScale = inward
        ? 0.55 + 0.75 * distanceFromVent
        : 1.3 - 0.75 * distanceFromVent
      const r = rFraction * spread * radiusScale

      // Small lateral wobble so the stream breathes instead of running on
      // rails. Amplitude grows with distance from the vent: flow is
      // orderly in the throat and turbulent once it is out in open air.
      const wobbleAmplitude = spread * 0.12 * distanceFromVent
      const wobbleU = Math.cos(elapsed * 2.1 + wobblePhaseU) * wobbleAmplitude
      const wobbleV = Math.sin(elapsed * 1.7 + wobblePhaseV) * wobbleAmplitude

      scratchPos
        .copy(base)
        .addScaledVector(basisU, Math.cos(angle) * r + wobbleU)
        .addScaledVector(basisV, Math.sin(angle) * r + wobbleV)
        .addScaledVector(direction, alongDirection)

      dummy.position.copy(scratchPos)
      dummy.quaternion.setFromUnitVectors(upAxis, direction)

      // Smoothly scale up in the middle, down at both ends, so capsules
      // don't pop in/out abruptly as they enter/leave the visible run.
      const scale = Math.min(u * 6, (1 - u) * 6, 1)
      dummy.scale.set(scale, scale, scale)

      dummy.updateMatrix()
      mesh.setMatrixAt(i, dummy.matrix)
    }

    // Only the first visibleCount instances were positioned this frame;
    // capping count stops the rest from rendering as a frozen clump at
    // whatever position they last held.
    mesh.count = visibleCount
    mesh.instanceMatrix.needsUpdate = true
  })

  if (visibleCount === 0) return null

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, AIR_CAPSULE_COUNT]}>
      <capsuleGeometry args={[capsuleRadius, capsuleLength, 4, 12]} />
      <meshStandardMaterial
        color={AIR_COLOR}
        emissive={AIR_COLOR}
        emissiveIntensity={0.35}
        transparent
        opacity={0.55}
        roughness={0.2}
      />
    </instancedMesh>
  )
}

export function CircularFlapDamper({
  x,
  y,
  z,
  radius,
  open,
  ventilationRate,
}: {
  x: number
  y: number
  z: number
  radius: number
  open: number
  ventilationRate: number
}) {
  // 0 -> flap flat in the ceiling plane (horizontal, sealing the hole).
  // 1 -> rotated 90 degrees about the x-axis, swung to vertical.
  const swingRef = useDamperSwing(open)
  const flapRef = useRef<THREE.Group>(null)

  // The flap's rotation is written straight to the object each frame rather
  // than going through React: the swing angle changes continuously and this
  // is the only thing that depends on its exact value.
  useFrame(() => {
    if (flapRef.current) flapRef.current.rotation.x = swingRef.current
  })

  // Same value as a 0-1 fraction, handed to the airflow so the stream ramps
  // in with the flap instead of snapping to full flow on command. Read from
  // the ref at render time — the quantised state update in useDamperSwing is
  // what re-renders this component, so it refreshes as the flap swings.
  const openness = swingRef.current / (Math.PI / 2)

  return (
    <group position={[x, y, z]}>
      {/* 1. Master Group: Handles only the base position in the room */}

      {/* 2. Airflow: Placed outside the rotation group so it blows straight down */}
      <ExhaustAirflow
        position={[0, 0, 0.1]}
        direction={new THREE.Vector3(0, 0, 1)}
        openness={openness}
        ventilationRate={ventilationRate}
        spread={radius}
      />

      {/* 3. Flap Group: Handles ONLY the swing rotation */}
      <group ref={flapRef}>
        <mesh rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[radius, radius, 0.01, 24]} />
          <meshStandardMaterial
            color={FURNITURE} // Ensure FURNITURE is imported or defined in this file
            roughness={0.6}
            metalness={0.2}
            side={THREE.DoubleSide}
          />
        </mesh>
      </group>
      
    </group>
  )
}

/**
 * Exhaust damper: circular flap sealing the round duct opening at the
 * ceiling above the extraction hood.
 */
export function Damper({ open = 0, ventilationRate }: { open?: number; ventilationRate: number }) {
  const cx = EXHAUST.mouth.x + EXHAUST.mouth.width / 2
  const cy = EXHAUST.mouth.y + EXHAUST.mouth.depth / 2
  return (
    <CircularFlapDamper
      x={cx}
      y={cy}
      z={ROOM.height - 0.005}
      radius={EXHAUST.duct.diameter / 2}
      open={open}
      ventilationRate={ventilationRate}
    />
  )
}

/**
 * Central damper: circular flap in the ceiling, independent of the exhaust
 * duct (see CENTRAL_DAMPER in roomGeometry.ts).
 */
export function CentralDamper({ open = 0, ventilationRate }: { open?: number; ventilationRate: number }) {
  const { x, y, diameter } = CENTRAL_DAMPER
  return (
    <CircularFlapDamper
      x={x}
      y={y}
      z={ROOM.height - 0.005}
      radius={diameter / 2}
      open={open}
      ventilationRate={ventilationRate}
    />
  )
}

/**
 * Wall damper: a single square flap set into the right wall (see
 * WALL_DAMPER in roomGeometry.ts). Pivots about a vertical hinge line
 * through the CENTRE of the opening, rotating around the y-axis — closed
 * (open=0) it lies flat in the wall plane (vertical, sealing the hole);
 * open (open=1) it has swung 90 degrees to horizontal.
 *
 * The wall's own hole is cut in RoomGeometry.tsx's WallWithHoles; this is
 * only the moving leaf that covers it.
 */
export function WallDamper({ open = 0, ventilationRate }: { open?: number; ventilationRate: number }) {
  const { y, z, width, height } = WALL_DAMPER
  // Hinge at the hole's own centre, in world space: right wall is the
  // x=ROOM.width plane, with the hole's (u, v) = (y, z) measured from the
  // wall's near-bottom corner (see roomGeometry.ts's coordinate frame note).
  const hingeX = ROOM.width
  const hingeY = y + width / 2
  const hingeZ = z + height / 2

  // 0 -> flap flat against the wall (vertical, in the y-z plane). 1 ->
  // rotated 90 degrees about the y-axis, swung out to horizontal. Eased
  // toward that target over DAMPER_SWING_SECONDS rather than snapping to it.
  const swingRef = useDamperSwing(open)
  const flapRef = useRef<THREE.Group>(null)

  // Written straight to the object each frame, same as CircularFlapDamper —
  // this flap turns about y rather than x.
  useFrame(() => {
    if (flapRef.current) flapRef.current.rotation.y = swingRef.current
  })

  const openness = swingRef.current / (Math.PI / 2)

  return (
    <group position={[hingeX, hingeY, hingeZ]}>
      {/* Airflow placed outside the flap's own rotation group, same
          pattern as CircularFlapDamper, so it always draws straight in
          through the wall (-x) regardless of the flap's current swing
          angle. inward: particles start further outside (+x side, away
          from the room) and travel back toward the hole, fading as they
          arrive — so the moving air is only ever visible OUTSIDE the wall,
          never crossing into the kitchen. */}
      <ExhaustAirflow
        position={[0, 0, 0]}
        direction={new THREE.Vector3(1, 0, 0)}
        openness={openness}
        ventilationRate={ventilationRate}
        spread={Math.min(width, height) / 2}
        inward
      />

      <group ref={flapRef}>
        {/* Leaf is centred on the hinge, so it exactly covers the hole at
            swing=0 and stays centred on that same point as it rotates. */}
        <mesh>
          <boxGeometry args={[0.01, width, height]} />
          <meshStandardMaterial
            color={FURNITURE}
            roughness={0.6}
            metalness={0.2}
            side={THREE.DoubleSide}
          />
        </mesh>
      </group>
    </group>
  )
}

/**
 * The leak tube's L-shaped centreline: starts at the wall, runs outFromWall
 * out into the room (+y), then bends and rises `rise` straight up (+z).
 * Shared by LeakTube (the pipe itself) and LeakFlowAnimation (the moving
 * stripe texture painted along the same path), so the two never drift apart
 * if LEAK_TUBE's numbers change. Same technique as the water heater's flue
 * in RoomGeometry.tsx: a quadratic Bezier with its control point at the
 * corner of the bend, so the curve leaves horizontal and arrives vertical.
 */
function useLeakTubeCurve() {
  const { x, zAtWall, outFromWall, rise } = LEAK_TUBE
  return useMemo(() => {
    return new THREE.QuadraticBezierCurve3(
      new THREE.Vector3(x, 0, zAtWall),
      new THREE.Vector3(x, outFromWall, zAtWall),
      new THREE.Vector3(x, outFromWall, zAtWall + rise),
    )
  }, [x, zAtWall, outFromWall, rise])
}

/**
 * Leak tube: the static 3/4" pipe under the water heater (see LEAK_TUBE in
 * roomGeometry.ts). Always present — the leak itself is shown by
 * LeakFlowAnimation running inside it, not by the tube appearing/
 * disappearing. Mostly transparent so the flow animation reads clearly
 * through the pipe wall rather than being hidden inside a solid tube.
 */
export function LeakTube() {
  const curve = useLeakTubeCurve()
  return (
    <mesh>
      <tubeGeometry args={[curve, 16, LEAK_TUBE.diameter / 2, 10, false]} />
      <meshStandardMaterial
        color={FURNITURE}
        roughness={0.5}
        metalness={0.3}
        transparent
        opacity={0.18}
        depthWrite={false}
        side={THREE.DoubleSide}
      />
    </mesh>
  )
}

/** Real-world leak rate range the sandbox slider covers, in L/min. */
export const LEAK_RATE_MIN_LPM = 5
export const LEAK_RATE_MAX_LPM = 20

/** Seconds for the stream to travel the tube's full length at
 *  LEAK_RATE_MIN_LPM. LEAK_RATE_MAX_LPM (4x the rate) takes a quarter of
 *  this — see the linear speed scaling below. */
const SECONDS_PER_TRIP_AT_MIN_RATE = 1

/** How many slugs of fluid are in the tube at once. The tube is short
 *  (~0.15m of path), so only a few fit before they smear together. */
const LEAK_SLUG_REPEATS = 4

/** Texture resolution along the tube. Only the U axis carries the pattern
 *  (it is uniform around the tube's circumference), so this is a
 *  1-pixel-TALL strip. */
const LEAK_STRIPE_TEX_W = 10

/**
 * Fraction of each repeat that is solid fluid. The rest is the gap between
 * slugs — the gap is what makes motion visible, so it has to be a real
 * hole, not a dim region.
 */
const LEAK_SLUG_DUTY = 0.55

/**
 * A LEAK_STRIPE_TEX_W x 1 alpha pattern: a hard-edged slug of fluid
 * followed by a clear gap, repeated along the tube.
 *
 * NOTE ON AXES: tubeGeometry maps U along the LENGTH of the tube and V
 * around its circumference. An earlier version of this had them swapped —
 * the pattern ran as rings around the pipe and scrolling it merely spun
 * those rings about the tube's axis, which from outside a 22mm pipe is
 * invisible and read as a plain coloured tube with no flow at all.
 *
 * The edges are deliberately hard rather than a soft gradient. This tube is
 * only ~0.15m long and 22mm across, so it occupies very few pixels: a gentle
 * ramp across that distance has too little contrast to register as movement.
 * Crisp leading/trailing edges are what the eye can actually track.
 */
function useLeakStripeTexture() {
  return useMemo(() => {
    const data = new Uint8Array(LEAK_STRIPE_TEX_W * 4)
    for (let i = 0; i < LEAK_STRIPE_TEX_W; i++) {
      const t = i / LEAK_STRIPE_TEX_W
      // Solid for the first LEAK_SLUG_DUTY of the cycle, clear after, with
      // a couple of texels of softening at each edge purely to stop the
      // boundary aliasing into a jagged line under linear filtering.
      const softness = 0.04
      const leadingEdge = THREE.MathUtils.smoothstep(t, 0, softness)
      const trailingEdge =
        1 - THREE.MathUtils.smoothstep(t, LEAK_SLUG_DUTY - softness, LEAK_SLUG_DUTY)
      const alpha = leadingEdge * trailingEdge
      const o = i * 4
      data[o] = 0x00
      data[o + 1] = 0xe5
      data[o + 2] = 0xff
      data[o + 3] = Math.round(THREE.MathUtils.clamp(alpha, 0, 1) * 255)
    }
    const tex = new THREE.DataTexture(data, LEAK_STRIPE_TEX_W, 1, THREE.RGBAFormat)
    tex.wrapS = THREE.RepeatWrapping
    tex.wrapT = THREE.RepeatWrapping
    // x = U = along the tube's length. This is the axis that must repeat
    // and the axis that gets scrolled.
    tex.repeat.set(LEAK_SLUG_REPEATS, 1)
    tex.minFilter = THREE.LinearFilter
    tex.magFilter = THREE.LinearFilter
    tex.needsUpdate = true
    return tex
  }, [])
}

/**
 * Leak flow animation: a continuous stream scrolling up the tube from the
 * wall end to the open end. `flowRateLpm` is the real leak rate in L/min
 * (5-20, see LEAK_RATE_MIN_LPM/MAX_LPM) — speed scales linearly with it,
 * so 20 L/min moves 4x faster than 5 L/min.
 *
 * Drawn as a slightly-inset copy of the tube geometry carrying a scrolling
 * pattern of hard-edged fluid slugs, rather than as discrete particle
 * meshes. Scrolling a texture cannot strobe the way a countable handful of
 * blobs did at high rates, and the slugs' crisp edges give the eye
 * something to actually track along a tube this small.
 */
export function LeakFlowAnimation({ flowRateLpm = 0 }: { flowRateLpm?: number }) {
  const curve = useLeakTubeCurve()
  const texture = useLeakStripeTexture()

  // Inset slightly so the stream sits inside the pipe wall rather than
  // z-fighting with it.
  const streamRadius = LEAK_TUBE.diameter / 2 - 0.002

  useEffect(() => () => texture.dispose(), [texture])

  useFrame((_, delta) => {
    if (flowRateLpm <= 0) return
    const tripsPerSecond = flowRateLpm / LEAK_RATE_MIN_LPM / SECONDS_PER_TRIP_AT_MIN_RATE
    // offset.x, because U is the along-the-tube axis (see the axis note on
    // useLeakStripeTexture). Negative so the pattern appears to travel from
    // the wall end toward the open end.
    texture.offset.x -= tripsPerSecond * delta
  })

  return (
    <mesh visible={flowRateLpm > 0}>
      {/* tubularSegments stays high (64): that's the along-the-tube axis
          the scrolling slug texture is sampled against, so it needs to be
          fine enough for the slugs' hard edges to look crisp rather than
          faceted. radialSegments (circumference) barely matters on a 22mm
          pipe — dropped from 12 to 8, a third fewer triangles for no
          visible difference. */}
      <tubeGeometry args={[curve, 64, streamRadius, 8, false]} />
      {/* Basic, not Standard: the slugs should be a self-lit stream of
          fluid, not a lit surface whose shading competes with the pattern.
          Crucially the alpha map must gate the GLOW as well — with
          meshStandardMaterial's flat `emissive` the gaps between slugs
          still emitted full cyan, which on its own was enough to make the
          whole thing read as one evenly-lit tube. Here colour and alpha
          both come from the map, so a gap is genuinely empty. */}
      <meshBasicMaterial
        map={texture}
        alphaMap={texture}
        color="#00E5FF" // Bright cyan cuts straight through the dark grey
        transparent
        opacity={0.95}
        depthWrite={false}
        side={THREE.DoubleSide}
        toneMapped={false}
      />
    </mesh>
  )
}


