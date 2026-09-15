"""routes.sensors — sensor_config CRUD (numeric X/Y/Z entry, per the design
spec's 'Room and sensors': positions come off a tape measure, not
drag-and-drop) and layout-snapshot lookup for replay.

DELETE is a plain hard delete: a removed sensor's row is gone immediately.
A past run's layout_snapshots row is a JSON copy taken at run start, not a
foreign key to sensor_config, so history of a past run's layout survives the
row's removal regardless — only replay's no-snapshot fallback (today's live
layout) would stop listing a deleted sensor.
"""

from flask import Blueprint, jsonify, request

import app.daq as daq
import app.db as db
import app.layout as layout
from app.auth import require_auth

bp = Blueprint("sensors", __name__)


def _derive_daq_sensor_name(device_id: str, pin: int) -> str:
    """Look up the friendly name DataAcquisition currently reports for
    (device_id, pin), rather than trusting a typed daq_sensor_name.

    This is where the design's silent-mismatch trap gets structurally
    closed (Part 5): DataAcquisition shows friendly names on its own cards,
    but the raw MQTT topic this webapp subscribes to carries the PIN LABEL,
    not the friendly name. A hand-typed daq_sensor_name could name a sensor
    "H2-7" while the device actually publishes on a pin DAQ calls "A1" —
    DAQ looks correct while the kitchen room view silently shows nothing.
    Deriving the name from DAQ's own current pin map makes that mismatch
    impossible to create through this form.
    """
    for device in daq.list_devices():
        if device.get("device_id") != device_id:
            continue
        for sensor in device.get("config", {}).get("sensors", []):
            if sensor.get("pin") == pin:
                return sensor.get("name")
    return None


@bp.get("/api/sensors")
@require_auth
def list_sensors():
    enabled_only = request.args.get("enabled_only") == "1"
    return jsonify(db.list_sensors(enabled_only=enabled_only))


@bp.put("/api/sensors/<sensor_key>")
@require_auth
def upsert_sensor(sensor_key: str):
    body = request.get_json(force=True, silent=True) or {}
    required = ("label", "x", "y", "z")
    if any(k not in body for k in required):
        return jsonify({"error": f"required fields: {', '.join(required)}"}), 400
    if not str(body["label"]).strip():
        return jsonify({"error": "label cannot be empty"}), 400

    coords = {}
    for axis in ("x", "y", "z"):
        try:
            coords[axis] = float(body[axis])
        except (TypeError, ValueError):
            # A tape-measure typo should read as a 400, not a 500.
            return jsonify({"error": f"{axis} must be a number"}), 400
        if coords[axis] != coords[axis] or coords[axis] in (float("inf"), float("-inf")):
            return jsonify({"error": f"{axis} must be a finite number"}), 400

    label = str(body["label"]).strip()
    # Duplicate (Part 6) prefills label+position from an existing sensor —
    # a same-model sensor placed nearby is the typical case, so only an
    # EXACT match is refused; a small nudge on any axis is allowed through.
    # The sensor being edited itself never collides with itself. Label match
    # is case-insensitive since two sensors named "Above the stove" /
    # "ABOVE THE STOVE" would be just as confusing in the sensor list as an
    # exact match.
    others = [s for s in db.list_sensors() if s["sensor_key"] != sensor_key]
    if any(s["label"].strip().lower() == label.lower() for s in others):
        return jsonify({"error": f"a sensor is already labelled {label!r}"}), 409
    if any(s["x"] == coords["x"] and s["y"] == coords["y"] and s["z"] == coords["z"] for s in others):
        return jsonify({
            "error": f"a sensor already exists at position {coords['x']}, {coords['y']}, {coords['z']}"
        }), 409

    daq_device_id = body.get("daq_device_id")
    daq_pin = body.get("daq_pin")
    daq_sensor_name = None
    if daq_pin is not None:
        if isinstance(daq_pin, bool) or not isinstance(daq_pin, int):
            return jsonify({"error": "daq_pin must be an integer"}), 400
        if not daq_device_id:
            return jsonify({"error": "daq_device_id is required when daq_pin is set"}), 400
        try:
            daq_sensor_name = _derive_daq_sensor_name(daq_device_id, daq_pin)
        except daq.DaqError as exc:
            return jsonify({"error": f"could not reach DataAcquisition to resolve daq_pin: {exc}"}), 502
        if daq_sensor_name is None:
            return jsonify({
                "error": f"device {daq_device_id!r} does not currently report pin "
                          f"{daq.pin_label(daq_pin)} — it must be configured in DataAcquisition first"
            }), 400

    sensor = db.upsert_sensor(
        sensor_key,
        label=str(body["label"]),
        x=coords["x"],
        y=coords["y"],
        z=coords["z"],
        enabled=bool(body.get("enabled", True)),
        daq_device_id=daq_device_id,
        daq_sensor_name=daq_sensor_name,
        daq_pin=daq_pin,
    )
    return jsonify(sensor)


@bp.delete("/api/sensors/<sensor_key>")
@require_auth
def delete_sensor(sensor_key: str):
    if db.get_sensor(sensor_key) is None:
        return jsonify({"error": "not found"}), 404
    db.delete_sensor(sensor_key)
    return jsonify({"deleted": True})


# KITCHEN_WIRED_LOCAL_SENSORS - 1 — mirrors routes/thresholds.py's own bound.
_MAX_FIRMWARE_INDEX = 5


@bp.put("/api/sensors/<sensor_key>/firmware-index")
@require_auth
def set_firmware_index(sensor_key: str):
    """Which firmware channel (0-5) this sensor's danger threshold goes to
    (routes/thresholds.py) — set once, with no naming requirement, so a
    newly defined sensor is not locked to being named "H2-N" to receive a
    threshold (problems.txt)."""
    if db.get_sensor(sensor_key) is None:
        return jsonify({"error": "not found"}), 404

    body = request.get_json(force=True, silent=True) or {}
    if "firmware_index" not in body:
        return jsonify({"error": "firmware_index is required (an integer 0-5, or null to clear)"}), 400

    value = body["firmware_index"]
    if value is not None:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return jsonify({"error": "firmware_index must be an integer or null"}), 400
        if not (0 <= value <= _MAX_FIRMWARE_INDEX):
            return jsonify({"error": f"firmware_index must be 0-{_MAX_FIRMWARE_INDEX}"}), 400

    db.set_firmware_index(sensor_key, value)
    return jsonify({"sensor_key": sensor_key, "firmware_index": value})


@bp.get("/api/runs/<int:run_id>/layout")
@require_auth
def layout_for_run(run_id: int):
    return jsonify(layout.layout_for_replay(run_id))
