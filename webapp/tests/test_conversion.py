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


# --- ppm-calibrated sensors (problems.txt B3) -----------------------------
#
# Some DataAcquisition sensors are calibrated in ppm, but every consumer of a
# reading in this app (heatmap colour scale, quorum threshold, threshold form,
# plots) assumes the number it gets is already %v/v. Normalising here, at the
# single boundary that already applies calibrations, keeps that assumption
# true everywhere downstream instead of teaching each consumer about units.

def test_ppm_is_normalised_to_pct_vv():
    """10000 ppm is 1 %v/v — the heatmap must not paint it as 10000 %v/v."""
    conv = {
        "unit_symbol": "ppm",
        "params": {"method": "linear", "raw_min": 4, "raw_max": 20,
                   "min_value": 0, "max_value": 10000},
    }
    value, unit, converted = convert_current(20.0, conv)
    assert value == pytest.approx(1.0)
    assert unit == "%v/v"
    assert converted


def test_ppm_below_the_lel_stays_below_the_lel():
    """5000 ppm = 0.5 %v/v, well under hydrogen's 4 %v/v LEL. Before this
    normalisation it read as 5000 %v/v: saturated red, and enough to satisfy
    any quorum threshold instantly."""
    conv = {
        "unit_symbol": "ppm",
        "params": {"method": "linear", "raw_min": 4, "raw_max": 20,
                   "min_value": 0, "max_value": 10000},
    }
    value, unit, _ = convert_current(12.0, conv)
    assert value == pytest.approx(0.5)
    assert unit == "%v/v"


def test_ppm_normalisation_applies_to_every_supported_method():
    """The unit is a property of the calibration's output, not of how it was
    computed, so ax_b and raw normalise the same way linear does."""
    ax_b = {"unit_symbol": "ppm", "params": {"method": "ax_b", "a": 1000, "b": 0}}
    assert convert_current(2.0, ax_b)[0] == pytest.approx(0.2)
    assert convert_current(2.0, ax_b)[1] == "%v/v"

    raw = {"unit_symbol": "ppm", "params": {"method": "raw"}}
    assert convert_current(10000.0, raw)[0] == pytest.approx(1.0)
    assert convert_current(10000.0, raw)[1] == "%v/v"


def test_ppm_normalisation_is_case_and_spacing_tolerant():
    """DataAcquisition's unit_symbol is free text typed by an operator."""
    for symbol in ("ppm", "PPM", " ppm ", "pPm"):
        conv = {"unit_symbol": symbol, "params": {"method": "raw"}}
        value, unit, converted = convert_current(10000.0, conv)
        assert value == pytest.approx(1.0), symbol
        assert unit == "%v/v", symbol
        assert converted, symbol


def test_pct_vv_is_left_untouched():
    """The common case must not be disturbed by the ppm branch."""
    conv = {
        "unit_symbol": "%v/v",
        "params": {"method": "linear", "raw_min": 4, "raw_max": 20,
                   "min_value": 0, "max_value": 4},
    }
    value, unit, converted = convert_current(12.0, conv)
    assert value == pytest.approx(2.0)
    assert unit == "%v/v"
    assert converted


def test_voltage_ppm_is_normalised_too():
    """Remote CM7 acquisition PLCs are the likeliest ppm source of all."""
    conv = {
        "unit_symbol": "ppm",
        "params": {"method": "linear", "raw_min": 0, "raw_max": 10,
                   "min_value": 0, "max_value": 20000},
    }
    value, unit, converted = convert_sample("voltage", 5.0, conv)
    assert value == pytest.approx(1.0)
    assert unit == "%v/v"
    assert converted
