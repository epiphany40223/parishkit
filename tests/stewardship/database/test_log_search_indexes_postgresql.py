"""The System logs text search indexes and migration file 0116 (#765)."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit import log_reads
from parishkit.stewardship.audit.log_rows import LogQuery
from parishkit.stewardship.audit.log_search import indexable

pytestmark = pytest.mark.django_db(transaction=True)
FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0116_log_search_indexes.sql"
).read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
TRIGRAM = (
    "operational_value_trgm",
    "operational_event_trgm",
    "audit_context_value_trgm",
    "audit_event_type_trgm",
)
PATTERN = ("operational_event_pattern", "audit_event_type_pattern")
# The events' condition on the contexts listed once, up front (AmongSubquery).
LISTED = "Index Cond: (id = ANY ((InitPlan"
# Synthetic entries, so the planner's statistics describe logs of some size
# rather than empty tables. The guards' row triggers are skipped (replica
# role, superuser test database only); every CHECK still applies.
POPULATE = """
SET LOCAL session_replication_role = replica;
INSERT INTO stewardship_operational_log
    (id, correlation_id, level, event, schema, context)
SELECT gen_random_uuid(), gen_random_uuid(),
       (ARRAY['INFO','WARNING','ERROR'])[1 + g % 3], 'task_failed', 'failure',
       jsonb_build_object('failure', 'alert_mail', 'outcome', 'failed', 'count', g)
FROM generate_series(1, 20000) g;
INSERT INTO stewardship_audit_event (id, correlation_id, event_type)
SELECT gen_random_uuid(), gen_random_uuid(), 'dashboard_viewed'
FROM generate_series(1, 20000) g;
INSERT INTO stewardship_audit_context
    (id, correlation_id, actor_kind, schema, context, event_id)
SELECT gen_random_uuid(), e.correlation_id, 'system', 'action',
       '{"decision": "keep_role"}', e.id
FROM stewardship_audit_event e WHERE e.event_type='dashboard_viewed' LIMIT 2000;
RESET session_replication_role;
ANALYZE stewardship_operational_log, stewardship_audit_event,
    stewardship_audit_context;
"""


@pytest.fixture
def populated():
    """Some thousands of synthetic entries in both logs, rolled back after."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(POPULATE)
        yield
        transaction.set_rollback(True)


def plans(text, **filters):
    """The EXPLAIN text of the search's count and page reads of both logs.

    Sequential and plain index scans are switched off, so a bitmap scan of
    an index appears exactly when the query's conditions match what that
    index holds; with only a few rows the planner would otherwise read the
    table either way. ``filters`` are more ``LogQuery`` fields.
    """
    operational, audit = log_reads.sources(LogQuery(text=text, **filters), None)
    found = {}
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("SET LOCAL enable_indexscan = off")
        for name, rows in (("operational", operational), ("audit", audit)):
            for read, query in (
                ("count", rows.order_by()[:10001]),
                ("page", log_reads.ordered(rows, oldest=False)[:50]),
            ):
                sql, params = query.query.sql_with_params()
                cursor.execute("EXPLAIN " + sql, params)
                found[name, read] = "\n".join(row[0] for row in cursor.fetchall())
    return found


def test_the_extension_and_every_index_are_installed():
    """pg_trgm is in public and the closing check accepts the catalog."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT extnamespace::regnamespace::text FROM pg_extension "
            "WHERE extname='pg_trgm'"
        )
        assert cursor.fetchone() == ("public",)
        cursor.execute(CHECK)


@pytest.mark.parametrize(
    "statement",
    [
        "DROP INDEX public.operational_value_trgm",
        "DROP INDEX public.audit_event_type_pattern",
        # Same name, different definition (the bare expression, no UPPER).
        "DROP INDEX public.audit_event_type_trgm; CREATE INDEX audit_event_type_trgm "
        "ON public.stewardship_audit_event USING gin (event_type gin_trgm_ops)",
        "DROP EXTENSION pg_trgm CASCADE",
    ],
)
def test_the_migration_check_refuses_a_missing_or_different_object(statement):
    """The DO block raises for a missing index, a changed one or no extension."""
    with (
        pytest.raises(DatabaseError, match="Migration 0116"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_file_runs_again_harmlessly():
    """Every statement is safe to repeat, as the users swap test may."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(FROZEN)
        transaction.set_rollback(True)


