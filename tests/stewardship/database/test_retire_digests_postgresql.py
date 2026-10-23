"""Migration 0171 retires the digest pipelines (#1834).

The schedule definition check admits no digest kind any more (the revision
checks still do, for history), the digest write functions and triggers are
gone, and the read functions, views and guards the saved digest pages use
stay. The frozen file refuses to run while a definition is still a digest,
runs again cleanly on a converted database, and its closing DO block
refuses each way the result could differ from what it declares.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from .report_builders import DIGEST_KIND_CHECK, _definition_check, triggers_off

pytestmark = pytest.mark.django_db

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0171_retire_digests.sql"
).read_text(encoding="utf-8")
# The file's closing self-check, run on its own against chosen states.
CHECK = FROZEN[FROZEN.index("DO $check$") :]
DROPPED = (
    "stewardship_daily_digest_write_admitted_v1",
    "stewardship_daily_digest_mail_write_v1",
    "stewardship_daily_digest_mail_commit_v1",
    "stewardship_daily_digest_source_commit_v1",
    "stewardship_daily_digest_fact_commit_v1",
    "stewardship_daily_dispatch_write_v1",
    "stewardship_daily_dispatch_render_v1",
    "stewardship_daily_digest_settle_v1",
    "stewardship_daily_digest_completion_v1",
    "stewardship_daily_digest_resolved_v1",
    "stewardship_weekly_digest_write_admitted_v1",
    "stewardship_weekly_digest_mail_write_v1",
    "stewardship_weekly_mail_commit_v1",
    "stewardship_weekly_snapshot_commit_v1",
    "stewardship_weekly_dispatch_write_v1",
    "stewardship_weekly_dispatch_render_v1",
    "stewardship_weekly_digest_settle_v1",
    "stewardship_weekly_digest_completion_v1",
    "stewardship_weekly_digest_resolved_v1",
    "stewardship_weekly_manual_allocate_v1",
)
DROPPED_TRIGGERS = (
    "daily_digest_mail_complete",
    "daily_digest_delivery_complete",
    "daily_digest_late_completion",
    "daily_digest_cohort_complete",
    "daily_digest_snapshot_complete",
    "daily_digest_ready_complete",
    "weekly_mail_complete",
    "weekly_delivery_complete",
    "weekly_late_completion",
    "weekly_cohort_complete",
    "weekly_snapshot_complete",
    "weekly_manual_allocate",
)
# The logins and digest tables whose write grants the file revokes.
WRITERS = ("pk_stewardship_worker", "pk_stewardship_scheduler", "pk_stewardship_web")
DIGEST_TABLES = (
    "stewardship_daily_digest_preparation",
    "stewardship_daily_digest_snapshot",
    "stewardship_daily_digest_ready",
    "stewardship_daily_digest_recipient",
    "stewardship_weekly_digest_preparation",
    "stewardship_weekly_digest_snapshot",
    "stewardship_weekly_digest_recipient",
    "stewardship_weekly_manual_request",
)
# Make a writer login when the test database has none (rolled back with
# the test's transaction; roles are transactional in PostgreSQL).
ENSURE_LOGIN = (
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{0}') "
    "THEN CREATE ROLE {0} NOLOGIN; END IF; END $$;"
)


def scalar(sql, params=()):
    """One value from the test's own (superuser) connection."""
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def insert_definition(kind):
    """A bare schedule definition of ``kind``, with the triggers off.

    Only the table's checks run: the foreign keys and guards are triggers,
    so the definition needs no campaign or revision.
    """
    with triggers_off(), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_schedule_definition "
            "(id,created_at,updated_at,version,actor_id,correlation_id,"
            "campaign_id,kind,removed_at) "
            "VALUES (%s,now(),now(),1,%s,%s,%s,%s,now())",
            [uuid4(), uuid4(), uuid4(), uuid4(), kind],
        )


