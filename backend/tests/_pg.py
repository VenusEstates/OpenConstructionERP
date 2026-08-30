"""PostgreSQL test-isolation helpers.

The backend runs only on PostgreSQL, so the test suite does too. ``conftest``
provisions a cluster for the session (an embedded PostgreSQL 16 when no
``DATABASE_URL`` is set, otherwise the operator/CI-supplied instance). This
module hands out isolated, throwaway databases on that cluster for the unit
fixtures that historically built their own ``create_async_engine(":memory:")``
SQLite engine.

Isolation is fast because the full schema is materialised into a template
database exactly once per session; each fixture then clones it with
``CREATE DATABASE ... TEMPLATE`` (a file copy, no ``create_all`` round-trip)
and drops the clone on teardown.

Usage (drop-in for the old in-memory SQLite fixture)::

    from tests._pg import isolated_engine

    @pytest_asyncio.fixture
    async def session():
        async with isolated_engine() as engine:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as s:
                yield s
"""

from __future__ import annotations

import contextlib
import importlib
import os
import pkgutil
import uuid
from collections.abc import AsyncIterator

import psycopg2
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

_TEMPLATE_DB = "oe_test_template"
_template_ready = False

# Dedicated database for the fast, transaction-isolated unit/module fixtures.
# Built once with the full schema and then kept pristine: every
# ``transactional_session`` runs inside an outer transaction that is rolled
# back on teardown, so the database always starts each test empty.
_UNIT_DB = "oe_test_unit"
_unit_ready = False
_shared_engine: AsyncEngine | None = None


def _sync_url_for(database: str) -> str:
    """libpq URL for ``database`` on the session cluster (sync, psycopg2)."""
    base = make_url(os.environ["DATABASE_SYNC_URL"])
    return base.set(drivername="postgresql", database=database).render_as_string(hide_password=False)


def _async_url_for(database: str) -> str:
    """asyncpg URL for ``database`` on the session cluster."""
    base = make_url(os.environ["DATABASE_URL"])
    return base.set(drivername="postgresql+asyncpg", database=database).render_as_string(hide_password=False)


def _maintenance_db() -> str:
    """The cluster's default database, used to issue CREATE/DROP DATABASE."""
    return make_url(os.environ["DATABASE_SYNC_URL"]).database or "postgres"


def _connect_admin():
    """Autocommit connection to the maintenance database (for CREATE/DROP)."""
    conn = psycopg2.connect(_sync_url_for(_maintenance_db()))
    conn.autocommit = True
    # A whole-suite run stalled on `CREATE DATABASE ... TEMPLATE` here until the
    # per-test timeout killed the process. The cause was NOT isolated - that run
    # had a second pytest session and a second cluster live on the same machine,
    # and a 624-table template copy is slow under that on its own - so this is a
    # bound, not a diagnosis. If the stall is a file copy rather than a lock
    # wait, `lock_timeout` will not fire and the bound costs nothing.
    conn.cursor().execute(f"SET lock_timeout = '{LOCK_TIMEOUT_S}s'")
    return conn


def _terminate_backends(cur, db_name: str) -> None:
    cur.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (db_name,),
    )


def _import_all_models() -> None:
    """Import every module's ORM models so ``Base.metadata`` is complete.

    Mirrors the dynamic model discovery the app runs at startup so the
    template database carries the full schema (every module table plus the
    cross-cutting audit / translation-cache tables).
    """
    import app.core.audit  # noqa: F401
    import app.core.audit_log  # noqa: F401
    import app.core.translation.cache  # noqa: F401  (registers oe_translation_cache)
    import app.modules as _modules_pkg

    for mod in pkgutil.iter_modules(_modules_pkg.__path__):
        if not mod.ispkg:
            continue
        name = f"app.modules.{mod.name}.models"
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            # A module without a models.py is fine; re-raise a genuinely
            # different missing import.
            if exc.name != name:
                raise


def ensure_template() -> None:
    """Build the schema-loaded template database once per session."""
    global _template_ready
    if _template_ready:
        return

    conn = _connect_admin()
    try:
        cur = conn.cursor()
        # Drop any stale template (a reused external cluster) so the schema is
        # always current, then create a fresh one.
        _terminate_backends(cur, _TEMPLATE_DB)
        cur.execute(f'DROP DATABASE IF EXISTS "{_TEMPLATE_DB}"')
        cur.execute(f'CREATE DATABASE "{_TEMPLATE_DB}"')
        cur.close()
    finally:
        conn.close()

    _import_all_models()
    from app.database import Base

    sync_engine = create_engine(_sync_url_for(_TEMPLATE_DB))
    try:
        Base.metadata.create_all(sync_engine)
    finally:
        sync_engine.dispose()

    _template_ready = True


def _create_throwaway_db() -> str:
    """Clone the session template into a fresh throwaway database and return its name."""
    ensure_template()
    db_name = f"oe_test_{uuid.uuid4().hex[:16]}"
    conn = _connect_admin()
    try:
        conn.cursor().execute(f'CREATE DATABASE "{db_name}" TEMPLATE "{_TEMPLATE_DB}"')
    finally:
        conn.close()
    return db_name


