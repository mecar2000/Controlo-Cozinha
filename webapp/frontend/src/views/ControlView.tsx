/**
 * ControlView — three zones, never rearranged.
 *
 *   room   fills the left, because spatial truth is the point
 *   rail   fixed width on the right, so the eye learns one location per value
 *   plot   spans the bottom, the time axis under the spatial view
 *
 * The stop button lives at the bottom of the rail, outside any scroll
 * container, so it never moves and never scrolls out of view — regardless
 * of what the scrollable part of the rail is currently showing.
 *
 * The scrollable part of the rail shows exactly one of three things, never
 * more than one: the latch panel (danger latched), the run composer
 * (WAITING, nothing running — problems.txt: config should be fillable
 * directly, not behind a button), or the numeric rail (a run is in flight).
 * The composer's own review-and-confirm step is the one thing that stays a
 * modal — see RunComposer's docstring for why.
 */

import { useState } from 'react'

import * as api from '@/api/client'
import { ConditionBanners } from '@/components/Banner'
import { LatchPanel } from '@/components/control/LatchPanel'
import { NumericRail } from '@/components/control/NumericRail'
import { StopButton } from '@/components/control/StopButton'
import { Legend } from '@/components/room/Legend'
import { ConfigEditor } from '@/components/configs/ConfigEditor'
import { RoomScene, type ViewMode } from '@/components/room/RoomScene'
import { ViewModeToggle } from '@/components/room/ViewModeToggle'
import { SensorPanel } from '@/components/sensors/SensorPanel'
import { RunComposer } from '@/components/start/RunComposer'
import { ReviewModal } from '@/components/start/ReviewModal'
import { LivePlot } from '@/components/plot/LivePlot'
import type { KitchenView } from '@/hooks/useKitchen'
import { usePolling } from '@/hooks/usePolling'
import type { Run, StartRunResponse } from '@/api/types'
import { peakConcentration } from '@/lib/interpolation'

export function ControlView({ kitchen }: { kitchen: KitchenView }) {
  const [mode, setMode] = useState<ViewMode>('field')
  const [sensorPanelOpen, setSensorPanelOpen] = useState(false)
  const [configEditorOpen, setConfigEditorOpen] = useState(false)

  // Owned here, not by RunComposer: start(spec) landing an ack is exactly
  // what flips kitchen_state.phase to ARMED on the next poll, which would
  // unmount RunComposer (canStart requires phase === 'WAITING') — and used
  // to take the review-and-confirm modal down with it, mid-review, before
  // the operator could ever click Confirm. Living here, above that mount
  // boundary, means the modal survives the WAITING -> ARMED transition.
  const [pendingReview, setPendingReview] = useState<StartRunResponse | null>(null)

  // Current-run identity (run number/name) changes far less often than
  // phase/readings — 5s is plenty, and refresh() is called explicitly right
  // after any action that would change it (start/confirm/stop).
  const currentRun = usePolling<Run | null>(() => api.getCurrentRun(), 5000)

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
            kitchenState={kitchenState}
          />

          {/* View toggle, over the canvas. Sensors-only is always one click
              away: if the field looks implausible it can be checked against
              raw values immediately.

              The gap after the toggle is wider than the gap between the two
              buttons that follow, because they are different kinds of
              control: the toggle changes what this canvas DRAWS, while
              Sensors and Configs open editors. Grouping them by spacing
              stops the row reading as one four-item switch. */}
          <div className="absolute left-4 top-4 flex items-center gap-2">
            <ViewModeToggle mode={mode} onModeChange={setMode} />
            <span className="w-3" aria-hidden />
            <button
              type="button"
              aria-label="edit sensors"
              className="cursor-pointer rounded-sm border border-hairline bg-panel/90 px-2.5 py-1 font-sans text-ink-dim transition-colors hover:border-brand-dim hover:text-ink"
              onClick={() => setSensorPanelOpen(true)}
            >
              Sensors
            </button>
            <button
              type="button"
              className="cursor-pointer rounded-sm border border-hairline bg-panel/90 px-2.5 py-1 font-sans text-ink-dim transition-colors hover:border-brand-dim hover:text-ink"
              onClick={() => setConfigEditorOpen(true)}
            >
              Configs
            </button>
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
                statusReceivedAt={kitchen.statusReceivedAt}
                onAcknowledge={async () => {
                  await api.acknowledge()
                  kitchen.refresh()
                }}
              />
            ) : canStart ? (
              <RunComposer
                daqReachable={status?.daq.reachable ?? null}
                onArmed={setPendingReview}
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

          {/* Outside the scroll container: stop never scrolls away, no
              matter which of the three panels above is showing. */}
          <div className="shrink-0 border-t border-hairline p-4">
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

      {sensorPanelOpen && (
        <SensorPanel
          onClose={() => setSensorPanelOpen(false)}
          liveReadings={kitchen.liveReadings}
          liveSensors={kitchen.sensors}
          configAck={status?.config_ack ?? {}}
          onSensorsChanged={kitchen.refreshLayout}
        />
      )}

      {configEditorOpen && <ConfigEditor onClose={() => setConfigEditorOpen(false)} />}

      {pendingReview && (
        <ReviewModal
          result={pendingReview}
          onClose={async () => {
            // Backing out after an ack releases the pending run rather than
            // leaving the firmware armed until its own 60s timeout.
            if (pendingReview.run) {
              try {
                await api.cancelRun(pendingReview.run.id)
              } catch {
                /* the firmware disarms itself after ARM_TIMEOUT_MS regardless */
              }
            }
            setPendingReview(null)
            kitchen.refresh()
            currentRun.refresh()
          }}
          onConfirmed={() => {
            setPendingReview(null)
            kitchen.refresh()
            currentRun.refresh()
          }}
        />
      )}
    </div>
  )
}
