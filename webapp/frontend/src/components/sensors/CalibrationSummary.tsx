import type { DaqConversion } from '@/api/types'
import { presetFor } from '@/lib/calibrationPreset'

/** Read-only calibration display, replacing the old CalibrationForm editor.
 *
 * Calibration is DataAcquisition's data, not Cozinha's — sensor_config has
 * no calibration columns at all (only identity/position/pin — see
 * db/schema.py). CalibrationForm let this app write DataAcquisition's
 * conversion table directly, which worked, but blurred a boundary the user
 * asked to make unmistakable: editing calibration should happen in
 * DataAcquisition's own dashboard, and this app should show what it
 * currently is (read from the same live conversions map SensorPanel
 * already fetches) with a link out rather than a second editor.
 *
 * Zeroing ("Zero in clean air", ZeroingControl) is NOT part of this move —
 * it's a distinct capability unique to this app (averaging a live raw
 * sample across the room while it's running) that happens to write one
 * calibration field (raw_min) as its result, not a general editor. */
export function CalibrationSummary({
  conversion,
  daqDashboardUrl,
}: {
  conversion: DaqConversion | undefined
  daqDashboardUrl: string | null
}) {
  const preset = presetFor(conversion)
  const presetLabel =
    preset === 'voltage-0-4' ? '0.5–4.5 V → 0–4 %vol' : preset === 'current-0-100' ? '4–20 mA → 0–100 %' : 'Custom'

  return (
    <fieldset className="flex flex-col gap-3 border-t border-hairline pt-4">
      <legend className="prose-text mb-1 text-ink-dim">
        Calibration — owned by DataAcquisition; edit it there, not here
      </legend>

      {conversion ? (
        <div className="rail-row">
          <span className="rail-label">{presetLabel}</span>
          <span className="rail-value">{conversion.unit_symbol}</span>
        </div>
      ) : (
        <p className="prose-text text-ink-faint">No calibration set yet.</p>
      )}

      {daqDashboardUrl ? (
        <a
          href={daqDashboardUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn-quiet self-start"
        >
          Edit calibration in DataAcquisition →
        </a>
      ) : (
        <p className="prose-text text-ink-faint">
          Could not determine DataAcquisition's dashboard address.
        </p>
      )}
    </fieldset>
  )
}