def _drop_throwaway_db(db_name: str) -> None:
    """Terminate backends and drop a throwaway database created by ``_create_throwaway_db``."""
    conn = _connect_admin()
    try:
        cur = conn.cursor()
        _terminate_backends(cur, db_name)
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        cur.close()
    finally:
        conn.close()


@contextlib.asynccontextmanager
async def isolated_engine() -> AsyncIterator[AsyncEngine]:
    """Yield an async engine bound to a throwaway, schema-loaded database.

    The database is cloned from the session template (fast, no ``create_all``)
    and dropped when the context exits.
    """
    db_name = _create_throwaway_db()
    engine = create_async_engine(_async_url_for(db_name), future=True)
    try:
        yield engine
    finally:
        await engine.dispose()
        _drop_throwaway_db(db_name)


@contextlib.contextmanager
def isolated_database_url():
    """Yield the asyncpg URL of a throwaway, schema-loaded database (sync context).

    Unlike :func:`isolated_engine`, this hands out only the connection URL and
    does NOT open an async engine. It is the right primitive when several
    independent event loops (e.g. one per worker thread) each need to build
    their OWN engine bound to their OWN loop against the SAME database. Sharing
    a single async engine across foreign loops deadlocks: SQLAlchemy's pool and
    its first-connect ``asyncio.Lock`` bind to whichever loop touches them
    first, so the other loops hang forever on a cross-loop future.

    The database is cloned from the session template (fast, no ``create_all``)
    and dropped when the context exits.
    """
    db_name = _create_throwaway_db()
    try:
        yield _async_url_for(db_name)
    finally:
        _drop_throwaway_db(db_name)


def _ensure_unit_db() -> None:
    """Create the dedicated unit-test database with the full schema, once."""
    global _unit_ready
    if _unit_ready:
        return
    conn = _connect_admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_UNIT_DB,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{_UNIT_DB}"')
        cur.close()
    finally:
        conn.close()

    _import_all_models()
    from app.database import Base

    sync_engine = create_engine(_sync_url_for(_UNIT_DB))
    try:
        Base.metadata.create_all(sync_engine)
    finally:
        sync_engine.dispose()

    _unit_ready = True


def _get_shared_engine() -> AsyncEngine:
    global _shared_engine
    if _shared_engine is None:
        _ensure_unit_db()
        # NullPool: pytest-asyncio runs each test in a fresh event loop, and
        # asyncpg connections are loop-bound. Pooling would hand a connection
        # opened on one test's loop to another, raising "attached to a
        # different loop". NullPool opens a fresh connection per ``connect()``
        # (cheap against the local embedded cluster) so each binds to the
        # current loop.
        _shared_engine = create_async_engine(_async_url_for(_UNIT_DB), future=True, poolclass=NullPool)
    return _shared_engine


