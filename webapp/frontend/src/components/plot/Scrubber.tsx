/**
 * Scrubber — replay transport for a past run.
 *
 * Speed runs 0.05x to 5x. The slow end is the interesting one: a cloud
 * crossing LEL does so in seconds, and 0.05x is what makes that readable.
 * The fast end skims the ventilation tail.
 */

import { useEffect, useRef } from 'react'

import { formatElapsed } from '@/lib/format'

const SPEEDS = [0.05, 0.1, 0.25, 0.5, 1, 2, 5] as const

export function Scrubber({
  startMs,
  endMs,
  playheadMs,
  playing,
  speed,
  onSeek,
  onPlayingChange,
  onSpeedChange,
}: {
  startMs: number
  endMs: number
  playheadMs: number
  playing: boolean
  speed: number
  onSeek: (t: number) => void
  onPlayingChange: (p: boolean) => void
  onSpeedChange: (s: number) => void
}) {
  const rafRef = useRef<number | null>(null)
  const lastRef = useRef<number>(0)

  // The loop reads these through refs so that advancing the playhead does not
  // re-run the effect below. Depending on `playheadMs` directly meant every
  // single frame tore down the loop and started a new one — it happened to
  // work, but it reset the frame clock on each tick and left the animation
  // one restart away from stuttering.
  const playheadRef = useRef(playheadMs)
  playheadRef.current = playheadMs
  const seekRef = useRef(onSeek)
  seekRef.current = onSeek
  const playingChangeRef = useRef(onPlayingChange)
  playingChangeRef.current = onPlayingChange

  // Advance the playhead in real time, scaled by the speed control. Stops at
  // the end rather than looping — a run that ended should look ended.
  useEffect(() => {
    if (!playing) return

    lastRef.current = performance.now()
    const step = (now: number) => {
      const dt = now - lastRef.current
      lastRef.current = now
      const next = playheadRef.current + dt * speed
      if (next >= endMs) {
        seekRef.current(endMs)
        playingChangeRef.current(false)
        return
      }
      seekRef.current(next)
      rafRef.current = requestAnimationFrame(step)
    }
    rafRef.current = requestAnimationFrame(step)

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
  }, [playing, speed, endMs])

  const elapsed = playheadMs - startMs
  const total = endMs - startMs

  return (
    <div className="flex items-center gap-4 border-t border-hairline bg-panel px-4 py-2.5">
      <button
        type="button"
        className="btn btn-neutral w-20"
        onClick={() => onPlayingChange(!playing)}
      >
        {playing ? 'Pause' : 'Play'}
      </button>

      <input
        type="range"
        min={startMs}
        max={endMs}
        value={playheadMs}
        step={Math.max(1, Math.round(total / 2000))}
        onChange={(e) => onSeek(Number(e.target.value))}
        className="min-w-0 flex-1 accent-white"
        aria-label="Replay position"
      />

      <span className="shrink-0 tabular-nums text-ink">
        {formatElapsed(elapsed)}
        <span className="text-ink-faint"> / {formatElapsed(total)}</span>
      </span>

      <label className="flex shrink-0 items-center gap-2">
        <span className="sr-only">Playback speed</span>
        <select
          className="field-input w-24 py-1"
          value={speed}
          onChange={(e) => onSpeedChange(Number(e.target.value))}
        >
          {SPEEDS.map((s) => (
            <option key={s} value={s}>
              {s}×
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}
