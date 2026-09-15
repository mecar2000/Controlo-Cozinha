"""
app.runs — orchestrates DAQ recording + firmware confirm behind ONE entry
point: start_run(config_id, experiment_id, run_name). Deliberately a single
function so a future batch sequencer (deferred per the design spec) is just
a loop around this same call, never a second code path.

Run start is coupled to recording by default, decoupled deliberately: the
unrecorded_test_run flag exists for sensor checks that shouldn't pollute the
experiment record, and is off by default so nobody has to invent an
experiment name just to check a sensor is alive. Not polluting the record
means the run's own row in Cozinha's local DB is deleted the moment it ends
(see _purge_if_unrecorded) — it exists only transiently while the run is in
flight, since phase_watcher/confirm/cancel/the one-run-at-a-time guard all
need to find it by id until then.
"""

from typing import Optional

import app.commands as commands
import app.daq as daq
import app.db as db
import app.layout as layout
import app.state as state
import app.zeroing as zeroing
from app.commands import CommandError
from app.daq import DaqError

# Firmware phase -> DataAcquisition stage label, per the design spec's
# "Stage mapping": run{N}-leak, run{N}-hold, run{N}-vent, run{N}-purge.
# Keyed by the firmware's own phase names as published on the state topic.
_PHASE_TO_STAGE_SUFFIX = {
    "LEAKING": "leak",
    "HOLD": "hold",
    "VENTILATING": "vent",
    "FULLY_VENTILATING": "purge",
}


class RunStartRejected(Exception):
    """Raised when start is refused before any command reaches the firmware
    (DAQ unreachable, display mode, no config). Distinct from a firmware-side
    ack rejection, which is a normal outcome the caller must still show."""


class RunConfirmRejected(Exception):
    """Raised when a confirm() cannot be honoured — the run is not awaiting
    confirmation, or the runId offered is not the one this run was started
    with. Never reaches the firmware."""


class RunResult:
    def __init__(self, run: dict, ack: Optional[dict], rejected: bool):
        self.run = run
        self.ack = ack
        self.rejected = rejected


def _stop_recording_quietly(context: str) -> None:
    """
    Stop DAQ recording on a path where the run is not going to happen.

    Best-effort by design: this runs while unwinding a failure, and a DAQ that
    is already unreachable is exactly why we might be here. Leaving the
    historian recording forever is the worse outcome, so we always try, but a
    failure here must never mask the original error.
    """
    try:
        daq.stop_recording()
    except Exception as exc:
        print(f"[runs] Could not stop DAQ recording after {context}: {exc}")


def _inlet_only(spec: dict) -> bool:
    """True when the requested vent registers open the inlet but no
    exhaust/central path for it to draw through — the interlock the webapp
    is responsible for (firmware treats any register combination as legal;
    see kitchen/RunSpec.h). Checked against every stop-condition phase's
    register set that a spec can carry (today just ventRegisters, the
    VENTILATING phase's — see ConfigEditor's RegisterCheckboxes)."""
    vr = spec.get("ventRegisters") or {}
    return bool(vr.get("inlet")) and not (vr.get("central") or vr.get("exhaust"))


