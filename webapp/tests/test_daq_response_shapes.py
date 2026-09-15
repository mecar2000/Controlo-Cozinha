"""
app.daq — unwrapping DataAcquisition's actual response shapes.

DataAcquisition wraps its list and create responses in an envelope object
rather than returning the array/record directly (confirmed against the real
service, not assumed):

    GET  /experiments            -> {"experiments": [...], "active_id": ...,
                                      "active_name": ..., "location": ...}
    GET  /experiments/<id>/stages -> {"stages": [...], "counts": [...]}
    POST /experiments            -> {"experiment": {"id": ..., "name": ...,
                                      "table_name": ...}, "ok": true}

Earlier code (and the fake_daq test fixture in test_runs.py) assumed each of
these returned the bare list or record, which crashed the frontend's
experiment picker (`experiments.map is not a function`) and silently broke
every DAQ-recorded run whose experiment was created fresh rather than
selected from an existing one (`experiment_id_of` found no top-level "id" and
raised DaqUnreachable on every such run).
"""

import pytest

import app.daq as daq
from app.daq import DaqUnreachable


def test_list_experiments_unwraps_the_envelope(monkeypatch):
    monkeypatch.setattr(
        daq,
        "_request",
        lambda method, path, **kw: {
            "experiments": [{"id": 1, "name": "Exp1"}],
            "active_id": None,
            "active_name": "",
            "location": "",
        },
    )
    assert daq.list_experiments() == [{"id": 1, "name": "Exp1"}]


def test_list_experiments_tolerates_a_bare_list(monkeypatch):
    """Defensive: if DataAcquisition ever returns the array directly, that
    still works rather than being treated as the wrong shape."""
    monkeypatch.setattr(daq, "_request", lambda method, path, **kw: [{"id": 1}])
    assert daq.list_experiments() == [{"id": 1}]


def test_list_stages_unwraps_the_envelope(monkeypatch):
    monkeypatch.setattr(
        daq,
        "_request",
        lambda method, path, **kw: {"stages": [{"name": "LEAKING"}], "counts": []},
    )
    assert daq.list_stages(13) == [{"name": "LEAKING"}]


def test_experiment_id_of_unwraps_the_create_envelope():
    """The real POST /experiments body nests the record under "experiment",
    not at the top level."""
    created = {
        "experiment": {"id": 13, "name": "Exp1", "table_name": "exp1"},
        "ok": True,
    }
    assert daq.experiment_id_of(created) == 13


def test_experiment_id_of_still_accepts_a_flat_record():
    """Backward-compatible with a hypothetical unwrapped response."""
    assert daq.experiment_id_of({"id": 42}) == 42


def test_experiment_id_of_raises_when_no_id_is_found_anywhere():
    with pytest.raises(DaqUnreachable, match="no id"):
        daq.experiment_id_of({"ok": True})


# --- /history/experiment* param name (problems.txt: "GET /history/
#     experiment: id param required") -----------------------------------
#
# DataAcquisition's own /history/experiment* routes (dashboard/app/routes/
# data.py) read the experiment id as `id`, not `experiment_id`. This app's
# internal function/route names keep `experiment_id` throughout (it's the
# clearer name); only the outbound param crossing into DataAcquisition needs
# the rename, at daq.py's three history calls.


def _capture_request(monkeypatch):
    calls = []
    monkeypatch.setattr(
        daq, "_request", lambda method, path, **kw: calls.append((method, path, kw)) or {}
    )
    return calls


def test_get_history_experiment_sends_id_not_experiment_id(monkeypatch):
    calls = _capture_request(monkeypatch)
    daq.get_history_experiment(42)
    method, path, kw = calls[0]
    assert path == "/history/experiment"
    assert kw["params"]["id"] == 42
    assert "experiment_id" not in kw["params"]


def test_get_history_window_sends_id_not_experiment_id(monkeypatch):
    calls = _capture_request(monkeypatch)
    daq.get_history_window(42, 1000, 2000)
    method, path, kw = calls[0]
    assert path == "/history/experiment/window"
    assert kw["params"]["id"] == 42
    assert kw["params"]["start_ms"] == 1000
    assert kw["params"]["end_ms"] == 2000
    assert "experiment_id" not in kw["params"]


def test_get_history_experiment_stage_sends_id_and_stage(monkeypatch):
    """This endpoint also requires `stage` — DataAcquisition 400s without it
    regardless of the id param name, and this app never sent one before."""
    calls = _capture_request(monkeypatch)
    daq.get_history_experiment_stage(42, "run3-leak")
    method, path, kw = calls[0]
    assert path == "/history/experiment/stage"
    assert kw["params"]["id"] == 42
    assert kw["params"]["stage"] == "run3-leak"


# --- GET /devices (Part 5: PLC/sensor commissioning wizard, Stage 1) -------
#
# DataAcquisition wraps this too: {"devices": [...]}, each device carrying
# device_id, location, status, expansions, base_pins, interval_ms, and a
# nested config: {sensors: [{pin, name, type}]} (confirmed against
# dashboard/app/routes/core.py and db/devices.py, not assumed).


