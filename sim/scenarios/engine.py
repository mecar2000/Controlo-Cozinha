"""
scenarios.engine — the headless scenario runner.

A Scenario is a list of steps executed in order against a live KitchenSim +
its DaqDeviceSim list, driven in-process with NO MQTT broker required (the
same shape SimRuntime._tick_loop drives, just without the background thread
or a real network client — see runtime.KitchenSim.feed_daq_sensors/
tick_and_publish). Ramps and holds run in real wall-clock time; scenarios are
kept short (leaks <=30s) so this stays fast enough for CI and local use.

ExpectState/ExpectWithin raise ScenarioFailed with enough detail to see what
went wrong straight from a CI log, without re-running under a debugger.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from kitchen_core_sim import KitchenState

_TICK_HZ = 20.0  # engine's own drive rate — independent of SimRuntime.TICK_HZ


class ScenarioFailed(Exception):
    """A scenario step's expectation was not met. The message names the
    scenario, the step, and what was actually observed."""


class Step:
    """Base class for scenario steps. Subclasses implement run()."""

    def run(self, ctx: "_Context") -> None:
        raise NotImplementedError


@dataclass
class Hold(Step):
    """Advance real time by `seconds`, ticking the core/DAQ throughout, with
    no expectation — for letting a condition (warm-up, a stop-condition
    duration) simply elapse."""

    seconds: float

    def run(self, ctx: "_Context") -> None:
        ctx.tick_for(self.seconds)


@dataclass
class Spike(Step):
    """Instantly pin one DAQ's channel to a fixed mA (a fast leak) — thin
    wrapper over DaqDeviceSim.force_leak so scenarios read declaratively."""

    daq: int
    channel: int
    ma: float

    def run(self, ctx: "_Context") -> None:
        ctx.daqs[self.daq].force_leak(self.channel, self.ma)


@dataclass
class Ramp(Step):
    """Move one DAQ's channel linearly from from_ma to to_ma over
    duration_s of real time (a slow leak) — wraps DaqDeviceSim.ramp. Unlike
    Hold, this step returns immediately; the ramp continues across whatever
    steps follow until something else overrides that channel."""

    daq: int
    channel: int
    from_ma: float
    to_ma: float
    duration_s: float

    def run(self, ctx: "_Context") -> None:
        ctx.daqs[self.daq].ramp(self.channel, self.from_ma, self.to_ma, self.duration_s)


@dataclass
class ClearForce(Step):
    """Release one DAQ's channel (or all, if channel is None) back to its
    random walk — ends a Spike or Ramp."""

    daq: int
    channel: int | None = None

    def run(self, ctx: "_Context") -> None:
        ctx.daqs[self.daq].clear_force(self.channel)


@dataclass
class SetPowered(Step):
    daq: int
    on: bool

    def run(self, ctx: "_Context") -> None:
        ctx.daqs[self.daq].set_powered(self.on)


@dataclass
class SetOnline(Step):
    """Simulate a DAQ box going unreachable (network cut, crash) mid-run."""

    daq: int
    on: bool

    def run(self, ctx: "_Context") -> None:
        ctx.daqs[self.daq].set_online(self.on)


@dataclass
class SetPeerAlarm(Step):
    active: bool

    def run(self, ctx: "_Context") -> None:
        ctx.sim.core.peer_alarm_active = self.active


@dataclass
class SetPermit(Step):
    present: bool
    value: bool = True

    def run(self, ctx: "_Context") -> None:
        ctx.sim.core.permit_present = self.present
        ctx.sim.core.permit_value = self.value


@dataclass
class SetEstop(Step):
    pressed: bool

    def run(self, ctx: "_Context") -> None:
        ctx.sim.core.estop_pressed = self.pressed


@dataclass
class SendCmd(Step):
    """Feed a raw MQTT `cmd` payload through KitchenSim.handle_cmd, exactly
    as if it arrived over the wire — for a one-off command a scenario needs
    that isn't covered by a more specific step (Stop/Ack/StartAndConfirm)."""

    payload: dict

    def run(self, ctx: "_Context") -> None:
        import json

        ctx.sim.handle_cmd(json.dumps(self.payload))


