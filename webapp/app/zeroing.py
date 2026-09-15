"""
app.zeroing — "Zero in clean air": capture N raw samples of one sensor's live
reading, average them, and write the result as that sensor's calibration
raw_min via DataAcquisition's existing conversion store (app.daq).

Answers problems.txt's question directly: "Do wi have to measure it or cant
i just see it from when there is no hydrogen what's the baseline for each
sensor and get the adjustment from there?" — yes, this IS the adjustment.
app.conversion's `linear` method already takes an explicit raw_min
(conversion.py:108); capturing the no-hydrogen reading and storing it there
is the wiring-loss correction, no separate measuring rig or calibration
concept needed.

One session at a time, process-global (matches app.state's shape — this app
runs single-process, no per-worker split for this feature). While a session
is active, app.runs.start_run() refuses to start: the air must stay clean for
the whole capture, and a run beginning mid-capture would corrupt it.

"Zero all" (start_all/status_all/cancel_all) answers problems.txt's "30
sensors" scale problem: zeroing them one at a time, each requiring its own
Start/poll/Apply click, doesn't scale. It runs the exact same one-sensor
session engine sequentially across every sensor with a DAQ identity — there
is still only ever one _session active at a time, so nothing about the
single-sensor path (including the start_run() block above) needed to change.
"""

import statistics
import threading
import time
from typing import Optional

import app.daq as daq
import app.db as db
import app.state as state
from app.conversion import _RAW_SPAN_DEFAULTS, resolve_method

DEFAULT_TARGET_SAMPLES = 100

# A clean-air reading should be near-flat; a spread this wide means either a
# real leak is present or the sensor is too noisy to trust an average of —
# either way, refuse rather than silently bake noise into the calibration.
_MAX_PLAUSIBLE_SPREAD_MA = 1.0
_MAX_PLAUSIBLE_SPREAD_V = 1.0

_lock = threading.Lock()
_session: Optional[dict] = None

# "Zero all in clean air" — runs the same one-sensor-at-a-time session engine
# across every sensor with a DAQ identity, sequentially (there is still only
# ever one _session active, so start_run()'s "blocked while zeroing" check
# above needs no changes at all). _batch tracks the queue and each sensor's
# outcome so far; _session is always the one currently capturing.
_batch: Optional[dict] = None


class ZeroingError(Exception):
    """Raised for every rejected zeroing operation — the route layer turns
    this into a 4xx with the message as-is, so keep messages user-facing."""


def is_active() -> bool:
    with _lock:
        return _session is not None


def reset() -> None:
    """Test/startup hook — clears any session with no side effects (does NOT
    touch DataAcquisition). Not exposed over HTTP."""
    global _session, _batch
    with _lock:
        _session = None
        _batch = None


def _live_reading_key(sensor: dict) -> str:
    return f"{sensor['daq_device_id']}/{sensor['daq_sensor_name']}"


def start(sensor_key: str, target_samples: int = DEFAULT_TARGET_SAMPLES) -> dict:
    global _session
    sensor = db.get_sensor(sensor_key)
    if sensor is None:
        raise ZeroingError(f"sensor {sensor_key!r} not found")
    if not sensor.get("daq_device_id") or not sensor.get("daq_sensor_name"):
        raise ZeroingError(
            f"sensor {sensor_key!r} has no DAQ identity — nothing live to average yet"
        )
    with _lock:
        if _session is not None:
            raise ZeroingError(
                f"a zeroing session is already active for {_session['sensor_key']!r} — "
                "cancel it first"
            )
        _session = {
            "sensor_key": sensor_key,
            "device_id": sensor["daq_device_id"],
            "sensor_name": sensor["daq_sensor_name"],
            "target": target_samples,
            "samples": [],
            "last_ts_ms": None,
            "started_at": time.time(),
        }
        return _status_locked()


def _status_locked() -> dict:
    return {
        "sensor_key": _session["sensor_key"],
        "target": _session["target"],
        "collected": len(_session["samples"]),
        "done": len(_session["samples"]) >= _session["target"],
    }


