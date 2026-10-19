"""The baseline refresh re-evaluation of final and decided items (#1689).

Every promoted refresh re-evaluates open pipeline items (review pipeline
spec, "Baseline refresh re-evaluation" and "Refresh checks"): an Entered by
hand census change is Confirmed when ParishSoft holds the reviewer's value,
Entered incorrectly when it holds a third value, and flagged Not seen in
ParishSoft after two full refreshes at its old value; an Ignored change
stays Ignored when ParishSoft reaches the Family's answer; an Edited
change compares with the reviewer's value; a Ministry request a person
accepted is Confirmed when the roster shows it, and cannot go back to
review then. The checks are written by real promotions (``prepare`` and
``promote``) and read by the real pages' row readers.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Decision, Stage
from parishkit.stewardship.reports.census_changes import census_rows
from parishkit.stewardship.reports.ministry_roster_views import roster_rows
from parishkit.stewardship.reports.ministry_worklist import (
    WorklistQuery,
    chosen,
    read_rows,
    stage_choices,
    stage_counts,
)
from parishkit.stewardship.responses.models import ProposedChange
from parishkit.stewardship.web.review_worklist import NOW_MATCHES, StaleDecision
from parishkit.stewardship.workflows.models import RefreshCheck, RefreshCheckStart

from ..policy_factory import address as rule
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_campaign_end_counts_postgresql import roster_count
from .test_census_pipeline_review_postgresql import decide as census_decide
from .test_census_review_postgresql import fresh
from .test_ministry_reports_postgresql import actor, setup
from .test_ministry_responses_postgresql import ministry_source, respond, start
from .test_ministry_review_postgresql import decide as ministry_decide
from .test_ministry_review_postgresql import requests, withdraw_leave
from .test_policy_postgresql import user
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def started():
    """The Not seen start row, which each test's flush removes again.

    Migration 0132 writes it once; a later insert is stamped now, so only
    refreshes promoted after it count, as after the release.
    """
    with connection.cursor() as cursor:
        # The migration's own row is still there before the first flush.
        cursor.execute("DELETE FROM stewardship_refresh_check_start")
        cursor.execute(
            "INSERT INTO stewardship_refresh_check_start "
            "(singleton, started_at, not_seen_after) "
            "VALUES (true, statement_timestamp(), 2)"
        )
    return RefreshCheckStart.objects.get()


# Member 3's ParishSoft prefix before the Family answers "Dr.": a value the
# census read returns, so a refresh can compare with it.
BASE = {"salutation": "Mr."}


def refresh(harness, *, member=None, joins=(), leaves=()):
    """Promote a full refresh of the Ministry fixture with Member 3 changed.

    ``member`` updates Member 3's ParishSoft record (its prefix is ``BASE``'s
    unless given); ``joins`` puts Member 3 in those Ministries' rosters and
    ``leaves`` takes Member 3 out of theirs.
    """
    data = ministry_source()
    data.members[3].update(BASE | (member or {}))
    for ministry in joins:
        data.ministry_type_memberships.setdefault(ministry, {"membership": []})[
            "membership"
        ].append(
            {
                "memberId": 3,
                "ministryRoleId": 7,
                "ministryRoleName": "Member",
                "startDate": "2026-01-01",
                "endDate": None,
            }
        )
    for ministry in leaves:
        rows = data.ministry_type_memberships[ministry]["membership"]
        rows[:] = [row for row in rows if row["memberId"] != 3]
    snapshot, claim = prepare(data)
    return promote(snapshot, claim, harness.campaign, harness.rings)


def prepared(harness):
    """A live response changing Member 3's first name and prefix, and Staff.

    As the census pipeline tests' fixture, but Member 3 has a ParishSoft
    prefix (``BASE``) before the Family answers "Dr.", so a refresh can
    compare with the value recorded at the mark.
    """
    start(harness)
    refresh(harness)
    harness = activate_response_service(harness)
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Requested"
        answers["members"]["3"]["prefix"] = "Dr."
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
    assert rows["prefix"].baseline_value == "Mr."
    return harness, user("staff@example.org").pk, rows


def census_row(harness, proposal):
    """The Census changes page's shaped row for ``proposal``, read as web."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        rows = census_rows(harness.campaign.pk)
    return next(row for row in rows if row["id"] == str(proposal.pk))


def checks(**item):
    """The item's refresh check outcomes, oldest first."""
    return list(
        RefreshCheck.objects.filter(**item)
        .order_by("created_at", "id")
        .values_list("outcome", flat=True)
    )


