"""routes.status — connection health, retained state, display mode. Backs the
header's permit/daq/mqtt dots and the Control view's numeric rail."""

from flask import Blueprint, jsonify, request

import app.phase_watcher as phase_watcher
import app.state as state
from app.auth import require_auth

bp = Blueprint("status", __name__)


@bp.get("/api/status")
@require_auth
def get_status():
    # One lock acquisition for every state-derived field, so an MQTT callback
    # can't land mid-response and mix a phase from before it with a permit
    # from after. See state.snapshot().
    snap = state.snapshot()
    mqtt_age = snap["mqtt"]["last_message_age_s"]
    # Never show last-known values as current when stale — the frontend
    # should treat the live view as stale past this age.
    snap["mqtt"]["stale"] = mqtt_age is None or mqtt_age > 15
    # Separate module, separate lock — not part of the atomic state snapshot.
    snap["daq"]["recording_lost"] = phase_watcher.is_recording_lost()
    return jsonify(snap)


@bp.get("/api/live-readings")
@require_auth
def get_live_readings():
    return jsonify(state.get_live_readings())


@bp.post("/api/display-mode")
@require_auth
def set_display_mode():
    """Server-side switch, per screen. Start is refused server-side when this
    is on; stop and ack remain available regardless (design spec, 'Display').

    Held as a LEASE: enabled=True both takes and renews it, and the display
    screen renews on a heartbeat. A screen that is closed or loses the network
    lets the lease expire rather than leaving Start disabled forever — the
    release request is exactly the one a dying tab never sends.
    """
    body = request.get_json(force=True, silent=True) or {}
    enabled = bool(body.get("enabled"))
    state.set_display_mode(enabled)
    return jsonify({
        "display_mode": enabled,
        "lease_s": state.DISPLAY_MODE_LEASE_S,
    })