def test_a_login_without_database_create_cannot_install_the_extension():
    """The migration login cannot create pg_trgm (no CREATE on the database),
    so the operator installs it first; once it is there, the migration's
    IF NOT EXISTS only notices it."""
    role = "pk_765_" + uuid4().hex[:12]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"CREATE ROLE {role} NOLOGIN")
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        cursor.execute(f"SET LOCAL ROLE {role}")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
        transaction.set_rollback(True)
    with (
        pytest.raises(
            DatabaseError, match='permission denied to create extension "pg_trgm"'
        ),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(f"CREATE ROLE {role} NOLOGIN")
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        cursor.execute("DROP EXTENSION pg_trgm CASCADE")
        cursor.execute(f"SET LOCAL ROLE {role}")
        cursor.execute(FROZEN)


def test_an_indexable_search_reads_every_arm_from_an_index(populated):
    """Every arm of the text OR is served by an index, combined in one bitmap
    scan per log, for the count and the page (#765)."""
    # "web request" explains request_completed, so the operational type arms
    # hold an equality too; "moved to a new step" names trigger prefixes, so
    # the audit type arms hold LIKE 'prefix%'. (Every synthetic operational
    # entry is a task_failed, which the "task_" prefix matches, so that
    # term would rightly read them all from one index.)
    cases = {
        "zqxjv": ("operational", "audit"),
        "keep_role": ("operational", "audit"),
        "web request": ("operational", "audit"),
        "moved to a new step": ("audit",),
    }
    for text, logs in cases.items():
        assert indexable(text)
        for (name, read), plan in plans(text).items():
            if name not in logs:
                continue
            assert "BitmapOr" in plan, (text, name, read, plan)
            assert "Seq Scan" not in plan, (text, name, read, plan)
            if name == "operational":
                wanted = ("operational_value_trgm", "operational_event_trgm")
            else:
                wanted = ("audit_context_value_trgm", "audit_event_type_trgm")
                # The matching contexts' events are listed once, up front.
                assert LISTED in plan and "hashed SubPlan" not in plan, plan
            for index in wanted:
                assert f"Bitmap Index Scan on {index}" in plan, (text, index, plan)
    for text, (name, index) in {
        "web request": ("operational", PATTERN[0]),
        "moved to a new step": ("audit", PATTERN[1]),
    }.items():
        for read in ("count", "page"):
            plan = plans(text)[name, read]
            assert f"Bitmap Index Scan on {index}" in plan, plan
    # JSON text escapes a double quote, so such a term keeps only the exact
    # detail condition (log_search): the audit contexts it lists are read
    # in full, once, and the events' OR is still one bitmap scan.
    for (name, _read), plan in plans('say "yes"').items():
        if name == "audit":
            assert LISTED in plan and "hashed SubPlan" not in plan, plan
            assert "Bitmap Index Scan on audit_event_type_trgm" in plan, plan
            assert "audit_context_value_trgm" not in plan, plan


def test_a_one_day_range_keeps_the_trigram_indexes(populated):
    """With From and Through on one day, the text condition is still read
    from the trigram indexes beside the date range, and the matching audit
    contexts are still listed once, up front (#765)."""
    today = datetime.now(UTC).date().isoformat()
    day = {"start": today, "end": today, "zone": "UTC"}
    for text in ("keep_role", "web request"):
        for (name, read), plan in plans(text, **day).items():
            assert "Seq Scan" not in plan, (text, name, read, plan)
            assert "created_at" in plan, (text, name, read, plan)
            wanted = (
                ("operational_value_trgm", "operational_event_trgm")
                if name == "operational"
                else ("audit_context_value_trgm", "audit_event_type_trgm")
            )
            assert any(f"Bitmap Index Scan on {i}" in plan for i in wanted), plan
            if name == "audit":
                assert "InitPlan" in plan, (text, read, plan)


def test_a_term_without_a_trigram_never_reads_a_trigram_index(populated):
    """A term with no three letters or digits in a row would make a trigram
    index read every one of its entries, so it compares expressions no index
    holds, and the audit detail stays a hashed EXISTS (#765)."""
    for text in ("ok", "42", "a-b", "%"):
        assert not indexable(text)
        for (name, _read), plan in plans(text).items():
            for index in TRIGRAM:
                assert index not in plan, (text, index, plan)
            if name == "audit":
                assert LISTED not in plan and "SubPlan" in plan, plan
