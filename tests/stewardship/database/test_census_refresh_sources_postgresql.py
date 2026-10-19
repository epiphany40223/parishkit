"""Refresh checks of the census fields the contact read does not carry (#1738).

The review pipeline (spec, "Refresh checks") confirms an entered move, death
and email opt-out against their own reading of the promoted data, and keeps
an open change whose Member left the Family at its stage (rule 4) rather
than cancelling it. The checks are written by real promotions (``prepare``
and ``promote``), under migration 0141's guard, and read by the real page's
row reader.
"""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.responses.models import ProposedChange
from parishkit.stewardship.responses.refresh_checks import census_source
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.workflows.models import RefreshCheck

from ..policy_factory import address as rule
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_census_pipeline_review_postgresql import decide
from .test_census_review_postgresql import fresh
from .test_ministry_responses_postgresql import ministry_source, respond, start
from .test_policy_postgresql import user
from .test_refresh_checks_postgresql import (  # noqa: F401 (started: fixture)
    BASE,
    census_row,
    checks,
    started,
)
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import TODAY, prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0141_census_refresh_sources.sql"


def refresh(harness, *, member=None, family=None):
    """Promote a full refresh with Member 3 and Family 1 changed.

    ``member`` updates Member 3's ParishSoft record (over the shared
    fixture's prefix) and ``family`` Family 1's.
    """
    data = ministry_source()
    data.members[3].update(BASE | (member or {}))
    data.families[1].update(family or {})
    snapshot, claim = prepare(data)
    return promote(snapshot, claim, harness.campaign, harness.rings)


def answered(harness, edit):
    """A live response whose answers ``edit`` changed, its rows, and Staff.

    The promoted data before the Family answers has Family 1 opted in
    (``sendNoMail`` false), so a refresh can compare with it.
    """
    start(harness)
    refresh(harness, family={"sendNoMail": False})
    harness = activate_response_service(harness)
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        edit(answers)
        respond(harness, form, answers)
    admin = user("admin@example.org")
    store = harness.service.store
    change(
        store,
        store.active(),
        admin.pk,
        [
            {
                "operation": "add",
                "section": "login_rules",
                **rule("staff@example.org", ("staff",)),
            }
        ],
    )
    rows = {row.field: row for row in ProposedChange.objects.all()}
    return harness, user("staff@example.org").pk, rows


def terminal(**request):
    """An edit asking for one terminal request (moved, deceased) on Member 3."""

    def edit(answers):
        """Replace Member 3's answer with the request, as the form sends it:
        a terminal Member has no Ministry choices."""
        answers["members"]["3"] = request | {"confirmed": True}
        answers["ministries"]["members"] = {}

    return edit


def opt_out(answers):
    """The Family asks to opt out of all parish emails."""
    answers["family"]["email_opt_out"] = True


def first_name(answers):
    """The Family changes Member 3's first name (an Automatic change)."""
    answers["members"]["3"]["first_name"] = "Requested"


def entered(harness, staff, row):
    """Mark ``row`` Entered by hand (accepting it in the same step)."""
    decide(harness, staff, [row], "entered_by_hand")
    return fresh(row)


@pytest.mark.parametrize(
    "leaving",
    [{"familyDUID": 2}, {"memberStatus": "Inactive"}],
    ids=["moved", "inactive"],
)
def test_an_entered_move_is_confirmed_when_the_member_leaves(
    response_service,
    started,  # noqa: F811
    leaving,
):
    """A move ParishSoft does not show yet is unchanged (true only from the
    data's own reading); the refresh that no longer lists the Member as an
    active Member of the Family confirms it, and it is not checked again."""
    harness, staff, rows = answered(response_service, terminal(moved_household=True))
    proposal = entered(harness, staff, rows["moved_household"])
    refresh(harness)
    assert checks(proposal=proposal) == ["unchanged"]
    assert RefreshCheck.objects.get(proposal=proposal).observed == {
        "available": True,
        "value": False,
    }
    refresh(harness, member=leaving)
    assert checks(proposal=proposal) == ["unchanged", "confirmed"]
    row = census_row(harness, proposal)
    assert row["stage"] == Stage.CONFIRMED
    assert row["member_status"] in {"moved", "inactive"}
    refresh(harness)
    assert checks(proposal=proposal) == ["unchanged", "confirmed"]


def test_a_death_is_no_evidence_of_a_move(response_service):
    """A Member marked deceased gets no move check: the move stays Entered
    by hand."""
    harness, staff, rows = answered(response_service, terminal(moved_household=True))
    proposal = entered(harness, staff, rows["moved_household"])
    refresh(harness, member={"memberStatus": "Deceased"})
    assert checks(proposal=proposal) == []
    assert census_row(harness, proposal)["stage"] == Stage.ENTERED_BY_HAND


