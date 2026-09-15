"""
routes.meta — exposes the backend's own device-id settings to the frontend,
so a value like KITCHEN_DAQ_DEVICE_ID ("mainBoard") lives in exactly one
place (app/config.py) instead of also being hardcoded in
SensorPanel.tsx's KITCHEN_DEVICE_ID constant.

Follows test_daq_proxy_devices.py's pattern: a minimal Flask app carrying
only this blueprint, no MySQL/MQTT/DataAcquisition needed.
"""

import pytest
from flask import Flask


@pytest.fixture
def client(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "KITCHEN_DAQ_DEVICE_ID", "mainBoard")
    monkeypatch.setattr(config, "KITCHEN_DEVICE_ID", "KITCHEN-01")
    monkeypatch.setattr(config, "DAQ_BASE_URL", "http://localhost:5001")

    from app.routes.meta import bp as meta_bp

    flask_app = Flask(__name__)
    flask_app.register_blueprint(meta_bp)
    with flask_app.test_client() as c:
        yield c


def test_meta_reports_the_configured_device_ids(client):
    resp = client.get("/api/meta")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {
        "kitchen_daq_device_id": "mainBoard",
        "kitchen_device_id": "KITCHEN-01",
        "daq_dashboard_url": "http://localhost:5001",
    }


def test_meta_reflects_a_non_default_env_value(client, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "KITCHEN_DAQ_DEVICE_ID", "some-other-board")
    resp = client.get("/api/meta")
    assert resp.get_json()["kitchen_daq_device_id"] == "some-other-board"


def test_meta_reports_the_daq_dashboard_url_from_daq_base_url(client, monkeypatch):
    """DataAcquisition's dashboard UI and REST API are the SAME Flask app on
    the SAME port (dashboard/server.py serves both) — so the link the
    frontend needs is just DAQ_BASE_URL itself, not a second setting to keep
    in sync with it."""
    import app.config as config

    monkeypatch.setattr(config, "DAQ_BASE_URL", "http://daq.lab.local:5001")
    resp = client.get("/api/meta")
    assert resp.get_json()["daq_dashboard_url"] == "http://daq.lab.local:5001"
