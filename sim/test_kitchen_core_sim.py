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


def _build_sim():
    """The `sim` fixture's body as a plain callable — tests that need SEVERAL
    independent sims in one case (comparing two fan speeds' decay curves)
    cannot get them from a single function-scoped fixture."""
    return runtime.KitchenSim(FakeClient(), "KITCHEN-01", "KitchenLeaks", "lab5")


@pytest.fixture
def sim():
    return _build_sim()


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
    rt.daq1 = DaqDeviceSim(device_id="KITCHEN-DAQ-1", transport=sim.client)
    rt.daq2 = DaqDeviceSim(device_id="KITCHEN-DAQ-2", transport=sim.client)

    sim.core.fully_vent_min_hold_ms = 1_000
    data = rt.snapshot()
    assert data["clearForMs"] == 0
    assert data["clearRequiredMs"] == 1_000


def test_snapshot_daq_channels_carry_pin_and_name_not_bare_floats(sim):
    """runtime.py's daqs block used to emit `channels` as bare floats and
    `forced` as list-index positions — meaningless once DaqDeviceSim is keyed
    by pin instead of array index. Each channel entry must now carry the pin
    and sensor name so the GUI can label/address it, and `forced` must list
    pins, not indices."""
    from daq_device_sim import DaqDeviceSim

    rt = object.__new__(runtime.SimRuntime)
    rt.client = sim.client
    rt.sim = sim
    rt.daq1 = DaqDeviceSim(device_id="KITCHEN-DAQ-1", transport=sim.client)
    rt.daq2 = DaqDeviceSim(device_id="KITCHEN-DAQ-2", transport=sim.client)
    rt.daq1.force_leak(3, 3.9)

    data = rt.snapshot()
    daq1 = data["daqs"][0]
    assert daq1["forced"] == [3]
    channels = daq1["channels"]
    assert len(channels) == 8
    for entry in channels:
        assert "pin" in entry and "name" in entry and "volts" in entry
    forced_entry = next(c for c in channels if c["pin"] == 3)
    assert forced_entry["volts"] == pytest.approx(3.9)
    assert forced_entry["name"] == "H2-4"


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
    daq = DaqDeviceSim(device_id="KITCHEN-DAQ-1", transport=client)
    daq.powered = True
    daq.online = True
    daq._tick()

    assert client.topics, "expected 8 channel publishes"
    assert all("/H2-" in t for t in client.topics)
    assert not any("/H2_" in t for t in client.topics)


# --- Automatic leak ramp (problems.txt Area D1: "concentration should
#     increase gradually, and decrease fast once ventilation starts") -------
#
# Only UNFORCED local sensors auto-ramp — a sensor already under console/
# scenario control (force_local_sensor/ramp_local_sensor) is untouched, so
# every existing scenario in sim/scenarios/library.py keeps its exact
# hand-driven timing.


def _start_leak(sim, gas_setpoint_pct=50.0, run_id="r1"):
    sim.handle_cmd(json.dumps({
        "cmd": "start",
        "runId": run_id,
        "spec": {"gasSetpointPct": gas_setpoint_pct, "leakStop": {"maxDurationMs": 60_000}},
    }))
    sim.handle_cmd(json.dumps({"cmd": "confirm", "runId": run_id}))
    # confirm() timestamps warmup_started_ms/state_entered_ms with the REAL
    # now_ms() (via runtime.KitchenSim.handle_cmd), but these tests drive
    # update() with small synthetic `now` values from t=0 — clear the warmup
    # gate directly, mirroring exactly what update()'s own LEAKING branch
    # does once its gate opens, so leak_stop's elapsed-time math (relative to
    # phase_clock_from_ms) lines up with the synthetic clock too.
    sim.core.warmup_pending = False
    sim.core.phase_clock_from_ms = 0
    sim.core._last_integration_ms = 0


def test_unforced_sensor_stays_at_zero_the_instant_leaking_starts(sim):
    """Gradual means not-instant: the very first tick of LEAKING must not
    already show a nonzero reading."""
    _start_leak(sim)
    sim.core.update(0)
    assert sim.core.sensor_counts.get(0, 0) == 0


