/**
 * App — the shell: header, the current view, and the one piece of global
 * state (which view is showing).
 *
 * The view is set once per screen rather than flipped during a run, but it
 * lives in the header rather than buried in settings, because someone will
 * need to undo it on an unfamiliar machine.
 */

import { useEffect, useRef, useState } from 'react'

import * as api from '@/api/client'
import { StopButton } from '@/components/control/StopButton'
import { Header, type ViewName } from '@/components/Header'
import { useKitchen } from '@/hooks/useKitchen'
import { shouldSwitchToDisplayOnLeak } from '@/lib/viewSwitch'
import { AnalysisView } from '@/views/AnalysisView'
import { ControlView } from '@/views/ControlView'
import { DisplayView } from '@/views/DisplayView'

/**
 * What replaces the views when the backend cannot be reached.
 *
 * STOP stays. Every other control is gone because nothing on screen is
 * current, but removing the stop button would contradict the rule it is
 * built on: a stop that cannot be ATTEMPTED is worse than one that reports a
 * failure. The request may well fail — the button says so when it does, and
 * points at the physical stop — but the operator gets to try from here
 * rather than being told to walk away from the screen.
 */
function OfflineScreen({ onStop }: { onStop: () => Promise<void> }) {
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <div className="w-full max-w-md text-center">
        <p className="mb-2 text-lede text-live">Cannot reach the kitchen server</p>
        <p className="prose-text text-ink-dim">
          The control app is not responding. Nothing on this screen is current.
        </p>
        <div className="mt-6 text-left">
          <StopButton onStop={onStop} />
        </div>
        <p className="prose-text mt-3 text-ink-faint">
          Stop is sent directly to the broker. If it cannot be delivered, use
          the physical emergency stop.
        </p>
      </div>
    </div>
  )
}

export function App() {
  const [view, setView] = useState<ViewName>('control')
  const kitchen = useKitchen()

  // problems.txt Area C1: a leak starting should pull whoever is looking at
  // this screen to the wall-display view, not leave the plot buried under
  // the room on Control. An edge trigger (see shouldSwitchToDisplayOnLeak),
  // so it fires once per leak rather than fighting an operator who
  // deliberately navigates back to Control mid-leak.
  const previousPhaseRef = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    const phase = kitchen.status?.kitchen_state?.phase ?? kitchen.status?.kitchen_state?.state
    if (shouldSwitchToDisplayOnLeak(previousPhaseRef.current, phase)) {
      setView('display')
    }
    previousPhaseRef.current = phase
  }, [kitchen.status?.kitchen_state?.phase, kitchen.status?.kitchen_state?.state])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg">
      <Header
        status={kitchen.status}
        view={view}
        onViewChange={setView}
        offline={kitchen.offline}
      />

      {kitchen.offline ? (
        <OfflineScreen
          onStop={async () => {
            await api.stop()
            kitchen.refresh()
          }}
        />
      ) : view === 'control' ? (
        <ControlView kitchen={kitchen} />
      ) : view === 'analysis' ? (
        <AnalysisView />
      ) : (
        <DisplayView kitchen={kitchen} />
      )}
    </div>
  )
}
