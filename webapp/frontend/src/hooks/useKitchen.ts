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

/**
 * Status drives the header dots and the phase name. 1s, comfortably inside
 * the server-side 15s staleness cutoff (routes/status.py) with ~15 polls'
 * margin, so dropped polls don't push the view into "stale".
 *
 * problems.txt asked for 2-5s ("why is the state updated so frequently?").
 * That is safe for staleness but NOT for the run clock: elapsedMs is frozen
 * between the publisher's heartbeats, so the longer this interval, the more
 * visible any error in the interpolation anchor becomes. The anchor is now
 * the payload's publish time (kitchen_state_age_s) rather than the fetch
 * time, so the clock is smooth at any cadence — but a fast poll also keeps
 * the phase name honest, and there is no bandwidth reason here to slow it.
 * Exported so the staleness margin is checked in pollingIntervals.test.ts
 * rather than asserted only in a comment.
 */
export const STATUS_INTERVAL_MS = 1000
/** Readings drive the room. Same cadence: the field should track the phase. */
export const READINGS_INTERVAL_MS = 500
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
  /** The raw {device_id}/{sensor_name} -> reading map, before it is joined
   *  onto sensor_config positions. The SensorPanel needs this keyed form
   *  directly — it shows a sensor's live reading even when calibration is
   *  being edited, not just what the room can plot. */
  liveReadings: LiveReadings
  /** Wall-clock ms at which the CURRENT kitchen_state payload was published,
   *  in this browser's clock — the anchor a ms-elapsed-shaped value (the
   *  clear-air countdown, the run clock) interpolates forward from.
   *
   *  NOT the fetch time. elapsedMs/clearForMs are frozen between the
   *  publisher's heartbeats, so a fetch-time anchor re-bases a stale value
   *  once per poll: the clock creeps up, then snaps back when a heartbeat
   *  finally delivers the true value. See lib/interpolatedClock.ts. */
  statusReceivedAt: number | null
  /** Sensors carrying a usable reading — what the field interpolates over. */
  samples: SensorSample[]
  /** True when the broker link is stale or unreachable. Live values must be
   *  visibly marked, never presented as current. */
  stale: boolean
  /** True when the backend itself cannot be reached. */
  offline: boolean
  loading: boolean
  refresh: () => void
  /** Force an immediate re-fetch of the sensor layout specifically — layout
   *  otherwise only polls every LAYOUT_INTERVAL_MS (30s), so a sensor
   *  saved/cleared/moved in the Sensors & devices panel would only reach the
   *  room behind it up to 30s later. Call this right after any mutation
   *  that changes sensor_config, so the room catches up on the same action
   *  instead of the next poll tick. */
  refreshLayout: () => void
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
      // The backend keys readings by "{device_id}/{sensor_name}" (mqtt.py) —
      // sensor names are only unique WITHIN a device, so two acquisition
      // PLCs can both publish "H2-1". Same composite shape AnalysisView.tsx
      // uses for DAQ history lookups. Falls back to the bare sensor_key when
      // no DAQ identity has been configured for this sensor at all.
      const lookupKey = s.daq_device_id
        ? `${s.daq_device_id}/${s.daq_sensor_name ?? s.sensor_key}`
        : s.daq_sensor_name ?? s.sensor_key
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

  // Back-date the fetch time by however old the payload already was when the
  // server built the response. Both terms are measured on THIS machine's
  // clock (lastSuccessAt) and the server's own elapsed interval (age), so the
  // two clocks never get compared to each other. Falls back to the fetch time
  // when the server sends no age — an unpatched backend then behaves exactly
  // as it did before rather than breaking the clock entirely.
  const statusAgeS = status.data?.kitchen_state_age_s
  const statusReceivedAt = useMemo(
    () =>
      status.lastSuccessAt == null
        ? null
        : status.lastSuccessAt - (statusAgeS != null ? statusAgeS * 1000 : 0),
    [status.lastSuccessAt, statusAgeS],
  )

  const offline = status.error != null && status.data == null
  const stale = Boolean(status.data?.mqtt.stale) || status.error != null

  return {
    status: status.data,
    sensors,
    liveReadings: readings.data ?? {},
    statusReceivedAt: statusReceivedAt,
    samples,
    stale,
    offline,
    loading: status.loading || layout.loading,
    refresh: () => {
      status.refresh()
      readings.refresh()
    },
    refreshLayout: () => layout.refresh(),
  }
}
