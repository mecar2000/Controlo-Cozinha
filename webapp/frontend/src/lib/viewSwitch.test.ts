import { describe, expect, it } from 'vitest'

import { shouldSwitchToDisplayOnLeak } from './viewSwitch'

describe('shouldSwitchToDisplayOnLeak', () => {
  it('fires on the edge into LEAKING', () => {
    expect(shouldSwitchToDisplayOnLeak('ARMED', 'LEAKING')).toBe(true)
  })

  it('does not fire while already LEAKING (no repeated switch)', () => {
    expect(shouldSwitchToDisplayOnLeak('LEAKING', 'LEAKING')).toBe(false)
  })

  it('does not fire for any other phase', () => {
    expect(shouldSwitchToDisplayOnLeak('WAITING', 'ARMED')).toBe(false)
    expect(shouldSwitchToDisplayOnLeak('LEAKING', 'HOLD')).toBe(false)
    expect(shouldSwitchToDisplayOnLeak(null, 'WAITING')).toBe(false)
  })

  it('fires from an unknown/undefined previous phase (first poll lands mid-leak)', () => {
    expect(shouldSwitchToDisplayOnLeak(undefined, 'LEAKING')).toBe(true)
    expect(shouldSwitchToDisplayOnLeak(null, 'LEAKING')).toBe(true)
  })
})