@dataclass
class Stop(Step):
    def run(self, ctx: "_Context") -> None:
        SendCmd({"cmd": "stop", "runId": ctx.sim.core.spec.run_id}).run(ctx)


@dataclass
class Ack(Step):
    """The operator's human-acknowledge for a latched FULLY_VENTILATING."""

    def run(self, ctx: "_Context") -> None:
        SendCmd({"cmd": "ack"}).run(ctx)


@dataclass
class StartAndConfirm(Step):
    """ARMED->LEAKING in one step: sends start(spec), ticks until the sim's
    own ack has been processed, then sends confirm with the same runId —
    exactly the two-command sequence the webapp's start_run()/confirm_run()
    drive, minus the review pause in between (a scenario knows what it asked
    for; a human operator is the one who needs to see the diff first)."""

    spec: dict
    run_id: str = "scenario-run"

    def run(self, ctx: "_Context") -> None:
        SendCmd({"cmd": "start", "runId": self.run_id, "spec": self.spec}).run(ctx)
        if ctx.sim.core.state != KitchenState.ARMED:
            raise ScenarioFailed(
                f"[{ctx.scenario_name}] start() did not reach ARMED "
                f"(state={ctx.sim.core.state.value}) — check the spec is valid"
            )
        SendCmd({"cmd": "confirm", "runId": self.run_id}).run(ctx)


@dataclass
class ExpectState(Step):
    """Assert the core is in exactly this state RIGHT NOW (no waiting) —
    for asserting a state that a prior step should already have caused."""

    state: KitchenState

    def run(self, ctx: "_Context") -> None:
        actual = ctx.sim.core.state
        if actual != self.state:
            raise ScenarioFailed(
                f"[{ctx.scenario_name}] expected state {self.state.value}, "
                f"got {actual.value} (reason={ctx.sim.core.reason.value})"
            )


@dataclass
class ExpectWithin(Step):
    """Assert `condition(sim)` becomes true within `seconds` of real time,
    ticking throughout — for asserting an eventual consequence (a ramp
    crossing a threshold, a stop condition elapsing) without hardcoding
    exactly which tick it happens on.

    Always ticks at least once before the first check, even if the condition
    already holds. A condition can become true purely from a synchronous
    state change in a prior step (e.g. stop() sets ack_required=False
    immediately, with no tick involved) — checking before ticking would then
    return without ever calling update()/publish, leaving the sim (and
    anything consuming its MQTT topics) one cycle behind whatever the
    condition was actually confirming."""

    seconds: float
    condition: Callable[["object"], bool]
    description: str = ""

    def run(self, ctx: "_Context") -> None:
        deadline = time.monotonic() + self.seconds
        while True:
            ctx.tick_once()
            if self.condition(ctx.sim):
                return
            if time.monotonic() >= deadline:
                what = self.description or "condition"
                raise ScenarioFailed(
                    f"[{ctx.scenario_name}] {what} never became true within "
                    f"{self.seconds}s (state={ctx.sim.core.state.value}, "
                    f"reason={ctx.sim.core.reason.value})"
                )
            time.sleep(1.0 / _TICK_HZ)


class _Context:
    def __init__(self, scenario_name: str, sim, daqs: list):
        self.scenario_name = scenario_name
        self.sim = sim
        self.daqs = daqs

    def tick_once(self) -> None:
        self.sim.feed_daq_sensors(self.daqs)
        self.sim.tick_and_publish()

    def tick_for(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        # Always tick at least once, even for seconds<=0 — a Hold(0) still
        # advances the state machine one step, matching "do nothing, then
        # check" rather than "do literally nothing".
        while True:
            self.tick_once()
            if time.monotonic() >= deadline:
                return
            time.sleep(1.0 / _TICK_HZ)


class Scenario:
    """A named, ordered list of steps. run() executes them in-process against
    the given KitchenSim + DaqDeviceSim list — no MQTT broker needed."""

    def __init__(self, name: str, steps: list[Step]):
        self.name = name
        self.steps = steps

    def run(self, sim, daqs: list) -> None:
        ctx = _Context(self.name, sim, daqs)
        for step in self.steps:
            step.run(ctx)
