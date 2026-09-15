"""
routes.daq_proxy — the only place the frontend reaches DataAcquisition,
through this backend's own token, per 'the browser never talks to
DataAcquisition directly' (design spec, 'Auth').

Mostly a read-only pass-through used by pickers, replay, and calibration
display — with one deliberate exception: the conversion (calibration)
endpoints also proxy WRITES. DataAcquisition still owns the calibration
store (one `conv_id` provenance chain, shared by this app's live view and
DAQ's own historian), so a write here is never a second copy — it is this
app's calibration editor reaching the same row DAQ's own dashboard would
edit. See app.daq.set_conversion/delete_conversion.
"""

from flask import Blueprint, jsonify, request

import app.daq as daq
import app.mqtt as mqtt_mod
from app.auth import require_auth
from app.conversion import _SUPPORTED_METHODS
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
    """Readings within ONE named stage of an experiment — not the list of
    stages. For that, see GET /api/daq/experiments/<id>/stages below."""
    experiment_id = request.args.get("experiment_id", type=int)
    stage = request.args.get("stage")
    if experiment_id is None:
        return jsonify({"error": "experiment_id is required"}), 400
    if not stage:
        return jsonify({"error": "stage is required"}), 400
    return _wrap(daq.get_history_experiment_stage, experiment_id, stage)


@bp.get("/api/daq/devices")
@require_auth
def devices():
    """Every device DataAcquisition has seen, with its capabilities and
    current pin configuration — read-only viewer (Part 5, Stage 1). See
    app.daq.list_devices."""
    return _wrap(daq.list_devices)


@bp.post("/api/daq/devices/<device_id>/config")
@require_auth
def push_device_config(device_id: str):
    """Replace a device's whole pin map (Part 5, Stage 2) — see
    app.daq.push_config for the full-replace semantics this proxies. Validated
    here before it ever reaches DataAcquisition, the same way set_conversion
    validates below: a 400 with a clear message beats DAQ's own error being
    the operator's first sign something was wrong.
    """
    body = request.get_json(force=True, silent=True) or {}
    sensors = body.get("sensors")
    if not sensors or not isinstance(sensors, list):
        return jsonify({"error": "sensors must be a non-empty list"}), 400

    pins = []
    for entry in sensors:
        if not isinstance(entry, dict) or "pin" not in entry:
            return jsonify({"error": "each sensor must be an object with a pin"}), 400
        pin = entry["pin"]
        if isinstance(pin, bool) or not isinstance(pin, int):
            return jsonify({"error": f"pin must be an integer, got {pin!r}"}), 400
        pins.append(pin)
    if len(pins) != len(set(pins)):
        return jsonify({"error": "duplicate pin in sensors list"}), 400

    interval_ms = body.get("interval_ms")
    if interval_ms is not None and not isinstance(interval_ms, int):
        return jsonify({"error": "interval_ms must be an integer"}), 400

    return _wrap(
        daq.push_config, device_id, sensors,
        interval_ms=interval_ms, location=body.get("location"),
    )


@bp.delete("/api/daq/devices/<device_id>")
@require_auth
def delete_device(device_id: str):
    """Permanently remove a device from DataAcquisition — see app.daq.
    delete_device for what this does and does not affect. The frontend is
    responsible for warning the operator when sensors are still bound to
    this device before calling this (nothing here refuses on their behalf,
    matching push_config's own no-cascade behaviour when a single pin is
    dropped)."""
    return _wrap(daq.delete_device, device_id)


@bp.get("/api/daq/conversions/<device_id>")
@require_auth
def conversions(device_id: str):
    """Calibration provenance for a device's sensors, keyed by sensor_name.
    DataAcquisition exposes conversions only per-device (its per-sensor path is
    POST/DELETE), so this proxies the whole table."""
    return _wrap(daq.get_conversions, device_id)


@bp.put("/api/daq/conversions/<device_id>/<sensor_name>")
@require_auth
def set_conversion(device_id: str, sensor_name: str):
    """Create or update one sensor's calibration — the webapp-side editor
    this app was missing; the write itself lands in DataAcquisition's store
    (see the module docstring) so recalibrating here and recalibrating in
    DAQ's own dashboard are the same action.

    `method` "custom" is rejected here, before it ever reaches DAQ: this
    app's applier (app.conversion) does not evaluate arbitrary formulas on a
    safety display, so a stored "custom" calibration would silently read
    back as unconverted. Better to refuse the write than accept one that
    can never take effect here.
    """
    body = request.get_json(force=True, silent=True) or {}
    method = body.get("method")
    if not method:
        return jsonify({"error": "method is required"}), 400
    if method == "custom":
        return jsonify({
            "error": "method 'custom' is not supported here — this app's "
                      "live view cannot evaluate custom formulas; use "
                      "DataAcquisition's own dashboard for that sensor."
        }), 400
    if method not in _SUPPORTED_METHODS:
        return jsonify({"error": f"unsupported method: {method!r}"}), 400

    params = body.get("params")
    if not isinstance(params, dict):
        return jsonify({"error": "params must be an object"}), 400
    unit_symbol = body.get("unit_symbol")
    if not unit_symbol:
        return jsonify({"error": "unit_symbol is required"}), 400

    result = _wrap(
        daq.set_conversion, device_id, sensor_name,
        type=body.get("type"), method=method, params=params, unit_symbol=unit_symbol,
    )
    # Only invalidate on success — _wrap already turned a DAQ failure into an
    # error response, and there is nothing to invalidate for a write that
    # never landed.
    if not isinstance(result, tuple):
        mqtt_mod.invalidate_conversions(device_id)
    return result


@bp.delete("/api/daq/conversions/<device_id>/<sensor_name>")
@require_auth
def delete_conversion(device_id: str, sensor_name: str):
    """Remove one sensor's calibration — reverts to raw passthrough in
    DataAcquisition, mirrored here as converted=False on the next sample."""
    result = _wrap(daq.delete_conversion, device_id, sensor_name)
    if not isinstance(result, tuple):
        mqtt_mod.invalidate_conversions(device_id)
    return result
