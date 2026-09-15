import { useState } from 'react'

import * as api from '@/api/client'
import { ConfirmPopover } from '@/components/common/ConfirmPopover'

/** Permanently removes a device from DataAcquisition — its config, sensor
 *  list, and stored conversions (app.daq.delete_device). Two-click confirm:
 *  a destructive, hard-to-reverse action should never be one click.
 *
 *  Warns up front when sensors are currently bound to this device — nothing
 *  on the backend refuses or cascades on the operator's behalf (matching
 *  push_config's own no-cascade behaviour when a single pin is dropped), so
 *  this is the only place that surfaces the consequence before it happens. */
export function DeleteDeviceControl({
  deviceId,
  boundSensorCount,
  onDeleted,
}: {
  deviceId: string
  boundSensorCount: number
  onDeleted: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleDelete() {
    setBusy(true)
    setError(null)
    try {
      await api.deleteDaqDevice(deviceId)
      onDeleted()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete the device')
      setBusy(false)
      setConfirming(false)
    }
  }

  const boundWarning =
    boundSensorCount > 0
      ? ` ${boundSensorCount} sensor${boundSensorCount === 1 ? '' : 's'} currently bound to this device will be left pointing at a pin DataAcquisition no longer reports.`
      : ''

  return (
    <div className="flex flex-col items-end gap-1">
      {confirming ? (
        <ConfirmPopover
          message={`Delete ${deviceId} for good?${boundWarning}`}
          confirmLabel="Delete"
          busyLabel="Deleting…"
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={() => void handleDelete()}
        />
      ) : (
        <button type="button" className="btn btn-quiet text-live" onClick={() => setConfirming(true)}>
          Delete device
        </button>
      )}
      {error && (
        <p className="prose-text text-live" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
