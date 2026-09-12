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
    mqtt_age = state.mqtt_last_message_age_s()
    return jsonify({
        "mqtt": {
            "connected": state.is_mqtt_connected(),
            "last_message_age_s": mqtt_age,
            # Never show last-known values as current when stale — the
            # frontend should treat the live view as stale past this age.
            "stale": mqtt_age is None or mqtt_age > 15,
        },
        "daq": {
            "reachable": state.is_daq_reachable(),
            "recording_lost": phase_watcher.is_recording_lost(),
        },
        "permit": state.get_permit_status(),
        "peer_alarm": state.get_peer_alarm(),
        "kitchen_state": state.get_kitchen_state(),
        "display_mode": state.is_display_mode(),
        # The firmware's honest echo of the last config/set it accepted: per
        # sensor, requestedPct vs effectivePct/effectiveCounts and whether it
        # was clamped. Surfaced here so the operator can see when a requested
        # threshold was NOT what actually took effect — see
        # routes/thresholds.py and kitchen/Protocol.cpp protocolBuildConfigAck.
        "config_ack": state.get_last_config_ack(),
    })


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
