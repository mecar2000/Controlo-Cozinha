"""
app.db — KitchenControl MySQL layer, re-exported flat so callers write
`db.create_run(...)` rather than reaching into submodules directly.
"""

from .schema import init_db
from .configs import (
    list_configs,
    get_config,
    create_config,
    update_config,
    archive_config,
)
from .runs import (
    LATCH_CAUSES,
    OUTCOMES,
    next_run_number,
    run_name_exists,
    next_available_run_name,
    create_run,
    set_wire_run_id,
    set_acked_spec,
    mark_confirmed,
    mark_ended,
    delete_run,
    get_run,
    get_latest_run,
    get_active_run,
    list_runs,
)
from .sensor_config import (
    list_sensors,
    get_sensor,
    upsert_sensor,
    delete_sensor,
    set_firmware_index,
)
from .layout_snapshots import (
    capture_snapshot,
    get_snapshot_for_run,
)

__all__ = [
    "init_db",
    "list_configs", "get_config", "create_config", "update_config", "archive_config",
    "LATCH_CAUSES", "OUTCOMES", "next_run_number", "run_name_exists", "next_available_run_name",
    "create_run", "set_wire_run_id",
    "set_acked_spec", "mark_confirmed", "mark_ended", "delete_run", "get_run", "get_latest_run",
    "get_active_run", "list_runs",
    "list_sensors", "get_sensor", "upsert_sensor",
    "delete_sensor", "set_firmware_index",
    "capture_snapshot", "get_snapshot_for_run",
]
