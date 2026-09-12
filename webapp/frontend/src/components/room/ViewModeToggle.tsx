/**
 * ViewModeToggle — sensors-only against interpolated field, over the canvas.
 *
 * Both views that draw a room carry this, so it lives in one place: the
 * Control view at full size, the Analysis view's narrow column compact. The
 * toggle itself is the safeguard described in RoomScene — sensors-only has to
 * stay ONE click away, so a field that looks implausible can be checked
 * against raw values immediately. Making it a shared component is what keeps
 * that promise identical in both places.
 */

import type { ViewMode } from './RoomScene'

const MODES: readonly ViewMode[] = ['sensors', 'field']

/** What each mode is called on screen. The mode ids stay 'sensors'/'field'
 *  (they are in the URL-free component API and in RoomScene), but the labels
 *  say what the operator will SEE: discrete markers, or the interpolated
 *  cloud between them. "Sensors" alone collided with the Sensors button that
 *  opens the editor right next to this toggle. */
const MODE_LABELS: Record<ViewMode, string> = {
  sensors: 'Points',
  field: 'Cloud',
}

export function ViewModeToggle({
  mode,
  onModeChange,
  compact = false,
}: {
  mode: ViewMode
  onModeChange: (m: ViewMode) => void
  compact?: boolean
}) {
  return (
    <div
      className="flex gap-1 rounded-sm border border-hairline bg-panel/90 p-1"
      role="group"
      aria-label="Room view mode"
    >
      {MODES.map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => onModeChange(m)}
          aria-pressed={mode === m}
          aria-label={m}
          className={`cursor-pointer rounded-[2px] border font-sans transition-colors ${
            compact ? 'px-2 py-0.5 text-label' : 'px-2.5 py-1'
          } ${mode === m ? 'seg-on' : 'border-transparent text-ink-dim hover:text-ink'}`}
        >
          {MODE_LABELS[m]}
        </button>
      ))}
    </div>
  )
}
