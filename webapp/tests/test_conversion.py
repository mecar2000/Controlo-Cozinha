"""Applying DataAcquisition's calibrations to raw mA samples.

The rule under test throughout: a reading is either converted with a real
calibration, or it is flagged unconverted. It is never silently turned into a
plausible-looking concentration — that is what would put a wrong number on the
safety heatmap.
"""

import pytest

from app.conversion import convert_current, convert_sample, resolve_method


# --- the happy path: DataAcquisition's 4-20 mA -> %v/v linear -------------

def test_linear_maps_the_current_span_onto_the_configured_range():
    conv = {
        "conversion_type": "current_linear",
        "unit_symbol": "%v/v",
        "params": {"method": "linear", "raw_min": 4, "raw_max": 20,
                   "min_value": 0, "max_value": 4},
    }
    # Midscale current -> midscale concentration.
    value, unit, converted = convert_current(12.0, conv)
    assert value == pytest.approx(2.0)
    assert unit == "%v/v"
    assert converted

    # Both endpoints land exactly, so the span is not off by a step.
    assert convert_current(4.0, conv)[0] == pytest.approx(0.0)
    assert convert_current(20.0, conv)[0] == pytest.approx(4.0)


def test_linear_defaults_match_the_4_20ma_hardware_span():
    """Params omitted -> DataAcquisition's own defaults for a current signal."""
    conv = {"params": {"method": "linear"}, "unit_symbol": "%"}
    # 4..20 mA -> 0..100 by default; 12 mA is midscale.
    assert convert_current(12.0, conv)[0] == pytest.approx(50.0)


def test_ax_b_applies_slope_and_offset():
    conv = {"params": {"method": "ax_b", "a": 0.25, "b": -1.0}, "unit_symbol": "%v/v"}
    value, unit, converted = convert_current(20.0, conv)
    assert value == pytest.approx(4.0)
    assert unit == "%v/v"
    assert converted


def test_raw_method_passes_the_current_through_as_configured():
    conv = {"params": {"method": "raw"}, "unit_symbol": "mA"}
    value, unit, converted = convert_current(12.345, conv)
    assert value == pytest.approx(12.345)
    assert unit == "mA"
    # Converted: "report it in mA" is what this calibration actually asks for.
    assert converted


# --- everything that must NOT produce a concentration ---------------------

def test_no_conversion_is_flagged_unconverted():
    value, unit, converted = convert_current(12.345, None)
    assert value == pytest.approx(12.345)
    assert unit == "mA"
    assert not converted


def test_unsupported_method_falls_back_rather_than_guessing():
    """`custom` needs DataAcquisition's AST sandbox; a safety display must not
    quietly run an arbitrary formula, so it reads as unconverted."""
    conv = {"params": {"method": "custom", "formula": "raw * 2"}, "unit_symbol": "%v/v"}
    value, unit, converted = convert_current(12.0, conv)
    assert value == pytest.approx(12.0)
    assert unit == "mA"
    assert not converted


def test_degenerate_span_does_not_divide_by_zero():
    conv = {"params": {"method": "linear", "raw_min": 4, "raw_max": 4}, "unit_symbol": "%v/v"}
    value, unit, converted = convert_current(12.0, conv)
    assert not converted
    assert unit == "mA"


def test_malformed_params_read_as_unconverted_not_as_a_number():
    conv = {"params": {"method": "linear", "raw_min": "abc"}, "unit_symbol": "%v/v"}
    value, unit, converted = convert_current(12.0, conv)
    assert not converted
    assert value == pytest.approx(12.0)


# --- legacy DAQ rows that carry no explicit method ------------------------

def test_legacy_conversion_type_resolves_to_a_method():
    assert resolve_method({"conversion_type": "current_linear"}) == "linear"
    assert resolve_method({"conversion_type": "current"}) == "raw"
    # Unknown/absent never guesses a scale.
    assert resolve_method({}) == "raw"
    assert resolve_method({"conversion_type": "nonsense"}) == "raw"


def test_explicit_method_wins_over_legacy_type():
    conv = {"conversion_type": "current", "method": "linear"}
    assert resolve_method(conv) == "linear"


# --- voltage signals (remote CM7 acquisition PLCs, 0-10 V) ----------------

def test_voltage_linear_defaults_match_the_0_10v_hardware_span():
    """Params omitted -> the base-board 0-10 V ADC span, not the 4-20 mA one."""
    conv = {"params": {"method": "linear"}, "unit_symbol": "%v/v"}
    # 0..10 V -> 0..100 by default; 5 V is midscale.
    assert convert_sample("voltage", 5.0, conv)[0] == pytest.approx(50.0)


def test_voltage_linear_maps_the_configured_span():
    conv = {"params": {"method": "linear", "raw_min": 0, "raw_max": 10,
                        "min_value": 0, "max_value": 4}, "unit_symbol": "%v/v"}
    value, unit, converted = convert_sample("voltage", 2.5, conv)
    assert value == pytest.approx(1.0)
    assert unit == "%v/v"
    assert converted


def test_voltage_ax_b_applies_slope_and_offset():
    conv = {"params": {"method": "ax_b", "a": 0.4, "b": -1.0}, "unit_symbol": "%v/v"}
    value, unit, converted = convert_sample("voltage", 10.0, conv)
    assert value == pytest.approx(3.0)
    assert converted


def test_voltage_raw_method_passes_through_in_volts():
    conv = {"params": {"method": "raw"}, "unit_symbol": "V"}
    value, unit, converted = convert_sample("voltage", 4.2, conv)
    assert value == pytest.approx(4.2)
    assert unit == "V"
    assert converted


def test_voltage_no_conversion_is_flagged_unconverted():
    value, unit, converted = convert_sample("voltage", 4.2, None)
    assert value == pytest.approx(4.2)
    assert unit == "V"
    assert not converted


def test_voltage_degenerate_span_falls_back_to_raw_unit():
    conv = {"params": {"method": "linear", "raw_min": 0, "raw_max": 0}, "unit_symbol": "%v/v"}
    value, unit, converted = convert_sample("voltage", 4.2, conv)
    assert not converted
    assert unit == "V"


def test_legacy_voltage_conversion_type_resolves_to_a_method():
    assert resolve_method({"conversion_type": "voltage_linear"}) == "linear"
    assert resolve_method({"conversion_type": "voltage"}) == "raw"


def test_convert_current_is_a_thin_wrapper_over_convert_sample():
    conv = {"params": {"method": "linear"}, "unit_symbol": "%"}
    assert convert_current(12.0, conv) == convert_sample("current", 12.0, conv)
