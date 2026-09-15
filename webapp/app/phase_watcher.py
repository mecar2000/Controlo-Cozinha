"""
app.phase_watcher — watches the retained state topic and drives:

  1. Automatic stage mapping (POST /stage on each phase transition), per the
     design spec's "Stage mapping".
  2. Run outcome bookkeeping (completed / latched / stopped) as the firmware
     state machine moves, so runs.py doesn't need its own polling loop.
  3. DAQ-dies-mid-run detection: a run continues regardless, but recording is
     marked lost with a persistent banner (design spec, "Availability").

Runs on its own thread, polling app.state (which mqtt.py keeps current) at a
short interval — simpler than wiring a second MQTT subscriber, and the design
only needs phase transitions to be noticed within roughly a second.
"""

import threading
import time
from typing import Optional

import app.daq as daq
import app.db as db
import app.runs as runs
import app.state as state

_POLL_INTERVAL_S = 0.5
_DAQ_HEALTH_CHECK_INTERVAL_S = 5.0

# Written by the watcher thread, read by Flask request threads (is_recording_lost).
# Guarded rather than left as bare globals so this module keeps the same
# discipline as app.state — it is the only other cross-thread state in the app.
_lock = threading.Lock()

_last_phase: Optional[str] = None
_last_ack_required = False
_last_run_id: Optional[int] = None
_last_daq_ok = True
_recording_lost = False


def is_recording_lost() -> bool:
    with _lock:
        return _recording_lost


def _current_run_id() -> Optional[int]:
    """
    The run this watcher is driving.

    Uses get_active_run(), not get_latest_run(): a rejected start creates a run
    row that is already ended, and if one lands after a live run began, asking
    for the "latest" run would return the dead one and this watcher would go
    silent — no stage mapping, no outcome — for the rest of the real run.
    """
    run = db.get_active_run()
    return run["id"] if run else None


def _handle_phase_change(run_id: int, new_phase: str) -> None:
    runs.on_phase_transition(run_id, new_phase)

    if new_phase == "WAITING":
        # Reached WAITING from FULLY_VENTILATING with no ack pending == a
        # clean end-of-run purge completed. A latched run is closed out by
        # _handle_latch the moment the danger fires, not here, so this only
        # fires for runs that are still 'pending' at this point.
        #
        # But WAITING is also where an ARMED run lands on its own if nobody
        # ever confirms (the firmware's ARM_TIMEOUT_MS auto-revert) — that
        # path never passes through LEAKING, so confirmed_at is still NULL.
        # Both look identical here (outcome still 'pending', phase now
        # WAITING); confirmed_at is the only fact that tells them apart, so
        # it — not the phase transition alone — decides completed vs expired.
        run = db.get_run(run_id)
        if run and run["outcome"] == "pending":
            if run.get("confirmed_at") is not None:
                runs.end_run_completed(run_id)
            else:
                runs.end_run_expired(run_id)


def _handle_latch(run_id: int, reason: str, detail: Optional[str]) -> None:
    run = db.get_run(run_id)
    if run and run["outcome"] == "pending":
        runs.end_run_from_latch(run_id, reason, detail)


def _poll_once() -> None:
    global _last_phase, _last_ack_required, _last_run_id
    kstate = state.get_kitchen_state()
    phase = kstate.get("phase") or kstate.get("state")
    if not phase:
        return

    ack_required = bool(kstate.get("ackRequired"))
    run_id = _current_run_id()

    # A new run resets the transition baseline: otherwise a run that both
    # starts and ends on the same phase name as the last one saw (e.g. this
    # process restarted and read the retained topic) would never notice.
    if run_id != _last_run_id:
        _last_phase = None
        _last_ack_required = False
        _last_run_id = run_id

    phase_changed = phase != _last_phase
    # ackRequired is an independent flag on the same payload, so the firmware
    # can raise it while already sitting in FULLY_VENTILATING. Gating latch
    # detection on a phase CHANGE would miss exactly that case and leave the
    # run un-latched in the record.
    latch_raised = ack_required and not _last_ack_required

    if run_id is not None:
        if phase == "FULLY_VENTILATING" and ack_required and (phase_changed or latch_raised):
            reason = kstate.get("dangerReason") or kstate.get("latchCause") or "UNKNOWN"
            detail = kstate.get("reasonDetail") or kstate.get("description")
            _handle_latch(run_id, reason, detail)
        elif phase_changed:
            _handle_phase_change(run_id, phase)

    _last_phase = phase
    _last_ack_required = ack_required


def _check_daq_health() -> None:
    global _last_daq_ok, _recording_lost
    ok = daq.ping()
    state.set_daq_reachable(ok)
    run_id = _current_run_id()
    with _lock:
        if run_id is not None and not ok and _last_daq_ok:
            # DAQ died mid-run: the run continues, recording is marked lost.
            _recording_lost = True
            print(
                f"[phase_watcher] DataAcquisition unreachable during run {run_id}"
                f" — recording marked lost"
            )
        elif ok and _recording_lost and run_id is None:
            # Cleared once no run is active, so the banner survives the whole run.
            _recording_lost = False
    _last_daq_ok = ok


def _run_forever() -> None:
    last_health_check = 0.0
    while True:
        try:
            _poll_once()
            now = time.time()
            if now - last_health_check >= _DAQ_HEALTH_CHECK_INTERVAL_S:
                _check_daq_health()
                last_health_check = now
        except Exception as exc:
            print(f"[phase_watcher] error: {exc}")
        time.sleep(_POLL_INTERVAL_S)


def start() -> None:
    threading.Thread(target=_run_forever, daemon=True).start()
