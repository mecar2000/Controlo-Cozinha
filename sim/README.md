# Kitchen simulator

Stands in for real hardware so the webapp can be developed/demoed without a
flashed Opta PLC or a real DAQ box. Talks the exact same MQTT wire protocol
the real firmware uses (see `docs/KitchenCore-and-Protocol.md`), so the
webapp cannot tell the difference.

Pieces:

- `kitchen_core_sim.py` — a Python port of the firmware's `KitchenCore`
  state machine (WAITING/ARMED/LEAKING/HOLD/VENTILATING/FULLY_VENTILATING),
  including the danger checks, the ack/5-minute-hold latch, and the 70s
  sensor warm-up gate with phase-clock rebase.
- `daq_device_sim.py` — simulates one remote CM7 DAQ device publishing 8
  H2 sensor channels (4-20 mA) on `DataAcquisition/Kitchen/{deviceId}/H2_n`.
- `runtime.py` — `SimRuntime`: owns the MQTT client, the `KitchenSim` (the
  MQTT-facing wrapper around `KitchenCoreSim`), the two `DaqDeviceSim`
  instances, and the background tick thread. Both front-ends below build
  one of these and drive it.
- `simulate_kitchen.py` — terminal front-end: an interactive `sim>` console.
- `gui_app.py` + `gui_static/index.html` — browser front-end: a small Flask
  app serving a single-page dashboard (status polling + buttons/sliders
  instead of typed commands).

## Run — browser GUI (recommended)

```
cd sim
../.venv/Scripts/python.exe gui_app.py --host localhost --port 1883 --device-id KITCHEN-01
```

Open http://127.0.0.1:5050 (default `--gui-port`). Shows the PLC state
machine (state badge, gas/fan/registers, ack/danger flags) and both DAQ
devices (8 channels each, power/online toggles, per-channel spike-to-mA).
`--gui-host`/`--gui-port` change where the dashboard itself listens; `--host`/
`--port` are still the MQTT broker the simulator connects to.

## Run — terminal console

```
cd sim
../.venv/Scripts/python.exe simulate_kitchen.py --host localhost --port 1883 --device-id KITCHEN-01
```

Type `help` at the `sim>` prompt for the full console command list
(e-stop, permit, peer alarm, sensor spikes, DAQ power/offline).

## Either way

Point it at the same broker + device id the webapp uses (`webapp/.env`),
then start the webapp as usual and drive runs from the browser — start,
confirm, stop, ack all flow through this simulator exactly like the real
PLC, and the GUI/console reflect what the webapp is doing in real time
(and vice versa).

## Notes / simplifications vs the real firmware

- No inlet-open sequencing delay (`INLET_OPEN_DELAY_MS`) — registers are
  simulated as an instantaneous end-state, since the webapp only observes
  the *outcome* of a vent-register combination, not the actuation order.
- No per-sensor calibration offsets — one global mA-to-counts mapping.
- Flow/inventory integration uses a fixed nominal 50 mL/s full-scale rate
  rather than a real flowmeter voltage curve.
- `EXTERNAL_TRIP` is omitted (the real firmware never sets it either).

If the webapp's behavior around a specific edge case matters, check it
against `kitchen/KitchenCore.cpp` / `docs/KitchenCore-and-Protocol.md` —
this simulator is a development aid, not a substitute for firmware tests.
