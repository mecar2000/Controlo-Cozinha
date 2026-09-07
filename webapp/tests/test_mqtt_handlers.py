"""Unit tests for app.mqtt's message handlers — the pure parsing/routing
logic, invoked directly rather than through a real paho client."""

import json

import app.mqtt as mqtt_mod
import app.state as state
from app.config import KITCHEN_DEVICE_ID


def test_handle_state_updates_state():
    mqtt_mod._handle_state(json.dumps({"phase": "LEAKING", "elapsed": 5}))
    assert state.get_kitchen_state()["phase"] == "LEAKING"


def test_handle_state_ignores_malformed_json():
    state.set_kitchen_state({"phase": "WAITING"})
    mqtt_mod._handle_state("{not json")
    assert state.get_kitchen_state()["phase"] == "WAITING"


def test_handle_ack_stores_payload():
    mqtt_mod._handle_ack(json.dumps({"runId": "abc123", "valid": True}))
    assert state.get_last_ack()["runId"] == "abc123"


def test_handle_permit_true():
    mqtt_mod._handle_permit(json.dumps({"permit": True, "seq": 1}))
    assert state.get_permit_status()["ok"] is True


def test_handle_permit_false():
    mqtt_mod._handle_permit(json.dumps({"permit": False, "seq": 1}))
    assert state.get_permit_status()["ok"] is False


def test_handle_alarm_filters_own_device_id():
    """Must filter out its own device id to avoid self-latching (per the
    firmware's own rule, mirrored here for display purposes)."""
    topic = f"status/KitchenLeaks/{KITCHEN_DEVICE_ID}/alarm/lab5/hydrogen"
    mqtt_mod._handle_alarm(topic, json.dumps({"danger": True}))
    assert state.get_peer_alarm()["active"] is False


def test_handle_alarm_from_peer_sets_active():
    topic = "status/TurbineExp/OPTA-Turbine/alarm/lab2/hydrogen"
    mqtt_mod._handle_alarm(topic, json.dumps({"danger": True, "description": "leak"}))
    peer = state.get_peer_alarm()
    assert peer["active"] is True
    assert peer["detail"]["device_id"] == "OPTA-Turbine"
    assert peer["detail"]["description"] == "leak"


def test_peer_zones_are_tracked_independently():
    """THE REGRESSION: one zone clearing must not cancel another zone's alarm.
    Mirrors peer_one_zone_clearing_does_not_cancel_another in the firmware's
    test_safety.cpp — same bug, both ends."""
    zone_a = "status/ExpA/OPTA-A/alarm/lab2/hydrogen"
    zone_b = "status/ExpB/OPTA-B/alarm/lab3/hydrogen"

    mqtt_mod._handle_alarm(zone_a, json.dumps({"danger": True}))
    mqtt_mod._handle_alarm(zone_b, json.dumps({"danger": True}))
    assert state.get_peer_alarm()["active_count"] == 2

    mqtt_mod._handle_alarm(zone_b, json.dumps({"danger": False}))
    peer = state.get_peer_alarm()
    assert peer["active"] is True, "zone A is still alarming"
    assert peer["active_count"] == 1
    assert peer["detail"]["device_id"] == "OPTA-A"

    mqtt_mod._handle_alarm(zone_a, json.dumps({"danger": False}))
    assert state.get_peer_alarm()["active"] is False


def test_peer_zone_clear_alone_does_not_activate():
    mqtt_mod._handle_alarm(
        "status/ExpC/OPTA-C/alarm/lab4/hydrogen", json.dumps({"danger": False})
    )
    assert state.get_peer_alarm()["active"] is False


def test_handle_sensor_sample_stores_live_reading():
    topic = f"DataAcquisition/Kitchen/{KITCHEN_DEVICE_ID}/sensor-1"
    mqtt_mod._handle_sensor_sample(topic, json.dumps({"value": 1.23, "unit": "%v/v", "ts_ms": 1000}))
    readings = state.get_live_readings()
    assert readings["sensor-1"]["value"] == 1.23
    assert readings["sensor-1"]["unit"] == "%v/v"


def test_handle_sensor_sample_ignores_missing_value():
    topic = f"DataAcquisition/Kitchen/{KITCHEN_DEVICE_ID}/sensor-2"
    before = state.get_live_readings()
    mqtt_mod._handle_sensor_sample(topic, json.dumps({"unit": "%v/v"}))
    assert state.get_live_readings() == before


def test_on_message_dispatches_by_topic(monkeypatch):
    calls = []
    monkeypatch.setattr(mqtt_mod, "_handle_state", lambda p: calls.append(("state", p)))

    class FakeMsg:
        topic = f"KitchenControl/{KITCHEN_DEVICE_ID}/state"
        payload = b'{"phase": "WAITING"}'

    mqtt_mod._on_message(None, None, FakeMsg())
    assert calls == [("state", '{"phase": "WAITING"}')]
