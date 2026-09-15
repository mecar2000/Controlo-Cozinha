"""
"Zero in clean air" (problems.txt:3 — "cant i just see it from when there is
no hydrogen what's the baseline for each sensor and get the adjustment from
there?"): captures N raw samples of one sensor's live reading, averages
them, and writes the result as that sensor's calibration raw_min via
DataAcquisition's existing /conversions endpoint (app.daq.set_conversion) —
no new calibration store, no measuring rig, per the user's decisions.

Written before app/zeroing.py and routes/sensor_zero.py exist, per the
project's test-first workflow. Two layers:
  - app.state's capture buffer (pure in-memory bookkeeping, tested directly)
  - app.zeroing (the session lifecycle: start/collect/apply/cancel) and its
    route, tested against a bare Flask app + monkeypatched daq/db, matching
    this suite's convention (test_daq_proxy_conversions.py,
    test_thresholds_route.py).
"""

import json

import pytest
from flask import Flask

import app.daq as daq
import app.db as db
import app.state as state
import app.zeroing as zeroing
from app.routes.sensor_zero import bp as sensor_zero_bp

SENSOR_KEY = "sensor-1"
DEVICE_ID = "mainBoard"
SENSOR_NAME = "H2-1"


def _sensor(key=SENSOR_KEY, daq_device_id=DEVICE_ID, daq_sensor_name=SENSOR_NAME):
    return {
        "id": 1, "sensor_key": key, "label": "Counter", "x": 0.0, "y": 0.0, "z": 0.0,
        "enabled": True,
        "daq_device_id": daq_device_id, "daq_sensor_name": daq_sensor_name,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _stub_daq_conversions(monkeypatch, raw_min=4.0):
    monkeypatch.setattr(daq, "get_conversions", lambda device_id: {
        SENSOR_NAME: {
            "sensor_name": SENSOR_NAME,
            "params": {"method": "linear", "raw_min": raw_min, "raw_max": 20.0,
                       "min_value": 0, "max_value": 100},
            "unit_symbol": "%v/v",
        }
    })
    monkeypatch.setattr(daq, "set_conversion", lambda *a, **kw: {"ok": True})


@pytest.fixture(autouse=True)
def _reset_zeroing_state():
    zeroing.reset()
    yield
    zeroing.reset()


@pytest.fixture
def client():
    flask_app = Flask(__name__)
    flask_app.register_blueprint(sensor_zero_bp)
    with flask_app.test_client() as c:
        yield c


# --- app.zeroing: the session lifecycle -------------------------------------


def test_start_creates_an_active_session(monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    session = zeroing.start(SENSOR_KEY, target_samples=100)
    assert session["sensor_key"] == SENSOR_KEY
    assert session["target"] == 100
    assert session["collected"] == 0
    assert session["done"] is False
    assert zeroing.is_active() is True


def test_start_unknown_sensor_raises(monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    with pytest.raises(zeroing.ZeroingError, match="not found"):
        zeroing.start("no-such-sensor")


def test_start_sensor_with_no_daq_identity_raises(monkeypatch):
    """A sensor with no daq_device_id/daq_sensor_name has no live reading to
    average — nothing to zero yet."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(daq_device_id=None, daq_sensor_name=None))
    with pytest.raises(zeroing.ZeroingError, match="no DAQ identity"):
        zeroing.start(SENSOR_KEY)


def test_start_while_already_active_raises(monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    zeroing.start(SENSOR_KEY)
    with pytest.raises(zeroing.ZeroingError, match="already"):
        zeroing.start(SENSOR_KEY)


def test_collect_ingests_new_raw_readings_since_start(monkeypatch):
    """Each reading must carry its own ts_ms, matching a real sensor stream
    where every publish is a fresh sample — collect() dedupes on ts_ms
    (see test_collect_does_not_double_count_the_same_reading below), so
    reusing one ts_ms across distinct samples would undercount here too."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    zeroing.start(SENSOR_KEY, target_samples=3)

    for i, raw in enumerate((4.01, 4.02, 4.00)):
        state.set_live_reading(f"{DEVICE_ID}/{SENSOR_NAME}", raw, "mA", 1000 + i, converted=False,
                                raw_value=raw, raw_unit="mA")
        zeroing.collect()

    status = zeroing.status()
    assert status["collected"] == 3
    assert status["done"] is True


def test_collect_does_not_double_count_the_same_reading(monkeypatch):
    """set_live_reading overwrites in place — collect() must only count a
    NEW sample (by ts_ms), not re-average a value it already saw because the
    caller polled status twice between publishes."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    zeroing.start(SENSOR_KEY, target_samples=5)

    state.set_live_reading(f"{DEVICE_ID}/{SENSOR_NAME}", 4.0, "mA", 1000, converted=False,
                            raw_value=4.0, raw_unit="mA")
    zeroing.collect()
    zeroing.collect()
    zeroing.collect()
    assert zeroing.status()["collected"] == 1


def test_apply_averages_collected_samples_and_writes_raw_min(monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    calls = []
    monkeypatch.setattr(daq, "get_conversions", lambda device_id: {
        SENSOR_NAME: {
            "sensor_name": SENSOR_NAME,
            "params": {"method": "linear", "raw_min": 4.0, "raw_max": 20.0,
                       "min_value": 0, "max_value": 100},
            "unit_symbol": "%v/v",
        }
    })
    monkeypatch.setattr(daq, "set_conversion", lambda *a, **kw: calls.append((a, kw)) or {"ok": True})

    zeroing.start(SENSOR_KEY, target_samples=3)
    for i, raw in enumerate((4.00, 4.02, 3.98)):
        state.set_live_reading(f"{DEVICE_ID}/{SENSOR_NAME}", raw, "mA", 1000 + i, converted=False,
                                raw_value=raw, raw_unit="mA")
        zeroing.collect()

    result = zeroing.apply()
    assert result["previous_raw_min"] == 4.0
    assert result["new_raw_min"] == pytest.approx(4.0, abs=0.01)
    assert result["sample_count"] == 3

    (device_id, sensor_name), kwargs = calls[0]
    assert device_id == DEVICE_ID
    assert sensor_name == SENSOR_NAME
    assert kwargs["params"]["raw_min"] == pytest.approx(4.0, abs=0.01)
    assert kwargs["params"]["raw_max"] == 20.0  # untouched
    assert kwargs["params"]["min_value"] == 0   # untouched
    assert zeroing.is_active() is False  # session cleared after apply


def test_apply_before_target_reached_raises(monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    zeroing.start(SENSOR_KEY, target_samples=100)
    with pytest.raises(zeroing.ZeroingError, match="not enough samples"):
        zeroing.apply()


def test_apply_with_no_existing_calibration_uses_signal_defaults(monkeypatch):
    """A sensor with no calibration yet still has SOMETHING to zero against
    — the signal-appropriate default span (4-20 for current), same defaults
    app.conversion falls back to."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    monkeypatch.setattr(daq, "get_conversions", lambda device_id: {})
    calls = []
    monkeypatch.setattr(daq, "set_conversion", lambda *a, **kw: calls.append((a, kw)) or {"ok": True})

    zeroing.start(SENSOR_KEY, target_samples=1)
    state.set_live_reading(f"{DEVICE_ID}/{SENSOR_NAME}", 4.05, "mA", 1000, converted=False,
                            raw_value=4.05, raw_unit="mA")
    zeroing.collect()
    result = zeroing.apply()
    assert result["new_raw_min"] == pytest.approx(4.05, abs=0.001)
    _, kwargs = calls[0]
    assert kwargs["params"]["raw_max"] == 20.0  # current default span


def test_apply_rejects_an_implausibly_wide_spread(monkeypatch):
    """A moving reading is not clean air — refuse rather than silently
    average noise into the calibration."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    zeroing.start(SENSOR_KEY, target_samples=3)
    for i, raw in enumerate((4.0, 9.0, 4.0)):  # 5mA spread, way too wide
        state.set_live_reading(f"{DEVICE_ID}/{SENSOR_NAME}", raw, "mA", 1000 + i, converted=False,
                                raw_value=raw, raw_unit="mA")
        zeroing.collect()
    with pytest.raises(zeroing.ZeroingError, match="spread"):
        zeroing.apply()


def test_cancel_clears_the_session(monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    zeroing.start(SENSOR_KEY)
    zeroing.cancel()
    assert zeroing.is_active() is False
    with pytest.raises(zeroing.ZeroingError, match="no active"):
        zeroing.apply()


def test_manual_raw_min_override_bypasses_capture(monkeypatch):
    """The user asked to be able to override manually — a direct raw_min
    write with no session at all, same underlying DataAcquisition call."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    monkeypatch.setattr(daq, "get_conversions", lambda device_id: {
        SENSOR_NAME: {"sensor_name": SENSOR_NAME,
                      "params": {"method": "linear", "raw_min": 4.0, "raw_max": 20.0,
                                 "min_value": 0, "max_value": 100},
                      "unit_symbol": "%v/v"}
    })
    calls = []
    monkeypatch.setattr(daq, "set_conversion", lambda *a, **kw: calls.append((a, kw)) or {"ok": True})

    result = zeroing.set_raw_min_manually(SENSOR_KEY, 4.3)
    assert result["new_raw_min"] == 4.3
    _, kwargs = calls[0]
    assert kwargs["params"]["raw_min"] == 4.3


# --- routes.sensor_zero ------------------------------------------------------


def test_route_start_returns_the_session(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    resp = client.post(f"/api/sensors/{SENSOR_KEY}/zero/start")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["sensor_key"] == SENSOR_KEY
    assert body["target"] == 100  # the decided default


def test_route_start_unknown_sensor_is_404(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    resp = client.post(f"/api/sensors/{SENSOR_KEY}/zero/start")
    assert resp.status_code == 404


def test_route_status_reports_progress(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    client.post(f"/api/sensors/{SENSOR_KEY}/zero/start?target_samples=2")
    state.set_live_reading(f"{DEVICE_ID}/{SENSOR_NAME}", 4.0, "mA", 1000, converted=False,
                            raw_value=4.0, raw_unit="mA")
    resp = client.get(f"/api/sensors/{SENSOR_KEY}/zero/status")
    body = resp.get_json()
    assert body["collected"] == 1
    assert body["target"] == 2
    assert body["done"] is False


def test_route_apply_returns_409_before_target_reached(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    client.post(f"/api/sensors/{SENSOR_KEY}/zero/start")
    resp = client.post(f"/api/sensors/{SENSOR_KEY}/zero/apply")
    assert resp.status_code == 409


def test_route_apply_succeeds_once_target_reached(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    monkeypatch.setattr(daq, "get_conversions", lambda device_id: {
        SENSOR_NAME: {"sensor_name": SENSOR_NAME,
                      "params": {"method": "linear", "raw_min": 4.0, "raw_max": 20.0,
                                 "min_value": 0, "max_value": 100},
                      "unit_symbol": "%v/v"}
    })
    monkeypatch.setattr(daq, "set_conversion", lambda *a, **kw: {"ok": True})

    client.post(f"/api/sensors/{SENSOR_KEY}/zero/start?target_samples=1")
    state.set_live_reading(f"{DEVICE_ID}/{SENSOR_NAME}", 4.02, "mA", 1000, converted=False,
                            raw_value=4.02, raw_unit="mA")
    client.get(f"/api/sensors/{SENSOR_KEY}/zero/status")  # trigger collect()
    resp = client.post(f"/api/sensors/{SENSOR_KEY}/zero/apply")
    assert resp.status_code == 200
    assert resp.get_json()["new_raw_min"] == pytest.approx(4.02, abs=0.001)


def test_route_cancel(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    client.post(f"/api/sensors/{SENSOR_KEY}/zero/start")
    resp = client.post(f"/api/sensors/{SENSOR_KEY}/zero/cancel")
    assert resp.status_code == 200
    assert zeroing.is_active() is False


def test_route_manual_override(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    monkeypatch.setattr(daq, "get_conversions", lambda device_id: {
        SENSOR_NAME: {"sensor_name": SENSOR_NAME,
                      "params": {"method": "linear", "raw_min": 4.0, "raw_max": 20.0,
                                 "min_value": 0, "max_value": 100},
                      "unit_symbol": "%v/v"}
    })
    monkeypatch.setattr(daq, "set_conversion", lambda *a, **kw: {"ok": True})
    resp = client.put(
        f"/api/sensors/{SENSOR_KEY}/zero/manual",
        data=json.dumps({"raw_min": 4.15}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["new_raw_min"] == 4.15


def test_route_manual_override_rejects_missing_raw_min(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    resp = client.put(
        f"/api/sensors/{SENSOR_KEY}/zero/manual",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400


# --- zero all (batch across every DAQ-identified sensor) -------------------


def _sensors_2():
    # sensor-2 is wired to its own device/name pair, unused by any other
    # test in this file — app.state's live-readings dict is process-global
    # with no reset, so reusing DEVICE_ID/SENSOR_NAME here would let a
    # leftover reading from another test complete sensor-2 before the
    # assertions that check it's still pending get to run.
    return [
        _sensor("sensor-1", "mainBoard", "H2-1"),
        _sensor("sensor-2", "batchBoard", "H2-1"),
    ]


def _mock_sensor_lookup(monkeypatch, sensors):
    """start_all()/start() need BOTH db.list_sensors (to build the queue) and
    db.get_sensor (start() looks the sensor up again by key) mocked from the
    same underlying list, or every queued sensor "fails" as not-found."""
    monkeypatch.setattr(db, "list_sensors", lambda **kw: sensors)
    monkeypatch.setattr(db, "get_sensor", lambda key: next((s for s in sensors if s["sensor_key"] == key), None))


def test_start_all_begins_the_first_sensor(monkeypatch):
    _mock_sensor_lookup(monkeypatch, _sensors_2())
    status = zeroing.start_all(target_samples=2)
    assert status["total"] == 2
    assert status["index"] == 0
    assert status["current"]["sensor_key"] == "sensor-1"
    assert zeroing.is_active() is True


def test_start_all_skips_sensors_with_no_daq_identity(monkeypatch):
    sensors = [_sensor("sensor-1", "mainBoard", "H2-1"), _sensor("sensor-2", None, None)]
    _mock_sensor_lookup(monkeypatch, sensors)
    status = zeroing.start_all()
    assert status["total"] == 1
    assert status["current"]["sensor_key"] == "sensor-1"


def test_start_all_with_no_zeroable_sensors_raises(monkeypatch):
    monkeypatch.setattr(db, "list_sensors", lambda **kw: [_sensor("s", None, None)])
    with pytest.raises(zeroing.ZeroingError, match="no sensors"):
        zeroing.start_all()


def test_start_all_while_already_active_raises(monkeypatch):
    _mock_sensor_lookup(monkeypatch, _sensors_2())
    zeroing.start_all()
    with pytest.raises(zeroing.ZeroingError, match="already active"):
        zeroing.start_all()


def test_status_all_auto_applies_and_advances_to_next_sensor(monkeypatch):
    # Dedicated device ids, unused by any other test in this file — see the
    # comment on _sensors_2 for why that matters against the global,
    # never-reset app.state live-readings dict.
    sensors = [
        _sensor("sensor-1", "progressBoardA", "H2-1"),
        _sensor("sensor-2", "progressBoardB", "H2-1"),
    ]
    _mock_sensor_lookup(monkeypatch, sensors)
    _stub_daq_conversions(monkeypatch)

    zeroing.start_all(target_samples=1)
    state.set_live_reading("progressBoardA/H2-1", 4.05, "mA", 1000, converted=False,
                            raw_value=4.05, raw_unit="mA")
    status = zeroing.status_all()

    assert status["index"] == 1
    assert len(status["results"]) == 1
    assert status["results"][0]["sensor_key"] == "sensor-1"
    assert status["done"] is False
    assert status["current"]["sensor_key"] == "sensor-2"


def test_status_all_reports_done_after_the_last_sensor(monkeypatch):
    _mock_sensor_lookup(monkeypatch, [_sensor("sensor-1", "mainBoard", "H2-1")])
    _stub_daq_conversions(monkeypatch)

    zeroing.start_all(target_samples=1)
    state.set_live_reading("mainBoard/H2-1", 4.05, "mA", 1000, converted=False,
                            raw_value=4.05, raw_unit="mA")
    status = zeroing.status_all()

    assert status["done"] is True
    assert status["current"] is None
    assert len(status["results"]) == 1
    assert zeroing.is_active() is False


def test_status_all_with_no_active_batch_raises(monkeypatch):
    with pytest.raises(zeroing.ZeroingError, match="no active"):
        zeroing.status_all()


def test_cancel_all_clears_the_batch(monkeypatch):
    _mock_sensor_lookup(monkeypatch, _sensors_2())
    zeroing.start_all()
    zeroing.cancel_all()
    assert zeroing.is_active() is False
    with pytest.raises(zeroing.ZeroingError, match="no active"):
        zeroing.status_all()


def test_route_start_all(client, monkeypatch):
    # target_samples left at the (large) default — this only checks the
    # batch starts pointed at sensor-1, not progression, so it must not be
    # vulnerable to a stray leftover live reading (module-global state,
    # shared with other tests) completing sensor-1 before the assertion.
    _mock_sensor_lookup(monkeypatch, _sensors_2())
    resp = client.post("/api/sensors/zero-all/start")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 2
    assert body["current"]["sensor_key"] == "sensor-1"


def test_route_start_all_with_no_zeroable_sensors_is_404(client, monkeypatch):
    monkeypatch.setattr(db, "list_sensors", lambda **kw: [])
    resp = client.post("/api/sensors/zero-all/start")
    assert resp.status_code == 404


def test_route_status_all_progresses_through_the_batch(client, monkeypatch):
    # Dedicated device ids — see the comment on the equivalent app.zeroing
    # test (test_status_all_auto_applies_and_advances_to_next_sensor).
    sensors = [
        _sensor("sensor-1", "routeProgressA", "H2-1"),
        _sensor("sensor-2", "routeProgressB", "H2-1"),
    ]
    _mock_sensor_lookup(monkeypatch, sensors)
    _stub_daq_conversions(monkeypatch)

    client.post("/api/sensors/zero-all/start?target_samples=1")
    state.set_live_reading("routeProgressA/H2-1", 4.05, "mA", 1000, converted=False,
                            raw_value=4.05, raw_unit="mA")
    resp = client.get("/api/sensors/zero-all/status")
    body = resp.get_json()
    assert body["index"] == 1
    assert body["current"]["sensor_key"] == "sensor-2"


def test_route_cancel_all(client, monkeypatch):
    _mock_sensor_lookup(monkeypatch, _sensors_2())
    client.post("/api/sensors/zero-all/start")
    resp = client.post("/api/sensors/zero-all/cancel")
    assert resp.status_code == 200
    assert zeroing.is_active() is False


# --- runs.start_run() blocks while a zeroing session is active -------------


def test_start_run_is_blocked_while_zeroing(monkeypatch):
    """The air must stay clean for the whole capture — a run must not be
    startable mid-session (problems.txt decision: zeroing 'blocks starts so
    air remains clean for the sampling')."""
    import app.runs as runs
    from app.runs import RunStartRejected

    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor())
    zeroing.start(SENSOR_KEY)
    with pytest.raises(RunStartRejected, match="[Zz]ero"):
        runs.start_run(config_id=1, run_name="run1")