def test_unforced_sensor_rises_gradually_during_leaking(sim):
    _start_leak(sim)
    sim.core.update(0)  # warmup clears and the ramp phase starts THIS tick
    sim.core.update(5_000)
    early = sim.core.sensor_counts.get(0, 0)
    sim.core.update(sim.core.AUTO_LEAK_RISE_MS)
    late = sim.core.sensor_counts.get(0, 0)
    assert 0 < early < late


def test_unforced_sensor_reaches_gas_setpoint_target_counts(sim):
    """A high enough gas_setpoint_pct must be able to drive counts past the
    default danger threshold, same as a manual Spike — an ungoverned leak has
    to be able to trip FULLY_VENTILATING on its own, not plateau safely."""
    _start_leak(sim, gas_setpoint_pct=100.0)
    sim.core.update(0)
    sim.core.update(sim.core.AUTO_LEAK_RISE_MS * 2)
    assert sim.core.sensor_counts.get(0, 0) >= sim.core.default_threshold_counts


def test_auto_leak_never_trips_fully_ventilating_on_its_own(sim):
    """The auto ramp is DISPLAY ONLY. It drives all six wired indices whether
    or not the operator has those sensors configured, so letting it trip meant
    six phantom sensors crossing the default threshold at once at the end of a
    high-setpoint run — a spike on hardware that isn't there. Only an explicit
    spike (lspike / GUI Spike / scenario Spike) represents a real reading."""
    _start_leak(sim, gas_setpoint_pct=100.0)
    sim.core.update(0)
    sim.core.update(sim.core.AUTO_LEAK_RISE_MS * 2)
    # The reading still climbs past the threshold — it just cannot latch.
    assert sim.core.sensor_counts.get(0, 0) >= sim.core.default_threshold_counts
    # The run may legitimately have advanced LEAKING -> HOLD by now (the
    # default leakStop maxDurationMs elapses within this window); what must
    # NOT happen is a danger latch off the auto ramp alone.
    assert sim.core.state != KitchenState.FULLY_VENTILATING
    assert sim.core.reason == DangerReason.NONE


def test_explicit_spike_still_trips_fully_ventilating(sim):
    """The other half of the above: the spike rig must still latch danger,
    since it is now the ONLY path that can."""
    _start_leak(sim)
    sim.core.update(0)
    sim.core.force_local_sensor(0, 18.0)
    sim.core.update(1_000)
    assert sim.core.state == KitchenState.FULLY_VENTILATING
    assert sim.core.reason == DangerReason.LOCAL_SENSOR_THRESHOLD


def test_manually_forced_sensor_is_never_touched_by_auto_ramp(sim):
    _start_leak(sim)
    sim.core.force_local_sensor(0, 18.0)  # console/scenario override
    sim.core.update(0)
    forced_counts = sim.core.sensor_counts[0]
    sim.core.update(sim.core.AUTO_LEAK_RISE_MS)
    assert sim.core.sensor_counts[0] == forced_counts


def test_unforced_sensor_holds_steady_through_hold(sim):
    # 15% stays under the default danger threshold at AUTO_LEAK_RISE_MS (see
    # test_unforced_sensor_reaches_gas_setpoint_target_counts for the case
    # where a high setpoint DOES trip FULLY_VENTILATING) — this test is about
    # the leak_stop-driven LEAKING -> HOLD path: HOLD must preserve the
    # concentration exactly as it was at the end of LEAKING (fan is off,
    # nothing vents the room), not decay it. leak_stop is long enough that
    # LEAKING is still active at AUTO_LEAK_RISE_MS, so "peak" is read while
    # still LEAKING, then a separate call trips leak_stop -> HOLD.
    _start_leak(sim, gas_setpoint_pct=15.0)
    sim.core.spec.leak_stop.max_duration_ms = sim.core.AUTO_LEAK_RISE_MS + 1_000
    sim.core.update(0)
    sim.core.update(sim.core.AUTO_LEAK_RISE_MS)  # ramp up while LEAKING
    peak = sim.core.sensor_counts.get(0, 0)
    assert peak > 0
    assert sim.core.state == KitchenState.LEAKING

    hold_entered_ms = sim.core.AUTO_LEAK_RISE_MS + 1_100
    sim.core.update(hold_entered_ms)  # leak_stop trips -> HOLD
    assert sim.core.state == KitchenState.HOLD

    sim.core.update(hold_entered_ms + sim.core.AUTO_LEAK_FALL_MS * 5)
    assert sim.core.sensor_counts.get(0, 0) == peak


