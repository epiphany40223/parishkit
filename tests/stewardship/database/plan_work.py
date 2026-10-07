"""Contention-proof work measures for one SQL statement (#690).

Wall-clock bounds in database tests measure shared-CPU noise on packed CI
runners. The rows an executed plan visits do not change with CPU speed, so
a bound on them catches the same regressions (a quadratic join, a scan per
row) without depending on how busy the machine is.

These measure work on Django's default connection only. Reads made on other
connections (a worker thread, a second session) are not counted: a plan
covers its one statement, and ``rows_read_by`` forces only this
connection's statistics flush, so another connection's reads appear only if
it happens to flush during the window (an addition the callers' fewest-of-
three readings discard), never reliably.
"""

import json

from django.db import connection, transaction


def analyzed_plan(statement, params=None):
    """Run ``statement`` under EXPLAIN ANALYZE and return its root plan node.

    EXPLAIN ANALYZE executes the statement, so callers pass read-only
    statements only. JIT is off, as in the report reads, so a plan's first
    execution does not pay seconds of compilation.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL jit = off")
        cursor.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + statement, params)
        return cursor.fetchone()[0][0]["Plan"]


def rows_visited(node):
    """Rows every node of an executed plan produced or discarded, all loops.

    A row a node filters out still cost a visit, and a node inside a nested
    loop runs once per outer row, so a quadratic plan's count grows with the
    square of the population while a linear plan's grows with it.

    This relies on PostgreSQL 18's EXPLAIN, which reports a node's
    ``Actual Rows`` per loop as a fractional average; multiplied back by
    ``Actual Loops`` it gives the node's total. Earlier versions round the
    per-loop average to an integer, which would undercount a node that
    returns less than one row a loop.
    """
    per_loop = (
        node.get("Actual Rows", 0)
        + node.get("Rows Removed by Filter", 0)
        + node.get("Rows Removed by Join Filter", 0)
    )
    own = per_loop * node.get("Actual Loops", 1)
    return own + sum(rows_visited(child) for child in node.get("Plans", []))


def assert_linear_plan(statement, params, population, *, per_row):
    """Assert one execution visits at most ``per_row`` rows per population row.

    ``per_row`` is a generous constant (the statement's fixed number of
    passes over the population, with headroom); a quadratic plan exceeds it
    by a factor of the population. Returns the count for evidence.
    """
    plan = analyzed_plan(statement, params)
    visited = rows_visited(plan)
    assert visited <= per_row * population, json.dumps(
        {"rows_visited": visited, "population": population, "plan": plan}
    )
    return visited


def _rows_read_so_far():
    """Rows and index entries this database has served, flushed and current.

    A backend flushes its pending table counters to shared statistics only
    when it goes idle, at most once a second unless forced; forcing the
    next flush and then reading in a new statement makes every earlier read
    of this connection visible. Requires autocommit, as each statement must
    end its own transaction for the flush to run. Index entries count as
    well as heap rows, so an index-only scan of a whole index is seen too.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_force_next_flush()")
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            "SELECT (SELECT coalesce(sum(seq_tup_read), 0) FROM pg_stat_user_tables)"
            " + (SELECT coalesce(sum(idx_tup_read), 0) FROM pg_stat_user_indexes)"
        )
        return int(cursor.fetchone()[0])


def rows_read_by(operation):
    """Rows ``operation`` read from all user tables, across its transactions.

    Sequential-scan rows plus index entries, counted by the server, so reads
    inside private SQL functions and triggers are included. Only this
    connection may use the database meanwhile.
    """
    if connection.in_atomic_block:
        raise AssertionError("Row counts need autocommit to flush statistics.")
    before = _rows_read_so_far()
    operation()
    return _rows_read_so_far() - before


def analyze_all():
    """Refresh planner statistics for every table, as autovacuum would.

    Right after a bulk load the planner may still see the tables as tiny and
    pick a scan it would not use in a deployment, until autovacuum happens
    to analyze them. Row counts taken after this do not depend on that race.
    """
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE")
