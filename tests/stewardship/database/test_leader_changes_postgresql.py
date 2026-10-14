"""The count-only Ministry leader change audit event (#1034, migration 0070).

A source promotion and a configuration activation each read the pointers the
leader definition depends on just before their change and, just after it,
call stewardship_ministry_leader_changes_v1, which writes one
ministry_leaders_changed event with the (address, Ministry) pairs gained and
lost when either is non-zero, and returns nothing: no address, Member DUID
or count ever reaches the caller. Nothing changed means no event, and a
failure never fails the change.
"""

import logging
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from psycopg import sql

from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns import leader_changes
from parishkit.stewardship.deployment import ServiceRole

from .leader_builders import leader, promote_leaders
from .test_background_grants_postgresql import task_login
from .test_live_ministries_postgresql import changed
from .test_ministry_leaders_postgresql import LEADERS as EVERY_KIND
from .test_ministry_leaders_postgresql import set_roles, source
from .test_ministry_responses_postgresql import configure

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0070_ministry_leader_changes.sql"
)

EVENT = "ministry_leaders_changed"
LEADERS = [
    leader("choir@example.org", 4, role="Chairperson"),
    leader("pantry@example.org", 9),
    leader("both@example.org", 4, 9),
    leader("team@example.org", 9, role="Team 1 leader"),
]


def events():
    """Every recorded leader change, oldest first: (event, context)."""
    return [
        (row.event, row.context)
        for row in AuditContext.objects.filter(event__event_type=EVENT)
        .select_related("event")
        .order_by("event__created_at", "event__id")
    ]


def started(harness):
    """Promote the leaders and select both Ministries; count the events.

    The leader pairs are then these four plus the fixture's own Choir
    Chairperson (``PAIRS``).
    """
    promote_leaders(harness, source(), LEADERS)
    configure(harness, selected=(4, 9))
    assert len(pairs()) == PAIRS
    return len(events())


def pairs():
    """The current leader pairs, as the schema owner reads them."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT email, ministry_duid "
            "FROM public.stewardship_ministry_leaders_v1()"
        )
        return set(cursor.fetchall())


PROBE = "stewardship_leaders_0042_probe"


def copy_rows():
    """Full rows at the current pointers: 0070's definition, 0042's original
    body (installed as ``PROBE`` by ``original_definition``) and the wrapper.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT leader.* FROM public.stewardship_system_configuration runtime "
            "CROSS JOIN public.stewardship_source_current pointer "
            "CROSS JOIN LATERAL public.stewardship_ministry_leaders_at_v1("
            "runtime.active_configuration_id, runtime.current_campaign_id, "
            "pointer.snapshot_id) leader WHERE pointer.singleton"
        )
        definition = sorted(cursor.fetchall())
        cursor.execute(f"SELECT * FROM public.{PROBE}()")
        original = sorted(cursor.fetchall())
        cursor.execute("SELECT * FROM public.stewardship_ministry_leaders_v1()")
        return definition, original, sorted(cursor.fetchall())


@contextmanager
def original_definition():
    """Migration 0042's leaders body under ``PROBE``, for the duration.

    Less its Ministry activity test, which #1267's 0091 removed from the
    current definition along with the table it read.
    """
    body = _old_definition().replace(
        "public.stewardship_ministry_leaders_v1(", f"public.{PROBE}(", 1
    )
    assert body.count(ACTIVITY_TEST) == 1
    body = body.replace(ACTIVITY_TEST, ";")
    with connection.cursor() as cursor:
        cursor.execute(body)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP FUNCTION public.{PROBE}()")


def same_rows():
    """The three agree row for row; return the rows."""
    definition, original, wrapper = copy_rows()
    assert definition == original == wrapper
    return definition


# LEADERS' four pairs and the response fixture's Choir Chairperson.
PAIRS = 5


def test_a_promotion_records_counts_only_when_leaders_change(response_service):
    """Gained and lost pairs, and no addresses.

    The same data again records nothing. A Member who drops the role, a
    leader whose address changes and a new leader then give one event:
    two pairs gained (the new address, the new leader) and two lost (the
    dropped role, the old address).
    """
    harness = response_service
    before = started(harness)
    # However the fixture's first configuration and promotion split it,
    # every leader pair was counted in as it appeared.
    assert sum(c["count"] - c["matching_count"] for _, c in events()) == PAIRS
    promote_leaders(harness, source(), LEADERS)
    assert len(events()) == before
    snapshot = promote_leaders(
        harness,
        source(),
        [
            leader("choir@example.org", 4, role="Member"),
            leader("pantry.new@example.org", 9),
            leader("both@example.org", 4, 9),
            leader("team@example.org", 9, role="Team 1 leader"),
            leader("usher@example.org", 4, role="Staff"),
        ],
    )
    assert len(events()) == before + 1
    event, context = events()[-1]
    assert context == {
        "count": 2,
        "matching_count": 2,
        "snapshot_id": str(snapshot.pk),
    }
    assert event.subject_id == snapshot.task_id
    assert event.ownership_scope == "parish" and event.parish_id is not None
    assert "@" not in repr(context)


