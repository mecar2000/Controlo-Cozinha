"""routes.sensors — sensor_config CRUD (numeric X/Y/Z entry, per the design
spec's 'Room and sensors': positions come off a tape measure, not
drag-and-drop) and layout-snapshot lookup for replay.

DELETE is soft (archive): a removed sensor stays in the table and stays
joinable by historical runs/layout snapshots that reference its sensor_key,
rather than vanishing (problems.txt: "there should be memory of previous
ones"). See app.db.sensor_config's archive_sensor/restore_sensor.
"""

from flask import Blueprint, jsonify, request

import app.db as db
import app.layout as layout
from app.auth import require_auth

bp = Blueprint("sensors", __name__)


@bp.get("/api/sensors")
@require_auth
def list_sensors():
    enabled_only = request.args.get("enabled_only") == "1"
    include_archived = request.args.get("include_archived") == "1"
    return jsonify(db.list_sensors(enabled_only=enabled_only, include_archived=include_archived))


@bp.put("/api/sensors/<sensor_key>")
@require_auth
def upsert_sensor(sensor_key: str):
    existing = db.get_sensor(sensor_key)
    if existing is not None and existing["archived"]:
        return jsonify({
            "error": f"sensor_key {sensor_key!r} belongs to an archived sensor — "
                      "restore it first (POST .../restore), or pick a different key"
        }), 409

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

    sensor = db.upsert_sensor(
        sensor_key,
        label=str(body["label"]),
        x=coords["x"],
        y=coords["y"],
        z=coords["z"],
        enabled=bool(body.get("enabled", True)),
        daq_device_id=body.get("daq_device_id"),
        daq_sensor_name=body.get("daq_sensor_name"),
    )
    return jsonify(sensor)


@bp.delete("/api/sensors/<sensor_key>")
@require_auth
def delete_sensor(sensor_key: str):
    if db.get_sensor(sensor_key) is None:
        return jsonify({"error": "not found"}), 404
    db.archive_sensor(sensor_key)
    return jsonify({"archived": True})


@bp.post("/api/sensors/<sensor_key>/restore")
@require_auth
def restore_sensor(sensor_key: str):
    if db.get_sensor(sensor_key) is None:
        return jsonify({"error": "not found"}), 404
    db.restore_sensor(sensor_key)
    return jsonify({"archived": False})


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
