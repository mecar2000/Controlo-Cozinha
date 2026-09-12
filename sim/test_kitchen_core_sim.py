"""
Regression tests for the problems.txt fixes made directly to the simulator:

  - clear_for_ms()/timing overrides on KitchenCoreSim (problems.txt:1, :8 —
    "make fixed timings small for tests", "clear air timer doesn't decrease")
  - the quorum %->counts mapping in runtime.KitchenSim._parse_spec, and the
    acked spec now echoing sensorQuorum (problems.txt:25 — "why didn't it
    change the quorum?")

Run from sim/: python -m pytest test_kitchen_core_sim.py -q
"""

import json

import pytest

import runtime
from kitchen_core_sim import DangerReason, KitchenCoreSim, KitchenState, RunSpec, StopCondition


class FakeClient:
    """Just enough of paho's Client interface for KitchenSim to publish to."""

    def __init__(self):
        self.published: dict[str, str] = {}

    def publish(self, topic, payload, qos=0, retain=False):
        self.published[topic] = payload

    def is_connected(self):
        return True


@pytest.fixture
def sim():
    return runtime.KitchenSim(FakeClient(), "KITCHEN-01", "KitchenLeaks", "lab5")


# --- Timing overrides (problems.txt:1) --------------------------------------


def test_constructor_kwargs_override_module_defaults():
    core = KitchenCoreSim(fully_vent_min_hold_ms=10_000, sensor_warmup_ms=5_000, arm_timeout_ms=2_000)
    assert core.fully_vent_min_hold_ms == 10_000
    assert core.sensor_warmup_ms == 5_000
    assert core.arm_timeout_ms == 2_000


def test_default_timings_match_real_hardware_when_unoverridden():
    """The defaults must still match Kitchen_Settings.h — only an explicit
    override (env var, in runtime.py) may shorten them."""
    core = KitchenCoreSim()
    assert core.fully_vent_min_hold_ms == 300_000
    assert core.sensor_warmup_ms == 70_000


def test_timing_env_reads_seconds_and_converts_to_ms(monkeypatch):
    monkeypatch.setenv("SIM_FULLY_VENT_HOLD_S", "10")
    assert runtime._timing_env("SIM_FULLY_VENT_HOLD_S", 300_000) == 10_000


def test_timing_env_falls_back_on_garbage(monkeypatch, capsys):
    monkeypatch.setenv("SIM_FULLY_VENT_HOLD_S", "not-a-number")
    assert runtime._timing_env("SIM_FULLY_VENT_HOLD_S", 300_000) == 300_000


def test_timing_env_unset_uses_default(monkeypatch):
    monkeypatch.delenv("SIM_FULLY_VENT_HOLD_S", raising=False)
    assert runtime._timing_env("SIM_FULLY_VENT_HOLD_S", 300_000) == 300_000


# --- Clear-air countdown (problems.txt:8) -----------------------------------


def test_clear_for_ms_is_zero_when_condition_not_clear():
    core = KitchenCoreSim()
    assert core.clear_for_ms(12_345) == 0


def test_clear_for_ms_counts_up_once_clear():
    core = KitchenCoreSim(fully_vent_min_hold_ms=5_000)
    core.sensor_counts = {0: 4000}
    core.update(0)
    assert core.state == KitchenState.FULLY_VENTILATING
    assert core.clear_for_ms(0) == 0  # danger still active this same tick

    core.sensor_counts = {}
    core.human_ack(0)
    core.update(100)
    assert core.clear_for_ms(100) == 0  # clear_since_ms was just set to 100
    assert core.clear_for_ms(2100) == 2000


def test_clear_since_resets_on_a_fresh_spike_mid_hold():
    """A spike partway through the hold must restart the countdown, not just
    pause it — mirrors KitchenCore::update()'s unconditional reset on danger."""
    core = KitchenCoreSim(fully_vent_min_hold_ms=5_000)
    core.sensor_counts = {0: 4000}
    core.update(0)
    core.sensor_counts = {}
    core.human_ack(0)
    core.update(1000)
    assert core.clear_for_ms(3000) == 2000

    # Fresh spike at t=3000
    core.sensor_counts = {0: 4000}
    core.update(3000)
    core.sensor_counts = {}
    core.update(3100)
    assert core.clear_for_ms(3100) == 0  # restarted, not continued from 2000


def test_auto_exits_waiting_once_hold_satisfied():
    core = KitchenCoreSim(fully_vent_min_hold_ms=1_000)
    core.sensor_counts = {0: 4000}
    core.update(0)
    core.sensor_counts = {}
    core.human_ack(0)
    core.update(100)
    core.update(999)
    assert core.state == KitchenState.FULLY_VENTILATING
    core.update(1101)
    assert core.state == KitchenState.WAITING


