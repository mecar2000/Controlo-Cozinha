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

import { useEffect } from 'react'

import * as api from '@/api/client'
import { StopButton } from '@/components/control/StopButton'
import { RoomScene } from '@/components/room/RoomScene'
import { Legend } from '@/components/room/Legend'
import type { KitchenView } from '@/hooks/useKitchen'
import { formatElapsed, formatNumber, latchCauseLabel } from '@/lib/format'
import { isAtOrAboveLel } from '@/lib/colorScale'
import { peakConcentration } from '@/lib/interpolation'

const CLEAR_AIR_REQUIRED_MS = 5 * 60 * 1000

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

  const clearForMs = kitchenState?.clearForMs ?? 0
  const remainingMs = Math.max(0, CLEAR_AIR_REQUIRED_MS - clearForMs)

  return (
    <div className="flex min-h-0 flex-1">
      <section className="relative min-w-0 flex-1" aria-label="Kitchen, in three dimensions">
        <RoomScene
          sensors={kitchen.sensors}
          samples={kitchen.samples}
          mode="field"
          stale={kitchen.stale}
          showLabels={false}
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

          <div className="mt-8">
            <div className="text-ink-dim">peak concentration</div>
            <div
              className={`text-value font-bold tabular-nums ${
                alarming ? 'text-live' : 'text-ink'
              }`}
            >
              {formatNumber(peak, 2)}
              <span className="ml-2 text-lede font-normal text-ink-dim">%v/v</span>
            </div>
          </div>

          <div className="mt-6 grid grid-cols-2 gap-6">
            <div>
              <div className="text-ink-dim">delivered</div>
              <div className="text-reading font-bold tabular-nums">
                {formatNumber(kitchenState?.deliveredInventory_mL, 0)}
                <span className="ml-1.5 text-ui font-normal text-ink-dim">mL</span>
              </div>
            </div>
            <div>
              <div className="text-ink-dim">fan</div>
              <div className="text-reading font-bold tabular-nums">
                {formatNumber(kitchenState?.fanSpeedPct, 0)}
                <span className="ml-1.5 text-ui font-normal text-ink-dim">%</span>
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