def test_auto_leak_decays_fast_once_ventilating_starts(sim):
    _start_leak(sim, gas_setpoint_pct=15.0)
    sim.core.spec.leak_stop.max_duration_ms = sim.core.AUTO_LEAK_RISE_MS + 1_000
    sim.core.update(0)
    sim.core.update(sim.core.AUTO_LEAK_RISE_MS)  # ramp up while LEAKING
    peak = sim.core.sensor_counts.get(0, 0)
    assert peak > 0
    assert sim.core.state == KitchenState.LEAKING

    hold_entered_ms = sim.core.AUTO_LEAK_RISE_MS + 1_100
    sim.core.update(hold_entered_ms)  # leak_stop trips -> HOLD
    assert sim.core.state == KitchenState.HOLD
    assert sim.core.sensor_counts.get(0, 0) == peak  # unchanged through HOLD

    sim.core.spec.hold_stop.max_duration_ms = 1_000  # ends HOLD quickly, deterministically
    vent_entered_ms = hold_entered_ms + 1_100
    sim.core.update(vent_entered_ms)  # hold_stop trips -> VENTILATING
    assert sim.core.state == KitchenState.VENTILATING
    assert sim.core.sensor_counts.get(0, 0) == peak  # decay starts observing VENTILATING next tick

    sim.core.update(vent_entered_ms + 1)  # observe VENTILATING, decay phase starts here
    # Exponential decay, so "cleared" is a settle threshold rather than a hard
    # linear deadline — give it several time constants at the commanded fan.
    tau = sim.core._auto_leak_fall_tau_ms()
    sim.core.update(int(vent_entered_ms + 1 + tau * 6))
    assert sim.core.sensor_counts.get(0, 0) == 0


def test_auto_leak_decay_is_gradual_not_instant(sim):
    _start_leak(sim, gas_setpoint_pct=15.0)  # stays under the default danger threshold
    sim.core.spec.leak_stop.max_duration_ms = sim.core.AUTO_LEAK_RISE_MS + 1_000
    sim.core.update(0)
    sim.core.update(sim.core.AUTO_LEAK_RISE_MS)
    peak = sim.core.sensor_counts.get(0, 0)

    hold_entered_ms = sim.core.AUTO_LEAK_RISE_MS + 1_100
    sim.core.update(hold_entered_ms)  # -> HOLD

    sim.core.spec.hold_stop.max_duration_ms = 1_000
    vent_entered_ms = hold_entered_ms + 1_100
    sim.core.update(vent_entered_ms)  # -> VENTILATING
    sim.core.update(vent_entered_ms + 1)  # observe VENTILATING, decay phase starts here
    tau = sim.core._auto_leak_fall_tau_ms()
    sim.core.update(int(vent_entered_ms + 1 + tau / 2))
    midway = sim.core.sensor_counts.get(0, 0)
    assert 0 < midway < peak


def _decay_counts_at(fan_pct, elapsed_ms):
    """Peak-then-VENTILATE one sim at a given commanded fan speed, and read the
    remaining counts `elapsed_ms` into the decay. Returns (remaining, peak)."""
    sim = _build_sim()
    _start_leak(sim, gas_setpoint_pct=15.0)
    sim.core.spec.fan_speed_pct = fan_pct
    sim.core.spec.leak_stop.max_duration_ms = sim.core.AUTO_LEAK_RISE_MS + 1_000
    sim.core.update(0)
    sim.core.update(sim.core.AUTO_LEAK_RISE_MS)
    peak = sim.core.sensor_counts.get(0, 0)

    hold_entered_ms = sim.core.AUTO_LEAK_RISE_MS + 1_100
    sim.core.update(hold_entered_ms)
    sim.core.spec.hold_stop.max_duration_ms = 1_000
    vent_entered_ms = hold_entered_ms + 1_100
    sim.core.update(vent_entered_ms)
    sim.core.update(vent_entered_ms + 1)  # decay phase starts here
    sim.core.update(vent_entered_ms + 1 + elapsed_ms)
    return sim.core.sensor_counts.get(0, 0), peak


