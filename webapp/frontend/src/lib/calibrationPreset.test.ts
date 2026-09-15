/**
 * calibrationPreset.test.ts — regression coverage for the "saved calibration
 * reverts to Custom after reopening" bug.
 *
 * Root cause: DataAcquisition/dashboard/app/routes/data.py::set_conversion
 * stores `method` inside the `params` JSON, and (before the DataAcquisition
 * fix landed) its GET /conversions/{device_id} read path never surfaced it
 * back out to the top level — only `params.method` had it. presetFor() used
 * to check only the top-level `conv.method`, so every conversion read back
 * from a fresh page load matched no preset and fell back to 'custom',
 * regardless of what preset was actually saved.
 *
 * DataAcquisition has since been fixed to surface `method`/`type` at the top
 * level too (see its db/conversions.py), but this test also covers an
 * OLDER/unpatched DataAcquisition instance responding with the pre-fix shape
 * — presetFor() must resolve correctly either way.
 */
import { describe, expect, it } from 'vitest'

import { presetFor } from './calibrationPreset'

const VOLTAGE_PARAMS = { raw_min: 0.5, raw_max: 4.5, min_value: 0, max_value: 4 }
const CURRENT_PARAMS = { raw_min: 4, raw_max: 20, min_value: 0, max_value: 100 }

describe('presetFor', () => {
  it('matches the voltage preset when method is at the top level (the POST response shape)', () => {
    const conv = { method: 'linear', params: VOLTAGE_PARAMS, unit_symbol: '%v/v', conv_id: null }
    expect(presetFor(conv)).toBe('voltage-0-4')
  })

  it('matches the voltage preset when method is ONLY nested in params (the pre-fix DataAcquisition GET shape)', () => {
    // No top-level `method` — exactly what an unpatched GET /conversions/{device_id}
    // used to return, and what an older DataAcquisition instance still might.
    const conv = {
      params: { ...VOLTAGE_PARAMS, method: 'linear' },
      unit_symbol: '%v/v',
      conv_id: null,
    } as never
    expect(presetFor(conv)).toBe('voltage-0-4')
  })

  it('matches the current preset the same way, nested-only', () => {
    const conv = { params: { ...CURRENT_PARAMS, method: 'linear' }, conv_id: null } as never
    expect(presetFor(conv)).toBe('current-0-100')
  })

  it('falls back to custom when params match no known preset, nested-only shape', () => {
    const conv = {
      params: { raw_min: 1, raw_max: 2, min_value: 0, max_value: 1, method: 'linear' },
      conv_id: null,
    } as never
    expect(presetFor(conv)).toBe('custom')
  })

  it('falls back to custom for a non-linear method regardless of shape', () => {
    expect(presetFor({ method: 'ax_b', params: { a: 1, b: 0 }, conv_id: null })).toBe('custom')
    expect(presetFor({ params: { a: 1, b: 0, method: 'ax_b' }, conv_id: null } as never)).toBe('custom')
  })

  it('falls back to custom when there is no conversion at all', () => {
    expect(presetFor(undefined)).toBe('custom')
  })
})
