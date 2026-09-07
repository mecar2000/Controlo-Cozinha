/**
 * Banner — a persistent condition that changes what the operator can do.
 *
 * Reserved for state that PERSISTS and MATTERS: recording lost mid-run, a
 * peer lab's alarm, display mode active. Transient errors belong next to the
 * control that produced them, not up here.
 *
 * Each banner says what happened and what it means for the operator, in the
 * interface's voice. No apologies, no vagueness about what is affected.
 */

import type { ReactNode } from 'react'

export type BannerTone = 'armed' | 'live' | 'neutral'

const TONE_CLASS: Record<BannerTone, string> = {
  armed: 'border-armed text-armed',
  live: 'border-live text-live',
  neutral: 'border-hairline text-ink-dim',
}

export function Banner({
  tone = 'neutral',
  children,
}: {
  tone?: BannerTone
  children: ReactNode
}) {
  return (
    <div
      role="status"
      className={`prose-text shrink-0 border-l-2 bg-panel px-3 py-2 ${TONE_CLASS[tone]}`}
    >
      {children}
    </div>
  )
}

/** The banners the Control and Display views both need. */
export function ConditionBanners({
  recordingLost,
  peerAlarmDetail,
  displayMode,
}: {
  recordingLost: boolean
  peerAlarmDetail: string | null
  displayMode: boolean
}) {
  return (
    <>
      {peerAlarmDetail && (
        <Banner tone="live">
          Another lab PLC has raised a hydrogen alarm. The kitchen ventilates
          fully until it clears. {peerAlarmDetail}
        </Banner>
      )}

      {recordingLost && (
        <Banner tone="armed">
          The historian stopped responding during this run. The run continues
          and stop still works, but readings from this point are not being
          stored.
        </Banner>
      )}

      {displayMode && (
        <Banner tone="neutral">
          Display mode. Starting a run is disabled on this screen. Stop and
          acknowledge still work.
        </Banner>
      )}
    </>
  )
}
