"""
routes.daq_proxy — the only place the frontend reaches DataAcquisition,
through this backend's own token, per 'the browser never talks to
DataAcquisition directly' (design spec, 'Auth'). Everything here is a
read-only pass-through used by pickers, replay, and calibration display.
"""

from flask import Blueprint, jsonify, request

import app.daq as daq
from app.auth import require_auth
from app.daq import DaqRejected, DaqUnreachable

bp = Blueprint("daq_proxy", __name__)

# Query parameters this proxy will forward to DataAcquisition. Anything else a
# client sends is dropped: forwarding request.args wholesale would pass along
# our own `token`, plus any parameter DAQ happens to honour that this app
# never meant to expose.
_HISTORY_PASSTHROUGH = frozenset({
    "device_id", "sensor", "sensor_name", "location", "stage",
    "max_points", "downsample", "limit",
})


def _passthrough(*exclude: str) -> dict:
    """The allowlisted query params from this request, minus the ones the
    caller handles explicitly."""
    excluded = set(exclude)
    return {
        k: v for k, v in request.args.items()
        if k in _HISTORY_PASSTHROUGH and k not in excluded
    }


def _wrap(fn, *args, **kwargs):
    try:
        return jsonify(fn(*args, **kwargs))
    except DaqRejected as exc:
        # DAQ understood and refused: pass its own status through rather than
        # reporting a transport failure the operator can't act on.
        return jsonify({"error": str(exc)}), exc.status_code
    except DaqUnreachable as exc:
        return jsonify({"error": str(exc)}), 502


@bp.get("/api/daq/experiments")
@require_auth
def list_experiments():
    return _wrap(daq.list_experiments)


@bp.post("/api/daq/experiments")
@require_auth
def create_experiment():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name")
    if not name:
        return jsonify({"error": "name is required"}), 400
    return _wrap(daq.create_experiment, name)


@bp.get("/api/daq/experiments/<int:experiment_id>/stages")
@require_auth
def list_stages(experiment_id: int):
    return _wrap(daq.list_stages, experiment_id)


@bp.get("/api/daq/history/experiment")
@require_auth
def history_experiment():
    experiment_id = request.args.get("experiment_id", type=int)
    if experiment_id is None:
        return jsonify({"error": "experiment_id is required"}), 400
    return _wrap(daq.get_history_experiment, experiment_id,
                 **_passthrough("experiment_id"))


@bp.get("/api/daq/history/experiment/window")
@require_auth
def history_window():
    experiment_id = request.args.get("experiment_id", type=int)
    start_ms = request.args.get("start_ms", type=int)
    end_ms = request.args.get("end_ms", type=int)
    if experiment_id is None or start_ms is None or end_ms is None:
        return jsonify({"error": "experiment_id, start_ms and end_ms are required"}), 400
    if end_ms < start_ms:
        return jsonify({"error": "end_ms must be >= start_ms"}), 400
    return _wrap(daq.get_history_window, experiment_id, start_ms, end_ms,
                 **_passthrough("experiment_id", "start_ms", "end_ms"))


@bp.get("/api/daq/history/experiment/stage")
@require_auth
def history_stage():
    experiment_id = request.args.get("experiment_id", type=int)
    if experiment_id is None:
        return jsonify({"error": "experiment_id is required"}), 400
    return _wrap(daq.get_history_experiment_stage, experiment_id)


@bp.get("/api/daq/conversions/<device_id>")
@require_auth
def conversions(device_id: str):
    """Calibration provenance for a device's sensors, keyed by sensor_name.
    DataAcquisition exposes conversions only per-device (its per-sensor path is
    POST/DELETE), so this proxies the whole table."""
    return _wrap(daq.get_conversions, device_id)