def test_state_payload_carries_clear_for_ms_and_required(sim):
    sim.core.fully_vent_min_hold_ms = 1_000
    sim.core.sensor_counts = {0: 4000}
    sim.tick_and_publish()
    payload = json.loads(sim.client.published[sim.topic_state])
    assert payload["clearForMs"] == 0
    assert payload["clearRequiredMs"] == 1_000


def test_state_payload_clear_for_ms_is_quantised_to_whole_seconds(sim):
    """Published on change-detect (runtime.py) — an unrounded ms value would
    republish every tick at TICK_HZ instead of once a second."""
    sim.core.fully_vent_min_hold_ms = 5_000
    sim.core.sensor_counts = {0: 4000}
    sim.tick_and_publish()
    sim.core.sensor_counts = {}
    sim.core.human_ack(runtime.now_ms())
    sim.core.clear_since_ms = runtime.now_ms() - 1499  # 1.499s clear so far
    sim.tick_and_publish()
    payload = json.loads(sim.client.published[sim.topic_state])
    assert payload["clearForMs"] == 1000  # floored to the whole second


def test_snapshot_carries_clear_for_ms(sim):
    """snapshot() lives on SimRuntime, not KitchenSim — built here without
    __init__ (which opens a real MQTT connection) since it only needs the
    handful of attributes snapshot() actually reads."""
    from daq_device_sim import DaqDeviceSim

    rt = object.__new__(runtime.SimRuntime)
    rt.client = sim.client
    rt.sim = sim
    rt.daq1 = DaqDeviceSim(sim.client, "KITCHEN-DAQ-1")
    rt.daq2 = DaqDeviceSim(sim.client, "KITCHEN-DAQ-2")

    sim.core.fully_vent_min_hold_ms = 1_000
    data = rt.snapshot()
    assert data["clearForMs"] == 0
    assert data["clearRequiredMs"] == 1_000


# --- Quorum mapping + ack echo (problems.txt:25) ----------------------------


def _start_cmd(quorum_count=2, threshold_pct=10.0, run_id="r1"):
    return json.dumps({
        "cmd": "start",
        "runId": run_id,
        "spec": {
            "gasSetpointPct": 5,
            "leakStop": {
                "maxDurationMs": 5000,
                "sensorQuorum": {"quorumCount": quorum_count, "thresholdPct": threshold_pct},
            },
        },
    })


def test_quorum_pct_to_counts_matches_the_sensor_feed_mapping(sim):
    """Both must agree on the live-zero: 4mA=0 counts .. 20mA=4095 counts.
    They used to disagree (this one used ma/20, ~819 counts too high at
    4mA), so a 10% threshold silently became ~18.4% before this fix."""
    sim.handle_cmd(_start_cmd(quorum_count=2, threshold_pct=10.0))
    assert sim.core.spec.leak_stop.quorum_threshold_pct == 409  # not 1146


def test_a_sensor_reading_exactly_at_the_requested_percent_trips_the_quorum(sim):
    sim.handle_cmd(_start_cmd(quorum_count=2, threshold_pct=10.0))
    sim.core.sensor_counts = {0: 409, 1: 409}  # exactly the 10% counts value
    assert sim.core._stop_condition_met(sim.core.spec.leak_stop, 0) is True


def test_acked_spec_echoes_the_quorum(sim):
    sim.handle_cmd(_start_cmd(quorum_count=2, threshold_pct=10.0))
    ack = json.loads(sim.client.published[sim.topic_ack])
    quorum = ack["spec"]["leakStop"]["sensorQuorum"]
    assert quorum["quorumCount"] == 2
    assert quorum["thresholdPct"] == pytest.approx(10.0, abs=0.1)


def test_acked_spec_quorum_is_zero_when_not_requested(sim):
    sim.handle_cmd(_start_cmd(quorum_count=0, threshold_pct=0.0))
    ack = json.loads(sim.client.published[sim.topic_ack])
    quorum = ack["spec"]["leakStop"]["sensorQuorum"]
    assert quorum == {"quorumCount": 0, "thresholdPct": 0.0}


# --- H2-N naming must match firmware (problems.txt: sim published H2_N with
#     an underscore, which no calibration is ever keyed on) ------------------


def test_daq_sim_publishes_hyphenated_sensor_names():
    from daq_device_sim import DaqDeviceSim

    class FakeClient:
        def __init__(self):
            self.topics = []

        def publish(self, topic, payload, qos=0, retain=False):
            self.topics.append(topic)

    client = FakeClient()
    daq = DaqDeviceSim(client, "KITCHEN-DAQ-1")
    daq.powered = True
    daq.online = True
    daq._tick()

    assert client.topics, "expected 8 channel publishes"
    assert all("/H2-" in t for t in client.topics)
    assert not any("/H2_" in t for t in client.topics)
