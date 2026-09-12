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