def start_run(
    *,
    config_id: Optional[int],
    spec_override: Optional[dict] = None,
    experiment_id: Optional[int] = None,
    experiment_name: Optional[str] = None,
    run_name: str,
    unrecorded_test_run: bool = False,
    operator: Optional[str] = None,
    on_name_conflict: str = "reject",
) -> RunResult:
    """
    The single entry point for starting a run. Composes a config (or an
    override spec), gates on DAQ availability and display mode, starts
    recording (unless unrecorded_test_run), sends start(spec), and returns
    once an ack has arrived (or timed out) — NOT once the run is confirmed.
    Confirming is a separate, explicit operator action (confirm_run) so the
    ack-review diff is seen before gas flows.

    `on_name_conflict`: "reject" (default) refuses a name already used by an
    earlier run; "suffix" instead appends the lowest free "-2", "-3", ... —
    both per problems.txt: "block runs with the same name as previous ones,
    or ask if the user wants to simply add a number".
    """
    if state.is_display_mode():
        raise RunStartRejected("Display mode: starting a run is disabled on this screen")

    if zeroing.is_active():
        raise RunStartRejected(
            "A sensor zero-in-clean-air capture is in progress — the air must "
            "stay undisturbed until it finishes or is cancelled"
        )

    run_name = (run_name or "").strip()
    if not run_name:
        raise RunStartRejected("run_name is required")
    if db.run_name_exists(run_name):
        if on_name_conflict == "suffix":
            run_name = db.next_available_run_name(run_name)
        else:
            raise RunStartRejected(
                f"A run named {run_name!r} already exists — choose another name "
                "or start again with on_name_conflict=suffix"
            )

    # One run at a time. Without this, a second start would create a run row
    # that the phase watcher then has to disambiguate from the live one, and
    # would send start(spec) to firmware that is already running.
    active = db.get_active_run()
    if active is not None:
        raise RunStartRejected(
            f"Run {active['run_number']} is still active — stop it before starting another"
        )

    if config_id is not None:
        config = db.get_config(config_id)
        if config is None:
            raise RunStartRejected(f"Config {config_id} not found")
        spec = config["spec"]
        config_snapshot = config
    elif spec_override is not None:
        spec = spec_override
        config_snapshot = {"ad_hoc": True, "spec": spec_override}
    else:
        raise RunStartRejected("Either config_id or spec_override is required")

    # Inlet-only ventilation is refused here rather than left to the
    # firmware: KitchenCore sequences the inlet-open delay (RunSpec.h) but
    # treats any register combination as legal, so an inlet with nothing
    # open to draw air through would run without anyone having said so on
    # purpose (problems.txt: "Block start if inlet is on but no other
    # ventilation system is open").
    if _inlet_only(spec):
        raise RunStartRejected(
            "Inlet is open with no central or exhaust register — "
            "open at least one of them too, or turn the inlet off"
        )

    recording_started = False
    if not unrecorded_test_run:
        # DAQ absence blocks starting (design spec, "Availability") — but
        # only starting; a run already in flight must never depend on DAQ.
        if not daq.ping():
            raise RunStartRejected("DataAcquisition is unreachable — start blocked")

        daq_experiment_id = experiment_id
        try:
            if daq_experiment_id is None:
                if not experiment_name:
                    raise RunStartRejected(
                        "experiment_id or experiment_name is required to record"
                    )
                daq_experiment_id = daq.experiment_id_of(
                    daq.create_experiment(experiment_name)
                )
            daq.set_active_experiment(daq_experiment_id)
        except DaqError as exc:
            # Nothing is recording yet, so there is nothing to unwind — but a
            # 4xx here would otherwise escape as an unhandled HTTP error.
            raise RunStartRejected(f"Could not prepare DAQ recording: {exc}") from exc

        try:
            daq.start_recording(experiment_name)
        except DaqError as exc:
            raise RunStartRejected(f"Could not start DAQ recording: {exc}") from exc
        recording_started = True
        daq_experiment_name = experiment_name
    else:
        daq_experiment_id = None
        daq_experiment_name = None

    # From here on every failure path must stop recording again: DAQ was told
    # to record for a run that is not going to happen, and nothing else in the
    # system will ever tell it to stop.
    try:
        run = db.create_run(
            name=run_name,
            config_id=config_id,
            config_snapshot=config_snapshot,
            daq_experiment_id=daq_experiment_id,
            daq_experiment_name=daq_experiment_name,
            requested_spec=spec,
            operator=operator,
        )
    except Exception:
        if recording_started:
            _stop_recording_quietly("failing to create the run row")
        raise

    if daq_experiment_id is not None:
        layout.capture_for_run(run["id"], daq_experiment_id)

    try:
        wire_run_id = commands.start(spec)
    except CommandError as exc:
        if recording_started:
            _stop_recording_quietly("a command failure")
        db.mark_ended(run["id"], outcome="rejected", outcome_detail=str(exc))
        raise RunStartRejected(str(exc)) from exc

    # Persist the wire id before waiting: if this process dies mid-wait, the
    # run row still says which firmware runId it belongs to.
    db.set_wire_run_id(run["id"], wire_run_id)

    ack = commands.wait_for_ack(wire_run_id)
    if ack is None:
        if recording_started:
            _stop_recording_quietly("an ack timeout")
        db.mark_ended(run["id"], outcome="rejected", outcome_detail="No ack received (timeout)")
        return RunResult(db.get_run(run["id"]), None, rejected=True)

    db.set_acked_spec(run["id"], ack.get("spec", {}))

    if not ack.get("valid", ack.get("accepted", True)):
        reason = ack.get("reason") or ack.get("rejectReason") or "Rejected by firmware"
        if recording_started:
            _stop_recording_quietly("a firmware rejection")
        db.mark_ended(run["id"], outcome="rejected", outcome_detail=reason)
        return RunResult(db.get_run(run["id"]), ack, rejected=True)

    return RunResult(db.get_run(run["id"]), ack, rejected=False)


def confirm_run(run_id: int, wire_run_id: str) -> dict:
    """
    Sends confirm(runId) for a run already ack'd and reviewed. Separate from
    start_run so the operator sees the ack-review diff before this is ever
    called (design spec, 'Starting a run').

    The runId offered by the caller is checked against the one this run was
    actually started with, and against the pending run this process is holding.
    Confirming is the step that lets gas flow, so it must not be possible to
    confirm a spec nobody reviewed: a stale browser tab replaying an old
    confirm, or two operators confirming at once, would otherwise send
    confirm() for the wrong run.
    """
    run = db.get_run(run_id)
    if run is None:
        raise RunConfirmRejected(f"Run {run_id} not found")
    if run["ended_at"] is not None:
        raise RunConfirmRejected(
            f"Run {run_id} has already ended ({run['outcome']}) — nothing to confirm"
        )
    if run["confirmed_at"] is not None:
        raise RunConfirmRejected(f"Run {run_id} is already confirmed")
    if run["acked_spec"] is None:
        raise RunConfirmRejected(f"Run {run_id} has not been ack'd by the firmware yet")

    expected = run["wire_run_id"]
    if not expected:
        raise RunConfirmRejected(f"Run {run_id} has no recorded runId to confirm")
    if wire_run_id != expected:
        raise RunConfirmRejected(
            "wire_run_id does not match the run being confirmed — refusing to confirm"
        )

    # The firmware disarms after ARM_TIMEOUT_MS regardless; if this process no
    # longer holds the run as pending, the arm window is gone or belongs to a
    # different start, and confirming would be a command nobody reviewed.
    pending = state.get_pending_run()
    if pending != expected:
        raise RunConfirmRejected(
            "No matching armed run is pending — the arm window has expired; start again"
        )

    commands.confirm(expected)
    db.mark_confirmed(run_id)
    return db.get_run(run_id)