def test_an_activation_records_a_leader_role_change(response_service):
    """Campaign settings' leader roles change scope without a refresh.

    The installer's own login holds the definer grant. A change that leaves
    every pair alone (a rename) records nothing, and a login without the
    grant (offline Admin-access recovery) applies its change and records
    nothing.
    """
    harness = response_service
    before = started(harness)
    result = changed(
        harness,
        {"ministry_leader_roles": ["Team 1 Leader"]},
        ServiceRole.CONFIG_INSTALLER,
    )
    assert result.state == "applied"
    assert len(events()) == before + 1
    event, context = events()[-1]
    # Team 1 leader gains Ministry 9; every Chairperson and Staff pair goes.
    assert context == {"count": 1, "matching_count": PAIRS}
    assert event.subject_id == result.request_id
    renamed = changed(harness, {"name": "Renamed"}, ServiceRole.CONFIG_INSTALLER)
    assert renamed.state == "applied"
    assert len(events()) == before + 1
    restored = changed(
        harness,
        {"ministry_leader_roles": ["Chairperson", "Staff"]},
        ServiceRole.ADMIN_RECOVERY,
    )
    assert restored.state == "applied"
    assert len(events()) == before + 1


def test_a_failed_comparison_never_fails_the_promotion(
    response_service, monkeypatch, caplog
):
    """A recorder that fails is logged at WARNING and the promotion commits."""
    harness = response_service
    before = started(harness)
    monkeypatch.setattr(
        leader_changes,
        "RECORD_SQL",
        "SELECT stewardship_no_such_function(%s, %s, %s, %s, %s, %s, %s)",
    )
    with caplog.at_level(logging.WARNING):
        snapshot = promote_leaders(harness, source(), {"new@example.org": [9]})
    snapshot.refresh_from_db()
    assert snapshot.state == "promoted"
    assert len(events()) == before
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def record(before, **values):
    """Call the recorder as the owners do, with these pointers and names."""
    with connection.cursor() as cursor:
        cursor.execute(
            leader_changes.RECORD_SQL,
            [
                before.snapshot,
                before.configuration,
                before.campaign,
                values.get("actor"),
                values.get("subject"),
                values.get("correlation", uuid4()),
                values.get("snapshot"),
            ],
        )
        return cursor.fetchone()


def test_only_the_change_owners_may_record_and_learn_nothing(response_service):
    """database-grants gives the recorder to the worker and the installer only.

    Under their own logins they can record but never read a leader or a
    count back (it returns nothing, and the installer cannot read the audit
    log); a login given EXECUTE by mistake is refused by the recorder itself.
    """
    started(response_service)
    before = leader_changes.leader_pointers()
    empty = leader_changes.Pointers(None, None, None)
    count = len(events())
    for role, granted in (
        (ServiceRole.WORKER, True),
        (ServiceRole.CONFIG_INSTALLER, True),
        (ServiceRole.WEB, False),
        (ServiceRole.ADMIN_RECOVERY, False),
    ):
        with task_login(role), connection.cursor() as cursor:
            assert (leader_changes.leader_pointers() is not None) is granted, role
            if granted:
                # Unchanged: nothing recorded. Against no leaders at all:
                # recorded, and still only void comes back.
                # psycopg reads a void result as the empty string.
                assert record(before) == ("",), role
                assert record(empty, subject=uuid4()) == ("",), role
            else:
                with pytest.raises(DatabaseError), transaction.atomic():
                    record(before)
            for definition in (
                "public.stewardship_ministry_leaders_v1()",
                "public.stewardship_ministry_leaders_at_v1(%s, %s, %s)",
            ):
                with pytest.raises(DatabaseError), transaction.atomic():
                    cursor.execute(
                        f"SELECT * FROM {definition}",
                        [before.configuration, before.campaign, before.snapshot]
                        if "%s" in definition
                        else None,
                    )
    assert len(events()) == count + 2
    assert [c for _, c in events()[-2:]] == [{"count": PAIRS, "matching_count": 0}] * 2
    name = "pk_leader_changes_probe"
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOINHERIT").format(sql.Identifier(name))
        )
        cursor.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                sql.SQL(leader_changes.RECORDER), sql.Identifier(name)
            )
        )
        cursor.execute(
            sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(name))
        )
        try:
            with (
                pytest.raises(DatabaseError, match="Only source promotion"),
                transaction.atomic(),
            ):
                record(empty)
        finally:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(name)))
            cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))


def test_the_event_is_attributed_and_rolls_back_with_its_change(response_service):
    """The definer's event names the given actor, subject, correlation and
    snapshot, is owned by the current Parish, and a rolled-back change
    leaves no trace of it."""
    started(response_service)
    empty = leader_changes.Pointers(None, None, None)
    count = len(events())
    actor, subject, correlation, snapshot = uuid4(), uuid4(), uuid4(), uuid4()
    with transaction.atomic():
        record(
            empty,
            actor=actor,
            subject=subject,
            correlation=correlation,
            snapshot=snapshot,
        )
        event, context = events()[-1]
        row = AuditContext.objects.get(event=event)
        assert (event.actor_id, event.subject_id, event.correlation_id) == (
            actor,
            subject,
            correlation,
        )
        assert (row.actor_id, row.actor_kind, row.schema) == (actor, "system", "action")
        assert event.ownership_scope == "parish" and event.parish_id is not None
        assert context == {
            "count": PAIRS,
            "matching_count": 0,
            "snapshot_id": str(snapshot),
        }
        transaction.set_rollback(True)
    assert len(events()) == count
    # A correlation is required, as for every audit row.
    with pytest.raises(DatabaseError), transaction.atomic():
        record(empty, correlation=None)


