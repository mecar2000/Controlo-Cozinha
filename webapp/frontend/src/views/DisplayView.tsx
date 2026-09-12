/**
 * DisplayView — for a wall-mounted or standing screen.
 *
 * Room nearly fullscreen, values oversized, no chrome.
 *
 * Display mode genuinely disables the command path: START is refused
 * SERVER-SIDE, so a stray touch or a tab to an invisible button cannot begin
 * a leak. Stop and Acknowledge remain — a screen in the room is exactly where
 * someone standing next to the hazard would reach for stop. Display mode
 * blocks starting things, never stopping them.
 *
 * Both remaining controls are sized for deliberate use, not accidental brush.
 */

import { useEffect, useState } from 'react'

import * as api from '@/api/client'
import { Unit } from '@/components/Unit'
import { StopButton } from '@/components/control/StopButton'
import { RoomScene } from '@/components/room/RoomScene'
import { Legend } from '@/components/room/Legend'
import type { KitchenView } from '@/hooks/useKitchen'
import { formatElapsed, formatNumber, latchCauseLabel } from '@/lib/format'
import { isAtOrAboveLel } from '@/lib/colorScale'
import { peakConcentration } from '@/lib/interpolation'
import { interpolatedElapsed } from '@/lib/interpolatedClock'

/** Fallback ONLY for a payload that predates clearRequiredMs — see the same
 *  constant's comment in components/control/LatchPanel.tsx. */
const CLEAR_AIR_REQUIRED_MS_FALLBACK = 5 * 60 * 1000

/** Renew the display-mode lease well inside the server's expiry window, so a
 *  single dropped request never releases it mid-shift. */
const DISPLAY_LEASE_HEARTBEAT_MS = 10_000

export function DisplayView({ kitchen }: { kitchen: KitchenView }) {
  const status = kitchen.status
  const kitchenState = status?.kitchen_state
  const phase = kitchenState?.phase ?? kitchenState?.state
  const latched = phase === 'FULLY_VENTILATING' && Boolean(kitchenState?.ackRequired)
  const peak = peakConcentration(kitchen.samples)
  const alarming = isAtOrAboveLel(peak) && !kitchen.stale

  // Entering this view takes the display-mode lease server-side, so the
  // refusal is real rather than cosmetic, and a heartbeat renews it.
  //
  // The lease matters because the release on unmount is not guaranteed to
  // happen at all: a wall display usually ends its life by having its tab
  // closed, its browser killed, or its network pulled — none of which send
  // the release. A plain on/off flag would then leave Start disabled for the
  // next operator on a control screen with no way to clear it. Letting the
  // lease lapse fails safe instead.
  useEffect(() => {
    void api.setDisplayMode(true).catch(() => {})
    const id = setInterval(() => {
      void api.setDisplayMode(true).catch(() => {})
    }, DISPLAY_LEASE_HEARTBEAT_MS)

    return () => {
      clearInterval(id)
      // Best effort — the lease expires on its own if this never lands.
      void api.setDisplayMode(false).catch(() => {})
    }
  }, [])

  // Re-render once a second purely to advance the interpolated clock below.
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const requiredMs = kitchenState?.clearRequiredMs ?? CLEAR_AIR_REQUIRED_MS_FALLBACK
  const baseClearForMs = kitchenState?.clearForMs ?? 0
  const clearForMs =
    kitchen.statusReceivedAt != null
      ? (interpolatedElapsed(baseClearForMs, kitchen.statusReceivedAt, nowMs, { ceiling: requiredMs }) ?? 0)
      : baseClearForMs
  const remainingMs = Math.max(0, requiredMs - clearForMs)

  return (
    <div className="flex min-h-0 flex-1">
      <section className="relative min-w-0 flex-1" aria-label="Kitchen, in three dimensions">
        <RoomScene
          sensors={kitchen.sensors}
          samples={kitchen.samples}
          mode="field"
          stale={kitchen.stale}
          showLabels={false}
          kitchenState={kitchenState}
        />
        <div className="absolute bottom-5 left-5 rounded-sm border border-hairline bg-panel/90 px-3 py-2">
          <Legend />
        </div>
      </section>

      <aside className="flex w-[420px] shrink-0 flex-col justify-between border-l border-hairline bg-panel px-6 py-6">
        <div>
          {/* Phase sits one step below the peak reading: the peak is the
              number this screen is built around, and two values at the same
              size would leave neither dominant. Colour carries the phase. */}
          <div
            className={`text-reading font-bold tracking-tight ${
              latched
                ? 'text-armed'
                : phase === 'LEAKING'
                  ? 'text-live'
                  : phase === 'WAITING'
                    ? 'text-safe'
                    : 'text-ink'
            }`}
          >
            {phase ?? 'NO SIGNAL'}
          </div>

          <div className="mt-2 text-reading tabular-nums text-ink-dim">
            {formatElapsed(kitchenState?.elapsedMs)}
          </div>

          {kitchen.stale && (
            <p className="prose-text mt-4 border-l-2 border-armed pl-2 text-armed">
              Live data is stale. These are last-known values.
            </p>
          )}

          {latched && (
            <div className="mt-6 border-t border-hairline pt-4">
              <p className="prose-text text-ink">
                {latchCauseLabel(kitchenState?.dangerReason ?? kitchenState?.latchCause)}
              </p>
              <p className="prose-text mt-2 text-ink-dim">
                {remainingMs === 0
                  ? 'Air has been clear long enough.'
                  : `Clear air for another ${formatElapsed(remainingMs)}.`}
              </p>
            </div>
          )}

          {/* The number this screen exists for. Its label sits above it at
              the label size — from ten feet away the number is the only
              thing that has to be legible, and a same-size caption steals
              from it. */}
          <div className="mt-8">
            <div className="font-sans text-label text-ink-dim">Peak concentration</div>
            <div
              className={`text-value font-bold tabular-nums ${
                alarming ? 'text-live' : 'text-ink'
              }`}
            >
              {formatNumber(peak, 2)}
              <span className="ml-2 font-sans text-lede font-normal text-ink-dim">
                %v/v
              </span>
            </div>
          </div>

          <div className="mt-6 grid grid-cols-2 gap-6">
            <div>
              <div className="font-sans text-label text-ink-dim">Delivered</div>
              <div className="text-reading font-bold tabular-nums">
                {formatNumber(kitchenState?.deliveredInventory_mL, 0)}
                <Unit>mL</Unit>
              </div>
            </div>
            <div>
              <div className="font-sans text-label text-ink-dim">Fan</div>
              <div className="text-reading font-bold tabular-nums">
                {formatNumber(kitchenState?.fanSpeedPct, 0)}
                <Unit>%</Unit>
              </div>
            </div>
          </div>
        </div>

        {/* Stop and acknowledge stay. Sized for deliberate use. */}
        <div className="flex flex-col gap-3">
          {latched && !kitchenState?.acked && (
            <button
              type="button"
              onClick={() => {
                void api.acknowledge().then(kitchen.refresh)
              }}
              className="w-full cursor-pointer rounded-sm border-2 border-armed bg-transparent py-5 text-xl font-bold text-armed transition-colors hover:bg-armed hover:text-bg"
            >
              ACKNOWLEDGE
            </button>
          )}
          <StopButton
            size="large"
            onStop={async () => {
              await api.stop()
              kitchen.refresh()
            }}
          />
        </div>
      </aside>
    </div>
  )
}
