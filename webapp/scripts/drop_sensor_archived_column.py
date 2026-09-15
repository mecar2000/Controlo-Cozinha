"""
One-off script: drop sensor_config.archived from the live database now that
sensor archiving (soft delete) has been removed in favour of a plain hard
delete — see routes/sensors.py.

NOT wired into app boot and NOT a standing migration in db/schema.py — this
is a manual, one-time operational step. Run it by hand against the real
MySQL once, after deploying the code that no longer reads/writes this
column:

    cd webapp && python scripts/drop_sensor_archived_column.py

Idempotent: safe to re-run, a no-op if the column is already gone.
"""

from app.db._core import cursor


def main() -> None:
    with cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = 'sensor_config'
                 AND COLUMN_NAME = 'archived'"""
        )
        if cur.fetchone()["n"] == 0:
            print("sensor_config.archived already absent — nothing to do")
            return
        cur.execute("ALTER TABLE sensor_config DROP COLUMN archived")
        print("dropped sensor_config.archived")


if __name__ == "__main__":
    main()
