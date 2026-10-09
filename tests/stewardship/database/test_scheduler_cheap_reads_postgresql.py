"""A recovery sweep's read before the lock is never narrower than its sweep (#715).

Each of the four recovery sweeps first checks, without the work-order lock,
whether any row is in RECOVERABLE_STATES, and skips the locked sweep when
none is. That is safe only if the locked sweep (RECOVERABLE) can never
select a row the read misses. These tests try every combination the sweep
looks at: each row state (every literal in the table's state checks, plus
the sweeps' own), the task's state and lease, a past or future deadline,
and a live or dead scope, and assert that whenever the locked query finds
the row, the read before the lock finds it too.

The rows are synthetic: inside one rolled-back transaction the table's
check constraints are dropped, triggers are off (replica mode) and the
scope function returns the case's answer, so any combination can be
written. Nothing outlives the test's transaction.
"""

import re
from itertools import product
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts import (
    campaign_mail_delivery,
    setup_mail,
    setup_notifications,
)
from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest
from parishkit.stewardship.accounts.setup_delivery_models import SetupMailDelivery
from parishkit.stewardship.accounts.setup_notification_models import (
    SetupSlackDelivery,
)
from parishkit.stewardship.jobs import family_mail_test_tasks
from parishkit.stewardship.jobs.family_mail_models import FamilyMailTest

pytestmark = pytest.mark.django_db

SWEEPS = {
    "setup_mail": (
        setup_mail,
        SetupMailDelivery,
        "stewardship_setup_mail_live_v1",
    ),
    "setup_slack": (
        setup_notifications,
        SetupSlackDelivery,
        "stewardship_setup_slack_live_v1",
    ),
    "campaign_mail_test": (
        campaign_mail_delivery,
        CampaignMailTest,
        "stewardship_campaign_mail_live_v1",
    ),
    "family_mail_test": (
        family_mail_test_tasks,
        FamilyMailTest,
        "stewardship_family_test_scope_v1",
    ),
}
# Task rows: (state, lease in the future?).
TASKS = [("running", True), ("running", False), ("failed", False)]
TASKS += [("cancelled", False), ("succeeded", False), ("queued", False)]
# Fill values for NOT NULL columns without defaults, by SQL type.
FILL = {
    "uuid": "gen_random_uuid()",
    "integer": "1",
    "bigint": "1",
    "smallint": "1",
    "numeric": "1",
    "boolean": "false",
    "jsonb": "'{}'::jsonb",
    "json": "'{}'::json",
    "timestamp with time zone": "clock_timestamp()",
    "date": "current_date",
    "ARRAY": "'{}'",
}


def columns(cursor, table):
    """The table's columns: {name: (type, needs a value)}."""
    cursor.execute(
        "SELECT column_name, data_type, is_nullable='NO' AND column_default IS NULL "
        "FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name=%s",
        [table],
    )
    return {name: (kind, needed) for name, kind, needed in cursor.fetchall()}


def insert(cursor, table, values):
    """Insert one row: ``values`` (SQL expressions) plus fillers for the rest."""
    shape = columns(cursor, table)
    row = {
        name: FILL.get(kind, "'x'")
        for name, (kind, needed) in shape.items()
        if needed and name not in values
    }
    row |= values
    names = ",".join(f'"{name}"' for name in row)
    cursor.execute(f"INSERT INTO {table} ({names}) VALUES ({','.join(row.values())})")


def loosen(cursor, table):
    """Drop the table's check constraints; return the literals its state checks name.

    Only inside the test's transaction, which is rolled back.
    """
    cursor.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid=%s::regclass AND contype='c'",
        [table],
    )
    states = set()
    for name, definition in cursor.fetchall():
        if "state" in definition:
            states.update(re.findall(r"'([a-z_]+)'", definition))
        cursor.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"')
    return states


def scope(cursor, function, live):
    """Make the sweep's scope function answer ``live`` for every row."""
    cursor.execute(
        "SELECT pg_get_function_arguments(p.oid) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public' AND p.proname=%s",
        [function],
    )
    (arguments,) = cursor.fetchone()
    cursor.execute(
        f"CREATE OR REPLACE FUNCTION public.{function}({arguments}) "
        f"RETURNS boolean LANGUAGE sql AS $$SELECT {str(live).lower()}$$"
    )


@pytest.mark.parametrize("sweep", sorted(SWEEPS))
def test_the_read_before_the_lock_covers_every_recoverable_row(sweep):
    """Whenever RECOVERABLE selects a row, RECOVERABLE_STATES includes its state."""
    module, model, function = SWEEPS[sweep]
    table = model._meta.db_table
    found = 0
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        states = loosen(cursor, table) | set(module.RECOVERABLE_STATES)
        states |= {"queued", "submitting", "delivered", "failed", "cancelled"}
        loosen(cursor, "stewardship_task_run")
        shape = columns(cursor, table)
        for state, (task_state, leased), past, live in product(
            sorted(states), TASKS, (True, False), (True, False)
        ):
            scope(cursor, function, live)
            cursor.execute(f"DELETE FROM {table}")
            task, worker = uuid4(), uuid4()
            insert(
                cursor,
                "stewardship_task_run",
                {
                    "id": f"'{task}'",
                    "root_id": f"'{task}'",
                    "state": f"'{task_state}'",
                    "fence": "7",
                    "worker_id": f"'{worker}'",
                    "lease_expires_at": "clock_timestamp()+interval '1 hour'"
                    if leased
                    else "clock_timestamp()-interval '1 hour'",
                },
            )
            values = {"state": f"'{state}'"}
            optional = {
                "task_id": f"'{task}'",
                "run_id": f"'{task}'",
                "task_fence": "7",
                "worker_id": f"'{worker}'",
                "deadline_at": "clock_timestamp()-interval '1 hour'"
                if past
                else "clock_timestamp()+interval '1 hour'",
            }
            values |= {name: value for name, value in optional.items() if name in shape}
            insert(cursor, table, values)
            cursor.execute(
                module.RECOVERABLE, [100] if "%s" in module.RECOVERABLE else []
            )
            locked = bool(cursor.fetchall())
            cheap = model.objects.filter(state__in=module.RECOVERABLE_STATES).exists()
            assert locked <= cheap, (state, task_state, leased, past, live)
            found += locked
    # Not vacuous: some combinations are recoverable.
    assert found
