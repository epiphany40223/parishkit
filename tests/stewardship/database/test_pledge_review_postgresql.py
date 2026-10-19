"""Pledges under the real web role (#1686).

Frozen migration 0135 (provisional number,
``schema/migrations/0135_pledge_review.sql``) adds the financial stream's
review history, the item selection the page and the guard share, and the
guard. These tests run the decision service, the page, its download and the
SQL guard as installed: the stages a decision and a Family's resubmission
leave, the audit, the races and replays, the guard's refusals of rows the
service would never write, and who may see the page.
"""

import csv
import io
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Action
from parishkit.stewardship.reports.pledge_models import PledgeReview
from parishkit.stewardship.reports.pledge_review import PledgeDecisions, read_items
from parishkit.stewardship.web.review_worklist import StaleDecision, WorklistDecision

from ..policy_factory import address
from ..test_financial_answers import CHECK, OPTIONS
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_financial_report_postgresql import pledge
from .test_financial_source_postgresql import financial_source
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_responses_postgresql import revisit
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0135_pledge_review.sql"
)
ROUTE = "admin:pledges_to_enter"


def pledged(response_service, **answer):
    """The harness campaign with Financial stewardship and one live pledge."""
    harness = response_service
    financial_source(harness, options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        submission = pledge(harness, load_form(harness), shares={CHECK: ""}, **answer)
    return harness, submission


def again(harness, **answer):
    """The Family answers again through a new session, with this pledge."""
    with web_login():
        return pledge(harness, revisit(harness), shares={CHECK: ""}, **answer)


def admin():
    """The harness's Administrator, as a principal the service reloads."""
    found = PortalUser.objects.filter(email="admin@example.org").first()
    return found.pk if found else user("admin@example.org").pk


class Actor:
    """The smallest principal the service needs: it reloads the rest itself."""

    def __init__(self, identity):
        self.identity = identity


def staff_browser(harness, google):
    """Sign in staff@example.org through an exact-address Staff rule."""
    store = harness.service.store
    rule = address("staff@example.org", roles=("staff",))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    browser, login = signed_in()
    assert login.status_code == 302
    return browser, PortalUser.objects.get(google_subject="synthetic-staff").pk


def state(harness, submission):
    """The item of ``submission`` as the shared selection reads it."""
    return read_items(harness.campaign.pk).get(submission.pk)


def decide(harness, actor, action, submissions, *, note="", key=None, **fields):
    """One decision through the service on the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        items = read_items(harness.campaign.pk)
        versions = {str(s.pk): items[s.pk].sequence for s in submissions}
        decision = WorklistDecision(
            Action(action),
            versions,
            note,
            key or uuid4(),
            bulk=len(submissions) > 1,
            fields={name: [value] for name, value in fields.items()},
        )
        return PledgeDecisions(harness.service.store, harness.campaign.pk).decide(
            Actor(actor), decision
        )


def test_decisions_move_the_pledge_through_its_stages_with_audit(
    response_service,
):
    """Accept, Entered by hand; each a row in sequence, with its audit event.

    The answer recorded is the response's own, set by the guard; the mark
    keeps the decision and records no ParishSoft pledge.
    """
    harness, submission = pledged(response_service)
    actor = admin()
    assert state(harness, submission).stage == "to_review"
    assert decide(harness, actor, "accept", [submission]) == "The pledge accepted."
    assert state(harness, submission).stage == "to_enter_by_hand"
    decide(harness, actor, "entered_by_hand", [submission])
    found = state(harness, submission)
    assert (found.stage, found.sequence) == ("entered_by_hand", 2)
    rows = list(PledgeReview.objects.order_by("sequence"))
    assert [(r.action, r.decision, r.entered) for r in rows] == [
        ("accept", "accepted", False),
        ("entered_by_hand", "accepted", True),
    ]
    assert rows[0].answer == {
        "annual_pledge": "1234.50",
        "frequency": "monthly",
        "cannot_give": False,
        "shares": {CHECK: ""},
    }
    assert not rows[1].marked_pledge_known and rows[1].marked_pledge is None
    contexts = [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(
            event_type="pledge_review_updated"
        ).order_by("created_at")
    ]
    assert contexts == [
        {"outcome": "changed", "before_version": 0, "after_version": 1},
        {"outcome": "changed", "before_version": 1, "after_version": 2},
    ]
    # The mark is final: nothing more is offered.
    with pytest.raises(ValueError):
        decide(harness, actor, "back_to_review", [submission])


def test_edit_records_the_reviewers_value_and_a_match_is_an_accept(response_service):
    """An Edit carries forward its amount and frequency; the Family's stays."""
    harness, submission = pledged(response_service)
    actor = admin()
    notice = decide(
        harness,
        actor,
        "edit",
        [submission],
        value_amount="1,300",
        value_frequency="quarterly",
    )
    assert notice == "The pledge accepted (edited)."
    row = PledgeReview.objects.get()
    assert (row.decision, row.reviewed_annual_pledge, row.reviewed_frequency) == (
        "accepted_edited",
        Decimal("1300.00"),
        "quarterly",
    )
    decide(harness, actor, "back_to_review", [submission])
    assert state(harness, submission).stage == "to_review"
    # The Family's own value, "edited" in, is recorded as what it is.
    notice = decide(
        harness,
        actor,
        "edit",
        [submission],
        value_amount="1234.50",
        value_frequency="monthly",
    )
    assert notice.endswith("accepted: the value matched the Family's answer.")
    assert PledgeReview.objects.order_by("-sequence").first().action == "accept"


def test_a_family_answering_again_keeps_or_reopens_its_pledge(response_service):
    """Rule 1: the same pledge keeps its decision; rule 2: a changed one reopens,
    even if a later response returns to the decided answer."""
    harness, submission = pledged(response_service)
    actor = admin()
    decide(harness, actor, "ignore", [submission])
    same = again(harness)
    found = state(harness, same)
    assert (found.stage, found.in_force, found.sequence) == ("ignored", True, 1)
    # A decision posted against the earlier response is stale now.
    with (
        pytest.raises(StaleDecision),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        PledgeDecisions(harness.service.store, harness.campaign.pk).decide(
            Actor(actor),
            WorklistDecision(
                Action.BACK_TO_REVIEW, {str(submission.pk): 1}, "why", uuid4()
            ),
        )
    changed = again(harness, annual_pledge="99")
    assert state(harness, changed).stage == "to_review"
    back = again(harness)
    found = state(harness, back)
    assert (found.stage, found.in_force) == ("to_review", False)
    # Deciding it again starts from the current response.
    decide(harness, actor, "accept", [back])
    assert state(harness, back).stage == "to_enter_by_hand"


def test_bulk_is_all_or_none_and_a_replay_records_nothing_new(response_service):
    """A bulk decision with one stale item records nothing; a resend is a replay."""
    harness, submission = pledged(response_service)
    actor = admin()
    key = uuid4()
    decide(harness, actor, "accept", [submission], key=key)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # The same post again (version 0, same key) is the replay.
        notice = PledgeDecisions(harness.service.store, harness.campaign.pk).decide(
            Actor(actor),
            WorklistDecision(Action.ACCEPT, {str(submission.pk): 0}, "", key),
        )
    assert notice == "The pledge accepted."
    assert PledgeReview.objects.count() == 1
    with (
        pytest.raises(StaleDecision),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        PledgeDecisions(harness.service.store, harness.campaign.pk).decide(
            Actor(actor),
            WorklistDecision(Action.IGNORE, {str(submission.pk): 0}, "", uuid4()),
        )
    assert PledgeReview.objects.count() == 1


def test_reopening_an_ignored_pledge_needs_a_note(response_service):
    """Back to review on Ignored is refused without a reason."""
    harness, submission = pledged(response_service)
    actor = admin()
    decide(harness, actor, "ignore", [submission])
    with pytest.raises(ValueError):
        decide(harness, actor, "back_to_review", [submission])
    decide(harness, actor, "back_to_review", [submission], note="Not a duplicate")
    assert state(harness, submission).stage == "to_review"


def forge(submission, actor, **values):
    """Insert a review row directly as the web login, with no audit."""
    row = dict(sequence=1, action="accept", decision="accepted", entered=False)
    row |= values
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_pledge_review (id,correlation_id,actor_id,"
            "family_id,submission_id,sequence,request_key,action,decision,"
            "reviewed_annual_pledge,reviewed_frequency,entered,answer,"
            "marked_pledge_known,note) VALUES (gen_random_uuid(),"
            "gen_random_uuid(),%s,%s,%s,%s,gen_random_uuid(),%s,%s,%s,%s,%s,"
            "'{}',%s,%s) RETURNING id",
            [
                actor,
                submission.family_id,
                submission.pk,
                row["sequence"],
                row["action"],
                row["decision"],
                row.get("amount"),
                row.get("frequency"),
                row["entered"],
                row.get("known", False),
                row.get("note", ""),
            ],
        )
        return cursor.fetchone()[0]


GUARD = "Pledge review lacks current authority, version or state"
# Each forged row, and the refusal that names why: the guard's, a check
# constraint's, or (for a row otherwise valid) the deferred audit check at
# commit. Every other case is refused at the INSERT itself.
FORGED = {
    "no_order": ({}, "Pledge review requires ordered admission"),
    "sequence": ({"sequence": 2}, GUARD),
    "leader": ({}, GUARD),
    "stage": ({"action": "entered_by_hand", "entered": True}, GUARD),
    "known": (
        {"sequence": 2, "action": "entered_by_hand", "entered": True, "known": True},
        GUARD,
    ),
    "no_frequency": (
        {"action": "edit", "decision": "accepted_edited", "amount": "500.00"},
        "pledge_review_value_shape",
    ),
    "null_decision": ({"decision": None}, "pledge_review_action_state"),
    "back_with_values": (
        {
            "sequence": 2,
            "action": "back_to_review",
            "decision": None,
            "amount": "500.00",
            "frequency": "weekly",
        },
        "pledge_review_value_shape",
    ),
    "mark_other_decision": (
        {
            "sequence": 2,
            "action": "entered_by_hand",
            "decision": "accepted_edited",
            "amount": "500.00",
            "frequency": "weekly",
            "entered": True,
        },
        GUARD,
    ),
    "reopen_without_note": (
        {"sequence": 2, "action": "back_to_review", "decision": None},
        GUARD,
    ),
    "long_note": ({"note": "x" * 2001}, GUARD),
    "audit": ({}, "Pledge review requires its audit"),
}


PRIOR = {
    "known": "accept",
    "back_with_values": "accept",
    "mark_other_decision": "accept",
    "reopen_without_note": "ignore",
}


@pytest.mark.parametrize("case", sorted(FORGED))
def test_the_guard_refuses_rows_the_service_never_writes(
    response_service, google, case
):
    """Outside the writers' order, at another version, from a Ministry leader,
    at a stage that does not offer it, with a ParishSoft pledge, an Edit
    above zero with no frequency, an action with no decision, Back to review
    carrying values, a mark changing the decision, an ignored pledge reopened
    without a note, a note too long, or without its audit event, the
    database refuses the row, each for its own reason."""
    harness, submission = pledged(response_service)
    actor = admin()
    # The decision each case starts from, so the forged row's action is
    # offered and only the check under test can refuse it.
    first = PRIOR.get(case)
    if first:
        decide(harness, actor, first, [submission])
    if case == "leader":
        _browser, actor, _rule, _ = leader(harness, google)
    values, reason = FORGED[case]
    at_insert = case != "audit"
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match=reason),
        transaction.atomic() if case == "no_order" else work_transaction(),
    ):
        try:
            forge(submission, actor, **values)
        except DatabaseError:
            assert at_insert
            raise
        assert not at_insert
    assert PledgeReview.objects.count() == (1 if first else 0)


def test_review_rows_are_immutable(response_service):
    """Neither web nor the owner may change or delete a decision."""
    harness, submission = pledged(response_service)
    decide(harness, admin(), "accept", [submission])
    for statement in (
        "UPDATE stewardship_pledge_review SET note='x'",
        "DELETE FROM stewardship_pledge_review",
    ):
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
    assert PledgeReview.objects.get().note == ""


def test_the_files_do_block_refuses_a_missing_guard(response_service):
    """The migration's own check fails if the guard is not installed."""
    body = FROZEN.read_text(encoding="utf-8")
    check = body[body.index("DO $check$") :]
    with (
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "DROP TRIGGER stewardship_pledge_review_effect ON stewardship_pledge_review"
        )
        cursor.execute(check)


def test_the_page_lists_decides_and_downloads_for_staff_only(response_service, google):
    """Staff see To review by default, decide in place by POST and download
    the list; a GET may not search, and a Ministry leader is denied."""
    harness, submission = pledged(response_service)
    route = reverse(ROUTE)
    browser, staff = staff_browser(harness, google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, route)
        assert response.status_code == 200
        assert b"To review (1)" in body and b"$1,234.50, Monthly" in body
        assert f'value="{submission.pk}:0"'.encode() in body
        assert get(browser, route + "?search=Example")[0].status_code == 400
        assert get(browser, route + "?stage=to_enter_by_hand")[0].status_code == 200
        response, body = search(
            browser,
            route,
            {
                "bulk": "accept",
                "bulk_key": str(uuid4()),
                "bulk_note": "",
                "item": f"{submission.pk}:0",
                "stage": "to_review",
                "search": "",
            },
        )
        assert response.status_code == 200
        assert b"accepted." in body and b"To enter by hand (1)" in body
        response, body = search(
            browser,
            reverse("admin:pledges_to_enter_export"),
            {"stage": "to_enter_by_hand", "search": "", "format": "csv"},
        )
        assert response.status_code == 200
        rows = list(csv.reader(io.StringIO(body.decode())))
        assert rows[0][:2] == ["Family", "Family DUID"] and len(rows) == 2
        assert rows[1][8:12] == [
            "$1,234.50",
            "Monthly",
            "To enter by hand",
            "Accepted",
        ]
    # An Edit value the server refuses is answered at its field.
    decide(harness, staff, "back_to_review", [submission])
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(
            browser,
            route,
            {
                "decide": "edit",
                "item": str(submission.pk),
                "version": "2",
                "request_key": str(uuid4()),
                "note": "",
                "value_amount": "100",
                "value_frequency": "",
                "stage": "to_review",
                "search": "",
            },
        )
        assert response.status_code == 400
        assert b'"field_id": "value_frequency"' in body
    assert PledgeReview.objects.filter(actor_id=staff).count() == 2
    assert AuditEvent.objects.filter(event_type="pledges_to_enter_viewed").exists()
    assert AuditEvent.objects.filter(event_type="pledges_to_enter_exported").exists()
    head, _actor, _rule, _ = leader(harness, google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert get(head, route)[0].status_code == 403


def test_limitations_lists_cannot_contribute_at_its_pledges_stage(response_service):
    """A Family's Cannot contribute financially answer is a Limitations row
    with its Pledges stage, a link to Pledges and no action (R39 finding
    11), and the Limitations menu count includes it while it is To review."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts import admin_context
    from parishkit.stewardship.accounts.policy import Principal
    from parishkit.stewardship.reports.limitation_worklist import (
        answer_rows,
        at_stage,
        worklist_rows,
    )
    from parishkit.stewardship.reports.limitations import (
        LimitationsQuery,
        limitations_report,
    )

    harness = response_service
    financial_source(harness, options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        submission = pledge(
            harness,
            load_form(harness),
            annual_pledge="",
            frequency="",
            shares={},
            cannot_give=True,
        )
    staff = Principal(admin(), frozenset({"staff"}), frozenset())
    items = [
        SimpleNamespace(name="limitations_report", url="/admin/reports/limitations/"),
        SimpleNamespace(
            name="pledges_to_enter", url="/admin/reports/financial/pledges/"
        ),
    ]

    def read(**filters):
        """The Limitations report and the menu's count, as the web role reads them."""
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            transaction.atomic(),
        ):
            result = limitations_report(
                harness.campaign.pk, LimitationsQuery.parse(filters), staff
            )
            counts = admin_context._open_counts(
                SimpleNamespace(), staff, items, harness.campaign
            )
        return result, counts.get("limitations_report")

    result, count = read()
    (row,) = result["contributions"]
    assert (row["kind"], row["pledge_item"]) == ("cannot_give", str(submission.pk))
    assert row["pledges_url"] == reverse(ROUTE) + "?stage=to_review"
    rows = answer_rows(result)
    shown, counts = at_stage(rows, "to_review")
    assert count == counts["to_review"] == len(shown)
    (drawn,) = [r for r in worklist_rows(shown) if r["kind"] == "cannot_give"]
    assert drawn["family_answer"] == "Cannot contribute financially"
    assert drawn["elsewhere"]["label"] == "Open in Pledges"
    # A menu that does not offer Pledges (a viewer without FINANCIAL_DETAIL)
    # leaves the Cannot contribute row out of the count (pledges false).
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        without = admin_context._open_counts(
            SimpleNamespace(), staff, items[:1], harness.campaign
        ).get("limitations_report")
    assert (without or 0) == count - 1
    # Decided on Pledges, it follows its Pledges stage and leaves the count.
    decide(harness, admin(), "accept", [submission])
    result, after = read()
    (row,) = result["contributions"]
    assert row["placement"].stage.value == "to_enter_by_hand"
    assert (after or 0) == count - 1
    # Show narrowed to a Limitations answer lists none.
    assert read(show="cannot_attend")[0]["contributions"] == []
