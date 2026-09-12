/**
 * e2e/sim.ts — thin wrapper over sim/gui_app.py's REST API, so E2E tests can
 * drive the simulated PLC/sensors deterministically (pin a channel to a
 * stable value, force a leak, cut a DAQ box's power) instead of only
 * observing whatever the random walk happens to produce.
 *
 * The sim GUI must already be running (see e2e/README.md) — this module
 * makes no attempt to start it, matching playwright.config.ts's choice to
 * not own any of the stack's long-lived processes.
 */

const SIM_URL = process.env.E2E_SIM_URL ?? 'http://localhost:5050'

async function post(path: string, body: unknown = {}): Promise<void> {
  const res = await fetch(`${SIM_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    throw new Error(`sim ${path} -> ${res.status}: ${await res.text()}`)
  }
}

/** daqNum: 1 or 2, matching sim/README.md's KITCHEN-DAQ-1/2. channel: 0-7,
 *  i.e. H2-{channel+1}. */
export const simSpike = (daqNum: 1 | 2, channel: number, mA: number) =>
  post(`/api/daq/${daqNum}/spike`, { channel, mA })

export const simClear = (daqNum: 1 | 2, channel?: number) =>
  post(`/api/daq/${daqNum}/clear`, channel === undefined ? {} : { channel })

export const simDaqPower = (daqNum: 1 | 2, on: boolean) =>
  post(`/api/daq/${daqNum}/power`, { on })

export const simDaqOnline = (daqNum: 1 | 2, on: boolean) =>
  post(`/api/daq/${daqNum}/online`, { on })

export const simEstop = (on: boolean) => post('/api/estop', { on })

export const simAck = () => post('/api/ack')

export const simStop = () => post('/api/stop')

export async function simStatus(): Promise<Record<string, unknown>> {
  const res = await fetch(`${SIM_URL}/api/status`)
  if (!res.ok) throw new Error(`sim /api/status -> ${res.status}`)
  return res.json()
}
