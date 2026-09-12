#!/usr/bin/env python
"""
run_scenario — headless CLI for the scenario library (problems.txt:11:
"Define multiple test scenarios ... Dont need GUI for that. Make sure webapp
behaves as expected").

Runs entirely in-process against KitchenSim + DaqDeviceSim — no MQTT broker,
no GUI, no webapp needed to exercise the state machine itself. To also check
the WEBAPP's behaviour end to end (the "make sure webapp behaves as
expected" half), point a real SimRuntime + this app's own webapp at the same
broker and drive scenarios through MQTT instead — see docs on
scenarios.engine.Scenario for why it works against either.

Usage:
    python run_scenario.py <name>              run one scenario
    python run_scenario.py --all               run every scenario, summarize
    python run_scenario.py --list              list scenario names and exit

Timings default to FAST (10s purge hold, 5s sensor warm-up) regardless of
KitchenSim's own real-hardware defaults, so a routine invocation needs no
setup. Set SIM_FULLY_VENT_HOLD_S / SIM_SENSOR_WARMUP_S / SIM_ARM_TIMEOUT_S
(seconds) to override — e.g. a final pre-hydrogen check against real timings:
    SIM_FULLY_VENT_HOLD_S=300 SIM_SENSOR_WARMUP_S=70 python run_scenario.py --all
"""

from __future__ import annotations

import os
import sys

from daq_device_sim import DaqDeviceSim
from kitchen_core_sim import KitchenCoreSim
from runtime import KitchenSim
from scenarios.engine import ScenarioFailed
from scenarios.library import SCENARIOS

# Fast enough that `run_scenario.py --all` completes in well under a minute;
# real-hardware values (300 / 70) are still reachable via the SIM_* env vars.
# sensor_warmup deliberately shorter than fully_vent_hold: scenarios wait out
# a full warm-up before doing anything interesting (scenarios.library's
# _WaitForWarmup), so keeping this small keeps every scenario's fixed cost
# small too.
_FAST_FULLY_VENT_HOLD_S = 10
_FAST_SENSOR_WARMUP_S = 1
_FAST_ARM_TIMEOUT_S = 10


def resolve_timing(env_var: str, fast_default: float) -> float:
    """SIM_* env var (seconds) if set and numeric, else fast_default."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return fast_default
    try:
        return float(raw)
    except ValueError:
        print(f"[run_scenario] Ignoring non-numeric {env_var}={raw!r}, using {fast_default}")
        return fast_default


def build_rig(
    *,
    fully_vent_hold_s: float | None = None,
    sensor_warmup_s: float | None = None,
    arm_timeout_s: float | None = None,
    device_id: str = "KITCHEN-01",
) -> tuple[KitchenSim, list[DaqDeviceSim]]:
    """One KitchenSim + two DaqDeviceSim, wired together like SimRuntime
    does, minus the MQTT connection and background tick thread — scenarios
    drive ticking themselves (see scenarios.engine). No client is passed to
    KitchenSim's underlying core; a no-op client is used since nothing here
    inspects published MQTT traffic (scenarios assert against sim.core /
    sim.daqs directly, not the wire)."""
    if fully_vent_hold_s is None:
        fully_vent_hold_s = resolve_timing("SIM_FULLY_VENT_HOLD_S", _FAST_FULLY_VENT_HOLD_S)
    if sensor_warmup_s is None:
        sensor_warmup_s = resolve_timing("SIM_SENSOR_WARMUP_S", _FAST_SENSOR_WARMUP_S)
    if arm_timeout_s is None:
        arm_timeout_s = resolve_timing("SIM_ARM_TIMEOUT_S", _FAST_ARM_TIMEOUT_S)

    client = _NullClient()
    sim = KitchenSim(client, device_id, "KitchenLeaks", "lab5")
    sim.core = KitchenCoreSim(
        fully_vent_min_hold_ms=int(fully_vent_hold_s * 1000),
        sensor_warmup_ms=int(sensor_warmup_s * 1000),
        arm_timeout_ms=int(arm_timeout_s * 1000),
    )
    daq1 = DaqDeviceSim(client, "KITCHEN-DAQ-1")
    daq2 = DaqDeviceSim(client, "KITCHEN-DAQ-2")
    sim.daq_devices = [daq1, daq2]
    return sim, [daq1, daq2]


class _NullClient:
    """A publish target that goes nowhere — scenarios assert against sim
    state directly, not wire traffic, so nothing needs to observe this."""

    def publish(self, topic, payload, qos=0, retain=False):
        return None

    def is_connected(self) -> bool:
        return True


def run_one(name: str) -> bool:
    """Run one named scenario. Returns True on pass; on failure or an
    unexpected exception, prints the reason and returns False rather than
    raising, so callers (main --all) can keep going through the rest."""
    scenario = SCENARIOS[name]
    sim, daqs = build_rig()
    try:
        scenario.run(sim, daqs)
    except ScenarioFailed as exc:
        print(f"FAIL  {name}: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 - a scenario bug is still a FAIL, not a crash
        print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        return False
    print(f"PASS  {name}")
    return True


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2

    if argv[0] == "--list":
        for name in SCENARIOS:
            print(name)
        return 0

    if argv[0] == "--all":
        results = {name: run_one(name) for name in SCENARIOS}
        passed = sum(results.values())
        total = len(results)
        print(f"\n{passed}/{total} scenarios passed")
        return 0 if passed == total else 1

    name = argv[0]
    if name not in SCENARIOS:
        print(f"Unknown scenario {name!r}. Known scenarios:")
        for known in SCENARIOS:
            print(f"  {known}")
        return 2

    return 0 if run_one(name) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
