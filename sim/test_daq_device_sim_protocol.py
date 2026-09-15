"""
Protocol-parity tests for daq_device_sim.py against a REAL local MQTT broker
(127.0.0.1:1883, verified reachable in this dev environment — see
[[controlo-cozinha-repo-layout]] memory). These exercise everything
cm7_simulator.py does that the old DaqDeviceSim(mqtt_client, device_id) did
not: its own connection, status/LWT, and the config/set|get|ack handshake.

Written before the DaqDeviceSim implementation changes, per the project's
test-first workflow — this file defines the contract the new constructor and
connection lifecycle must satisfy.

Each test uses a unique device id (uuid suffix) so retained topics from one
run never leak into the next, and clears every retained topic it published
in a `finally` block — the same hygiene problem as the `e2e-`-prefixed rows
documented in [[controlo-cozinha-repo-layout]]. If nothing is listening on
the configured broker, tests skip cleanly instead of hanging on connect.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid

import pytest

from daq_device_sim import DaqDeviceSim

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None

BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1883


def _broker_reachable() -> bool:
    try:
        with socket.create_connection((BROKER_HOST, BROKER_PORT), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    mqtt is None or not _broker_reachable(),
    reason="no local MQTT broker reachable on 127.0.0.1:1883",
)


def _unique_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class _Observer:
    """A second real MQTT client that subscribes to a device's whole topic
    tree and records every message, so tests can assert on wire traffic
    without depending on DaqDeviceSim's own internals."""

    def __init__(self, base_topic: str):
        self.base_topic = base_topic
        self.messages: list[tuple[str, str, bool]] = []  # (topic, payload, retained)
        self._lock = threading.Lock()
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                       client_id=f"observer-{uuid.uuid4().hex[:8]}")
        except AttributeError:
            self.client = mqtt.Client(client_id=f"observer-{uuid.uuid4().hex[:8]}")
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect
        self._connected = threading.Event()
        self.client.connect(BROKER_HOST, BROKER_PORT, keepalive=30)
        self.client.loop_start()
        if not self._connected.wait(timeout=5.0):
            raise RuntimeError("observer failed to connect to broker")

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe(f"{self.base_topic}/#", qos=1)
        self._connected.set()

    def _on_message(self, client, userdata, msg):
        with self._lock:
            self.messages.append((msg.topic, msg.payload.decode(errors="replace"), bool(msg.retain)))

    def wait_for(self, topic_suffix: str, timeout: float = 5.0) -> tuple[str, str, bool] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for m in self.messages:
                    if m[0] == f"{self.base_topic}/{topic_suffix}":
                        return m
            time.sleep(0.05)
        return None

    def all_matching(self, topic_suffix: str) -> list[tuple[str, str, bool]]:
        with self._lock:
            return [m for m in self.messages if m[0] == f"{self.base_topic}/{topic_suffix}"]

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def _read_retained(topic: str, timeout: float = 3.0) -> str | None:
    """Connect a brand-new subscriber to exactly `topic` and return the
    retained payload the broker replays on subscribe, or None if there is
    none. A live-delivered message (subscriber already subscribed before the
    publish happens) carries retain=False on the wire even when published
    with retain=True — Mosquitto only sets the flag on the initial replay to
    a new subscriber — so this is the only reliable way to assert retention."""
    result: list[str] = []
    done = threading.Event()

    def on_message(client, userdata, msg):
        result.append(msg.payload.decode(errors="replace"))
        done.set()

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"retain-check-{uuid.uuid4().hex[:8]}")
    except AttributeError:
        client = mqtt.Client(client_id=f"retain-check-{uuid.uuid4().hex[:8]}")
    client.on_message = on_message
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=10)
    client.loop_start()
    client.subscribe(topic, qos=1)
    done.wait(timeout=timeout)
    client.loop_stop()
    client.disconnect()
    return result[0] if result else None


