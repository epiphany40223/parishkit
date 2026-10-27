"""Ministry checks read the roster as of the Ministry roster date (#2293).

Staff enter a campaign's Ministry joins and leaves in ParishSoft dated for
its commitment period (review pipeline spec, "Ministry roster date"): for
the fixture campaign, which ends 2054-10-31 with a commitment period from
2055-01-01, a leave ends 2054-12-31 and a join starts 2055-01-01. The
refresh checks, their guard (frozen migration 0189) and "ParishSoft already
holds this value." read the roster as of the later of the refresh date and
2055-01-01, so a refresh made now confirms both, and one made once the
period is under way confirms an entry dated after its start too; the
Family form and its response guard keep today's roster. A campaign without
a commitment period reads the roster as of the refresh's date, as before.
"""

from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.workflows.models import MinistryRequest

from ..campaign_factory import financial
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_ministry_reports_postgresql import actor
from .test_ministry_responses_postgresql import ministry_source, respond
from .test_ministry_review_postgresql import decide, requests
from .test_refresh_checks_postgresql import checks, view
from .test_refresh_checks_postgresql import started as started  # noqa: F401
from .test_response_http_postgresql import answers_for, load_form
from .test_source_families_postgresql import prepare, promote
from .test_strict_phase_order_postgresql import Rollback, body

pytestmark = pytest.mark.django_db(transaction=True)

# The fixture campaign's commitment period start (campaign_factory.financial).
COMMITMENT_START = "2055-01-01"


def dated_source(joined=COMMITMENT_START):
    """The Ministry fixture with Member 3's roster entered for the period.

    Member 3's Choir (4) membership ends the day before the commitment
    period starts, and a Food pantry (9) membership starts on ``joined``,
    by default the period's first day: the leave and the join the Family
    asks for, entered by hand as staff are told to date them.
    """
    data = ministry_source()
    data.ministry_type_memberships[4]["membership"][0]["endDate"] = "2054-12-31"
    data.ministry_type_memberships[9]["membership"].append(
        {
            "memberId": 3,
            "ministryRoleId": 7,
            "ministryRoleName": "Member",
            "startDate": joined,
            "endDate": None,
        }
    )
    return data


def refresh(harness, *, joined=COMMITMENT_START, as_of=None):
    """Promote a full refresh of ``dated_source(joined)``, made on ``as_of``
    (by default today, with no recorded refresh date); returns the snapshot.
    """
    snapshot, claim = prepare(dated_source(joined), as_of=as_of)
    return promote(snapshot, claim, harness.campaign, harness.rings)


def responded(harness, *, commitment, joined=COMMITMENT_START):
    """A live response joining Food pantry and leaving Choir, on the dated roster.

    ``commitment`` gives the campaign a commitment period (Financial
    stewardship on), else it has none. Returns the harness and the Family
    form's current Ministries for Member 3, as the form drew them. The
    join is dated ``joined``.
    """
    harness.snapshot = refresh(harness, joined=joined)
    values = {
        "modules": sorted(["census", "ministry", *(["financial"] * commitment)]),
        "ministry_duids": [4, 9],
    }
    if commitment:
        values["financial"] = financial(fund_duids=[9], comparison_fund_duids=[9])
    store = harness.service.store
    receipt = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(harness.campaign.pk),
                "values": values,
            }
        ],
    )
    assert receipt.state == "applied"
    harness.campaign.refresh_from_db()
    harness = activate_response_service(harness)
    form = load_form(harness)
    current = form["ministries"]["members"]["3"]["current"]
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": [4]}
    if commitment:
        answers["financial"] |= {"annual_pledge": "100", "frequency": "annual"}
    # The response guard checks the leave against today's roster too: Choir
    # is still current today, so leaving it is admitted.
    respond(harness, form, answers)
    return harness, current


def accepted(harness, staff):
    """Member 3's join and leave, each accepted by Staff (To enter by hand)."""
    join, leave = requests()
    for request in (join, leave):
        decide(harness, staff, "accept", request)
    return [MinistryRequest.objects.get(pk=row.pk) for row in (join, leave)]


