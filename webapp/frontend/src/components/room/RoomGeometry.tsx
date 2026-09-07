/**
 * RoomGeometry — the kitchen's fixed civil geometry as Three.js primitives.
 *
 * Drawn quietly: this is the container the hydrogen moves through, not the
 * subject. Walls are near-transparent so the field reads through them,
 * furniture is flat dark grey with visible edges, and nothing here uses a
 * semantic colour — green, amber and red belong to status alone.
 *
 * Three.js is Y-up while the room's own frame is Z-up (see roomGeometry.ts),
 * so <RoomScene> rotates the whole group once rather than every component
 * doing its own axis juggling. Inside this file, positions are the room's
 * own metres exactly as measured.
 */

import { Edges } from '@react-three/drei'
import { useMemo } from 'react'
import * as THREE from 'three'

import {
  DOOR,
  EXHAUST,
  LOWER_CABINET,
  ROOM,
  TOP_CABINET,
  WATER_HEATER,
  WINDOW,
  type Box,
} from '@/lib/roomGeometry'

// Furniture sits clearly lighter than the walls: against a near-black page,
// and behind a translucent field, a dark box reads as a hole rather than an
// object. Bright edges carry the shape — they stay legible even where the
// field is densest, which is exactly where the operator needs to know what
// the gas is sitting against.
const SURFACE = '#232A36'
const FURNITURE = '#4A5665'
const EDGE = '#8793A5'
const OPENING = '#4A5568'

/** A box positioned by its min corner, the way the measurements are written. */
function SolidBox({
  box,
  color = FURNITURE,
  opacity = 1,
}: {
  box: Box
  color?: string
  opacity?: number
}) {
  return (
    <mesh
      position={[
        box.x + box.width / 2,
        box.y + box.depth / 2,
        box.z + box.height / 2,
      ]}
      castShadow
      receiveShadow
    >
      <boxGeometry args={[box.width, box.depth, box.height]} />
      <meshStandardMaterial
        color={color}
        transparent={opacity < 1}
        opacity={opacity}
        roughness={0.85}
        metalness={0.05}
      />
      <Edges threshold={15} color={EDGE} />
    </mesh>
  )
}

/** Water heater: rounded box, as asked, with a curved flue into the wall. */
function WaterHeater() {
  const { x, y, z, width, depth, height, cornerRadius, flue } = WATER_HEATER

  // Rounded via a small extrude with a rounded profile — cheaper and more
  // predictable than pulling in RoundedBoxGeometry, and the radius is small
  // enough that a chamfered silhouette reads the same at this scale.
  const shape = useMemo(() => {
    const s = new THREE.Shape()
    const w = width
    const h = height
    const r = Math.min(cornerRadius, w / 2, h / 2)
    s.moveTo(r, 0)
    s.lineTo(w - r, 0)
    s.quadraticCurveTo(w, 0, w, r)
    s.lineTo(w, h - r)
    s.quadraticCurveTo(w, h, w - r, h)
    s.lineTo(r, h)
    s.quadraticCurveTo(0, h, 0, h - r)
    s.lineTo(0, r)
    s.quadraticCurveTo(0, 0, r, 0)
    return s
  }, [width, height, cornerRadius])

  // Flue: sweeps up out of the heater's top, then curves back into the wall.
  const flueCurve = useMemo(() => {
    const topCentreX = x + width / 2
    const topZ = z + height
    const frontY = y + depth / 2
    return new THREE.QuadraticBezierCurve3(
      new THREE.Vector3(topCentreX, frontY, topZ),
      new THREE.Vector3(topCentreX, frontY, topZ + 0.22),
      new THREE.Vector3(topCentreX, y + 0.02, topZ + 0.30),
    )
  }, [x, y, z, width, depth, height])

  return (
    <group>
      <mesh position={[x, y + depth, z]} rotation={[Math.PI / 2, 0, 0]} castShadow>
        <extrudeGeometry args={[shape, { depth, bevelEnabled: false, curveSegments: 8 }]} />
        <meshStandardMaterial color={FURNITURE} roughness={0.7} metalness={0.15} />
      </mesh>
      <mesh castShadow>
        <tubeGeometry args={[flueCurve, 16, flue.diameter / 2, 10, false]} />
        <meshStandardMaterial color={FURNITURE} roughness={0.6} metalness={0.2} />
      </mesh>
    </group>
  )
}

/**
 * Extraction hood: a trapezoid tapering from the wide lower mouth to the
 * small throat at the wall, then a round duct to the ceiling.
 *
 * Built as a lathe-free custom geometry: eight vertices, two rectangles,
 * joined by four quads. Simpler and more faithful than approximating the
 * taper with a cylinder.
 */