def cancel_run(run_id: int) -> dict:
    """Operator backed out after the ack but before confirming."""
    commands.cancel_pending()
    run = db.get_run(run_id)
    if run is not None and run.get("recorded"):
        _stop_recording_quietly("a cancelled run")
    ended = db.mark_ended(run_id, outcome="aborted", outcome_detail="Cancelled before confirm")
    return _purge_if_unrecorded(run, ended)


def _set_stage(run_id: int, stage: str) -> None:
    """Push a stage label to DataAcquisition. Never raises: DAQ dying mid-run
    must not interrupt the run — recording is marked lost by the phase
    watcher's own reachability check, not here."""
    try:
        daq.set_stage(stage)
    except DaqError as exc:
        print(f"[runs] Could not set stage {stage!r} for run {run_id}: {exc}")


def on_phase_transition(run_id: int, phase: str) -> None:
    """
    Called by the phase-watcher (see app.phase_watcher) on each firmware
    state-topic transition. Maps to a DataAcquisition stage label named
    run{N}-{phase}, per the design spec's 'Stage mapping'.
    """
    run = db.get_run(run_id)
    if run is None or not run.get("daq_experiment_id"):
        return
    suffix = _PHASE_TO_STAGE_SUFFIX.get(phase)
    if suffix is None:
        return
    _set_stage(run_id, f"run{run['run_number']}-{suffix}")


def set_stage_label(run_id: int, label: str) -> bool:
    """
    The operator's manual stage override/label, mid-phase — the 'manual' half
    of the design spec's 'Both automatic and manual' rule. Separate from
    on_phase_transition because no phase transition has happened; routing it
    through there meant passing an empty phase and relying on the override
    short-circuit to skip the lookup.

    Returns False when the run isn't recording, so the caller can say so
    rather than silently accepting a label that went nowhere.
    """
    run = db.get_run(run_id)
    if run is None or not run.get("daq_experiment_id"):
        return False
    _set_stage(run_id, label)
    return True


def _purge_if_unrecorded(run: Optional[dict], ended: Optional[dict]) -> Optional[dict]:
    """
    Delete the run row once an unrecorded_test_run ends, whichever way it
    ends — the flag exists so a sensor check never pollutes the permanent
    record (module docstring), and a row that survives past the end of the
    run is exactly that pollution (it would still show up in /api/runs
    history). The row is kept only WHILE the run is in flight: phase_watcher,
    confirm/cancel, and the one-run-at-a-time guard all key off it by id, so
    it cannot be skipped at create_run time (see the design discussion this
    followed) — it can only be removed once nothing else needs to find it.

    `ended` (mark_ended's own return value) is what callers get back instead
    of a row that, post-delete, db.get_run() would no longer find.
    """
    if run is not None and not run.get("recorded") and ended is not None:
        db.delete_run(run["id"])
    return ended


def _end_run(run_id: int, **mark_kwargs) -> dict:
    """
    Close out a run and stop DAQ recording.

    Recording is started by start_run and must be stopped exactly here —
    otherwise the historian keeps recording after every run, and the next run
    inherits an already-running recording session.
    """
    run = db.get_run(run_id)
    if run is not None and run.get("recorded") and run.get("ended_at") is None:
        _stop_recording_quietly(f"run {run_id} ending")
    ended = db.mark_ended(run_id, **mark_kwargs)
    return _purge_if_unrecorded(run, ended)


def end_run_from_latch(run_id: int, latch_cause: str, detail: Optional[str] = None) -> dict:
    return _end_run(run_id, outcome="latched", outcome_detail=detail, latch_cause=latch_cause)


def end_run_completed(run_id: int) -> dict:
    return _end_run(run_id, outcome="completed")


def end_run_expired(run_id: int) -> dict:
    """The firmware reverted ARMED -> WAITING on its own (ARM_TIMEOUT_MS)
    with nobody ever confirming — no gas flowed. Distinct from 'completed'
    (confirmed and ran its full sequence) and from 'aborted' (an operator
    explicitly cancelled the pending run before the timeout)."""
    return _end_run(
        run_id, outcome="expired", outcome_detail="Armed but never confirmed — timed out"
    )


def end_run_stopped(run_id: int, detail: Optional[str] = None) -> dict:
    return _end_run(run_id, outcome="stopped", outcome_detail=detail)
