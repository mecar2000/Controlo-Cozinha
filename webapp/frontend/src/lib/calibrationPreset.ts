/**
 * calibrationPreset — the two real sensor types this app actually has
 * (problems.txt: "sensors theere will be simply either voltage 0.5-4.5V
 * meaning 0-4% vol or 4-20ma meaning 0-100%"). Picking one fills in the
 * whole linear calibration in one click; "Custom" drops to the free-form
 * method picker for anything else.
 *
 * Extracted from SensorPanel.tsx so presetFor() is unit-testable — it is the
 * fix point for a real bug (see calibrationPreset.test.ts): a saved
 * calibration read back from DataAcquisition's GET /conversions/{device_id}
 * used to always resolve to 'custom' after a reopen, because that endpoint
 * only put `method` inside `params`, never at the top level presetFor()
 * checked. DataAcquisition's own read path has since been fixed to surface
 * both, but presetFor() also checks params.method as a fallback so this app
 * stays correct against an older/unpatched DataAcquisition too.
 */
import type { DaqConversion } from '@/api/types'

export type CalibrationPreset = 'voltage-0-4' | 'current-0-100' | 'custom'

export const PRESETS: Array<{
  id: CalibrationPreset
  label: string
  params?: { raw_min: number; raw_max: number; min_value: number; max_value: number }
  unit?: string
}> = [
  {
    id: 'voltage-0-4',
    label: '0.5–4.5 V → 0–4 %vol',
    params: { raw_min: 0.5, raw_max: 4.5, min_value: 0, max_value: 4 },
    unit: '%v/v',
  },
  {
    id: 'current-0-100',
    label: '4–20 mA → 0–100 %',
    params: { raw_min: 4, raw_max: 20, min_value: 0, max_value: 100 },
    unit: '%v/v',
  },
  { id: 'custom', label: 'Custom…' },
]

export function presetFor(conv: DaqConversion | undefined): CalibrationPreset {
  if (!conv) return 'custom'
  // Prefer the top-level field (what a fresh POST's own response — and a
  // fixed DataAcquisition's GET — both use), but fall back to params.method
  // for an older/unpatched DataAcquisition whose GET only nests it there.
  const method = conv.method ?? (conv.params?.method as string | undefined)
  if (method !== 'linear') return 'custom'
  const p = conv.params ?? {}
  const num = (v: unknown): number => (typeof v === 'number' ? v : Number(v ?? NaN))
  const matches = (preset: (typeof PRESETS)[number]) =>
    !!preset.params &&
    Math.abs(num(p.raw_min) - preset.params.raw_min) < 1e-6 &&
    Math.abs(num(p.raw_max) - preset.params.raw_max) < 1e-6 &&
    Math.abs(num(p.min_value) - preset.params.min_value) < 1e-6 &&
    Math.abs(num(p.max_value) - preset.params.max_value) < 1e-6
  return PRESETS.find((preset) => preset.id !== 'custom' && matches(preset))?.id ?? 'custom'
}
