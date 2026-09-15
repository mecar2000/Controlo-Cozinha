/**
 * usedFirmwareChannels.test.ts — which of the kitchen PLC's 6 hardwired H2
 * inputs (0-5) are already claimed by an active sensor, so the "firmware
 * channel" picker can warn before creating two sensors that would silently
 * compete for one threshold. Mirrors SensorPanel.tsx's existing
 * firmwareIndexOf: explicit sensor.firmware_index wins, else an H2-N
 * daq_sensor_name implies channel N-1 (routes/thresholds.py's fallback).
 */
import { describe, expect, it } from 'vitest'

import type { Sensor } from '@/api/types'

import { usedFirmwareChannels } from './usedFirmwareChannels'

function sensor(overrides: Partial<Sensor> = {}): Sensor {
  return {
    id: 1,
    sensor_key: 'sensor-1',
    label: 'Sensor 1',
    x: 0,
    y: 0,
    z: 0,
    enabled: true,
    daq_device_id: null,
    daq_sensor_name: null,
    daq_pin: null,
    firmware_index: null,
    updated_at: '',
    ...overrides,
  }
}

describe('usedFirmwareChannels', () => {
  it('is empty when no sensor claims a channel', () => {
    expect(usedFirmwareChannels([sensor()])).toEqual(new Set())
  })

  it('counts an explicit firmware_index as claimed', () => {
    const result = usedFirmwareChannels([sensor({ sensor_key: 'a', firmware_index: 2 })])
    expect(result).toEqual(new Set([2]))
  })

  it('counts the H2-N naming fallback as claimed when firmware_index is unset', () => {
    const result = usedFirmwareChannels([sensor({ sensor_key: 'a', daq_sensor_name: 'H2-4' })])
    expect(result).toEqual(new Set([3]))
  })

  it('combines explicit and fallback claims across multiple sensors', () => {
    const result = usedFirmwareChannels([
      sensor({ sensor_key: 'a', firmware_index: 2 }),
      sensor({ sensor_key: 'b', daq_sensor_name: 'H2-1' }),
      sensor({ sensor_key: 'c' }),
    ])
    expect(result).toEqual(new Set([2, 0]))
  })

  it('excludes a given sensor_key from the claimed set (editing that sensor should not warn against itself)', () => {
    const result = usedFirmwareChannels(
      [sensor({ sensor_key: 'a', firmware_index: 2 })],
      'a',
    )
    expect(result).toEqual(new Set())
  })
})
