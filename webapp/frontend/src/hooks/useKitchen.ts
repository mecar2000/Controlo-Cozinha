/**
 * useKitchen — the live view of the kitchen: status, sensor layout, and the
 * readings joined onto it.
 *
 * The staleness rule from the design spec's Availability table is enforced
 * here rather than in each component: when the broker link is stale, the
 * live view is flagged and MUST NOT present last-known values as current.
 * Components read `stale` and dim accordingly; they never have to work out
 * for themselves whether a number is trustworthy.
 */

import { useMemo } from 'react'

import * as api from '@/api/client'
import type { LiveReadings, Sensor, Status } from '@/api/types'
import type { SensorSample } from '@/lib/interpolation'
import { usePolling } from './usePolling'

/** Status drives the header dots and the phase name — poll it briskly. */
const STATUS_INTERVAL_MS = 1000
/** Readings drive the room. Same cadence: the field should track the phase. */
const READINGS_INTERVAL_MS = 1000
/** The layout changes only when someone edits it. */
const LAYOUT_INTERVAL_MS = 30_000

/** A sensor with its current reading, ready for the room and the rail. */
export interface LiveSensor extends SensorSample {
  key: string
  label: string
  /** False when this sensor has no reading at all — absent, not zero.
   *  Silence is not evidence of low concentration. */
  hasReading: boolean
  /** Seconds since this particular sensor last reported. */
  ageS: number | null
  unit: string
}

export interface KitchenView {
  status: Status | null
  sensors: LiveSensor[]
  /** Sensors carrying a usable reading — what the field interpolates over. */
  samples: SensorSample[]
  /** True when the broker link is stale or unreachable. Live values must be
   *  visibly marked, never presented as current. */
  stale: boolean
  /** True when the backend itself cannot be reached. */
  offline: boolean
  loading: boolean
  refresh: () => void
}

/** Past this, one sensor's own reading is too old to plot as current. */
const SENSOR_STALE_AFTER_S = 15

export function useKitchen(): KitchenView {
  const status = usePolling<Status>(() => api.getStatus(), STATUS_INTERVAL_MS)
  const readings = usePolling<LiveReadings>(() => api.getLiveReadings(), READINGS_INTERVAL_MS)
  const layout = usePolling<Sensor[]>(() => api.listSensors(true), LAYOUT_INTERVAL_MS)

  const sensors = useMemo<LiveSensor[]>(() => {
    const positions = layout.data ?? []
    const values: LiveReadings = readings.data ?? {}
    const nowS = Date.now() / 1000

    return positions.map((s) => {
      // The backend keys readings by the MQTT sensor name; fall back to the
      // sensor_key when no explicit DAQ name has been configured.
      const lookupKey = s.daq_sensor_name ?? s.sensor_key
      const reading = values[lookupKey]
      const ageS = reading ? nowS - reading.received_at : null
      // An UNCONVERTED reading is raw mA, not a concentration: DataAcquisition
      // had no calibration for this sensor. Treating it as fresh would paint
      // the heatmap with a number that is not %v/v at all, so it counts as no
      // reading — the same rule absent/stale sensors follow (silence is not
      // evidence of low concentration, and neither is an unconverted signal).
      const fresh = reading != null && ageS != null && ageS <= SENSOR_STALE_AFTER_S
        && reading.converted !== false

      return {
        key: s.sensor_key,
        label: s.label,
        x: s.x,
        y: s.y,
        z: s.z,
        value: fresh ? reading!.value : 0,
        hasReading: fresh,
        ageS,
        unit: reading?.unit ?? '%v/v',
      }
    })
  }, [layout.data, readings.data])

  // Absent and stale sensors are excluded from the field, matching the
  // firmware's own quorum rule: silence is not evidence of low
  // concentration, and interpolating over a zero it never measured would
  // paint a false cold spot.
  const samples = useMemo<SensorSample[]>(
    () =>
      sensors
        .filter((s) => s.hasReading)
        .map(({ x, y, z, value }) => ({ x, y, z, value })),
    [sensors],
  )

  const offline = status.error != null && status.data == null
  const stale = Boolean(status.data?.mqtt.stale) || status.error != null

  return {
    status: status.data,
    sensors,
    samples,
    stale,
    offline,
    loading: status.loading || layout.loading,
    refresh: () => {
      status.refresh()
      readings.refresh()
    },
  }
}
