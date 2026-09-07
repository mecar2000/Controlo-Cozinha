"""
db._core — connection pool + cursor helper for the KitchenControl database.

Same pooled-connection shape as DataAcquisition/dashboard/db/_core.py, sized
down: this app is single-database and single-machine, so one fixed pool
(no per-database dict) is enough.
"""

import queue
import threading
from contextlib import contextmanager

import pyodbc

from app.config import DB_SERVER, DB_NAME, DB_DRIVER

_POOL_MAX = 8
_pool: "queue.Queue[_PooledConnection]" = queue.Queue(maxsize=_POOL_MAX)
_pool_size = 0
_pool_lock = threading.Lock()


class _PooledConnection:
    """Thin proxy: delegates everything to a real pyodbc connection, but
    .close() returns it to the pool instead of closing the socket."""

    __slots__ = ("_conn", "_closed", "_broken")

    def __init__(self, conn: pyodbc.Connection):
        self._conn = conn
        self._closed = False
        self._broken = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._broken:
            _discard_conn(self)
        else:
            _release_conn(self)

    def mark_broken(self) -> None:
        """Flag this connection so close() discards it instead of pooling it.
        Set when a statement raised a connection-level error: the server may
        have dropped it, and handing it to the next caller just moves the
        failure somewhere harder to read."""
        self._broken = True

    def is_alive(self) -> bool:
        """Cheap round-trip to confirm the server still has this connection.
        SQL Server drops idle connections (and restarts), so a socket that has
        been sitting in the pool is not necessarily still usable."""
        try:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT 1")
                cur.fetchone()
            finally:
                cur.close()
            return True
        except pyodbc.Error:
            return False

    def _hard_close(self) -> None:
        try:
            self._conn.close()
        except pyodbc.Error:
            pass


def _open_raw() -> pyodbc.Connection:
    """Open a brand-new autocommit connection to the master server, i.e. no
    DATABASE clause — used once at startup to create KitchenControl itself."""
    conn_str = (
        f"DRIVER={DB_DRIVER};"
        f"SERVER={DB_SERVER};"
        f"Trusted_Connection=yes;"
        f"TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str, autocommit=True)


def _open_db() -> pyodbc.Connection:
    """Open a brand-new autocommit connection to the KitchenControl database."""
    conn_str = (
        f"DRIVER={DB_DRIVER};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
        f"Trusted_Connection=yes;"
        f"TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str, autocommit=True)


def _new_conn() -> "_PooledConnection":
    """Open a connection, counting it against the pool cap. Decrements the
    count again if opening fails, so a database outage can't permanently
    exhaust the pool with slots that were never filled."""
    global _pool_size
    with _pool_lock:
        _pool_size += 1
    try:
        return _PooledConnection(_open_db())
    except Exception:
        with _pool_lock:
            _pool_size -= 1
        raise


def _get_conn() -> "_PooledConnection":
    """
    Check a connection out of the pool, or open one up to the cap.

    Pooled connections are validated before being handed out: SQL Server drops
    idle connections and can restart underneath us, and a dead socket would
    otherwise surface as a failure in whichever request happened to draw it.
    """
    global _pool_size
    while True:
        try:
            conn = _pool.get_nowait()
        except queue.Empty:
            break
        if conn.is_alive():
            conn._closed = False
            conn._broken = False
            return conn
        # Stale: drop it and free its slot so a fresh one can be opened.
        conn._hard_close()
        with _pool_lock:
            _pool_size -= 1

    with _pool_lock:
        can_open = _pool_size < _POOL_MAX
    if can_open:
        return _new_conn()

    # Pool is at capacity and empty — block for one to free up. It may have
    # gone stale while queued, so it gets the same validation.
    conn = _pool.get()
    if not conn.is_alive():
        conn._hard_close()
        with _pool_lock:
            _pool_size -= 1
        return _new_conn()
    conn._closed = False
    conn._broken = False
    return conn


def _release_conn(conn: "_PooledConnection") -> None:
    global _pool_size
    conn._closed = False
    try:
        _pool.put_nowait(conn)
    except queue.Full:
        conn._hard_close()
        with _pool_lock:
            _pool_size -= 1


def _discard_conn(conn: "_PooledConnection") -> None:
    """Close a connection for good and give its slot back to the cap."""
    global _pool_size
    conn._hard_close()
    with _pool_lock:
        _pool_size -= 1


@contextmanager
def cursor():
    """
    Context manager yielding a cursor on a pooled connection. Commits are
    implicit (autocommit=True on every pooled connection), so callers never
    need to call .commit(). The connection is returned to the pool on exit —
    unless the statement raised a connection-level error, in which case it is
    discarded rather than handed to the next caller in an unknown state.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()
    except pyodbc.Error as exc:
        # Programming/integrity errors leave the connection perfectly usable;
        # anything else may mean the socket itself is gone.
        if not isinstance(exc, (pyodbc.ProgrammingError, pyodbc.IntegrityError,
                                pyodbc.DataError)):
            conn.mark_broken()
        raise
    finally:
        conn.close()


# Isolation levels this app asks for, as the T-SQL that sets them. Kept as a
# small allowlist rather than accepting arbitrary SQL from a caller.
SERIALIZABLE = "SERIALIZABLE"
READ_COMMITTED = "READ COMMITTED"
_ISOLATION_SQL = {
    SERIALIZABLE: "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE",
    READ_COMMITTED: "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
}


@contextmanager
def transaction(isolation: "str | None" = None):
    """
    Like cursor(), but wraps the body in one real transaction: autocommit is
    turned off for the duration, the work is committed on success, and rolled
    back if the body raises. Use it when a read and the write that depends on
    it must be a single step — a plain cursor() autocommits each statement
    separately, so another connection can interleave between them.

    isolation is SERIALIZABLE or READ_COMMITTED. The level and autocommit are
    both restored before the connection goes back to the pool: a stricter
    isolation level leaking into an unrelated request would be a real bug, so
    a connection that cannot be restored is discarded instead.
    """
    if isolation is not None and isolation not in _ISOLATION_SQL:
        raise ValueError(f"unsupported isolation level: {isolation!r}")

    conn = _get_conn()
    try:
        conn._conn.autocommit = False
        cur = conn.cursor()
        try:
            if isolation is not None:
                cur.execute(_ISOLATION_SQL[isolation])
            yield cur
            conn._conn.commit()
        except Exception:
            try:
                conn._conn.rollback()
            except pyodbc.Error:
                conn.mark_broken()
            raise
        finally:
            cur.close()
    except pyodbc.Error as exc:
        if not isinstance(exc, (pyodbc.ProgrammingError, pyodbc.IntegrityError,
                                pyodbc.DataError)):
            conn.mark_broken()
        raise
    finally:
        try:
            if isolation is not None and isolation != READ_COMMITTED:
                restore = conn.cursor()
                try:
                    restore.execute(_ISOLATION_SQL[READ_COMMITTED])
                finally:
                    restore.close()
            conn._conn.autocommit = True
        except pyodbc.Error:
            conn.mark_broken()
        conn.close()
