"""
routes.daq_proxy's device/pin viewer (Part 5, Stage 1) — read-only, so this
file only covers GET /api/daq/devices. Follows test_daq_proxy_conversions.py
exactly: a minimal Flask app carrying only this blueprint, no MySQL/MQTT/
DataAcquisition needed.
"""

import pytest
from flask import Flask

import app.daq as daq
from app.routes.daq_proxy import bp as daq_proxy_bp


@pytest.fixture
def client():
    flask_app = Flask(__name__)
    flask_app.register_blueprint(daq_proxy_bp)
    with flask_app.test_client() as c:
        yield c


def test_devices_returns_the_unwrapped_device_list(client, monkeypatch):
    devices = [
        {
            "device_id": "mainBoard",
            "location": "Kitchen",
            "status": "online",
            "expansions": 1,
            "base_pins": 8,
            "interval_ms": 1000,
            "config": {"sensors": [{"pin": 100, "name": "H2-1", "type": "current"}]},
        }
    ]
    monkeypatch.setattr(daq, "list_devices", lambda: devices)
    resp = client.get("/api/daq/devices")
    assert resp.status_code == 200
    assert resp.get_json() == devices


def test_devices_daq_unreachable_returns_502(client, monkeypatch):
    def _boom():
        raise daq.DaqUnreachable("DAQ down")
    monkeypatch.setattr(daq, "list_devices", _boom)
    resp = client.get("/api/daq/devices")
    assert resp.status_code == 502


def test_devices_daq_rejected_passes_status_through(client, monkeypatch):
    def _boom():
        raise daq.DaqRejected("not found", 404)
    monkeypatch.setattr(daq, "list_devices", _boom)
    resp = client.get("/api/daq/devices")
    assert resp.status_code == 404


# --- POST /api/daq/devices/<device_id>/config (Part 5, Stage 2) ------------
#
# Validates BEFORE proxying to DataAcquisition — the existing set_conversion
# route is the model (see test_daq_proxy_conversions.py): a 400 with a clear
# message here beats letting DAQ's own error be the operator's first
# encounter with the mistake.

_VALID_SENSORS = [{"pin": 0, "name": "H2-1", "type": "current"}]


def test_push_config_rejects_empty_sensor_list(client, monkeypatch):
    monkeypatch.setattr(daq, "push_config", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not call DAQ")))
    resp = client.post(
        "/api/daq/devices/mainBoard/config",
        json={"sensors": []},
    )
    assert resp.status_code == 400
    assert "sensors" in resp.get_json()["error"].lower()


def test_push_config_rejects_non_integer_pin(client, monkeypatch):
    monkeypatch.setattr(daq, "push_config", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not call DAQ")))
    resp = client.post(
        "/api/daq/devices/mainBoard/config",
        json={"sensors": [{"pin": "A0", "name": "H2-1", "type": "current"}]},
    )
    assert resp.status_code == 400
    assert "pin" in resp.get_json()["error"].lower()


def test_push_config_rejects_duplicate_pins(client, monkeypatch):
    monkeypatch.setattr(daq, "push_config", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not call DAQ")))
    resp = client.post(
        "/api/daq/devices/mainBoard/config",
        json={"sensors": [
            {"pin": 0, "name": "H2-1", "type": "current"},
            {"pin": 0, "name": "H2-2", "type": "current"},
        ]},
    )
    assert resp.status_code == 400
    assert "duplicate" in resp.get_json()["error"].lower()


def test_push_config_rejects_missing_sensors_field(client, monkeypatch):
    monkeypatch.setattr(daq, "push_config", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not call DAQ")))
    resp = client.post("/api/daq/devices/mainBoard/config", json={})
    assert resp.status_code == 400


def test_push_config_reaches_daq_with_the_complete_sensor_list(client, monkeypatch):
    calls = []
    monkeypatch.setattr(daq, "push_config", lambda *a, **kw: calls.append((a, kw)) or {"ok": True})
    resp = client.post(
        "/api/daq/devices/mainBoard/config",
        json={"sensors": _VALID_SENSORS, "interval_ms": 500},
    )
    assert resp.status_code == 200
    args, kwargs = calls[0]
    assert args[0] == "mainBoard"
    assert args[1] == _VALID_SENSORS
    assert kwargs.get("interval_ms") == 500


def test_push_config_daq_rejected_passes_status_through(client, monkeypatch):
    def _boom(*_a, **_kw):
        raise daq.DaqRejected("pin E0:CH0 references expansion 0 which is not present", 400)
    monkeypatch.setattr(daq, "push_config", _boom)
    resp = client.post(
        "/api/daq/devices/mainBoard/config",
        json={"sensors": _VALID_SENSORS},
    )
    assert resp.status_code == 400


# --- DELETE /api/daq/devices/<device_id> ------------------------------------
#
# Proxies DataAcquisition's own DELETE /devices/<id> (dashboard/app/routes/
# core.py::delete_device_route) — permanently removes the device's config,
# sensor list and stored conversions in DataAcquisition. Does not touch
# Cozinha's own sensor_config: a sensor still pointing at the deleted device
# becomes exactly the existing "bound to a pin the device no longer reports"
# case the frontend's device pane already surfaces, so no cascade is needed
# here — same reasoning as a pin being removed from a device's config.

def test_delete_device_reaches_daq_with_the_device_id(client, monkeypatch):
    calls = []
    monkeypatch.setattr(daq, "delete_device", lambda *a, **kw: calls.append((a, kw)) or {"ok": True})
    resp = client.delete("/api/daq/devices/mainBoard")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    args, _kwargs = calls[0]
    assert args[0] == "mainBoard"


def test_delete_device_daq_rejected_passes_status_through(client, monkeypatch):
    def _boom(*_a, **_kw):
        raise daq.DaqRejected("unknown device_id 'never-seen'", 404)
    monkeypatch.setattr(daq, "delete_device", _boom)
    resp = client.delete("/api/daq/devices/never-seen")
    assert resp.status_code == 404


def test_delete_device_daq_unreachable_returns_502(client, monkeypatch):
    def _boom(*_a, **_kw):
        raise daq.DaqUnreachable("DAQ down")
    monkeypatch.setattr(daq, "delete_device", _boom)
    resp = client.delete("/api/daq/devices/mainBoard")
    assert resp.status_code == 502
