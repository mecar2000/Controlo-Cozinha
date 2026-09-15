/**
 * quorumDiff — is a difference between the requested and acked quorum
 * threshold a real firmware decision, or just ADC quantisation?
 *
 * The website sends the quorum threshold as a percent, but the comparison the
 * firmware actually performs is in integer ADC counts, so the percent makes a
 * lossy round trip: 10 %v/v -> 409 counts -> 9.988 %v/v (problems.txt B1).
 * Shown raw in the review table, that 0.012 reads as "the firmware changed
 * your value" when nothing was changed at all.
 *
 * One count is the finest distinction the hardware can represent, so any gap
 * smaller than that cannot be a disagreement — it is the same trip point
 * described in a unit that cannot express it exactly. A gap of a count or
 * more still shows, because that is a real clamp and the whole reason the
 * two-phase start exists.
 */

/** 12-bit ADC, full scale spanning 0-100 %v/v (sim/runtime.py, Protocol.cpp). */
const ADC_FULL_SCALE_COUNTS = 4095

/** How much %v/v one ADC count is worth: ~0.0244. The resolution floor. */
export const QUORUM_PCT_PER_COUNT = 100 / ADC_FULL_SCALE_COUNTS

/**
 * True when requested and acked thresholds differ by enough to be a real
 * change rather than a rounding artifact.
 *
 * Missing values count as 0 so a threshold that vanishes entirely still
 * reports as changed — that IS a real difference, not quantisation.
 */
export function quorumThresholdChanged(
  requestedPct: number | undefined,
  ackedPct: number | undefined,
): boolean {
  const requested = requestedPct ?? 0
  const acked = ackedPct ?? 0
  return Math.abs(requested - acked) >= QUORUM_PCT_PER_COUNT
}
