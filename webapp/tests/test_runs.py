"""
Integration-style tests for app.runs.start_run — the single entry point —
against a stubbed app.db, app.daq and app.commands. Covers the cases the
design spec calls out explicitly under 'Integration':
  - two-phase start including ack-mismatch and rejection paths
  - DAQ-down blocks start
  - display mode refuses start server-side but permits stop (stop is tested
    in test_commands.py since it never goes through runs.py at all)

Plus the invariants that keep the physical system honest:
  - recording is stopped on every path where the run does not happen, and
    when the run ends — DAQ is otherwise left recording forever
  - confirm() is verified server-side against the runId the run was started
    with, so only a reviewed spec can let gas flow
  - one run at a time
"""

import pytest

import app.db as db
import app.runs as runs
import app.state as state
from app.commands import CommandError
from app.daq import DaqRejected
from app.runs import RunConfirmRejected, RunStartRejected


class FakeDb:
    """In-memory stand-in for app.db, just enough surface for start_run."""

    def __init__(self):
        self.configs = {}
        self.runs = {}
        self._next_run_id = 1
        self._next_run_number = 1

    def get_config(self, config_id):
        return self.configs.get(config_id)

    def next_run_number(self):
        return self._next_run_number

    def run_name_exists(self, name):
        return any(r["name"] == name for r in self.runs.values())

    def next_available_run_name(self, name):
        if not self.run_name_exists(name):
            return name
        n = 2
        while any(r["name"] == f"{name}-{n}" for r in self.runs.values()):
            n += 1
        return f"{name}-{n}"

    def create_run(self, **kwargs):
        run_id = self._next_run_id
        self._next_run_id += 1
        run_number = self._next_run_number
        self._next_run_number += 1
        row = {
            "id": run_id,
            "run_number": run_number,
            "wire_run_id": None,
            "acked_spec": None,
            "started_at": "now",
            "confirmed_at": None,
            "ended_at": None,
            "outcome": "pending",
            "outcome_detail": None,
            "latch_cause": None,
            "recorded": kwargs.get("daq_experiment_id") is not None,
            **kwargs,
        }
        self.runs[run_id] = row
        return dict(row)

    def get_run(self, run_id):
        return dict(self.runs[run_id]) if run_id in self.runs else None

    def get_active_run(self):
        live = [r for r in self.runs.values() if r["ended_at"] is None]
        return dict(live[-1]) if live else None

    def set_wire_run_id(self, run_id, wire_run_id):
        self.runs[run_id]["wire_run_id"] = wire_run_id

    def set_acked_spec(self, run_id, acked_spec):
        self.runs[run_id]["acked_spec"] = acked_spec

    def mark_ended(self, run_id, *, outcome, outcome_detail=None, latch_cause=None):
        self.runs[run_id].update(
            outcome=outcome, outcome_detail=outcome_detail, latch_cause=latch_cause, ended_at="now"
        )
        return dict(self.runs[run_id])

    def mark_confirmed(self, run_id):
        self.runs[run_id]["confirmed_at"] = "now"

    def delete_run(self, run_id):
        del self.runs[run_id]


@pytest.fixture
def fake_db(monkeypatch):
    fdb = FakeDb()
    monkeypatch.setattr(runs, "db", fdb)
    fdb.configs[1] = {"id": 1, "name": "standard", "spec": {"gasSetpointPct": 5}}
    return fdb


@pytest.fixture
def fake_daq(monkeypatch):
    calls = {"ping": True, "recording_started": False, "recording_stopped": 0}

    monkeypatch.setattr(runs.daq, "ping", lambda: calls["ping"])
    # Real DataAcquisition wraps this: {"experiment": {"id": ...}, "ok": true}.
    monkeypatch.setattr(runs.daq, "create_experiment", lambda name: {"experiment": {"id": 42}, "ok": True})
    monkeypatch.setattr(runs.daq, "set_active_experiment", lambda eid: {"ok": True})
    monkeypatch.setattr(runs.daq, "set_stage", lambda stage: {"ok": True})

    def start_recording(name=None):
        calls["recording_started"] = True
        return {"ok": True}

    def stop_recording():
        calls["recording_stopped"] += 1
        calls["recording_started"] = False
        return {"ok": True}

    monkeypatch.setattr(runs.daq, "start_recording", start_recording)
    monkeypatch.setattr(runs.daq, "stop_recording", stop_recording)
    return calls


