"""
The kitchen control PLC's OWN six 4-20 mA H2 sensors, published to the
historian.

The firmware does this in kitchen/SensorStream.cpp (sensorStreamTick ->
sensorStreamPublish -> publishOne), on
`DataAcquisition/<DAQ_PUBLISH_LOCATION>/<KITCHEN_DAQ_DEVICE_ID>/<name>` with
the payload protocolBuildSensorSample() builds. The simulator modelled these
sensors internally (KitchenSim.sensor_counts drives the danger check and the
quorum) but never put them on the wire at all — they only ever left the sim
through gui_app.py's local REST `/api/status`. So the historian could never
see the control PLC's own sensors from a simulated run, and that whole
ingest path went untested without real hardware.

Written before the implementation, per the project's test-first workflow:
this file defines the wire contract the sim's publisher must satisfy, and
every assertion here is checked against the firmware constants it mirrors
(kitchen/Kitchen_Settings.h, kitchen/Protocol.cpp).
"""

from __future__ import annotations

import json

import pytest

from kitchen_core_sim import ma_to_counts
from local_sensor_publish import (
    KITCHEN_LOCAL_SENSOR_NAMES,
    KITCHEN_LOCAL_SENSOR_PINS,
    build_local_sensor_payload,
    counts_to_ma,
    local_sensor_topic,
)


# --- identity: topic + pin encoding must match the firmware exactly --------

def test_topic_matches_the_firmware_publish_topic():
    """publishOne() builds "DataAcquisition/%s/%s/%s" from
    DAQ_PUBLISH_LOCATION ("Kitchen"), KITCHEN_DAQ_DEVICE_ID ("mainBoard")
    and KITCHEN_LOCAL_SENSOR_NAMES[i]."""
    assert local_sensor_topic("Kitchen", "mainBoard", "H2-1") == (
        "DataAcquisition/Kitchen/mainBoard/H2-1"
    )


def test_sensor_names_use_hyphens_not_underscores():
    """KITCHEN_LOCAL_SENSOR_NAMES is "H2-1".."H2-6". An underscore reads back
    unconverted forever, since no calibration row is ever keyed "H2_1" —
    the same trap daq_device_sim.py already documents at its own publish."""
    assert KITCHEN_LOCAL_SENSOR_NAMES == ["H2-1", "H2-2", "H2-3", "H2-4", "H2-5", "H2-6"]
    for name in KITCHEN_LOCAL_SENSOR_NAMES:
        assert "_" not in name


def test_pins_match_the_a0602_channel_layout_including_its_gap():
    """A0602 layout (Kitchen_Settings.h): I1..I6 = OA_CH_0,1,2,3,5,6 — CH4
    and CH7 are the fan/flow output DACs, NOT sensors. EXP_ENC(0, ch) is
    100 + ch, which DataAcquisition decodes as "E0:CH{ch}". A contiguous
    0..5 range here would silently mislabel H2-5 and H2-6."""
    assert KITCHEN_LOCAL_SENSOR_PINS == [100, 101, 102, 103, 105, 106]


# --- counts -> mA, the inverse of the sim's own mapping --------------------

def test_counts_to_ma_inverts_ma_to_counts():
    """The sim stores sensor_counts in ADC counts (kitchen_core_sim's
    ma_to_counts); the wire carries raw mA, so publishing has to invert it."""
    for ma in (4.0, 6.5, 12.0, 20.0):
        assert counts_to_ma(ma_to_counts(ma)) == pytest.approx(ma, abs=0.01)


def test_counts_to_ma_spans_the_4_20ma_loop():
    assert counts_to_ma(0) == pytest.approx(4.0)
    assert counts_to_ma(4095) == pytest.approx(20.0)


# --- payload shape: protocolBuildSensorSample() parity ---------------------

def test_payload_matches_the_firmware_wire_shape():
    """protocolBuildSensorSample() emits
    {"pin":N,"type":"current","raw_ma":X,"ts":T} — the same shape
    app/mqtt.py's _handle_sensor_sample() reads and DataAcquisition's
    ingest keys on."""
    payload = json.loads(build_local_sensor_payload(pin=100, counts=0, ts_ms=1700000000123))
    assert payload == {"pin": 100, "type": "current", "raw_ma": 4.0, "ts": 1700000000123}


def test_payload_reports_current_not_voltage():
    """The control PLC's local sensors are 4-20 mA
    (KITCHEN_LOCAL_SENSOR_IS_CURRENT is all-true). Publishing these as
    "voltage" would make the historian store them with the wrong phys_type
    and pick the wrong calibration defaults."""
    payload = json.loads(build_local_sensor_payload(pin=100, counts=2048, ts_ms=1))
    assert payload["type"] == "current"
    assert "raw_ma" in payload and "raw_v" not in payload


def test_payload_carries_the_encoded_pin_not_the_array_index():
    """publishOne() passes KITCHEN_LOCAL_SENSOR_PINS[index], the REAL encoded
    pin — DataAcquisition keys storage on the pin whenever one is present, so
    an array index here would store every sensor under the wrong pin."""
    payload = json.loads(build_local_sensor_payload(pin=106, counts=0, ts_ms=1))
    assert payload["pin"] == 106


def test_full_scale_counts_publish_as_20ma():
    payload = json.loads(build_local_sensor_payload(pin=100, counts=4095, ts_ms=1))
    assert payload["raw_ma"] == pytest.approx(20.0)


def test_a_mid_scale_leak_publishes_a_plausible_ma():
    """A sensor spiked to ~2 %v/v on the 0-100 %v/v loop lands mid-range."""
    counts = ma_to_counts(4.0 + 0.5 * 16.0)   # 50% of the loop -> 12 mA
    payload = json.loads(build_local_sensor_payload(pin=101, counts=counts, ts_ms=1))
    assert payload["raw_ma"] == pytest.approx(12.0, abs=0.01)
