"""
db.runs — the runs table: what was commanded and what happened.

Never stores measurements (those live in DataAcquisition). Stores
requested_spec_json AND acked_spec_json separately (the diff between them is
the forensic record of what the firmware clamped), plus config_snapshot_json
alongside config_id so editing a config later can't retroactively change what
a past run did.
"""

import json
import random
import time
from datetime import datetime, timezone
from typing import Optional

import MySQLdb

from ._core import SERIALIZABLE, cursor, transaction, to_db_datetime, from_db_datetime

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
    # External H2 sensors (base A6/A7): hydrogen outside the kitchen, at the
    # voltage-regulation stage, where there must never be any at all — a
    # distinct hazard from the six in-kitchen LOCAL_SENSOR_* causes above.
    "EXTERNAL_H2_THRESHOLD",
    "EXTERNAL_H2_SENSOR_FAULT",
}

OUTCOMES = {"pending", "completed", "stopped", "latched", "rejected", "aborted", "expired"}


def _now():
    return to_db_datetime(datetime.now(timezone.utc))


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "run_number": row["run_number"],
        "wire_run_id": row["wire_run_id"],
        "name": row["name"],
        "config_id": row["config_id"],
        "config_snapshot": json.loads(row["config_snapshot_json"]),
        "daq_experiment_id": row["daq_experiment_id"],
        "daq_experiment_name": row["daq_experiment_name"],
        "requested_spec": json.loads(row["requested_spec_json"]),
        "acked_spec": json.loads(row["acked_spec_json"]) if row["acked_spec_json"] else None,
        "started_at": from_db_datetime(row["started_at"]),
        "confirmed_at": from_db_datetime(row["confirmed_at"]),
        "ended_at": from_db_datetime(row["ended_at"]),
        "outcome": row["outcome"],
        "outcome_detail": row["outcome_detail"],
        "latch_cause": row["latch_cause"],
        "recorded": bool(row["recorded"]),
        "operator": row["operator"],
    }


def next_run_number() -> int:
    """The next run_number, one past the highest used so far.

    Advisory only — for previewing a number in the UI. Do NOT read this and
    then insert with it: create_run() allocates the number inside its own
    transaction so two concurrent starts cannot claim the same one.
    """
    with cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(run_number), 0) AS max_run FROM runs")
        row = cur.fetchone()
        return (row["max_run"] or 0) + 1


def run_name_exists(name: str) -> bool:
    """Whether any run (any outcome, ever) already used this exact name.

    Advisory, same caveat as next_run_number(): a second start racing in
    between this check and create_run() is not closed off by this alone
    (`name` has no UNIQUE index — unlike run_number, a duplicate name is a
    usability problem, not a correctness one, so a small race here is an
    acceptable trade for not adding a constraint that would also block a
    deliberate reused name via next_available_run_name()'s suffix path).
    """
    with cursor() as cur:
        cur.execute("SELECT 1 FROM runs WHERE name = %s LIMIT 1", (name,))
        return cur.fetchone() is not None


def next_available_run_name(name: str) -> str:
    """`name` if it's free, else `name` with the lowest -2, -3, ... suffix
    that is. Mirrors next_run_number()'s MAX+1 shape: a preview helper for
    the "suffix" branch of the duplicate-name guard in app.runs.start_run,
    not itself race-proof (see run_name_exists)."""
    if not run_name_exists(name):
        return name
    with cursor() as cur:
        cur.execute(
            "SELECT name FROM runs WHERE name = %s OR name LIKE %s",
            (name, f"{name}-%"),
        )
        taken = {row["name"] for row in cur.fetchall()}
    n = 2
    while f"{name}-{n}" in taken:
        n += 1
    return f"{name}-{n}"


# Retried when two concurrent starts collide over the run_number unique index.
#
# Measured live against MySQL 8: with ~10 starts racing at once, InnoDB's
# deadlock detector can pick the same transaction as the victim more than
# once in a row, so a small retry budget with no backoff empties out under
# real contention (observed 4/10 threads exhausting 5 immediate retries).
# 10 attempts with jittered backoff clears the same scenario reliably without
# meaningfully slowing down the common case (0-1 retries, no contention).
_RUN_NUMBER_RETRIES = 10
_RETRY_BACKOFF_BASE_S = 0.02

