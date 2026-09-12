/**
 * Legend — the colour scale, in both %v/v and % LEL.
 *
 * Both units because the operator thinks in both: %v/v is what the sensors
 * report, % LEL is what "how close are we to trouble" means. The LEL mark is
 * called out, since it is the boundary the whole ramp is built around.
 *
 * The heatmap is always %v/v regardless of the plot's unit toggle, because
 * this scale is pinned to LEL — so this legend never changes with it.
 */

import {
  legendGradientCss,
  LEGEND_TICKS_PCT_VV,
  LEL_PCT_VV,
  pctVvToPctLel,
  rampPosition,
} from '@/lib/colorScale'

export function Legend({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? 'w-56' : 'w-72'}>
      <div
        className="h-2.5 w-full rounded-sm"
        style={{ background: legendGradientCss() }}
        role="img"
        aria-label="Concentration colour scale, 0 to 20 percent by volume"
      />

      {/* Ticks are positioned along the same ramp function the field uses,
          so the legend cannot drift out of step with what is drawn. */}
      <div className="relative mt-1 h-3.5">
        {LEGEND_TICKS_PCT_VV.map((tick) => {
          const atLel = tick === LEL_PCT_VV
          const pos = rampPosition(tick)
          // The end ticks anchor to their own edge instead of centring on it.
          // Centred, half of "0" and half of "20" fell outside the bar.
          const edge = pos <= 0 ? 'start' : pos >= 1 ? 'end' : 'middle'
          return (
            <span
              key={tick}
              className={`absolute text-micro ${
                edge === 'middle' ? '-translate-x-1/2' : edge === 'end' ? '-translate-x-full' : ''
              } ${atLel ? 'text-ink' : 'text-ink-faint'}`}
              style={{ left: `${pos * 100}%` }}
            >
              {tick}
            </span>
          )
        })}
      </div>

      <div className="mt-0.5 flex items-baseline justify-between text-micro text-ink-faint">
        <span>%v/v</span>
        <span className="font-sans">
          LEL at <span className="font-mono">{LEL_PCT_VV}</span> %v/v ={' '}
          <span className="font-mono">{pctVvToPctLel(LEL_PCT_VV)}</span>%
        </span>
      </div>
    </div>
  )
}
