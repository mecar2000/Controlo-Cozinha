#!/usr/bin/env python3
"""
gui_app — browser GUI for the Kitchen simulator (runtime.py). A tiny Flask
app: one polling status endpoint + a handful of action endpoints, and a
single static HTML/JS page that renders the PLC state and the two DAQ
devices with buttons/sliders instead of the CLI console's typed commands.

Usage:
    python sim/gui_app.py
    python sim/gui_app.py --host localhost --port 1883 --device-id KITCHEN-01 --gui-port 5050

Then open http://localhost:5050 in a browser. Point the webapp itself at
the same MQTT broker/device-id (webapp/.env) to see runs started from the
Kitchen webapp UI reflected here, and vice versa.
"""

from __future__ import annotations

import argparse
import os

from flask import Flask, jsonify, request, send_from_directory

from kitchen_core_sim import now_ms
from runtime import SimRuntime

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_static")

app = Flask(__name__, static_folder=None)
_rt: SimRuntime | None = None


def rt() -> SimRuntime:
    if _rt is None:
        raise RuntimeError("runtime not initialised")
    return _rt


@app.get("/")
def index():
    return send_from_directory(_STATIC_DIR, "index.html")


@app.get("/api/status")
def api_status():
    return jsonify(rt().snapshot())


@app.post("/api/estop")
def api_estop():
    rt().sim.core.estop_pressed = bool(request.json.get("on"))
    return jsonify(ok=True)


@app.post("/api/permit")
def api_permit():
    body = request.json or {}
    mode = body.get("mode")  # "on" | "off" | "absent"
    with rt().sim.lock:
        if mode == "absent":
            rt().sim.core.permit_present = False
        else:
            rt().sim.core.permit_present = True
            rt().sim.core.permit_value = mode == "on"
    return jsonify(ok=True)


@app.post("/api/peer_alarm")
def api_peer_alarm():
    rt().sim.core.peer_alarm_active = bool(request.json.get("on"))
    return jsonify(ok=True)


@app.post("/api/expansion")
def api_expansion():
    rt().sim.core.expansion_unhealthy = bool(request.json.get("on"))
    return jsonify(ok=True)


@app.post("/api/role")
def api_role():
    rt().sim.core.role_is_leak_test = request.json.get("role") == "leak-test"
    return jsonify(ok=True)


@app.post("/api/ack")
def api_ack():
    rt().sim.core.human_ack(now_ms())
    return jsonify(ok=True)


@app.post("/api/stop")
def api_stop():
    rt().sim.core.stop(now_ms())
    return jsonify(ok=True)


@app.post("/api/daq/<int:daq_num>/power")
def api_daq_power(daq_num: int):
    if daq_num not in (1, 2):
        return jsonify(ok=False, error="daq_num must be 1 or 2"), 400
    daq = rt().daqs[daq_num - 1]
    daq.set_powered(bool(request.json.get("on")))
    return jsonify(ok=True)


@app.post("/api/daq/<int:daq_num>/online")
def api_daq_online(daq_num: int):
    if daq_num not in (1, 2):
        return jsonify(ok=False, error="daq_num must be 1 or 2"), 400
    daq = rt().daqs[daq_num - 1]
    daq.set_online(bool(request.json.get("on")))
    return jsonify(ok=True)


@app.post("/api/daq/<int:daq_num>/spike")
def api_daq_spike(daq_num: int):
    if daq_num not in (1, 2):
        return jsonify(ok=False, error="daq_num must be 1 or 2"), 400
    body = request.json or {}
    ch = int(body.get("channel", -1))
    ma = float(body.get("mA", 4.0))
    if not (0 <= ch <= 7):
        return jsonify(ok=False, error="channel must be 0-7"), 400
    rt().daqs[daq_num - 1].force_leak(ch, ma)
    return jsonify(ok=True)


@app.post("/api/daq/<int:daq_num>/clear")
def api_daq_clear(daq_num: int):
    if daq_num not in (1, 2):
        return jsonify(ok=False, error="daq_num must be 1 or 2"), 400
    body = request.json or {}
    ch = body.get("channel")
    rt().daqs[daq_num - 1].clear_force(int(ch) if ch is not None else None)
    return jsonify(ok=True)


def main() -> None:
    global _rt
    ap = argparse.ArgumentParser(description="Kitchen simulator — web GUI")
    ap.add_argument("--host", default="localhost", help="MQTT broker host")
    ap.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--device-id", default="KITCHEN-01", help="must match webapp KITCHEN_DEVICE_ID")
    ap.add_argument("--experiment-name", default="KitchenLeaks")
    ap.add_argument("--lab-id", default="lab5")
    ap.add_argument("--daq1-id", default="KITCHEN-DAQ-1")
    ap.add_argument("--daq2-id", default="KITCHEN-DAQ-2")
    ap.add_argument("--gui-host", default="127.0.0.1")
    ap.add_argument("--gui-port", type=int, default=5050)
    args = ap.parse_args()

    _rt = SimRuntime(
        host=args.host, port=args.port, user=args.user, password=args.password,
        device_id=args.device_id, experiment_name=args.experiment_name, lab_id=args.lab_id,
        daq1_id=args.daq1_id, daq2_id=args.daq2_id,
    )

    print(f"[GUI] Kitchen sim device_id={args.device_id}, DAQ-1={args.daq1_id}, DAQ-2={args.daq2_id}")
    print(f"[GUI] MQTT broker {args.host}:{args.port}")
    print(f"[GUI] open http://{args.gui_host}:{args.gui_port}")

    try:
        app.run(host=args.gui_host, port=args.gui_port, debug=False, use_reloader=False)
    finally:
        if _rt:
            _rt.shutdown()


if __name__ == "__main__":
    main()
