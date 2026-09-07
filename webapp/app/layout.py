"""
app.layout — sensor config + layout snapshots, at the level routes need.

Thin orchestration over app.db: capturing a snapshot at run start, and
resolving "what layout should replay draw against" with the documented
fallback (current layout, and say so) when a run predates snapshotting.
"""

from typing import Optional

import app.db as db


def current_layout() -> list[dict]:
    return db.list_sensors(enabled_only=False)


def capture_for_run(run_id: int, daq_experiment_id: Optional[int]) -> dict:
    layout = db.list_sensors(enabled_only=True)
    return db.capture_snapshot(run_id=run_id, daq_experiment_id=daq_experiment_id, layout=layout)


def layout_for_replay(run_id: int) -> dict:
    """
    Returns {"layout": [...], "is_current_fallback": bool}. When a run has no
    snapshot, replay must draw with the current layout AND say so — never
    silently misplace sensors (design spec, 'Layout snapshots').
    """
    snapshot = db.get_snapshot_for_run(run_id)
    if snapshot is not None:
        return {"layout": snapshot["layout"], "is_current_fallback": False}
    return {"layout": current_layout(), "is_current_fallback": True}
