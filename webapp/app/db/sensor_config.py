"""
db.sensor_config — sensor positions for the 3D room.

Room geometry is hardcoded in the frontend (fixed civil geometry); sensor
positions live here because sensors are what actually churn (added, removed,
moved between experiments) — see the design spec's "Room and sensors" section.

Positions are numeric (X/Y/Z in metres against the room's coordinate frame),
entered by hand from a tape measure — never drag-and-drop.
"""

from datetime import datetime, timezone
from typing import Optional

from ._core import cursor


def _now():
    return datetime.now(timezone.utc)


def _row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "sensor_key": row.sensor_key,
        "label": row.label,
        "x": row.x,
        "y": row.y,
        "z": row.z,
        "enabled": bool(row.enabled),
        "daq_device_id": row.daq_device_id,
        "daq_sensor_name": row.daq_sensor_name,
        "updated_at": row.updated_at.isoformat(),
    }


def list_sensors(enabled_only: bool = False) -> list[dict]:
    with cursor() as cur:
        if enabled_only:
            cur.execute("SELECT * FROM sensor_config WHERE enabled = 1 ORDER BY sensor_key")
        else:
            cur.execute("SELECT * FROM sensor_config ORDER BY sensor_key")
        return [_row_to_dict(r) for r in cur.fetchall()]


def get_sensor(sensor_key: str) -> Optional[dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM sensor_config WHERE sensor_key = ?", sensor_key)
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
        cur.execute("SELECT id FROM sensor_config WHERE sensor_key = ?", sensor_key)
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """UPDATE sensor_config
                   SET label = ?, x = ?, y = ?, z = ?, enabled = ?,
                       daq_device_id = ?, daq_sensor_name = ?, updated_at = ?
                   WHERE sensor_key = ?""",
                label, x, y, z, 1 if enabled else 0,
                daq_device_id, daq_sensor_name, now, sensor_key,
            )
        else:
            cur.execute(
                """INSERT INTO sensor_config (
                       sensor_key, label, x, y, z, enabled,
                       daq_device_id, daq_sensor_name, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                sensor_key, label, x, y, z, 1 if enabled else 0,
                daq_device_id, daq_sensor_name, now,
            )
    return get_sensor(sensor_key)


def delete_sensor(sensor_key: str) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM sensor_config WHERE sensor_key = ?", sensor_key)


def seed_placeholder_layout() -> None:
    """
    Seeds a placeholder layout so the 3D view and heatmap are exercisable
    before real room dimensions and sensor positions are measured. NOT the
    real kitchen — replace via the sensor-config UI once a tape measure has
    been used (see the design spec's open question 1).

    The room itself is REAL and measured (2.859 x 2.341 x 2.560 m, tape
    measure, 2026-09-07) and is hardcoded in the frontend's
    lib/roomGeometry.ts. Only these sensor POSITIONS are guesses.

    Each position below sits in air beside the fixture it is named for, not
    inside it — a sensor inside a cabinet has no air around it and would
    never read anything. Verified against roomGeometry.isInsideSolid(); keep
    that true when editing, and prefer the frontend's sensor editor, which
    checks it for you.

    Coordinate frame: origin at the floor corner where the BACK wall (the
    2.859 m equipment wall) meets the LEFT wall. x runs along the back wall,
    y away from it into the room, z up.
    """
    if list_sensors():
        return  # never overwrite a real layout

    placeholders = [
        # Along the counter run, at likely leak height
        ("sensor-1", "Counter — left", 0.45, 0.30, 0.95),
        ("sensor-2", "Counter — right", 2.35, 0.30, 0.95),
        # In the gap under the top cabinet
        ("sensor-3", "Under top cabinet", 0.74, 0.45, 1.40),
        # Below the extraction hood, where a plume would be drawn
        ("sensor-4", "Below exhaust hood", 1.53, 0.30, 2.10),
        # Beside the water heater
        ("sensor-5", "Beside water heater", 2.66, 0.42, 1.90),
        # Room-centre baseline
        ("sensor-6", "Room centre", 1.43, 1.30, 1.60),
    ]
    for key, label, x, y, z in placeholders:
        upsert_sensor(key, label=label, x=x, y=y, z=z, enabled=True)