def test_the_wrapper_is_the_one_definition(response_service):
    """0070's definition answers exactly what 0042's original body answers,
    row for row, and so does the wrapper that replaced it: every kind of
    leader and non-leader from the #922 tests, a custom role list and a
    removed Ministry. (There is no inactive Ministry any more, #1267.)
    """
    harness = response_service
    promote_leaders(harness, source(), EVERY_KIND)
    configure(harness, selected=(4, 9))
    with original_definition():
        assert len(same_rows()) > PAIRS
        set_roles(harness, ["Team 1 Leader"])
        assert same_rows()
        set_roles(harness, ["Chairperson", "Staff"])
        configure(harness, selected=(4,))
        assert same_rows()
    # Earlier pointers that name nothing give no leaders, not an error.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) "
            "FROM public.stewardship_ministry_leaders_at_v1(%s, %s, %s)",
            [uuid4(), uuid4(), None],
        )
        assert cursor.fetchone()[0] == 0


def test_migration_check_refuses_absent_invoker_or_restored_functions(
    response_service,
):
    """The frozen file's DO block passes as installed and fails otherwise.

    Each case runs the file's "before" block (the leaders digest it keeps in
    a transaction-local setting) and the check in one transaction, as the
    migration does, after ``as_0070_left_it`` restores what #1267's 0091
    later changed.
    """
    started(response_service)
    text = MIGRATION.read_text(encoding="utf-8")
    before = text[
        text.index("DO $before$") : text.index("$before$;") + len("$before$;")
    ]
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    copy = "public.stewardship_ministry_leaders_at_v1(uuid, uuid, uuid)"
    wrapper = "public.stewardship_ministry_leaders_v1()"
    recorder = f"public.{RECORDER_SIGNATURE}"
    with connection.cursor() as cursor:
        with transaction.atomic():
            as_0070_left_it(cursor, text)
            cursor.execute(before)
            cursor.execute(check)  # The installed definitions pass.
            transaction.set_rollback(True)
        for broken in (
            f"DROP FUNCTION {recorder}",
            f"DROP FUNCTION {copy}",
            f"ALTER FUNCTION {recorder} SECURITY INVOKER",
            f"ALTER FUNCTION {wrapper} SECURITY INVOKER",
            f"ALTER FUNCTION {copy} SECURITY DEFINER",
            f"GRANT EXECUTE ON FUNCTION {recorder} TO PUBLIC",
            # 0042's own body again: a second definition of who leads what.
            _old_definition(),
            # Leaders that differ from those before the replacement.
            "SELECT set_config('stewardship.leaders_before_0070', 'x', true)",
        ):
            with (
                pytest.raises(DatabaseError, match="Migration 0070"),
                transaction.atomic(),
            ):
                as_0070_left_it(cursor, text)
                cursor.execute(before)
                cursor.execute(broken)
                cursor.execute(check)
        with transaction.atomic():
            as_0070_left_it(cursor, text)
            cursor.execute(before)
            cursor.execute(check)  # Rolled back to the installed definitions.
            transaction.set_rollback(True)


RECORDER_SIGNATURE = (
    "stewardship_ministry_leader_changes_v1(uuid, uuid, uuid, uuid, uuid, uuid, uuid)"
)


# 0042's and 0070's Ministry activity test, which 0091 (#1267) removed.
ACTIVITY_TEST = """
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_ministry_activity activity
          WHERE activity.configuration_id=current_configuration
            AND activity.organization_id=ministry.organization_id
            AND activity.ministry_duid=duid.ministry
            AND NOT activity.active);"""


def as_0070_left_it(cursor, text):
    """Restore, within the caller's transaction, what 0070 left and 0091
    (#1267) changed: the Ministry activity table, reduced to what the
    leader definition reads, and 0070's own body of that definition, whose
    activity test 0070's check names."""
    cursor.execute(
        "CREATE TABLE public.stewardship_ministry_activity (configuration_id uuid, "
        "organization_id bigint, ministry_duid bigint, active boolean)"
    )
    start = text.index("CREATE FUNCTION public.stewardship_ministry_leaders_at_v1(")
    body = text[start : text.index("END $$;", start) + len("END $$;")]
    cursor.execute(body.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))


def _old_definition():
    """Migration 0042's stewardship_ministry_leaders_v1, as CREATE OR REPLACE."""
    text = (MIGRATION.parent / "0042_ministry_leaders_from_roles.sql").read_text(
        encoding="utf-8"
    )
    start = text.index("CREATE FUNCTION public.stewardship_ministry_leaders_v1(")
    body = text[start : text.index("END $$;", start) + len("END $$;")]
    return body.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
