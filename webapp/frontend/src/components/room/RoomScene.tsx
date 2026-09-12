/**
 * RoomScene — the 3D view, and the hero of this interface.
 *
 * Two modes, one click apart:
 *   sensors  markers only. No interpolation, no invention. The baseline.
 *   field    interpolated concentration with confidence fade, markers on top.
 *
 * Keeping sensors-only one click away is itself a safeguard: if the field
 * ever looks implausible, it can be checked against raw values immediately.
 *
 * The room's own frame is Z-up (see lib/roomGeometry.ts) while Three.js is
 * Y-up, so the whole scene group is rotated once here. Every child then works
 * in the room's measured metres.
 */

import { OrbitControls } from '@react-three/drei'
import { Canvas, useThree } from '@react-three/fiber'
import { Suspense, useEffect } from 'react'
import * as THREE from 'three'

import type { KitchenState } from '@/api/types'
import type { LiveSensor } from '@/hooks/useKitchen'
import { DEFAULT_ANISOTROPY, type SensorSample } from '@/lib/interpolation'
import { ROOM } from '@/lib/roomGeometry'
import { CentralDamper, Damper, LeakFlowAnimation, LeakTube, WallDamper } from './Equipment'
import { Field } from './Field'
import { RoomGeometry } from './RoomGeometry'
import { SensorMarkers } from './SensorMarkers'

export type ViewMode = 'sensors' | 'field'

/** mL/s -> L/min, the unit LeakFlowAnimation's speed scaling is defined in
 *  (see Equipment.tsx's LEAK_RATE_MIN/MAX_LPM — carried over from the
 *  sandbox's slider, which used the same real-world range). */
const ML_PER_S_TO_L_PER_MIN = 60 / 1000

/**
 * Pulls the camera back far enough that the whole room fits the canvas,
 * whatever shape that canvas is.
 *
 * The Control view gives the room two-thirds of a wide screen; the Analysis
 * view gives it a narrow side column. A distance that frames the room in the
 * first crops it badly in the second, so the distance is derived from the
 * room's bounding sphere against the CURRENT aspect and fov, and redone
 * whenever the canvas resizes.
 */
function FitCamera() {
  const { camera, size } = useThree()

  useEffect(() => {
    const cam = camera as THREE.PerspectiveCamera
    if (!cam.isPerspectiveCamera) return

    const centre = new THREE.Vector3(ROOM.width / 2, ROOM.depth / 2, ROOM.height / 2)
    const radius = Math.hypot(ROOM.width, ROOM.depth, ROOM.height) / 2

    const vFov = (cam.fov * Math.PI) / 180
    // A narrow canvas is constrained horizontally, so the horizontal field
    // of view is what has to accommodate the room there.
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * cam.aspect)
    const distance = (radius / Math.sin(Math.min(vFov, hFov) / 2)) * 1.06

    // Keep the established viewing direction; only change how far along it
    // the camera sits.
    const dir = cam.position.clone().sub(centre).normalize()
    cam.position.copy(centre).addScaledVector(dir, distance)
    cam.lookAt(centre)
    cam.updateProjectionMatrix()
  }, [camera, size.width, size.height])

  return null
}

