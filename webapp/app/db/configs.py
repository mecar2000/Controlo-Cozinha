"""db.configs — run_configs CRUD. Configs are editable presets; runs snapshot
them at start time (see db.runs), so editing a config never rewrites history."""

import json
from datetime import datetime, timezone
from typing import Optional

from ._core import cursor, to_db_datetime, from_db_datetime


def _now():
    return to_db_datetime(datetime.now(timezone.utc))


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "spec": json.loads(row["spec_json"]),
        "created_at": from_db_datetime(row["created_at"]),
        "updated_at": from_db_datetime(row["updated_at"]),
        "archived": bool(row["archived"]),
    }


def list_configs(include_archived: bool = False) -> list[dict]:
    with cursor() as cur:
        if include_archived:
            cur.execute("SELECT * FROM run_configs ORDER BY updated_at DESC")
        else:
            cur.execute(
                "SELECT * FROM run_configs WHERE archived = 0 ORDER BY updated_at DESC"
            )
        return [_row_to_dict(r) for r in cur.fetchall()]


def get_config(config_id: int) -> Optional[dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM run_configs WHERE id = %s", (config_id,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def create_config(name: str, spec: dict) -> dict:
    now = _now()
    with cursor() as cur:
        cur.execute(
            """INSERT INTO run_configs (name, spec_json, created_at, updated_at, archived)
               VALUES (%s, %s, %s, %s, 0)""",
            (name, json.dumps(spec), now, now),
        )
        new_id = cur.lastrowid
    return get_config(new_id)


def update_config(config_id: int, name: str, spec: dict) -> Optional[dict]:
    with cursor() as cur:
        cur.execute(
            """UPDATE run_configs SET name = %s, spec_json = %s, updated_at = %s
               WHERE id = %s""",
            (name, json.dumps(spec), _now(), config_id),
        )
    return get_config(config_id)


def archive_config(config_id: int) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE run_configs SET archived = 1, updated_at = %s WHERE id = %s",
            (_now(), config_id),
        )