def test_a_dated_join_and_leave_are_confirmed_months_early(response_service):
    """A join dated the commitment start and a leave ending the day before
    are held as of that start: before the tick the rows say ParishSoft
    already holds them, and once ticked a refresh made now confirms both
    (the check guard admits each confirmation). The Family form still drew
    today's roster: Choir current, Food pantry not yet."""
    harness, current = responded(response_service, commitment=True)
    assert current == [4]
    staff = actor(harness, {"staff"}, ())
    join, leave = accepted(harness, staff)
    rows = view(harness, staff)
    assert rows[str(join.pk)]["already_held"]
    assert rows[str(leave.pk)]["already_held"]
    for request in (join, leave):
        decide(
            harness,
            staff,
            "entered_by_hand",
            MinistryRequest.objects.get(pk=request.pk),
        )
    refresh(harness)
    assert checks(request=join) == ["confirmed"]
    assert checks(request=leave) == ["confirmed"]


def test_without_a_commitment_period_the_refresh_date_decides(
    response_service, started
):
    """With no commitment period the roster is read as of the refresh's
    date, as before #2293: the join that starts later is not held, so it is
    Not seen after two full refreshes, and the leave of a membership still
    current is unchanged; neither said ParishSoft already holds it."""
    harness, current = responded(response_service, commitment=False)
    assert current == [4]
    staff = actor(harness, {"staff"}, ())
    join, leave = accepted(harness, staff)
    rows = view(harness, staff)
    assert not rows[str(join.pk)]["already_held"]
    assert not rows[str(leave.pk)]["already_held"]
    for request in (join, leave):
        decide(
            harness,
            staff,
            "entered_by_hand",
            MinistryRequest.objects.get(pk=request.pk),
        )
    refresh(harness)
    refresh(harness)
    assert checks(request=join) == ["unchanged", "unchanged"]
    assert checks(request=leave) == ["unchanged", "unchanged"]
    assert view(harness, staff)[str(join.pk)]["not_seen"]


def test_once_the_period_is_under_way_the_refresh_date_decides(
    response_service, started
):
    """A join dated after the commitment start (entered late) is not held
    as of the start, so it is neither already held nor confirmed by a
    refresh made before the period begins; a refresh made once the period
    is under way reads the roster as of its own, later date and confirms
    it (the check guard admits the confirmation), with the leave."""
    joined = "2055-02-01"
    harness, current = responded(response_service, commitment=True, joined=joined)
    assert current == [4]
    staff = actor(harness, {"staff"}, ())
    join, leave = accepted(harness, staff)
    rows = view(harness, staff)
    assert not rows[str(join.pk)]["already_held"]
    assert rows[str(leave.pk)]["already_held"]
    for request in (join, leave):
        decide(
            harness,
            staff,
            "entered_by_hand",
            MinistryRequest.objects.get(pk=request.pk),
        )
    # Made before the period begins: the start is the later day.
    early = refresh(harness, joined=joined, as_of=date(2054, 11, 1))
    assert roster_day(harness.campaign.pk, early.pk) == date(2055, 1, 1)
    assert checks(request=join) == ["unchanged"]
    assert checks(request=leave) == ["confirmed"]
    # Made once it is under way: the refresh's own date is.
    late = refresh(harness, joined=joined, as_of=date(2055, 3, 1))
    assert roster_day(harness.campaign.pk, late.pk) == date(2055, 3, 1)
    assert checks(request=join) == ["unchanged", "confirmed"]
    assert checks(request=leave) == ["confirmed"]


def roster_day(campaign, snapshot):
    """The 0189 Ministry roster date of ``campaign`` in ``snapshot``."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_ministry_roster_date_v1(%s, %s)", [campaign, snapshot]
        )
        return cursor.fetchone()[0]


def held(snapshot, family, ministry, as_of):
    """The 0189 roster read for Member 3 in ``ministry`` on ``as_of``."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_response_ministry_held_v1(%s, %s, '3', %s, %s)",
            [snapshot.pk, family, ministry, as_of],
        )
        return cursor.fetchone()[0]


