"""Unit tests for app.commands: the two-phase start/ack/confirm sequence and
the invariant that stop() never depends on a stored connection state."""

import json

import pytest

import app.commands as commands
import app.state as state


class FakePublishResult:
    pass


@pytest.fixture
def fake_mqtt(monkeypatch):
    """Captures every publish() call; simulates the broker being up."""
    calls = []

    def fake_publish(topic, payload, qos=1, retain=False):
        calls.append({"topic": topic, "payload": json.loads(payload), "qos": qos, "retain": retain})
        return FakePublishResult()

    monkeypatch.setattr(commands, "publish", fake_publish)
    state.set_mqtt_connected(True)
    return calls


def test_start_publishes_unretained_with_generated_run_id(fake_mqtt):
    run_id = commands.start({"gasSetpointPct": 10})
    assert len(fake_mqtt) == 1
    call = fake_mqtt[0]
    assert call["retain"] is False  # commands are NEVER retained
    assert call["payload"]["cmd"] == "start"
    assert call["payload"]["runId"] == run_id
    assert call["payload"]["spec"] == {"gasSetpointPct": 10}
    assert state.get_pending_run() == run_id


def test_stop_does_not_require_a_pending_run(fake_mqtt):
    """Stop must work regardless of two-phase-start bookkeeping state."""
    commands.stop()
    assert fake_mqtt[0]["payload"] == {"cmd": "stop"}


def test_ack_sends_plain_ack_command(fake_mqtt):
    commands.ack()
    assert fake_mqtt[0]["payload"] == {"cmd": "ack"}


def test_wait_for_ack_matches_on_run_id(fake_mqtt):
    run_id = commands.start({})
    state.set_last_ack({"runId": "some-other-run", "valid": True})
    assert commands.wait_for_ack(run_id, timeout_s=0.3) is None

    state.set_last_ack({"runId": run_id, "valid": True, "spec": {}})
    ack = commands.wait_for_ack(run_id, timeout_s=0.3)
    assert ack["runId"] == run_id


def test_wait_for_ack_times_out_with_no_ack(fake_mqtt):
    run_id = commands.start({})
    assert commands.wait_for_ack(run_id, timeout_s=0.2) is None


def test_confirm_clears_pending_run(fake_mqtt):
    run_id = commands.start({})
    commands.confirm(run_id)
    assert fake_mqtt[-1]["payload"] == {"cmd": "confirm", "runId": run_id}
    assert state.get_pending_run() is None


def test_publish_raises_when_mqtt_down(monkeypatch):
    monkeypatch.setattr(commands, "publish", lambda *a, **k: None)
    with pytest.raises(commands.CommandError):
        commands.stop()
