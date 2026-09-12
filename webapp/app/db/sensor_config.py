"""
db.sensor_config — sensor positions for the 3D room.

Room geometry is hardcoded in the frontend (fixed civil geometry); sensor
positions live here because sensors are what actually churn (added, removed,
moved between experiments) — see the design spec's "Room and sensors" section.

Positions are numeric (X/Y/Z in metres against the room's coordinate frame),
entered by hand from a tape measure — never drag-and-drop.

No placeholder sensors are seeded here or anywhere else. An earlier version
of this module auto-inserted 6 guessed positions on every app boot
(seed_placeholder_layout(), removed) — which meant deleting every sensor
just silently recreated them on the next restart (problems.txt: "there
shouldn't be some predefined sensors like there are"). An empty room is a
legitimate, normal state; sensors are added through the sensor editor.
"""

from datetime import datetime, timezone
from typing import Optional

from ._core import cursor, to_db_datetime, from_db_datetime


def _now():
    return to_db_datetime(datetime.now(timezone.utc))


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "sensor_key": row["sensor_key"],
        "label": row["label"],
        "x": row["x"],
        "y": row["y"],
        "z": row["z"],
        "enabled": bool(row["enabled"]),
        "archived": bool(row["archived"]),
        "daq_device_id": row["daq_device_id"],
        "daq_sensor_name": row["daq_sensor_name"],
        "firmware_index": row["firmware_index"],
        "updated_at": from_db_datetime(row["updated_at"]),
    }


def list_sensors(enabled_only: bool = False, include_archived: bool = False) -> list[dict]:
    """Archived sensors are excluded by default — they are kept for history
    (a past run's layout snapshot references its sensor_key) but should not
    clutter the room view or a fresh sensor picker. Pass include_archived to
    see them (the sensor editor's "archived" tab)."""
    clauses = []
    if enabled_only:
        clauses.append("enabled = 1")
    if not include_archived:
        clauses.append("archived = 0")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with cursor() as cur:
        cur.execute(f"SELECT * FROM sensor_config{where} ORDER BY sensor_key")
        return [_row_to_dict(r) for r in cur.fetchall()]


def get_sensor(sensor_key: str) -> Optional[dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM sensor_config WHERE sensor_key = %s", (sensor_key,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def upsert_sensor(
    sensor_key: str,
    *,
    label: str,
    x: float,
    y: float,
    z: float,
    enabled: bool = True,
    daq_device_id: Optional[str] = None,
    daq_sensor_name: Optional[str] = None,
) -> dict:
    now = _now()
    with cursor() as cur:
        cur.execute("SELECT id FROM sensor_config WHERE sensor_key = %s", (sensor_key,))
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """UPDATE sensor_config
                   SET label = %s, x = %s, y = %s, z = %s, enabled = %s,
                       daq_device_id = %s, daq_sensor_name = %s, updated_at = %s
                   WHERE sensor_key = %s""",
                (label, x, y, z, 1 if enabled else 0,
                 daq_device_id, daq_sensor_name, now, sensor_key),
            )
        else:
            cur.execute(
                """INSERT INTO sensor_config (
                       sensor_key, label, x, y, z, enabled,
                       daq_device_id, daq_sensor_name, updated_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (sensor_key, label, x, y, z, 1 if enabled else 0,
                 daq_device_id, daq_sensor_name, now),
            )
    return get_sensor(sensor_key)


def archive_sensor(sensor_key: str) -> None:
    """Soft delete. Not `enabled = 0`, which already means something else
    (a real sensor temporarily excluded from the live view/heatmap) —
    archived means "removed", enabled means "paused"."""
    now = _now()
    with cursor() as cur:
        cur.execute(
            "UPDATE sensor_config SET archived = 1, updated_at = %s WHERE sensor_key = %s",
            (now, sensor_key),
        )


def restore_sensor(sensor_key: str) -> None:
    now = _now()
    with cursor() as cur:
        cur.execute(
            "UPDATE sensor_config SET archived = 0, updated_at = %s WHERE sensor_key = %s",
            (now, sensor_key),
        )


def set_firmware_index(sensor_key: str, firmware_index: Optional[int]) -> None:
    """Set (or clear, with None) which firmware channel (0-5) this sensor's
    danger threshold goes to — see routes/thresholds.py. Kept separate from
    upsert_sensor(): this is a one-time wiring fact about a physical
    channel, not part of the position/label editing flow."""
    now = _now()
    with cursor() as cur:
        cur.execute(
            "UPDATE sensor_config SET firmware_index = %s, updated_at = %s WHERE sensor_key = %s",
            (firmware_index, now, sensor_key),
        )
