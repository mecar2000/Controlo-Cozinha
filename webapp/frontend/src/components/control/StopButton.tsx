/**
 * StopButton — the one element that must always work.
 *
 * It never moves, never scrolls out of view, and is never disabled while gas
 * can flow. It is the only large element in the interface, and it commands
 * the present regardless of what else is broken: stop goes straight to MQTT
 * and is never gated on the historian, display mode, or the database.
 *
 * It stays enabled even when the backend looks unreachable. A failed request
 * reports what happened; refusing to send at all would be worse, because the
 * one thing an operator must be able to attempt is stopping.
 */

import { useState } from 'react'

export function StopButton({
  onStop,
  size = 'normal',
}: {
  onStop: () => Promise<void>
  size?: 'normal' | 'large'
}) {
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleClick() {
    setSending(true)
    setError(null)
    try {
      await onStop()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Stop command failed to send')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="shrink-0">
      <button
        type="button"
        onClick={handleClick}
        // Deliberately never disabled: a stop that cannot be attempted is
        // worse than one that reports a failure.
        className={`w-full cursor-pointer rounded-sm border-2 border-live bg-live font-bold tracking-wide text-bg transition-[filter] hover:brightness-110 active:brightness-95 ${
          size === 'large' ? 'py-8 text-4xl' : 'py-6 text-2xl'
        }`}
      >
        {sending ? 'STOPPING' : 'STOP'}
      </button>

      {error && (
        <p className="prose-text mt-2 text-live" role="alert">
          {error} Use the physical emergency stop.
        </p>
      )}
    </div>
  )
}