def _clear_retained(base_topic: str, suffixes: list[str]) -> None:
    """Publish an empty retained payload to wipe each retained topic so it
    doesn't leak into a later test run or a live gui_app.py session."""
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"cleanup-{uuid.uuid4().hex[:8]}")
    except AttributeError:
        client = mqtt.Client(client_id=f"cleanup-{uuid.uuid4().hex[:8]}")
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=10)
    client.loop_start()
    for suffix in suffixes:
        client.publish(f"{base_topic}/{suffix}", payload=None, qos=1, retain=True)
    time.sleep(0.2)
    client.loop_stop()
    client.disconnect()


@pytest.fixture
def daq():
    """A DaqDeviceSim with its own real broker connection, torn down (and
    retained topics wiped) after the test regardless of outcome."""
    device_id = _unique_id("KITCHEN-DAQ-TEST")
    location = "KitchenTest"
    base = f"DataAcquisition/{location}/{device_id}"
    sim = DaqDeviceSim(device_id=device_id, location=location, host=BROKER_HOST, port=BROKER_PORT)
    try:
        yield sim, base
    finally:
        sim.stop()
        _clear_retained(base, ["status", "config/ack", "config/get"] +
                         [f"H2-{i+1}" for i in range(8)])


# --- status / LWT ------------------------------------------------------


def test_connect_publishes_retained_status_online(daq):
    sim, base = daq
    obs = _Observer(base)
    try:
        sim.start()
        msg = obs.wait_for("status")
        assert msg is not None, "no status message observed"
        assert msg[1] == "online"
        assert _read_retained(f"{base}/status") == "online"
    finally:
        obs.close()


def test_abrupt_disconnect_yields_retained_lwt_offline():
    """An ungraceful death (no stop()) must still leave the broker's retained
    LWT at 'offline' for the next subscriber — that's the whole point of
    will_set(). Simulated by closing the underlying socket directly (via the
    public socket() accessor) rather than calling stop()/disconnect(), since
    a clean DISCONNECT packet suppresses LWT delivery per the MQTT spec and
    would defeat the point of this test."""
    device_id = _unique_id("KITCHEN-DAQ-TEST")
    location = "KitchenTest"
    base = f"DataAcquisition/{location}/{device_id}"
    sim = DaqDeviceSim(device_id=device_id, location=location, host=BROKER_HOST, port=BROKER_PORT)
    obs = _Observer(base)
    try:
        sim.start()
        online_msg = obs.wait_for("status")
        assert online_msg is not None and online_msg[1] == "online"

        raw_sock = sim._client.socket()
        assert raw_sock is not None, "sim's client has no open socket to kill"
        raw_sock.close()

        deadline = time.monotonic() + 10.0
        found = None
        while time.monotonic() < deadline:
            matches = obs.all_matching("status")
            offline_matches = [m for m in matches if m[1] == "offline"]
            if offline_matches:
                found = offline_matches[-1]
                break
            time.sleep(0.1)
        assert found is not None, "LWT offline was never delivered"
        assert _read_retained(f"{base}/status") == "offline"
    finally:
        obs.close()
        try:
            sim._stop.set()
        except Exception:
            pass
        _clear_retained(base, ["status", "config/ack", "config/get"] +
                         [f"H2-{i+1}" for i in range(8)])


# --- config handshake on connect ---------------------------------------


def test_connect_requests_config_via_config_get(daq):
    sim, base = daq
    obs = _Observer(base)
    try:
        sim.start()
        msg = obs.wait_for("config/get")
        assert msg is not None
    finally:
        obs.close()


def test_connect_publishes_config_ack_with_default_8_sensors(daq):
    sim, base = daq
    obs = _Observer(base)
    try:
        sim.start()
        msg = obs.wait_for("config/ack")
        assert msg is not None
        ack = json.loads(msg[1])
        names = [s["name"] for s in ack["sensors"]]
        assert names == [f"H2-{i+1}" for i in range(8)]
        assert ack["interval_ms"] == 500
    finally:
        obs.close()


