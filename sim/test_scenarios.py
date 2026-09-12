"""
Tests for the headless scenario engine (problems.txt:11 — "make some
predefined tests in sim when leaks happening ... define multiple test
scenarios to test all types of situations. Dont need GUI for that.").

Written BEFORE scenarios/engine.py and scenarios/library.py exist, per the
project's test-first workflow — these define the contract those modules must
satisfy. Run with: python -m pytest test_scenarios.py -q (will fail to
collect until the scenarios/ package exists).

Design (see chat decisions):
  - Ramps run in REAL wall-clock time via DaqDeviceSim's own background
    thread (leaks are kept <=30s, so this stays fast enough for CI/local use
    — no virtual clock needed).
  - The engine drives KitchenSim + DaqDeviceSim in-process, no MQTT broker
    required, so `python run_scenario.py <name>` needs nothing running.
  - A scenario is a list of typed steps; ExpectState/ExpectWithin raise
    ScenarioFailed on violation, with enough detail to see why in a CI log.
"""

import time

import pytest

from daq_device_sim import DaqDeviceSim
from kitchen_core_sim import KitchenState
from runtime import KitchenSim
from scenarios.engine import (
    ExpectState,
    ExpectWithin,
    Hold,
    Ramp,
    Scenario,
    ScenarioFailed,
    SetOnline,
    SetPowered,
    Spike,
)