def test_an_entered_census_change_is_confirmed_by_the_refresh(response_service):
    """Entered by hand stays final in the census guard; the refresh that finds
    the Family's prefix in ParishSoft records a confirmed check, the change
    is Confirmed (keeping its Accepted), and it is never checked again."""
    harness, staff, rows = prepared(response_service)
    census_decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    assert census_row(harness, rows["prefix"])["stage"] == Stage.ENTERED_BY_HAND
    refresh(harness, member={"salutation": "Dr."})
    proposal = fresh(rows["prefix"])
    assert proposal.execution == "resolved_external"
    assert checks(proposal=proposal) == ["confirmed"]
    check = RefreshCheck.objects.get(proposal=proposal)
    assert check.observed == {"available": True, "value": "Dr."}
    assert check.full_refresh and check.item_version == proposal.version
    row = census_row(harness, proposal)
    assert row["stage"] == Stage.CONFIRMED
    assert row["placement"].decision is Decision.ACCEPTED
    refresh(harness, member={"salutation": "Rev."})
    assert checks(proposal=proposal) == ["confirmed"]


def test_not_seen_after_two_full_refreshes_at_the_old_value(response_service, started):
    """An entered change ParishSoft still does not show is flagged Not seen
    after two full refreshes, without changing its stage; the checks stop
    once the flag is reached, and deleting the start row turns it off."""
    harness, staff, rows = prepared(response_service)
    census_decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    refresh(harness)
    assert checks(proposal=rows["prefix"]) == ["unchanged"]
    assert not census_row(harness, rows["prefix"])["not_seen"]
    refresh(harness)
    row = census_row(harness, rows["prefix"])
    assert row["not_seen"] and row["stage"] == Stage.ENTERED_BY_HAND
    refresh(harness)
    assert checks(proposal=rows["prefix"]) == ["unchanged", "unchanged"]
    RefreshCheckStart.objects.all().delete()
    assert not census_row(harness, rows["prefix"])["not_seen"]


def test_a_third_value_is_entered_incorrectly_until_corrected(
    response_service, started
):
    """A refresh finding a value that is neither the reviewer's nor the one
    recorded at the mark makes the change Entered incorrectly, with
    ParishSoft's value shown and no action offered; correcting ParishSoft
    confirms it."""
    harness, staff, rows = prepared(response_service)
    census_decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    refresh(harness, member={"salutation": "Rev."})
    row = census_row(harness, rows["prefix"])
    assert row["stage"] == Stage.ENTERED_INCORRECTLY
    assert row["parishsoft_now"] == "Rev." and not row["not_seen"]
    # The same wrong value again records nothing new.
    refresh(harness, member={"salutation": "Rev."})
    assert checks(proposal=rows["prefix"]) == ["entered_incorrectly"]
    # ParishSoft back at the value recorded at the mark: it stays (rule 2).
    refresh(harness)
    assert checks(proposal=rows["prefix"]) == ["entered_incorrectly"]
    assert census_row(harness, rows["prefix"])["stage"] == Stage.ENTERED_INCORRECTLY
    refresh(harness, member={"salutation": "Dr."})
    assert checks(proposal=rows["prefix"]) == ["entered_incorrectly", "confirmed"]
    assert census_row(harness, rows["prefix"])["stage"] == Stage.CONFIRMED


def test_an_ignored_change_stays_ignored_when_parishsoft_matches(response_service):
    """Rule 1 for an Ignored change: ParishSoft reaching the Family's answer
    is noted, and the change is not Confirmed."""
    harness, staff, rows = prepared(response_service)
    census_decide(harness, staff, [rows["first_name"]], "ignore")
    refresh(harness, member={"firstName": "Requested"})
    proposal = fresh(rows["first_name"])
    assert (proposal.decision, proposal.execution) == ("ignored", "pending")
    assert proposal.current_value == "Requested"
    row = census_row(harness, proposal)
    assert row["stage"] == Stage.IGNORED and row["now_matches"]


def test_an_edited_change_compares_with_the_reviewers_value(response_service):
    """C is the reviewer's value: ParishSoft holding the Family's answer
    instead is a third value (Conflict), and the reviewer's value confirms."""
    harness, staff, rows = prepared(response_service)
    census_decide(harness, staff, [rows["first_name"]], "edit", value="Corrected")
    assert fresh(rows["first_name"]).admin_value == "Corrected"
    refresh(harness, member={"firstName": "Requested"})
    assert fresh(rows["first_name"]).execution == "conflict"
    refresh(harness, member={"firstName": "Corrected"})
    assert fresh(rows["first_name"]).execution == "resolved_upstream"