def collect() -> dict:
    """Pull in any new raw reading published since the last call. Safe to
    call as often as convenient (e.g. on every status poll) — a reading with
    the same ts_ms as the last one seen is not re-counted, so polling faster
    than the sensor publishes never inflates the sample count."""
    with _lock:
        if _session is None:
            raise ZeroingError("no active zeroing session")
        if len(_session["samples"]) >= _session["target"]:
            return _status_locked()

        key = f"{_session['device_id']}/{_session['sensor_name']}"
        reading = state.get_live_readings().get(key)
        if reading is None:
            return _status_locked()

        ts_ms = reading.get("ts_ms")
        if ts_ms is not None and ts_ms == _session["last_ts_ms"]:
            return _status_locked()  # same sample as last time — not new

        raw = reading.get("raw_value")
        if raw is None:
            return _status_locked()

        _session["samples"].append(raw)
        _session["last_ts_ms"] = ts_ms
        _session["last_raw_unit"] = reading.get("raw_unit")
        return _status_locked()


def status() -> dict:
    collect()
    with _lock:
        if _session is None:
            raise ZeroingError("no active zeroing session")
        return _status_locked()


def cancel() -> None:
    global _session
    with _lock:
        _session = None


def _signal_type_for_unit(raw_unit: Optional[str]) -> str:
    return "current" if raw_unit == "mA" else "voltage"


def _apply_raw_min(sensor: dict, raw_min: float, signal_type: str, sample_count: int) -> dict:
    """Shared by apply() (averaged) and set_raw_min_manually() (typed in):
    fetch the sensor's current calibration, overwrite only raw_min, write it
    back. Every other field (raw_max, min_value, max_value, method,
    unit_symbol) is preserved untouched."""
    device_id = sensor["daq_device_id"]
    sensor_name = sensor["daq_sensor_name"]

    existing = {}
    try:
        existing = daq.get_conversions(device_id).get(sensor_name) or {}
    except Exception:
        existing = {}  # DAQ unreachable — proceed with signal defaults, same as conversion.py's own fallback

    default_min, default_max = _RAW_SPAN_DEFAULTS.get(signal_type, (0.0, 1.0))
    params = dict(existing.get("params") or {})
    method = resolve_method(existing) if existing else "linear"
    previous_raw_min = float(params.get("raw_min", default_min))
    params["raw_min"] = raw_min
    params.setdefault("raw_max", default_max)
    params.setdefault("min_value", 0.0)
    params.setdefault("max_value", 100.0)
    unit_symbol = existing.get("unit_symbol") or ("%v/v" if signal_type == "current" else "V")

    daq.set_conversion(
        device_id, sensor_name,
        type=existing.get("type"), method=method, params=params, unit_symbol=unit_symbol,
    )

    return {
        "sensor_key": sensor["sensor_key"],
        "previous_raw_min": previous_raw_min,
        "new_raw_min": raw_min,
        "sample_count": sample_count,
    }


def apply() -> dict:
    """Average the collected samples and write them as raw_min. Clears the
    session on success — the operation is one-shot, not a running baseline."""
    global _session
    collect()
    with _lock:
        if _session is None:
            raise ZeroingError("no active zeroing session")
        samples = _session["samples"]
        target = _session["target"]
        if len(samples) < target:
            raise ZeroingError(
                f"not enough samples yet ({len(samples)}/{target}) — "
                "keep the air clean and wait for the capture to finish"
            )
        spread = max(samples) - min(samples)
        raw_unit = _session.get("last_raw_unit")
        max_spread = _MAX_PLAUSIBLE_SPREAD_MA if raw_unit == "mA" else _MAX_PLAUSIBLE_SPREAD_V
        if spread > max_spread:
            raise ZeroingError(
                f"reading spread too wide ({spread:.3f} over {len(samples)} samples) — "
                "this doesn't look like clean, stable air; check for a leak or a noisy "
                "sensor before zeroing"
            )
        mean_raw = statistics.fmean(samples)
        sensor = db.get_sensor(_session["sensor_key"])
        signal_type = _signal_type_for_unit(raw_unit)
        sample_count = len(samples)
        _session = None  # clear before the DAQ call so a slow/failing write doesn't hold the lock's session open

    return _apply_raw_min(sensor, mean_raw, signal_type, sample_count)


