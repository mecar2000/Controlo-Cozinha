"""The retained-state arrival stamp behind /api/status's kitchen_state_age_s.

elapsedMs is frozen between the publisher's heartbeats, so a client that
interpolates it locally has to know how old the payload ALREADY was. Without
this age the run clock re-bases on every poll and visibly ratchets up then
snaps back once per heartbeat.
"""

import time

import pytest
from flask import Flask

import app.state as state


@pytest.fixture
def client():
    """Minimal app carrying only the status blueprint, following
    test_meta_route.py. require_auth is a no-op with DASHBOARD_TOKEN empty
    (the documented lab default) and /api/status is a GET, so the origin
    check is skipped too — no auth plumbing needed here."""
    from app.routes.status import bp as status_bp

    flask_app = Flask(__name__)
    flask_app.register_blueprint(status_bp)
    with flask_app.test_client() as c:
        yield c


def test_age_is_none_before_any_state_arrives():
    # reset_state (conftest) calls set_kitchen_state({}), which counts as an
    # arrival, so clear the stamp explicitly to model a cold process.
    state._kitchen_state_received_at = None
    assert state.kitchen_state_age_s() is None


def test_age_is_fresh_right_after_arrival():
    state.set_kitchen_state({"elapsedMs": 10_000})
    age = state.kitchen_state_age_s()
    assert age is not None and age < 0.5


def test_age_grows_while_the_payload_goes_stale():
    state.set_kitchen_state({"elapsedMs": 10_000})
    time.sleep(0.05)
    assert state.kitchen_state_age_s() >= 0.05


def test_a_repeat_publish_resets_the_age():
    """A heartbeat carrying the SAME elapsedMs is still a fresh arrival — the
    value is current again, so the age must drop back to ~0."""
    state.set_kitchen_state({"elapsedMs": 10_000})
    time.sleep(0.05)
    state.set_kitchen_state({"elapsedMs": 10_000})
    assert state.kitchen_state_age_s() < 0.05


def test_status_route_exposes_the_age(client):
    state.set_kitchen_state({"elapsedMs": 10_000})
    body = client.get("/api/status").get_json()
    assert body["kitchen_state"]["elapsedMs"] == 10_000
    assert body["kitchen_state_age_s"] is not None
    assert body["kitchen_state_age_s"] < 1.0
