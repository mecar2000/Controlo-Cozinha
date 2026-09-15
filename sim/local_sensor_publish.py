"""
local_sensor_publish — the kitchen control PLC's own six 4-20 mA H2 sensors,
on the wire.

The firmware publishes these from kitchen/SensorStream.cpp; this module is
the simulator's counterpart, kept deliberately small and pure so the wire
contract is testable without a broker (see test_local_sensor_publish.py).

Everything here MIRRORS firmware constants rather than inventing its own —
kitchen/Kitchen_Settings.h for the pin map and names, kitchen/Protocol.cpp's
protocolBuildSensorSample() for the payload shape. If a constant changes
there, it has to change here too; the tests pin each one so the divergence
fails loudly instead of showing up as silently mis-keyed history.

Why this exists at all: KitchenSim models these sensors internally
(sensor_counts drives dangerActive() and the quorum), but nothing ever
published them, so the historian could not see the control PLC's own sensors
from a simulated run — the one sensor path that matters most for the kitchen
and the only one that had no simulator coverage.
"""

from __future__ import annotations

import json

# --- firmware mirrors -----------------------------------------------------

# Kitchen_Settings.h: KITCHEN_LOCAL_SENSOR_NAMES. HYPHENS, not underscores —
# thresholds.py's ^H2-(\d+)$ regex and every DataAcquisition calibration row
# are keyed on this spelling; "H2_1" reads back unconverted forever.
KITCHEN_LOCAL_SENSOR_NAMES = ["H2-1", "H2-2", "H2-3", "H2-4", "H2-5", "H2-6"]

# Kitchen_Settings.h: KITCHEN_LOCAL_SENSOR_PINS, EXP_ENC(0, ch) == 100 + ch.
# The A0602 channel layout is I1..I6 = OA_CH_0,1,2,3,5,6 — CH4 and CH7 are the
# fan-speed and flowmeter-setpoint output DACs, not sensors. Hence the gap at
# index 4; a contiguous 100..105 range would mislabel H2-5 and H2-6.
KITCHEN_LOCAL_SENSOR_PINS = [100, 101, 102, 103, 105, 106]

# 12-bit ADC spanning the 4-20 mA loop, matching kitchen_core_sim.ma_to_counts.
_ADC_FULL_SCALE_COUNTS = 4095
_LOOP_MIN_MA = 4.0
_LOOP_SPAN_MA = 16.0


def counts_to_ma(counts: int) -> float:
    """ADC counts -> loop current, the inverse of kitchen_core_sim's
    ma_to_counts(). The sim holds sensor values as counts (that is the space
    the danger/quorum comparisons happen in, same as the firmware), but the
    wire carries raw mA, so publishing has to invert the mapping."""
    frac = counts / _ADC_FULL_SCALE_COUNTS
    return _LOOP_MIN_MA + frac * _LOOP_SPAN_MA


def local_sensor_topic(location: str, device_id: str, sensor_name: str) -> str:
    """Mirrors publishOne()'s snprintf of
    "DataAcquisition/%s/%s/%s" — the topic tree DataAcquisition subscribes
    to as `DataAcquisition/#`, and the same one app/mqtt.py watches."""
    return f"DataAcquisition/{location}/{device_id}/{sensor_name}"


def build_local_sensor_payload(*, pin: int, counts: int, ts_ms: int) -> str:
    """The exact shape protocolBuildSensorSample() emits for a current sensor:
    {"pin":N,"type":"current","raw_ma":X,"ts":T}.

    `pin` is the ENCODED pin (KITCHEN_LOCAL_SENSOR_PINS[i]), never the array
    index — DataAcquisition's ingest keys storage on the pin whenever one is
    present, so an index here would store every sensor under the wrong pin.
    """
    return json.dumps({
        "pin": pin,
        "type": "current",
        "raw_ma": round(counts_to_ma(counts), 4),
        "ts": ts_ms,
    })
