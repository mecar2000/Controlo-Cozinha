import { describe, expect, it } from 'vitest'
import { QUORUM_PCT_PER_COUNT, quorumThresholdChanged } from './quorumDiff'

describe('quorumThresholdChanged', () => {
  it('treats the 10 -> 9.988 counts round-trip as unchanged', () => {
    // The exact case from problems.txt B1: the website asks for 10 %v/v, the
    // firmware stores it as int((10/100) * 4095) = 409 counts and echoes back
    // 409/4095*100 = 9.988. That 0.012 gap is one ADC count of quantisation,
    // not the firmware disagreeing, so it must not be reported as a change.
    expect(quorumThresholdChanged(10, 9.988)).toBe(false)
  })

  it('treats any difference below one ADC count as unchanged', () => {
    // One count is ~0.0244 %v/v, so nothing finer than that is representable
    // and no sub-count difference can be a real disagreement.
    expect(quorumThresholdChanged(4, 4 - QUORUM_PCT_PER_COUNT / 2)).toBe(false)
    expect(quorumThresholdChanged(4, 4 + QUORUM_PCT_PER_COUNT / 2)).toBe(false)
  })

  it('reports a real clamp as changed', () => {
    // The whole point of the review screen: a firmware clamp that actually
    // moves the trip point must stay visible.
    expect(quorumThresholdChanged(10, 4)).toBe(true)
    expect(quorumThresholdChanged(2, 0)).toBe(true)
  })

  it('reports a difference of more than one count as changed', () => {
    expect(quorumThresholdChanged(10, 10 - QUORUM_PCT_PER_COUNT * 2)).toBe(true)
  })

  it('treats an exact match as unchanged', () => {
    expect(quorumThresholdChanged(10, 10)).toBe(false)
    expect(quorumThresholdChanged(0, 0)).toBe(false)
  })

  it('treats a missing value as 0 on either side', () => {
    expect(quorumThresholdChanged(undefined, 0)).toBe(false)
    expect(quorumThresholdChanged(0, undefined)).toBe(false)
    // A threshold vanishing entirely IS a real change, not a rounding artifact.
    expect(quorumThresholdChanged(10, undefined)).toBe(true)
  })
})