function ExhaustHood() {
  const geometry = useMemo(() => {
    const { mouth, throat } = EXHAUST
    const mx0 = mouth.x
    const mx1 = mouth.x + mouth.width
    const my0 = mouth.y
    const my1 = mouth.y + mouth.depth
    const mz = mouth.z

    const cx = mouth.x + mouth.width / 2
    const cy = mouth.y + mouth.depth / 2
    const tx0 = cx - throat.width / 2
    const tx1 = cx + throat.width / 2
    const ty0 = cy - throat.depth / 2
    const ty1 = cy + throat.depth / 2
    const tz = throat.z

    // prettier-ignore
    const positions = new Float32Array([
      // mouth ring (0-3), throat ring (4-7)
      mx0, my0, mz,  mx1, my0, mz,  mx1, my1, mz,  mx0, my1, mz,
      tx0, ty0, tz,  tx1, ty0, tz,  tx1, ty1, tz,  tx0, ty1, tz,
    ])
    // prettier-ignore
    const indices = [
      0, 1, 5,  0, 5, 4,   // front face
      1, 2, 6,  1, 6, 5,   // right
      2, 3, 7,  2, 7, 6,   // back
      3, 0, 4,  3, 4, 7,   // left
    ]

    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    g.setIndex(indices)
    g.computeVertexNormals()
    return g
  }, [])

  const { throat, duct } = EXHAUST
  const cx = EXHAUST.mouth.x + EXHAUST.mouth.width / 2
  const cy = EXHAUST.mouth.y + EXHAUST.mouth.depth / 2
  const ductHeight = duct.zTop - throat.z

  return (
    <group>
      <mesh geometry={geometry} castShadow>
        <meshStandardMaterial
          color={FURNITURE}
          side={THREE.DoubleSide}
          roughness={0.6}
          metalness={0.25}
        />
        <Edges threshold={15} color={EDGE} />
      </mesh>
      <mesh position={[cx, cy, throat.z + ductHeight / 2]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[duct.diameter / 2, duct.diameter / 2, ductHeight, 20]} />
        <meshStandardMaterial color={FURNITURE} roughness={0.6} metalness={0.25} />
      </mesh>
    </group>
  )
}

/** Door and window as recessed panels on their walls. */
function Openings() {
  return (
    <group>
      {/* Door, left wall (x = 0) */}
      <mesh position={[0.012, DOOR.y + DOOR.width / 2, DOOR.z + DOOR.height / 2]}>
        <boxGeometry args={[0.02, DOOR.width, DOOR.height]} />
        <meshStandardMaterial color={OPENING} roughness={0.9} />
        <Edges threshold={15} color={EDGE} />
      </mesh>

      {/* Window, right wall (x = ROOM.width) */}
      <mesh
        position={[
          ROOM.width - 0.012,
          WINDOW.y + WINDOW.width / 2,
          WINDOW.z + WINDOW.height / 2,
        ]}
      >
        <boxGeometry args={[0.02, WINDOW.width, WINDOW.height]} />
        <meshStandardMaterial
          color="#5B7A99"
          transparent
          opacity={0.35}
          roughness={0.1}
          metalness={0.1}
        />
        <Edges threshold={15} color={EDGE} />
      </mesh>
    </group>
  )
}

/**
 * Floor, ceiling and the two equipment-bearing walls, as separate planes.
 *
 * A closed box was tried first and does not work: whichever face is toward
 * the camera occludes the whole interior, and the room reads as a solid
 * block. Drawing the shell as an open cutaway — floor, ceiling, back wall
 * and left wall only — leaves the interior permanently visible from the
 * default viewpoint while still giving the space a floor to sit on and a
 * back wall for the equipment to hang from.
 */
function Shell() {
  const { width, depth, height } = ROOM
  return (
    <group>
      {/* Floor. Unlit (meshBasic) like the rest of the shell: the room is a
          container, and letting it take highlights competes with the field,
          which is the only thing here whose brightness should mean
          something. */}
      <mesh position={[width / 2, depth / 2, 0]}>
        <planeGeometry args={[width, depth]} />
        <meshBasicMaterial color="#141922" side={THREE.DoubleSide} />
        <Edges threshold={1} color="#46536B" />
      </mesh>

      {/* Ceiling — a wireframe only. Hydrogen collects against it, so a
          solid lid (even a faint one) would sit directly over the region
          that matters most and wash it out. The duct still has an edge to
          terminate against. */}
      <mesh position={[width / 2, depth / 2, height]}>
        <planeGeometry args={[width, depth]} />
        <meshBasicMaterial visible={false} />
        <Edges threshold={1} color="#46536B" />
      </mesh>

      {/* Back wall (y = 0): the equipment wall everything hangs from. */}
      <mesh position={[width / 2, 0, height / 2]} rotation={[Math.PI / 2, 0, 0]}>
        <planeGeometry args={[width, height]} />
        <meshBasicMaterial color={SURFACE} side={THREE.DoubleSide} />
      </mesh>

      {/* Left wall (x = 0): carries the door.
          Rotation is [PI/2, PI/2, 0], NOT [PI/2, 0, PI/2] — the latter
          yaws the plane about the already-rotated axis and lands it
          spanning x[-1.28,1.28] at y=1.17, i.e. a panel slicing through the
          middle of the room. */}
      <mesh position={[0, depth / 2, height / 2]} rotation={[Math.PI / 2, Math.PI / 2, 0]}>
        <planeGeometry args={[depth, height]} />
        <meshBasicMaterial color="#1D242F" side={THREE.DoubleSide} />
      </mesh>
    </group>
  )
}

/** The wireframe outline that makes the room's extent legible at a glance. */
function Outline() {
  return (
    <mesh position={[ROOM.width / 2, ROOM.depth / 2, ROOM.height / 2]}>
      <boxGeometry args={[ROOM.width, ROOM.depth, ROOM.height]} />
      <meshBasicMaterial visible={false} />
      <Edges threshold={1} color="#46536B" />
    </mesh>
  )
}

export function RoomGeometry() {
  return (
    <group>
      <Shell />
      <Outline />
      <Openings />
      <SolidBox box={LOWER_CABINET} />
      <SolidBox box={TOP_CABINET} />
      <ExhaustHood />
      <WaterHeater />
    </group>
  )
}