def test_an_edit_back_to_the_baseline_is_still_re_evaluated(response_service):
    """An Edit to ParishSoft's own value (C = A): a third value is a Conflict,
    and ParishSoft back at it holds the reviewer's value."""
    harness, staff, rows = prepared(response_service)
    baseline = rows["first_name"].baseline_value
    census_decide(harness, staff, [rows["first_name"]], "edit", value=baseline)
    assert fresh(rows["first_name"]).admin_value == baseline
    refresh(harness, member={"firstName": "Someone else"})
    assert fresh(rows["first_name"]).execution == "conflict"
    refresh(harness, member={"firstName": baseline})
    assert fresh(rows["first_name"]).execution == "resolved_upstream"


def view(harness, principal, stage="all"):
    """The Ministry worklist's rows for ``principal``, by request id."""
    query = WorklistQuery.parse({"stage": stage})
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        result = read_rows(harness.campaign.pk, query, principal)
    return {row["id"]: row for row in result["rows"]}


def test_an_accepted_request_is_confirmed_and_cannot_go_back(response_service):
    """A join Staff accepted is Confirmed by the refresh that finds the
    Member in the roster, keeping its Accepted; Back to review is no longer
    offered, so the service refuses it."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _ = requests()
    ministry_decide(harness, staff, "accept", join)
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=harness.campaign.pk
    )

    def to_enter():
        """Roster changes to enter's Not yet entered rows and menu count."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            rows, _ = roster_rows(campaign.pk, staff, "todo")
            every, _ = roster_rows(campaign.pk, staff, "all")
        return len(rows), roster_count(staff, campaign), every

    assert to_enter()[:2] == (1, 1)
    refresh(harness, joins=(9,))
    # Already in ParishSoft: nothing left to enter, still listed under All.
    todo, count, every = to_enter()
    assert (todo, count) == (0, 0)
    assert [row["confirmed"] for row in every] == [True]
    join.refresh_from_db()
    assert (join.state, join.resolution_source_id) == ("resolved", None)
    assert checks(request=join) == ["confirmed"]
    row = view(harness, staff)[str(join.pk)]
    assert row["placement"].stage is Stage.CONFIRMED
    assert row["placement"].decision is Decision.ACCEPTED
    with pytest.raises(StaleDecision):
        ministry_decide(harness, staff, "back_to_review", join, note="Undo")


def test_a_ticked_request_not_in_the_roster_is_not_seen(response_service, started):
    """Entered by hand on a join the roster still lacks is flagged Not seen
    after two full refreshes, with its own Stage choice for Staff."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _ = requests()
    ministry_decide(harness, staff, "accept", join)
    ministry_decide(harness, staff, "entered_by_hand", join)
    refresh(harness)
    assert not view(harness, staff)[str(join.pk)]["not_seen"]
    refresh(harness)
    rows = view(harness, staff)
    assert rows[str(join.pk)]["not_seen"]
    assert rows[str(join.pk)]["placement"].stage is Stage.ENTERED_BY_HAND
    counts = stage_counts(rows.values())
    assert counts["not_seen"] == 1
    assert ("not_seen", "Not seen in ParishSoft (1)") in stage_choices(counts, False)
    shown = chosen(list(view(harness, staff, "not_seen").values()), "not_seen")
    assert [row["id"] for row in shown] == [str(join.pk)]
    assert checks(request=join) == ["unchanged", "unchanged"]


def test_an_ignored_request_notes_parishsoft_now_matches(response_service):
    """An ignored join whose Member the roster now lists stays Ignored, with
    the shared line saying ParishSoft now matches the Family's answer."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _ = requests()
    ministry_decide(harness, staff, "ignore", join)
    refresh(harness, joins=(9,))
    assert checks(request=join) == ["ignored_now_matches"]
    row = view(harness, staff)[str(join.pk)]
    assert row["placement"].stage is Stage.IGNORED and row["now_matches"]
    # Decided again, the request's earlier checks no longer speak for it.
    ministry_decide(harness, staff, "back_to_review", join, note="Asked again")
    ministry_decide(harness, staff, "ignore", join)
    assert not view(harness, staff)[str(join.pk)]["now_matches"]
    refresh(harness, joins=(9,))
    assert view(harness, staff)[str(join.pk)]["now_matches"]
    # The roster dropping the Member again clears the note.
    refresh(harness)
    assert checks(request=join) == [
        "ignored_now_matches",
        "ignored_now_matches",
        "unchanged",
    ]
    assert not view(harness, staff)[str(join.pk)]["now_matches"]
    assert NOW_MATCHES == "ParishSoft now matches the Family's answer."