class FakeClient:
    """Enough of paho's Client interface for KitchenSim/DaqDeviceSim to run
    against with no broker — same fixture shape as test_kitchen_core_sim.py."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))
        return None

    def is_connected(self):
        return True


@pytest.fixture
def rig():
    """One KitchenSim + two DaqDeviceSim instances wired together like
    SimRuntime does, minus the MQTT connection and background tick thread —
    the engine drives ticking itself."""
    client = FakeClient()
    sim = KitchenSim(client, "KITCHEN-01", "KitchenLeaks", "lab5")
    sim.core.fully_vent_min_hold_ms = 1_000  # keep the suite fast
    sim.core.sensor_warmup_ms = 100
    daq1 = DaqDeviceSim(client, "KITCHEN-DAQ-1")
    daq2 = DaqDeviceSim(client, "KITCHEN-DAQ-2")
    sim.daq_devices = [daq1, daq2]
    return sim, [daq1, daq2]


# --- DaqDeviceSim.ramp() -----------------------------------------------------


def test_ramp_moves_linearly_from_start_to_end_value():
    daq = DaqDeviceSim(None, "d1")
    daq.ramp(0, from_ma=4.0, to_ma=12.0, duration_s=0.3)
    daq.powered = True
    daq.online = True

    daq._tick()
    mid = daq.snapshot()[0]
    time.sleep(0.3)
    daq._tick()
    end = daq.snapshot()[0]

    assert 4.0 <= mid <= 12.0
    assert end == pytest.approx(12.0, abs=0.5)


def test_ramp_holds_at_target_after_duration_elapses():
    daq = DaqDeviceSim(None, "d1")
    daq.ramp(0, from_ma=4.0, to_ma=8.0, duration_s=0.05)
    daq.powered = True
    daq.online = True
    time.sleep(0.15)
    daq._tick()
    assert daq.snapshot()[0] == pytest.approx(8.0, abs=0.1)
    # A second tick well past the ramp must not overshoot or drift.
    time.sleep(0.05)
    daq._tick()
    assert daq.snapshot()[0] == pytest.approx(8.0, abs=0.1)


def test_ramp_can_be_cancelled_by_clear_force():
    daq = DaqDeviceSim(None, "d1")
    daq.ramp(0, from_ma=4.0, to_ma=20.0, duration_s=5.0)
    daq.clear_force(0)
    daq.powered = True
    daq.online = True
    daq._tick()
    # Back to the random walk around the clean-air baseline, not mid-ramp.
    assert daq.snapshot()[0] < 6.0


def test_force_leak_still_overrides_a_ramp():
    """force_leak (an instant pin) and ramp both write into the same
    override slot — whichever was called last wins, same as before."""
    daq = DaqDeviceSim(None, "d1")
    daq.ramp(0, from_ma=4.0, to_ma=20.0, duration_s=5.0)
    daq.force_leak(0, 15.0)
    daq._tick()
    assert daq.snapshot()[0] == 15.0


# --- Scenario engine steps ---------------------------------------------------


def test_hold_advances_real_time_without_changing_readings(rig):
    sim, daqs = rig
    Scenario("noop", [Hold(seconds=0.05)]).run(sim, daqs)
    assert sim.core.state == KitchenState.WAITING


def test_spike_trips_the_danger_threshold(rig):
    sim, daqs = rig
    daqs[0].set_powered(True)
    daqs[0].set_online(True)
    Scenario(
        "spike",
        [
            Spike(daq=0, channel=0, ma=12.0),
            Hold(seconds=0.05),
            ExpectState(KitchenState.FULLY_VENTILATING),
        ],
    ).run(sim, daqs)


def test_expect_state_raises_scenario_failed_on_mismatch(rig):
    sim, daqs = rig
    with pytest.raises(ScenarioFailed, match="WAITING"):
        Scenario("bad", [ExpectState(KitchenState.LEAKING)]).run(sim, daqs)


def test_expect_within_passes_once_condition_becomes_true(rig):
    sim, daqs = rig
    daqs[0].set_powered(True)
    daqs[0].set_online(True)
    Scenario(
        "eventually-danger",
        [
            Spike(daq=0, channel=0, ma=12.0),
            ExpectWithin(seconds=1.0, condition=lambda s: s.core.state == KitchenState.FULLY_VENTILATING),
        ],
    ).run(sim, daqs)


def test_expect_within_fails_if_condition_never_becomes_true(rig):
    sim, daqs = rig
    with pytest.raises(ScenarioFailed, match="never became true"):
        Scenario(
            "never",
            [ExpectWithin(seconds=0.1, condition=lambda s: s.core.state == KitchenState.LEAKING)],
        ).run(sim, daqs)


def test_set_powered_and_set_online_toggle_the_named_daq(rig):
    sim, daqs = rig
    Scenario("power", [SetPowered(daq=0, on=True), SetOnline(daq=1, on=False)]).run(sim, daqs)
    assert daqs[0].powered is True
    assert daqs[1].online is False


def test_ramp_step_drives_a_named_daq_channel(rig):
    sim, daqs = rig
    daqs[0].set_powered(True)
    daqs[0].set_online(True)
    Scenario(
        "ramp-to-danger",
        [
            Ramp(daq=0, channel=0, from_ma=4.0, to_ma=12.0, duration_s=0.1),
            ExpectWithin(seconds=1.0, condition=lambda s: s.core.state == KitchenState.FULLY_VENTILATING),
        ],
    ).run(sim, daqs)


# --- A representative named scenario from the library -----------------------


def test_library_has_the_documented_scenarios():
    from scenarios.library import SCENARIOS

    expected = {
        "slow_leak_to_quorum",
        "fast_leak_above_threshold",
        "multisensor_quorum",
        "sensor_goes_stale",
        "daq_offline_midrun",
        "peer_alarm",
        "permit_withdrawn",
        "estop",
        "operator_stop_during_leaking",
        "clear_air_interrupted_by_fresh_spike",
        "warmup_gate_blocks_gas",
    }
    assert expected.issubset(SCENARIOS.keys())


@pytest.mark.parametrize("scenario_name", [
    "slow_leak_to_quorum",
    "fast_leak_above_threshold",
    "multisensor_quorum",
    "sensor_goes_stale",
    "daq_offline_midrun",
    "peer_alarm",
    "permit_withdrawn",
    "estop",
    "operator_stop_during_leaking",
    "clear_air_interrupted_by_fresh_spike",
    "warmup_gate_blocks_gas",
])
def test_every_named_scenario_passes(rig, scenario_name):
    from scenarios.library import SCENARIOS

    sim, daqs = rig
    SCENARIOS[scenario_name].run(sim, daqs)


# --- run_scenario.py CLI -----------------------------------------------------
#
# Tests the module's functions directly (build_rig, run_one, main) rather
# than shelling out, so they run at unit-test speed and don't need a
# subprocess per case. One real subprocess smoke test at the bottom covers
# "the file is actually executable as a script".
#
# build_rig() takes explicit fully_vent_hold_s/sensor_warmup_s/arm_timeout_s
# kwargs (env-var reading happens once, in main(), before calling it) rather
# than re-reading os.environ itself — keeps these tests free of any need to
# reload modules to see an env change take effect.


def test_build_rig_default_kwargs_are_fast():
    """main()'s own defaults for these kwargs (used when no --fast-* flag
    and no SIM_* env var was given) must not be the real-hardware 5min/70s —
    that would make every routine `run_scenario.py` invocation impractically
    slow."""
    import run_scenario

    sim, daqs = run_scenario.build_rig()
    assert sim.core.fully_vent_min_hold_ms <= 10_000
    assert sim.core.sensor_warmup_ms <= 5_000


def test_build_rig_honours_explicit_overrides():
    import run_scenario

    sim, daqs = run_scenario.build_rig(fully_vent_hold_s=300, sensor_warmup_s=70)
    assert sim.core.fully_vent_min_hold_ms == 300_000
    assert sim.core.sensor_warmup_ms == 70_000


def test_resolve_timing_prefers_env_over_the_fast_default(monkeypatch):
    """main() resolves each timing as: SIM_* env var if set, else its own
    fast default — so `SIM_FULLY_VENT_HOLD_S=300 python run_scenario.py ...`
    (an operator wanting a real-timing check) is still honoured."""
    import run_scenario

    monkeypatch.setenv("SIM_FULLY_VENT_HOLD_S", "300")
    assert run_scenario.resolve_timing("SIM_FULLY_VENT_HOLD_S", fast_default=10) == 300
    monkeypatch.delenv("SIM_FULLY_VENT_HOLD_S", raising=False)
    assert run_scenario.resolve_timing("SIM_FULLY_VENT_HOLD_S", fast_default=10) == 10


def test_run_one_returns_true_on_a_passing_scenario():
    import run_scenario

    assert run_scenario.run_one("slow_leak_to_quorum") is True


def test_run_one_returns_false_and_does_not_raise_on_a_failing_scenario():
    import run_scenario
    from scenarios.engine import ExpectState, Scenario
    from scenarios.library import SCENARIOS

    SCENARIOS["_always_fails"] = Scenario("_always_fails", [ExpectState(KitchenState.LEAKING)])
    try:
        assert run_scenario.run_one("_always_fails") is False
    finally:
        del SCENARIOS["_always_fails"]


def test_main_all_exits_nonzero_when_any_scenario_fails():
    import run_scenario
    from scenarios.engine import ExpectState, Scenario
    from scenarios.library import SCENARIOS

    SCENARIOS["_always_fails"] = Scenario("_always_fails", [ExpectState(KitchenState.LEAKING)])
    try:
        code = run_scenario.main(["--all"])
    finally:
        del SCENARIOS["_always_fails"]
    assert code != 0


def test_main_all_exits_zero_when_every_scenario_passes():
    import run_scenario

    assert run_scenario.main(["--all"]) == 0


def test_main_list_prints_every_scenario_name(capsys):
    import run_scenario
    from scenarios.library import SCENARIOS

    run_scenario.main(["--list"])
    out = capsys.readouterr().out
    for name in SCENARIOS:
        assert name in out


def test_main_unknown_scenario_name_exits_nonzero():
    import run_scenario

    assert run_scenario.main(["not-a-real-scenario"]) != 0


def test_cli_runs_as_a_script_end_to_end():
    """One real subprocess invocation, to catch anything only visible when
    actually run as `python run_scenario.py ...` (argv handling, the
    __main__ guard) rather than through main()'s function-call surface."""
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "run_scenario.py", "slow_leak_to_quorum"],
        cwd=os.path.dirname(__file__),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
