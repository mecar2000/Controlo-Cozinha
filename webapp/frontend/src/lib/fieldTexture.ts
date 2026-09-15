/**
 * fieldTexture — sensor readings packed into a 3D texture for the volume shader.
 *
 * The renderer raymarches a single box against a Data3DTexture rather than
 * compositing a stack of planes. That moves two decisions out of the draw loop
 * and into this file: what each texel carries, and where in the room it sits.
 *
 * WHAT EACH TEXEL CARRIES. Trilinear filtering interpolates whatever is stored,
 * so storing COLOUR would interpolate colour — and the ramp is non-linear twice
 * over (a knee at LEL, then eight piecewise stops), which makes
 * ramp(lerp(a,b)) != lerp(ramp(a),ramp(b)). The midpoint of 1.0 and 3.0 %v/v
 * would render as a straight teal-to-orange blend rather than the colour of
 * 2.0 %v/v. Storing scalars and ramping in the shader interpolates the
 * concentration instead, which is the quantity that actually varies smoothly.
 *
 * It also fixes a second-order bug the slice renderer had: premultiplied colour
 * filtered across a cabinet boundary drags neighbouring RGB toward black, so
 * gas beside furniture darkened for no physical reason.
 *
 * WHERE EACH TEXEL SITS. See the texel-centre note on packField — it is the
 * most bug-prone arithmetic in the whole change.
 */

import { concentrationToRgb, rampPosition, rampPositionToPctVv } from './colorScale'
import { sampleField, type SensorSample } from './interpolation'
import { isInsideSolid, ROOM } from './roomGeometry'

/**
 * Texels per axis.
 *
 * Horizontal stays at 32 (~9cm), which is already finer than the field is
 * resolved — the sensors are 1.2-1.6m apart and IDW between them is smooth, so
 * texels beyond that spacing interpolate an interpolation.
 *
 * Vertical is 96 (~2.7cm) rather than a tidy 32. Hydrogen stratifies, so the
 * vertical gradient is the one carrying the information; dropping z to 32 would
 * have tripled the spacing on the only axis where it matters. A non-cube 3D
 * texture costs nothing to sample.
 */
export const TEX_W = 32
export const TEX_H = 32
export const TEX_D = 96

/** Concentrations below this contribute nothing. */
export const VISIBILITY_FLOOR_PCT_VV = 0.04

/** Concentration at which a texel reaches full density. */
export const FULL_ALPHA_AT_PCT_VV = 3.0

/**
 * Optical depth through the full room height at full density and confidence.
 *
 * Hand-tuned, and carried over verbatim from the slice renderer it replaces,
 * where it appeared as MAX_SLICE_ALPHA = 5.4 / SLICE_COUNT. Raising it makes
 * the field saturate into a flat wash that hides the room.
 *
 * The slice stack only had correct optical depth for VERTICAL rays — a grazing
 * ray passed between planes and picked up almost nothing, which is the banding
 * the slice count kept being raised to chase. The raymarch is correct in every
 * direction, so side views legitimately read denser than the old build. Re-tune
 * here, against the real room, rather than compensating elsewhere.
 */
export const TOTAL_OPTICAL_DEPTH = 5.4

/**
 * Absorption coefficient, per metre. Derived so a vertical ray matches the old
 * slice stack exactly:
 *
 *   T = (1 - alpha)^96  with alpha = 5.4/96    [slices]
 *   T = exp(-sigma * H)                        [raymarch]
 *
 * ln(1-a) ~= -a for a <= 0.056, so 96 * (5.4/96) = sigma * H, i.e.
 * sigma = 5.4 / ROOM.height. Checked in fieldTexture.test.ts so nobody
 * "tidies" the derivation and silently changes the calibrated look.
 */
export const SIGMA_PER_M = TOTAL_OPTICAL_DEPTH / ROOM.height

/** Bytes per texel (RGBA8). */
const CHANNELS = 4

/** Allocates the backing buffer for the field texture.
 *
 *  Typed as Uint8Array<ArrayBuffer> rather than plain Uint8Array: since TS 5.7
 *  the latter widens to ArrayBufferLike, which includes SharedArrayBuffer and
 *  so will not satisfy the BufferSource that texture uploads take. */
export function createFieldBuffer(): Uint8Array<ArrayBuffer> {
  return new Uint8Array(TEX_W * TEX_H * TEX_D * CHANNELS)
}

