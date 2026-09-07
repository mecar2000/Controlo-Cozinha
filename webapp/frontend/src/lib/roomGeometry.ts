/**
 * roomGeometry — the kitchen's fixed civil geometry, in metres.
 *
 * Hardcoded on purpose: this is a real room that changes only on renovation,
 * so it lives in code while sensor positions (which churn) live in the
 * database. See the design spec's "Room and sensors".
 *
 * COORDINATE FRAME — origin at the floor corner where the BACK wall meets
 * the LEFT wall, as seen standing in the room facing the back wall:
 *
 *   x  along the back wall, left -> right      0 .. 2.859
 *   y  away from the back wall into the room   0 .. 2.341
 *   z  up from the floor                       0 .. 2.560
 *
 * The back wall (y = 0) is the equipment wall: it carries the lower cabinet,
 * the top cabinet, the extraction hood and the water heater. Sensor X/Y/Z in
 * `sensor_config` are entered by hand against this same frame.
 *
 * Measured with a tape measure on 2026-09-07. Every value below is a real
 * measurement, not a placeholder.
 */

/** Interior dimensions of the room itself. */
export const ROOM = {
  width: 2.859, // x — the back wall, carrying all equipment
  depth: 2.341, // y — back wall to near wall
  height: 2.56, // z — floor to ceiling
} as const

/** Lower cabinet: full-depth counter run, flush against the right wall. */
export const LOWER_CABINET = {
  x: 0.241,
  y: 0,
  z: 0,
  width: 2.618,
  depth: 0.597,
  height: 0.889,
} as const

/** Top cabinet: wall unit, left-aligned with the lower cabinet below it. */
export const TOP_CABINET = {
  x: 0.241,
  y: 0,
  z: 1.484, // 0.595 clearance above the lower cabinet's 0.889 top
  width: 0.994,
  depth: 0.37,
  height: 0.594,
} as const

/**
 * Extraction hood: a trapezoid narrowing from a wide lower mouth to a small
 * throat at the wall, then a round duct up to the ceiling. Sits to the right
 * of the top cabinet in x, but higher — 0.162 above that cabinet's top.
 */
export const EXHAUST = {
  /** Wide lower mouth. */
  mouth: {
    x: 1.235, // starts where the top cabinet ends
    y: 0,
    z: 2.24, // TOP_CABINET top (2.078) + 0.162
    width: 0.6,
    depth: 0.431,
  },
  /** Narrow upper throat, centred on the mouth, flat against the wall. */
  throat: {
    z: 2.446, // 0.206 above the mouth
    width: 0.228,
    depth: 0.181,
  },
  /** Round duct from the throat to the ceiling. */
  duct: {
    diameter: 0.15,
    zTop: ROOM.height,
  },
} as const

/**
 * Water heater: rounded-corner box on the back wall, right-hand end, with a
 * curved flue sweeping from its top back into the wall.
 */
export const WATER_HEATER = {
  x: 2.481, // 0.088 from the right wall (2.859 - 0.088 - 0.340)
  y: 0,
  z: 1.562, // 0.673 above the lower cabinet's 0.889 top
  width: 0.34,
  depth: 0.273,
  height: 0.57,
  cornerRadius: 0.04,
  flue: { diameter: 0.08 },
} as const

/** Door in the left wall (x = 0), measured from the near wall. */
export const DOOR = {
  wall: 'left',
  y: 0.65,
  z: 0,
  width: 0.83,
  height: 2.032,
} as const

/** Window in the right wall (x = ROOM.width), measured from the back wall. */
export const WINDOW = {
  wall: 'right',
  y: 0.6,
  z: 1.109,
  width: 0.938,
  height: 0.944,
} as const

/** Room centre at standing head height — the default camera target. */
export const ROOM_CENTRE: [number, number, number] = [
  ROOM.width / 2,
  ROOM.depth / 2,
  ROOM.height / 2,
]

/**
 * Solid volumes, as axis-aligned boxes. The field interpolation uses these to
 * skip grid cells inside furniture — there is no air there to hold hydrogen,
 * and drawing concentration inside a cabinet is visibly wrong.
 *
 * The hood is approximated by its lower mouth: the trapezoid's taper is a
 * rendering detail, and over-claiming solidity near the ceiling would hide
 * exactly the region where H2 collects.
 */
export const SOLID_VOLUMES = [
  LOWER_CABINET,
  TOP_CABINET,
  {
    x: EXHAUST.mouth.x,
    y: EXHAUST.mouth.y,
    z: EXHAUST.mouth.z,
    width: EXHAUST.mouth.width,
    depth: EXHAUST.mouth.depth,
    height: EXHAUST.throat.z - EXHAUST.mouth.z,
  },
  WATER_HEATER,
] as const

export interface Box {
  x: number
  y: number
  z: number
  width: number
  depth: number
  height: number
}

/** True when a point falls inside any solid volume. */
export function isInsideSolid(x: number, y: number, z: number): boolean {
  for (const b of SOLID_VOLUMES) {
    if (
      x >= b.x &&
      x <= b.x + b.width &&
      y >= b.y &&
      y <= b.y + b.depth &&
      z >= b.z &&
      z <= b.z + b.height
    ) {
      return true
    }
  }
  return false
}

/** True when a point falls within the room's interior air volume. */
export function isInsideRoom(x: number, y: number, z: number): boolean {
  return (
    x >= 0 && x <= ROOM.width && y >= 0 && y <= ROOM.depth && z >= 0 && z <= ROOM.height
  )
}