def test_an_entered_death_and_its_date_are_confirmed(response_service):
    """The deceased flag and the death date, each read from the data while
    the Member is still in the Family, confirm both changes."""
    harness, staff, rows = answered(
        response_service,
        terminal(deceased_status=True, death_date="2026-01-01"),
    )
    status = entered(harness, staff, rows["deceased_status"])
    date = entered(harness, staff, rows["death_date"])
    refresh(harness, member={"memberStatus": "Deceased", "dateOfDeath": "2026-01-01"})
    assert checks(proposal=status) == ["confirmed"]
    assert checks(proposal=date) == ["confirmed"]
    assert RefreshCheck.objects.get(proposal=status).observed == {
        "available": True,
        "value": True,
    }
    for proposal in (status, date):
        assert census_row(harness, proposal)["stage"] == Stage.CONFIRMED


def test_a_death_needs_the_member_in_the_family(response_service):
    """A Member now in another Family gives no evidence about the death."""
    harness, staff, rows = answered(
        response_service, terminal(deceased_status=True, death_date="")
    )
    proposal = entered(harness, staff, rows["deceased_status"])
    refresh(harness, member={"familyDUID": 2, "memberStatus": "Deceased"})
    assert checks(proposal=proposal) == []


def test_an_entered_opt_out_is_confirmed_by_send_no_mail(response_service, started):  # noqa: F811
    """The Family's sendNoMail flag confirms the entered opt-out. Its
    baseline was never read, so the old flag is unchanged and, after two
    full refreshes, Not seen; never Entered incorrectly."""
    harness, staff, rows = answered(response_service, opt_out)
    proposal = entered(harness, staff, rows["email_opt_out"])
    assert not proposal.current_available
    refresh(harness, family={"sendNoMail": False})
    refresh(harness, family={"sendNoMail": False})
    assert checks(proposal=proposal) == ["unchanged", "unchanged"]
    row = census_row(harness, proposal)
    assert row["not_seen"] and row["stage"] == Stage.ENTERED_BY_HAND
    assert row["parishsoft_now"] == "No"
    refresh(harness, family={"sendNoMail": True})
    assert checks(proposal=proposal) == ["unchanged", "unchanged", "confirmed"]
    assert census_row(harness, proposal)["stage"] == Stage.CONFIRMED


def test_an_opt_out_without_the_flag_gets_no_check(response_service):
    """A Family record without sendNoMail is no evidence either way."""
    harness, staff, rows = answered(response_service, opt_out)
    proposal = entered(harness, staff, rows["email_opt_out"])
    refresh(harness)
    assert checks(proposal=proposal) == []


@pytest.mark.parametrize(
    "leaving,status",
    [
        ({"familyDUID": 2}, "moved"),
        ({"memberStatus": "Inactive"}, "inactive"),
        ({"memberStatus": "Deceased"}, "deceased"),
    ],
)
def test_an_open_change_whose_member_left_keeps_its_stage(
    response_service, leaving, status
):
    """Rule 4: the refresh no longer cancels an open change whose Member
    left the Family; it is left as it was, at its version, and the page
    marks the Member with the shared status."""
    harness, staff, rows = answered(response_service, first_name)
    accepted = fresh(rows["first_name"])
    decide(harness, staff, [accepted], "accept")
    accepted = fresh(accepted)
    refresh(harness, member=leaving | {"firstName": "Someone else"})
    kept = fresh(accepted)
    assert (kept.execution, kept.decision, kept.version) == (
        accepted.execution,
        "approved",
        accepted.version,
    )
    assert kept.current_value == accepted.current_value
    row = census_row(harness, kept)
    assert row["stage"] == Stage.READY_TO_UPDATE
    assert row["member_status"] == status
    # Back in the Family, the change is compared again as before.
    refresh(harness, member={"firstName": "Requested"})
    assert fresh(kept).execution == "resolved_upstream"


@pytest.mark.parametrize(
    "outcome,observed,words",
    [
        ("unchanged", {"available": True, "value": True}, "its source value"),
        ("confirmed", {"available": True, "value": False}, "evidence"),
        ("entered_incorrectly", {"available": True, "value": False}, "evidence"),
    ],
)
def test_the_guard_refuses_a_forged_move_check(
    response_service, monkeypatch, outcome, observed, words
):
    """Migration 0141's guard: a move's check must carry the snapshot's own
    reading, and a yes-or-no request is never Entered incorrectly."""
    from parishkit.stewardship.responses import refresh_checks

    harness, staff, rows = answered(response_service, terminal(moved_household=True))
    target = entered(harness, staff, rows["moved_household"])

    def forged(snapshot, corpus, campaign_id, full):
        """One check of the move that the snapshot does not bear out."""
        yield RefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            proposal_id=target.pk,
            item_version=target.version,
            outcome=outcome,
            observed=observed,
        )

    monkeypatch.setattr(refresh_checks, "_checks", forged)
    with pytest.raises(DatabaseError, match=words):
        refresh(harness)
    assert RefreshCheck.objects.count() == 0


