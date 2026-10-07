"""Task runs are found by type through an index, not a table scan (#641).

The operational and security producers look for notices or events no Task owns
yet on every scheduler loop, with an anti-join on Tasks of one type in any
state. Before ``task_type_request`` no index led with ``task_type``, so each
loop read the whole table (#629). The plans are checked on temporary copies of
the tables (temporary tables and views are searched before ``public``, and the
copies keep the real indexes), filled with tens of thousands of settled Tasks
of other types, as autovacuum would leave them: vacuumed and analyzed.
Generated identifiers are derived from a fixed name and row number, so runs
repeat.
"""

import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.jobs import operational_owner, security_owner
from parishkit.stewardship.jobs.models import TaskRun

TABLES = (
    "stewardship_task_run",
    "stewardship_ops_notice",
    "stewardship_policy_security_event",
)
NOISE = 30_000
NOTICES = 1_000
# Settled Tasks of the types that dominate a real table, then a few others.
NOISE_TYPE = """CASE
    WHEN n%%100<55 THEN 'outbox_delivery'
    WHEN n%%100<70 THEN 'source_refresh'
    WHEN n%%100<80 THEN 'schedule_occurrence'
    WHEN n%%100<90 THEN 'family_mail_prepare'
    ELSE 'report_facts' END"""
TASK_INSERT = (
    "INSERT INTO pg_temp.stewardship_task_run (id,correlation_id,version,"
    "task_type,idempotency_key,retry_sequence,domain_request_id,state,action,"
    "attempt,fence,root_id) SELECT {id},{id},3,{type},{key},0,{request},"
    "'succeeded','complete',1,1,{id} FROM generate_series(1,%s) n"
)


def fill(cursor):
    """Copy the tables, then add noise Tasks and owned notices and events.

    Every notice already has its ``operational_prepare`` and
    ``operational_slack`` Task, and every security event (one per notice
    identifier) its ``security_prepare`` Task: the steady state in which the
    producers' scans find nothing and, without the index, read every Task.
    The notifiable view is copied too, because the real one is bound to the
    real event table.
    """
    for table in TABLES:
        cursor.execute(f"CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL)")
    cursor.execute(
        "CREATE TEMP VIEW stewardship_security_notifiable AS"
        " SELECT id, created_at, jsonb_array_length(recipients) AS recipient_count"
        " FROM pg_temp.stewardship_policy_security_event"
    )
    cursor.execute(
        "INSERT INTO pg_temp.stewardship_policy_security_event (id,created_at,"
        "correlation_id,rule_record_id,target,kind,before_roles,after_roles,"
        "recipients,activation_id) SELECT md5('pk641-n'||n)::uuid,"
        "statement_timestamp()-make_interval(mins=>n),md5('pk641-n'||n)::uuid,"
        "md5('pk641-e'||n)::uuid,'admin@example.org','role_changed','[]','[]',"
        "'[\"admin@example.org\"]',md5('pk641-a')::uuid"
        " FROM generate_series(1,%s) n",
        [NOTICES],
    )
    cursor.execute(
        "INSERT INTO pg_temp.stewardship_ops_notice (id,created_at,"
        "correlation_id,incident_id,incident_version,phase,level,first_seen,"
        "observed_at,occurrences) SELECT md5('pk641-n'||n)::uuid,"
        "statement_timestamp()-make_interval(mins=>n),md5('pk641-n'||n)::uuid,"
        "md5('pk641-i'||n)::uuid,1,'opened','CRITICAL',statement_timestamp(),"
        "statement_timestamp(),1 FROM generate_series(1,%s) n",
        [NOTICES],
    )
    cursor.execute(
        TASK_INSERT.format(
            id="md5('pk641-t'||n)::uuid",
            type=NOISE_TYPE,
            key="NULL",
            request="md5('pk641-r'||n)::uuid",
        ),
        [NOISE],
    )
    for kind in ("operational_prepare", "operational_slack", "security_prepare"):
        cursor.execute(
            TASK_INSERT.format(
                id=f"md5('pk641-{kind}'||n)::uuid",
                type=f"'{kind}'",
                key="md5('pk641-n'||n)",
                request="md5('pk641-n'||n)::uuid",
            ),
            [NOTICES],
        )
    for table in TABLES:
        cursor.execute(f"VACUUM ANALYZE pg_temp.{table}")


