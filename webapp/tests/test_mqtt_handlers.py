"""Unit tests for app.mqtt's message handlers — the pure parsing/routing
logic, invoked directly rather than through a real paho client."""

import json

import app.mqtt as mqtt_mod
import app.state as state
from app.config import KITCHEN_DEVICE_ID, KITCHEN_DAQ_DEVICE_ID


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
    """Fallback path: value/unit/ts_ms — kept so any other publisher that
    used this shape doesn't silently break. No publisher in this codebase
    actually sends it (the real firmware format is tested below)."""
    topic = f"DataAcquisition/Kitchen/{KITCHEN_DAQ_DEVICE_ID}/sensor-1"
    mqtt_mod._handle_sensor_sample(topic, json.dumps({"value": 1.23, "unit": "%v/v", "ts_ms": 1000}))
    readings = state.get_live_readings()
    assert readings["sensor-1"]["value"] == 1.23
    assert readings["sensor-1"]["unit"] == "%v/v"


def test_handle_sensor_sample_ignores_missing_value():
    topic = f"DataAcquisition/Kitchen/{KITCHEN_DAQ_DEVICE_ID}/sensor-2"
    before = state.get_live_readings()
    mqtt_mod._handle_sensor_sample(topic, json.dumps({"unit": "%v/v"}))
    assert state.get_live_readings() == before


def test_handle_sensor_sample_parses_firmware_current_format(monkeypatch):
    """The REAL firmware shape (kitchen/Protocol.cpp protocolBuildSensorSample(),
    matching DataAcquisition/CM7/CM7.ino's publishCurrent()) — the format that
    actually arrives on the wire from the kitchen PLC's mainBoard H2 sensors.

    With no DataAcquisition calibration and no fallback configured, the raw mA
    is stored and FLAGGED unconverted, so the heatmap won't plot it as %v/v."""
    monkeypatch.setattr(mqtt_mod, "H2_FALLBACK_PCT_VV_MAX", None)
    _seed_conversions(monkeypatch, {})
    topic = f"DataAcquisition/Kitchen/{KITCHEN_DAQ_DEVICE_ID}/H2-1"
    mqtt_mod._handle_sensor_sample(
        topic, json.dumps({"pin": 100, "type": "current", "raw_ma": 12.345, "ts": 1737000000801})
    )
    readings = state.get_live_readings()
    assert readings["H2-1"]["value"] == 12.345
    assert readings["H2-1"]["unit"] == "mA"
    assert readings["H2-1"]["ts_ms"] == 1737000000801
    assert readings["H2-1"]["converted"] is False


def _seed_conversions(monkeypatch, table):
    """Prime the conversion cache and freeze it, so no test touches the network."""
    monkeypatch.setattr(mqtt_mod, "_conversions", dict(table))
    monkeypatch.setattr(mqtt_mod, "_refresh_conversions_if_due", lambda: None)


def test_current_sample_is_converted_with_the_daq_calibration(monkeypatch):
    """The whole point: DataAcquisition defines mA->%v/v, this app applies it.
    Recalibrating there changes what the operator sees here, with no edit in
    this repo."""
    _seed_conversions(monkeypatch, {
        "H2-2": {
            "sensor_name": "H2-2",
            "conversion_type": "current_linear",
            "unit_symbol": "%v/v",
            "params": {"method": "linear", "raw_min": 4, "raw_max": 20,
                       "min_value": 0, "max_value": 4},
        }
    })
    topic = f"DataAcquisition/Kitchen/{KITCHEN_DAQ_DEVICE_ID}/H2-2"
    mqtt_mod._handle_sensor_sample(
        topic, json.dumps({"pin": 101, "type": "current", "raw_ma": 12.0, "ts": 1737000000801})
    )
    reading = state.get_live_readings()["H2-2"]
    assert reading["value"] == 2.0          # midscale current -> midscale %v/v
    assert reading["unit"] == "%v/v"
    assert reading["converted"] is True


