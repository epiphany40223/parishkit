"""A report's skipped sends are never planned, by any writer (#1658, #1786).

Through real configuration installation: a report shaped as the converted
Daily or Weekly whose revision carries ``skip_dates`` gets no occurrence for
those slots from the report producer and worker, the database's occurrence
guard refuses one even from a planner that ignored them, and the canonical
slot check answers no slot for them. Frozen migration 0121's closing DO
block passes on the bodies 0121 installed (migration 0191 has since
replaced them for report emails) and refuses each older body, a weakened
body (a guard that enforces Weekly skips alone, a slot check whose skip
line can never run) and a guard that lost its definer rights.
"""

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.reports_conversion import converted_values
from parishkit.stewardship.campaigns import schedule_evaluation
from parishkit.stewardship.campaigns.models import ScheduleOccurrence

from .campaign_builders import campaign_clock
from .report_builders import add_report, prepare_due
from .test_family_auth_postgresql import family_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = (MIGRATIONS / "0121_digest_skip_dates.sql").read_text(encoding="utf-8")
# The occurrence guard's skipped-send refusal, as 0121 adds it after the
# post-close resolution check.
GUARD_SKIP = """
           -- A digest's skipped send (#1658) is never planned, by any writer.
           OR (d.kind IN ('daily_digest','weekly_digest') AND EXISTS (
               SELECT 1 FROM stewardship_schedule_revision skipping WHERE skipping.id=NEW.revision_id
                   AND coalesce(skipping.values->'skip_dates' ? NEW.slot,false)))"""  # noqa: E501
DUE_SKIP = """    -- A digest's skipped send (#1658) is no slot: nothing may plan it.
    IF coalesce(v.values->'skip_dates' ? slot,false) THEN RETURN NULL; END IF;
"""
PAGE_NEW = """            -- A skipped week (#1658) has no slot and needs no occurrence.
            WHERE (expected.due IS NULL AND NOT coalesce(v.values->'skip_dates' ? day::date::text,false))
              OR expected.due>(proposed->>'cutoff')::timestamptz
              OR (expected.due IS NOT NULL
                AND NOT stewardship_schedule_slot_excluded_v1"""  # noqa: E501
PAGE_OLD = """            WHERE expected.due IS NULL OR expected.due>(proposed->>'cutoff')::timestamptz
              OR (NOT stewardship_schedule_slot_excluded_v1"""  # noqa: E501


def add_digest(store, campaign, *, skip, weekly=False):
    """Install a converted Daily (or Weekly, Wednesdays) skipping ``skip``; its id.

    Both send at 00:15; the Daily's slot is the day it reports on, the
    Weekly's its send date, as the digests' were.
    """
    values = converted_values(
        {
            "campaign_id": str(campaign.pk),
            "kind": "weekly_digest" if weekly else "daily_digest",
            "time": "00:15:00",
            "weekday": 2 if weekly else None,
            "subject": "",
            "skip_dates": skip,
        },
        None,
    )
    return add_report(store, campaign, **values)


def wednesdays(count):
    """The first ``count`` weekly slots (Wednesdays) of the October 2054 campaign."""
    first = date(2054, 10, 1)
    first += timedelta(days=(2 - first.weekday()) % 7)
    return [(first + timedelta(weeks=n)).isoformat() for n in range(count)]


def scalar(sql, params=()):
    """One value from the test's own (superuser) connection."""
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def test_the_planner_never_creates_a_skipped_daily_send(response_service):
    """The report's send plans the days around a skipped one, never the day itself.

    By midnight UTC on October 5 the sends of October 2, 3 and 4 are due;
    October 3's (reporting on October 2) is skipped, so the send made
    covers October 1 and 3 only. (A report's send reads the promoted
    source, so the campaign has one.)
    """
    harness = response_service
    identifier = add_digest(
        harness.service.store, harness.campaign, skip=["2054-10-02"]
    )
    with campaign_clock(datetime(2054, 10, 5, tzinfo=UTC)):
        assert len(prepare_due()) == 1
    assert sorted(
        ScheduleOccurrence.objects.filter(definition_id=identifier).values_list(
            "slot", flat=True
        )
    ) == ["2054-10-01", "2054-10-03"]


def test_the_database_refuses_an_occurrence_for_a_skipped_send(
    response_service, monkeypatch
):
    """A planner that ignored skip dates is stopped by the database.

    The worker's write admission for report sends
    (``stewardship_report_email_write_admitted_v1``, which the setup
    completion trigger asks first) admits no occurrence for a skipped
    slot, and the occurrence guard behind it refuses one too.
    """
    harness = response_service
    identifier = add_digest(
        harness.service.store, harness.campaign, skip=["2054-10-01"]
    )
    # Every candidate in range counts as a send: the skip is ignored.
    monkeypatch.setattr(schedule_evaluation.ReportPlan, "_sends", lambda *_: True)
    with (
        campaign_clock(datetime(2054, 10, 3, tzinfo=UTC)),
        pytest.raises(
            DatabaseError, match="Worker configuration effects require atomic setup"
        ),
    ):
        prepare_due()
    assert not ScheduleOccurrence.objects.filter(definition_id=identifier).exists()


def test_the_slot_check_answers_no_slot_for_a_skipped_send(
    family_service,  # noqa: F811
    auth_service,
):
    """stewardship_catchup_due_v1 is NULL only for the skipped dates."""
    store, campaign = auth_service.store, family_service.campaign
    daily = add_digest(store, campaign, skip=["2054-10-02"])
    weeks = wednesdays(2)
    weekly = add_digest(store, campaign, skip=[weeks[0]], weekly=True)
    due = "SELECT stewardship_catchup_due_v1(%s,%s)"
    assert scalar(due, [daily, "2054-10-02"]) is None
    assert scalar(due, [daily, "2054-10-03"]) == datetime(
        2054, 10, 4, 4, 15, tzinfo=UTC
    )
    assert scalar(due, [weekly, weeks[0]]) is None
    assert scalar(due, [weekly, weeks[1]]) is not None


