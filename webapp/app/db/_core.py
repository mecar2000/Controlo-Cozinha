"""
db._core — connection pool + cursor helper for the KitchenControl database.

Same pooled-connection shape as DataAcquisition/dashboard/db/_core.py, sized
down: this app is single-database and single-machine, so one fixed pool
(no per-database dict) is enough.
"""

import queue
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import MySQLdb
import MySQLdb.cursors

from app.config import DB_SERVER, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

_POOL_MAX = 8
_pool: "queue.Queue[_PooledConnection]" = queue.Queue(maxsize=_POOL_MAX)
_pool_size = 0
_pool_lock = threading.Lock()


class _PooledConnection:
    """Thin proxy: delegates everything to a real MySQLdb connection, but
    .close() returns it to the pool instead of closing the socket."""

    __slots__ = ("_conn", "_closed", "_broken")

    def __init__(self, conn: MySQLdb.Connection):
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
        MySQL drops idle connections (wait_timeout) and can restart, so a
        socket that has been sitting in the pool is not necessarily still
        usable."""
        try:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT 1")
                cur.fetchone()
            finally:
                cur.close()
            return True
        except MySQLdb.Error:
            return False

    def _hard_close(self) -> None:
        try:
            self._conn.close()
        except MySQLdb.Error:
            pass


def _open_raw() -> MySQLdb.Connection:
    """Open a brand-new autocommit connection to the server, i.e. no database
    selected — used once at startup to create KitchenControl itself."""
    return MySQLdb.connect(
        host=DB_SERVER,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        charset="utf8mb4",
        autocommit=True,
    )


def _open_db() -> MySQLdb.Connection:
    """Open a brand-new autocommit connection to the KitchenControl database.

    autocommit=True matters more here than it did under pyodbc: MySQLdb
    defaults it OFF, and under InnoDB's REPEATABLE READ an implicit,
    never-committed transaction pins a snapshot — a pooled connection would
    otherwise keep serving stale reads indefinitely. transaction() below turns
    it off and back on explicitly for the duration of a real transaction.

    cursorclass=DictCursor so callers can do row["col"] regardless of query
    shape (including SELECT *, where the keys come from cursor.description).
    """
    return MySQLdb.connect(
        host=DB_SERVER,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=MySQLdb.cursors.DictCursor,
    )


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

    Pooled connections are validated before being handed out: MySQL drops
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


# Connection-level errnos: the socket itself is suspect, so the connection
# must be discarded rather than handed to the next caller.
#   2002 can't connect (socket)      2003 can't connect (TCP)
#   2006 server has gone away        2013 lost connection during query
#   2055 lost connection to server   1053 server shutdown in progress
_FATAL_ERRNOS = {2002, 2003, 2006, 2013, 2055, 1053}


def _is_connection_broken(exc: Exception) -> bool:
    """True when the error means the SOCKET is gone, not just the statement.

    MySQLdb raises OperationalError for BOTH "server gone away" (2006) and a
    deadlock (1213). A deadlock leaves the connection perfectly usable — its
    transaction was rolled back, nothing more — so classifying by exception
    type alone would churn a healthy pool under contention. Classify by errno
    instead.

    InterfaceError does not inherit from DatabaseError (it inherits directly
    from MySQLdb.Error), so it has to be checked on its own rather than being
    swept up by an isinstance(exc, DatabaseError) check.
    """
    if isinstance(exc, (MySQLdb.ProgrammingError, MySQLdb.IntegrityError,
                         MySQLdb.DataError, MySQLdb.NotSupportedError)):
        return False
    if isinstance(exc, MySQLdb.OperationalError):
        code = exc.args[0] if exc.args else None
        return code in _FATAL_ERRNOS
    if isinstance(exc, MySQLdb.InterfaceError):
        return True  # driver-level: the connection object itself is unusable
    return True  # unknown MySQLdb.Error: be conservative, discard


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
    except MySQLdb.Error as exc:
        if _is_connection_broken(exc):
            conn.mark_broken()
        raise
    finally:
        conn.close()


# Isolation levels this app asks for, as the SQL that sets them. Kept as a
# small allowlist rather than accepting arbitrary SQL from a caller.
#
# SESSION is deliberate: MySQL's bare `SET TRANSACTION ISOLATION LEVEL X`
# applies to the NEXT transaction only, whereas this pool needs the level to
# hold for the duration of the connection's checkout and then be put back.
# SESSION gives the per-connection semantics the restore logic below assumes.
SERIALIZABLE = "SERIALIZABLE"
READ_COMMITTED = "READ COMMITTED"
REPEATABLE_READ = "REPEATABLE READ"  # MySQL's server default
_ISOLATION_SQL = {
    SERIALIZABLE: "SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE",
    READ_COMMITTED: "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
    REPEATABLE_READ: "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ",
}
# Restore target when a transaction() with a non-default isolation ends: the
# server's own default, not READ COMMITTED — MySQL defaults to REPEATABLE
# READ, so restoring to READ COMMITTED would leave every pooled connection
# permanently off-default, which is the exact leak this guard exists to
# prevent, just inverted.
_DEFAULT_ISOLATION = REPEATABLE_READ


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
        conn._conn.autocommit(False)
        cur = conn.cursor()
        try:
            if isolation is not None:
                cur.execute(_ISOLATION_SQL[isolation])
            yield cur
            conn._conn.commit()
        except Exception:
            try:
                conn._conn.rollback()
            except MySQLdb.Error:
                conn.mark_broken()
            raise
        finally:
            cur.close()
    except MySQLdb.Error as exc:
        if _is_connection_broken(exc):
            conn.mark_broken()
        raise
    finally:
        try:
            if isolation is not None and isolation != _DEFAULT_ISOLATION:
                restore = conn.cursor()
                try:
                    restore.execute(_ISOLATION_SQL[_DEFAULT_ISOLATION])
                finally:
                    restore.close()
            conn._conn.autocommit(True)
        except MySQLdb.Error:
            conn.mark_broken()
        conn.close()


def to_db_datetime(dt):
    """Strip tzinfo for MySQL DATETIME(3), which has no timezone concept.
    Normalised to UTC first, so the column holds UTC throughout and a naive
    value written by any code path means the same instant."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=None)


def from_db_datetime(dt):
    """Re-attach UTC to a naive DATETIME(3) read back from MySQL, and render
    it as an ISO string.

    The tzinfo is not cosmetic: the frontend does `new Date(iso)`, and JS
    parses an offset-less date-time as LOCAL time. Dropping the '+00:00'
    would shift every displayed run timestamp by the browser's UTC offset,
    silently and plausibly.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()
