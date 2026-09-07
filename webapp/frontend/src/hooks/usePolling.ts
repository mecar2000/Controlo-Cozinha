/**
 * usePolling — repeatedly fetch something, without stampeding on a slow
 * backend.
 *
 * Deliberately not a websocket: the backend mirrors MQTT into memory and
 * exposes it over plain HTTP, so polling is the whole contract. The interval
 * is short enough that a phase change is on screen within a second.
 *
 * Two properties matter for a safety display:
 *   - A failed poll never clears the last good value; it sets `error`, and
 *     the caller decides how to mark it stale. Blanking the room on one
 *     dropped request would be worse than showing a value with a warning.
 *   - Polls never overlap. A slow response delays the next request rather
 *     than queueing another alongside it.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export interface PollingResult<T> {
  data: T | null
  error: Error | null
  /** True until the first response of any kind arrives. */
  loading: boolean
  /** Wall-clock ms of the last successful poll. */
  lastSuccessAt: number | null
  /** Force an immediate poll — used after a command, so the operator sees
   *  the result of their own action without waiting for the next tick. */
  refresh: () => void
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled = true,
): PollingResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null)

  // Held in a ref so changing the fetcher identity between renders does not
  // restart the timer — callers pass inline arrow functions.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelledRef = useRef(false)
  const inFlightRef = useRef(false)

  const poll = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    try {
      const next = await fetcherRef.current()
      if (cancelledRef.current) return
      setData(next)
      setError(null)
      setLastSuccessAt(Date.now())
    } catch (err) {
      if (cancelledRef.current) return
      // Keep the last good data. The caller marks it stale rather than the
      // display going blank on a single dropped request.
      setError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      inFlightRef.current = false
      if (!cancelledRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    // Disabled means disabled: mark cancelled so any poll still in flight
    // from a previously-enabled render discards its result instead of
    // writing state after the caller asked us to stop.
    if (!enabled) {
      cancelledRef.current = true
      if (timerRef.current) clearTimeout(timerRef.current)
      return
    }
    cancelledRef.current = false

    let stopped = false
    const tick = async () => {
      await poll()
      if (stopped || cancelledRef.current) return
      timerRef.current = setTimeout(tick, intervalMs)
    }
    void tick()

    return () => {
      stopped = true
      cancelledRef.current = true
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [poll, intervalMs, enabled])

  const refresh = useCallback(() => {
    // A disabled poller does not fetch on demand either — poll() would
    // discard the result anyway, so skipping saves the request.
    if (cancelledRef.current) return
    void poll()
  }, [poll])

  return { data, error, loading, lastSuccessAt, refresh }
}
