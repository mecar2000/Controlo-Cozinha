/**
 * pinLabel.test.ts — mirrors DataAcquisition's own pin encoding
 * (dashboard/app/conversion.py::_pin_label) so this app's UI never shows a
 * pin label that disagrees with DataAcquisition's own dashboard. The
 * backend's app.daq.pin_label implements the same arithmetic; this is the
 * frontend copy, unit-tested independently since nothing here can import
 * Python.
 */
import { describe, expect, it } from 'vitest'

import { pinLabel } from './pinLabel'

describe('pinLabel', () => {
  it('encodes base board pins as A0..A7', () => {
    expect(pinLabel(0)).toBe('A0')
    expect(pinLabel(7)).toBe('A7')
  })

  it('encodes the first expansion pin at the 100 boundary', () => {
    expect(pinLabel(100)).toBe('E0:CH0')
  })

  it('encodes later expansion pins', () => {
    expect(pinLabel(101)).toBe('E0:CH1')
    expect(pinLabel(206)).toBe('E1:CH6')
  })
})