def refused(sql, message):
    """``sql`` fails with ``message``, leaving nothing behind."""
    with (
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)


@pytest.mark.parametrize("kind", ["daily_digest", "weekly_digest"])
def test_no_definition_may_be_a_digest_any_more(kind):
    """The definition check refuses a digest kind; a report is admitted."""
    with (
        pytest.raises(IntegrityError, match="schedule_definition_kind"),
        transaction.atomic(),
    ):
        insert_definition(kind)
    insert_definition("report")


def test_the_revision_checks_keep_the_digest_kinds_for_history():
    """Old revisions are immutable: their check still names both digests."""
    definition = scalar(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='schedule_revision_due_kind'"
    )
    assert "'daily_digest'" in definition and "'weekly_digest'" in definition


def test_the_write_machinery_is_gone_and_the_history_stays():
    """No dropped function or trigger is installed; the read side is."""
    assert (
        scalar("SELECT count(*) FROM pg_proc WHERE proname = ANY(%s)", [list(DROPPED)])
        == 0
    )
    assert (
        scalar(
            "SELECT count(*) FROM pg_trigger WHERE tgname = ANY(%s)",
            [list(DROPPED_TRIGGERS)],
        )
        == 0
    )
    for function in (
        "stewardship_daily_dispatch_live_v1(uuid,boolean)",
        "stewardship_weekly_dispatch_live_v1(uuid,boolean)",
        "stewardship_daily_digest_seed_v1(uuid,uuid)",
        "stewardship_weekly_digest_seed_v1(uuid,uuid)",
        "stewardship_daily_digest_preparation_guard_v1()",
        "stewardship_weekly_manual_guard_v1()",
        "stewardship_daily_digest_pin_retained_v1()",
        "stewardship_weekly_history_v1(uuid,text,uuid)",
    ):
        assert scalar("SELECT to_regprocedure(%s)", [f"public.{function}"]), function
    for table in (
        "stewardship_daily_digest_preparation",
        "stewardship_daily_digest_snapshot",
        "stewardship_daily_digest_ready",
        "stewardship_daily_digest_recipient",
        "stewardship_daily_digest_completion_ready",
        "stewardship_weekly_digest_preparation",
        "stewardship_weekly_digest_snapshot",
        "stewardship_weekly_digest_recipient",
        "stewardship_weekly_digest_completion_ready",
        "stewardship_weekly_manual_request",
    ):
        assert scalar("SELECT to_regclass(%s)", [f"public.{table}"]), table


def test_the_file_runs_again_cleanly_on_a_converted_database():
    """Nothing is left to drop, and its own closing block passes."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(FROZEN)


@pytest.mark.parametrize("kind", ["daily_digest", "weekly_digest"])
def test_the_file_refuses_while_a_definition_is_still_a_digest(kind):
    """A database the conversion has not run on is refused, naming the count."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            _definition_check(cursor, DIGEST_KIND_CHECK)
        insert_definition(kind)
        insert_definition(kind)
        refused(
            FROZEN,
            "Migration 0171: 2 schedule definitions are still Daily or Weekly digests",
        )
        transaction.set_rollback(True)