@contextlib.asynccontextmanager
async def transactional_session(*, disable_fks: bool = False) -> AsyncIterator[AsyncSession]:
    """Yield a session wrapped in a transaction that is rolled back on teardown.

    This is the fast isolation primitive for the unit/module suites: the
    schema-loaded ``oe_test_unit`` database is built once for the session, and
    each call opens a connection, begins an outer transaction and binds a
    session with ``join_transaction_mode="create_savepoint"``. The session's
    own ``commit()`` calls become savepoint releases; the outer rollback at
    teardown undoes everything, so no per-test ``CREATE DATABASE`` is needed
    and the database stays empty between tests.

    Use this for fixtures that yield a single :class:`AsyncSession` (including
    client tests that override the DB dependency to hand the app this same
    session). For the rarer fixtures that need a real engine with
    cross-connection commits (the app opening its own sessions from an engine),
    use :func:`isolated_engine` instead.

    Args:
        disable_fks: When true, set ``session_replication_role = replica`` on
            the connection so foreign-key triggers do not fire. This is the
            PostgreSQL equivalent of the old ``PRAGMA foreign_keys=OFF`` some
            suites used to insert rows without satisfying cross-module FKs.
            Requires a superuser/replication role (the embedded cluster and the
            CI service both qualify).
    """
    engine = _get_shared_engine()
    conn = await engine.connect()
    trans = await conn.begin()
    if disable_fks:
        await conn.exec_driver_sql("SET session_replication_role = replica")
    factory = async_sessionmaker(
        bind=conn,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()


def schema_inspection_engine():
    """Return a SYNC engine bound to the schema-loaded unit database.

    For the handful of tests that introspect the schema itself (indexes,
    columns, constraints) via :func:`sqlalchemy.inspect` rather than running
    queries against rows. The unit database is built once per session with the
    full schema (every model's ``Index(...)`` / column declarations applied via
    ``create_all``), so inspecting it is equivalent to the old "build a throwaway
    engine, ``create_all``, inspect" pattern but without a per-test round-trip.

    The caller owns the returned engine and must ``dispose()`` it.
    """
    _ensure_unit_db()
    return create_engine(_sync_url_for(_UNIT_DB))


# ── Per-module tables on the SHARED database ───────────────────────────────
# The helpers above hand out a private database, which is the better answer
# whenever a test can take one. A few modules cannot: their code under test
# spawns its own sessions from the global ``async_session_factory``, so the
# fixture has to seed the very database that factory is bound to.
#
# Those modules reached for ``Base.metadata.drop_all`` + ``create_all`` in a
# per-test fixture to get a clean slate, and at this size that is a trap twice
# over.
#
# It is enormous. ``Base.metadata`` holds whatever every module imported so far
# registered - 624 tables on a full run - so the cost is set by the rest of the
# shard rather than by the test doing the work. One rebuild was measured
# holding 19529 locks in a single transaction and taking 78 seconds, once per
# test. That is what exhausts the server's shared lock table (asyncpg
# ``OutOfMemory``, "You might need to increase max_locks_per_transaction"), and
# it is why the casualties moved between nightly runs: they are whichever
# schema-rebuilding tests happen to share a shard, never a fixed list of names.
#
# It is also fragile in a way that costs hours. DROP TABLE needs ACCESS
# EXCLUSIVE, which conflicts with the ACCESS SHARE an ordinary reader holds, so
# a single connection left ``idle in transaction`` by a failing test blocks the
# next test's drop forever - no statement timeout covers a lock wait. On the
# nightly cross-OS run that wedged the job at 9% of the suite until the
# per-test timeout killed the process 900 seconds later.
#
# So build the module's own tables once and delete rows between tests. DELETE
# needs only ROW EXCLUSIVE, which does not conflict with a stray reader, so a
# leaked connection can no longer stop the suite.


# Seconds any of this module's operations may wait for a lock before giving up:
# the per-test clear, the per-module `create_all`, and the admin connection that
# issues CREATE/DROP DATABASE. DELETE takes ROW
# EXCLUSIVE and so does not queue behind a stray reader, but it can still wait
# on the ROW locks of a transaction that WROTE the rows we are deleting - and a
# leaked transaction is exactly the shape that does. ``lock_timeout`` covers
# that too: PostgreSQL applies it while acquiring a lock on "a table, index,
# row, or other database object". The number is a budget, not a tuning knob.
# It has to be long enough that a slow-but-healthy CI machine never trips it,
# and short enough that a genuine wedge is a named failure inside one test
# rather than a job that runs to the 900-second per-test timeout and dies with
# no name attached, which is what the nightly did. Thirty seconds is two orders
# of magnitude above the observed healthy clear (0.38-1.63s) and one below the
# timeout that used to kill the process.
LOCK_TIMEOUT_S = 30


def tables_for(*models) -> list:
    """The models' tables plus every table they reference, in creation order."""
    from app.database import Base

    wanted: set[str] = set()
    queue = [model.__table__ for model in models]
    while queue:
        table = queue.pop()
        if table.key in wanted:
            continue
        wanted.add(table.key)
        queue.extend(fk.column.table for fk in table.foreign_keys)
    return [t for t in Base.metadata.sorted_tables if t.key in wanted]


async def create_module_tables(*models) -> None:
    """Create the tables ``models`` need, foreign-key closure included.

    The closure is wider than the module's own tables because ``create_all``
    has to be able to resolve every foreign key it emits. What gets *emptied*
    between tests is deliberately narrower - see :func:`clear_module_tables`.
    """
    from app.database import Base, engine

    async with engine.begin() as conn:
        # DDL is the half that can actually queue: CREATE TABLE takes ACCESS
        # EXCLUSIVE, so a connection left open by an earlier test blocks it and
        # no statement timeout covers a lock wait. The clear below is DELETE and
        # cannot hang this way; this is the call that needs the bound.
        await conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_S}s'")
        await conn.run_sync(Base.metadata.create_all, tables=tables_for(*models))


async def clear_module_tables(models) -> None:
    """Empty these models' own tables between tests, children first.

    Pass the module's OWN tables, not the closure. Shared parents like users
    and projects are deliberately left alone: other modules' tables reference
    them, so emptying them would either break a foreign key or force a CASCADE
    that reaches into tables this module knows nothing about. Nothing here
    needs them empty - these tests mint a fresh user and project per test and
    scope every assertion by an id they just created.

    DELETE is the point of the exercise. It takes ROW EXCLUSIVE, which does not
    conflict with the ACCESS SHARE an ordinary reader holds, so a connection
    left ``idle in transaction`` by a failing test cannot block it. DROP TABLE
    and TRUNCATE both take ACCESS EXCLUSIVE and would block on exactly that.
    ``lock_timeout`` covers what DELETE alone does not - see
    :data:`LOCK_TIMEOUT_S`. It is set on the connection doing the clear,
    which is the one that can block; setting it on the test's own session would
    read as working right up to the first time it was needed.

    Order is children first, so a foreign key never refuses the delete.
    """
    from app.database import Base, engine

    own = {model.__table__.key for model in models}
    ordered = [t for t in Base.metadata.sorted_tables if t.key in own]
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_S}s'")
        for table in reversed(ordered):
            await conn.execute(table.delete())
