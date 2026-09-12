/**
 * interpolatedClock — advances a last-known "ms elapsed"-shaped value by
 * real wall-clock time since it was received, so a value on screen can tick
 * every second even though the network poll that supplies it runs slower
 * (see useKitchen.ts's STATUS_INTERVAL_MS and problems.txt's "why is the
 * state updated so frequently").
 *
 * Used for both the clear-air countdown (LatchPanel, ceiling = the required
 * hold) and — with no ceiling — a plain elapsed-run clock that should just
 * keep counting up between polls.
 */

export function interpolatedElapsed(
  baseMs: number | null | undefined,
  receivedAtMs: number,
  nowMs: number,
  options?: { ceiling?: number },
): number | null {
  if (baseMs == null) return null
  const advanced = baseMs + Math.max(0, nowMs - receivedAtMs)
  return options?.ceiling != null ? Math.min(advanced, options.ceiling) : advanced
}
