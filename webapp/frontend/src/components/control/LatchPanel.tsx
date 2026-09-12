/**
 * LatchPanel — what replaces the numeric rail when the kitchen has latched.
 *
 * Three things, in the order they matter: what tripped, how long until the
 * air has been clear long enough, and the Acknowledge button.
 *
 * Acknowledging is NOT a shortcut past the hold. Release needs the ack AND a
 * continuous clear-air period, whichever completes last. So the button stays
 * disabled until the clear-air hold is satisfied, with the remaining time
 * shown — otherwise a disabled button with no explanation looks broken, and
 * someone starts hunting for a way around it.
 */

import { useEffect, useState } from 'react'

import type { KitchenState } from '@/api/types'
import { formatElapsed, latchCauseLabel } from '@/lib/format'
import { interpolatedElapsed } from '@/lib/interpolatedClock'

/** Fallback ONLY for a payload that predates clearRequiredMs (older
 *  firmware/sim) — the firmware's real FULLY_VENT_MIN_HOLD_MS is 5 minutes,
 *  but a shortened test timing (sim's SIM_FULLY_VENT_HOLD_S) must show ITS
 *  own total, not this constant — see kitchenState.clearRequiredMs. */
const CLEAR_AIR_REQUIRED_MS_FALLBACK = 5 * 60 * 1000

export function LatchPanel({
  kitchenState,
  statusReceivedAt,
  onAcknowledge,
}: {
  kitchenState: KitchenState | undefined
  /** When kitchenState was last fetched — lets the countdown tick every
   *  second locally between polls rather than stepping once per poll. */
  statusReceivedAt: number | null
  onAcknowledge: () => Promise<void>
}) {
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Re-render once a second purely to advance the interpolated clock below —
  // this component's own inputs (kitchenState, statusReceivedAt) don't
  // change that often.
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const cause = kitchenState?.dangerReason ?? kitchenState?.latchCause
  const detail = kitchenState?.reasonDetail ?? kitchenState?.description
  const acked = kitchenState?.acked ?? false

  const requiredMs = kitchenState?.clearRequiredMs ?? CLEAR_AIR_REQUIRED_MS_FALLBACK
  const baseClearForMs = kitchenState?.clearForMs ?? 0
  const clearForMs =
    statusReceivedAt != null
      ? (interpolatedElapsed(baseClearForMs, statusReceivedAt, nowMs, { ceiling: requiredMs }) ?? 0)
      : baseClearForMs
  const remainingMs = Math.max(0, requiredMs - clearForMs)
  const clearAirSatisfied = remainingMs === 0
  const progress = Math.min(1, clearForMs / requiredMs)

  async function handleAck() {
    setSending(true)
    setError(null)
    try {
      await onAcknowledge()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Acknowledge failed to send')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="text-lede font-bold tracking-tight text-armed">LATCHED</div>
        <p className="prose-text mt-1 text-ink">{latchCauseLabel(cause)}</p>
        {detail && <p className="prose-text mt-1 text-ink-dim">{detail}</p>}
      </div>

      <div className="border-t border-hairline pt-3">
        <div className="rail-row">
          <span className="rail-label">clear air</span>
          <span className="rail-value">
            {clearAirSatisfied ? (
              <span className="text-safe">satisfied</span>
            ) : (
              <>
                {formatElapsed(remainingMs)} <span className="text-ink-dim">left</span>
              </>
            )}
          </span>
        </div>

        {/* The countdown needs a shape, not just a number: five minutes is
            long enough that a bare timer reads as stuck. */}
        <div
          className="mt-1 h-1 w-full overflow-hidden rounded-full bg-raised"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress * 100)}
          aria-label="Continuous clear-air hold"
        >
          <div
            className={`h-full transition-[width] duration-1000 ease-linear ${
              clearAirSatisfied ? 'bg-safe' : 'bg-armed'
            }`}
            style={{ width: `${progress * 100}%` }}
          />
        </div>

        <p className="prose-text mt-2 text-ink-faint">
          The hold restarts if a sensor trips again. Five continuous clear
          minutes are required, not five minutes since the trip.
        </p>
      </div>

      <div className="border-t border-hairline pt-3">
        {acked ? (
          <p className="prose-text text-safe" role="status">
            Acknowledged. Waiting on the clear-air hold to finish.
          </p>
        ) : (
          <>
            <button
              type="button"
              onClick={handleAck}
              disabled={sending}
              className="w-full cursor-pointer rounded-sm border-2 border-armed bg-transparent py-4 text-lg font-bold text-armed transition-colors hover:bg-armed hover:text-bg disabled:cursor-not-allowed disabled:opacity-40"
            >
              {sending ? 'SENDING' : 'ACKNOWLEDGE'}
            </button>
            <p className="prose-text mt-2 text-ink-faint">
              {clearAirSatisfied
                ? 'The air has been clear long enough. Acknowledging releases the kitchen.'
                : 'You can acknowledge now; the kitchen still waits for the clear-air hold.'}
            </p>
          </>
        )}

        {error && (
          <p className="prose-text mt-2 text-live" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  )
}
