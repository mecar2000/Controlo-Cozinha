"""
app.commands — the ONLY module that publishes to KitchenControl/{deviceId}/cmd.

Mirrors the firmware's own Outputs::drive() invariant: one auditable choke
point per layer. Every command this app can possibly send to the kitchen
passes through one of the four functions below, and nowhere else in this
codebase calls app.mqtt.publish() with a cmd topic.

Two-phase start: start(spec) -> firmware validates/clamps -> ack -> confirm(runId)
within 60s -> gas flows. This module only sends the wire messages; runs.py
owns orchestrating that sequence against the database and DataAcquisition.
"""

import json
import time
import uuid
from typing import Optional

import app.state as state
from app.config import KITCHEN_DEVICE_ID
from app.mqtt import publish

_CMD_TOPIC = f"KitchenControl/{KITCHEN_DEVICE_ID}/cmd"

# How long we wait in-process for an ack after sending start(), before giving
# up and reporting a timeout to the caller. The firmware's own ARM_TIMEOUT_MS
# (60s) governs how long ARMED waits for confirm(); this is a shorter,
# separate budget for the ack round-trip itself.
ACK_WAIT_TIMEOUT_S = 10.0
_ACK_POLL_INTERVAL_S = 0.1


class CommandError(Exception):
    """Raised when a command cannot be sent (e.g. MQTT down)."""


def _publish_cmd(payload: dict) -> None:
    """Commands are never retained — the firmware ignores retained cmd
    messages so a broker replay can't start a leak on reboot."""
    result = publish(_CMD_TOPIC, json.dumps(payload), qos=1, retain=False)
    if result is None:
        raise CommandError("MQTT not connected — command not sent")


def start(spec: dict) -> str:
    """
    Sends start(spec). Returns the runId this app generated and included in
    the request, which the caller (runs.py) uses to correlate the ack and to
    send confirm(runId) later. Does NOT wait for the ack — call
    wait_for_ack(run_id) separately so the HTTP request that triggers this
    isn't held open across a network round trip inside this function.
    """
    run_id = uuid.uuid4().hex[:16]
    state.set_pending_run(run_id)
    payload = {"cmd": "start", "runId": run_id, "spec": spec}
    _publish_cmd(payload)
    return run_id


def wait_for_ack(run_id: str, timeout_s: float = ACK_WAIT_TIMEOUT_S) -> Optional[dict]:
    """
    Polls app.state for an ack whose runId matches. Returns the ack payload,
    or None on timeout. A mismatched or stale runId in the ack (e.g. from a
    previous start() call still in flight) is not returned as a match.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ack = state.get_last_ack()
        if ack.get("runId") == run_id:
            return ack
        time.sleep(_ACK_POLL_INTERVAL_S)
    return None


def confirm(run_id: str) -> None:
    """Sends confirm(runId). Only valid after an ack for the same runId has
    been seen — callers should have already checked wait_for_ack()."""
    _publish_cmd({"cmd": "confirm", "runId": run_id})
    state.set_pending_run(None)


def cancel_pending() -> None:
    """Clears pending-run bookkeeping without sending anything to the
    firmware — used when the operator backs out of the review screen after
    an ack but before confirming. The firmware's own ARM_TIMEOUT_MS (60s)
    disarms it on the device side regardless."""
    state.set_pending_run(None)


def stop() -> None:
    """
    Sends stop(). Goes straight to MQTT — must not depend on any storage
    service, and must work even mid-run. This is the one command that must
    never be gated on DAQ, display mode, or anything else in this process
    (design spec, 'Availability').
    """
    _publish_cmd({"cmd": "stop"})


def ack() -> None:
    """
    Sends the human acknowledge for a latched FULLY_VENTILATING exit. The
    firmware requires both this ack AND a continuous clear-air hold before it
    actually releases — this call does not shortcut that; it only satisfies
    the ack half of the condition.
    """
    _publish_cmd({"cmd": "ack"})