def test_decay_is_proportional_to_ventilation_rate():
    """The actual ask: ventilation must clear the room in proportion to how
    hard it is ventilating, not dump everything on a fixed deadline. A 15% fan
    must still be holding meaningful concentration at a moment when a 100% fan
    has essentially cleared."""
    slow, slow_peak = _decay_counts_at(15.0, 5_000)
    fast, fast_peak = _decay_counts_at(100.0, 5_000)
    assert slow_peak == fast_peak          # same leak, same starting point
    assert fast < slow                     # harder ventilation clears faster
    assert slow > slow_peak * 0.3          # 15% fan is nowhere near cleared


def test_full_ventilating_clears_faster_than_a_low_fan_ventilate():
    """FULLY_VENTILATING always commands 100% fan, so a danger purge must
    outpace a low-fan routine VENTILATING phase."""
    assert KitchenCoreSim().AUTO_LEAK_FALL_MS > 0
    slow, _ = _decay_counts_at(10.0, 4_000)
    fast, _ = _decay_counts_at(100.0, 4_000)
    assert fast < slow


def test_auto_leak_fall_is_faster_than_its_rise():
    """The literal ask: decrease FAST relative to the gradual rise."""
    assert KitchenCoreSim().AUTO_LEAK_FALL_MS < KitchenCoreSim().AUTO_LEAK_RISE_MS


# --- DAQ auto-leak ramp: DAQ2 registers lower and slower than DAQ1 ---------


def _fake_client():
    class FakeClient:
        def __init__(self):
            self.topics = []

        def publish(self, topic, payload, qos=0, retain=False):
            self.topics.append(topic)

    return FakeClient()


def test_daq_auto_leak_stays_at_baseline_until_leak_active():
    from daq_device_sim import DaqDeviceSim

    daq = DaqDeviceSim(device_id="KITCHEN-DAQ-1", transport=_fake_client())
    daq.powered = True
    daq.online = True
    before = next(e["volts"] for e in daq.snapshot() if e["pin"] == 0)
    daq._tick()
    after = next(e["volts"] for e in daq.snapshot() if e["pin"] == 0)
    assert after == pytest.approx(before, abs=0.05)


def test_daq_auto_leak_rises_gradually_once_active():
    from daq_device_sim import DaqDeviceSim

    daq = DaqDeviceSim(device_id="KITCHEN-DAQ-1", transport=_fake_client())
    daq.powered = True
    daq.online = True
    daq.set_leak_active(True, now_s=0.0)
    v0 = next(e["volts"] for e in daq.snapshot(now_s=0.0) if e["pin"] == 0)
    v_mid = next(e["volts"] for e in daq.snapshot(now_s=daq.auto_leak_rise_ms / 1000 / 2) if e["pin"] == 0)
    v_full = next(e["volts"] for e in daq.snapshot(now_s=daq.auto_leak_rise_ms / 1000) if e["pin"] == 0)
    assert v0 < v_mid < v_full
    assert v_full == pytest.approx(daq.auto_leak_max_v, abs=0.05)


def test_daq_auto_leak_decays_fast_once_cleared():
    from daq_device_sim import DaqDeviceSim

    daq = DaqDeviceSim(device_id="KITCHEN-DAQ-1", transport=_fake_client())
    daq.powered = True
    daq.online = True
    daq.set_leak_active(True, now_s=0.0)
    peak = next(e["volts"] for e in daq.snapshot(now_s=daq.auto_leak_rise_ms / 1000) if e["pin"] == 0)

    daq.set_leak_active(False, now_s=daq.auto_leak_rise_ms / 1000)
    # Exponential now, so one time constant still leaves ~37% of the amplitude
    # — settling takes several. auto_leak_fall_ms is the tau at 100% fan, which
    # is what set_leak_active defaults to when no fan speed is supplied.
    fell = next(
        e["volts"] for e in daq.snapshot(
            now_s=daq.auto_leak_rise_ms / 1000 + (daq.auto_leak_fall_ms / 1000) * 6
        ) if e["pin"] == 0
    )
    assert fell < peak
    assert fell == pytest.approx(daq._baseline_locked(0), abs=0.1)