@pytest.fixture
def fake_layout(monkeypatch):
    monkeypatch.setattr(runs.layout, "capture_for_run", lambda run_id, exp_id: None)


def _fake_commands(monkeypatch, ack_payload, confirmed=None):
    """Stubs commands.start/wait_for_ack. start() also sets the pending run,
    exactly as the real one does — confirm_run() checks against it."""

    def start(spec):
        state.set_pending_run("wire-run-1")
        return "wire-run-1"

    monkeypatch.setattr(runs.commands, "start", start)
    monkeypatch.setattr(runs.commands, "wait_for_ack", lambda run_id, timeout_s=None: ack_payload)

    def confirm(wire_run_id):
        if confirmed is not None:
            confirmed.append(wire_run_id)
        state.set_pending_run(None)

    monkeypatch.setattr(runs.commands, "confirm", confirm)
    monkeypatch.setattr(runs.commands, "cancel_pending", lambda: state.set_pending_run(None))


def test_start_run_rejected_in_display_mode(fake_db, fake_daq, fake_layout):
    state.set_display_mode(True)
    with pytest.raises(RunStartRejected, match="Display mode"):
        runs.start_run(config_id=1, run_name="test", experiment_name="Exp1")


def test_display_mode_lease_expires_without_heartbeat(monkeypatch):
    """A display screen that dies without releasing must not disable Start
    forever. The lease lapses and the next control screen can start a run."""
    state.set_display_mode(True)
    assert state.is_display_mode() is True

    # No heartbeat for longer than the lease.
    now = [state.time.time() + state.DISPLAY_MODE_LEASE_S + 1]
    monkeypatch.setattr(state.time, "time", lambda: now[0])

    assert state.is_display_mode() is False


def test_display_mode_lease_survives_heartbeat(monkeypatch):
    """Renewing inside the window keeps the lease held."""
    base = state.time.time()
    now = [base]
    monkeypatch.setattr(state.time, "time", lambda: now[0])

    state.set_display_mode(True)
    # Heartbeat at half the lease, repeatedly, past the original expiry.
    for _ in range(4):
        now[0] += state.DISPLAY_MODE_LEASE_S / 2
        assert state.is_display_mode() is True
        state.set_display_mode(True)

    assert now[0] > base + state.DISPLAY_MODE_LEASE_S
    assert state.is_display_mode() is True


def test_display_mode_release_is_immediate(monkeypatch):
    """An explicit release does not wait for the lease to run out."""
    state.set_display_mode(True)
    state.set_display_mode(False)
    assert state.is_display_mode() is False


def test_start_run_blocked_when_daq_unreachable(fake_db, fake_daq, fake_layout):
    fake_daq["ping"] = False
    with pytest.raises(RunStartRejected, match="unreachable"):
        runs.start_run(config_id=1, run_name="test", experiment_name="Exp1")


def test_start_run_unrecorded_test_run_skips_daq_ping(fake_db, fake_daq, fake_layout, monkeypatch):
    fake_daq["ping"] = False  # DAQ down
    _fake_commands(monkeypatch, {"runId": "wire-run-1", "valid": True, "spec": {"gasSetpointPct": 5}})
    result = runs.start_run(
        config_id=1, run_name="sensor check", unrecorded_test_run=True
    )
    assert result.rejected is False
    assert fake_daq["recording_started"] is False


def test_start_run_happy_path_records_ack(fake_db, fake_daq, fake_layout, monkeypatch):
    ack_payload = {"runId": "wire-run-1", "valid": True, "spec": {"gasSetpointPct": 4}}
    _fake_commands(monkeypatch, ack_payload)
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    assert result.rejected is False
    assert result.run["acked_spec"] == {"gasSetpointPct": 4}
    assert fake_daq["recording_started"] is True


def test_start_run_ack_rejection_marks_run_rejected(fake_db, fake_daq, fake_layout, monkeypatch):
    ack_payload = {"runId": "wire-run-1", "valid": False, "reason": "Selector in equipment-test"}
    _fake_commands(monkeypatch, ack_payload)
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    assert result.rejected is True
    assert result.run["outcome"] == "rejected"
    assert result.run["outcome_detail"] == "Selector in equipment-test"


def test_start_run_ack_timeout_marks_run_rejected(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, None)  # simulate wait_for_ack timing out
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    assert result.rejected is True
    assert result.ack is None
    assert "timeout" in result.run["outcome_detail"].lower()


