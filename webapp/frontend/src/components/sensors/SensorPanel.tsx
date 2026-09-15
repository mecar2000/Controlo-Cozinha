/**
 * SensorPanel — position sensors, calibrate them, set their danger
 * threshold, and hard-delete (clear) them. Device-first: a device is the
 * thing that exists, and a sensor only ever exists in Cozinha because
 * DataAcquisition is already publishing it on one of that device's pins —
 * see DevicePane for the actual navigation/layout (its inline, per-pin
 * label/x/y/z editor plus the live 3D position preview). This file is just
 * the modal shell: fetch sensors/devices/conversions/meta and hand them to
 * DevicePane.
 *
 *   DevicePane          — the device-first navigation + inline pin editor +
 *                          3D preview + inline sensor detail
 *   SensorDetail          — one sensor's calibration/threshold/zeroing
 *   CalibrationSummary, ZeroingControl, ThresholdForm,
 *   FirmwareIndexForm, BatchZeroingPanel, DeleteDeviceControl — own files
 *
 * CalibrationSummary is READ-ONLY plus a link out to DataAcquisition's own
 * dashboard (its base URL comes from /api/meta's daq_dashboard_url) — this
 * app no longer offers a calibration editor of its own (see its module
 * comment for why: calibration is DataAcquisition's data, sensor_config has
 * no calibration columns at all).
 *
 * "Zero all in clean air" (BatchZeroingPanel) answers problems.txt's scale
 * concern (30 sensors): one click zeros every sensor with a DAQ identity,
 * sequentially, via app.zeroing's batch endpoints.
 */
import { useEffect, useState } from 'react'

import * as api from '@/api/client'
import type { ConfigAck, DaqConversion, DaqDevice, LiveReadings, Sensor } from '@/api/types'
import type { LiveSensor } from '@/hooks/useKitchen'

import { BatchZeroingPanel } from './BatchZeroingPanel'
import { DevicePane } from './DevicePane'

export function SensorPanel({
  onClose,
  liveReadings,
  liveSensors,
  configAck,
  onSensorsChanged,
}: {
  onClose: () => void
  liveReadings: LiveReadings
  /** Every active sensor with its live reading, from useKitchen() — passed
   *  straight through to DevicePane's 3D position preview. */
  liveSensors: LiveSensor[]
  configAck: ConfigAck
  /** useKitchen()'s refreshLayout — called alongside this panel's own
   *  reloadSensors() so the room behind the modal picks up a save/clear
   *  immediately instead of waiting for the next 30s layout poll. */
  onSensorsChanged: () => void
}) {
  const [sensors, setSensors] = useState<Sensor[] | null>(null)
  const [devices, setDevices] = useState<DaqDevice[] | null>(null)
  const [daqDashboardUrl, setDaqDashboardUrl] = useState<string | null>(null)
  const [conversions, setConversions] = useState<Record<string, DaqConversion>>({})
  const [zeroingAllOpen, setZeroingAllOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function reloadSensors() {
    void api
      .listSensors()
      .then(setSensors)
      .catch((err: Error) => setError(err.message))
    onSensorsChanged()
  }

  function reloadDevices() {
    void api
      .listDaqDevices()
      .then(setDevices)
      .catch((err: Error) => setError(err.message))
  }

  useEffect(reloadSensors, [])
  useEffect(reloadDevices, [])
  useEffect(() => {
    void api
      .getMeta()
      .then((meta) => {
        setDaqDashboardUrl(meta.daq_dashboard_url)
      })
      .catch(() => {
        /* daqDashboardUrl stays null; CalibrationSummary shows a plain
           message instead of a broken link. */
      })
  }, [])

  // Calibration lives in DataAcquisition, per device. Sensors here can span
  // more than one device once a remote acquisition PLC is configured, so
  // fetch each device's table once rather than one HTTP call per sensor.
  useEffect(() => {
    if (!sensors) return
    const deviceIds = [...new Set(sensors.map((s) => s.daq_device_id).filter((d): d is string => Boolean(d)))]
    void Promise.all(deviceIds.map((id) => api.getConversions(id).then((c) => [id, c] as const)))
      .then((pairs) => {
        const merged: Record<string, DaqConversion> = {}
        for (const [deviceId, table] of pairs) {
          for (const [sensorName, conv] of Object.entries(table)) {
            merged[`${deviceId}/${sensorName}`] = conv
          }
        }
        setConversions(merged)
      })
      .catch(() => setConversions({}))
  }, [sensors])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Sensors & devices"
    >
      <div className="flex h-[83vh] w-[86vw] max-w-[1440px] flex-col overflow-hidden rounded-sm border border-hairline bg-panel">
        <div className="flex items-baseline justify-between border-b border-hairline px-8 py-5">
          <h2 className="text-lede font-medium text-ink">Sensors & devices</h2>
          <div className="flex items-center gap-3">
            <button type="button" className="btn btn-quiet" onClick={() => setZeroingAllOpen(true)}>
              Zero all in clean air
            </button>
            <button type="button" className="btn btn-quiet" onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        {error && (
          <p className="prose-text border-b border-hairline px-8 py-3 text-live" role="alert">
            {error}
          </p>
        )}

        {zeroingAllOpen ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-8 py-6">
            <BatchZeroingPanel
              onClose={() => {
                setZeroingAllOpen(false)
                reloadSensors()
              }}
            />
          </div>
        ) : !sensors || !devices ? (
          <p className="prose-text px-5 py-4 text-ink-dim">Loading…</p>
        ) : (
          <DevicePane
            sensors={sensors}
            devices={devices}
            conversions={conversions}
            liveReadings={liveReadings}
            liveSensors={liveSensors}
            configAck={configAck}
            daqDashboardUrl={daqDashboardUrl}
            reloadSensors={reloadSensors}
            reloadDevices={reloadDevices}
          />
        )}
      </div>
    </div>
  )
}