def body(name, terminator):
    """One replaced function's whole definition, as 0121 installs it."""
    start = FROZEN.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    return FROZEN[start : FROZEN.index(terminator, start) + len(terminator)]


def old_body(name):
    """The definition 0121 replaced: its own copy without #1658's change."""
    if name == "stewardship_occurrence_guard_v1":
        text = body(name, "END $_$;")
        replaced = text.replace(GUARD_SKIP, "")
    elif name == "stewardship_catchup_due_v1":
        text = body(name, "END $$;")
        replaced = text.replace(DUE_SKIP, "")
    else:
        text = body(name, "END $$;")
        replaced = text.replace(PAGE_NEW, PAGE_OLD)
    assert replaced != text and "skip_dates" not in replaced
    return replaced


def check():
    """0121's DO block, as the frozen file runs it."""
    return re.search(r"^DO \$check\$.*?\$check\$;", FROZEN, re.M | re.S)[0]


def as_0121(cursor):
    """Install the three bodies 0121 made, as it left them, in this transaction.

    Migration 0191 replaced them for report emails (#1786), so 0121's check
    is run on its own bodies; each test's transaction rolls them back.
    """
    for name, terminator in (
        ("stewardship_occurrence_guard_v1", "END $_$;"),
        ("stewardship_catchup_due_v1", "END $$;"),
        ("stewardship_weekly_digest_page_v1", "END $$;"),
    ):
        cursor.execute(body(name, terminator))
    cursor.execute(
        "ALTER FUNCTION public.stewardship_occurrence_guard_v1() SECURITY DEFINER"
    )


def recorded_acl(cursor):
    """Record the grants, settings and owner as the frozen file does first."""
    statement = re.search(
        r"^SELECT set_config\('stewardship\.migration_0121_acl'.*?, true\);",
        FROZEN,
        re.M | re.S,
    )[0]
    cursor.execute(statement.replace(", true);", ", false);"))


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("stewardship_occurrence_guard_v1", "did not replace the occurrence guard"),
        ("stewardship_catchup_due_v1", "did not replace the slot check"),
        ("stewardship_weekly_digest_page_v1", "did not replace the weekly page proof"),
    ],
)
def test_migration_self_check_refuses_each_old_body(name, message):
    """The DO block passes on 0121's bodies and refuses each function's old body.

    Each body runs inside a rolled-back transaction, so the installed
    functions are left as the full migration chain made them.
    """
    with connection.cursor() as cursor:
        recorded_acl(cursor)
        with transaction.atomic():
            as_0121(cursor)
            cursor.execute(check())
            cursor.execute(old_body(name))
            if name == "stewardship_occurrence_guard_v1":
                # Even with its definer rights back, the old guard is refused.
                cursor.execute(
                    "ALTER FUNCTION public.stewardship_occurrence_guard_v1() "
                    "SECURITY DEFINER"
                )
            with pytest.raises(DatabaseError, match=message), transaction.atomic():
                cursor.execute(check())
            transaction.set_rollback(True)


def test_migration_self_check_refuses_a_guard_without_definer_rights():
    """CREATE OR REPLACE resets SECURITY DEFINER; the DO block refuses that."""
    with connection.cursor() as cursor:
        recorded_acl(cursor)
        with transaction.atomic():
            as_0121(cursor)
            cursor.execute(body("stewardship_occurrence_guard_v1", "END $_$;"))
            with (
                pytest.raises(DatabaseError, match="occurrence guard as a definer"),
                transaction.atomic(),
            ):
                cursor.execute(check())
            transaction.set_rollback(True)


def weakened(name):
    """A body that keeps 0121's skip text but no longer enforces it fully."""
    if name == "stewardship_occurrence_guard_v1":
        text = body(name, "END $_$;")
        # Daily skips are no longer refused; the clause's last line is kept.
        replaced = text.replace(
            "OR (d.kind IN ('daily_digest','weekly_digest') AND EXISTS (",
            "OR (d.kind IN ('weekly_digest') AND EXISTS (",
        )
    else:
        text = body(name, "END $$;")
        # The skip line moved after the last branch, where it never runs.
        replaced = text.replace(DUE_SKIP, "").replace(
            "    ELSE RETURN NULL;\n", "    ELSE RETURN NULL;\n" + DUE_SKIP
        )
    assert replaced != text and replaced.count("skip_dates") == text.count("skip_dates")
    return replaced


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("stewardship_occurrence_guard_v1", "did not replace the occurrence guard"),
        ("stewardship_catchup_due_v1", "did not replace the slot check"),
    ],
)
def test_migration_self_check_refuses_a_weakened_body(name, message):
    """The pinned body (md5) refuses what a text match alone would pass."""
    with connection.cursor() as cursor:
        recorded_acl(cursor)
        with transaction.atomic():
            as_0121(cursor)
            cursor.execute(weakened(name))
            if name == "stewardship_occurrence_guard_v1":
                cursor.execute(
                    "ALTER FUNCTION public.stewardship_occurrence_guard_v1() "
                    "SECURITY DEFINER"
                )
            with pytest.raises(DatabaseError, match=message), transaction.atomic():
                cursor.execute(check())
            transaction.set_rollback(True)
