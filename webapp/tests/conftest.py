"""
Shared fixtures. Backend tests run against a fake broker and a stubbed
DataAcquisition per the design spec's Testing section — nothing here touches
a real SQL Server, MQTT broker, or DataAcquisition instance.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def reset_state():
    """app.state is module-level global state; reset it between tests so one
    test's pending run / ack doesn't leak into the next."""
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
