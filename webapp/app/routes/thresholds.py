"""
routes.thresholds — publishes the per-sensor danger-threshold table over
config/set (kitchen/Protocol.h). Completes an already-designed path: the
firmware fully implements config/set (parse, clamp, ack with effectivePct —
kitchen/Protocol.cpp), but until this route existed nothing in this app ever
published it, so every sensor sat at the compiled default
(SENSOR_THRESHOLD_DEFAULT_COUNTS).

Sensor identity here is the firmware's own KITCHEN_LOCAL_SENSOR_PINS index
(0-5), not sensor_key — see _sensor_index below for the one place that
mapping is encoded.

A sensor's firmware_index (sensor_config column) is the primary source: it
can be set once, when a newly-defined sensor is wired to a real firmware
channel, with no naming requirement at all (problems.txt: sensors "shouldnt
be predefined ... [with] some predefined sensors like there are" — the H2-N
naming lock was one of those fixed assumptions). The H2-N regex below is
kept ONLY as a fallback for rows that predate that column.
"""

import re

from flask import Blueprint, jsonify, request

import app.commands as commands
import app.db as db
from app.auth import require_auth
from app.commands import CommandError

bp = Blueprint("thresholds", __name__)

# kitchen/Kitchen_Settings.h: KITCHEN_LOCAL_SENSOR_NAMES is "H2-1".."H2-6", in
# the same order as KITCHEN_LOCAL_SENSOR_PINS — so "H2-N"'s firmware index is
# N-1. Fallback only — see the module docstring; firmware_index is preferred
# whenever a sensor has one set.
_H2_NAME_RE = re.compile(r"^H2-(\d+)$")
_MAX_SENSOR_INDEX = 5  # KITCHEN_WIRED_LOCAL_SENSORS - 1


def _sensor_index(sensor: dict) -> "int | None":
    explicit = sensor.get("firmware_index")
    if explicit is not None:
        try:
            explicit = int(explicit)
        except (TypeError, ValueError):
            return None
        return explicit if 0 <= explicit <= _MAX_SENSOR_INDEX else None

    m = _H2_NAME_RE.match(sensor.get("daq_sensor_name") or "")
    if not m:
        return None
    idx = int(m.group(1)) - 1
    return idx if 0 <= idx <= _MAX_SENSOR_INDEX else None


@bp.put("/api/thresholds")
@require_auth
def set_thresholds():
    """
    Body: {"thresholds": [{"sensor_key": "...", "thresholdPct": float}, ...]}
    `sensor_key` is this app's sensor_config key; resolved here to the
    firmware's numeric index via daq_sensor_name before publishing, so the
    frontend never needs to know the firmware's index scheme.
    """
    body = request.get_json(force=True, silent=True) or {}
    requested = body.get("thresholds")
    if not isinstance(requested, list) or not requested:
        return jsonify({"error": "thresholds must be a non-empty array"}), 400

    entries = []
    for e in requested:
        if not isinstance(e, dict):
            return jsonify({"error": "each threshold entry must be an object"}), 400
        sensor_key = e.get("sensor_key")
        sensor = db.get_sensor(sensor_key) if sensor_key else None
        if not sensor:
            return jsonify({"error": f"unknown sensor_key: {sensor_key!r}"}), 400
        idx = _sensor_index(sensor)
        if idx is None:
            return jsonify({
                "error": f"sensor {sensor_key!r} has no firmware threshold index — "
                          "set firmware_index (0-5) on it, or name its daq_sensor_name H2-1..H2-6"
            }), 400
        try:
            pct = float(e["thresholdPct"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": f"{sensor_key!r}: thresholdPct must be a number"}), 400
        if pct < 0.0:
            return jsonify({"error": f"{sensor_key!r}: thresholdPct must be >= 0"}), 400
        entries.append({"sensor": idx, "thresholdPct": pct})

    try:
        commands.set_thresholds(entries)
    except CommandError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"sent": entries})
