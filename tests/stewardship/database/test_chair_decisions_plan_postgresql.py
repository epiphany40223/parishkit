"""Chair decisions read the current chairs once, with unchanged results (#147).

Frozen file 0026 replaced the body of ``stewardship_chair_decisions_v1``: it
used to read ``stewardship_current_chair`` in a correlated EXISTS that the
planner copied into both uses of the CASE result, so every call planned the
view's ten-relation join twice (100-200 ms under the work-order lock). These
tests compare the new body with the old one, kept here verbatim, on data that
produces every decision reason, and bound the new body's planning time.
"""

import json
import re
from time import perf_counter
from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

from .test_chair_reconciliation_postgresql import configured
from .test_source_families_postgresql import source_singletons  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

# The body before frozen file 0026, verbatim, as a temporary function.
BEFORE = """
CREATE FUNCTION pg_temp.chair_decisions_before(configuration uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'assignment_record_id',record_id::text,
        'active',reason='current_chair','reason',reason) ORDER BY record_id),
        '[]'::jsonb)
    FROM (
        SELECT assignment.record_id, CASE
            WHEN evidence.id IS NULL THEN 'missing_binding'
            WHEN evidence.organization_id::text IS DISTINCT FROM
                integration.settings->>'organization_id' THEN 'organization_changed'
            WHEN EXISTS (SELECT 1 FROM stewardship_ministry_activity activity
                WHERE activity.configuration_id=configuration
                  AND activity.organization_id=evidence.organization_id
                  AND activity.ministry_duid=assignment.ministry_duid
                  AND NOT activity.active) THEN 'ministry_inactive'
            WHEN EXISTS (SELECT 1 FROM stewardship_current_chair chair
                WHERE chair.organization_id=evidence.organization_id
                  AND chair.member_duid=evidence.member_duid
                  AND chair.ministry_duid=assignment.ministry_duid
                  AND chair.email=assignment.email) THEN 'current_chair'
            ELSE 'relationship_missing' END AS reason
        FROM public.stewardship_ministry_assignment assignment
        LEFT JOIN public.stewardship_chair_seed_evidence evidence
            ON evidence.assignment_record_id=assignment.record_id
        LEFT JOIN public.stewardship_applied_integration integration
            ON integration.configuration_id=configuration
            AND integration.kind='parishsoft'
        WHERE assignment.configuration_id=configuration
            AND assignment.source='chair-seed'
    ) decisions;
$$;
"""


def seed_every_reason(configuration_id, snapshot_id):
    """Add chair-seed assignments that decide each non-current reason.

    The fixture's own seed (Member 3 chairs Ministry 4 as valid@example.org)
    is the current chair. These rows are written by the schema owner with
    triggers off, because only their content matters to the function: one
    assignment has no evidence, one has evidence from another organization,
    one names a Ministry the configuration marks inactive, and one names an
    address the source does not show for that chair.
    """
    rows = []

    def assignment(email, ministry, *, evidence=None, inactive=False):
        record = uuid4()
        rows.append(
            (
                "INSERT INTO stewardship_ministry_assignment (id, actor_id, "
                "correlation_id, record_id, email, ministry_duid, source, "
                "operation_id, configuration_id) VALUES "
                "(%s, NULL, %s, %s, %s, %s, 'chair-seed', %s, %s)",
                [uuid4(), uuid4(), record, email, ministry, uuid4(), configuration_id],
            )
        )
        if evidence is not None:
            rows.append(
                (
                    "INSERT INTO stewardship_chair_seed_evidence (id, actor_id, "
                    "correlation_id, assignment_record_id, organization_id, "
                    "member_duid, roster_keys, assignment_id, snapshot_id) VALUES "
                    "(%s, %s, %s, %s, %s, 3, '[]', %s, %s)",
                    [uuid4(), uuid4(), uuid4(), record, evidence, uuid4(), snapshot_id],
                )
            )
        if inactive:
            rows.append(
                (
                    "INSERT INTO stewardship_ministry_activity (id, actor_id, "
                    "correlation_id, record_id, organization_id, ministry_duid, "
                    "active, configuration_id) VALUES "
                    "(%s, NULL, %s, %s, 12345, %s, false, %s)",
                    [uuid4(), uuid4(), uuid4(), ministry, configuration_id],
                )
            )

    assignment("missing@example.org", 4)
    assignment("moved@example.org", 4, evidence=99999)
    assignment("valid@example.org", 7, evidence=12345, inactive=True)
    assignment("other@example.org", 4, evidence=12345)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        for sql, params in rows:
            cursor.execute(sql, params)


