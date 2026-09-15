"""
Tests for app.phase_watcher — the thread that turns firmware phase changes
into DataAcquisition stages and run outcomes.

The cases here are the ones where the watcher silently does nothing, which is
the failure mode that matters: a run whose stages never got mapped, or which
was never marked latched, looks like a normal run in the record afterwards.
"""

import pytest

import app.phase_watcher as pw
import app.state as state


class FakeDb:
    def __init__(self):
        self.runs = {}
        self._next_id = 1

    def add_run(self, *, ended=False, outcome="pending", run_number=None, confirmed=False):
        run_id = self._next_id
        self._next_id += 1
        self.runs[run_id] = {
            "id": run_id,
            "run_number": run_number or run_id,
            "daq_experiment_id": 42,
            "outcome": outcome,
            "ended_at": "now" if ended else None,
            "confirmed_at": "now" if confirmed else None,
        }
        return run_id

    def get_run(self, run_id):
        return dict(self.runs[run_id]) if run_id in self.runs else None

    def get_active_run(self):
        live = [r for r in self.runs.values() if r["ended_at"] is None]
        return dict(live[-1]) if live else None

    def get_latest_run(self):
        return dict(list(self.runs.values())[-1]) if self.runs else None


@pytest.fixture
def fake(monkeypatch):
    """Resets the watcher's module state and captures what it calls."""
    db = FakeDb()
    calls = {"stages": [], "latched": [], "completed": [], "expired": []}

    monkeypatch.setattr(pw, "db", db)
    monkeypatch.setattr(pw, "_last_phase", None, raising=False)
    monkeypatch.setattr(pw, "_last_ack_required", False, raising=False)
    monkeypatch.setattr(pw, "_last_run_id", None, raising=False)

    def on_phase_transition(run_id, phase):
        calls["stages"].append((run_id, phase))

    def end_run_from_latch(run_id, cause, detail=None):
        calls["latched"].append((run_id, cause, detail))
        db.runs[run_id].update(outcome="latched", ended_at="now")

    def end_run_completed(run_id):
        calls["completed"].append(run_id)
        db.runs[run_id].update(outcome="completed", ended_at="now")

    def end_run_expired(run_id):
        calls["expired"].append(run_id)
        db.runs[run_id].update(outcome="expired", ended_at="now")

    monkeypatch.setattr(pw.runs, "on_phase_transition", on_phase_transition)
    monkeypatch.setattr(pw.runs, "end_run_from_latch", end_run_from_latch)
    monkeypatch.setattr(pw.runs, "end_run_completed", end_run_completed)
    monkeypatch.setattr(pw.runs, "end_run_expired", end_run_expired)
    return db, calls


def _publish(**kstate):
    state.set_kitchen_state(kstate)
    pw._poll_once()


def test_phase_transition_maps_a_stage(fake):
    db, calls = fake
    run_id = db.add_run()
    _publish(phase="LEAKING")
    assert calls["stages"] == [(run_id, "LEAKING")]


def test_repeated_phase_is_not_remapped(fake):
    db, calls = fake
    db.add_run()
    _publish(phase="LEAKING")
    _publish(phase="LEAKING")
    assert len(calls["stages"]) == 1


def test_reaching_waiting_completes_a_confirmed_run(fake):
    db, calls = fake
    run_id = db.add_run(confirmed=True)
    _publish(phase="LEAKING")
    _publish(phase="WAITING")
    assert calls["completed"] == [run_id]
    assert calls["expired"] == []


def test_reaching_waiting_without_ever_confirming_expires_not_completes(fake):
    """ARMED reverts to WAITING on its own after ARM_TIMEOUT_MS if nobody
    confirms — phase goes straight WAITING -> ARMED -> WAITING with no
    LEAKING in between. That must not be recorded as 'completed': no gas
    ever flowed, and confirmed_at was never set."""
    db, calls = fake
    run_id = db.add_run(confirmed=False)
    _publish(phase="ARMED")
    _publish(phase="WAITING")
    assert calls["expired"] == [run_id]
    assert calls["completed"] == []


def test_latch_is_caught_when_ack_required_rises_without_a_phase_change(fake):
    """ackRequired is an independent flag on the same payload: the firmware
    can raise it while already sitting in FULLY_VENTILATING. Gating latch
    detection on a phase CHANGE would leave the run un-latched in the record."""
    db, calls = fake
    run_id = db.add_run()
    _publish(phase="FULLY_VENTILATING", ackRequired=False)
    assert calls["latched"] == []

    _publish(phase="FULLY_VENTILATING", ackRequired=True, dangerReason="ESTOP")
    assert calls["latched"] == [(run_id, "ESTOP", None)]


def test_latch_on_entry_to_fully_ventilating(fake):
    db, calls = fake
    run_id = db.add_run()
    _publish(phase="HOLD")
    _publish(phase="FULLY_VENTILATING", ackRequired=True, dangerReason="PEER_ALARM")
    assert calls["latched"] == [(run_id, "PEER_ALARM", None)]


def test_a_dead_run_does_not_hide_the_live_one(fake):
    """A rejected start creates an already-ended run row. Asking for the
    'latest' run would return that one and the watcher would go silent for the
    rest of the real run — no stages, no outcome."""
    db, calls = fake
    live_run = db.add_run()
    db.add_run(ended=True, outcome="rejected")  # rejected start, lands later

    _publish(phase="LEAKING")
    assert calls["stages"] == [(live_run, "LEAKING")]


def test_no_active_run_maps_nothing(fake):
    db, calls = fake
    db.add_run(ended=True, outcome="completed")
    _publish(phase="LEAKING")
    assert calls["stages"] == []


def test_a_new_run_resets_the_transition_baseline(fake):
    """Run 2 opening on the same phase run 1 last reported must still map:
    otherwise a restart that re-reads the retained topic loses the first
    stage of the new run."""
    db, calls = fake
    first = db.add_run()
    _publish(phase="LEAKING")
    db.runs[first].update(ended_at="now", outcome="completed")

    second = db.add_run()
    _publish(phase="LEAKING")
    assert calls["stages"] == [(first, "LEAKING"), (second, "LEAKING")]


def test_already_ended_run_is_not_latched_twice(fake):
    db, calls = fake
    run_id = db.add_run()
    _publish(phase="FULLY_VENTILATING", ackRequired=True, dangerReason="ESTOP")
    assert len(calls["latched"]) == 1
    # The run is closed now; a repeat publish must not re-close it.
    _publish(phase="WAITING")
    assert len(calls["latched"]) == 1


def test_empty_phase_is_ignored(fake):
    db, calls = fake
    db.add_run()
    _publish(phase="")
    assert calls["stages"] == []
