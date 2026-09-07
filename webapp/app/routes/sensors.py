"""routes.sensors — sensor_config CRUD (numeric X/Y/Z entry, per the design
spec's 'Room and sensors': positions come off a tape measure, not
drag-and-drop) and layout-snapshot lookup for replay."""

from flask import Blueprint, jsonify, request

import app.db as db
import app.layout as layout
from app.auth import require_auth

bp = Blueprint("sensors", __name__)


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
    db.delete_sensor(sensor_key)
    return jsonify({"deleted": True})


@bp.get("/api/runs/<int:run_id>/layout")
@require_auth
def layout_for_run(run_id: int):
    return jsonify(layout.layout_for_replay(run_id))
