"""
db.schema — database/table creation for KitchenControl.

KitchenControl stores what was commanded and what happened, never what was
measured — readings live in DataAcquisition (see the design spec's "Data
model" section). Four tables: run_configs, runs, sensor_config,
layout_snapshots.
"""

import re
import threading

import pyodbc

from app.config import DB_NAME
from ._core import _open_raw, cursor

_db_ready = False
_ready_lock = threading.Lock()

# CREATE DATABASE cannot take a parameter marker, so DB_NAME has to be
# interpolated. It comes from the environment rather than a request, but this
# is the one raw-interpolation site in an otherwise fully parameterized layer —
# so refuse anything that isn't a plain identifier rather than trusting that.
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
    print(f"[DB] Connected to SQL Server — database: {DB_NAME}")


def _ensure_database() -> None:
    if not _SAFE_DB_NAME.match(DB_NAME):
        raise ValueError(
            f"DB_NAME {DB_NAME!r} is not a plain SQL identifier — refusing to "
            f"interpolate it into CREATE DATABASE"
        )
    conn = _open_raw()
    try:
        cur = conn.cursor()
        # The existence check takes a real parameter; only the bracketed
        # identifier in CREATE DATABASE has to be interpolated.
        cur.execute(
            f"""
            IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = ?)
            EXEC('CREATE DATABASE [{DB_NAME}]')
            """,
            DB_NAME,
        )
        cur.close()
    finally:
        conn.close()


def _create_tables() -> None:
    with cursor() as cur:
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'run_configs')
            CREATE TABLE run_configs (
                id          INT           IDENTITY(1,1) PRIMARY KEY,
                name        NVARCHAR(200) NOT NULL,
                spec_json   NVARCHAR(MAX) NOT NULL,
                created_at  DATETIME2(3)  NOT NULL,
                updated_at  DATETIME2(3)  NOT NULL,
                archived    BIT           NOT NULL DEFAULT 0
            )
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
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'runs')
            CREATE TABLE runs (
                id                     INT           IDENTITY(1,1) PRIMARY KEY,
                run_number             INT           NOT NULL UNIQUE,
                wire_run_id            NVARCHAR(64)  NULL,
                name                   NVARCHAR(200) NOT NULL,
                config_id              INT           NULL REFERENCES run_configs(id),
                config_snapshot_json   NVARCHAR(MAX) NOT NULL,
                daq_experiment_id      INT           NULL,
                daq_experiment_name    NVARCHAR(200) NULL,
                requested_spec_json    NVARCHAR(MAX) NOT NULL,
                acked_spec_json        NVARCHAR(MAX) NULL,
                started_at             DATETIME2(3)  NOT NULL,
                confirmed_at           DATETIME2(3)  NULL,
                ended_at               DATETIME2(3)  NULL,
                outcome                NVARCHAR(32)  NOT NULL DEFAULT 'pending',
                outcome_detail         NVARCHAR(MAX) NULL,
                latch_cause            NVARCHAR(64)  NULL,
                recorded               BIT           NOT NULL DEFAULT 0,
                operator               NVARCHAR(200) NULL
            )
        """)

        # Migration for databases created before wire_run_id existed.
        cur.execute("""
            IF NOT EXISTS (
                SELECT * FROM sys.columns
                WHERE object_id = OBJECT_ID('runs') AND name = 'wire_run_id'
            )
            ALTER TABLE runs ADD wire_run_id NVARCHAR(64) NULL
        """)

        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'sensor_config')
            CREATE TABLE sensor_config (
                id              INT           IDENTITY(1,1) PRIMARY KEY,
                sensor_key      NVARCHAR(64)  NOT NULL UNIQUE,
                label           NVARCHAR(200) NOT NULL,
                x               FLOAT         NOT NULL,
                y               FLOAT         NOT NULL,
                z               FLOAT         NOT NULL,
                enabled         BIT           NOT NULL DEFAULT 1,
                daq_device_id   NVARCHAR(64)  NULL,
                daq_sensor_name NVARCHAR(64)  NULL,
                updated_at      DATETIME2(3)  NOT NULL
            )
        """)

        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'layout_snapshots')
            CREATE TABLE layout_snapshots (
                id                INT           IDENTITY(1,1) PRIMARY KEY,
                daq_experiment_id INT           NULL,
                run_id            INT           NULL REFERENCES runs(id),
                layout_json       NVARCHAR(MAX) NOT NULL,
                captured_at       DATETIME2(3)  NOT NULL
            )
        """)