def test_the_guard_admits_only_the_promotion_and_keeps_checks(response_service):
    """Outside a promotion no check can be written, even by the owner; a
    check is never updated or deleted."""
    harness, staff, rows = prepared(response_service)
    census_decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    proposal = fresh(rows["prefix"])
    with pytest.raises(DatabaseError), transaction.atomic():
        RefreshCheck.objects.create(
            snapshot_id=uuid4(),
            full_refresh=True,
            proposal=proposal,
            item_version=proposal.version,
            outcome="confirmed",
            observed={"available": True, "value": "Dr."},
        )
    refresh(harness, member={"salutation": "Dr."})
    check = RefreshCheck.objects.get()
    with connection.cursor() as cursor:
        for statement in (
            "UPDATE stewardship_refresh_check SET outcome='unchanged' WHERE id=%s",
            "DELETE FROM stewardship_refresh_check WHERE id=%s",
        ):
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute(statement, [check.pk])


def test_the_worker_login_records_checks_in_promotion(response_service):
    """Promotion on the source worker's own login reads what the checks need
    and inserts them under its grants (the other tests promote as the
    database owner)."""
    harness, staff, rows = prepared(response_service)
    census_decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    data = ministry_source()
    data.members[3].update(BASE | {"salutation": "Dr."})
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    assert checks(proposal=rows["prefix"]) == ["confirmed"]


def test_the_migration_records_the_start_with_its_audit(response_service):
    """Run on a database holding live responses, 0132's start block writes
    the start row and its count-only audit event (a fresh install, with no
    response, writes the row alone)."""
    from pathlib import Path

    from parishkit.stewardship.audit.models import AuditEvent

    prepared(response_service)
    sql = (
        Path(__file__).resolve().parents[3]
        / "src/parishkit/stewardship/schema/migrations/0132_refresh_checks.sql"
    ).read_text(encoding="utf-8")
    block = sql[sql.index("DO $start$") : sql.index("$start$;") + len("$start$;")]
    RefreshCheckStart.objects.all().delete()
    with connection.cursor() as cursor:
        cursor.execute(block)
    assert RefreshCheckStart.objects.get().not_seen_after == 2
    # The fixture's flush left no earlier event behind.
    assert AuditEvent.objects.filter(event_type="refresh_checks_started").count() == 1


def test_an_edited_request_is_judged_by_the_ministry_carried_forward(
    response_service,
):
    """Staff edit the join of Food pantry into Ministry 4: the roster of the
    Family's Ministry no longer counts, the Edit's does."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join = withdraw_leave(harness)
    ministry_decide(harness, staff, "edit", join, fields={"value": ["4"]})
    assert join.reviewed_ministry_duid == 4
    refresh(harness, joins=(9,), leaves=(4,))
    assert checks(request=join) == []
    refresh(harness)
    assert checks(request=join) == ["confirmed"]
    row = view(harness, staff)[str(join.pk)]
    assert row["placement"].stage is Stage.CONFIRMED
    assert row["placement"].decision is Decision.ACCEPTED_EDITED


@pytest.mark.parametrize("stream", ["census", "ministry"])
def test_the_guard_refuses_a_forged_check_inside_a_promotion(
    response_service, monkeypatch, stream
):
    """Inside a real promotion, a check whose outcome the snapshot does not
    bear out is refused, and the whole refresh with it: nothing is written."""
    from parishkit.stewardship.responses import refresh_checks

    if stream == "census":
        harness, staff, rows = prepared(response_service)
        census_decide(harness, staff, [rows["prefix"]], "entered_by_hand")
        target = fresh(rows["prefix"])
        forged = {
            "proposal_id": target.pk,
            "observed": {"available": True, "value": "Mr."},
        }
    else:
        harness = setup(response_service)
        staff = actor(harness, {"staff"}, ())
        target, _ = requests()
        ministry_decide(harness, staff, "accept", target)
        forged = {"request_id": target.pk}

    def lying(snapshot, corpus, campaign_id, full):
        """Claim a confirmation ParishSoft does not show."""
        yield RefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            item_version=target.version,
            outcome="confirmed",
            **forged,
        )

    monkeypatch.setattr(refresh_checks, "_checks", lying)
    with pytest.raises(DatabaseError, match="differs from its"):
        refresh(harness)
    assert not RefreshCheck.objects.exists()


def test_the_worker_login_confirms_a_ministry_request(response_service):
    """The Ministry path on the worker's own login: roster ticks and checks
    are read and the confirmation inserted under its grants."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _ = requests()
    ministry_decide(harness, staff, "accept", join)
    ministry_decide(harness, staff, "entered_by_hand", join)
    data = ministry_source()
    data.members[3].update(BASE)
    data.ministry_type_memberships[9]["membership"].append(
        {
            "memberId": 3,
            "ministryRoleId": 7,
            "ministryRoleName": "Member",
            "startDate": "2026-01-01",
            "endDate": None,
        }
    )
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    assert checks(request=join) == ["confirmed"]


