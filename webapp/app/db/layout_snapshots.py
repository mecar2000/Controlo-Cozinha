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

from ._core import cursor


def _now():
    return datetime.now(timezone.utc)


def _row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "daq_experiment_id": row.daq_experiment_id,
        "run_id": row.run_id,
        "layout": json.loads(row.layout_json),
        "captured_at": row.captured_at.isoformat(),
    }


def capture_snapshot(
    *, run_id: Optional[int], daq_experiment_id: Optional[int], layout: list[dict]
) -> dict:
    """layout is the list[dict] shape from db.sensor_config.list_sensors()."""
    now = _now()
    with cursor() as cur:
        cur.execute(
            """INSERT INTO layout_snapshots (daq_experiment_id, run_id, layout_json, captured_at)
               OUTPUT INSERTED.id
               VALUES (?, ?, ?, ?)""",
            daq_experiment_id, run_id, json.dumps(layout), now,
        )
        new_id = cur.fetchone()[0]
    with cursor() as cur:
        cur.execute("SELECT * FROM layout_snapshots WHERE id = ?", new_id)
        return _row_to_dict(cur.fetchone())


def get_snapshot_for_run(run_id: int) -> Optional[dict]:
    with cursor() as cur:
        cur.execute(
            "SELECT TOP 1 * FROM layout_snapshots WHERE run_id = ? ORDER BY captured_at DESC",
            run_id,
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None