def test_start_run_requires_config_or_override(fake_db, fake_daq, fake_layout):
    with pytest.raises(RunStartRejected, match="config_id or spec_override"):
        runs.start_run(config_id=None, run_name="run1", experiment_name="Exp1")


def test_start_run_missing_config_rejected(fake_db, fake_daq, fake_layout):
    with pytest.raises(RunStartRejected, match="not found"):
        runs.start_run(config_id=999, run_name="run1", experiment_name="Exp1")


# --- Duplicate run names (problems.txt: "block runs with the same name") ---


def test_start_run_rejects_blank_name(fake_db, fake_daq, fake_layout):
    with pytest.raises(RunStartRejected, match="run_name is required"):
        runs.start_run(config_id=1, run_name="   ", experiment_name="Exp1")


def test_start_run_rejects_duplicate_name_by_default(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    runs.end_run_completed(fake_db.get_active_run()["id"])
    with pytest.raises(RunStartRejected, match="already exists"):
        runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")


def test_start_run_suffixes_duplicate_name_on_request(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    runs.end_run_completed(fake_db.get_active_run()["id"])
    result = runs.start_run(
        config_id=1, run_name="run1", experiment_name="Exp1", on_name_conflict="suffix"
    )
    assert result.run["name"] == "run1-2"


def test_start_run_suffix_finds_the_lowest_free_number(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    for name in ("run1", "run1-2", "run1-3"):
        runs.start_run(config_id=1, run_name=name, experiment_name="Exp1")
        runs.end_run_completed(fake_db.get_active_run()["id"])
    result = runs.start_run(
        config_id=1, run_name="run1", experiment_name="Exp1", on_name_conflict="suffix"
    )
    assert result.run["name"] == "run1-4"


# --- Inlet interlock (problems.txt: "block start if inlet is on but no
#     other ventilation system is open") --------------------------------


def test_start_run_rejects_inlet_only_ventilation(fake_db, fake_daq, fake_layout):
    fake_db.configs[2] = {
        "id": 2, "name": "inlet-only",
        "spec": {"gasSetpointPct": 5, "ventRegisters": {"central": False, "exhaust": False, "inlet": True}},
    }
    with pytest.raises(RunStartRejected, match="Inlet is open"):
        runs.start_run(config_id=2, run_name="run1", experiment_name="Exp1")


def test_start_run_allows_inlet_with_exhaust_open(fake_db, fake_daq, fake_layout, monkeypatch):
    fake_db.configs[2] = {
        "id": 2, "name": "inlet-and-exhaust",
        "spec": {"gasSetpointPct": 5, "ventRegisters": {"central": False, "exhaust": True, "inlet": True}},
    }
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=2, run_name="run1", experiment_name="Exp1")
    assert result.rejected is False


def test_start_run_allows_no_registers_open(fake_db, fake_daq, fake_layout, monkeypatch):
    """The interlock only fires on inlet-with-nothing-else; no registers open
    at all (e.g. before the vent phase) is not the condition it guards."""
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    assert result.rejected is False


# --- Recording must not be left running when the run never happens ---------


def _ack(valid=True, spec=None):
    return {"runId": "wire-run-1", "valid": valid, "spec": spec or {"gasSetpointPct": 5}}


def test_recording_stopped_when_firmware_rejects(fake_db, fake_daq, fake_layout, monkeypatch):
    """DAQ was told to record for a run that is not going to happen; nothing
    else in the system would ever tell it to stop."""
    _fake_commands(monkeypatch, {"runId": "wire-run-1", "valid": False, "reason": "nope"})
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    assert result.rejected is True
    assert fake_daq["recording_stopped"] == 1


def test_recording_stopped_on_ack_timeout(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, None)
    runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    assert fake_daq["recording_stopped"] == 1


def test_recording_stopped_when_command_fails(fake_db, fake_daq, fake_layout, monkeypatch):
    def boom(spec):
        raise CommandError("MQTT not connected")

    monkeypatch.setattr(runs.commands, "start", boom)
    with pytest.raises(RunStartRejected):
        runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    assert fake_daq["recording_stopped"] == 1


def test_recording_stopped_when_run_ends(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    assert fake_daq["recording_stopped"] == 0  # still running

    runs.end_run_completed(result.run["id"])
    assert fake_daq["recording_stopped"] == 1


def test_unrecorded_test_run_never_stops_recording(fake_db, fake_daq, fake_layout, monkeypatch):
    """A test run never started recording, so ending it must not stop a
    recording session that belongs to someone else."""
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="check", unrecorded_test_run=True)
    runs.end_run_completed(result.run["id"])
    assert fake_daq["recording_stopped"] == 0


# --- Unrecorded test runs leave no row once they end -----------------------
#
# unrecorded_test_run exists for sensor checks that must not pollute the
# permanent record (module docstring). A row that survives past the end of
# the run — even with recorded=0 — is exactly the pollution the flag is
# supposed to prevent: it would still show up in /api/runs history. The row
# is kept only WHILE the run is in flight (phase_watcher, confirm/cancel,
# and the one-run-at-a-time guard all key off it by id), and removed the
# moment it ends, whichever way it ends.


def test_unrecorded_run_is_gone_after_completing(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="check", unrecorded_test_run=True)
    run_id = result.run["id"]
    assert fake_db.get_run(run_id) is not None  # still there while in flight

    runs.end_run_completed(run_id)
    assert fake_db.get_run(run_id) is None


def test_unrecorded_run_is_gone_after_expiring(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="check", unrecorded_test_run=True)
    run_id = result.run["id"]

    runs.end_run_expired(run_id)
    assert fake_db.get_run(run_id) is None


def test_unrecorded_run_is_gone_after_latching(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="check", unrecorded_test_run=True)
    run_id = result.run["id"]

    runs.end_run_from_latch(run_id, "EXTERNAL_TRIP")
    assert fake_db.get_run(run_id) is None


def test_unrecorded_run_is_gone_after_being_cancelled(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="check", unrecorded_test_run=True)
    run_id = result.run["id"]

    runs.cancel_run(run_id)
    assert fake_db.get_run(run_id) is None


def test_end_run_still_returns_the_final_state_for_an_unrecorded_run(
    fake_db, fake_daq, fake_layout, monkeypatch
):
    """The caller (phase_watcher, the /confirm and /cancel routes) still needs
    the outcome/ended_at to respond with, even though nothing persists."""
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="check", unrecorded_test_run=True)
    ended = runs.end_run_completed(result.run["id"])
    assert ended["outcome"] == "completed"
    assert ended["ended_at"] is not None


def test_cancel_run_still_returns_the_final_state_for_an_unrecorded_run(
    fake_db, fake_daq, fake_layout, monkeypatch
):
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="check", unrecorded_test_run=True)
    cancelled = runs.cancel_run(result.run["id"])
    assert cancelled["outcome"] == "aborted"
    assert cancelled["ended_at"] is not None


