"""
Shared fixtures for the repo-root integration suite (Part 4.3: "webapp-side
assertions" — scenarios drive MQTT, tests assert what the webapp reports over
HTTP). Lives outside both sim/ and webapp/ so neither package needs to know
about the other; this is the one place that imports both.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _pkg_dir in (_ROOT / "sim", _ROOT / "webapp"):
    p = str(_pkg_dir)
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest


@pytest.fixture(autouse=True)
def reset_webapp_state():
    """app.state is module-level global state; reset it between tests so one
    scenario's kitchen/peer state doesn't leak into the next."""
    import app.state as state
    state.set_mqtt_connected(False)
    state.set_daq_reachable(None)
    state.set_kitchen_state({})
    state.set_last_ack({})
    state.set_last_config_ack({})
    state.set_permit(False)
    state._permit_ok = None
    state.set_peer_alarm(False, None)
    state.set_pending_run(None)
    state.set_display_mode(False)
    yield
