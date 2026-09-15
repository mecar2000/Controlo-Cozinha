/**
 * AnalysisView — inverted proportions: plots dominant, room secondary but
 * scrubbable.
 *
 * Replay resamples every sensor onto one common time grid, because LTTB
 * returns each sensor on its own timestamps — correct for plotting, unusable
 * for a heatmap that needs all sensors at one instant. The plot keeps the
 * original points; only the room reads the grid.
 *
 * When a run has no layout snapshot, the room draws with today's layout and
 * SAYS SO. Sensors get moved between experiments, and an old replay drawn
 * against a current layout would be quietly wrong.
 */

import { useEffect, useMemo, useState } from 'react'

import * as api from '@/api/client'
import { Scrubber } from '@/components/plot/Scrubber'
import { TimeSeries, type PlotSeries, type StageBand } from '@/components/plot/TimeSeries'
import { Legend } from '@/components/room/Legend'
import { RoomScene, type ViewMode } from '@/components/room/RoomScene'
import { ViewModeToggle } from '@/components/room/ViewModeToggle'
import type { DaqStage, LayoutForRun, Run } from '@/api/types'
import type { LiveSensor } from '@/hooks/useKitchen'
import { formatTimestamp, outcomeLabel } from '@/lib/format'
import type { SensorSample } from '@/lib/interpolation'
import {
  buildTimeGrid,
  indexForTime,
  resampleAll,
  seriesExtent,
  type SeriesPoint,
} from '@/lib/resample'
import { PLOT_MODES, type PlotMode } from '@/lib/transforms'

/** DataAcquisition only records each stage's FIRST reading (`started_at`) —
 *  there is no per-stage end time anywhere in its schema. A stage's end is
 *  therefore inferred as the next stage's start (or the run's own end for
 *  the last one) — an approximation, but the right one: stages are
 *  contiguous phases of a single run, so "until the next phase starts" is
 *  exactly the boundary a phase transition draws. */
function stageBands(stages: DaqStage[], runEndMs: number): StageBand[] {
  const withMs = stages
    .filter((s) => s.started_at)
    .map((s) => ({ stage: s.stage, start_ms: new Date(s.started_at as string).getTime() }))
    .filter((s) => Number.isFinite(s.start_ms))
    .sort((a, b) => a.start_ms - b.start_ms)

  return withMs.map((s, i) => ({
    stage: s.stage,
    start_ms: s.start_ms,
    end_ms: withMs[i + 1]?.start_ms ?? runEndMs,
  }))
}

