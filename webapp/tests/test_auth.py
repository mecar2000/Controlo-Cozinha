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


# --- frame-ancestors: only this app and DataAcquisition's dashboard may embed it ---

@pytest.fixture
def framed_client(monkeypatch):
    """A minimal app carrying the after_request hook create_app() installs."""
    monkeypatch.setattr(auth, "DAQ_BASE_URL", "http://localhost:5002")
    monkeypatch.setattr(auth, "FRAME_ANCESTORS", ())
    flask_app = flask.Flask(__name__)

    @flask_app.get("/")
    def page():
        return "<html></html>"

    flask_app.after_request(auth.add_frame_ancestors)
    return flask_app.test_client()


def _ancestors(resp) -> list:
    policy = resp.headers["Content-Security-Policy"]
    assert policy.startswith("frame-ancestors "), policy
    return policy.split()[1:]


def test_frame_ancestors_allow_self_daq_and_daq_on_the_same_host(framed_client):
    """The Cozinha tab's default iframe URL is its own host on our port, so
    DAQ's port on the host the browser used is the dashboard's origin."""
    resp = framed_client.get("/", base_url="http://lab-pc:5010")
    assert _ancestors(resp) == ["'self'", "http://localhost:5002", "http://lab-pc:5002"]


def test_frame_ancestors_do_not_repeat_an_origin(framed_client):
    resp = framed_client.get("/", base_url="http://localhost:5010")
    assert _ancestors(resp) == ["'self'", "http://localhost:5002"]


def test_frame_ancestors_include_configured_extras(framed_client, monkeypatch):
    monkeypatch.setattr(auth, "FRAME_ANCESTORS", ("https://kiosk.lab:8443/", "http://10.0.0.7:5002"))
    resp = framed_client.get("/", base_url="http://localhost:5010")
    assert _ancestors(resp) == [
        "'self'", "http://localhost:5002", "https://kiosk.lab:8443", "http://10.0.0.7:5002",
    ]


def test_frame_ancestors_drop_entries_that_are_not_http_origins(framed_client, monkeypatch):
    """A bad FRAME_ANCESTORS entry must never widen the policy or inject a
    directive: '*', a CSP keyword, a bare host, a ';' or a javascript: URL are
    all dropped, leaving the defaults."""
    monkeypatch.setattr(auth, "FRAME_ANCESTORS", (
        "*", "'unsafe-inline'", "lab-pc:5002", "http://a.lab; script-src *",
        "javascript:alert(1)", "http://bad port:1", "http://x:99999",
    ))
    resp = framed_client.get("/", base_url="http://localhost:5010")
    assert _ancestors(resp) == ["'self'", "http://localhost:5002"]


def test_frame_ancestors_bracket_an_ipv6_host(framed_client):
    resp = framed_client.get("/", base_url="http://[::1]:5010")
    assert "http://[::1]:5002" in _ancestors(resp)


def test_frame_ancestors_on_api_responses_too(framed_client):
    """Set on every response, so an error page or a route added later is
    never frameable by accident."""
    resp = framed_client.get("/nope", base_url="http://localhost:5010")
    assert resp.status_code == 404
    assert "frame-ancestors" in resp.headers["Content-Security-Policy"]
