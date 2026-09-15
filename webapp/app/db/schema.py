"""
db.schema — database/table creation for KitchenControl.

KitchenControl stores what was commanded and what happened, never what was
measured — readings live in DataAcquisition (see the design spec's "Data
model" section). Four tables: run_configs, runs, sensor_config,
layout_snapshots.
"""

import re
import threading

from app.config import DB_NAME
from ._core import _open_raw, cursor

_db_ready = False
_ready_lock = threading.Lock()

# CREATE DATABASE cannot take a parameter marker, so DB_NAME has to be
# interpolated. It comes from the environment rather than a request, but this
# is the one raw-interpolation site in an otherwise fully parameterized layer,
# and CREATE DATABASE IF NOT EXISTS has no existence check to hide behind
# (MySQL supports it natively) — this regex is now the ONLY thing standing
# between an env var and raw SQL here, so refuse anything that isn't a plain
# identifier rather than trusting that.
_SAFE_DB_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def init_db() -> None:
    """Ensure the KitchenControl database and tables exist. Safe to call
    multiple times (e.g. across gunicorn workers)."""
    global _db_ready
    with _ready_lock:
        if _db_ready:
            return
        _ensure_database()
        _create_tables()
        _db_ready = True
    print(f"[DB] Connected to MySQL — database: {DB_NAME}")


def _ensure_database() -> None:
    if not _SAFE_DB_NAME.match(DB_NAME):
        raise ValueError(
            f"DB_NAME {DB_NAME!r} is not a plain SQL identifier — refusing to "
            f"interpolate it into CREATE DATABASE"
        )
    conn = _open_raw()
    try:
        cur = conn.cursor()
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
            f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        cur.close()
    finally:
        conn.close()


