"""
Tests for app.auth — the token check and the cross-origin refusal.

The origin check matters most when DASHBOARD_TOKEN is empty (the documented
lab default): with auth off, every mutating route would otherwise be reachable
by any page the operator happens to have open, and POST /api/runs/start is a
hydrogen release.
"""

import flask
import pytest

import app.auth as auth


@pytest.fixture
def client(monkeypatch):
    """A minimal app with one guarded GET and one guarded POST."""
    flask_app = flask.Flask(__name__)

    @flask_app.get("/api/thing")
    @auth.require_auth
    def read_thing():
        return {"ok": True}

    @flask_app.post("/api/thing")
    @auth.require_auth
    def write_thing():
        return {"ok": True}

    return flask_app.test_client()


def _no_token(monkeypatch):
    monkeypatch.setattr(auth, "DASHBOARD_TOKEN", "")


def _with_token(monkeypatch, token="s3cret"):
    monkeypatch.setattr(auth, "DASHBOARD_TOKEN", token)


# --- Token -----------------------------------------------------------------


def test_no_token_configured_allows_everything(client, monkeypatch):
    _no_token(monkeypatch)
    assert client.get("/api/thing").status_code == 200


def test_bearer_token_accepted(client, monkeypatch):
    _with_token(monkeypatch)
    resp = client.get("/api/thing", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200


def test_missing_token_rejected(client, monkeypatch):
    _with_token(monkeypatch)
    assert client.get("/api/thing").status_code == 401


def test_wrong_token_rejected(client, monkeypatch):
    _with_token(monkeypatch)
    resp = client.get("/api/thing", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_token_is_not_accepted_as_a_query_parameter(client, monkeypatch):
    """A token in a URL leaks into access logs, Referer headers and browser
    history — and this token authorizes hydrogen commands."""
    _with_token(monkeypatch)
    assert client.get("/api/thing?token=s3cret").status_code == 401


# --- Cross-origin ----------------------------------------------------------


def test_cross_origin_post_refused_even_with_auth_disabled(client, monkeypatch):
    _no_token(monkeypatch)
    monkeypatch.setattr(auth, "ALLOWED_ORIGINS", frozenset())
    resp = client.post("/api/thing", json={}, headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403


def test_same_origin_post_allowed(client, monkeypatch):
    _no_token(monkeypatch)
    monkeypatch.setattr(auth, "ALLOWED_ORIGINS", frozenset())
    resp = client.post(
        "/api/thing", json={}, headers={"Origin": "http://localhost"}
    )
    assert resp.status_code == 200


def test_explicitly_allowed_origin_permitted(client, monkeypatch):
    _no_token(monkeypatch)
    monkeypatch.setattr(auth, "ALLOWED_ORIGINS", frozenset({"http://dash.example"}))
    resp = client.post("/api/thing", json={}, headers={"Origin": "http://dash.example"})
    assert resp.status_code == 200


def test_cross_origin_get_is_not_blocked(client, monkeypatch):
    """Only state changes are origin-checked; a read is harmless."""
    _no_token(monkeypatch)
    monkeypatch.setattr(auth, "ALLOWED_ORIGINS", frozenset())
    resp = client.get("/api/thing", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 200


def test_non_browser_post_without_origin_allowed(client, monkeypatch):
    """curl and the test suite send no Origin — there is nothing to disbelieve."""
    _no_token(monkeypatch)
    assert client.post("/api/thing", json={}).status_code == 200


def test_form_encoded_post_refused(client, monkeypatch):
    """A form POST is the cross-origin request a browser sends without a
    preflight, so it is the CSRF path Origin checking alone can miss."""
    _no_token(monkeypatch)
    resp = client.post("/api/thing", data={"enabled": "true"})
    assert resp.status_code == 415


def test_bodyless_post_allowed(client, monkeypatch):
    """stop/ack/cancel send no body and must not trip the content-type rule."""
    _no_token(monkeypatch)
    assert client.post("/api/thing").status_code == 200


def test_referer_used_when_origin_absent(client, monkeypatch):
    _no_token(monkeypatch)
    monkeypatch.setattr(auth, "ALLOWED_ORIGINS", frozenset())
    resp = client.post(
        "/api/thing", json={}, headers={"Referer": "http://evil.example/page"}
    )
    assert resp.status_code == 403