export function RoomScene({
  sensors,
  samples,
  mode,
  stale = false,
  showLabels = true,
  anisotropy = DEFAULT_ANISOTROPY,
  kitchenState,
}: {
  sensors: LiveSensor[]
  samples: SensorSample[]
  mode: ViewMode
  stale?: boolean
  showLabels?: boolean
  anisotropy?: number
  /** Drives the dampers and leak-flow animation. Omitted entirely in
   *  contexts with no live control-system state (AnalysisView's replay) —
   *  the equipment then simply doesn't render, same as `kitchenState` being
   *  absent because the backend hasn't seen a payload yet. */
  kitchenState?: KitchenState
}) {
  const phase = kitchenState?.phase ?? kitchenState?.state
  // Dampers only actually move air while the fan is commanded to run; in
  // every other phase the registers reflect the NEXT ventilating run's
  // configuration, not anything currently open — same gating the sandbox
  // used (see SandboxScene.tsx).
  const ventilating = phase === 'VENTILATING' || phase === 'FULLY_VENTILATING'
  const leaking = phase === 'LEAKING'
  const registers = kitchenState?.registers
  const ventilationRate = kitchenState?.fanSpeedPct ?? 0
  const leakFlowLpm = (kitchenState?.flowRate_mLps ?? 0) * ML_PER_S_TO_L_PER_MIN
  return (
    <Canvas
      className="room-enter"
      shadows={false}
      dpr={[1, 2]}
      camera={{
        // In front of the room (large +y, since +y runs from the back wall
        // toward the viewer), off to one side and above standing height,
        // looking back at the equipment wall. The two walls on this side are
        // drawn semi-transparent (see RoomGeometry) so the interior reads
        // through them.
        //
        // Handedness is corrected by the scene mirror below. Don't also move
        // the camera to -x to fix a mirrored-looking render: two mirrors
        // cancel and the room comes back the way it started.
        position: [ROOM.width * 1.15, ROOM.depth * 2.6, ROOM.height * 1.35],
        fov: 42,
        near: 0.05,
        far: 60,
      }}
      gl={{ antialias: true, alpha: false }}
      onCreated={({ gl, camera }) => {
        gl.setClearColor('#0A0C0F')
        camera.up.set(0, 0, 1) // room frame is Z-up
        camera.lookAt(ROOM.width / 2, ROOM.depth / 2, ROOM.height / 2)
      }}
    >
      <Suspense fallback={null}>
        {/* Neutral, even, and bright enough that the furniture stays legible
            through the field. Nothing here is theatrical; the only colour in
            the scene should be concentration, so the lights are white and
            the shading is flat. */}
        <ambientLight intensity={1.6} />
        <directionalLight position={[4, 6, 5]} intensity={0.8} />
        <directionalLight position={[-3, 2, 3]} intensity={0.4} />

        <FitCamera />

        {/* SCENE MIRROR — the room is measured facing the back wall, but the
            camera views that wall from the far side, so x runs right-to-left
            on screen and the whole room reads mirrored: the door landed on
            the right and the window on the left.

            Fixed here rather than in the measurements. Every x in
            roomGeometry is a real tape measurement, and negating them one by
            one to chase the render is what left that file self-contradictory
            before. Mirroring the scene once keeps every object's measured
            position and spacing intact.

            A negative x scale is the only transform that mirrors x alone —
            a 180° spin about z would flip y too and swing the equipment wall
            round to the near side. The mirror reverses triangle winding, so
            materials below render DoubleSide where back-face culling would
            otherwise punch holes in the furniture. */}
        <group scale={[-1, 1, 1]} position={[ROOM.width, 0, 0]}>
          <RoomGeometry />

          {mode === 'field' && (
            <Field samples={stale ? [] : samples} anisotropy={anisotropy} />
          )}

          {/* Equipment: dampers always render (they read as closed at
              open=0 with no kitchenState), the leak tube is likewise
              always present, and its flow only draws while actually
              leaking — matching Equipment.tsx/SandboxScene's own rules. */}
          <Damper open={ventilating && registers?.exhaust ? 1 : 0} ventilationRate={ventilationRate} />
          <WallDamper open={ventilating && registers?.inlet ? 1 : 0} ventilationRate={ventilationRate} />
          <CentralDamper open={ventilating && registers?.central ? 1 : 0} ventilationRate={ventilationRate} />
          <LeakTube />
          {leaking && leakFlowLpm > 0 && <LeakFlowAnimation flowRateLpm={leakFlowLpm} />}

          {/* Markers stay on top in both modes. */}
          <SensorMarkers sensors={sensors} showLabels={showLabels} stale={stale} />
        </group>

        <OrbitControls
          target={[ROOM.width / 2, ROOM.depth / 2, ROOM.height / 2]}
          enablePan
          enableDamping
          dampingFactor={0.12}
          minDistance={1.2}
          maxDistance={14}
          makeDefault
        />
      </Suspense>
    </Canvas>
  )
}
