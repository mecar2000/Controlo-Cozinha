import type { ConfigAck, DaqConversion, LiveReadings, Sensor } from '@/api/types'

import { CalibrationSummary } from './CalibrationSummary'
import { FirmwareIndexForm } from './FirmwareIndexForm'
import { ThresholdForm } from './ThresholdForm'
import { ZeroingControl } from './ZeroingControl'

export interface SensorRow {
  sensor: Sensor
  /** sensor.firmware_index if set, else H2-N -> N-1 as a fallback (see
   *  routes/thresholds.py). Null when neither resolves — no threshold can
   *  be sent for it, only calibration. */
  firmwareIndex: number | null
}

export function SensorDetail({
  row,
  conversion,
  liveReading,
  configAck,
  usedFirmwareChannels,
  daqDashboardUrl,
  onConversionSaved,
  onRefresh,
}: {
  row: SensorRow
  conversion: DaqConversion | undefined
  liveReading: LiveReadings[string] | undefined
  configAck: ConfigAck
  /** Firmware channels claimed by OTHER active sensors — see
   *  lib/usedFirmwareChannels. Passed down so FirmwareIndexForm can warn
   *  before creating a collision. */
  usedFirmwareChannels: Set<number>
  /** DataAcquisition's own dashboard base URL, for CalibrationSummary's
   *  "edit in DataAcquisition" link — null while /api/meta hasn't resolved. */
  daqDashboardUrl: string | null
  onConversionSaved: (conv: DaqConversion) => void
  onRefresh: () => void
}) {
  const { sensor, firmwareIndex } = row
  const ackEntry = configAck.thresholds?.find((t) => t.sensor === firmwareIndex)

  return (
    <div className="flex flex-col gap-6">
      <div>
        <p className="text-lede font-medium text-ink">{sensor.label}</p>
        <p className="prose-text text-ink-faint">
          {sensor.daq_device_id && sensor.daq_sensor_name
            ? `${sensor.daq_device_id} / ${sensor.daq_sensor_name}`
            : 'No DAQ identity configured — this sensor cannot receive live readings.'}
        </p>
        <p className="prose-text text-ink-faint">
          position {sensor.x}, {sensor.y}, {sensor.z} m
        </p>
      </div>

      {liveReading && (
        <div className="rail-row border-b border-hairline">
          <span className="rail-label">live reading</span>
          <span className="rail-value">
            {liveReading.value.toFixed(3)} {liveReading.unit}
            {liveReading.converted === false && (
              <span className="prose-text ml-2 text-armed">unconverted</span>
            )}
          </span>
        </div>
      )}

      {sensor.daq_device_id && sensor.daq_sensor_name ? (
        <>
          <CalibrationSummary conversion={conversion} daqDashboardUrl={daqDashboardUrl} />
          <ZeroingControl
            sensorKey={sensor.sensor_key}
            onApplied={(conv) => onConversionSaved(conv)}
          />
        </>
      ) : (
        <p className="prose-text border-l-2 border-armed pl-2 text-armed">
          Set a DAQ device and sensor name for this sensor before calibrating it — use
          Edit, above.
        </p>
      )}

      {firmwareIndex != null ? (
        <ThresholdForm sensorKey={sensor.sensor_key} ackEntry={ackEntry} />
      ) : (
        <FirmwareIndexForm
          sensorKey={sensor.sensor_key}
          usedChannels={usedFirmwareChannels}
          onSet={onRefresh}
        />
      )}
    </div>
  )
}