def test_the_roster_read_judges_each_row_on_the_day(response_service):
    """The SQL read the guard and the worklist share: a row is held on a day
    when it starts on or before it and ends after it (an end date is
    exclusive, as ``ministry_membership_is_current`` has it); no day keeps
    the stored flag (today's roster); a Member or Ministry with no evidence
    stays NULL. The Ministry roster date is the later of the campaign's
    commitment start and the snapshot's recorded refresh date (the start
    alone when none is recorded), or NULL without a commitment period."""
    harness, _ = responded(response_service, commitment=True)
    family = MinistryRequest.objects.select_related("submission").first()
    family = family.submission.family_id
    snapshot = harness.snapshot
    assert held(snapshot, family, 4, date(2054, 12, 30)) is True
    assert held(snapshot, family, 4, date(2054, 12, 31)) is False
    assert held(snapshot, family, 9, date(2054, 12, 31)) is False
    assert held(snapshot, family, 9, date(2055, 1, 1)) is True
    # No day: the stored flag, as of the refresh's date.
    assert held(snapshot, family, 4, None) is True
    assert held(snapshot, family, 9, None) is False
    # A Ministry the data does not list gives no evidence.
    assert held(snapshot, family, 77, date(2055, 1, 1)) is None
    # No refresh date recorded (or no such snapshot): the start alone.
    assert roster_day(harness.campaign.pk, snapshot.pk) == date(2055, 1, 1)
    assert roster_day(harness.campaign.pk, uuid4()) == date(2055, 1, 1)
    # No commitment period (no such campaign): no day.
    assert roster_day(uuid4(), snapshot.pk) is None


SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = SCHEMA / "migrations/0189_commitment_roster_date.sql"
ROSTER_DATE = "public.stewardship_ministry_roster_date_v1(uuid,uuid)"
HELD_READ = "public.stewardship_response_ministry_held_v1(uuid,uuid,text,integer,date)"


def test_the_do_block_refuses_a_stale_install():
    """0189's closing check passes as installed and raises once the refresh
    check guard is put back to 0159's body (which reads today's roster), a
    new read is missing or has another body, or a new read or the guard is a
    definer, as the 0137, 0138 and 0159 checks are tested."""
    text = FROZEN.read_text(encoding="utf-8")
    captured = text[text.index("SELECT set_config(") : text.index("CREATE FUNCTION")]
    check = text[text.index("DO $check$") :]
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(captured)
        cursor.execute(check)
        raise Rollback
    for statement, words in (
        (
            body(
                "stewardship_refresh_check_guard_v1",
                SCHEMA / "migrations/0159_strict_phase_order.sql",
            ),
            "refresh check guard does not read the roster",
        ),
        (f"DROP FUNCTION {ROSTER_DATE}", "stewardship_ministry_roster_date_v1"),
        (f"DROP FUNCTION {HELD_READ}", "stewardship_response_ministry_held_v1"),
        (
            "CREATE OR REPLACE FUNCTION public.stewardship_ministry_roster_date_v1("
            "campaign_uuid uuid, snapshot_uuid uuid) RETURNS date LANGUAGE sql"
            " STABLE SET search_path TO pg_catalog, public, pg_temp"
            " AS $$ SELECT NULL::date $$",
            "Ministry roster date read is not installed",
        ),
        (
            f"ALTER FUNCTION {ROSTER_DATE} SECURITY DEFINER",
            "Ministry roster date read is not installed",
        ),
        (
            f"ALTER FUNCTION {HELD_READ} SECURITY DEFINER",
            "roster read as of a date is not installed",
        ),
        (
            "ALTER FUNCTION public.stewardship_refresh_check_guard_v1()"
            " SECURITY DEFINER",
            "refresh check guard does not read the roster",
        ),
    ):
        with (
            pytest.raises(DatabaseError, match=words),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(captured)
            cursor.execute(statement)
            cursor.execute(check)