def test_hardcoded_fallback_converts_when_daq_has_no_calibration(monkeypatch):
    """Opt-in H2_FALLBACK_PCT_VV_MAX keeps the view usable with DAQ offline."""
    _seed_conversions(monkeypatch, {})
    monkeypatch.setattr(mqtt_mod, "H2_FALLBACK_PCT_VV_MAX", 4.0)
    monkeypatch.setattr(mqtt_mod, "H2_FALLBACK_MA_MIN", 4.0)
    monkeypatch.setattr(mqtt_mod, "H2_FALLBACK_MA_MAX", 20.0)
    topic = f"DataAcquisition/Kitchen/{KITCHEN_DAQ_DEVICE_ID}/H2-3"
    mqtt_mod._handle_sensor_sample(
        topic, json.dumps({"pin": 102, "type": "current", "raw_ma": 12.0, "ts": 1737000000801})
    )
    reading = state.get_live_readings()["H2-3"]
    assert reading["value"] == 2.0
    assert reading["unit"] == "%v/v"
    assert reading["converted"] is True


def test_daq_calibration_wins_over_the_hardcoded_fallback(monkeypatch):
    """The fallback is only for when DataAcquisition has nothing — it must
    never override a real stored calibration."""
    _seed_conversions(monkeypatch, {
        "H2-4": {
            "sensor_name": "H2-4",
            "unit_symbol": "%v/v",
            "params": {"method": "linear", "raw_min": 4, "raw_max": 20,
                       "min_value": 0, "max_value": 10},
        }
    })
    monkeypatch.setattr(mqtt_mod, "H2_FALLBACK_PCT_VV_MAX", 4.0)
    topic = f"DataAcquisition/Kitchen/{KITCHEN_DAQ_DEVICE_ID}/H2-4"
    mqtt_mod._handle_sensor_sample(
        topic, json.dumps({"pin": 103, "type": "current", "raw_ma": 12.0, "ts": 1737000000801})
    )
    # DAQ's 0..10 span, not the fallback's 0..4.
    assert state.get_live_readings()["H2-4"]["value"] == 5.0


def test_conversion_fetch_failure_keeps_readings_flowing(monkeypatch):
    """DataAcquisition is not in the safety path: it being down must degrade
    the reading to raw mA, never stop live samples."""
    import app.daq as daq
    monkeypatch.setattr(mqtt_mod, "_conversions", {})
    monkeypatch.setattr(mqtt_mod, "_conversions_fetched_at", 0.0)
    monkeypatch.setattr(mqtt_mod, "H2_FALLBACK_PCT_VV_MAX", None)

    def _boom(_device_id):
        raise daq.DaqUnreachable("DAQ down")

    monkeypatch.setattr(daq, "get_conversions", _boom)
    topic = f"DataAcquisition/Kitchen/{KITCHEN_DAQ_DEVICE_ID}/H2-5"
    mqtt_mod._handle_sensor_sample(
        topic, json.dumps({"pin": 104, "type": "current", "raw_ma": 9.5, "ts": 1737000000801})
    )
    reading = state.get_live_readings()["H2-5"]
    assert reading["value"] == 9.5
    assert reading["converted"] is False


def test_handle_sensor_sample_parses_firmware_voltage_format():
    topic = f"DataAcquisition/Kitchen/{KITCHEN_DAQ_DEVICE_ID}/flow"
    mqtt_mod._handle_sensor_sample(
        topic, json.dumps({"pin": 1, "type": "voltage", "raw_v": 3.21, "ts": 1737000000900})
    )
    readings = state.get_live_readings()
    assert readings["flow"]["value"] == 3.21
    assert readings["flow"]["unit"] == "V"


def test_on_message_dispatches_by_topic(monkeypatch):
    calls = []
    monkeypatch.setattr(mqtt_mod, "_handle_state", lambda p: calls.append(("state", p)))

    class FakeMsg:
        topic = f"KitchenControl/{KITCHEN_DEVICE_ID}/state"
        payload = b'{"phase": "WAITING"}'

    mqtt_mod._on_message(None, None, FakeMsg())
    assert calls == [("state", '{"phase": "WAITING"}')]
