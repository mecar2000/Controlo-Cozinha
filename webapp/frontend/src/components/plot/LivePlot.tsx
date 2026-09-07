/**
 * LivePlot — a rolling window of every sensor, under the room.
 *
 * Deliberately thin: this is the time axis beneath the spatial view, not the
 * analysis surface. It answers "is this rising or falling, and which sensor
 * is leading" at a glance. Anything more — transforms, stage bands, replay —
 * belongs in the Analysis view.
 *
 * History is kept client-side because the backend mirrors only the LATEST
 * reading per sensor; the historian owns real history. So this buffer is
 * explicitly a view of the last few minutes, not a record, and it resets on
 * reload. The LEL line is drawn because crossing it is the event this plot
 * exists to make visible.
 *
 * The buffer lives in a REF, not in state.
 *
 * `useKitchen` rebuilds its sensor array on every poll, so a state-backed
 * buffer re-rendered this component at the polling rate and copied the whole
 * point set each time — the most expensive thing on a screen that must not
 * stutter during an alarm. Appending to a ref and redrawing on a fixed
 * cadence decouples "how often readings arrive" from "how often the DOM
 * changes", and keeps the redraw cost flat as the window fills.
 */

import { useEffect, useRef, useState } from 'react'

import type { LiveSensor } from '@/hooks/useKitchen'
import { concentrationToCss, LEL_PCT_VV } from '@/lib/colorScale'

/** How much recent history the rolling window holds. */
const WINDOW_MS = 5 * 60 * 1000
/** How often the trace is redrawn. Independent of the 1 Hz poll: the window
 *  is five minutes wide, so a frame every 500 ms is already finer than one
 *  pixel of movement on any realistic width. */
const REDRAW_MS = 500
/** Y ceiling until a reading needs more room. Pinned low so ordinary
 *  sub-LEL activity is legible rather than flattened against the axis. */
const BASE_Y_MAX = 1.0

/** Axis and label colours, read from the same tokens as the rest of the
 *  interface — SVG presentation attributes resolve CSS custom properties. */
const AXIS_LINE = 'var(--color-hairline)'
const AXIS_TEXT = 'var(--color-ink-faint)'
const LEL_LINE = '#ff8a1f'
const MONO = 'var(--font-mono)'

interface Trace {
  key: string
  label: string
  points: Array<{ t: number; v: number }>
}

export function LivePlot({
  sensors,
  stale,
}: {
  sensors: LiveSensor[]
  stale: boolean
}) {
  // The rolling buffer. Mutated on every poll, read on every redraw.
  const tracesRef = useRef<Map<string, Trace>>(new Map())
  const frameRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(800)
  // Bumped on a timer purely to trigger a redraw from the ref.
  const [, setTick] = useState(0)

  useEffect(() => {
    const el = frameRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Append the current readings to the rolling buffer. No setState here, so
  // a poll that changes nothing visible costs nothing.
  useEffect(() => {
    if (stale) return
    const now = Date.now()
    const cutoff = now - WINDOW_MS
    const traces = tracesRef.current

    // A sensor removed from the layout must stop being drawn. Without this
    // the map only ever grew, and a deleted sensor's trace lingered until
    // the page was reloaded.
    const present = new Set(sensors.map((s) => s.key))
    for (const key of traces.keys()) {
      if (!present.has(key)) traces.delete(key)
    }

    for (const s of sensors) {
      if (!s.hasReading) continue
      const existing = traces.get(s.key)
      if (existing) {
        existing.label = s.label
        existing.points.push({ t: now, v: s.value })
        // Drop from the front rather than rebuilding the array, so a long
        // shift stays bounded at constant cost.
        let drop = 0
        while (drop < existing.points.length && existing.points[drop]!.t < cutoff) drop++
        if (drop > 0) existing.points.splice(0, drop)
      } else {
        traces.set(s.key, { key: s.key, label: s.label, points: [{ t: now, v: s.value }] })
      }
    }
  }, [sensors, stale])

  // Redraw on a fixed cadence, so render frequency is set here rather than
  // by however fast readings happen to arrive.
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), REDRAW_MS)
    return () => clearInterval(id)
  }, [])

  const height = 180
  const padL = 44
  const padR = 12
  const padT = 12
  const padB = 22
  const plotW = Math.max(1, width - padL - padR)
  const plotH = height - padT - padB

  const all = Array.from(tracesRef.current.values())
  const now = Date.now()
  const tMin = now - WINDOW_MS
  let yMax = BASE_Y_MAX
  for (const tr of all) for (const p of tr.points) if (p.v > yMax) yMax = p.v
  yMax = Math.ceil(yMax * 1.15 * 10) / 10

  const xFor = (t: number) => padL + ((t - tMin) / WINDOW_MS) * plotW
  const yFor = (v: number) => padT + plotH - (v / yMax) * plotH

  const hasData = all.some((t) => t.points.length > 1)

  return (
    <div ref={frameRef} className="h-full w-full">
      {!hasData ? (
        <div className="flex h-full items-center justify-center">
          <p className="prose-text text-ink-faint">
            {stale
              ? 'Live data is stale. The plot resumes when the broker link recovers.'
              : 'Waiting for sensor readings.'}
          </p>
        </div>
      ) : (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label="Sensor concentration over the last five minutes"
        >
          {/* Y axis ticks */}
          {[0, yMax / 2, yMax].map((v) => (
            <g key={v}>
              <line
                x1={padL}
                x2={width - padR}
                y1={yFor(v)}
                y2={yFor(v)}
                stroke={AXIS_LINE}
                strokeWidth={1}
              />
              <text
                x={padL - 6}
                y={yFor(v) + 3}
                textAnchor="end"
                fontSize={10}
                fill={AXIS_TEXT}
                fontFamily={MONO}
              >
                {v.toFixed(1)}
              </text>
            </g>
          ))}

          {/* LEL, if it is in range. The one reference line worth drawing. */}
          {LEL_PCT_VV <= yMax && (
            <>
              <line
                x1={padL}
                x2={width - padR}
                y1={yFor(LEL_PCT_VV)}
                y2={yFor(LEL_PCT_VV)}
                stroke={LEL_LINE}
                strokeWidth={1}
                strokeDasharray="4 3"
              />
              <text
                x={width - padR}
                y={yFor(LEL_PCT_VV) - 4}
                textAnchor="end"
                fontSize={10}
                fill={LEL_LINE}
                fontFamily={MONO}
              >
                LEL
              </text>
            </>
          )}

          {all.map((tr) => {
            if (tr.points.length < 2) return null
            const d = tr.points
              .map((p, i) => `${i === 0 ? 'M' : 'L'}${xFor(p.t).toFixed(1)},${yFor(p.v).toFixed(1)}`)
              .join(' ')
            const last = tr.points[tr.points.length - 1]!
            return (
              <path
                key={tr.key}
                d={d}
                fill="none"
                stroke={concentrationToCss(last.v)}
                strokeWidth={1.5}
                strokeLinejoin="round"
                opacity={stale ? 0.4 : 1}
              />
            )
          })}

          <text x={padL} y={height - 6} fontSize={10} fill={AXIS_TEXT} fontFamily={MONO}>
            5 min ago
          </text>
          <text
            x={width - padR}
            y={height - 6}
            textAnchor="end"
            fontSize={10}
            fill={AXIS_TEXT}
            fontFamily={MONO}
          >
            now
          </text>
        </svg>
      )}
    </div>
  )
}