def set_raw_min_manually(sensor_key: str, raw_min: float) -> dict:
    """The manual-override path the user asked to keep alongside the
    one-click capture — same underlying write, no session/averaging."""
    sensor = db.get_sensor(sensor_key)
    if sensor is None:
        raise ZeroingError(f"sensor {sensor_key!r} not found")
    if not sensor.get("daq_device_id") or not sensor.get("daq_sensor_name"):
        raise ZeroingError(f"sensor {sensor_key!r} has no DAQ identity")
    # No live reading needed for a manual value — signal type is inferred
    # from whatever's cached, defaulting to current (the common case: the
    # kitchen's own H2 sensors are all 4-20 mA).
    key = _live_reading_key(sensor)
    reading = state.get_live_readings().get(key)
    signal_type = _signal_type_for_unit(reading.get("raw_unit") if reading else "mA")
    return _apply_raw_min(sensor, raw_min, signal_type, sample_count=0)


def _zeroable_sensor_keys() -> list[str]:
    """Every sensor with a DAQ identity — the ones start() could ever accept.
    Order matches db.list_sensors (sensor_key), so a batch run is
    deterministic and resumable-looking across status polls."""
    return [
        s["sensor_key"] for s in db.list_sensors(enabled_only=False)
        if s.get("daq_device_id") and s.get("daq_sensor_name")
    ]


def start_all(target_samples: int = DEFAULT_TARGET_SAMPLES) -> dict:
    """Begin zeroing every sensor with a DAQ identity, one at a time. Skips
    sensors with no DAQ identity entirely (nothing live to average there) —
    it does not fail the whole batch for them."""
    global _batch
    with _lock:
        if _session is not None or _batch is not None:
            raise ZeroingError("a zeroing session is already active — cancel it first")
        keys = _zeroable_sensor_keys()
        if not keys:
            raise ZeroingError("no sensors have a DAQ identity to zero")
        _batch = {"keys": keys, "index": 0, "target": target_samples, "results": [], "failures": []}
    _start_current_locked_out(target_samples)
    return status_all()


def _start_current_locked_out(target_samples: int) -> None:
    """Start the session for _batch['keys'][_batch['index']], outside the
    lock (start() takes it itself). Records a failure and skips ahead rather
    than aborting the whole batch — one bad sensor (e.g. deleted mid-batch)
    shouldn't stop the other 29."""
    global _batch
    while True:
        with _lock:
            if _batch is None or _batch["index"] >= len(_batch["keys"]):
                return
            sensor_key = _batch["keys"][_batch["index"]]
        try:
            start(sensor_key, target_samples=target_samples)
            return
        except ZeroingError as exc:
            with _lock:
                if _batch is not None:
                    _batch["failures"].append({"sensor_key": sensor_key, "error": str(exc)})
                    _batch["index"] += 1


def status_all() -> dict:
    """Progress of the current batch. Auto-applies the current sensor once
    its capture is done and advances to the next — the caller just polls
    this, same shape as status() but for the whole batch.

    status()/collect()/apply() each take _lock themselves (it is a plain,
    non-reentrant threading.Lock), so this function must never call them
    while holding _lock itself — every _lock block below is closed before
    any of those are called."""
    global _batch
    with _lock:
        if _batch is None:
            raise ZeroingError("no active zeroing batch")
        keys = _batch["keys"]
        index = _batch["index"]

    if index < len(keys) and _session is not None and _session.get("sensor_key") == keys[index]:
        current = status()
        if current["done"]:
            result = apply()
            with _lock:
                if _batch is not None:
                    _batch["results"].append(result)
                    _batch["index"] += 1
                    target = _batch["target"]
                else:
                    target = DEFAULT_TARGET_SAMPLES
            _start_current_locked_out(target)

    with _lock:
        if _batch is None:
            # apply() above can, in principle, race a concurrent cancel_all();
            # report a finished-and-cleared batch rather than raising.
            return {"total": 0, "index": 0, "done": True, "current": None, "results": [], "failures": []}
        total = len(_batch["keys"])
        batch_index = _batch["index"]
        done = batch_index >= total
        current_key = _batch["keys"][batch_index] if not done else None
        results = list(_batch["results"])
        failures = list(_batch["failures"])

    current_status = status() if current_key is not None and _session is not None else None
    return {
        "total": total,
        "index": batch_index,
        "done": done,
        "current": current_status,
        "results": results,
        "failures": failures,
    }


def cancel_all() -> None:
    global _session, _batch
    with _lock:
        _session = None
        _batch = None