# Each state the closing block must refuse, made in a rolled-back
# transaction: the old definition check, a dropped write function or
# trigger made again, a kept body that names a dropped function, a read
# function kept for history gone, and a write grant on a digest table.
BROKEN = {
    "digest kinds": (
        "ALTER TABLE stewardship_schedule_definition "
        "DROP CONSTRAINT schedule_definition_kind; "
        "ALTER TABLE stewardship_schedule_definition "
        f"ADD CONSTRAINT schedule_definition_kind {DIGEST_KIND_CHECK};",
        "the schedule kind checks are not as declared",
    ),
    "write function": (
        "CREATE FUNCTION public.stewardship_daily_dispatch_write_v1"
        "(relation_name text,proposed jsonb,prior jsonb) RETURNS boolean "
        "LANGUAGE sql AS $$ SELECT false $$;",
        "a digest write function or trigger remains",
    ),
    "trigger": (
        "CREATE FUNCTION public.example_settle_v1() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$; "
        "CREATE TRIGGER weekly_late_completion AFTER UPDATE ON "
        "stewardship_task_run FOR EACH ROW "
        "EXECUTE FUNCTION public.example_settle_v1();",
        "a digest write function or trigger remains",
    ),
    "body": (
        "CREATE FUNCTION public.example_caller_v1() RETURNS boolean "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN "
        "public.stewardship_weekly_digest_mail_write_v1(NULL,NULL,NULL,NULL); "
        "END $$;",
        "an installed body still names a dropped digest function",
    ),
    "read function": (
        "DROP FUNCTION public.stewardship_weekly_digest_seed_v1(uuid,uuid);",
        "a digest read function, view or guard kept for history is missing",
    ),
    "write grant": (
        ENSURE_LOGIN.format("pk_stewardship_worker")
        + " GRANT INSERT ON stewardship_daily_digest_ready TO pk_stewardship_worker;",
        "a service login can still write a digest table",
    ),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_the_closing_block_refuses_each_undeclared_state(case):
    """The block raises on each broken state, and passes once it is undone."""
    change, message = BROKEN[case]
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(change)
        refused(CHECK, f"Migration 0171: {message}")
        transaction.set_rollback(True)
    with connection.cursor() as cursor:
        cursor.execute(CHECK)


# Each dropped function's identity and each dropped trigger's table, as a
# database the conversion left has them (the upgrade-parity test runs the
# file on the previous release's real definitions).
IDENTITIES = {
    "stewardship_daily_digest_write_admitted_v1": "text,jsonb,jsonb",
    "stewardship_daily_digest_mail_write_v1": "uuid,text,jsonb,jsonb",
    "stewardship_daily_dispatch_write_v1": "text,jsonb,jsonb",
    "stewardship_daily_dispatch_render_v1": "jsonb,uuid",
    "stewardship_daily_digest_completion_v1": "jsonb",
    "stewardship_daily_digest_resolved_v1": "uuid",
    "stewardship_weekly_digest_write_admitted_v1": "text,jsonb,jsonb",
    "stewardship_weekly_digest_mail_write_v1": "uuid,text,jsonb,jsonb",
    "stewardship_weekly_dispatch_write_v1": "text,jsonb,jsonb",
    "stewardship_weekly_dispatch_render_v1": "jsonb,uuid",
    "stewardship_weekly_digest_completion_v1": "jsonb",
    "stewardship_weekly_digest_resolved_v1": "uuid",
}
TRIGGER_TABLES = {
    "daily_digest_mail_complete": "stewardship_outbox_message",
    "daily_digest_delivery_complete": "stewardship_outbox_message",
    "daily_digest_late_completion": "stewardship_task_run",
    "daily_digest_cohort_complete": "stewardship_daily_digest_preparation",
    "daily_digest_snapshot_complete": "stewardship_daily_digest_snapshot",
    "daily_digest_ready_complete": "stewardship_daily_digest_ready",
    "weekly_mail_complete": "stewardship_outbox_message",
    "weekly_delivery_complete": "stewardship_outbox_message",
    "weekly_late_completion": "stewardship_task_run",
    "weekly_cohort_complete": "stewardship_weekly_digest_preparation",
    "weekly_snapshot_complete": "stewardship_weekly_digest_snapshot",
    "weekly_manual_allocate": "stewardship_weekly_manual_request",
}
# The trigger function each dropped trigger ran.
TRIGGER_FUNCTIONS = {
    "daily_digest_mail_complete": "stewardship_daily_digest_mail_commit_v1",
    "daily_digest_delivery_complete": "stewardship_daily_digest_settle_v1",
    "daily_digest_late_completion": "stewardship_daily_digest_settle_v1",
    "daily_digest_cohort_complete": "stewardship_daily_digest_settle_v1",
    "daily_digest_snapshot_complete": "stewardship_daily_digest_source_commit_v1",
    "daily_digest_ready_complete": "stewardship_daily_digest_fact_commit_v1",
    "weekly_mail_complete": "stewardship_weekly_mail_commit_v1",
    "weekly_delivery_complete": "stewardship_weekly_digest_settle_v1",
    "weekly_late_completion": "stewardship_weekly_digest_settle_v1",
    "weekly_cohort_complete": "stewardship_weekly_digest_settle_v1",
    "weekly_snapshot_complete": "stewardship_weekly_snapshot_commit_v1",
    "weekly_manual_allocate": "stewardship_weekly_manual_allocate_v1",
}


def test_the_file_drops_what_the_conversion_left():
    """Every function and trigger it names, made again, is dropped by it.

    Stand-ins with the real identities and tables prove the file's drop
    statements name them exactly (a wrong identity would leave one and the
    closing block would refuse); the plain functions and the trigger
    functions together are the whole dropped list.
    """
    assert set(IDENTITIES) | set(TRIGGER_FUNCTIONS.values()) == set(DROPPED)
    assert set(TRIGGER_TABLES) == set(TRIGGER_FUNCTIONS) == set(DROPPED_TRIGGERS)
    with transaction.atomic():
        with connection.cursor() as cursor:
            for name, arguments in IDENTITIES.items():
                cursor.execute(
                    f"CREATE FUNCTION public.{name}({arguments}) RETURNS boolean "
                    "LANGUAGE sql AS $$ SELECT false $$"
                )
            for name in set(TRIGGER_FUNCTIONS.values()):
                cursor.execute(
                    f"CREATE FUNCTION public.{name}() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$"
                )
            for trigger, table in TRIGGER_TABLES.items():
                cursor.execute(
                    f"CREATE TRIGGER {trigger} AFTER INSERT ON public.{table} "
                    f"FOR EACH ROW EXECUTE FUNCTION "
                    f"public.{TRIGGER_FUNCTIONS[trigger]}()"
                )
            cursor.execute(FROZEN)
        assert (
            scalar(
                "SELECT count(*) FROM pg_proc WHERE proname = ANY(%s)",
                [list(DROPPED)],
            )
            == 0
        )
        assert (
            scalar(
                "SELECT count(*) FROM pg_trigger WHERE tgname = ANY(%s)",
                [list(DROPPED_TRIGGERS)],
            )
            == 0
        )
        transaction.set_rollback(True)


def test_the_file_revokes_the_digest_write_grants_and_keeps_the_reads():
    """Each writer login loses INSERT and UPDATE on every digest table.

    The logins hold the writes an earlier release's database-grants gave
    (and SELECT); after the file they keep only SELECT, so database-grants,
    which no longer declares the writes, admits them.
    """
    privileges = ("SELECT", "INSERT", "UPDATE")
    with transaction.atomic():
        with connection.cursor() as cursor:
            for login in WRITERS:
                cursor.execute(ENSURE_LOGIN.format(login))
                for table in DIGEST_TABLES:
                    cursor.execute(
                        f"GRANT SELECT, INSERT, UPDATE ON {table} TO {login}"
                    )
            cursor.execute(FROZEN)
            cursor.execute(
                "SELECT r.rolname, t.name, p.privilege FROM pg_roles r "
                "CROSS JOIN unnest(%s::text[]) t(name) "
                "CROSS JOIN unnest(%s::text[]) p(privilege) "
                "WHERE r.rolname = ANY(%s) "
                "AND has_table_privilege(r.oid, t.name::regclass, p.privilege)",
                [list(DIGEST_TABLES), list(privileges), list(WRITERS)],
            )
            held = set(cursor.fetchall())
        assert held == {
            (login, table, "SELECT") for login in WRITERS for table in DIGEST_TABLES
        }
        transaction.set_rollback(True)
