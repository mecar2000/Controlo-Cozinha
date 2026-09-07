"""
app.mqtt — broker connection; subscribes to every topic this app CONSUMES.

This module never publishes commands — that is commands.py's sole job (see
its module docstring). This module only reads: retained state/ack topics,
live sensor samples, peer alarms, and the permit heartbeat, all mirrored into
app.state so routes and commands.py can read them without touching MQTT
directly.

Two consumers of one broker stream (this app and DataAcquisition), not a
chain — heatmap latency does not depend on the historian (design spec,
"Architecture" / rule 3).
"""

import json
import os
import re
import threading
import time

import paho.mqtt.client as mqtt

import app.state as state
from app.config import MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS, KITCHEN_DEVICE_ID

_mqtt_client: "mqtt.Client | None" = None


def publish(topic: str, payload, qos: int = 1, retain: bool = False):
    """The only way to publish from this process. Called exclusively by
    commands.py — see that module's docstring for why. Returns None (and
    logs) if the client isn't connected yet, rather than raising, since a
    caller-side "not connected" branch is easier to test than an exception."""
    if _mqtt_client is None or not state.is_mqtt_connected():
        print("[MQTT] publish() called while disconnected — dropped:", topic)
        return None
    return _mqtt_client.publish(topic, payload, qos=qos, retain=retain)


# --- Topic patterns this app subscribes to ---
_TOPIC_STATE = f"KitchenControl/{KITCHEN_DEVICE_ID}/state"
_TOPIC_ACK = f"KitchenControl/{KITCHEN_DEVICE_ID}/ack"
_TOPIC_CONFIG_ACK = f"KitchenControl/{KITCHEN_DEVICE_ID}/config/ack"
_TOPIC_SENSORS = f"DataAcquisition/Kitchen/{KITCHEN_DEVICE_ID}/#"
_TOPIC_PERMIT = f"safety/permit/{KITCHEN_DEVICE_ID}"
_TOPIC_ALARM_WILDCARD = f"status/+/+/alarm/+/hydrogen"

_SUBSCRIPTIONS = [
    (_TOPIC_STATE, 1),
    (_TOPIC_ACK, 1),
    (_TOPIC_CONFIG_ACK, 1),
    (_TOPIC_SENSORS, 0),
    (_TOPIC_PERMIT, 0),
    (_TOPIC_ALARM_WILDCARD, 1),
]

# status/{ExperimentName}/{deviceId}/alarm/{labId}/hydrogen
_ALARM_TOPIC_RE = re.compile(r"^status/([^/]+)/([^/]+)/alarm/([^/]+)/hydrogen$")
# DataAcquisition/Kitchen/{deviceId}/{sensorName}
_SENSOR_TOPIC_RE = re.compile(r"^DataAcquisition/Kitchen/([^/]+)/(.+)$")


def _on_connect(client, userdata, flags, reason_code, properties=None):
    """Compatible with both paho-mqtt v1 (int rc) and v2 (ReasonCode +
    properties) callback signatures — a ReasonCode compares equal to 0 on
    success, so the same check works for both."""
    if reason_code == 0:
        print(f"[MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}")
        state.set_mqtt_connected(True)
        for topic, qos in _SUBSCRIPTIONS:
            client.subscribe(topic, qos=qos)
    else:
        print(f"[MQTT] Connection failed rc={reason_code}")
        state.set_mqtt_connected(False)


def _on_disconnect(client, userdata, *args):
    state.set_mqtt_connected(False)
    rc = args[-2] if len(args) >= 2 else (args[0] if args else 0)
    if rc != 0:
        print(f"[MQTT] Unexpected disconnect (rc={rc})")


def _on_message(client, userdata, msg):
    state.touch_mqtt_message()
    topic = msg.topic
    try:
        payload_text = msg.payload.decode("utf-8")
    except UnicodeDecodeError:
        return

    if topic == _TOPIC_STATE:
        _handle_state(payload_text)
    elif topic == _TOPIC_ACK:
        _handle_ack(payload_text)
    elif topic == _TOPIC_CONFIG_ACK:
        _handle_config_ack(payload_text)
    elif topic == _TOPIC_PERMIT:
        _handle_permit(payload_text)
    elif topic.startswith("status/"):
        _handle_alarm(topic, payload_text)
    elif topic.startswith("DataAcquisition/Kitchen/"):
        _handle_sensor_sample(topic, payload_text)


