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
  FRONT_WINDOW,
  LOWER_CABINET,
  ROOM,
  TOP_CABINET,
  WALL_DAMPER,
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

/**
 * How far each shell surface is pushed OUTWARD, away from the room interior,
 * from its true measured plane.
 *
 * The furniture is genuinely flush against the walls — the lower cabinet runs
 * to x = 2.859 = ROOM.width, the cabinets and water heater all sit at y = 0 —
 * and those are real tape measurements, not approximations. But a wall drawn
 * at exactly that plane is COPLANAR with the furniture face touching it, and
 * coplanar surfaces have no depth ordering: the GPU resolves the tie
 * arbitrarily per pixel, so the cabinets flicker against the wall as the
 * camera orbits (most visibly while OrbitControls' damping is still coasting,
 * which is why it settles after a second or two).
 *
 * Biasing the SHELL rather than the furniture keeps every measurement in
 * roomGeometry.ts exactly as taped — the room is drawn 2mm larger than
 * measured in each direction, which is a hundred times finer than anything
 * legible at this scale and leaves the furniture where it truly is.
 */
const SHELL_BIAS = 0.002

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
  // No castShadow/receiveShadow: the Canvas runs with shadows={false} (see
  // RoomScene), so they were inert flags implying a shadow pass that never
  // happens.
  return (
    <mesh
      position={[
        box.x + box.width / 2,
        box.y + box.depth / 2,
        box.z + box.height / 2,
      ]}
    >
      <boxGeometry args={[box.width, box.depth, box.height]} />
      <meshStandardMaterial
        color={color}
        transparent={opacity < 1}
        opacity={opacity}
        roughness={0.85}
        metalness={0.05}
        side={THREE.DoubleSide}
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

  // Flue: rises `rise` straight up from the heater's top, then turns back
  // into the back wall, ending flush in the wall plane. The control point
  // sits at the corner of that turn, so the tube leaves vertical and
  // arrives horizontal instead of overshooting past the wall.
  const flueCurve = useMemo(() => {
    const topCentreX = x + width / 2
    const topZ = z + height
    const frontY = y + depth / 2
    const riseZ = topZ + flue.rise
    return new THREE.QuadraticBezierCurve3(
      new THREE.Vector3(topCentreX, frontY, topZ),
      new THREE.Vector3(topCentreX, frontY, riseZ),
      new THREE.Vector3(topCentreX, y, riseZ),
    )
  }, [x, y, z, width, depth, height, flue.rise])

  return (
    <group>
      <mesh position={[x, y + depth, z]} rotation={[Math.PI / 2, 0, 0]}>
        <extrudeGeometry args={[shape, { depth, bevelEnabled: false, curveSegments: 8 }]} />
        <meshStandardMaterial
          color={FURNITURE}
          roughness={0.7}
          metalness={0.15}
          side={THREE.DoubleSide}
        />
      </mesh>
      <mesh>
        <tubeGeometry args={[flueCurve, 16, flue.diameter / 2, 10, false]} />
        <meshStandardMaterial
          color={FURNITURE}
          roughness={0.6}
          metalness={0.2}
          side={THREE.DoubleSide}
        />
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

      // Top cap across the throat ring (4-7). The duct above is round and
      // only 0.15 across while the throat is 0.228 x 0.181, so without this
      // the taper ends in an open rectangle and you see straight down into
      // the hollow hood from above. The cap is flat, spans the full throat,
      // and the duct cylinder simply sits on top of it.
      4, 5, 6,  4, 6, 7,
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
      <mesh geometry={geometry}>
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
        <meshStandardMaterial
          color={FURNITURE}
          roughness={0.6}
          metalness={0.25}
          side={THREE.DoubleSide}
        />
      </mesh>
    </group>
  )
}

/**
 * A wall with rectangular openings cut out of it.
 *
 * The openings are real holes in the geometry, not transparent panes laid
 * over a solid wall: a pane still catches light and still hides whatever is
 * behind it, so at any opacity above zero it reads as glass rather than as
 * an opening, and at zero it disappears entirely. Cutting the hole means you
 * see straight through to the room beyond, which is what a doorway looks
 * like, and the wall's own outline draws the frame for free.
 *
 * `holes` are given in the wall's own 2D frame: `u` runs along the wall and
 * `v` runs up it, both from the wall's min corner.
 */
