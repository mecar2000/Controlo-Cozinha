"""
routes.sensors — sensor CRUD, including soft delete (problems.txt: "There
shouldnt be some predefined sensors ... There should be memory of previous
ones" — decided as local soft-delete: an archived sensor stays recoverable
rather than vanishing, and stays joinable by historical runs/layout
snapshots that reference its sensor_key).

Built against a minimal Flask app carrying only this blueprint + monkeypatched
app.db, matching test_thresholds_route.py's convention.
"""

import json

import pytest
from flask import Flask

import app.db as db
from app.routes.sensors import bp as sensors_bp


@pytest.fixture
def client():
    flask_app = Flask(__name__)
    flask_app.register_blueprint(sensors_bp)
    with flask_app.test_client() as c:
        yield c


def _sensor(key="sensor-1", archived=False, **overrides):
    base = {
        "id": 1, "sensor_key": key, "label": "Counter — left",
        "x": 0.45, "y": 0.30, "z": 0.95, "enabled": True, "archived": archived,
        "daq_device_id": "mainBoard", "daq_sensor_name": "H2-1",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


# --- listing: archived hidden by default -----------------------------------


def test_list_sensors_excludes_archived_by_default(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "list_sensors", lambda **kw: calls.append(kw) or [_sensor()])
    resp = client.get("/api/sensors")
    assert resp.status_code == 200
    assert calls[0].get("include_archived", False) is False


def test_list_sensors_include_archived_param(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "list_sensors", lambda **kw: calls.append(kw) or [])
    client.get("/api/sensors?include_archived=1")
    assert calls[0]["include_archived"] is True


def test_list_sensors_enabled_only_still_works(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "list_sensors", lambda **kw: calls.append(kw) or [])
    client.get("/api/sensors?enabled_only=1")
    assert calls[0]["enabled_only"] is True


# --- create/update -----------------------------------------------------------


def test_upsert_sensor_creates_with_required_fields(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: None)  # new sensor, nothing to collide with
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


def test_upsert_sensor_rejects_reviving_an_archived_key_by_accident(client, monkeypatch):
    """Upserting a sensor_key that currently belongs to an ARCHIVED sensor
    must not silently un-archive and overwrite it — that's what the
    dedicated restore endpoint is for. Prevents a name collision from
    quietly resurrecting old (possibly stale/wrong) config."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key, archived=True))
    resp = client.put(
        "/api/sensors/sensor-1",
        data=json.dumps({"label": "New label", "x": 0.0, "y": 0.0, "z": 0.0}),
        content_type="application/json",
    )
    assert resp.status_code == 409
    assert "archived" in resp.get_json()["error"].lower()


# --- archive / restore (soft delete) ----------------------------------------


def test_delete_sensor_archives_rather_than_deletes(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key))
    monkeypatch.setattr(db, "archive_sensor", lambda key: calls.append(key))
    resp = client.delete("/api/sensors/sensor-1")
    assert resp.status_code == 200
    assert calls == ["sensor-1"]
    assert resp.get_json()["archived"] is True


def test_delete_unknown_sensor_is_404(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    resp = client.delete("/api/sensors/no-such-sensor")
    assert resp.status_code == 404


def test_restore_sensor(client, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key, archived=True))
    monkeypatch.setattr(db, "restore_sensor", lambda key: calls.append(key))
    resp = client.post("/api/sensors/sensor-1/restore")
    assert resp.status_code == 200
    assert calls == ["sensor-1"]


def test_restore_sensor_not_archived_is_a_noop_success(client, monkeypatch):
    """Restoring a sensor that's already active isn't an error — idempotent,
    matches upsert's own idempotent-write convention."""
    calls = []
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key, archived=False))
    monkeypatch.setattr(db, "restore_sensor", lambda key: calls.append(key))
    resp = client.post("/api/sensors/sensor-1/restore")
    assert resp.status_code == 200


def test_restore_unknown_sensor_is_404(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    resp = client.post("/api/sensors/no-such-sensor/restore")
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