def copied_index(cursor):
    """The copy's name for ``task_type_request``, found by its definition.

    ``LIKE ... INCLUDING ALL`` copies each index under a generated name.
    """
    cursor.execute(
        "SELECT indexname FROM pg_indexes"
        " WHERE schemaname=pg_my_temp_schema()::regnamespace::text"
        " AND tablename='stewardship_task_run'"
        " AND indexdef LIKE '%%USING btree (task_type, domain_request_id)'"
    )
    (name,) = cursor.fetchall()
    return name[0]


@pytest.fixture
def copies():
    """The filled temporary copies and the copied index's name.

    Dropped afterwards; the tests run in autocommit because VACUUM needs it.
    """
    with connection.cursor() as cursor:
        try:
            fill(cursor)
            yield cursor, copied_index(cursor)
        finally:
            cursor.execute(
                "DROP VIEW IF EXISTS pg_temp.stewardship_security_notifiable"
            )
            for table in TABLES:
                cursor.execute(f"DROP TABLE IF EXISTS pg_temp.{table}")


def task_scans(cursor, statement, params):
    """Run EXPLAIN ANALYZE; return each scan of the Task table and the plan.

    Each scan is (node type, index name or None, rows read from the heap or
    index, rows the scan discarded).
    """
    cursor.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + statement, params)
    plan = cursor.fetchone()[0][0]["Plan"]
    found = []

    def walk(node):
        """Collect the Task scans anywhere in the plan, sub-plans included."""
        if node.get("Relation Name") == "stewardship_task_run":
            found.append(
                (
                    node["Node Type"],
                    node.get("Index Name"),
                    node.get("Actual Rows", 0),
                    node.get("Rows Removed by Filter", 0),
                )
            )
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    return found, plan


def producer_scans(cursor, pending):
    """Run a producer's real ``_pending(25)``; plan the Task query it sent."""
    with CaptureQueriesContext(connection) as captured:
        assert pending(25) == ()
    (statement,) = [
        query["sql"]
        for query in captured.captured_queries
        if "stewardship_task_run" in query["sql"]
    ]
    return task_scans(cursor, statement.replace("%", "%%"), [])


@pytest.mark.django_db(transaction=True)
def test_unowned_notice_lookup_reads_one_task_type_through_the_index(copies):
    """The real producer query reads only its own type's index entries."""
    cursor, index = copies
    scans, plan = producer_scans(cursor, operational_owner._pending)
    # One index-only scan of the operational_prepare entries: no Task of
    # another type is read, and no heap page is visited.
    assert scans == [("Index Only Scan", index, NOTICES, 0)], json.dumps(plan)


@pytest.mark.django_db(transaction=True)
def test_unowned_security_event_lookup_uses_the_index(copies):
    """The security producer's NOT EXISTS reads Tasks only through the index.

    The planner may probe once per event or read the type's entries once; in
    either shape it uses only the index and discards no Task of another type.
    """
    cursor, index = copies
    scans, plan = producer_scans(cursor, security_owner._pending)
    assert scans, json.dumps(plan)
    assert all(
        (kind, name, removed) == ("Index Only Scan", index, 0)
        for kind, name, _, removed in scans
    ), json.dumps(plan)


@pytest.mark.django_db(transaction=True)
def test_unowned_notice_is_still_found(copies):
    """The index changes only how Tasks are read: a new notice is pending."""
    cursor, _ = copies
    cursor.execute(
        "INSERT INTO pg_temp.stewardship_ops_notice (id,created_at,"
        "correlation_id,incident_id,incident_version,phase,level,first_seen,"
        "observed_at,occurrences) SELECT md5('pk641-new')::uuid,"
        "'2000-01-01T00:00:00Z',md5('pk641-new')::uuid,md5('pk641-i0')::uuid,"
        "1,'opened','CRITICAL',statement_timestamp(),statement_timestamp(),1"
    )
    cursor.execute("SELECT md5('pk641-new')::uuid")
    (new,) = cursor.fetchone()
    assert operational_owner._pending(25) == (new,)


@pytest.mark.django_db(transaction=True)
def test_type_and_request_check_is_one_index_probe(copies):
    """The cleanup producers' and SQL guards' (type, request) check reads one entry."""
    cursor, index = copies
    cursor.execute("SELECT md5('pk641-n7')::uuid")
    (notice,) = cursor.fetchone()
    query = TaskRun.objects.filter(
        task_type="operational_slack", domain_request_id=notice
    ).values("pk")[:1]
    statement, params = query.query.sql_with_params()
    scans, plan = task_scans(cursor, statement, params)
    assert scans == [("Index Scan", index, 1, 0)], json.dumps(plan)