def test_inbound_config_get_republishes_config_ack(daq):
    sim, base = daq
    obs = _Observer(base)
    try:
        sim.start()
        assert obs.wait_for("config/ack") is not None  # the connect-time ack

        # A second client (standing in for the dashboard) asks again.
        try:
            requester = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"req-{uuid.uuid4().hex[:8]}")
        except AttributeError:
            requester = mqtt.Client(client_id=f"req-{uuid.uuid4().hex[:8]}")
        requester.connect(BROKER_HOST, BROKER_PORT, keepalive=10)
        requester.loop_start()
        requester.publish(f"{base}/config/get", sim.device_id, qos=1)
        time.sleep(0.3)
        requester.loop_stop()
        requester.disconnect()

        acks = obs.all_matching("config/ack")
        assert len(acks) >= 2, "config/get did not trigger a fresh config/ack"
    finally:
        obs.close()


# --- config/set changes active sensors + cadence ------------------------


def test_config_set_changes_published_topics_and_triggers_ack(daq):
    sim, base = daq
    obs = _Observer(base)
    try:
        sim.start()
        sim.powered = True
        sim.online = True
        assert obs.wait_for("config/ack") is not None

        new_config = {
            "sensors": [
                {"pin": 11, "name": "Custom-11", "type": "voltage"},
                {"pin": 12, "name": "Custom-12", "type": "voltage"},
            ],
            "interval_ms": 100,
        }
        try:
            setter = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"setter-{uuid.uuid4().hex[:8]}")
        except AttributeError:
            setter = mqtt.Client(client_id=f"setter-{uuid.uuid4().hex[:8]}")
        setter.connect(BROKER_HOST, BROKER_PORT, keepalive=10)
        setter.loop_start()
        setter.publish(f"{base}/config/set", json.dumps(new_config), qos=1)
        time.sleep(0.3)
        setter.loop_stop()
        setter.disconnect()

        # Fresh ack echoing the new config.
        deadline = time.monotonic() + 5.0
        latest_ack = None
        while time.monotonic() < deadline:
            acks = obs.all_matching("config/ack")
            for _, payload, _ in reversed(acks):
                parsed = json.loads(payload)
                if [s["name"] for s in parsed["sensors"]] == ["Custom-11", "Custom-12"]:
                    latest_ack = parsed
                    break
            if latest_ack:
                break
            time.sleep(0.1)
        assert latest_ack is not None, "config/set did not trigger a matching config/ack"
        assert latest_ack["interval_ms"] == 100

        # New sensor topics must now be published; old H2-N topics must stop.
        assert obs.wait_for("Custom-11", timeout=2.0) is not None
        old_count_before = len(obs.all_matching("H2-1"))
        time.sleep(0.5)
        old_count_after = len(obs.all_matching("H2-1"))
        assert old_count_after == old_count_before, "old H2-1 topic kept publishing after reconfig"
    finally:
        obs.close()
        _clear_retained(base, ["Custom-11", "Custom-12"])


def test_pins_outside_default_8_get_baseline_on_demand_and_are_forceable(daq):
    sim, base = daq
    obs = _Observer(base)
    try:
        new_config = {"sensors": [{"pin": 11, "name": "Custom-11", "type": "voltage"}], "interval_ms": 200}
        sim.start()
        obs.wait_for("config/ack")

        try:
            setter = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"setter-{uuid.uuid4().hex[:8]}")
        except AttributeError:
            setter = mqtt.Client(client_id=f"setter-{uuid.uuid4().hex[:8]}")
        setter.connect(BROKER_HOST, BROKER_PORT, keepalive=10)
        setter.loop_start()
        setter.publish(f"{base}/config/set", json.dumps(new_config), qos=1)
        time.sleep(0.3)
        setter.loop_stop()
        setter.disconnect()

        sim.force_leak(11, 3.9)
        sim.powered = True
        sim.online = True

        msg = obs.wait_for("Custom-11", timeout=3.0)
        assert msg is not None
        payload = json.loads(msg[1])
        assert payload["pin"] == 11
        assert payload["raw_v"] == pytest.approx(3.9, abs=1e-6)
    finally:
        obs.close()
        _clear_retained(base, ["Custom-11"])