def _create_tables() -> None:
    with cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS run_configs (
                id          INT           AUTO_INCREMENT PRIMARY KEY,
                name        VARCHAR(200)  NOT NULL,
                spec_json   LONGTEXT      NOT NULL,
                created_at  DATETIME(3)   NOT NULL,
                updated_at  DATETIME(3)   NOT NULL,
                archived    TINYINT(1)    NOT NULL DEFAULT 0
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # run_number is a real column (not derived from id) so stage names
        # and future batch repeats get a stable, collision-free source — see
        # the design spec's "Key fields" section.
        # wire_run_id is the runId sent on the MQTT cmd topic. Persisted (not
        # just held in memory) so confirm() can be checked against the run it
        # actually belongs to, and so the frontend can recover it after a
        # reload instead of passing an unverified id back to the server.
        #
        # run_number is UNIQUE: it names DataAcquisition stages (run{N}-leak),
        # so two runs sharing a number would silently merge in the historian.
        # This UNIQUE index is also what create_run() in runs.py leans on for
        # its concurrency safety — see the comment there.
        #
        # run_configs must be created before this table: MySQL requires an
        # FK's parent table to already exist, and config_id references it.
        #
        # The FK is a real, named CONSTRAINT ... FOREIGN KEY clause, not the
        # column-level `INT NULL REFERENCES tbl(id)` shorthand T-SQL accepts:
        # MySQL parses that shorthand but silently does NOT enforce it (no
        # constraint is actually created), which would make this a quiet
        # loss of referential integrity rather than a like-for-like port.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id                     INT           AUTO_INCREMENT PRIMARY KEY,
                run_number             INT           NOT NULL UNIQUE,
                wire_run_id            VARCHAR(64)   NULL,
                name                   VARCHAR(200)  NOT NULL,
                config_id              INT           NULL,
                config_snapshot_json   LONGTEXT      NOT NULL,
                daq_experiment_id      INT           NULL,
                daq_experiment_name    VARCHAR(200)  NULL,
                requested_spec_json    LONGTEXT      NOT NULL,
                acked_spec_json        LONGTEXT      NULL,
                started_at             DATETIME(3)   NOT NULL,
                confirmed_at           DATETIME(3)   NULL,
                ended_at               DATETIME(3)   NULL,
                outcome                VARCHAR(32)   NOT NULL DEFAULT 'pending',
                outcome_detail         LONGTEXT      NULL,
                latch_cause            VARCHAR(64)   NULL,
                recorded               TINYINT(1)    NOT NULL DEFAULT 0,
                operator               VARCHAR(200)  NULL,
                CONSTRAINT fk_runs_config_id
                    FOREIGN KEY (config_id) REFERENCES run_configs(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # Migration for databases created before wire_run_id existed. MySQL
        # has no `ADD COLUMN IF NOT EXISTS` (that's a MariaDB extension), so
        # the check is a query against information_schema instead.
        cur.execute(
            """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = 'runs'
                 AND COLUMN_NAME = 'wire_run_id'"""
        )
        if cur.fetchone()["n"] == 0:
            cur.execute("ALTER TABLE runs ADD COLUMN wire_run_id VARCHAR(64) NULL")

        # archived is soft-delete: a "removed" sensor stays in the table (and
        # so stays joinable by historical runs/layout_snapshots that recorded
        # its sensor_key) rather than being hard-deleted. Replaces an earlier
        # design where DELETE was permanent (problems.txt: "there should be
        # memory of previous ones").
        #
        # firmware_index (0-5, KITCHEN_WIRED_LOCAL_SENSORS - 1) lets a newly
        # defined sensor receive a firmware danger threshold with no naming
        # requirement — routes/thresholds.py previously required
        # daq_sensor_name to literally be "H2-N" (problems.txt: sensors
        # "shouldnt be predefined"). NULL means "use the H2-N fallback, or
        # this sensor has no threshold at all".
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sensor_config (
                id              INT           AUTO_INCREMENT PRIMARY KEY,
                sensor_key      VARCHAR(64)   NOT NULL UNIQUE,
                label           VARCHAR(200)  NOT NULL,
                x               DOUBLE        NOT NULL,
                y               DOUBLE        NOT NULL,
                z               DOUBLE        NOT NULL,
                enabled         TINYINT(1)    NOT NULL DEFAULT 1,
                archived        TINYINT(1)    NOT NULL DEFAULT 0,
                daq_device_id   VARCHAR(64)   NULL,
                daq_sensor_name VARCHAR(64)   NULL,
                firmware_index  TINYINT       NULL,
                daq_pin         INT           NULL,
                updated_at      DATETIME(3)   NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # Migrations for databases created before these columns existed (same
        # information_schema pattern as wire_run_id above — MySQL has no ADD
        # COLUMN IF NOT EXISTS).
        #
        # daq_pin (Part 5: PLC/sensor commissioning wizard) is the encoded
        # DataAcquisition pin (app.daq.pin_label's input) this sensor is
        # bound to. daq_sensor_name is DERIVED from it, never hand-typed —
        # see routes/sensors.py::upsert_sensor — to close the trap where a
        # sensor is named to match DAQ's friendly name while the device
        # actually still publishes on a different pin.
        for column, ddl in (
            ("archived", "ALTER TABLE sensor_config ADD COLUMN archived TINYINT(1) NOT NULL DEFAULT 0"),
            ("firmware_index", "ALTER TABLE sensor_config ADD COLUMN firmware_index TINYINT NULL"),
            ("daq_pin", "ALTER TABLE sensor_config ADD COLUMN daq_pin INT NULL"),
        ):
            cur.execute(
                """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = 'sensor_config'
                     AND COLUMN_NAME = %s""",
                (column,),
            )
            if cur.fetchone()["n"] == 0:
                cur.execute(ddl)

        # runs must be created before this table: run_id references it.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS layout_snapshots (
                id                INT           AUTO_INCREMENT PRIMARY KEY,
                daq_experiment_id INT           NULL,
                run_id            INT           NULL,
                layout_json       LONGTEXT      NOT NULL,
                captured_at       DATETIME(3)   NOT NULL,
                CONSTRAINT fk_layout_snapshots_run_id
                    FOREIGN KEY (run_id) REFERENCES runs(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
