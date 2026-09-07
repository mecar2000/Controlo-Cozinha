/**
 * TimeSeries — the analysis plot: every sensor over a whole run, with the
 * firmware's own phase boundaries as navigation.
 *
 * Only the room animates during replay. This plot shows the WHOLE run
 * statically with a moving playhead, so a position is always seen in
 * context — scrolling the plot with the scrubber would lose exactly the
 * comparison the analysis view exists for.
 *
 * Stage bands come from the historian's stage records, which the phase
 * watcher wrote on each firmware transition. Clicking one jumps there.
 */

import { useEffect, useRef, useState } from 'react'

import type { DaqStage } from '@/api/types'
import { concentrationToCss } from '@/lib/colorScale'
import { applyTransform, unitForMode, yExtent, type PlotMode, type Point } from '@/lib/transforms'

export interface PlotSeries {
  key: string
  label: string
  points: Point[]
}

/**
 * SVG presentation attributes cannot take Tailwind classes, but they DO
 * resolve CSS custom properties — so the plots read the same tokens as the
 * rest of the interface instead of carrying their own copies of the palette.
 */
const AXIS_LINE = 'var(--color-hairline)'
const AXIS_TEXT = 'var(--color-ink-faint)'
const AXIS_LABEL = 'var(--color-ink-dim)'
const PLAYHEAD = 'var(--color-ink)'
const STAGE_TEXT = 'var(--color-ink)'
const TRACE_NEUTRAL = 'var(--color-ink-dim)'
const MONO = 'var(--font-mono)'

/**
 * Stage bands read in the firmware's own vocabulary: run3-leak, run3-hold.
 *
 * Neutral by design. An earlier version painted the leak band with the same
 * red that means "live hazard" and the purge band with the armed amber —
 * which put the two semantic colours on screen for every run ever recorded,
 * whether or not anything was wrong. The bands mark regions of the time axis,
 * so they are distinguished by lightness and separated from state entirely.
 */
const STAGE_TONES: Record<string, string> = {
  leak: 'var(--color-stage-leak)',
  hold: 'var(--color-stage-hold)',
  vent: 'var(--color-stage-vent)',
  purge: 'var(--color-stage-purge)',
}

function stageTone(stage: string): string {
  const suffix = stage.split('-').pop() ?? ''
  return STAGE_TONES[suffix] ?? 'var(--color-stage-other)'
}

export function TimeSeries({
  series,
  stages,
  mode,
  startMs,
  endMs,
  playheadMs,
  onSeek,
  rawUnit = 'mA',
  convertedUnit = '%v/v',
}: {
  series: PlotSeries[]
  stages: DaqStage[]
  mode: PlotMode
  startMs: number
  endMs: number
  playheadMs: number
  onSeek: (tMs: number) => void
  rawUnit?: string
  convertedUnit?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 900, height: 340 })

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      if (entry) {
        setSize({
          width: entry.contentRect.width,
          height: Math.max(200, entry.contentRect.height),
        })
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const { width, height } = size
  const padL = 52
  const padR = 16
  const padT = 14
  const padB = 44 // room for the stage bands
  const plotW = Math.max(1, width - padL - padR)
  const plotH = Math.max(1, height - padT - padB)

  // The baseline window ends when the leak phase does, so "resting" means
  // genuinely before any gas flowed.
  const leakStage = stages.find((s) => s.stage.endsWith('-leak'))
  const transformed = series.map((s) => ({
    ...s,
    points: applyTransform(s.points, mode, leakStage?.start_ms),
  }))

  const [yMin, yMax] = yExtent(transformed)
  const span = endMs - startMs || 1

  const xFor = (t: number) => padL + ((t - startMs) / span) * plotW
  const yFor = (v: number) => padT + plotH - ((v - yMin) / (yMax - yMin || 1)) * plotH

  function handleClick(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const frac = Math.min(1, Math.max(0, (x - padL) / plotW))
    onSeek(startMs + frac * span)
  }

  if (series.length === 0) {
    return (
      <div ref={ref} className="flex h-full items-center justify-center">
        <p className="prose-text text-ink-faint">No readings for this run.</p>
      </div>
    )
  }

  return (
    <div ref={ref} className="h-full w-full">
      <svg
        width={width}
        height={height}
        onClick={handleClick}
        className="cursor-crosshair"
        role="img"
        aria-label="Sensor readings across the run"
      >
        {/* Y grid */}
        {[yMin, (yMin + yMax) / 2, yMax].map((v) => (
          <g key={v}>
            <line x1={padL} x2={width - padR} y1={yFor(v)} y2={yFor(v)} stroke={AXIS_LINE} />
            <text
              x={padL - 6}
              y={yFor(v) + 3}
              textAnchor="end"
              fontSize={10}
              fill={AXIS_TEXT}
              fontFamily={MONO}
            >
              {v.toFixed(2)}
            </text>
          </g>
        ))}

        <text x={4} y={padT + 8} fontSize={10} fill={AXIS_LABEL} fontFamily={MONO}>
          {unitForMode(mode, rawUnit, convertedUnit)}
        </text>

        {/* Stage bands: the firmware's phases as direct navigation. */}
        {stages.map((s) => {
          const x0 = xFor(s.start_ms)
          const x1 = xFor(s.end_ms ?? endMs)
          const w = Math.max(1, x1 - x0)
          const tone = stageTone(s.stage)
          return (
            <g key={`${s.stage}-${s.start_ms}`}>
              {/* The wash behind the traces, and the clickable band beneath
                  them. Both lifted slightly from the old saturated tones,
                  which carried more contrast per unit opacity than these
                  neutrals do. */}
              <rect
                x={x0}
                y={padT}
                width={w}
                height={plotH}
                fill={tone}
                opacity={0.12}
              />
              <rect
                x={x0}
                y={height - padB + 8}
                width={w}
                height={14}
                fill={tone}
                opacity={0.55}
                className="cursor-pointer"
                onClick={(e) => {
                  e.stopPropagation()
                  onSeek(s.start_ms)
                }}
              >
                <title>Jump to {s.stage.split('-').pop()}</title>
              </rect>
              {w > 34 && (
                <text
                  x={x0 + 4}
                  y={height - padB + 18}
                  fontSize={10}
                  fill={STAGE_TEXT}
                  fontFamily={MONO}
                  pointerEvents="none"
                >
                  {s.stage.split('-').pop()}
                </text>
              )}
            </g>
          )
        })}

        {transformed.map((s) => {
          if (s.points.length < 2) return null
          const d = s.points
            .map((p, i) => `${i === 0 ? 'M' : 'L'}${xFor(p.t_ms).toFixed(1)},${yFor(p.value).toFixed(1)}`)
            .join(' ')
          // In converted mode the trace carries the concentration scale, so
          // colour is meaningful. In normalised modes it is not, so traces
          // are neutral and distinguished by the legend instead.
          const last = s.points[s.points.length - 1]!
          const stroke = mode === 'converted' ? concentrationToCss(last.value) : TRACE_NEUTRAL
          return <path key={s.key} d={d} fill="none" stroke={stroke} strokeWidth={1.4} />
        })}

        {/* Playhead */}
        <line
          x1={xFor(playheadMs)}
          x2={xFor(playheadMs)}
          y1={padT}
          y2={padT + plotH}
          stroke={PLAYHEAD}
          strokeWidth={1.5}
        />
      </svg>
    </div>
  )
}
