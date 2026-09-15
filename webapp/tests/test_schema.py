"""
db.schema — migration idempotency. Runs against the real, reachable test
MySQL (see controlo-cozinha-repo-layout memory: this dev machine can reach
it directly), the same way app startup does — there is nothing to
monkeypatch here, since schema.py's whole job IS talking to the real
database. MySQL has no `ADD COLUMN IF NOT EXISTS` (that's a MariaDB
extension), so schema.py checks information_schema before every ALTER —
this file pins that a repeated init_db() never re-runs an ALTER that would
otherwise raise "duplicate column".
"""

import app.db as db
from app.db._core import cursor


def _sensor_config_columns() -> set[str]:
    with cursor() as cur:
        cur.execute(
            """SELECT COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sensor_config'"""
        )
        return {row["COLUMN_NAME"] for row in cur.fetchall()}


def test_init_db_is_idempotent_across_repeated_calls():
    """The real regression this guards: schema.py's global _db_ready flag
    short-circuits repeated calls WITHIN one process, but a fresh call to
    _create_tables() itself (as happens on a second process/worker) must
    also be safe — it must not attempt to re-add a column that already
    exists."""
    db.init_db()
    db.init_db()
    db.init_db()


def test_sensor_config_has_daq_pin_column_after_init():
    db.init_db()
    assert "daq_pin" in _sensor_config_columns()