def test_list_devices_unwraps_the_envelope(monkeypatch):
    device = {
        "device_id": "mainBoard",
        "location": "Kitchen",
        "status": "online",
        "expansions": 1,
        "base_pins": 8,
        "interval_ms": 1000,
        "config": {"sensors": [{"pin": 0, "name": "H2-1", "type": "current"}]},
    }
    monkeypatch.setattr(daq, "_request", lambda method, path, **kw: {"devices": [device]})
    assert daq.list_devices() == [device]


def test_list_devices_tolerates_a_bare_list(monkeypatch):
    monkeypatch.setattr(daq, "_request", lambda method, path, **kw: [{"device_id": "cm7-1"}])
    assert daq.list_devices() == [{"device_id": "cm7-1"}]


def test_list_devices_requests_the_devices_path(monkeypatch):
    calls = _capture_request(monkeypatch)
    daq.list_devices()
    method, path, kw = calls[0]
    assert method == "GET"
    assert path == "/devices"


# --- pin_label (Part 5) -----------------------------------------------------
#
# Mirrors DataAcquisition's own dashboard/app/conversion.py::_pin_label
# exactly (base 0..7 -> "A0".."A7"; expansion 100*(e+1)+ch -> "E{e}:CH{ch}")
# so the pin label shown here always matches DAQ's own — this is the only
# place this app implements the encoding.


def test_pin_label_base_board_pins():
    assert daq.pin_label(0) == "A0"
    assert daq.pin_label(7) == "A7"


def test_pin_label_expansion_zero_boundary():
    """100*(0+1)+0 = 100 is the first expansion pin — the boundary between
    base and expansion encoding."""
    assert daq.pin_label(100) == "E0:CH0"


def test_pin_label_expansion_pins():
    assert daq.pin_label(101) == "E0:CH1"
    assert daq.pin_label(206) == "E1:CH6"


# --- push_config (Part 5, Stage 2) ------------------------------------------
#
# POST /config is a FULL REPLACE in DataAcquisition — it DELETEs + re-INSERTs
# the device's whole sensor list (dashboard/db/devices.py::save_device_config).
# push_config() is a thin wrapper; the caller (routes/daq_proxy.py) is
# responsible for re-fetching immediately before calling this, since there is
# no version/etag to protect a stale write.


def test_push_config_posts_to_the_config_path(monkeypatch):
    calls = _capture_request(monkeypatch)
    daq.push_config("mainBoard", [{"pin": 0, "name": "H2-1", "type": "current"}])
    method, path, kw = calls[0]
    assert method == "POST"
    assert path == "/config"
    assert kw["json"]["device_id"] == "mainBoard"
    assert kw["json"]["sensors"] == [{"pin": 0, "name": "H2-1", "type": "current"}]


def test_push_config_includes_interval_ms_when_given(monkeypatch):
    calls = _capture_request(monkeypatch)
    daq.push_config("mainBoard", [], interval_ms=500)
    method, path, kw = calls[0]
    assert kw["json"]["interval_ms"] == 500


def test_push_config_omits_interval_ms_when_not_given(monkeypatch):
    calls = _capture_request(monkeypatch)
    daq.push_config("mainBoard", [])
    method, path, kw = calls[0]
    assert "interval_ms" not in kw["json"]


# --- get_conversions (real-service shape drift, found via live testing) ----
#
# GET /conversions/<device_id> (dashboard/app/routes/data.py::get_conversions)
# currently answers {"conversions": {sensor_name: {...}, ...}, "conversions_
# list": [...]} — "conversions" is a DICT keyed by sensor_name, despite that
# route's own docstring calling it "a list" ("conversions_list" is the actual
# list). This app originally assumed "conversions" was always the list shape
# the confusing name suggests, which crashed with `'str' object has no
# attribute 'get'` in mqtt.py's periodic conversion refresh (iterating a
# dict yields its keys, i.e. sensor-name strings, not the records) — found
# running the real webapp against real DataAcquisition, not from a test.


def test_get_conversions_handles_the_real_dict_shape(monkeypatch):
    monkeypatch.setattr(
        daq, "_request",
        lambda method, path, **kw: {
            "device_id": "KITCHEN-01",
            "conversions": {
                "A0_voltage": {"sensor_name": "A0_voltage", "unit_symbol": "%v/v"},
            },
            "conversions_list": [
                {"sensor_name": "A0_voltage", "unit_symbol": "%v/v"},
            ],
        },
    )
    assert daq.get_conversions("KITCHEN-01") == {
        "A0_voltage": {"sensor_name": "A0_voltage", "unit_symbol": "%v/v"},
    }


def test_get_conversions_still_handles_a_list_shape(monkeypatch):
    """Defensive: if DataAcquisition is ever fixed to match its own
    docstring (or reverts), the originally-assumed list shape still works."""
    monkeypatch.setattr(
        daq, "_request",
        lambda method, path, **kw: {
            "conversions": [
                {"sensor_name": "H2-1", "unit_symbol": "%v/v"},
            ],
        },
    )
    assert daq.get_conversions("mainBoard") == {
        "H2-1": {"sensor_name": "H2-1", "unit_symbol": "%v/v"},
    }


def test_get_conversions_tolerates_missing_conversions_key(monkeypatch):
    monkeypatch.setattr(daq, "_request", lambda method, path, **kw: {"device_id": "x"})
    assert daq.get_conversions("x") == {}
