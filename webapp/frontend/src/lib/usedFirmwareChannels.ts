/**
 * usedFirmwareChannels — which of the kitchen PLC's 6 hardwired H2 inputs
 * (0-5) are already claimed by an active sensor. Nothing in the backend
 * stops two sensors sharing a firmware_index (see routes/sensors.py's
 * set_firmware_index), which would make them silently compete for one
 * danger threshold — this is surfaced in the UI so a channel already in use
 * is visible before it's picked again.
 */
import type { Sensor } from '@/api/types'

/** sensor.firmware_index if set, else H2-N -> N-1 as a fallback (matches
 *  routes/thresholds.py's own resolution order). Null when neither
 *  resolves. */
export function firmwareIndexOf(sensor: Sensor): number | null {
  if (sensor.firmware_index != null) return sensor.firmware_index
  const m = /^H2-(\d+)$/.exec(sensor.daq_sensor_name ?? '')
  if (!m) return null
  const idx = Number(m[1]) - 1
  return idx >= 0 && idx <= 5 ? idx : null
}

/** The set of firmware channels already claimed by every other sensor
 *  besides `excludeSensorKey` — pass the sensor being edited so it doesn't
 *  warn against its own existing claim. */
export function usedFirmwareChannels(sensors: Sensor[], excludeSensorKey?: string): Set<number> {
  const used = new Set<number>()
  for (const sensor of sensors) {
    if (excludeSensorKey != null && sensor.sensor_key === excludeSensorKey) continue
    const idx = firmwareIndexOf(sensor)
    if (idx != null) used.add(idx)
  }
  return used
}
