"""
app.conversion — applies DataAcquisition's stored calibrations to live samples.

**This module defines no calibration of its own.** DataAcquisition owns
conversion definitions (design spec, "Division of responsibility": *Conversion
definitions (V→ppm, mA→%v/v) → DataAcquisition*); the configs are fetched over
its REST API (`daq.get_conversions`) and only APPLIED here, so recalibrating a
sensor is one edit in DataAcquisition and needs no change in this repo.

Why apply them here at all rather than reading converted values from the
historian: live sensor data comes straight off MQTT, not through DAQ (design
spec rule 3 — heatmap latency must not depend on the historian, and the
historian is not in the safety path). The firmware publishes raw mA; something
on this side has to turn that into the %v/v the heatmap and the operator read.

Scope is deliberately narrower than DataAcquisition's engine: the kitchen's
sensors are all 4-20 mA current inputs (kitchen/Kitchen_Settings.h,
KITCHEN_LOCAL_SENSOR_IS_CURRENT is all-true), so only the calibration methods
reachable from a `current` signal are implemented. `custom` formulas are NOT
supported here — evaluating arbitrary expressions needs DataAcquisition's AST
sandbox, and a safety display should not quietly run one. A sensor configured
with an unsupported method falls back exactly like an unconfigured one, which
is visible rather than wrong.
"""

from typing import Optional

# Methods a `current` (4-20 mA) signal can legitimately use. Mirrors the
# subset of DataAcquisition/dashboard/app/conversion.py::_calibrate reachable
# from a current signal — the PWM rate track (rpm/pump/flow) and `custom` are
# out of scope (see the module docstring).
_SUPPORTED_METHODS = frozenset({"raw", "linear", "ax_b"})

# Legacy conversion_type -> method, for pre-redesign DAQ rows that store no
# explicit method. Same mapping as DataAcquisition's _LEGACY_TYPE_MAP, limited
# to the current-signal entries.
_LEGACY_METHOD = {
    "current": "raw",
    "current_linear": "linear",
}


def resolve_method(conv: dict) -> str:
    """The calibration method a stored conversion asks for.

    New-style DAQ configs carry `method` explicitly (inside `params` for rows
    written through the new model, or at the top level); older rows carry only
    `conversion_type`. Unknown/absent resolves to "raw" — a passthrough, never
    a guess at a scale.
    """
    params = conv.get("params") or {}
    method = conv.get("method") or params.get("method")
    if method:
        return str(method)
    return _LEGACY_METHOD.get(str(conv.get("conversion_type", "")), "raw")


def convert_current(raw_ma: float, conv: Optional[dict]) -> tuple[float, str, bool]:
    """Turn a raw mA reading into its configured physical value.

    Returns `(value, unit, converted)`. `converted` is False when no usable
    calibration applied — the caller decides what to show for that case rather
    than this module inventing a concentration (see mqtt.py's fallback).

    A malformed config (non-numeric params, zero span) also returns
    converted=False: a broken calibration must read as "not converted", never
    as a plausible-looking wrong number on a safety surface.
    """
    if not conv:
        return raw_ma, "mA", False

    method = resolve_method(conv)
    if method not in _SUPPORTED_METHODS:
        return raw_ma, "mA", False

    params = conv.get("params") or {}
    unit = conv.get("unit_symbol") or "mA"

    try:
        if method == "raw":
            # Explicit passthrough: the sensor IS reported in mA. Converted,
            # because that is what the calibration actually asks for.
            return raw_ma, unit, True

        if method == "linear":
            # Defaults match DataAcquisition's _calibrate() for a current
            # signal: the full 4-20 mA hardware span.
            raw_min = float(params.get("raw_min", 4.0))
            raw_max = float(params.get("raw_max", 20.0))
            min_val = float(params.get("min_value", 0.0))
            max_val = float(params.get("max_value", 100.0))
            span = raw_max - raw_min
            if span == 0:
                return raw_ma, "mA", False
            return (min_val + (raw_ma - raw_min) / span * (max_val - min_val)), unit, True

        # ax_b
        a = float(params.get("a", 1.0))
        b = float(params.get("b", 0.0))
        return a * raw_ma + b, unit, True
    except (TypeError, ValueError):
        return raw_ma, "mA", False