function WallWithHoles({
  uSize,
  vSize,
  holes,
  color,
  opacity = 1,
  position,
  rotation,
  renderOrder = 0,
}: {
  uSize: number
  vSize: number
  holes: { u: number; v: number; width: number; height: number }[]
  color: string
  opacity?: number
  position: [number, number, number]
  rotation: [number, number, number]
  /** Forced draw order. The semi-transparent walls need this: three.js sorts
   *  transparent meshes by camera distance every frame, so as the camera
   *  orbits past the furniture their order flips and the cabinets blink in
   *  and out from behind them. A fixed negative order draws the shell first,
   *  always, regardless of viewing angle. */
  renderOrder?: number
}) {
  const shape = useMemo(() => {
    const s = new THREE.Shape()
    s.moveTo(0, 0)
    s.lineTo(uSize, 0)
    s.lineTo(uSize, vSize)
    s.lineTo(0, vSize)
    s.lineTo(0, 0)
    for (const h of holes) {
      const path = new THREE.Path()
      path.moveTo(h.u, h.v)
      path.lineTo(h.u + h.width, h.v)
      path.lineTo(h.u + h.width, h.v + h.height)
      path.lineTo(h.u, h.v + h.height)
      path.lineTo(h.u, h.v)
      s.holes.push(path)
    }
    return s
  }, [uSize, vSize, holes])

  return (
    <mesh position={position} rotation={rotation} renderOrder={renderOrder}>
      <shapeGeometry args={[shape]} />
      <meshBasicMaterial
        color={color}
        side={THREE.DoubleSide}
        transparent={opacity < 1}
        opacity={opacity}
        depthWrite={opacity >= 1}
      />
      <Edges threshold={1} color={EDGE} />
    </mesh>
  )
}

/**
 * Floor, ceiling and the four walls, as separate planes.
 *
 * A closed box of opaque faces was tried first and does not work: whichever
 * face is toward the camera occludes the whole interior, and the room reads
 * as a solid block. So the two walls on the camera's side — right (x = width)
 * and front (y = depth) — are drawn at low opacity instead of being left out
 * altogether. That closes the volume without curtaining off the inside, and
 * the floor, back wall and left wall stay opaque so the equipment has
 * something solid to read against.
 *
 * The door and both windows are holes cut out of their walls, so they show
 * whatever lies beyond rather than being tinted panes over a solid surface.
 */
function Shell() {
  const { width, depth, height } = ROOM
  return (
    <group>
      {/* Floor. Unlit (meshBasic) like the rest of the shell: the room is a
          container, and letting it take highlights competes with the field,
          which is the only thing here whose brightness should mean
          something. */}
      <mesh position={[width / 2, depth / 2, -SHELL_BIAS]} renderOrder={-1}>
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

      {/* Back wall (y = 0): the equipment wall everything hangs from. Every
          piece of furniture has y: 0, so this is the worst coplanar case in
          the room — see SHELL_BIAS. */}
      <mesh
        position={[width / 2, -SHELL_BIAS, height / 2]}
        rotation={[Math.PI / 2, 0, 0]}
        renderOrder={-1}
      >
        <planeGeometry args={[width, height]} />
        <meshBasicMaterial color={SURFACE} side={THREE.DoubleSide} />
      </mesh>

      {/* Left wall (x = 0): carries the door, cut as a real opening.
          shapeGeometry is built in its own XY plane from the min corner, so
          these walls are positioned by that corner rather than by centre.
          Rotation is [PI/2, PI/2, 0], NOT [PI/2, 0, PI/2] — the latter
          yaws the plane about the already-rotated axis and lands it
          spanning x[-1.28,1.28] at y=1.17, i.e. a panel slicing through the
          middle of the room. */}
      <WallWithHoles
        uSize={depth}
        vSize={height}
        holes={[{ u: DOOR.y, v: DOOR.z, width: DOOR.width, height: DOOR.height }]}
        color="#1D242F"
        position={[-SHELL_BIAS, 0, 0]}
        rotation={[Math.PI / 2, Math.PI / 2, 0]}
        renderOrder={-1}
      />

      {/* Right wall (x = width), carrying the side window, and front wall
          (y = depth), carrying the big one. Both sit between the camera and
          the room, so they are semi-transparent: solid, they would occlude
          the interior the way a closed box does. */}
      <WallWithHoles
        uSize={depth}
        vSize={height}
        holes={[
          { u: WINDOW.y, v: WINDOW.z, width: WINDOW.width, height: WINDOW.height },
          {
            u: WALL_DAMPER.y,
            v: WALL_DAMPER.z,
            width: WALL_DAMPER.width,
            height: WALL_DAMPER.height,
          },
        ]}
        color="#1D242F"
        opacity={0.4}
        position={[width + SHELL_BIAS, 0, 0]}
        rotation={[Math.PI / 2, Math.PI / 2, 0]}
        renderOrder={-1}
      />

      <WallWithHoles
        uSize={width}
        vSize={height}
        holes={[
          {
            u: FRONT_WINDOW.x,
            v: FRONT_WINDOW.z,
            width: FRONT_WINDOW.width,
            height: FRONT_WINDOW.height,
          },
        ]}
        color={SURFACE}
        opacity={0.4}
        position={[0, depth + SHELL_BIAS, 0]}
        rotation={[Math.PI / 2, 0, 0]}
        renderOrder={-1}
      />
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
      <SolidBox box={LOWER_CABINET} />
      <SolidBox box={TOP_CABINET} />
      <ExhaustHood />
      <WaterHeater />
    </group>
  )
}
