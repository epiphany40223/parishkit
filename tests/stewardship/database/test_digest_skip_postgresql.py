"""A digest's skipped sends are never planned, by any writer (#1658).

Through real configuration installation: a Daily or Weekly Admin digest
whose revision carries ``skip_dates`` gets no occurrence for those slots from
the scheduler's digest planner, the database's occurrence guard refuses one
even from a planner that ignored them, the canonical slot check answers no
slot for them, and the weekly page proof accepts a skipped week without an
occurrence. Frozen migration 0121's closing DO block passes on the installed
functions and refuses each older body, a weakened body (a guard that
enforces Weekly skips alone, a slot check whose skip line can never run)
and a guard that lost its definer rights.
"""

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns import schedule_evaluation
from parishkit.stewardship.campaigns.digest_schedule_planning import (
    DigestScheduleProducer,
)
from parishkit.stewardship.campaigns.models import (
    Campaign,
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.jobs.scheduler import scheduler_session

from ..campaign_factory import schedule
from .campaign_builders import campaign_clock, change
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
    """Install one digest that skips ``skip``; return its definition id."""
    row = schedule(
        str(campaign.pk),
        kind="weekly_digest" if weekly else "daily_digest",
        date=None,
        weekday=2 if weekly else None,
        time="00:15:00",
        skip_dates=skip,
    )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "schedules", **row}],
        ).state
        == "applied"
    )
    return UUID(row["id"])


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


def test_the_planner_never_creates_a_skipped_daily_send(family_service, auth_service):  # noqa: F811
    """The scheduler plans the days around a skipped one, never the day itself."""
    identifier = add_digest(
        auth_service.store, family_service.campaign, skip=["2054-10-02"]
    )
    producer = DigestScheduleProducer(uuid4())
    with (
        campaign_clock(datetime(2054, 10, 5, tzinfo=UTC)),
        scheduler_session() as guard,
    ):
        (page,) = producer(guard)
        assert page.created == 2
        # The cursor moved past the skipped day, so it is not examined again.
        assert list(producer.cursors.values()) == [date(2054, 10, 3)]
    assert sorted(
        ScheduleOccurrence.objects.filter(definition_id=identifier).values_list(
            "slot", flat=True
        )
    ) == ["2054-10-01", "2054-10-03"]


def test_the_database_refuses_an_occurrence_for_a_skipped_send(
    family_service,  # noqa: F811
    auth_service,
    monkeypatch,
):
    """A planner that ignored skip dates is stopped by the occurrence guard."""
    identifier = add_digest(
        auth_service.store, family_service.campaign, skip=["2054-10-01"]
    )
    # Every candidate in range counts as a send: the skip is ignored.
    monkeypatch.setattr(schedule_evaluation.SchedulePlan, "_sends", lambda *_: True)
    with (
        campaign_clock(datetime(2054, 10, 3, tzinfo=UTC)),
        scheduler_session() as guard,
        pytest.raises(DatabaseError, match="Occurrence creation is not admitted"),
    ):
        DigestScheduleProducer(uuid4())(guard)
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


def page_proof(definition, campaign, week):
    """The weekly page proof for a first page ending at ``week``, no occurrences."""
    revision = ScheduleDefinition.objects.get(pk=definition).current_revision_id
    configuration = Campaign.objects.get(pk=campaign.pk).active_configuration_id
    proposed = {
        "phase": "dates",
        "cursor": week,
        "campaign_configuration_id": str(configuration),
        "revision_id": str(revision),
        "cutoff": "2054-12-31T00:00:00+00:00",
        "mode": "testing",
    }
    return scalar(
        "SELECT stewardship_weekly_digest_page_v1(%s::jsonb,%s::jsonb)",
        [json.dumps({"phase": "dates", "cursor": None}), json.dumps(proposed)],
    )


def test_the_weekly_page_proof_accepts_a_skipped_week_alone(
    family_service,  # noqa: F811
    auth_service,
):
    """A page over a skipped week needs no occurrence; an unskipped week does."""
    store, campaign = auth_service.store, family_service.campaign
    weeks = wednesdays(2)
    skipping_first = add_digest(store, campaign, skip=[weeks[0]], weekly=True)
    assert page_proof(skipping_first, campaign, weeks[0]) is True
    # The second week has no occurrence, so a page through it is refused.
    assert page_proof(skipping_first, campaign, weeks[1]) is False


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
    """The DO block passes as installed and refuses each function's old body.

    Each old body runs inside a rolled-back transaction, so the installed
    functions are left as the full migration made them.
    """
    with connection.cursor() as cursor:
        recorded_acl(cursor)
        cursor.execute(check())
        with transaction.atomic():
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
        cursor.execute(check())


def test_migration_self_check_refuses_a_guard_without_definer_rights():
    """CREATE OR REPLACE resets SECURITY DEFINER; the DO block refuses that."""
    with connection.cursor() as cursor:
        recorded_acl(cursor)
        with transaction.atomic():
            cursor.execute(body("stewardship_occurrence_guard_v1", "END $_$;"))
            with (
                pytest.raises(DatabaseError, match="occurrence guard as a definer"),
                transaction.atomic(),
            ):
                cursor.execute(check())
            transaction.set_rollback(True)
        cursor.execute(check())


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
            cursor.execute(weakened(name))
            if name == "stewardship_occurrence_guard_v1":
                cursor.execute(
                    "ALTER FUNCTION public.stewardship_occurrence_guard_v1() "
                    "SECURITY DEFINER"
                )
            with pytest.raises(DatabaseError, match=message), transaction.atomic():
                cursor.execute(check())
            transaction.set_rollback(True)
        cursor.execute(check())
