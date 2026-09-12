/**
 * SandboxScene — RoomScene's camera/lighting/mirror setup, minus sensors and
 * the concentration field, plus the new Equipment pieces. Kept as its own
 * small Canvas rather than reusing RoomScene directly so this sandbox never
 * has to satisfy RoomScene's sensors/samples props with fake data.
 *
 * See RoomScene.tsx for why the scene is mirrored and why the camera sits
 * where it does — copied verbatim here so the room reads identically.
 */

import { OrbitControls } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import { Suspense } from 'react'

import { RoomGeometry } from '@/components/room/RoomGeometry'
import {
  CentralDamper,
  Damper,
  LeakFlowAnimation,
  LeakTube,
  WallDamper,
} from '@/components/room/Equipment'
import { ROOM } from '@/lib/roomGeometry'
import type { DamperState, SandboxMode } from './DevModePanel'

export function SandboxScene({
  mode,
  dampers,
  leakFlowRate,
  ventilationRate,
}: {
  mode: SandboxMode
  dampers: DamperState
  leakFlowRate: number
  ventilationRate: number
}) {
  const ventilating = mode === 'VENTILATING'
  const leaking = mode === 'LEAKING'

  return (
    <Canvas
      shadows={false}
      dpr={[1, 2]}
      camera={{
        position: [ROOM.width * 1.15, ROOM.depth * 2.6, ROOM.height * 1.35],
        fov: 42,
        near: 0.05,
        far: 60,
      }}
      gl={{ antialias: true, alpha: false }}
      onCreated={({ gl, camera }) => {
        gl.setClearColor('#0A0C0F')
        camera.up.set(0, 0, 1)
        camera.lookAt(ROOM.width / 2, ROOM.depth / 2, ROOM.height / 2)
      }}
    >
      <Suspense fallback={null}>
        <ambientLight intensity={1.6} />
        <directionalLight position={[4, 6, 5]} intensity={0.8} />
        <directionalLight position={[-3, 2, 3]} intensity={0.4} />

        <group scale={[-1, 1, 1]} position={[ROOM.width, 0, 0]}>
          <RoomGeometry />
          {/* Checkboxes pick which dampers WOULD open; ventilating gates
              whether they actually do. In WAITING/LEAKING every damper
              stays closed regardless of which are checked. */}
          <Damper open={ventilating && dampers.exhaust ? 1 : 0} ventilationRate={ventilationRate} />
          <WallDamper open={ventilating && dampers.inlet ? 1 : 0} ventilationRate={ventilationRate} />
          <CentralDamper open={ventilating && dampers.central ? 1 : 0} ventilationRate={ventilationRate} />
          <LeakTube />
          {leaking && leakFlowRate > 0 && (
            <LeakFlowAnimation flowRateLpm={leakFlowRate} />
          )}
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