def test_the_quick_update_check_agrees_with_what_promotion_writes(
    response_service,
):
    """``refresh_checks_current`` answers False exactly while a promotion of
    the same corpus would write a check, and True once it has."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.responses.refresh_checks import (
        refresh_checks_current,
    )
    from parishkit.stewardship.source.families import PreparedPromotion

    harness, staff, rows = prepared(response_service)
    census_decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    data = ministry_source()
    data.members[3].update({"salutation": "Dr."})
    snapshot, claim = prepare(data)

    def current(value):
        """The check for ``value``'s own corpus."""
        corpus = PreparedPromotion.of(value).corpus
        with work_transaction():
            return refresh_checks_current(
                value, corpus, campaign_id=harness.campaign.pk
            )

    assert not current(snapshot)
    promote(snapshot, claim, harness.campaign, harness.rings)
    snapshot.refresh_from_db()
    assert checks(proposal=rows["prefix"]) == ["confirmed"]
    assert current(snapshot)


# Forged checks the guard refuses inside a real promotion: (stream, the
# check's fields over a truthful unchanged one, the refusal's words, the
# refresh). ``second`` first lets a real refresh confirm the change.
FORGERIES = {
    "census value not the snapshot's": (
        "census",
        {"observed": {"available": True, "value": "Sir"}},
        "its source value",
        {},
    ),
    "census stale version": ("census", {"item_version": 99}, "its source value", {}),
    "census full flag not the lease's": (
        "census",
        {"full_refresh": False},
        "promoting source worker",
        {},
    ),
    "census checked after Confirmed": (
        "census",
        {"outcome": "confirmed", "observed": {"available": True, "value": "Dr."}},
        "not checked again",
        {"member": {"salutation": "Dr."}, "second": True},
    ),
    "ministry entered incorrectly": (
        "ministry",
        {"outcome": "entered_incorrectly"},
        "decided request",
        {},
    ),
    "ministry ignored note on an accepted request": (
        "ministry",
        {"outcome": "ignored_now_matches"},
        "decided request",
        {},
    ),
    "ministry stale version": ("ministry", {"item_version": 99}, "decided request", {}),
    "ministry confirmed on an ignored request": (
        "ministry",
        {"outcome": "confirmed", "ignored": True},
        "decided request",
        {"joins": (9,)},
    ),
    "ministry resolved by the refresh": (
        "ministry",
        {"outcome": "confirmed", "leave": True},
        "decided request",
        {"leaves": (4,)},
    ),
}


@pytest.mark.parametrize("case", sorted(FORGERIES))
def test_the_guard_refuses_each_forgery_inside_a_promotion(
    response_service, monkeypatch, case
):
    """Each of the guard's refusals, met inside a real promotion: the refresh
    is rolled back and no forged check is written."""
    from parishkit.stewardship.responses import refresh_checks

    stream, fields, words, options = FORGERIES[case]
    fields = dict(fields)
    options = dict(options)
    if stream == "census":
        harness, staff, rows = prepared(response_service)
        census_decide(harness, staff, [rows["prefix"]], "entered_by_hand")
        target = fresh(rows["prefix"])
        base = {
            "proposal_id": target.pk,
            "outcome": "unchanged",
            "observed": {"available": True, "value": "Mr."},
        }
    else:
        harness = setup(response_service)
        staff = actor(harness, {"staff"}, ())
        join, leave = requests()
        if fields.pop("ignored", False):
            ministry_decide(harness, staff, "ignore", join)
        else:
            ministry_decide(harness, staff, "accept", join)
        target = leave if fields.pop("leave", False) else join
        base = {"request_id": target.pk, "outcome": "unchanged"}
    member = options.pop("member", None)
    if options.pop("second", False):
        refresh(harness, member=member)
    before = RefreshCheck.objects.count()

    def forged(snapshot, corpus, campaign_id, full):
        """One check that the snapshot does not bear out."""
        target.refresh_from_db()
        yield RefreshCheck(
            **(
                base
                | {
                    "snapshot_id": snapshot.pk,
                    "full_refresh": full,
                    "item_version": target.version,
                }
                | fields
            )
        )

    monkeypatch.setattr(refresh_checks, "_checks", forged)
    with pytest.raises(DatabaseError, match=words):
        refresh(harness, member=member, **options)
    assert RefreshCheck.objects.count() == before
