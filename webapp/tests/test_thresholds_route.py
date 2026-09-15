"""
routes.thresholds — resolves sensor_key -> firmware index via
daq_sensor_name, then publishes over config/set (app.commands.set_thresholds).
Built against a minimal Flask app carrying only this blueprint, matching
test_daq_proxy_conversions.py's convention.
"""

import json

import pytest
from flask import Flask

import app.commands as commands
import app.db as db
from app.commands import CommandError
from app.routes.thresholds import bp as thresholds_bp


@pytest.fixture
def client():
    flask_app = Flask(__name__)
    flask_app.register_blueprint(thresholds_bp)
    with flask_app.test_client() as c:
        yield c


def _sensor(sensor_key, daq_sensor_name, firmware_index=None):
    return {
        "id": 1, "sensor_key": sensor_key, "label": sensor_key,
        "x": 0.0, "y": 0.0, "z": 0.0, "enabled": True,
        "daq_device_id": "mainBoard", "daq_sensor_name": daq_sensor_name,
        "firmware_index": firmware_index,
        "updated_at": None,
    }


def test_set_thresholds_resolves_sensor_key_to_firmware_index(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key, "H2-3"))
    calls = []
    monkeypatch.setattr(commands, "set_thresholds", lambda entries: calls.append(entries))

    resp = client.put(
        "/api/thresholds",
        data=json.dumps({"thresholds": [{"sensor_key": "sensor-3", "thresholdPct": 2.5}]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    # H2-3 -> firmware index 2 (H2-N -> N-1).
    assert calls == [[{"sensor": 2, "thresholdPct": 2.5}]]


def test_set_thresholds_rejects_unknown_sensor_key(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: None)
    resp = client.put(
        "/api/thresholds",
        data=json.dumps({"thresholds": [{"sensor_key": "nope", "thresholdPct": 1.0}]}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_set_thresholds_rejects_sensor_with_no_firmware_index(client, monkeypatch):
    """A sensor whose daq_sensor_name doesn't follow H2-N (or has none
    configured), and no explicit firmware_index either, has no firmware
    index to send a threshold to."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key, None))
    resp = client.put(
        "/api/thresholds",
        data=json.dumps({"thresholds": [{"sensor_key": "flow", "thresholdPct": 1.0}]}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_set_thresholds_uses_explicit_firmware_index_over_a_differently_named_sensor(client, monkeypatch):
    """A newly-defined sensor doesn't have to be named H2-N at all — an
    explicit firmware_index (set once, when the sensor is wired to a real
    firmware channel) is enough (problems.txt: sensors shouldn't be locked
    to the predefined H2-N naming)."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key, "Stovetop", firmware_index=4))
    calls = []
    monkeypatch.setattr(commands, "set_thresholds", lambda entries: calls.append(entries))

    resp = client.put(
        "/api/thresholds",
        data=json.dumps({"thresholds": [{"sensor_key": "sensor-5", "thresholdPct": 3.0}]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert calls == [[{"sensor": 4, "thresholdPct": 3.0}]]


def test_set_thresholds_explicit_firmware_index_wins_over_h2_n_name(client, monkeypatch):
    """If both are present (e.g. during a migration), the explicit index is
    authoritative — the H2-N name is only ever a fallback guess."""
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key, "H2-1", firmware_index=5))
    calls = []
    monkeypatch.setattr(commands, "set_thresholds", lambda entries: calls.append(entries))

    resp = client.put(
        "/api/thresholds",
        data=json.dumps({"thresholds": [{"sensor_key": "sensor-1", "thresholdPct": 1.0}]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert calls == [[{"sensor": 5, "thresholdPct": 1.0}]]  # 5, not H2-1's implied 0


def test_set_thresholds_rejects_out_of_range_explicit_firmware_index(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key, "Stovetop", firmware_index=6))
    resp = client.put(
        "/api/thresholds",
        data=json.dumps({"thresholds": [{"sensor_key": "sensor-x", "thresholdPct": 1.0}]}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_set_thresholds_rejects_negative_pct(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key, "H2-1"))
    resp = client.put(
        "/api/thresholds",
        data=json.dumps({"thresholds": [{"sensor_key": "sensor-1", "thresholdPct": -1.0}]}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_set_thresholds_rejects_empty_body(client):
    resp = client.put(
        "/api/thresholds",
        data=json.dumps({"thresholds": []}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_set_thresholds_returns_503_when_mqtt_down(client, monkeypatch):
    monkeypatch.setattr(db, "get_sensor", lambda key: _sensor(key, "H2-1"))

    def _boom(_entries):
        raise CommandError("MQTT not connected")
    monkeypatch.setattr(commands, "set_thresholds", _boom)

    resp = client.put(
        "/api/thresholds",
        data=json.dumps({"thresholds": [{"sensor_key": "sensor-1", "thresholdPct": 1.0}]}),
        content_type="application/json",
    )
    assert resp.status_code == 503
