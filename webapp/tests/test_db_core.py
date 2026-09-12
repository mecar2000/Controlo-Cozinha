"""
Tests for db._core — the connection pool and the transaction helper.

There is no MySQL server in the test environment, so these drive a fake
MySQLdb connection that models the behaviour the pool actually depends on:
autocommit toggling, commit/rollback, and connections the server has dropped
underneath us. What is being tested is the pool's bookkeeping, not the
database.
"""

import queue

import MySQLdb
import pytest

import app.db._core as core


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.closed = False

    def execute(self, sql, *args):
        if self._conn.dead:
            raise MySQLdb.OperationalError(2006, "MySQL server has gone away")
        self._conn.statements.append(sql)
        return self

    def fetchone(self):
        return {"n": 1}

    def close(self):
        self.closed = True


class FakeConn:
    """Models the MySQLdb surface _core relies on.

    autocommit is a METHOD here, not an attribute — matching MySQLdb, where
    conn.autocommit is a callable, not a bool. If this were a bool attribute,
    _core's `conn._conn.autocommit(True)` calls would silently write over the
    real thing without ever exercising the bug that shape would hide.
    """

    def __init__(self):
        self.dead = False
        self.closed = False
        self._autocommit = True
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def autocommit(self, on):
        self._autocommit = bool(on)

    def cursor(self):
        if self.dead:
            raise MySQLdb.OperationalError(2006, "MySQL server has gone away")
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        if self.dead:
            raise MySQLdb.OperationalError(2006, "MySQL server has gone away")
        self.rollbacks += 1

    def close(self):
        self.closed = True


@pytest.fixture
def pool(monkeypatch):
    """A fresh, empty pool whose connections are FakeConns."""
    opened = []

    def _open_db():
        conn = FakeConn()
        opened.append(conn)
        return conn

    monkeypatch.setattr(core, "_open_db", _open_db)
    monkeypatch.setattr(core, "_pool", queue.Queue(maxsize=core._POOL_MAX))
    monkeypatch.setattr(core, "_pool_size", 0)
    return opened


# --- Pooling ---------------------------------------------------------------


def test_connection_is_reused(pool):
    with core.cursor():
        pass
    with core.cursor():
        pass
    assert len(pool) == 1, "second call should reuse the pooled connection"
    assert core._pool_size == 1


def test_dead_pooled_connection_is_replaced(pool):
    """MySQL drops idle connections and restarts; a dead socket must not be
    handed to whichever request happens to draw it."""
    with core.cursor():
        pass
    pool[0].dead = True

    with core.cursor() as cur:
        cur.execute("SELECT 1")

    assert len(pool) == 2, "a fresh connection should have been opened"
    assert pool[0].closed, "the dead one should have been closed"
    assert core._pool_size == 1, "its slot should have been freed, not leaked"


def test_pool_size_is_not_leaked_when_a_connection_is_discarded(pool):
    """_pool_size counts open connections against the cap. A slot that is
    counted but never filled shrinks the pool permanently."""
    for _ in range(3):
        with core.cursor():
            pass
        # Kill whatever is pooled so the next call has to replace it.
        core._pool.queue[0]._conn.dead = True

    assert core._pool_size == 1


def test_failed_open_does_not_consume_a_slot(monkeypatch):
    monkeypatch.setattr(core, "_pool", queue.Queue(maxsize=core._POOL_MAX))
    monkeypatch.setattr(core, "_pool_size", 0)

    def boom():
        raise MySQLdb.OperationalError(2003, "Can't connect to MySQL server")

    monkeypatch.setattr(core, "_open_db", boom)

    for _ in range(3):
        with pytest.raises(MySQLdb.OperationalError):
            with core.cursor():
                pass

    assert core._pool_size == 0, "a database outage must not exhaust the pool"


def test_broken_connection_is_not_returned_to_the_pool(pool):
    with pytest.raises(MySQLdb.OperationalError):
        with core.cursor() as cur:
            cur._conn.dead = True
            cur.execute("SELECT 1")

    assert pool[0].closed
    assert core._pool.empty()
    assert core._pool_size == 0


def test_programming_error_keeps_the_connection(pool):
    """A bad statement says nothing about the socket — pooling it is correct."""
    with pytest.raises(MySQLdb.ProgrammingError):
        with core.cursor():
            raise MySQLdb.ProgrammingError(1064, "you have an error in your SQL syntax")

    assert not pool[0].closed
    assert core._pool_size == 1


def test_deadlock_does_not_break_the_connection(pool):
    """A deadlock (1213) leaves the connection perfectly usable — only its
    transaction was rolled back. Classifying it as a broken connection would
    churn a healthy pool under contention, which is the whole point of
    classifying MySQLdb.OperationalError by errno rather than by type."""
    with pytest.raises(MySQLdb.OperationalError):
        with core.cursor():
            raise MySQLdb.OperationalError(1213, "Deadlock found trying to get lock")

    assert not pool[0].closed
    assert core._pool.qsize() == 1
    assert core._pool_size == 1


# --- transaction() ---------------------------------------------------------


def test_transaction_commits_and_restores_autocommit(pool):
    with core.transaction() as cur:
        cur.execute("INSERT INTO runs (run_number) VALUES (1)")

    conn = pool[0]
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert conn._autocommit is True, "autocommit must be restored for the pool"


def test_transaction_rolls_back_on_error(pool):
    with pytest.raises(ValueError):
        with core.transaction() as cur:
            cur.execute("INSERT INTO runs (run_number) VALUES (1)")
            raise ValueError("boom")

    conn = pool[0]
    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert conn._autocommit is True


def test_transaction_uses_the_autocommit_method(pool):
    """conn._conn.autocommit is a bound method on MySQLdb connections, not an
    attribute. _PooledConnection's __getattr__ delegation means assigning to
    it (`conn._conn.autocommit = False`) would silently shadow the method
    with a bool instead of raising — this asserts the method form is used."""
    with core.transaction():
        pass

    conn = pool[0]
    assert callable(conn.autocommit), "autocommit must still be the bound method"
    assert conn._autocommit is True


def test_transaction_sets_and_restores_isolation(pool):
    with core.transaction(isolation=core.SERIALIZABLE) as cur:
        cur.execute("SELECT 1")

    stmts = pool[0].statements
    assert stmts[0] == "SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE"
    assert stmts[-1] == "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ", (
        "a stricter isolation level must not leak into the next request, and "
        "must restore to MySQL's own default (REPEATABLE READ), not READ COMMITTED"
    )
    assert pool[0]._autocommit is True


def test_transaction_restores_isolation_even_after_an_error(pool):
    with pytest.raises(ValueError):
        with core.transaction(isolation=core.SERIALIZABLE):
            raise ValueError("boom")

    assert pool[0].statements[-1] == "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ"
    assert pool[0]._autocommit is True


def test_transaction_rejects_an_unknown_isolation_level(pool):
    with pytest.raises(ValueError, match="unsupported isolation"):
        with core.transaction(isolation="NONSENSE"):
            pass