def test_daq_decay_is_proportional_to_fan_speed():
    """The DAQ voltages must purge in proportion to the commanded fan, the
    same way the local sensors do — not dump on a fixed deadline."""
    from daq_device_sim import DaqDeviceSim

    def remaining_at(fan_pct):
        daq = DaqDeviceSim(device_id="KITCHEN-DAQ-1", transport=_fake_client())
        daq.powered = True
        daq.online = True
        daq.set_leak_active(True, now_s=0.0)
        rise_s = daq.auto_leak_rise_ms / 1000
        daq.set_leak_active(False, now_s=rise_s, fan_speed_pct=fan_pct)
        v = next(e["volts"] for e in daq.snapshot(now_s=rise_s + 5.0) if e["pin"] == 0)
        return v - daq._baseline_locked(0)

    assert remaining_at(100.0) < remaining_at(15.0)


def test_daq_manual_force_leak_overrides_auto_ramp():
    from daq_device_sim import DaqDeviceSim

    daq = DaqDeviceSim(device_id="KITCHEN-DAQ-1", transport=_fake_client())
    daq.powered = True
    daq.online = True
    daq.set_leak_active(True, now_s=0.0)
    daq.force_leak(0, 4.0)
    forced = next(e["volts"] for e in daq.snapshot(now_s=100.0) if e["pin"] == 0)
    assert forced == pytest.approx(4.0)


def test_daq_auto_leak_kwargs_configure_max_and_rate():
    """DaqDeviceSim itself has no notion of "DAQ-1 vs DAQ-2" — the
    constructor kwargs are what let a caller (runtime.py) give one instance a
    lower ceiling and a slower rise than another. Covered end-to-end by
    test_runtime_wires_daq2_lower_and_slower_than_daq1 below."""
    from daq_device_sim import DaqDeviceSim

    daq = DaqDeviceSim(device_id="X", transport=_fake_client(),
                        auto_leak_max_v=2.5, auto_leak_rise_ms=45_000)
    assert daq.auto_leak_max_v == 2.5
    assert daq.auto_leak_rise_ms == 45_000


def test_runtime_wires_daq2_lower_and_slower_than_daq1():
    rt = object.__new__(runtime.SimRuntime)
    from daq_device_sim import DaqDeviceSim

    # Mirrors what SimRuntime.__init__ constructs — asserts the wiring, not
    # just each class's own defaults, so a future refactor that hardcodes
    # matching kwargs for both instances fails loudly here.
    daq1 = DaqDeviceSim(device_id="KITCHEN-DAQ-1", transport=_fake_client(),
                         **runtime.DAQ1_AUTO_LEAK_KWARGS)
    daq2 = DaqDeviceSim(device_id="KITCHEN-DAQ-2", transport=_fake_client(),
                         **runtime.DAQ2_AUTO_LEAK_KWARGS)
    assert daq2.auto_leak_max_v < daq1.auto_leak_max_v
    assert daq2.auto_leak_rise_ms > daq1.auto_leak_rise_ms


def test_daq_leak_active_stays_true_through_hold(sim):
    """tick_and_publish()'s leak_active must track LEAKING-or-HOLD, not just
    LEAKING — otherwise DAQ voltages start falling the instant HOLD begins,
    same bug as the local-sensor auto-ramp (see
    test_unforced_sensor_holds_steady_through_hold)."""
    from daq_device_sim import DaqDeviceSim

    sim.daq_devices = [DaqDeviceSim(device_id="KITCHEN-DAQ-1", transport=_fake_client())]
    daq = sim.daq_devices[0]
    daq.powered = True
    daq.online = True

    sim.core.state = KitchenState.LEAKING
    sim.core.warmup_pending = False
    sim.tick_and_publish()
    assert daq._leak_active is True

    sim.core.state = KitchenState.HOLD
    sim.tick_and_publish()
    assert daq._leak_active is True  # still driving toward the leak level, not falling

    sim.core.state = KitchenState.VENTILATING
    sim.tick_and_publish()
    assert daq._leak_active is False  # only now does it fall