def test_the_guard_refuses_an_opt_out_check_of_another_family(
    response_service, monkeypatch
):
    """An opt-out check's value is the change's own Family's flag."""
    from parishkit.stewardship.responses import refresh_checks

    harness, staff, rows = answered(response_service, opt_out)
    target = entered(harness, staff, rows["email_opt_out"])

    def forged(snapshot, corpus, campaign_id, full):
        """A confirmed check while the Family's flag is still false."""
        yield RefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            proposal_id=target.pk,
            item_version=target.version,
            outcome="confirmed",
            observed={"available": True, "value": True},
        )

    monkeypatch.setattr(refresh_checks, "_checks", forged)
    with pytest.raises(DatabaseError, match="its source value"):
        refresh(harness, family={"sendNoMail": False})
    assert RefreshCheck.objects.count() == 0


# The data variants the SQL twin is compared over: Member 3's record and
# Family 1's, and whether Member 3 is in the data at all.
VARIANTS = {
    "current": ({}, {"sendNoMail": True}, True),
    "moved": ({"familyDUID": 2}, {"sendNoMail": True}, True),
    "inactive": ({"memberStatus": "Inactive"}, {"sendNoMail": False}, True),
    "deceased": (
        {"memberStatus": "Deceased", "dateOfDeath": "2026-01-01"},
        {"sendNoMail": True},
        True,
    ),
    "removed": ({}, {}, False),
    "moved and deceased": (
        {"familyDUID": 2, "memberStatus": "Deceased"},
        {"sendNoMail": True},
        True,
    ),
    "moved and inactive": (
        {"familyDUID": 2, "memberStatus": "Inactive"},
        {"sendNoMail": True},
        True,
    ),
    "family inactive": ({}, {"sendNoMail": True, "famGroupID": 8}, True),
}
# (kind, key, field) of each census change the twins read for Family 1.
READS = (
    ("member", "3", "moved_household"),
    ("member", "3", "deceased_status"),
    ("member", "3", "death_date"),
    ("member", "3", "first_name"),
    ("family", "1", "email_opt_out"),
    ("family", "2", "email_opt_out"),
)


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_the_sql_source_agrees_with_census_source(response_service, variant):
    """Migration 0141's stewardship_refresh_census_source_v1 returns exactly
    what ``refresh_checks.census_source`` reads from the same promoted data,
    for every field it reads, so the guard never refuses a check the
    promotion owes (which would roll the whole refresh back)."""
    harness, _, rows = answered(response_service, opt_out)
    family = rows["email_opt_out"].submission.family_id
    member, household, present = VARIANTS[variant]
    data = ministry_source()
    data.members[3].update(BASE | member)
    data.families[1].update(household)
    data.family_groups[8] = "Inactive"
    if not present:
        del data.members[3]
        data.member_contactinfos.clear()
        for roster in data.ministry_type_memberships.values():
            roster["membership"] = []
        # Family 1 keeps an active Member, so it stays in the data.
        data.members[4] = {**ministry_source().members[3], "memberDUID": 4}
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    corpus = normalize_core(replace(data, organization_id=12345), as_of=TODAY)
    for kind, key, field in READS:
        row = SimpleNamespace(
            entity_kind=kind, entity_key=key, field=field, family_duid=1
        )
        expected = census_source(row, corpus)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_refresh_census_source_v1(%s,%s,%s,%s,%s)",
                [snapshot.pk, family, kind, key, field],
            )
            (found,) = cursor.fetchone()
        found = json.loads(found) if isinstance(found, str) else found
        # A value ParishSoft did not report is no evidence either way: the
        # Python side records no check, and the guard admits none.
        if found is not None and not found["available"]:
            found = None
        assert found == (
            None
            if expected is None
            else {"available": expected.available, "value": expected.value}
        ), (variant, kind, key, field)


def _check():
    """0141's closing DO block, as the file has it."""
    text = FROZEN.read_text(encoding="utf-8")
    return text[text.index("DO $check$") :]


def _previous_guard():
    """0136's refresh check guard, as a replacement statement."""
    text = (MIGRATIONS / "0136_refresh_checks.sql").read_text(encoding="utf-8")
    start = text.index("CREATE FUNCTION public.stewardship_refresh_check_guard_v1")
    end = text.index("END $$;", start) + len("END $$;")
    return "CREATE OR REPLACE" + text[start + len("CREATE") : end]


def test_do_block_refuses_the_old_definitions(db):
    """The closing check passes as installed and raises once the guard is put
    back to 0136's body, its trigger is disabled, or the source function is
    made SECURITY DEFINER."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(_check())
    for statement, words in (
        (_previous_guard(), "refresh check guard"),
        (
            "ALTER TABLE public.stewardship_refresh_check"
            " DISABLE TRIGGER stewardship_refresh_check_guard",
            "refresh check guard",
        ),
        (
            "ALTER FUNCTION public.stewardship_refresh_census_source_v1"
            "(uuid,uuid,text,text,text) SECURITY DEFINER",
            "census refresh source",
        ),
    ):
        with (
            pytest.raises(DatabaseError, match=words),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
            cursor.execute(_check())