# Deadlock (1213) and lock-wait-timeout (1205): both leave the row unclaimed
# and are safe to retry. See the comment in create_run() for why these show
# up here at all.
_RETRYABLE_ERRNOS = {1213, 1205}


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, MySQLdb.IntegrityError):
        return True
    return (
        isinstance(exc, MySQLdb.OperationalError)
        and bool(exc.args)
        and exc.args[0] in _RETRYABLE_ERRNOS
    )


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

    run_number is allocated inside this same transaction rather than by a
    separate, unguarded SELECT MAX(...) — a read-then-write outside a
    transaction would let two concurrent starts claim the same number, and
    run_number names DataAcquisition stages, so a collision would silently
    merge two runs' data in the historian.

    MySQL rejects selecting FROM the table an INSERT targets (error 1093), so
    unlike the single OUTPUT-clause statement this once was, the number is
    read with its own SELECT and then inserted as a second statement — both
    inside one SERIALIZABLE transaction. Under SERIALIZABLE, InnoDB turns the
    plain SELECT MAX(run_number) into a shared locking read, which takes a
    next-key lock on the gap above the current highest number. Two concurrent
    starts can both acquire that SHARED lock (shared/shared don't conflict),
    then both try to insert — so the loser comes back as either a 1213
    deadlock (InnoDB breaks the cycle) or, in a narrower window, a 1062
    duplicate-key error. Both are retryable, and neither can ever let two
    runs share a number: the UNIQUE index on run_number is the actual
    guarantee: the isolation level only makes the collision rare rather than
    common.
    """
    now = _now()
    last_exc = None
    for attempt in range(_RUN_NUMBER_RETRIES):
        try:
            with transaction(isolation=SERIALIZABLE) as cur:
                cur.execute("SELECT COALESCE(MAX(run_number), 0) + 1 AS next_number FROM runs")
                run_number = cur.fetchone()["next_number"]
                cur.execute(
                    """INSERT INTO runs (
                           run_number, wire_run_id, name, config_id, config_snapshot_json,
                           daq_experiment_id, daq_experiment_name,
                           requested_spec_json, acked_spec_json,
                           started_at, confirmed_at, ended_at,
                           outcome, outcome_detail, latch_cause, recorded, operator
                       ) VALUES (
                           %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, NULL, NULL,
                           'pending', NULL, NULL, %s, %s
                       )""",
                    (
                        run_number, wire_run_id, name, config_id, json.dumps(config_snapshot),
                        daq_experiment_id, daq_experiment_name,
                        json.dumps(requested_spec), now,
                        1 if daq_experiment_id is not None else 0, operator,
                    ),
                )
                new_id = cur.lastrowid
            return get_run(new_id)
        except (MySQLdb.IntegrityError, MySQLdb.OperationalError) as exc:
            if not _is_retryable(exc):
                raise
            last_exc = exc
            if attempt < _RUN_NUMBER_RETRIES - 1:
                # Jittered backoff: colliding threads that retried in lockstep
                # would just deadlock again, so spread them out a little.
                time.sleep(_RETRY_BACKOFF_BASE_S * (attempt + 1) * random.uniform(0.5, 1.5))
    raise last_exc


def set_wire_run_id(run_id: int, wire_run_id: str) -> None:
    """Record the runId sent on the cmd topic, so confirm() can be verified
    against the run it belongs to instead of trusting the client."""
    with cursor() as cur:
        cur.execute(
            "UPDATE runs SET wire_run_id = %s WHERE id = %s", (wire_run_id, run_id)
        )


def set_acked_spec(run_id: int, acked_spec: dict) -> None:
    """Record what the firmware actually agreed to run, from the ack."""
    with cursor() as cur:
        cur.execute(
            "UPDATE runs SET acked_spec_json = %s WHERE id = %s",
            (json.dumps(acked_spec), run_id),
        )


def mark_confirmed(run_id: int) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE runs SET confirmed_at = %s WHERE id = %s", (_now(), run_id)
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
            """UPDATE runs SET ended_at = %s, outcome = %s, outcome_detail = %s, latch_cause = %s
               WHERE id = %s""",
            (_now(), outcome, outcome_detail, latch_cause, run_id),
        )
    return get_run(run_id)


def delete_run(run_id: int) -> None:
    """Hard-delete a run row. Only for a run that was never recorded (no
    layout_snapshots row can reference it — see app.runs._end_run) — a
    recorded run is the permanent record and must never go through here."""
    with cursor() as cur:
        cur.execute("DELETE FROM runs WHERE id = %s", (run_id,))


def get_run(run_id: int) -> Optional[dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def get_latest_run() -> Optional[dict]:
    """The most recently started run, ended or not — used by the Control view
    to know which run is 'current' after a page reload."""
    with cursor() as cur:
        cur.execute("SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT 1")
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
            "SELECT * FROM runs WHERE ended_at IS NULL ORDER BY started_at DESC, id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def list_runs(limit: int = 100, offset: int = 0) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """SELECT * FROM runs ORDER BY started_at DESC, id DESC
               LIMIT %s OFFSET %s""",
            (limit, offset),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
