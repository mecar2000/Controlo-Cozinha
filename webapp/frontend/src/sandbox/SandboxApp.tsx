/**
 * SandboxApp — top-level component for sandbox.html. Owns the fake mode/
 * damper/leak-rate state locally; no backend, no useKitchen, no MQTT.
 */

import { useState } from 'react'

import { DevModePanel, type DamperState, type SandboxMode } from './DevModePanel'
import { SandboxScene } from './SandboxScene'

export function SandboxApp() {
  const [mode, setMode] = useState<SandboxMode>('VENTILATING')
  const [dampers, setDampers] = useState<DamperState>({
    exhaust: true,
    inlet: true,
    central: false,
  })
  const [leakFlowRate, setLeakFlowRate] = useState(1)
  const [ventilationRate, setVentilationRate] = useState(50)

  return (
    <div className="relative h-screen w-screen bg-black">
      <SandboxScene mode={mode} dampers={dampers} leakFlowRate={leakFlowRate} ventilationRate={ventilationRate} />
      <DevModePanel
        mode={mode}
        onModeChange={setMode}
        dampers={dampers}
        onDampersChange={setDampers}
        leakFlowRate={leakFlowRate}
        onLeakFlowRateChange={setLeakFlowRate}
        ventilationRate={ventilationRate}
        onVentilationRateChange={setVentilationRate}
      />
    </div>
  )
}
