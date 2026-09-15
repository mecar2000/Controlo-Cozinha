/**
 * ConfirmPopover — a small, fixed-width confirm box anchored next to its
 * trigger, instead of swapping a longer line of text into the trigger's own
 * flow position.
 *
 * problems.txt: "there should be some way to confirm without that dialog
 * box, cause if its gone its gone" + "I dont like the long line of text for
 * the confirm, should be a small popup box." The previous pattern (used by
 * DevicePinTable's "Clear" and DeleteDeviceControl's "Delete device")
 * swapped a sentence into the SAME flow position as the trigger button, so
 * the actual Confirm/Cancel buttons landed at a different x-position than
 * where the trigger was — a fast double-click could miss them and silently
 * drop the action. Rendering the popover at a fixed size, absolutely
 * positioned off the trigger, means Confirm/Cancel are always in the same
 * place relative to the trigger regardless of message length.
 *
 * Deliberately not a portal: every current use site is a normal-flow
 * container (a table cell, a flex row) with no overflow:hidden/auto
 * ancestor clipping it, and staying in-place keeps it a plain DOM
 * descendant of the trigger's row — existing E2E assertions query it via
 * `row.getByText(...)`/`row.getByRole('button', ...)`.
 */
import { useRef } from 'react'

export function ConfirmPopover({
  message,
  confirmLabel,
  busyLabel,
  onConfirm,
  onCancel,
  busy = false,
}: {
  message: string
  confirmLabel: string
  busyLabel?: string
  onConfirm: () => void
  onCancel: () => void
  busy?: boolean
}) {
  const anchorRef = useRef<HTMLSpanElement>(null)

  return (
    <span ref={anchorRef} className="relative inline-block">
      <span
        role="alertdialog"
        aria-modal="true"
        className="absolute right-0 top-full z-10 mt-1 w-64 rounded-sm border border-hairline bg-panel p-3 shadow-lg"
      >
        <p className="prose-text text-ink-dim">{message}</p>
        <span className="mt-2 flex justify-end gap-2">
          <button type="button" className="btn btn-quiet" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-quiet text-live" onClick={onConfirm} disabled={busy}>
            {busy ? (busyLabel ?? 'Working…') : confirmLabel}
          </button>
        </span>
      </span>
    </span>
  )
}
