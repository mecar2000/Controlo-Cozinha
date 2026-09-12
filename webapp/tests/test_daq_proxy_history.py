"""
routes.daq_proxy's /history/experiment* endpoints — built against a minimal
Flask app carrying only this blueprint (see test_daq_proxy_conversions.py's
docstring for why: no MySQL/MQTT/DataAcquisition needed).

Covers problems.txt's "GET /history/experiment: id param required": the
outbound rename lives in app.daq (see test_daq_response_shapes.py) — these
tests cover the Flask route layer in front of it, which must still accept
`experiment_id` from the frontend/proxy side (renamed only at the
DataAcquisition boundary) and must require `stage` for the one endpoint that
DataAcquisition itself requires it for.
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


def test_history_experiment_requires_experiment_id(client):
    resp = client.get("/api/daq/history/experiment")
    assert resp.status_code == 400
    assert "experiment_id" in resp.get_json()["error"]


def test_history_experiment_forwards_to_daq_with_renamed_param(client, monkeypatch):
    calls = []
    monkeypatch.setattr(daq, "get_history_experiment", lambda exp_id, **kw: calls.append((exp_id, kw)) or {})
    resp = client.get("/api/daq/history/experiment?experiment_id=42")
    assert resp.status_code == 200
    assert calls[0][0] == 42


def test_history_window_requires_all_three_params(client):
    resp = client.get("/api/daq/history/experiment/window?experiment_id=1&start_ms=0")
    assert resp.status_code == 400


def test_history_window_rejects_end_before_start(client):
    resp = client.get("/api/daq/history/experiment/window?experiment_id=1&start_ms=100&end_ms=0")
    assert resp.status_code == 400
    assert "end_ms" in resp.get_json()["error"]


def test_history_stage_requires_experiment_id(client):
    resp = client.get("/api/daq/history/experiment/stage?stage=run1-leak")
    assert resp.status_code == 400
    assert "experiment_id" in resp.get_json()["error"]


def test_history_stage_requires_stage(client):
    """This app previously called DataAcquisition's stage-history endpoint
    with no `stage` at all — it 400s there regardless of the id/experiment_id
    naming, since DataAcquisition requires both. Catch the missing param here
    instead of forwarding a request guaranteed to fail."""
    resp = client.get("/api/daq/history/experiment/stage?experiment_id=1")
    assert resp.status_code == 400
    assert "stage" in resp.get_json()["error"]


def test_history_stage_forwards_both_params(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        daq, "get_history_experiment_stage", lambda exp_id, stage: calls.append((exp_id, stage)) or {}
    )
    resp = client.get("/api/daq/history/experiment/stage?experiment_id=1&stage=run1-leak")
    assert resp.status_code == 200
    assert calls[0] == (1, "run1-leak")


def test_list_stages_is_the_boundary_listing_endpoint(client, monkeypatch):
    """The endpoint the frontend should use for 'what stages exist in this
    run' — distinct from history_stage above, which returns READINGS within
    one named stage."""
    monkeypatch.setattr(daq, "list_stages", lambda exp_id: [{"stage": "run1-leak", "count": 10}])
    resp = client.get("/api/daq/experiments/1/stages")
    assert resp.status_code == 200
    assert resp.get_json() == [{"stage": "run1-leak", "count": 10}]
