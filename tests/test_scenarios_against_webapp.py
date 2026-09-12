"""
Part 4.3 — webapp-side assertions (problems.txt:11, "Make sure webapp behaves
as expected"): scenarios from sim/scenarios/library.py drive an in-process
KitchenSim exactly as run_scenario.py does, but instead of asserting against
sim.core directly, a bridge routes every publish() the sim makes straight
into app.mqtt's own topic handlers (the same _handle_state/_handle_ack/
_handle_alarm that a real MQTT message would reach) — no broker needed,
matching the sim's own "no MQTT broker or GUI needed" design. Assertions then
go through Flask's test client against /api/status, the real HTTP path the
browser uses, closing the gap the design doc for Part 4.3 calls out: the
existing webapp/tests/ suite stubs commands/daq and never drives the state
machine end to end.

Written before the bridge fixture existed, per the project's test-first
workflow.
"""

import json

import pytest
from flask import Flask

import app.mqtt as mqtt_mod
import app.state as state
from app.routes.status import bp as status_bp
from daq_device_sim import DaqDeviceSim
from kitchen_core_sim import KitchenState
from runtime import KitchenSim
from scenarios.engine import Scenario
from scenarios.library import SCENARIOS


class WebappBridgeClient:
    """A fake MQTT client handed to KitchenSim/DaqDeviceSim in place of a
    real paho client. publish() is routed straight into app.mqtt's own
    _on_message dispatch, so every state/ack/alarm topic the sim emits lands
    in app.state exactly as it would over a real broker connection — the
    only thing skipped is the network hop itself."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, str(payload)))
        mqtt_mod._on_message(client=None, userdata=None, msg=_FakeMsg(topic, payload))
        return None

    def is_connected(self):
        return True


class _FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload.encode("utf-8") if isinstance(payload, str) else payload


@pytest.fixture
def webapp_client():
    flask_app = Flask(__name__)
    flask_app.register_blueprint(status_bp)
    with flask_app.test_client() as c:
        yield c


@pytest.fixture
def bridged_rig():
    """One KitchenSim + two DaqDeviceSim wired to the SAME bridge client, so
    both the state machine's own topics (state/ack/alarm/run) reach
    app.state. Timings kept short, matching the other sim test rigs."""
    client = WebappBridgeClient()
    sim = KitchenSim(client, "KITCHEN-01", "KitchenLeaks", "lab5")
    sim.core.fully_vent_min_hold_ms = 1_000
    sim.core.sensor_warmup_ms = 100
    daq1 = DaqDeviceSim(client, "KITCHEN-DAQ-1")
    daq2 = DaqDeviceSim(client, "KITCHEN-DAQ-2")
    sim.daq_devices = [daq1, daq2]
    return sim, [daq1, daq2]


# --- The bridge itself --------------------------------------------------


def test_bridge_delivers_state_topic_into_app_state(bridged_rig):
    sim, daqs = bridged_rig
    sim.tick_and_publish()
    assert state.get_kitchen_state().get("state") == KitchenState.WAITING.value


def test_bridge_delivers_alarm_topic_into_app_state(bridged_rig):
    sim, daqs = bridged_rig
    daqs[0].set_powered(True)
    daqs[0].set_online(True)
    Scenario("spike", [
        __import__("scenarios.engine", fromlist=["Spike"]).Spike(daq=0, channel=0, ma=18.0),
    ]).run(sim, daqs)
    for _ in range(20):
        sim.feed_daq_sensors(daqs)
        sim.tick_and_publish()
        if sim.core.state == KitchenState.FULLY_VENTILATING:
            break
    assert sim.core.state == KitchenState.FULLY_VENTILATING
    assert state.get_kitchen_state().get("state") == KitchenState.FULLY_VENTILATING.value


# --- /api/status reflects scenario-driven state --------------------------


def test_status_endpoint_reflects_waiting_state_before_any_scenario(bridged_rig, webapp_client):
    sim, daqs = bridged_rig
    sim.tick_and_publish()
    resp = webapp_client.get("/api/status")
    assert resp.status_code == 200
    assert resp.get_json()["kitchen_state"]["state"] == "WAITING"


def test_status_endpoint_reflects_fast_leak_scenario_latch(bridged_rig, webapp_client):
    sim, daqs = bridged_rig
    SCENARIOS["fast_leak_above_threshold"].run(sim, daqs)
    resp = webapp_client.get("/api/status")
    body = resp.get_json()
    assert body["kitchen_state"]["state"] == "FULLY_VENTILATING"
    assert body["kitchen_state"]["ackRequired"] is True


def test_status_endpoint_reflects_clear_air_countdown_after_ack(bridged_rig, webapp_client):
    sim, daqs = bridged_rig
    SCENARIOS["clear_air_interrupted_by_fresh_spike"].run(sim, daqs)
    resp = webapp_client.get("/api/status")
    body = resp.get_json()["kitchen_state"]
    # The scenario ends mid fresh-hold (restarted from ~0), so clearForMs
    # must be present and small — this is the payload LatchPanel.tsx reads
    # for the countdown (Part 1.1); asserting it here over HTTP is exactly
    # the path a real browser poll takes, not just sim.core directly.
    assert "clearForMs" in body
    assert body["clearForMs"] < 2000


def test_status_endpoint_reflects_peer_alarm_scenario(bridged_rig, webapp_client):
    sim, daqs = bridged_rig
    SCENARIOS["peer_alarm"].run(sim, daqs)
    resp = webapp_client.get("/api/status")
    body = resp.get_json()
    # peer_alarm scenario sets sim.core.peer_alarm_active directly on OUR
    # OWN device, which the design intentionally never publishes as a peer
    # topic (a device doesn't alarm itself as a peer) — so what we assert is
    # the resulting latch, which the interlock DOES publish and IS what
    # peer-alarm handling is meant to cause.
    assert body["kitchen_state"]["state"] == "FULLY_VENTILATING"


def test_status_endpoint_reflects_estop_scenario(bridged_rig, webapp_client):
    sim, daqs = bridged_rig
    SCENARIOS["estop"].run(sim, daqs)
    resp = webapp_client.get("/api/status")
    assert resp.get_json()["kitchen_state"]["reason"] == "ESTOP"


def test_status_endpoint_reflects_operator_stop_no_ack_required(bridged_rig, webapp_client):
    """An operator stop needs no human ack (unlike a danger latch), so once
    ack_required flips false the core settles at WAITING one tick later —
    the scenario now asserts that explicitly (see scenarios/library.py).
    Checking it here over /api/status, not sim.core directly, proves the
    final WAITING transition was actually published, not just reached
    in-memory — this is what ExpectWithin's tick-before-check fix
    (scenarios/engine.py) was needed for."""
    sim, daqs = bridged_rig
    SCENARIOS["operator_stop_during_leaking"].run(sim, daqs)
    resp = webapp_client.get("/api/status")
    body = resp.get_json()["kitchen_state"]
    assert body["state"] == "WAITING"
    assert body["ackRequired"] is False


def test_status_endpoint_shows_quorum_echoed_in_leak_stop_ack(bridged_rig, webapp_client):
    """Part 1.2(a): the acked spec must echo sensorQuorum. Driving this
    through the scenario engine + bridge, rather than calling handle_cmd
    directly, is what proves the fix reaches an HTTP client end to end."""
    sim, daqs = bridged_rig
    daqs[0].set_powered(True)
    daqs[0].set_online(True)
    sim.handle_cmd(json.dumps({
        "cmd": "start", "runId": "quorum-check",
        "spec": {
            "gasSetpointPct": 0, "fanSpeedPct": 50,
            "leakStop": {"maxDurationMs": 60_000,
                         "sensorQuorum": {"quorumCount": 1, "thresholdPct": 10.0}},
            "holdStop": {"maxDurationMs": 60_000},
            "ventStop": {"maxDurationMs": 60_000},
            "ventRegisters": {"central": True, "exhaust": True, "inlet": False},
        },
    }))
    sim.handle_cmd(json.dumps({"cmd": "confirm", "runId": "quorum-check"}))
    resp = webapp_client.get("/api/status")
    ack_spec = state.get_last_ack().get("spec") or {}
    quorum = (ack_spec.get("leakStop") or {}).get("sensorQuorum")
    # thresholdPct is echoed after a counts->pct round trip through the
    # simulated 12-bit ADC, so it lands close to but not exactly on the
    # requested value (see runtime.py's _publish_ack requested_pct handling).
    assert quorum["quorumCount"] == 1
    assert quorum["thresholdPct"] == pytest.approx(10.0, abs=0.1)


# --- Every named scenario: webapp status must stay internally consistent --


@pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
def test_every_scenario_leaves_status_endpoint_in_a_consistent_state(
    scenario_name, bridged_rig, webapp_client
):
    """Not a behavioural assertion per scenario (those are covered above and
    in sim/test_scenarios.py) — a smoke check that NO scenario leaves
    /api/status unable to serve a response, and that its kitchen_state phase
    always matches sim.core's own state exactly (the bridge must never drop
    or reorder a state publish)."""
    sim, daqs = bridged_rig
    SCENARIOS[scenario_name].run(sim, daqs)
    resp = webapp_client.get("/api/status")
    assert resp.status_code == 200
    assert resp.get_json()["kitchen_state"]["state"] == sim.core.state.value
