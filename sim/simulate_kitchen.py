#!/usr/bin/env python3
"""
simulate_kitchen — interactive CLI console for the Kitchen simulator,
standing in for real hardware:

  - the kitchen PLC itself (kitchen.ino), reimplemented as KitchenCoreSim
    (see kitchen_core_sim.py) and driven by the same MQTT wire protocol the
    real firmware uses (kitchen/Protocol.cpp / docs/KitchenCore-and-Protocol.md)
  - two remote CM7 DAQ acquisition devices (daq_device_sim.py), each
    publishing 8 H2 sensor channels

The actual MQTT wiring lives in runtime.py (SimRuntime) — this file is just
the terminal front-end. See gui_app.py for a browser-based front-end to the
same runtime.

Point the webapp at the same MQTT broker as this script (same MQTT_HOST /
MQTT_PORT / KITCHEN_DEVICE_ID it already uses — see webapp/.env) and drive
runs from the browser UI as normal; this script stands in for everything
downstream of MQTT.

Usage:
    python sim/simulate_kitchen.py
    python sim/simulate_kitchen.py --host localhost --port 1883 --device-id KITCHEN-01

Interactive console commands (type at the `sim>` prompt):
    status                 show current state/sensors/DAQ snapshot
    estop on|off           toggle the physical e-stop input
    permit on|off          toggle the safety permit value
    permit absent          simulate no permit message ever received (warns only)
    peer on|off            toggle a fake peer-alarm zone
    expansion on|off       toggle EXPANSION_FAULT (missing A0602)
    role leak|equipment    flip the role selector
    daq1 on|off            power the DAQ-1 simulator device on/off
    daq2 on|off            power the DAQ-2 simulator device on/off
    daq1 offline|online    simulate DAQ-1 box itself going unreachable
    daq2 offline|online    simulate DAQ-2 box itself going unreachable
    spike <daq> <ch> <mA>  force one DAQ channel (0-7) to a fixed mA reading
                           e.g. "spike 1 3 18.5" forces DAQ-1 channel 3 high
    clear <daq> [ch]       release a forced channel back to random-walk
    ack                    send the physical-button-equivalent ack
    stop                   send stop (mirrors an operator stop command)
    quit                   exit

Danger conditions immediately drive KitchenCoreSim into FULLY_VENTILATING,
exactly like the real dangerActive() check — this is what lets you exercise
the webapp's alarm/ack/5-minute-hold UI without hydrogen.
"""

from __future__ import annotations

import argparse
import threading
import time

from kitchen_core_sim import now_ms
from runtime import SimRuntime


def print_status(rt: SimRuntime) -> None:
    snap = rt.snapshot()
    print("-" * 60)
    print(f"state={snap['state']} role={snap['role']} "
          f"ackRequired={snap['ackRequired']} acked={snap['acked']} reason={snap['reason']}")
    print(f"gasOpen={snap['gasOpen']} fanSpeedPct={snap['fanSpeedPct']:.0f} "
          f"registers={snap['registers']} inventory_mL={snap['inventory_mL']:.1f}")
    print(f"estop={snap['estop']} permitPresent={snap['permitPresent']} permitValue={snap['permitValue']} "
          f"peerAlarm={snap['peerAlarm']} expansionUnhealthy={snap['expansionUnhealthy']}")
    for daq in snap["daqs"]:
        readings = ", ".join(f"{v:.2f}" for v in daq["channels"])
        print(f"DAQ-{daq['index']} ({daq['deviceId']}) powered={daq['powered']} "
              f"online={daq['online']} mA=[{readings}]")
    print("-" * 60)


def console_loop(rt: SimRuntime, stop_event: threading.Event) -> None:
    print(__doc__)
    daqs = rt.daqs
    while not stop_event.is_set():
        try:
            line = input("sim> ").strip()
        except EOFError:
            break
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()
        now = now_ms()
        try:
            if cmd in ("quit", "exit"):
                stop_event.set()
                break
            elif cmd == "status":
                print_status(rt)
            elif cmd == "estop" and len(parts) == 2:
                rt.sim.core.estop_pressed = parts[1] == "on"
            elif cmd == "permit" and len(parts) == 2:
                if parts[1] == "absent":
                    rt.sim.core.permit_present = False
                else:
                    rt.sim.core.permit_present = True
                    rt.sim.core.permit_value = parts[1] == "on"
            elif cmd == "peer" and len(parts) == 2:
                rt.sim.core.peer_alarm_active = parts[1] == "on"
            elif cmd == "expansion" and len(parts) == 2:
                rt.sim.core.expansion_unhealthy = parts[1] == "on"
            elif cmd == "role" and len(parts) == 2:
                rt.sim.core.role_is_leak_test = parts[1] == "leak"
            elif cmd in ("daq1", "daq2") and len(parts) == 2:
                daq = daqs[0] if cmd == "daq1" else daqs[1]
                if parts[1] == "on":
                    daq.set_powered(True)
                elif parts[1] == "off":
                    daq.set_powered(False)
                elif parts[1] == "online":
                    daq.set_online(True)
                elif parts[1] == "offline":
                    daq.set_online(False)
                else:
                    print("usage: daq1|daq2 on|off|online|offline")
            elif cmd == "spike" and len(parts) == 4:
                daq_num = int(parts[1])
                ch = int(parts[2])
                ma = float(parts[3])
                if daq_num not in (1, 2) or not (0 <= ch <= 7):
                    print("usage: spike <1|2> <0-7> <mA>")
                else:
                    daqs[daq_num - 1].force_leak(ch, ma)
                    print(f"[SIM] forced DAQ-{daq_num} ch{ch} -> {ma} mA")
            elif cmd == "clear" and len(parts) in (2, 3):
                daq_num = int(parts[1])
                if daq_num not in (1, 2):
                    print("usage: clear <1|2> [ch]")
                else:
                    ch = int(parts[2]) if len(parts) == 3 else None
                    daqs[daq_num - 1].clear_force(ch)
            elif cmd == "ack":
                rt.sim.core.human_ack(now)
                print("[SIM] humanAck()")
            elif cmd == "stop":
                rt.sim.core.stop(now)
                print("[SIM] stop()")
            elif cmd == "help":
                print(__doc__)
            else:
                print(f"unrecognised command: {line!r} (try 'help')")
        except Exception as exc:
            print(f"[SIM] console error: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Interactive Kitchen PLC + DAQ simulator")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--device-id", default="KITCHEN-01", help="must match webapp KITCHEN_DEVICE_ID")
    ap.add_argument("--experiment-name", default="KitchenLeaks")
    ap.add_argument("--lab-id", default="lab5")
    ap.add_argument("--daq1-id", default="KITCHEN-DAQ-1")
    ap.add_argument("--daq2-id", default="KITCHEN-DAQ-2")
    args = ap.parse_args()

    rt = SimRuntime(
        host=args.host, port=args.port, user=args.user, password=args.password,
        device_id=args.device_id, experiment_name=args.experiment_name, lab_id=args.lab_id,
        daq1_id=args.daq1_id, daq2_id=args.daq2_id,
    )

    print(f"[SIM] Kitchen PLC sim device_id={args.device_id}, DAQ-1={args.daq1_id}, DAQ-2={args.daq2_id}")
    print(f"[SIM] connected to {args.host}:{args.port}")

    stop_event = threading.Event()
    try:
        console_loop(rt, stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        rt.shutdown()
        print("[SIM] stopped")


if __name__ == "__main__":
    main()
