"""
scenarios.library — the named scenarios from the plan's test matrix
(problems.txt:11): "Define multiple test scenarios to test all types of
situations."

Each entry in SCENARIOS is a ready-to-run Scenario built by Scenario.run(sim,
daqs) against an in-process KitchenSim + DaqDeviceSim pair — see
../run_scenario.py for the CLI and ../test_scenarios.py for how these are
exercised in the test suite. Every core timing (fully_vent_min_hold_ms,
sensor_warmup_ms) is expected to already be set short on the KitchenSim
instance passed in (run_scenario.py does this via the SIM_* env vars from
runtime.py) — the library itself hardcodes no timings.
"""

from kitchen_core_sim import KitchenState, now_ms

from .engine import (
    Ack,
    ClearForce,
    ExpectState,
    ExpectWithin,
    Hold,
    Ramp,
    Scenario,
    SetEstop,
    SetOnline,
    SetPeerAlarm,
    SetPermit,
    SetPowered,
    Spike,
    StartAndConfirm,
    Stop,
)


def _spec(**overrides) -> dict:
    """A minimal valid leak-test spec, only the fields a scenario cares about
    overridden. gasSetpointPct=0 by default so scenarios that don't care
    about inventory accumulation don't have to think about it."""
    base = {
        "gasSetpointPct": 0,
        "fanSpeedPct": 50,
        "leakStop": {"maxDurationMs": 60_000},
        "holdStop": {"maxDurationMs": 60_000},
        "ventStop": {"maxDurationMs": 60_000},
        "ventRegisters": {"central": True, "exhaust": True, "inlet": False},
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


class _WaitForWarmup:
    """ExpectWithin's timeout must exceed whatever sensor_warmup_ms the sim
    was actually built with — a fixed constant here would race a
    same-length CLI default (both were 5s, so this failed exactly at the
    wire). Reads the real budget off the sim at run() time and adds a 2x
    margin for scheduling jitter under load."""

    def run(self, ctx) -> None:
        from .engine import ExpectWithin

        budget_s = max(1.0, ctx.sim.core.sensor_warmup_ms / 1000.0)
        ExpectWithin(
            seconds=budget_s * 2 + 1.0,
            condition=lambda s: not s.core.warmup_pending,
            description="sensor warm-up gate",
        ).run(ctx)


def _started(daq_idx: int = 0) -> list:
    """The steps every leak-test scenario starts with: power the DAQ on and
    start+confirm a run, then let the warm-up gate elapse (kept short by the
    caller's fully_vent_min_hold_ms/sensor_warmup_ms overrides)."""
    return [
        SetPowered(daq=daq_idx, on=True),
        SetOnline(daq=daq_idx, on=True),
        StartAndConfirm(_spec()),
        _WaitForWarmup(),
    ]


SCENARIOS: dict[str, Scenario] = {}


def _register(name: str, steps: list) -> None:
    SCENARIOS[name] = Scenario(name, steps)


# --- Slow leak ramping up to the quorum threshold ---------------------------

_register(
    "slow_leak_to_quorum",
    [
        SetPowered(daq=0, on=True),
        SetOnline(daq=0, on=True),
        StartAndConfirm(_spec(leakStop={
            "maxDurationMs": 60_000,
            "sensorQuorum": {"quorumCount": 1, "thresholdPct": 10.0},
        })),
        _WaitForWarmup(),
        Ramp(daq=0, channel=0, from_ma=4.0, to_ma=8.0, duration_s=1.0),
        ExpectWithin(
            seconds=5.0,
            condition=lambda s: s.core.state == KitchenState.HOLD,
            description="quorum stop condition (LEAKING -> HOLD)",
        ),
    ],
)

# --- Fast leak straight past the danger threshold ---------------------------

_register(
    "fast_leak_above_threshold",
    [
        *_started(),
        Spike(daq=0, channel=0, ma=18.0),
        ExpectWithin(
            seconds=3.0,
            condition=lambda s: s.core.state == KitchenState.FULLY_VENTILATING,
            description="danger latch on a fast leak",
        ),
        ExpectState(KitchenState.FULLY_VENTILATING),
    ],
)

# --- Multi-sensor quorum: N-1 below never trips, the Nth crossing does ------

_register(
    "multisensor_quorum",
    [
        SetPowered(daq=0, on=True),
        SetOnline(daq=0, on=True),
        StartAndConfirm(_spec(leakStop={
            "maxDurationMs": 60_000,
            "sensorQuorum": {"quorumCount": 2, "thresholdPct": 10.0},
        })),
        _WaitForWarmup(),
        Spike(daq=0, channel=0, ma=8.0),  # one sensor over threshold, quorum needs 2
        Hold(seconds=0.3),
        ExpectState(KitchenState.LEAKING),  # must NOT have stopped yet
        Spike(daq=0, channel=1, ma=8.0),  # the second sensor crosses
        ExpectWithin(
            seconds=3.0,
            condition=lambda s: s.core.state == KitchenState.HOLD,
            description="quorum met once the 2nd sensor crosses",
        ),
    ],
)

# --- A sensor going stale mid-run (LOCAL_SENSOR_STALE is firmware-only —
#     the sim core has no staleness model, so this scenario documents that
#     gap rather than asserting a trip that cannot happen here) -------------

_register(
    "sensor_goes_stale",
    [
        *_started(),
        SetOnline(daq=0, on=False),  # the DAQ box itself goes dark
        Hold(seconds=0.3),
        # The sim's KitchenCoreSim has no LOCAL_SENSOR_STALE detection (that
        # lives only in the real firmware's SensorStream — see
        # kitchen/KitchenCore.cpp). This scenario exists to make that gap
        # visible rather than to assert a trip: it only checks nothing
        # crashes and the run is otherwise unaffected while the DAQ is dark.
        ExpectState(KitchenState.LEAKING),
    ],
)

# --- DAQ offline mid-run: readings stop, run keeps going -------------------

_register(
    "daq_offline_midrun",
    [
        *_started(),
        SetOnline(daq=0, on=False),
        Hold(seconds=0.3),
        ExpectState(KitchenState.LEAKING),  # no local sensor -> no local trip
        SetOnline(daq=0, on=True),
        Hold(seconds=0.2),
        ExpectState(KitchenState.LEAKING),  # recovers cleanly
    ],
)

# --- Peer alarm from the other kitchen zone --------------------------------

_register(
    "peer_alarm",
    [
        *_started(),
        SetPeerAlarm(active=True),
        ExpectWithin(
            seconds=2.0,
            condition=lambda s: s.core.state == KitchenState.FULLY_VENTILATING,
            description="peer alarm latch",
        ),
    ],
)

# --- Permit withdrawn mid-run -----------------------------------------------

_register(
    "permit_withdrawn",
    [
        *_started(),
        SetPermit(present=True, value=True),
        Hold(seconds=0.2),
        ExpectState(KitchenState.LEAKING),
        SetPermit(present=True, value=False),
        ExpectWithin(
            seconds=2.0,
            condition=lambda s: s.core.state == KitchenState.FULLY_VENTILATING,
            description="permit-denied latch",
        ),
    ],
)

# --- E-stop ------------------------------------------------------------------

_register(
    "estop",
    [
        *_started(),
        SetEstop(pressed=True),
        ExpectWithin(
            seconds=2.0,
            condition=lambda s: s.core.state == KitchenState.FULLY_VENTILATING,
            description="e-stop latch",
        ),
    ],
)

# --- Operator stop mid-LEAKING (routine, no ack required) -------------------

_register(
    "operator_stop_during_leaking",
    [
        *_started(),
        Stop(),
        ExpectState(KitchenState.FULLY_VENTILATING),
        ExpectWithin(
            seconds=1.0,
            condition=lambda s: not s.core.ack_required,
            description="operator stop should not require an ack",
        ),
        # No Ack() step here on purpose: a routine operator stop sets
        # ack_required=False synchronously (see stop()), so no human
        # acknowledgment is ever needed on this path — unlike a danger latch.
        # Since ack_required is already false, leaving FULLY_VENTILATING only
        # needs one more tick (_can_leave_fully_ventilating short-circuits on
        # "not ack_required"); asserting WAITING here is what actually proves
        # that follow-on tick happened and got published.
        ExpectWithin(
            seconds=1.0,
            condition=lambda s: s.core.state == KitchenState.WAITING,
            description="stop with no ack required should settle at WAITING",
        ),
    ],
)

# --- Clear-air hold restarts on a fresh spike mid-hold ----------------------

_register(
    "clear_air_interrupted_by_fresh_spike",
    [
        *_started(),
        Spike(daq=0, channel=0, ma=18.0),
        ExpectWithin(seconds=2.0, condition=lambda s: s.core.state == KitchenState.FULLY_VENTILATING,
                     description="initial danger latch"),
        ClearForce(daq=0, channel=0),
        Ack(),
        ExpectWithin(
            seconds=2.0,
            condition=lambda s: s.core.clear_since_ms != 0,
            description="clear-air hold starting",
        ),
        Hold(seconds=0.3),
        # A fresh spike partway through the hold must restart it, not just
        # pause it (KitchenCore::update()'s unconditional reset on danger).
        Spike(daq=0, channel=0, ma=18.0),
        Hold(seconds=0.1),
        ClearForce(daq=0, channel=0),
        ExpectWithin(
            seconds=2.0,
            condition=lambda s: s.core.clear_for_ms(now_ms()) < 300,
            description="clear-air hold restarted from ~0, not continued",
        ),
    ],
)

# --- Warm-up gate blocks gas until it elapses -------------------------------

class _WaitForGasToOpenAfterWarmup:
    """Same margin reasoning as _WaitForWarmup — the timeout must exceed the
    sim's ACTUAL configured sensor_warmup_ms, not a hardcoded guess."""

    def run(self, ctx) -> None:
        from .engine import ExpectWithin

        budget_s = max(1.0, ctx.sim.core.sensor_warmup_ms / 1000.0)
        ExpectWithin(
            seconds=budget_s * 2 + 1.0,
            condition=lambda s: not s.core.warmup_pending and s.core.gas_open(),
            description="gas opens once warm-up elapses",
        ).run(ctx)


_register(
    "warmup_gate_blocks_gas",
    [
        SetPowered(daq=0, on=True),
        SetOnline(daq=0, on=True),
        StartAndConfirm(_spec(gasSetpointPct=50)),
        ExpectState(KitchenState.LEAKING),
        # Immediately after confirm, gas must NOT be open — the warm-up gate
        # blocks it even though the phase is already LEAKING.
        ExpectWithin(
            seconds=0.2,
            condition=lambda s: not s.core.gas_open(),
            description="gas closed immediately after confirm, still warming up",
        ),
        _WaitForGasToOpenAfterWarmup(),
    ],
)