def decisions(function, configuration_id):
    """One function's decisions for a configuration, as Python values."""
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {function}(%s)", [configuration_id])
        value = cursor.fetchone()[0]
    return json.loads(value) if isinstance(value, str) else value


def test_new_body_decides_every_reason_as_the_old_one(tmp_path):
    """Both bodies give the same decisions, in the same order, for every reason."""
    configured(tmp_path)
    runtime = SystemConfiguration.objects.get()
    with connection.cursor() as cursor:
        cursor.execute("SELECT snapshot_id FROM stewardship_source_current")
        snapshot_id = cursor.fetchone()[0]
    seed_every_reason(runtime.active_configuration_id, snapshot_id)
    with connection.cursor() as cursor:
        cursor.execute(BEFORE)
    after = decisions("stewardship_chair_decisions_v1", runtime.active_configuration_id)
    before = decisions(
        "pg_temp.chair_decisions_before", runtime.active_configuration_id
    )
    assert after == before
    assert sorted(row["reason"] for row in after) == sorted(
        [
            "current_chair",
            "missing_binding",
            "organization_changed",
            "ministry_inactive",
            "relationship_missing",
        ]
    )
    assert [row["active"] for row in after if row["reason"] == "current_chair"] == [
        True
    ]
    # A configuration with no seeded assignments decides nothing, either way.
    other = uuid4()
    assert decisions("stewardship_chair_decisions_v1", other) == []
    assert decisions("pg_temp.chair_decisions_before", other) == []


def planning_ms(configuration_id, function="stewardship_chair_decisions_v1"):
    """A function body's planning time, best of three, in milliseconds.

    A SQL function's body is planned when it is called, so the body is read
    from the catalog and planned directly with its argument inlined.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT prosrc FROM pg_proc WHERE oid=%s::regproc", [function])
        body = cursor.fetchone()[0]
        inline = re.sub(
            r"\bconfiguration\b(?!_id)", f"'{configuration_id}'::uuid", body
        )
        times = []
        for _ in range(3):
            cursor.execute("EXPLAIN (SUMMARY ON) " + inline)
            line = next(
                row[0] for row in cursor.fetchall() if "Planning Time" in row[0]
            )
            times.append(float(re.search(r"([0-9.]+) ms", line)[1]))
    return min(times)


def test_new_body_plans_quickly(tmp_path):
    """The body plans in under half the old body's time, and under 60 ms.

    Both bodies are planned on the same data in the same session, so the
    comparison holds on a slow CI host; the absolute bound is loose (the new
    body plans in about 15 ms locally, the old one in 60-200 ms). A return
    to the duplicated subplan fails both.
    """
    configured(tmp_path)
    runtime = SystemConfiguration.objects.get()
    with connection.cursor() as cursor:
        cursor.execute(BEFORE)
    after = planning_ms(runtime.active_configuration_id)
    before = planning_ms(
        runtime.active_configuration_id, "pg_temp.chair_decisions_before"
    )
    assert after < before / 2 and after < 60, (after, before)
    started = perf_counter()
    decisions("stewardship_chair_decisions_v1", runtime.active_configuration_id)
    assert perf_counter() - started < 1