/**
 * Samples the field into an RGBA8 buffer laid out for a Data3DTexture.
 *
 * Channels:
 *   R  rampPosition(value) — the shader's LUT coordinate. Stored rather than
 *      the raw concentration because rampPosition spends 70% of its range on
 *      0-4 %v/v, so 8 bits resolve ~0.016 %v/v where the experiment lives.
 *      Storing value/SCALE_MAX would crush that region into 51 of 256 codes.
 *   G  confidence, 0..1
 *   B  density strength, value/FULL_ALPHA_AT_PCT_VV clamped — stored rather
 *      than recovered from R, which would mean inverting the piecewise ramp
 *      in GLSL for no reason.
 *   A  air mask: 0 inside furniture, 255 in open air.
 *
 * R and B are zeroed inside solids so the filtered boundary ramps density down
 * to nothing, instead of smearing cabinet interior outward.
 *
 * TEXEL CENTRES. Position i maps to ((i + 0.5) / n) * extent. LinearFilter maps
 * texcoord (i + 0.5)/n back to texel i's centre, so box spans room, [0,1]^3
 * spans box, and sample positions land exactly on texel centres — no half-texel
 * correction anywhere in the shader. Do not "simplify" this to i/n or
 * i/(n-1); either one shifts the whole field by half a texel and puts gas on
 * the wrong side of a cabinet edge.
 *
 * This replaced a step-based grid helper that sized itself with
 * Math.ceil(extent/step), which over-covered the room (nx*step > ROOM.width).
 * Harmless cell-by-cell, but as a texture it stretches the data ~5% when
 * [0,1] is mapped across the box — about 14cm of misplacement at the far wall.
 * Size from the texture dimensions, never from a step.
 *
 * @param data     buffer from createFieldBuffer(), written in place
 * @param samples  sensor readings; an empty list yields an empty field
 * @param anisotropy  vertical/horizontal weighting, see interpolation.ts
 */
export function packField(
  data: Uint8Array,
  samples: readonly SensorSample[],
  anisotropy: number,
): void {
  for (let iz = 0; iz < TEX_D; iz++) {
    const z = ((iz + 0.5) / TEX_D) * ROOM.height
    for (let iy = 0; iy < TEX_H; iy++) {
      const y = ((iy + 0.5) / TEX_H) * ROOM.depth
      for (let ix = 0; ix < TEX_W; ix++) {
        const x = ((ix + 0.5) / TEX_W) * ROOM.width
        // x-fastest, which is the layout Data3DTexture expects.
        const o = (ix + TEX_W * (iy + TEX_H * iz)) * CHANNELS

        // No air inside furniture, so no concentration to draw.
        if (isInsideSolid(x, y, z)) {
          data[o] = 0
          data[o + 1] = 0
          data[o + 2] = 0
          data[o + 3] = 0
          continue
        }

        const { value, confidence } = sampleField(x, y, z, samples, anisotropy)

        // Open air, but nothing worth drawing. The mask stays 255: this texel
        // is still air, and marking it solid would let neighbouring gas fade
        // against a phantom obstacle.
        if (value < VISIBILITY_FLOOR_PCT_VV || confidence <= 0) {
          data[o] = 0
          data[o + 1] = 0
          data[o + 2] = 0
          data[o + 3] = 255
          continue
        }

        const strength = Math.min(1, value / FULL_ALPHA_AT_PCT_VV)

        data[o] = Math.round(rampPosition(value) * 255)
        data[o + 1] = Math.round(confidence * 255)
        data[o + 2] = Math.round(strength * 255)
        data[o + 3] = 255
      }
    }
  }
}

/** Texels in the colour lookup table. */
export const RAMP_LUT_SIZE = 256

/**
 * The colour ramp baked into a 256x1 RGBA lookup table, indexed by the same
 * rampPosition stored in the field texture's R channel.
 *
 * RGBA, not RGB: WebGL2's texStorage2D upload path (what three's DataTexture
 * uses) requires a SIZED internal format, and plain 3-channel RGBFormat isn't
 * one — three cannot map it to a valid glTexStorage2D format on this driver.
 * The symptom is not a thrown error but GL_INVALID_ENUM on texStorage2D
 * followed by GL_INVALID_OPERATION on the subsequent upload, leaving the
 * texture with no storage; sampling it then returns black, which is why the
 * whole volume rendered as solid black rather than failing loudly. Enough of
 * those calls in a row can even take the context down. RGBA8 is the format
 * every WebGL2 implementation is guaranteed to support.
 *
 * Built from colorScale's own STOPS (via concentrationToRgb) so the field, the
 * legend and the sensor markers cannot drift apart — a legend drawn on a
 * different scale from the field would be worse than no legend.
 */
export function buildRampLut(): Uint8Array<ArrayBuffer> {
  const lut = new Uint8Array(RAMP_LUT_SIZE * 4)

  for (let i = 0; i < RAMP_LUT_SIZE; i++) {
    // The LUT is indexed by ramp position, and concentrationToRgb takes a
    // concentration, so walk the ramp in concentration and let it do the
    // interpolation. rampPosition is monotonic, so this fills every slot.
    const { r, g, b } = concentrationToRgb(rampPositionToPctVv(i / (RAMP_LUT_SIZE - 1)))
    const o = i * 4
    lut[o] = r
    lut[o + 1] = g
    lut[o + 2] = b
    lut[o + 3] = 255 // unused by the shader (only .rgb is sampled); kept opaque
  }

  return lut
}
