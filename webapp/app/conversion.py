"""
app.conversion — applies DataAcquisition's stored calibrations to live samples.

**This module defines no calibration of its own.** DataAcquisition owns
conversion definitions (design spec, "Division of responsibility": *Conversion
definitions (V→ppm, mA→%v/v) → DataAcquisition*); the configs are fetched over
its REST API (`daq.get_conversions`) and only APPLIED here, so recalibrating a
sensor is one edit — either directly in DataAcquisition, or through this app's
own calibration editor, which itself writes through to DataAcquisition's
`/conversions/{device}/{sensor}` endpoint (see `app.daq.set_conversion`) so
there is still exactly one stored copy and one `conv_id` provenance chain.

Why apply them here at all rather than reading converted values from the
historian: live sensor data comes straight off MQTT, not through DAQ (design
spec rule 3 — heatmap latency must not depend on the historian, and the
historian is not in the safety path). The firmware publishes raw mA/V;
something on this side has to turn that into the %v/v the heatmap and the
operator read.

Scope is deliberately narrower than DataAcquisition's engine: the kitchen's
own sensors are 4-20 mA current inputs (kitchen/Kitchen_Settings.h,
KITCHEN_LOCAL_SENSOR_IS_CURRENT is all-true) and remote acquisition PLCs
(CM7) publish 0-10 V, so only the calibration methods reachable from a
`current` or `voltage` signal are implemented. `custom` formulas are NOT
supported here — evaluating arbitrary expressions needs DataAcquisition's AST
sandbox, and a safety display should not quietly run one. A sensor configured
with an unsupported method falls back exactly like an unconfigured one, which
is visible rather than wrong.

One unit conversion DOES happen here: a calibration whose `unit_symbol` is ppm
is normalised to %v/v (see _normalise_unit). That is not a calibration of our
own — it is the same physical quantity in the unit every consumer of a reading
in this app already assumes it is getting.
"""

from typing import Optional

# Methods a `current` (4-20 mA) or `voltage` (0-10 V) signal can legitimately
# use. Mirrors the subset of DataAcquisition/dashboard/app/conversion.py's
# _calibrate reachable from those two signal types — the PWM rate track
# (rpm/pump/flow) and `custom` are out of scope (see the module docstring).
_SUPPORTED_METHODS = frozenset({"raw", "linear", "ax_b"})

# Signal-appropriate defaults for the `linear` method's raw span, mirroring
# DataAcquisition's own per-signal-type defaults in _calibrate(). A current
# signal is a 4-20 mA loop; a voltage signal is the base-board 0-10 V ADC
# range (CM7/CM7_Settings.h ADC_FULL_SCALE_V).
_RAW_SPAN_DEFAULTS = {
    "current": (4.0, 20.0),
    "voltage": (0.0, 10.0),
}

# Legacy conversion_type -> method, for pre-redesign DAQ rows that store no
# explicit method. Same mapping as DataAcquisition's _LEGACY_TYPE_MAP, limited
# to the current- and voltage-signal entries.
_LEGACY_METHOD = {
    "current": "raw",
    "current_linear": "linear",
    "voltage": "raw",
    "voltage_linear": "linear",
}

# Parts per million -> percent by volume. The only unit conversion this module
# performs: see _normalise_unit().
_PPM_PER_PCT_VV = 10_000.0


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


def _normalise_unit(value: float, unit: str) -> tuple[float, str]:
    """Express a converted reading in %v/v when DataAcquisition stored it in ppm.

    Some sensors are calibrated in ppm there (problems.txt B3), but every
    consumer of a reading in this app — the heatmap colour scale, the quorum
    threshold, the threshold form, the plots — assumes the number it is handed
    is already %v/v. Normalising at this one boundary keeps that assumption
    true everywhere downstream, instead of teaching each of those consumers
    about units and hoping none is ever missed.

    Left deliberately narrow: ppm is the only alternative unit in use, so
    anything else passes through untouched rather than being guessed at.
    """
    if unit.strip().lower() == "ppm":
        # 1 %v/v = 10 000 ppm (parts per million, by volume).
        return value / _PPM_PER_PCT_VV, "%v/v"
    return value, unit


def convert_sample(signal_type: str, raw: float, conv: Optional[dict]) -> tuple[float, str, bool]:
    """Turn a raw hardware reading (mA or V) into its configured physical value.

    `signal_type` is "current" or "voltage" — it only selects the default raw
    span for the `linear` method; everything else is signal-agnostic.

    Returns `(value, unit, converted)`. `converted` is False when no usable
    calibration applied — the caller decides what to show for that case rather
    than this module inventing a concentration (see mqtt.py's fallback).

    A malformed config (non-numeric params, zero span) also returns
    converted=False: a broken calibration must read as "not converted", never
    as a plausible-looking wrong number on a safety surface.
    """
    raw_unit = "mA" if signal_type == "current" else "V"

    if not conv:
        return raw, raw_unit, False

    method = resolve_method(conv)
    if method not in _SUPPORTED_METHODS:
        return raw, raw_unit, False

    params = conv.get("params") or {}
    unit = conv.get("unit_symbol") or raw_unit
    default_min, default_max = _RAW_SPAN_DEFAULTS.get(signal_type, (0.0, 1.0))

    try:
        if method == "raw":
            # Explicit passthrough: the sensor IS reported in its raw unit.
            # Converted, because that is what the calibration actually asks for.
            return (*_normalise_unit(raw, unit), True)

        if method == "linear":
            raw_min = float(params.get("raw_min", default_min))
            raw_max = float(params.get("raw_max", default_max))
            min_val = float(params.get("min_value", 0.0))
            max_val = float(params.get("max_value", 100.0))
            span = raw_max - raw_min
            if span == 0:
                return raw, raw_unit, False
            scaled = min_val + (raw - raw_min) / span * (max_val - min_val)
            return (*_normalise_unit(scaled, unit), True)

        # ax_b
        a = float(params.get("a", 1.0))
        b = float(params.get("b", 0.0))
        return (*_normalise_unit(a * raw + b, unit), True)
    except (TypeError, ValueError):
        return raw, raw_unit, False


def convert_current(raw_ma: float, conv: Optional[dict]) -> tuple[float, str, bool]:
    """Back-compat wrapper over convert_sample() for the current (4-20 mA)
    case — kept so existing call sites and tests need no change."""
    return convert_sample("current", raw_ma, conv)
