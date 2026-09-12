/**
 * DevModePanel — fake-state controls for the equipment sandbox.
 *
 * Deliberately NOT wired to useKitchen/the backend: this exists so the
 * damper and leak-flow animations can be iterated on with instant hot
 * reload, independent of a live broker/DAQ. The three buttons name real
 * KitchenPhase values (see api/types.ts) so swapping this panel out for the
 * real status later is just replacing where `phase`/flowmeter reading come
 * from — the props Equipment's components take stay the same.
 */

export type SandboxMode = 'WAITING' | 'VENTILATING' | 'LEAKING'

/** Which of the 3 real dampers are open. Independent of `mode` and of each
 *  other — any combination can be tested. */
export interface DamperState {
  exhaust: boolean
  inlet: boolean
  central: boolean
}

export function DevModePanel({
  mode,
  onModeChange,
  dampers,
  onDampersChange,
  leakFlowRate,
  onLeakFlowRateChange,
  ventilationRate,
  onVentilationRateChange,
}: {
  mode: SandboxMode
  onModeChange: (mode: SandboxMode) => void
  dampers: DamperState
  onDampersChange: (dampers: DamperState) => void
  leakFlowRate: number
  onLeakFlowRateChange: (rate: number) => void
  ventilationRate: number
  onVentilationRateChange: (rate: number) => void
}) {
  const modes: SandboxMode[] = ['WAITING', 'VENTILATING', 'LEAKING']

  return (
    <div className="absolute right-4 top-4 z-10 w-64 rounded-md border border-white/15 bg-black/70 p-3 font-mono text-xs text-white backdrop-blur">
      <p className="mb-2 text-[11px] uppercase tracking-wide text-white/50">
        Equipment sandbox — fake state
      </p>

      <div className="mb-3 flex gap-1">
        {modes.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => onModeChange(m)}
            className={`flex-1 cursor-pointer rounded border px-2 py-1 transition-colors ${
              mode === m
                ? 'border-sky-400 bg-sky-400/20 text-sky-200'
                : 'border-white/20 text-white/70 hover:border-white/40'
            }`}
          >
            {m}
          </button>
        ))}
      </div>

      <label className="mb-1 flex items-center justify-between text-white/60">
        <span>leak flow rate</span>
        <span>{leakFlowRate.toFixed(1)}</span>
      </label>
      <input
        type="range"
        min={5}
        max={20}
        step={1}
        value={leakFlowRate}
        onChange={(e) => onLeakFlowRateChange(Number(e.target.value))}
        className="w-full cursor-pointer"
        disabled={mode !== 'LEAKING'}
      />

      <label className="mb-1 flex items-center justify-between text-white/60">
        <span>Ventilation rate</span>
        <span>{ventilationRate.toFixed(0)}</span>
      </label>
      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={ventilationRate}
        onChange={(e) => onVentilationRateChange(Number(e.target.value))}
        className="w-full cursor-pointer"
        disabled={mode !== 'VENTILATING'}
      />

      <p className="mb-1 mt-3 text-[11px] uppercase tracking-wide text-white/50">
        Dampers — independent of mode
      </p>
      <div className="flex flex-col gap-1">
        {(
          [
            ['exhaust', 'Exhaust (roof)'],
            ['inlet', 'Inlet (wall)'],
            ['central', 'Central (roof)'],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="flex cursor-pointer items-center gap-2 text-white/80">
            <input
              type="checkbox"
              checked={dampers[key]}
              onChange={(e) => onDampersChange({ ...dampers, [key]: e.target.checked })}
              className="cursor-pointer"
            />
            {label}
          </label>
        ))}
      </div>

      <p className="mt-3 text-[11px] leading-snug text-white/40">
        Checked dampers open only while VENTILATING. LEAKING animates flow
        along the tube under the water heater, scaled by the rate slider.
      </p>
    </div>
  )
}
