"""
routes.runs — the run lifecycle: compose/start, review/confirm, stop, ack.

Stop and ack go straight to commands.py -> MQTT and must never be gated on
DAQ, display mode readiness, or the database — they are the one thing that
must keep working when everything else is on fire (design spec,
'Availability' / 'Display').
"""

from flask import Blueprint, jsonify, request

import app.commands as commands
import app.db as db
import app.runs as runs
from app.auth import require_auth
from app.commands import CommandError
from app.runs import RunConfirmRejected, RunStartRejected

bp = Blueprint("runs", __name__)

_MAX_LIMIT = 500


@bp.get("/api/runs")
@require_auth
def list_runs():
    # type=int returns None rather than raising on garbage, so a typo'd
    # ?limit=abc is a 400 instead of a 500 from an uncaught ValueError.
    limit = request.args.get("limit", default=100, type=int)
    offset = request.args.get("offset", default=0, type=int)
    if limit is None or offset is None or limit < 1 or offset < 0:
        return jsonify({"error": "limit must be >= 1 and offset >= 0"}), 400
    return jsonify(db.list_runs(limit=min(limit, _MAX_LIMIT), offset=offset))


@bp.get("/api/runs/current")
@require_auth
def get_current_run():
    """The run in flight, or null. Asks for the ACTIVE run rather than
    filtering the latest one: a rejected start creates an already-ended run
    row, which would otherwise hide a live run behind it."""
    return jsonify(db.get_active_run())


@bp.get("/api/runs/<int:run_id>")
@require_auth
def get_run(run_id: int):
    run = db.get_run(run_id)
    if run is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(run)


@bp.post("/api/runs/start")
@require_auth
def start_run():
    """
    Step 1 (Compose) and the send-half of step 2 (Review) in one call: runs
    the config, sends start(spec), and returns once the ack has arrived. The
    frontend renders the ack diff from this response; confirm() is a
    separate, explicit call so a human sees the diff before gas flows.
    """
    body = request.get_json(force=True, silent=True) or {}
    on_name_conflict = body.get("on_name_conflict", "reject")
    if on_name_conflict not in ("reject", "suffix"):
        return jsonify({"error": "on_name_conflict must be 'reject' or 'suffix'"}), 400
    try:
        result = runs.start_run(
            config_id=body.get("config_id"),
            spec_override=body.get("spec"),
            experiment_id=body.get("experiment_id"),
            experiment_name=body.get("experiment_name"),
            run_name=body.get("run_name", ""),
            unrecorded_test_run=bool(body.get("unrecorded_test_run", False)),
            operator=body.get("operator"),
            on_name_conflict=on_name_conflict,
        )
    except RunStartRejected as exc:
        return jsonify({"error": str(exc), "rejected": True}), 409

    return jsonify({
        "run": result.run,
        "ack": result.ack,
        "rejected": result.rejected,
    })


@bp.post("/api/runs/<int:run_id>/confirm")
@require_auth
def confirm_run(run_id: int):
    """
    The step that lets gas flow. wire_run_id is checked server-side against
    the runId this run was actually started with — a mismatch is refused, so a
    stale tab or a second operator cannot confirm a spec nobody reviewed.
    """
    body = request.get_json(force=True, silent=True) or {}
    wire_run_id = body.get("wire_run_id")
    if not wire_run_id or not isinstance(wire_run_id, str):
        return jsonify({"error": "wire_run_id is required"}), 400
    try:
        run = runs.confirm_run(run_id, wire_run_id)
    except RunConfirmRejected as exc:
        return jsonify({"error": str(exc), "rejected": True}), 409
    except CommandError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify(run)


@bp.post("/api/runs/<int:run_id>/cancel")
@require_auth
def cancel_run(run_id: int):
    if db.get_run(run_id) is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(runs.cancel_run(run_id))


@bp.post("/api/runs/<int:run_id>/stage")
@require_auth
def override_stage(run_id: int):
    """Manual stage override/label mid-phase, per the design spec's
    'Both automatic and manual' stage-mapping rule."""
    body = request.get_json(force=True, silent=True) or {}
    label = body.get("label")
    if not label or not isinstance(label, str):
        return jsonify({"error": "label is required"}), 400
    if db.get_run(run_id) is None:
        return jsonify({"error": "not found"}), 404
    if not runs.set_stage_label(run_id, label):
        # Saying so beats accepting a label that went nowhere.
        return jsonify({"error": "Run is not recording — no stage to label"}), 409
    return jsonify({"ok": True})


@bp.post("/api/control/stop")
@require_auth
def stop():
    """Never gated. Commands the present regardless of display mode, replay
    scrub position, or DAQ health."""
    try:
        commands.stop()
    except CommandError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"ok": True})


@bp.post("/api/control/ack")
@require_auth
def ack():
    """Never gated by display mode — a screen in the room is exactly where
    someone next to the hazard would reach for this."""
    try:
        commands.ack()
    except CommandError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"ok": True})
