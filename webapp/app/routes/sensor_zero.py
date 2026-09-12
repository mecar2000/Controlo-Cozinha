"""
routes.sensor_zero — "Zero in clean air": the HTTP surface over app.zeroing.

    POST   /api/sensors/<key>/zero/start    begin a capture (?target_samples=)
    GET    /api/sensors/<key>/zero/status   progress, also pulls in new samples
    POST   /api/sensors/<key>/zero/apply    average + write raw_min, ends session
    POST   /api/sensors/<key>/zero/cancel   abandon the session
    PUT    /api/sensors/<key>/zero/manual   {"raw_min": float} — direct override
"""

from flask import Blueprint, jsonify, request

import app.zeroing as zeroing
from app.auth import require_auth
from app.zeroing import ZeroingError

bp = Blueprint("sensor_zero", __name__)


@bp.post("/api/sensors/<sensor_key>/zero/start")
@require_auth
def start(sensor_key: str):
    target = request.args.get("target_samples", default=zeroing.DEFAULT_TARGET_SAMPLES, type=int)
    if target is None or target < 1:
        return jsonify({"error": "target_samples must be >= 1"}), 400
    try:
        return jsonify(zeroing.start(sensor_key, target_samples=target))
    except ZeroingError as exc:
        msg = str(exc)
        status_code = 404 if "not found" in msg else 409
        return jsonify({"error": msg}), status_code


@bp.get("/api/sensors/<sensor_key>/zero/status")
@require_auth
def status(sensor_key: str):
    try:
        return jsonify(zeroing.status())
    except ZeroingError as exc:
        return jsonify({"error": str(exc)}), 404


@bp.post("/api/sensors/<sensor_key>/zero/apply")
@require_auth
def apply(sensor_key: str):
    try:
        return jsonify(zeroing.apply())
    except ZeroingError as exc:
        msg = str(exc)
        status_code = 404 if "no active" in msg else 409
        return jsonify({"error": msg}), status_code


@bp.post("/api/sensors/<sensor_key>/zero/cancel")
@require_auth
def cancel(sensor_key: str):
    zeroing.cancel()
    return jsonify({"ok": True})


@bp.put("/api/sensors/<sensor_key>/zero/manual")
@require_auth
def manual(sensor_key: str):
    body = request.get_json(force=True, silent=True) or {}
    if "raw_min" not in body:
        return jsonify({"error": "raw_min is required"}), 400
    try:
        raw_min = float(body["raw_min"])
    except (TypeError, ValueError):
        return jsonify({"error": "raw_min must be a number"}), 400
    try:
        return jsonify(zeroing.set_raw_min_manually(sensor_key, raw_min))
    except ZeroingError as exc:
        return jsonify({"error": str(exc)}), 404
