/**
 * ControlView — three zones, never rearranged.
 *
 *   room   fills the left, because spatial truth is the point
 *   rail   fixed width on the right, so the eye learns one location per value
 *   plot   spans the bottom, the time axis under the spatial view
 *
 * The stop button lives at the bottom of the rail, outside any scroll
 * container, so it never moves and never scrolls out of view.
 *
 * When the kitchen latches, the rail is replaced by the latch cause, the
 * clear-air countdown and the acknowledge button — but the room, the plot
 * and stop stay exactly where they were.
 */

import { useState } from 'react'

import * as api from '@/api/client'
import { ConditionBanners } from '@/components/Banner'
import { LatchPanel } from '@/components/control/LatchPanel'
import { NumericRail } from '@/components/control/NumericRail'
import { StopButton } from '@/components/control/StopButton'
import { Legend } from '@/components/room/Legend'
import { RoomScene, type ViewMode } from '@/components/room/RoomScene'
import { ViewModeToggle } from '@/components/room/ViewModeToggle'
import { StartSheet } from '@/components/start/StartSheet'
import { LivePlot } from '@/components/plot/LivePlot'
import type { KitchenView } from '@/hooks/useKitchen'
import { usePolling } from '@/hooks/usePolling'
import type { Run } from '@/api/types'
import { peakConcentration } from '@/lib/interpolation'

export function ControlView({ kitchen }: { kitchen: KitchenView }) {
  const [mode, setMode] = useState<ViewMode>('field')
  const [sheetOpen, setSheetOpen] = useState(false)

  const currentRun = usePolling<Run | null>(() => api.getCurrentRun(), 2000)

  const status = kitchen.status
  const kitchenState = status?.kitchen_state
  const phase = kitchenState?.phase ?? kitchenState?.state
  const latched = phase === 'FULLY_VENTILATING' && Boolean(kitchenState?.ackRequired)

  const peak = peakConcentration(kitchen.samples)
  const run = currentRun.data

  const runLabel = run
    ? `run ${run.run_number}${run.name ? ` · ${run.name}` : ''}`
    : null

  // Display mode is a server-side switch: start is refused by the backend
  // regardless of what this client renders. Hiding the button is a courtesy,
  // not the enforcement.
  const displayMode = status?.display_mode ?? false
  const canStart = !displayMode && !run && phase === 'WAITING'

  // Zones are tracked independently, so more than one can be alarming at once.
  // Name the first and count the rest rather than showing one and implying it
  // is the only one.
  const peerDetail = (() => {
    if (!status?.peer_alarm.active) return null
    const named =
      [status.peer_alarm.detail?.device_id, status.peer_alarm.detail?.lab_id]
        .filter(Boolean)
        .join(', lab ') || null
    const others = (status.peer_alarm.active_count ?? 1) - 1
    if (!named) return null
    return others > 0 ? `${named} (+${others} more)` : named
  })()

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ConditionBanners
        recordingLost={status?.daq.recording_lost ?? false}
        peerAlarmDetail={peerDetail}
        displayMode={displayMode}
      />

      <div className="flex min-h-0 flex-1">
        {/* Room — the hero. */}
        <section className="relative min-w-0 flex-1" aria-label="Kitchen, in three dimensions">
          <RoomScene
            sensors={kitchen.sensors}
            samples={kitchen.samples}
            mode={mode}
            stale={kitchen.stale}
          />

          {/* View toggle, over the canvas. Sensors-only is always one click
              away: if the field looks implausible it can be checked against
              raw values immediately. */}
          <div className="absolute left-4 top-4">
            <ViewModeToggle mode={mode} onModeChange={setMode} />
          </div>

          <div className="absolute bottom-4 left-4 rounded-sm border border-hairline bg-panel/90 px-3 py-2">
            <Legend />
          </div>

          {mode === 'field' && (
            <p className="prose-text absolute bottom-4 right-4 max-w-56 text-right text-ink-faint">
              Opacity shows how well the sensors constrain each region. Thin
              means interpolated far from any sensor.
            </p>
          )}
        </section>

        {/* Rail — fixed width, same place, always. */}
        <aside className="flex w-[340px] shrink-0 flex-col border-l border-hairline bg-panel">
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            {latched ? (
              <LatchPanel
                kitchenState={kitchenState}
                onAcknowledge={async () => {
                  await api.acknowledge()
                  kitchen.refresh()
                }}
              />
            ) : (
              <NumericRail
                kitchenState={kitchenState}
                runLabel={runLabel}
                peakPctVv={peak}
                stale={kitchen.stale}
              />
            )}
          </div>

          {/* Outside the scroll container: stop never scrolls away. */}
          <div className="shrink-0 border-t border-hairline p-4">
            {canStart && (
              <button
                type="button"
                className="btn btn-neutral mb-3 w-full py-2.5"
                onClick={() => setSheetOpen(true)}
              >
                Start a run
              </button>
            )}
            <StopButton
              onStop={async () => {
                await api.stop()
                kitchen.refresh()
                currentRun.refresh()
              }}
            />
          </div>
        </aside>
      </div>

      {/* Plot — the time axis under the spatial view. */}
      <section
        className="h-[180px] shrink-0 border-t border-hairline bg-panel"
        aria-label="Sensor readings over time"
      >
        <LivePlot sensors={kitchen.sensors} stale={kitchen.stale} />
      </section>

      {sheetOpen && (
        <StartSheet
          onClose={() => setSheetOpen(false)}
          onStarted={() => {
            kitchen.refresh()
            currentRun.refresh()
          }}
          daqReachable={status?.daq.reachable ?? null}
        />
      )}
    </div>
  )
}
