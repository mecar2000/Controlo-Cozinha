"""
routes.sensors — sensor CRUD, including hard delete.

Built against a minimal Flask app carrying only this blueprint + monkeypatched
app.db, matching test_thresholds_route.py's convention.
"""

import json

import pytest
from flask import Flask

import app.daq as daq
import app.db as db
from app.routes.sensors import bp as sensors_bp


@pytest.fixture
def client():
    flask_app = Flask(__name__)
    flask_app.register_blueprint(sensors_bp)
    with flask_app.test_client() as c:
        yield c


def _sensor(key="sensor-1", **overrides):
    base = {
        "id": 1, "sensor_key": key, "label": "Counter — left",
        "x": 0.45, "y": 0.30, "z": 0.95, "enabled": True,
        "daq_device_id": "mainBoard", "daq_sensor_name": "H2-1",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


# --- listing -----------------------------------------------------------------


def test_list_sensors_enabled_only_still_works(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "list_sensors", lambda **kw: calls.append(kw) or [])
    client.get("/api/sensors?enabled_only=1")
    assert calls[0]["enabled_only"] is True


# --- create/update -----------------------------------------------------------


def test_upsert_sensor_creates_with_required_fields(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: None)  # new sensor, nothing to collide with
    monkeypatch.setattr(db, "list_sensors", lambda **kw: [])  # no other sensors to collide with
    monkeypatch.setattr(db, "upsert_sensor", lambda key, **kw: calls.append((key, kw)) or _sensor(key))
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({"label": "Counter — left", "x": 0.45, "y": 0.3, "z": 0.95}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    key, kwargs = calls[0]
    assert key == "sensor-1"
    assert kwargs["label"] == "Counter — left"
    assert kwargs["x"] == 0.45


def test_upsert_sensor_rejects_missing_required_field(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({"label": "x", "x": 0.0, "y": 0.0}),  # missing z
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_upsert_sensor_rejects_non_numeric_coordinate(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({"label": "x", "x": "oops", "y": 0.0, "z": 0.0}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_upsert_sensor_rejects_empty_label(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({"label": "  ", "x": 0.0, "y": 0.0, "z": 0.0}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_upsert_sensor_rejects_duplicate_label(client, monkeypatch):
    """Duplicate calls copy label/position onto a new sensor by design (the
    typical case: same-model sensors nearby) — if the operator forgets to
    change the label before saving, refuse rather than create two active
    sensors with the same name (confusing in the sensor list and on the
    room view)."""
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    monkeypatch.setattr(
        db, "list_sensors",
        lambda **kw: [_sensor("sensor-1", label="Counter — left", x=0.45, y=0.30, z=0.95)],
    )
    resp = client.put(
        "/api/sensors/sensor-2",
        data=json.dumps({"label": "Counter — left", "x": 9.0, "y": 9.0, "z": 9.0}),
        content_type="application/json",
    )
    assert resp.status_code == 409
    assert "label" in resp.get_json()["error"].lower()


def test_upsert_sensor_label_collision_check_is_case_insensitive(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    monkeypatch.setattr(
        db, "list_sensors",
        lambda **kw: [_sensor("sensor-1", label="Counter — left")],
    )
    resp = client.put(
        "/api/sensors/sensor-2",
        data=json.dumps({"label": "COUNTER — LEFT", "x": 9.0, "y": 9.0, "z": 9.0}),
        content_type="application/json",
    )
    assert resp.status_code == 409


def test_upsert_sensor_label_collision_ignores_the_sensor_being_edited(client, monkeypatch):
    """Editing sensor-1 without changing its own label must not collide with
    itself."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor("sensor-1", label="Counter — left"))
    calls = []
    monkeypatch.setattr(
        db, "list_sensors",
        lambda **kw: [_sensor("sensor-1", label="Counter — left", x=0.45, y=0.30, z=0.95)],
    )
    monkeypatch.setattr(db, "upsert_sensor", lambda key, **kw: calls.append((key, kw)) or _sensor(key))
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({"label": "Counter — left", "x": 0.45, "y": 0.30, "z": 0.95}),
        content_type="application/json",
    )
    assert resp.status_code == 200


def test_upsert_sensor_rejects_exact_duplicate_position(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    monkeypatch.setattr(
        db, "list_sensors",
        lambda **kw: [_sensor("sensor-1", label="Counter — left", x=0.45, y=0.30, z=0.95)],
    )
    resp = client.put(
        "/api/sensors/sensor-2",
        data=json.dumps({"label": "Different label", "x": 0.45, "y": 0.30, "z": 0.95}),
        content_type="application/json",
    )
    assert resp.status_code == 409
    assert "position" in resp.get_json()["error"].lower()


def test_upsert_sensor_allows_a_nearby_but_not_identical_position(client, monkeypatch):
    """Only an EXACT position match is refused — sensors of the same model
    placed nearby (the typical duplicate-and-nudge case) are exactly what
    this feature exists for."""
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    calls = []
    monkeypatch.setattr(
        db, "list_sensors",
        lambda **kw: [_sensor("sensor-1", label="Counter — left", x=0.45, y=0.30, z=0.95)],
    )
    monkeypatch.setattr(db, "upsert_sensor", lambda key, **kw: calls.append((key, kw)) or _sensor(key))
    resp = client.put(
        "/api/sensors/sensor-2",
        data=json.dumps({"label": "Different label", "x": 0.55, "y": 0.30, "z": 0.95}),
        content_type="application/json",
    )
    assert resp.status_code == 200


def test_upsert_sensor_position_collision_ignores_the_sensor_being_edited(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor("sensor-1", x=0.45, y=0.30, z=0.95))
    calls = []
    monkeypatch.setattr(
        db, "list_sensors",
        lambda **kw: [_sensor("sensor-1", label="Counter — left", x=0.45, y=0.30, z=0.95)],
    )
    monkeypatch.setattr(db, "upsert_sensor", lambda key, **kw: calls.append((key, kw)) or _sensor(key))
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({"label": "Counter — left", "x": 0.45, "y": 0.30, "z": 0.95}),
        content_type="application/json",
    )
    assert resp.status_code == 200


# --- delete (hard delete) ---------------------------------------------------


def test_delete_sensor_deletes(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key))
    monkeypatch.setattr(db, "delete_sensor", lambda key: calls.append(key))
    resp = client.delete("/api/sensors/sensor-1")
    assert resp.status_code == 200
    assert calls == ["sensor-1"]
    assert resp.get_json()["deleted"] is True


def test_delete_unknown_sensor_is_404(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    resp = client.delete("/api/sensors/no-such-sensor")
    assert resp.status_code == 404


# --- firmware_index (problems.txt: sensors shouldn't be locked to H2-N) -----


def test_set_firmware_index(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key))
    monkeypatch.setattr(db, "set_firmware_index", lambda key, idx: calls.append((key, idx)))
    resp = client.put(
        "/api/sensors/sensor-1/firmware-index",
        data=json.dumps({"firmware_index": 4}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert calls == [("sensor-1", 4)]


def test_clear_firmware_index_with_null(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key))
    monkeypatch.setattr(db, "set_firmware_index", lambda key, idx: calls.append((key, idx)))
    resp = client.put(
        "/api/sensors/sensor-1/firmware-index",
        data=json.dumps({"firmware_index": None}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert calls == [("sensor-1", None)]


def test_set_firmware_index_rejects_out_of_range(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key))
    resp = client.put(
        "/api/sensors/sensor-1/firmware-index",
        data=json.dumps({"firmware_index": 6}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_set_firmware_index_rejects_missing_field(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key))
    resp = client.put(
        "/api/sensors/sensor-1/firmware-index",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_set_firmware_index_unknown_sensor_is_404(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    resp = client.put(
        "/api/sensors/no-such-sensor/firmware-index",
        data=json.dumps({"firmware_index": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 404


# --- daq_pin (Part 5, Stage 2: PLC/sensor commissioning wizard) ------------
#
# daq_sensor_name must be DERIVED from daq_pin, never accepted as typed
# input — the trap this whole design exists to close: DataAcquisition shows
# friendly names on its own cards, but the raw MQTT topic this webapp
# subscribes to carries the PIN LABEL, not the friendly name. Naming a
# sensor to match DAQ's display while the device actually publishes on a
# different pin looks correct in DAQ and shows nothing in the kitchen view.


def test_upsert_sensor_round_trips_daq_pin(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    monkeypatch.setattr(db, "upsert_sensor", lambda key, **kw: calls.append((key, kw)) or _sensor(key))
    monkeypatch.setattr(
        daq, "list_devices",
        lambda: [{
            "device_id": "mainBoard",
            "config": {"sensors": [{"pin": 100, "name": "H2-1", "type": "current"}]},
        }],
    )
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({
            "label": "Counter — left", "x": 0.0, "y": 0.0, "z": 0.0,
            "daq_device_id": "mainBoard", "daq_pin": 100,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200
    _, kwargs = calls[0]
    assert kwargs["daq_pin"] == 100


def test_upsert_sensor_derives_daq_sensor_name_from_pin(client, monkeypatch):
    """daq_pin present -> daq_sensor_name is looked up from DataAcquisition's
    own reported pin map for that device, never taken from the request body."""
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    monkeypatch.setattr(db, "upsert_sensor", lambda key, **kw: calls.append((key, kw)) or _sensor(key))
    monkeypatch.setattr(
        daq, "list_devices",
        lambda: [{
            "device_id": "mainBoard",
            "config": {"sensors": [{"pin": 100, "name": "H2-1", "type": "current"}]},
        }],
    )
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({
            "label": "Counter — left", "x": 0.0, "y": 0.0, "z": 0.0,
            "daq_device_id": "mainBoard", "daq_pin": 100,
            "daq_sensor_name": "typed-name-should-be-ignored",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200
    _, kwargs = calls[0]
    assert kwargs["daq_sensor_name"] == "H2-1"


def test_upsert_sensor_rejects_daq_pin_not_reported_by_the_device(client, monkeypatch):
    """A pin DataAcquisition does not currently report for this device would
    derive no name at all — refuse rather than silently store a daq_pin with
    no daq_sensor_name, which would look bound but resolve nothing."""
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    monkeypatch.setattr(
        daq, "list_devices",
        lambda: [{"device_id": "mainBoard", "config": {"sensors": []}}],
    )
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({
            "label": "Counter — left", "x": 0.0, "y": 0.0, "z": 0.0,
            "daq_device_id": "mainBoard", "daq_pin": 100,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_upsert_sensor_daq_pin_daq_unreachable_returns_502(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)

    def _boom():
        raise daq.DaqUnreachable("DAQ down")
    monkeypatch.setattr(daq, "list_devices", _boom)
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({
            "label": "Counter — left", "x": 0.0, "y": 0.0, "z": 0.0,
            "daq_device_id": "mainBoard", "daq_pin": 100,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 502


def test_upsert_sensor_daq_pin_requires_daq_device_id(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({"label": "Counter — left", "x": 0.0, "y": 0.0, "z": 0.0, "daq_pin": 100}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_upsert_sensor_without_daq_pin_leaves_daq_sensor_name_untouched(client, monkeypatch):
    """No daq_pin in the request (e.g. editing only label/position) must not
    force daq_sensor_name to None — it simply isn't part of this write."""
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    monkeypatch.setattr(db, "upsert_sensor", lambda key, **kw: calls.append((key, kw)) or _sensor(key))
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({"label": "Counter — left", "x": 0.0, "y": 0.0, "z": 0.0}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    _, kwargs = calls[0]
    assert kwargs.get("daq_pin") is None
    assert kwargs.get("daq_sensor_name") is None