def test_recorded_run_still_exists_after_completing(fake_db, fake_daq, fake_layout, monkeypatch):
    """Only unrecorded runs are purged — a real run's row is the permanent
    record and must survive exactly as before this change."""
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    run_id = result.run["id"]

    runs.end_run_completed(run_id)
    assert fake_db.get_run(run_id) is not None


def test_second_unrecorded_run_can_reuse_the_name_after_the_first_ends(
    fake_db, fake_daq, fake_layout, monkeypatch
):
    """A deleted row must not leave run_name_exists()/next_available_run_name()
    thinking the name is still taken."""
    _fake_commands(monkeypatch, _ack())
    runs.start_run(config_id=1, run_name="check", unrecorded_test_run=True)
    runs.end_run_completed(fake_db.get_active_run()["id"])

    result = runs.start_run(config_id=1, run_name="check", unrecorded_test_run=True)
    assert result.run["name"] == "check"


# --- Arm timeout (armed, never confirmed) is distinct from completed -------
#
# phase_watcher.py used to call end_run_completed() for ANY run reaching
# WAITING with outcome still 'pending' — including a run that only ever
# reached ARMED and was never confirmed (the firmware reverts ARMED ->
# WAITING on its own after ARM_TIMEOUT_MS with no operator action at all).
# That mislabeled a run that never actually fired gas as "completed" in the
# permanent record. end_run_expired() is the correct outcome for that case;
# end_run_completed() must stay reserved for a run that was actually
# confirmed and ran its full sequence.


