"""
db.runs — the runs table: what was commanded and what happened.

Never stores measurements (those live in DataAcquisition). Stores
requested_spec_json AND acked_spec_json separately (the diff between them is
the forensic record of what the firmware clamped), plus config_snapshot_json
alongside config_id so editing a config later can't retroactively change what
a past run did.
"""

import json
from datetime import datetime, timezone
from typing import Optional

import pyodbc

from ._core import SERIALIZABLE, cursor, transaction

# Mirrors the firmware's DangerReason enum (see docs/implementation-plan.md,
# "Danger conditions") rather than inventing a separate vocabulary.
LATCH_CAUSES = {
    "LOCAL_SENSOR_THRESHOLD",
    "LOCAL_SENSOR_STALE",
    "FLOW_OVER_LIMIT",
    "INVENTORY_CAP_EXCEEDED",
    "PEER_ALARM",
    "PERMIT_DENIED",
    "ESTOP",
    "EXTERNAL_TRIP",
    "OPERATOR_ABORT",
}

OUTCOMES = {"pending", "completed", "stopped", "latched", "rejected", "aborted"}


def _now():
    return datetime.now(timezone.utc)


def _row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "run_number": row.run_number,
        "wire_run_id": row.wire_run_id,
        "name": row.name,
        "config_id": row.config_id,
        "config_snapshot": json.loads(row.config_snapshot_json),
        "daq_experiment_id": row.daq_experiment_id,
        "daq_experiment_name": row.daq_experiment_name,
        "requested_spec": json.loads(row.requested_spec_json),
        "acked_spec": json.loads(row.acked_spec_json) if row.acked_spec_json else None,
        "started_at": row.started_at.isoformat(),
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "outcome": row.outcome,
        "outcome_detail": row.outcome_detail,
        "latch_cause": row.latch_cause,
        "recorded": bool(row.recorded),
        "operator": row.operator,
    }


def next_run_number() -> int:
    """The next run_number, one past the highest used so far.

    Advisory only — for previewing a number in the UI. Do NOT read this and
    then insert with it: create_run() allocates the number inside its own
    INSERT so two concurrent starts cannot claim the same one.
    """
    with cursor() as cur:
        cur.execute("SELECT MAX(run_number) FROM runs")
        row = cur.fetchone()
        return (row[0] or 0) + 1


# Retried when two concurrent starts collide on the run_number unique index.
# The window is a single statement wide, so a couple of attempts is plenty.
_RUN_NUMBER_RETRIES = 5


def create_run(
    *,
    name: str,
    config_id: Optional[int],
    config_snapshot: dict,
    daq_experiment_id: Optional[int],
    daq_experiment_name: Optional[str],
    requested_spec: dict,
    operator: Optional[str],
    wire_run_id: Optional[str] = None,
) -> dict:
    """
    Insert a run row at start(spec) time, before the ack is known. Called once
    per run by runs.py (the orchestration module, not this data module).

    run_number is allocated inside the INSERT itself rather than by a separate
    SELECT MAX(...) — a read-then-write would let two concurrent starts claim
    the same number, and run_number names DataAcquisition stages, so a
    collision would silently merge two runs' data in the historian. The UNIQUE
    index on run_number is what makes that safe; a loser retries.
    """
    now = _now()
    for attempt in range(_RUN_NUMBER_RETRIES):
        try:
            # SERIALIZABLE so the MAX(run_number) read and the insert that
            # depends on it are one step. Without it two concurrent starts read
            # the same maximum; the UNIQUE index would still catch the
            # collision, but the range lock means the second start waits
            # instead of failing.
            with transaction(isolation=SERIALIZABLE) as cur:
                cur.execute(
                    """INSERT INTO runs (
                           run_number, wire_run_id, name, config_id, config_snapshot_json,
                           daq_experiment_id, daq_experiment_name,
                           requested_spec_json, acked_spec_json,
                           started_at, confirmed_at, ended_at,
                           outcome, outcome_detail, latch_cause, recorded, operator
                       )
                       OUTPUT INSERTED.id
                       VALUES (
                           (SELECT COALESCE(MAX(run_number), 0) + 1 FROM runs),
                           ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL,
                           'pending', NULL, NULL, ?, ?
                       )""",
                    wire_run_id, name, config_id, json.dumps(config_snapshot),
                    daq_experiment_id, daq_experiment_name,
                    json.dumps(requested_spec), now,
                    1 if daq_experiment_id is not None else 0, operator,
                )
                new_id = cur.fetchone()[0]
            return get_run(new_id)
        except pyodbc.IntegrityError:
            # Lost a race the range lock did not cover, or a deadlock victim
            # was retried. The next number is free; take it.
            if attempt == _RUN_NUMBER_RETRIES - 1:
                raise
    raise AssertionError("unreachable")


def set_wire_run_id(run_id: int, wire_run_id: str) -> None:
    """Record the runId sent on the cmd topic, so confirm() can be verified
    against the run it belongs to instead of trusting the client."""
    with cursor() as cur:
        cur.execute(
            "UPDATE runs SET wire_run_id = ? WHERE id = ?", wire_run_id, run_id
        )


def set_acked_spec(run_id: int, acked_spec: dict) -> None:
    """Record what the firmware actually agreed to run, from the ack."""
    with cursor() as cur:
        cur.execute(
            "UPDATE runs SET acked_spec_json = ? WHERE id = ?",
            json.dumps(acked_spec), run_id,
        )


def mark_confirmed(run_id: int) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE runs SET confirmed_at = ? WHERE id = ?", _now(), run_id
        )


def mark_ended(
    run_id: int,
    *,
    outcome: str,
    outcome_detail: Optional[str] = None,
    latch_cause: Optional[str] = None,
) -> Optional[dict]:
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome: {outcome!r}")
    if latch_cause is not None and latch_cause not in LATCH_CAUSES:
        raise ValueError(f"unknown latch_cause: {latch_cause!r}")
    with cursor() as cur:
        cur.execute(
            """UPDATE runs SET ended_at = ?, outcome = ?, outcome_detail = ?, latch_cause = ?
               WHERE id = ?""",
            _now(), outcome, outcome_detail, latch_cause, run_id,
        )
    return get_run(run_id)


def get_run(run_id: int) -> Optional[dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM runs WHERE id = ?", run_id)
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def get_latest_run() -> Optional[dict]:
    """The most recently started run, ended or not — used by the Control view
    to know which run is 'current' after a page reload."""
    with cursor() as cur:
        cur.execute("SELECT TOP 1 * FROM runs ORDER BY started_at DESC, id DESC")
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def get_active_run() -> Optional[dict]:
    """
    The newest run that has not ended.

    Distinct from get_latest_run(): a start that was rejected creates a run row
    that is immediately ended, so "latest" can be a dead run sitting in front
    of a live one. Anything driving a live run (stage mapping, outcome
    bookkeeping) must ask for the ACTIVE run, or it goes silent for the rest of
    the real run the moment a rejected start lands after it.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT TOP 1 * FROM runs WHERE ended_at IS NULL ORDER BY started_at DESC, id DESC"
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def list_runs(limit: int = 100, offset: int = 0) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """SELECT * FROM runs ORDER BY started_at DESC
               OFFSET ? ROWS FETCH NEXT ? ROWS ONLY""",
            offset, limit,
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
