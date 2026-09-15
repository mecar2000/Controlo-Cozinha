"""
app.state — shared mutable state + locks.

Holds everything the frontend needs to poll or that commands.py needs to
gate on, updated only from app.mqtt's callbacks. No module here talks to
MQTT or SQL directly — this is memory only, so reads are cheap and never
block on the network.
"""

import threading
import time
from typing import Optional

_lock = threading.Lock()

# --- Connection health, per the design spec's "Availability" table ---
_mqtt_connected = False
_daq_reachable: Optional[bool] = None   # None = not checked yet
_mqtt_last_message_at: Optional[float] = None

# --- Retained topics, mirrored in memory as they arrive ---
_kitchen_state: dict = {}       # KitchenControl/{deviceId}/state
_kitchen_state_received_at: Optional[float] = None
_last_ack: dict = {}            # KitchenControl/{deviceId}/ack
_last_config_ack: dict = {}     # KitchenControl/{deviceId}/config/ack
_permit_ok: Optional[bool] = None
_permit_last_seen_at: Optional[float] = None
# Peer alarms, tracked PER ZONE keyed by "experiment/device/lab". A flat
# boolean here had the same bug the firmware's PeerAlarmTable fixes: one zone
# publishing danger:false cleared another zone's still-active alarm. Display
# side only (the firmware owns the interlock), but it must not tell an operator
# the lab is clear while a peer is still alarming.
# {zone_key: {"active": bool, "detail": dict, "received_at": float}}
_peer_alarms: dict[str, dict] = {}

# --- Live sensor readings, keyed by sensor identity as published on MQTT ---
# {sensor_key: {"value": float, "unit": str, "ts_ms": int, "received_at": float}}
_live_readings: dict[str, dict] = {}

# --- Two-phase start bookkeeping (commands.py is the only writer) ---
_pending_run_id: Optional[str] = None
_pending_started_at: Optional[float] = None

# --- Display mode (server-side; disables Start regardless of client) ---
# Held as a lease renewed by heartbeat — see set_display_mode below.
_display_mode = False
_display_mode_renewed_at: Optional[float] = None


def set_mqtt_connected(v: bool) -> None:
    global _mqtt_connected
    with _lock:
        _mqtt_connected = v


def is_mqtt_connected() -> bool:
    with _lock:
        return _mqtt_connected


def touch_mqtt_message() -> None:
    global _mqtt_last_message_at
    with _lock:
        _mqtt_last_message_at = time.time()


def mqtt_last_message_age_s() -> Optional[float]:
    with _lock:
        if _mqtt_last_message_at is None:
            return None
        return time.time() - _mqtt_last_message_at


def set_daq_reachable(v: bool) -> None:
    global _daq_reachable
    with _lock:
        _daq_reachable = v


def is_daq_reachable() -> Optional[bool]:
    with _lock:
        return _daq_reachable


def set_kitchen_state(payload: dict) -> None:
    global _kitchen_state, _kitchen_state_received_at
    with _lock:
        _kitchen_state = dict(payload)
        _kitchen_state_received_at = time.time()


def get_kitchen_state() -> dict:
    with _lock:
        return dict(_kitchen_state)


def kitchen_state_age_s() -> Optional[float]:
    """Seconds since the retained state payload last ARRIVED — not since it
    was last fetched. elapsedMs/clearForMs are frozen between the publisher's
    heartbeats (sim/runtime.py's STATE_HEARTBEAT_MS, kitchen.ino's
    STATE_HEARTBEAT_MS), so a client interpolating them locally must advance
    from when the value was generated. Anchoring to fetch time instead makes
    the clock creep up and snap back on every heartbeat.

    Deliberately an AGE, not an absolute timestamp: the browser compares it
    against its own Date.now(), so no client/server clock offset enters the
    arithmetic."""
    with _lock:
        if _kitchen_state_received_at is None:
            return None
        return time.time() - _kitchen_state_received_at


def set_last_ack(payload: dict) -> None:
    global _last_ack
    with _lock:
        _last_ack = dict(payload)


def get_last_ack() -> dict:
    with _lock:
        return dict(_last_ack)


def set_last_config_ack(payload: dict) -> None:
    global _last_config_ack
    with _lock:
        _last_config_ack = dict(payload)


def get_last_config_ack() -> dict:
    with _lock:
        return dict(_last_config_ack)


def set_permit(ok: bool) -> None:
    global _permit_ok, _permit_last_seen_at
    with _lock:
        _permit_ok = ok
        _permit_last_seen_at = time.time()


def get_permit_status() -> dict:
    """Absence is distinct from denial — see the firmware's own rule that
    permit absence warns and never trips outside a run."""
    with _lock:
        return {
            "ok": _permit_ok,
            "last_seen_age_s": (
                None if _permit_last_seen_at is None else time.time() - _permit_last_seen_at
            ),
        }