def test_end_run_expired_marks_outcome_expired(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    runs.end_run_expired(result.run["id"])
    ended = fake_db.get_run(result.run["id"])
    assert ended["outcome"] == "expired"
    assert ended["ended_at"] is not None


def test_end_run_expired_stops_recording(fake_db, fake_daq, fake_layout, monkeypatch):
    """Same invariant as a completed/latched/stopped run: DAQ must not be
    left recording forever just because nobody confirmed in time."""
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    assert fake_daq["recording_stopped"] == 0
    runs.end_run_expired(result.run["id"])
    assert fake_daq["recording_stopped"] == 1


# --- One run at a time -----------------------------------------------------


def test_second_start_refused_while_a_run_is_active(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    with pytest.raises(RunStartRejected, match="still active"):
        runs.start_run(config_id=1, run_name="run2", experiment_name="Exp1")


# --- confirm() is verified server-side, never trusted from the client ------


def test_confirm_sends_the_recorded_wire_run_id(fake_db, fake_daq, fake_layout, monkeypatch):
    confirmed = []
    _fake_commands(monkeypatch, _ack(), confirmed=confirmed)
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    runs.confirm_run(result.run["id"], "wire-run-1")
    assert confirmed == ["wire-run-1"]


def test_confirm_refuses_a_mismatched_wire_run_id(fake_db, fake_daq, fake_layout, monkeypatch):
    """The whole point of two-phase start is that the confirmed spec is the
    reviewed spec — a stale tab must not be able to confirm something else."""
    confirmed = []
    _fake_commands(monkeypatch, _ack(), confirmed=confirmed)
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    with pytest.raises(RunConfirmRejected, match="does not match"):
        runs.confirm_run(result.run["id"], "some-other-run")
    assert confirmed == []


def test_confirm_refuses_when_arm_window_has_expired(fake_db, fake_daq, fake_layout, monkeypatch):
    confirmed = []
    _fake_commands(monkeypatch, _ack(), confirmed=confirmed)
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    state.set_pending_run(None)  # firmware disarmed after ARM_TIMEOUT_MS
    with pytest.raises(RunConfirmRejected, match="expired"):
        runs.confirm_run(result.run["id"], "wire-run-1")
    assert confirmed == []


def test_confirm_refuses_twice(fake_db, fake_daq, fake_layout, monkeypatch):
    confirmed = []
    _fake_commands(monkeypatch, _ack(), confirmed=confirmed)
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    runs.confirm_run(result.run["id"], "wire-run-1")
    with pytest.raises(RunConfirmRejected, match="already confirmed"):
        runs.confirm_run(result.run["id"], "wire-run-1")
    assert confirmed == ["wire-run-1"]


def test_confirm_refuses_a_rejected_run(fake_db, fake_daq, fake_layout, monkeypatch):
    confirmed = []
    _fake_commands(monkeypatch, {"runId": "wire-run-1", "valid": False, "reason": "nope"},
                   confirmed=confirmed)
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    with pytest.raises(RunConfirmRejected, match="already ended"):
        runs.confirm_run(result.run["id"], "wire-run-1")
    assert confirmed == []


def test_confirm_refuses_a_run_with_no_ack(fake_db, fake_daq, fake_layout, monkeypatch):
    _fake_commands(monkeypatch, _ack())
    result = runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    fake_db.runs[result.run["id"]]["acked_spec"] = None
    with pytest.raises(RunConfirmRejected, match="not been ack'd"):
        runs.confirm_run(result.run["id"], "wire-run-1")


# --- DAQ errors are typed, never leaked as raw HTTP errors -----------------


def test_daq_4xx_on_experiment_creation_is_a_clean_rejection(
    fake_db, fake_daq, fake_layout, monkeypatch
):
    def rejected(name):
        raise DaqRejected("POST /experiments: name already taken", 409)

    monkeypatch.setattr(runs.daq, "create_experiment", rejected)
    with pytest.raises(RunStartRejected, match="Could not prepare DAQ recording"):
        runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")
    # Recording never started, so there is nothing to unwind.
    assert fake_daq["recording_stopped"] == 0


def test_experiment_created_without_an_id_is_rejected(
    fake_db, fake_daq, fake_layout, monkeypatch
):
    """An id that quietly went missing would produce a run the operator
    believes is recording but which is stored as recorded=0."""
    monkeypatch.setattr(runs.daq, "create_experiment", lambda name: {"ok": True})
    with pytest.raises(RunStartRejected, match="no id"):
        runs.start_run(config_id=1, run_name="run1", experiment_name="Exp1")


def test_recorded_run_needs_an_experiment(fake_db, fake_daq, fake_layout):
    """This rejection is raised inside the block that catches DaqError; it
    must surface as itself, not get rewrapped as a DAQ preparation failure."""
    with pytest.raises(RunStartRejected, match="experiment_id or experiment_name"):
        runs.start_run(config_id=1, run_name="run1")
    assert fake_daq["recording_started"] is False