export function AnalysisView() {
  const [runs, setRuns] = useState<Run[]>([])
  const [runId, setRunId] = useState<number | null>(null)
  const [series, setSeries] = useState<PlotSeries[]>([])
  const [stages, setStages] = useState<DaqStage[]>([])
  const [layout, setLayout] = useState<LayoutForRun | null>(null)
  const [mode, setMode] = useState<PlotMode>('converted')
  const [roomMode, setRoomMode] = useState<ViewMode>('field')
  const [playheadMs, setPlayheadMs] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void api
      .listRuns(50)
      .then((r) => {
        setRuns(r)
        const firstRecorded = r.find((x) => x.daq_experiment_id != null)
        if (firstRecorded) setRunId(firstRecorded.id)
      })
      .catch((err: Error) => setError(err.message))
  }, [])

  const run = runs.find((r) => r.id === runId) ?? null

  useEffect(() => {
    if (!run?.daq_experiment_id) {
      setSeries([])
      setStages([])
      setLayout(null)
      return
    }
    setLoading(true)
    setError(null)

    Promise.all([
      api.getHistory(run.daq_experiment_id),
      api.listStages(run.daq_experiment_id).catch(() => [] as DaqStage[]),
      api.getLayoutForRun(run.id).catch(() => null),
    ])
      .then(([history, stageList, layoutForRun]) => {
        setSeries(
          (history.series ?? []).map((s) => ({
            key: `${s.device_id}/${s.sensor_name}`,
            label: s.sensor_name,
            points: s.points ?? [],
          })),
        )
        setStages(stageList)
        setLayout(layoutForRun)
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [run])

  const extent = useMemo(() => seriesExtent(series), [series])
  const startMs = extent?.startMs ?? 0
  const endMs = extent?.endMs ?? 1

  useEffect(() => {
    setPlayheadMs(startMs)
    setPlaying(false)
  }, [startMs])

  // The common time grid — the operation LTTB cannot provide and the heatmap
  // requires.
  const grid = useMemo(() => buildTimeGrid(startMs, endMs), [startMs, endMs])
  const resampled = useMemo(
    () =>
      resampleAll(
        series.map((s) => ({ key: s.key, points: s.points as SeriesPoint[] })),
        grid,
      ),
    [series, grid],
  )

  // Sensors at the playhead instant, positioned by the run's own layout.
  const { roomSensors, roomSamples } = useMemo(() => {
    const positions = layout?.layout ?? []
    const idx = indexForTime(grid, playheadMs)
    const byKey = new Map(resampled.map((r) => [r.key, r]))

    const sensors: LiveSensor[] = positions.map((p) => {
      const lookup = `${p.daq_device_id ?? ''}/${p.daq_sensor_name ?? p.sensor_key}`
      const match =
        byKey.get(lookup) ??
        resampled.find((r) => r.key.endsWith(`/${p.daq_sensor_name ?? p.sensor_key}`))
      const value = match?.values[idx] ?? 0
      return {
        key: p.sensor_key,
        label: p.label,
        x: p.x,
        y: p.y,
        z: p.z,
        value,
        hasReading: match != null,
        ageS: 0,
        unit: '%v/v',
      }
    })

    const samples: SensorSample[] = sensors
      .filter((s) => s.hasReading)
      .map(({ x, y, z, value }) => ({ x, y, z, value }))

    return { roomSensors: sensors, roomSamples: samples }
  }, [layout, resampled, grid, playheadMs])

  const recordedRuns = runs.filter((r) => r.daq_experiment_id != null)

  // An unrecorded_test_run's row is deleted the moment it ends (app.runs
  // never lets it pollute the permanent record), so it can never appear
  // here — no need to distinguish "no runs" from "runs, none recorded".
  const nothingToShow = recordedRuns.length === 0

  if (nothingToShow) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-6">
        <div className="max-w-md">
          <p className="text-lede text-ink">Nothing to replay yet</p>
          <p className="prose-text mt-2 text-ink-dim">
            Replay reads from the historian, so a run has to finish before it
            can be reviewed. Start one from the control view.
          </p>
          {error && (
            <p className="prose-text mt-4 border-l-2 border-live pl-2 text-live" role="alert">
              {error}
            </p>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Controls */}
      <div className="flex shrink-0 flex-wrap items-center gap-4 border-b border-hairline bg-panel px-4 py-2.5">
        <label className="flex items-center gap-2">
          <span className="font-sans text-label text-ink-dim">Run</span>
          <select
            className="field-input w-64 py-1"
            value={runId ?? ''}
            onChange={(e) => setRunId(e.target.value ? Number(e.target.value) : null)}
          >
            {recordedRuns.map((r) => (
              <option key={r.id} value={r.id}>
                run {r.run_number}
                {r.name ? ` · ${r.name}` : ''} · {outcomeLabel(r.outcome)}
              </option>
            ))}
          </select>
        </label>

        <div className="flex items-center gap-1" role="group" aria-label="Plot mode">
          {PLOT_MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              title={m.hint}
              onClick={() => setMode(m.id)}
              aria-pressed={mode === m.id}
              className={`cursor-pointer rounded-sm border px-2.5 py-1 font-sans transition-colors ${
                mode === m.id ? 'seg-on' : 'border-transparent text-ink-dim hover:text-ink'
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>

        {run && (
          <span className="ml-auto flex items-baseline gap-2.5 text-ink-faint">
            <span className="tabular-nums">{formatTimestamp(run.started_at)}</span>
            <span className="font-sans text-label">{outcomeLabel(run.outcome)}</span>
          </span>
        )}
      </div>

      {layout?.is_current_fallback && (
        <p
          className="prose-text shrink-0 border-l-2 border-armed bg-panel px-3 py-2 text-armed"
          role="status"
        >
          This run has no saved layout, so the room is drawn with today's
          sensor positions. If sensors have moved since, the markers are in
          the wrong places.
        </p>
      )}

      {error && (
        <p className="prose-text shrink-0 border-l-2 border-live bg-panel px-3 py-2 text-live" role="alert">
          {error}
        </p>
      )}

      {/* Plots dominant, room secondary. */}
      <div className="flex min-h-0 flex-1">
        <section className="min-w-0 flex-[2] border-r border-hairline" aria-label="Readings over time">
          {loading ? (
            <div className="flex h-full items-center justify-center">
              <p className="prose-text text-ink-faint">Loading readings…</p>
            </div>
          ) : (
            <TimeSeries
              series={series}
              stages={stageBands(stages, endMs)}
              mode={mode}
              startMs={startMs}
              endMs={endMs}
              playheadMs={playheadMs}
              onSeek={setPlayheadMs}
            />
          )}
        </section>

        <section className="relative min-w-0 flex-1" aria-label="Room at the playhead">
          <RoomScene
            sensors={roomSensors}
            samples={roomSamples}
            mode={roomMode}
            showLabels={false}
          />
          <div className="absolute left-3 top-3">
            <ViewModeToggle mode={roomMode} onModeChange={setRoomMode} compact />
          </div>
          <div className="absolute bottom-3 left-3 rounded-sm border border-hairline bg-panel/90 px-2 py-1.5">
            <Legend compact />
          </div>
        </section>
      </div>

      {extent && (
        <Scrubber
          startMs={startMs}
          endMs={endMs}
          playheadMs={playheadMs}
          playing={playing}
          speed={speed}
          onSeek={setPlayheadMs}
          onPlayingChange={setPlaying}
          onSpeedChange={setSpeed}
        />
      )}
    </div>
  )
}