def _handle_state(payload_text: str) -> None:
    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError:
        print("[MQTT] Malformed state payload, ignored")
        return
    if not isinstance(data, dict):
        print("[MQTT] State payload was not an object, ignored")
        return
    state.set_kitchen_state(data)
    # Live rewind, per the design spec, must auto-return to live on any
    # danger latch or phase change — routes.py reads this via state on
    # every poll rather than this module tracking rewind UI state itself.


def _handle_ack(payload_text: str) -> None:
    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError:
        print("[MQTT] Malformed ack payload, ignored")
        return
    if not isinstance(data, dict):
        print("[MQTT] Ack payload was not an object, ignored")
        return
    state.set_last_ack(data)


def _handle_config_ack(payload_text: str) -> None:
    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError:
        print("[MQTT] Malformed config/ack payload, ignored")
        return
    if not isinstance(data, dict):
        return
    state.set_last_config_ack(data)


def _handle_permit(payload_text: str) -> None:
    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    # Absence warns, never trips (see docs/implementation-plan.md — the
    # kitchen never depends on server.py being alive); we just mirror the
    # heartbeat's content, we don't interpret it as a stop condition here.
    state.set_permit(bool(data.get("permit", False)))


def _handle_alarm(topic: str, payload_text: str) -> None:
    m = _ALARM_TOPIC_RE.match(topic)
    if not m:
        return
    experiment_name, device_id, lab_id = m.groups()
    if device_id == KITCHEN_DEVICE_ID:
        return  # filter out our own alarm, matching the firmware's own rule
    try:
        data = json.loads(payload_text) if payload_text else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    # Defaults to True on purpose: a message arrived on a peer's hydrogen
    # ALARM topic, so if we cannot tell whether it says danger, the safe
    # reading is that it does. Fail-safe, not a parsing convenience.
    active = bool(data.get("danger", data.get("active", True)))
    state.set_peer_alarm(active, {
        "experiment_name": experiment_name,
        "device_id": device_id,
        "lab_id": lab_id,
        **data,
    })


def _handle_sensor_sample(topic: str, payload_text: str) -> None:
    m = _SENSOR_TOPIC_RE.match(topic)
    if not m:
        return
    _device_id, sensor_name = m.groups()
    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    value = data.get("value")
    if value is None:
        return
    unit = data.get("unit", "")
    ts_ms = data.get("ts_ms", int(time.time() * 1000))
    try:
        value = float(value)
        ts_ms = int(ts_ms)
    except (TypeError, ValueError):
        # paho swallows exceptions raised in this callback, so an unguarded
        # conversion would drop the sample with no trace of why.
        print(f"[MQTT] Non-numeric sample on {topic!r}, ignored: {data!r}")
        return
    if value != value:  # NaN
        return
    state.set_live_reading(sensor_name, value, str(unit), ts_ms)


def _run_forever():
    global _mqtt_client
    client_id = f"kitchen-webapp-{os.getpid()}"
    try:
        _mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        _mqtt_client = mqtt.Client(client_id=client_id)
    if MQTT_USER:
        _mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    _mqtt_client.on_connect = _on_connect
    _mqtt_client.on_disconnect = _on_disconnect
    _mqtt_client.on_message = _on_message

    while True:
        try:
            print(f"[MQTT] Connecting to {MQTT_HOST}:{MQTT_PORT}...")
            _mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
            _mqtt_client.loop_forever()
        except Exception as exc:
            print(f"[MQTT] Error: {exc} — retrying in 5s")
            state.set_mqtt_connected(False)
            time.sleep(5)


def start() -> None:
    """Spawn the MQTT daemon thread. Call once at app startup."""
    threading.Thread(target=_run_forever, daemon=True).start()
