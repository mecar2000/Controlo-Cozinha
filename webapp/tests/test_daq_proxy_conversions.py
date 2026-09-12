"""
routes.daq_proxy's conversion write endpoints — the webapp-side calibration
editor that writes through to DataAcquisition rather than owning a second
copy (see the module docstring). Built against a minimal Flask app carrying
only this blueprint, so these tests need no MySQL/MQTT/DataAcquisition —
matching this suite's convention of testing handler logic directly rather
than through a full create_app().
"""

import json

import pytest
from flask import Flask

import app.daq as daq
import app.mqtt as mqtt_mod
from app.routes.daq_proxy import bp as daq_proxy_bp


@pytest.fixture
def client():
    flask_app = Flask(__name__)
    flask_app.register_blueprint(daq_proxy_bp)
    with flask_app.test_client() as c:
        yield c


def test_set_conversion_rejects_custom_method(client):
    """The applier (app.conversion) cannot evaluate a custom formula, so a
    calibration stored as "custom" would silently read back as unconverted —
    refuse the write instead of accepting one that can never take effect."""
    resp = client.put(
        "/api/daq/conversions/mainBoard/H2-1",
        data=json.dumps({"method": "custom", "params": {"formula": "raw * 2"}, "unit_symbol": "%v/v"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "custom" in resp.get_json()["error"]


def test_set_conversion_rejects_unsupported_method(client):
    resp = client.put(
        "/api/daq/conversions/mainBoard/H2-1",
        data=json.dumps({"method": "diff_pressure", "params": {}, "unit_symbol": "bar"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_set_conversion_requires_params_object(client):
    resp = client.put(
        "/api/daq/conversions/mainBoard/H2-1",
        data=json.dumps({"method": "linear", "params": "nope", "unit_symbol": "%v/v"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_set_conversion_requires_unit_symbol(client):
    resp = client.put(
        "/api/daq/conversions/mainBoard/H2-1",
        data=json.dumps({"method": "linear", "params": {}}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_set_conversion_writes_through_to_daq_and_invalidates_cache(client, monkeypatch):
    calls = []
    monkeypatch.setattr(daq, "set_conversion", lambda *a, **kw: calls.append((a, kw)) or {"ok": True})
    invalidated = []
    monkeypatch.setattr(mqtt_mod, "invalidate_conversions", lambda device_id: invalidated.append(device_id))

    resp = client.put(
        "/api/daq/conversions/mainBoard/H2-1",
        data=json.dumps({
            "type": "gas", "method": "linear",
            "params": {"raw_min": 4, "raw_max": 20, "min_value": 0, "max_value": 4},
            "unit_symbol": "%v/v",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert calls and calls[0][0] == ("mainBoard", "H2-1")
    assert invalidated == ["mainBoard"]


def test_set_conversion_does_not_invalidate_on_daq_failure(client, monkeypatch):
    def _boom(*_a, **_kw):
        raise daq.DaqUnreachable("DAQ down")
    monkeypatch.setattr(daq, "set_conversion", _boom)
    invalidated = []
    monkeypatch.setattr(mqtt_mod, "invalidate_conversions", lambda device_id: invalidated.append(device_id))

    resp = client.put(
        "/api/daq/conversions/mainBoard/H2-1",
        data=json.dumps({"method": "linear", "params": {}, "unit_symbol": "%v/v"}),
        content_type="application/json",
    )
    assert resp.status_code == 502
    assert invalidated == []


def test_delete_conversion_writes_through_and_invalidates(client, monkeypatch):
    monkeypatch.setattr(daq, "delete_conversion", lambda *a, **kw: {"deleted": True})
    invalidated = []
    monkeypatch.setattr(mqtt_mod, "invalidate_conversions", lambda device_id: invalidated.append(device_id))

    resp = client.delete("/api/daq/conversions/mainBoard/H2-1")
    assert resp.status_code == 200
    assert invalidated == ["mainBoard"]