def set_peer_alarm(active: bool, detail: Optional[dict] = None) -> None:
    """Record one peer zone's alarm state. The zone is keyed from the detail
    dict's experiment/device/lab (mqtt.py parses these out of the topic), so
    zones are tracked independently: a clear from one zone never cancels
    another's alarm. A zone stays active until THAT zone reports clear.

    A call with no detail cannot be attributed to a zone; it is treated as a
    global reset (used by the test fixtures) rather than guessed at."""
    global _peer_alarms
    with _lock:
        if detail is None:
            _peer_alarms = {}
            return
        key = "/".join((
            str(detail.get("experiment_name", "?")),
            str(detail.get("device_id", "?")),
            str(detail.get("lab_id", "?")),
        ))
        if active:
            _peer_alarms[key] = {
                "active": True,
                "detail": detail,
                "received_at": time.time(),
            }
        else:
            # Only this zone stands down. Keep the row so the UI can show a
            # recently-cleared zone; anyActive is computed across rows.
            _peer_alarms[key] = {
                "active": False,
                "detail": detail,
                "received_at": time.time(),
            }


def get_peer_alarm() -> dict:
    """`active` is the OR across zones — the shape routes/frontend already
    consume. `detail` is one active zone (arbitrary but stable) so the existing
    single-zone banner keeps working; `zones` carries the full picture."""
    with _lock:
        active_zones = [z for z in _peer_alarms.values() if z["active"]]
        return {
            "active": bool(active_zones),
            "detail": active_zones[0]["detail"] if active_zones else None,
            "active_count": len(active_zones),
            "zones": {k: dict(v) for k, v in _peer_alarms.items()},
        }


def set_live_reading(sensor_key: str, value: float, unit: str, ts_ms: int,
                     converted: bool = True, raw_value: Optional[float] = None,
                     raw_unit: Optional[str] = None) -> None:
    """Store one sensor's latest value.

    `converted` is False when no DataAcquisition calibration could be applied
    and `value` is therefore a RAW hardware reading (mA), not a concentration.
    The frontend must not plot those on the %v/v heatmap — see mqtt.py's
    fallback and useKitchen.ts. Defaults True so non-current samples, which
    need no calibration, keep their existing behaviour.

    `raw_value`/`raw_unit` are the reading BEFORE any calibration was applied
    — kept alongside `value` (which IS converted when `converted` is True) so
    a feature like "zero in clean air" can average the true raw signal
    regardless of whether a calibration happens to be active right now.
    Default to `value`/`unit` when omitted, so a caller that has no
    conversion step at all (nothing currently calls it that way, but nothing
    should have to) still gets a sensible raw_value rather than None.
    """
    with _lock:
        _live_readings[sensor_key] = {
            "value": value,
            "unit": unit,
            "ts_ms": ts_ms,
            "converted": converted,
            "raw_value": value if raw_value is None else raw_value,
            "raw_unit": unit if raw_unit is None else raw_unit,
            "received_at": time.time(),
        }


def get_live_readings() -> dict:
    with _lock:
        return {k: dict(v) for k, v in _live_readings.items()}


def set_pending_run(run_id: Optional[str]) -> None:
    global _pending_run_id, _pending_started_at
    with _lock:
        _pending_run_id = run_id
        _pending_started_at = time.time() if run_id else None


def get_pending_run() -> Optional[str]:
    with _lock:
        return _pending_run_id


def pending_run_age_s() -> Optional[float]:
    with _lock:
        if _pending_started_at is None:
            return None
        return time.time() - _pending_started_at


# Display mode is a LEASE, not a flag.
#
# A wall display holds it by heartbeat. If that screen is closed, crashes, or
# loses the network, the lease simply expires and the next operator on a
# control screen can start a run again. A plain boolean would stay stuck on
# forever, because the one request that clears it is exactly the request a
# dying tab never gets to make.
DISPLAY_MODE_LEASE_S = 30.0


def set_display_mode(v: bool) -> None:
    """Take or release the display-mode lease.

    Taking it also renews it, so the frontend's heartbeat is just a repeated
    call with enabled=True.
    """
    global _display_mode, _display_mode_renewed_at
    with _lock:
        _display_mode = v
        _display_mode_renewed_at = time.time() if v else None


def is_display_mode() -> bool:
    """True only while an unexpired lease is held."""
    global _display_mode, _display_mode_renewed_at
    with _lock:
        if not _display_mode:
            return False
        if _display_mode_renewed_at is None:
            return False
        if time.time() - _display_mode_renewed_at > DISPLAY_MODE_LEASE_S:
            # Expired: the holder went away without releasing it.
            _display_mode = False
            _display_mode_renewed_at = None
            return False
        return True
