"""
db.layout_snapshots — layout captured at run start, stored per run.

Sensors get added, removed and moved between experiments; without a snapshot,
an old replay would draw against today's layout and be quietly wrong (design
spec, "Layout snapshots"). If a run has no snapshot (predates this feature,
or a failed write), the caller must fall back to the current layout AND say
so — never silently misplace sensors.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from ._core import cursor, to_db_datetime, from_db_datetime


def _now():
    return to_db_datetime(datetime.now(timezone.utc))


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "daq_experiment_id": row["daq_experiment_id"],
        "run_id": row["run_id"],
        "layout": json.loads(row["layout_json"]),
        "captured_at": from_db_datetime(row["captured_at"]),
    }


def capture_snapshot(
    *, run_id: Optional[int], daq_experiment_id: Optional[int], layout: list[dict]
) -> dict:
    """layout is the list[dict] shape from db.sensor_config.list_sensors()."""
    now = _now()
    with cursor() as cur:
        cur.execute(
            """INSERT INTO layout_snapshots (daq_experiment_id, run_id, layout_json, captured_at)
               VALUES (%s, %s, %s, %s)""",
            (daq_experiment_id, run_id, json.dumps(layout), now),
        )
        new_id = cur.lastrowid
    with cursor() as cur:
        cur.execute("SELECT * FROM layout_snapshots WHERE id = %s", (new_id,))
        return _row_to_dict(cur.fetchone())


def get_snapshot_for_run(run_id: int) -> Optional[dict]:
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM layout_snapshots WHERE run_id = %s ORDER BY captured_at DESC LIMIT 1",
            (run_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None
