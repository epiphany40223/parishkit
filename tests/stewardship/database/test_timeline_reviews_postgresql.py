"""The Timeline's review lines under the real web login (#1774).

Each stream's decisions are made through the real review services, then the
review read (``timeline_reviews.read_reviews``) runs as the restricted web
login and the hub page is fetched: for an Administrator and for Staff, who
see the full timeline since the Administrator's decision of 2026-10-19,
with the review statement run only while the Timeline tab is chosen and
under the page's one audited view. Ministry leaders still cannot open it
(``test_timelines_postgresql``).
"""

import re
from html import unescape
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.response_metrics import ResponseScope
from parishkit.stewardship.reports.timeline_reviews import read_reviews
from parishkit.stewardship.reports.timelines import scope_values
from parishkit.stewardship.responses.information import update_information
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    ProposedChange,
)

from ..test_financial_answers import OTHER
from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_census_add_page_postgresql import worklist
from .test_census_additions_postgresql import add, campaign_of, member_form
from .test_census_edit_postgresql import edit
from .test_census_pipeline_review_postgresql import prepared as pipeline_prepared
from .test_census_resolution_postgresql import prepared, resolve
from .test_census_review_postgresql import fresh, review
from .test_member_requests_postgresql import revisit, send
from .test_ministry_followup_postgresql import edit as follow_up
from .test_ministry_reports_postgresql import actor as ministry_actor
from .test_ministry_reports_postgresql import setup as ministry_setup
from .test_ministry_responses_postgresql import respond as ministry_respond
from .test_ministry_responses_postgresql import revisit as ministry_revisit
from .test_ministry_review_postgresql import decide as ministry_decide
from .test_ministry_review_postgresql import requests as ministry_requests
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for
from .test_response_submission_postgresql import form_and_answers, submit
from .test_reviewed_form_postgresql import again as reviewed_again
from .test_reviewed_form_postgresql import current_requests as reviewed_requests
from .test_reviewed_form_postgresql import decide_request, set_choices
from .test_reviewed_form_postgresql import ministry_setup as reviewed_ministry_setup
from .test_roster_entries_postgresql import resolved, tick
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

# Text only the review statement holds, to count it among a page's statements.
REVIEW_SQL = "JOIN stewardship_proposal_review v ON v.proposal_id=p.id"


def read(campaign_id, family_id, reader=None):
    """The review and request lines the review read returns, as the
    restricted web login, and the statements run."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
        CaptureQueriesContext(connection) as queries,
    ):
        values = scope_values(ResponseScope(campaign_id), family_id)
        lines = read_reviews(values, reader)
    return lines, len(queries)


def reviews(campaign_id, family_id, reader=None):
    """The review lines (What happened, Details), and the statements run."""
    (lines, _requests), statements = read(campaign_id, family_id, reader)
    return [(str(what), detail) for _at, what, detail in lines], statements


def requests(campaign_id, family_id):
    """The request lines (#2023) as (What happened, Details)."""
    (_reviews, lines), _ = read(campaign_id, family_id)
    return [(str(what), detail) for _at, what, detail in lines]


def page(client, path):
    """GET ``path`` as the web login: the body and the review statements run."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        CaptureQueriesContext(connection) as queries,
    ):
        response, body = get(client, path)
    assert response.status_code == 200
    return body.decode(), sum(REVIEW_SQL in q["sql"] for q in queries.captured_queries)


def rows(body):
    """The timeline's (What happened, Details) pairs, in row order, as text
    (Details keeps each amount in a one-line ``money`` span, #2258)."""
    return [
        (re.sub(r"<[^>]+>", "", what), re.sub(r"<[^>]+>", "", detail))
        for what, detail in re.findall(
            r'<th scope="row">(.*?)</th><td>(.*?)</td></tr>', body
        )
    ]


def spoken(body):
    """``rows`` as a screen reader hears them: the shared Edited chip's
    shown word is hidden from it, so "Accepted (edited)" reads "Accepted,
    edited" (#2257)."""
    return rows(re.sub(r'<span aria-hidden="true">[^<]*</span>', "", body))


def test_census_reviews_and_resolutions_reach_both_roles(response_service, google):
    """Accept, Edit, Clear edit, Ignore, Back to review and Entered by hand
    read in the pipeline's words for Administrators and Staff, naming the
    field, the Member and who acted, never the value or the note."""
    harness, admin, staff, home, name = prepared(response_service)
    family_id = home.submission.family_id
    review(harness, admin, name, "approved", note="Private note")
    edit(harness, admin, fresh(name), "Corrected")
    edit(harness, admin, fresh(name), None)
    review(harness, admin, fresh(name), "ignored")
    # Sending an ignored change back, with an optional note (#1889).
    review(harness, admin, fresh(name), "unreviewed", note="Asked again")
    resolve(harness, staff, home, "entered")
    lines, statements = reviews(harness.campaign.pk, family_id)
    # One statement: no Ministry line, so no names are read.
    assert statements == 1
    assert [what for what, _detail in lines] == [
        "Accepted",
        "Edited",
        "Edit cleared",
        "Ignored",
        "Back to review",
        # The address is accepted before its mark (#1890).
        "Accepted",
        "Entered by hand",
    ]
    member = lines[0][1]
    assert member.startswith("First name for ") and ", Requested;" in member
    assert member.endswith("; by admin@example.org")
    assert lines[-1][1] == "Home address for the Family; by staff@example.org"
    assert not any("Corrected" in d or "Private" in d for _w, d in lines)

    path = reverse("admin:timeline", args=[family_id])
    client, login = signed_in()
    assert login.status_code == 302
    before = AuditEvent.objects.filter(event_type="family_timeline_viewed").count()
    body, counted = page(client, path)
    assert counted == 1
    shown = rows(body)
    # The live response's receipt is a Production email line: the view
    # links it for an Administrator (BACKGROUND_WORK), so Staff's missing
    # link below is the view's own rule, not an absent line.
    assert any(what == "Submission receipt" for what, _detail in shown)
    assert 'href="/admin/mail/sent/' in body
    assert ("Entered by hand", lines[-1][1]) in shown
    assert '<option value="reviews">Reviews</option>' in body
    # The Kind filter narrows to the review lines alone.
    body, _ = page(client, path + "?kind=reviews")
    assert [what for what, _detail in rows(body)] == [
        what for what, _detail in reversed(lines)
    ]
    # Another tab draws no timeline, so the review lines are not read.
    body, counted = page(client, path + "?tab=family")
    assert counted == 0

    # Staff (#1774): the same lines, the filters, no export form and no
    # Mail message link.
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    client, login = signed_in()
    assert login.status_code == 302
    body, counted = page(client, path)
    assert counted == 1 and 'id="table-filters"' in body
    assert ("Entered by hand", lines[-1][1]) in rows(body)
    # Staff export it too (#1799), but get no Mail message links.
    assert "Export this timeline" in body and "/admin/mail/sent/" not in body
    assert '<th scope="row">Submission receipt</th>' in body
    # Four views, each audited once; the reads add no audit row.
    viewed = AuditEvent.objects.filter(event_type="family_timeline_viewed")
    assert viewed.count() == before + 4


# The rule before the reviewed Family form (#1705): forms drawn before
# its release, as one open across the deploy is.
@pytest.mark.usefixtures("earlier_rule")
def test_a_resubmitted_change_keeps_its_carried_decision(response_service):
    """A resubmission that keeps an accepted answer carries the decision to
    its new change row with no history row; Back to review on that row
    reads as such, not as a step that changed nothing."""
    harness, admin, _staff, home, name = prepared(response_service)
    family_id = home.submission.family_id
    review(harness, admin, name, "approved")
    asked = requests(harness.campaign.pk, family_id)
    assert ("Asked for a census change", "Home address for the Family") in asked
    with web_login():
        form = revisit(harness)
        send(harness, form, answers_for(form))
    name.refresh_from_db()
    assert name.execution == "superseded"
    (carried,) = ProposedChange.objects.filter(
        field="first_name", superseded_by__isnull=True
    )
    assert carried.decision == "approved"
    review(harness, admin, carried, "unreviewed")
    lines, _ = reviews(harness.campaign.pk, family_id)
    assert [what for what, _detail in lines] == ["Accepted", "Back to review"]
    # The same answers again ask for nothing new (#2023): no line repeats.
    assert requests(harness.campaign.pk, family_id) == asked


def test_ministry_follow_up_and_roster_ticks(response_service):
    """A follow-up edit, a resolution and the Entered in ParishSoft tick set
    and cleared, worded per step, with the Ministry names read once. The
    clear is Back to review of the mark (#1840): its revision reads Back to
    review, and the tick's clearing written with it is no line of its own."""
    harness, admin, join, leave = resolved(response_service)
    tick(harness, admin, join, True)
    tick(harness, admin, join, False)
    follow_up(harness, admin, leave, state="in_progress", notes="Phoned")
    leave.refresh_from_db()
    follow_up(harness, admin, leave, state="resolved", outcome="declined")
    lines, statements = reviews(harness.campaign.pk, join.submission.family_id)
    # The history, then the catalog's names (two more statements).
    assert statements == 3
    assert [what for what, _detail in lines] == [
        "Accepted",
        "Entered by hand",
        "Back to review",
        "Follow-up updated",
        "Ignored",
    ]
    assert lines[0][1].startswith("Join ")
    assert "; Joined the Ministry; by " in lines[0][1]
    assert lines[-1][1].startswith("Leave ")
    assert "; Stayed in the Ministry; by " in lines[-1][1]
    assert all(detail.endswith("; by admin@example.org") for _w, detail in lines)
    assert not any("Phoned" in detail for _w, detail in lines)
    # What the response asked for (#2023), read in the same statements.
    asked = requests(harness.campaign.pk, join.submission.family_id)
    assert [what for what, _detail in asked] == ["Asked for a Ministry change"] * 2
    assert sorted(detail.split(" ")[0] for _what, detail in asked) == ["Join", "Leave"]
    assert not any(" by " in detail for _what, detail in asked)


def test_additional_information_follow_up(live_response_service, google):
    """Follow-up needed, then done, read as Accepted and Handled."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["additional_information"] = "Please call about the choir"
    family_id = submit(harness, form, answers).submission.family_id
    client, login = signed_in()
    assert login.status_code == 302
    actor = PortalUser.objects.get(email="admin@example.org").pk
    item = AdditionalInformationItem.objects.get()
    for needed, done in ((True, False), (True, True)):
        item.refresh_from_db()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            update_information(
                harness.service.store,
                actor,
                item.pk,
                expected_version=item.version,
                follow_up_needed=needed,
                followed_up=done,
                confirm_clear=False,
                request_key=uuid4(),
                notes="Called them",
            )
    lines, _ = reviews(harness.campaign.pk, family_id)
    assert lines == [
        ("Accepted", "Additional information; by admin@example.org"),
        ("Handled", "Additional information; by admin@example.org"),
    ]


def test_back_to_review_notes_reach_their_readers_page_and_file(
    response_service, google
):
    """A Back to review line carries its note (#1893) for the readers of
    Census changes, on the page and in the Timeline export captured for its
    requester; a decision's note never shows, and a reader who may not open
    Census changes reads the line without it."""
    from parishkit.stewardship.accounts.policy import Principal, current_principal
    from parishkit.stewardship.reports.export_models import TimelineExportSnapshot
    from parishkit.stewardship.reports.timeline_exports import (
        create_timeline_export,
    )

    harness, admin, staff, home, name = prepared(response_service)
    store = harness.service.store
    family_id = home.submission.family_id
    review(harness, admin, name, "approved", note="Private note")
    review(harness, admin, fresh(name), "unreviewed", note="Asked the Family")
    resolve(harness, staff, home, "entered")
    home.refresh_from_db()
    resolve(harness, staff, home, "reopened", note="Typed the wrong street")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        principals = {who: current_principal(store, who) for who in (admin, staff)}
    for principal in principals.values():
        lines, statements = reviews(harness.campaign.pk, family_id, principal)
        assert statements == 1
        assert [what for what, _detail in lines] == [
            "Accepted",
            "Back to review",
            # Entered by hand on a change back in review accepts it first
            # (#1890), so the Accept shows as its own line.
            "Accepted",
            "Entered by hand",
            "Back to review",
        ]
        assert lines[1][1].endswith("; by admin@example.org; note: Asked the Family")
        assert lines[4][1] == (
            "Home address for the Family; by staff@example.org; "
            "note: Typed the wrong street"
        )
        assert not any("Private" in detail for _w, detail in lines)
    # No reader, or one who may not open Census changes: no note at all.
    leader = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({1}))
    for reader in (None, leader):
        lines, _ = reviews(harness.campaign.pk, family_id, reader)
        assert not any("note:" in detail for _w, detail in lines)

    # The page, for an Administrator and for Staff.
    path = reverse("admin:timeline", args=[family_id])
    for email, sub in (("admin@example.org", None), ("staff@example.org", "s")):
        if sub:
            google[0].update(email=email, sub="synthetic-staff")
        client, login = signed_in()
        assert login.status_code == 302
        body, _ = page(client, path + "?kind=reviews")
        shown = dict(reversed(rows(body)))
        assert "; note: Typed the wrong street" in body
        assert "Private note" not in body
        assert shown["Entered by hand"].endswith("; by staff@example.org")

    # The export's capture, for its requester.
    for who in (admin, staff):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            made = create_timeline_export(
                store,
                who,
                campaign_id=harness.campaign.pk,
                family_id=family_id,
                mode="production",
                format="csv",
                browser_timezone="UTC",
                request_key=uuid4(),
            )
        capture = TimelineExportSnapshot.objects.get(pk=made.timeline_snapshot_id)
        details = [event["detail"] for event in capture.document["events"]]
        assert (
            "Home address for the Family; by staff@example.org; "
            "note: Typed the wrong street"
        ) in details
        assert not any("Private" in detail for detail in details)


def test_a_replacing_change_on_the_reviewed_form_carries_nothing(
    response_service, google
):
    """A change the Family answers again on the reviewed form, over the
    Reviewer's value it was shown, is replaced by a new To review change with
    the same answer (rule 2), which carries no decision (#2028): Accepting
    the new change reads as Accepted, not as the old change's Edit cleared,
    for Staff too."""
    from .test_census_pipeline_review_postgresql import decide
    from .test_census_pipeline_review_postgresql import prepared as pipeline
    from .test_reviewed_form_postgresql import again, first_name, rows_for

    harness, _admin, staff, changes = pipeline(response_service)
    family_id = changes["first_name"].submission.family_id
    decide(harness, staff, [changes["first_name"]], "edit", value="Corrected")
    again(harness, first_name("Requested"))
    old, new = rows_for("first_name")
    assert (old.execution, old.superseded_by_id) == ("superseded", new.pk)
    assert (old.submitted_value, old.decision) == ("Requested", "approved")
    assert (new.submitted_value, new.decision) == ("Requested", "unreviewed")
    decide(harness, staff, [new], "accept")
    lines, _ = reviews(harness.campaign.pk, family_id)
    named = [what for what, detail in lines if detail.startswith("First name for ")]
    assert named == ["Accepted (edited)", "Accepted"]

    google[0].update(email="staff@example.org", sub="synthetic-staff")
    client, login = signed_in()
    assert login.status_code == 302
    path = reverse("admin:timeline", args=[family_id]) + "?kind=reviews"
    body, _ = page(client, path)
    shown = [w for w, detail in spoken(body) if detail.startswith("First name for ")]
    assert shown == ["Accepted", "Accepted, edited"]


def test_pledge_decisions_reach_only_financial_readers(response_service, google):
    """An Edit, its mark and the mark's undo (#2071) each add a pledge line,
    the Edit with the Reviewer's value and the undo with its note, for
    Administrators and Staff, on the page too; a reader without financial
    detail, or none, sees no pledge line. One statement either way."""
    from parishkit.stewardship.accounts.policy import Principal

    from .test_pledge_review_postgresql import admin as pledge_admin
    from .test_pledge_review_postgresql import decide, pledged

    harness, submission = pledged(response_service)
    actor = pledge_admin()
    decide(
        harness,
        actor,
        "edit",
        [submission],
        value_amount="1,300",
        value_frequency="quarterly",
    )
    decide(harness, actor, "entered_by_hand", [submission])
    decide(harness, actor, "back_to_review", [submission], note="Wrong amount")
    family_id = submission.family_id
    by = "; by admin@example.org"
    expected = [
        ("Accepted (edited)", "Pledge; Reviewer's value: $1,300.00, Quarterly" + by),
        ("Entered by hand", "Pledge" + by),
        ("Back to review", "Pledge" + by + "; note: Wrong amount"),
    ]
    for roles in ({"staff"}, {"administrator"}):
        reader = Principal(uuid4(), frozenset(roles), frozenset())
        assert reviews(harness.campaign.pk, family_id, reader) == (expected, 1)
    leader = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({1}))
    for reader in (None, leader):
        assert reviews(harness.campaign.pk, family_id, reader) == ([], 1)

    client, login = signed_in()
    assert login.status_code == 302
    path = reverse("admin:timeline", args=[family_id]) + "?kind=reviews"
    body, counted = page(client, path)
    assert counted == 1
    # The page escapes the apostrophe in Reviewer's.
    shown = [(what, unescape(detail)) for what, detail in spoken(body)]
    edited = ("Accepted, edited", expected[0][1])
    assert shown == list(reversed([edited, *expected[1:]]))


def test_the_page_names_share_methods_by_short_label_and_files_by_full(
    response_service, google, monkeypatch
):
    """An Edit's share methods are named by short label, with the full label
    as tooltip, in the page's table only (#2198); the line's text, which the
    PDF and ``pk-admin`` show, keeps the full label."""
    from parishkit.stewardship.accounts.policy import Principal
    from parishkit.stewardship.reports import timeline_reviews

    from .test_pledge_review_postgresql import admin as pledge_admin
    from .test_pledge_review_postgresql import pledged
    from .test_pledge_share_methods_postgresql import edit as edit_pledge

    harness, submission = pledged(response_service)
    edit_pledge(
        harness,
        pledge_admin(),
        submission,
        amount="1,300",
        frequency="quarterly",
        **{f"value_share_{OTHER}": "on", f"value_share_text_{OTHER}": "Stock"},
    )
    worded = timeline_reviews.share_wording

    def shortened(values, **context):
        """The configuration as if the Administrator set Other's short label."""
        options = [
            option | ({"short_label": "Else"} if option["id"] == OTHER else {})
            for option in values["share_options"]
        ]
        return worded(values | {"share_options": options}, **context)

    monkeypatch.setattr(timeline_reviews, "share_wording", shortened)
    reader = Principal(uuid4(), frozenset({"administrator"}), frozenset())
    (lines, _requests), _ = read(harness.campaign.pk, submission.family_id, reader)
    (detail,) = [detail for _at, _what, detail in lines]
    # The text (the PDF, `pk-admin`) has the full label, never the short one.
    assert "share methods: Other: Stock; by admin@example.org" in detail
    assert "Else" not in detail
    drawn = detail.before + detail.after
    assert 'title="Other"' in drawn and ">Else<" in drawn
    assert timeline_reviews.share_labels_for(harness.campaign.pk)[OTHER] == "Other"

    client, login = signed_in()
    assert login.status_code == 302
    path = reverse("admin:timeline", args=[submission.family_id]) + "?kind=reviews"
    body, _counted = page(client, path)
    assert re.search(
        r'<span title="Other"><span aria-hidden="true">Else</span>'
        r'<span class="visually-hidden">Other</span></span>: Stock',
        body,
    )


def test_limitation_decisions_reach_the_timeline(response_service, google):
    """Accept, Handled, Handled's undo and Ignore (#2071) each add a
    Limitations line; a Handled the Family's changed answer ended, then a
    fresh Accept, reads Accepted rather than as the undo."""
    from parishkit.stewardship.accounts.policy import Principal
    from parishkit.stewardship.campaigns.models import FamilyCampaign
    from parishkit.stewardship.pipeline import Action
    from parishkit.stewardship.responses.limitation_review import answer_id

    from .response_builders import activate_response_service
    from .test_information_limitation_review_postgresql import (
        decide_answer,
        resubmit,
        staff_actor,
    )
    from .test_limitations_report_postgresql import submit_live
    from .test_ministry_responses_postgresql import start

    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    first = submit_live(harness, cannot_serve=True)
    actor = staff_actor()
    family = FamilyCampaign.objects.get(pk=first.family_id)
    member = answer_id(family.family_duid, "member", "3")
    whole = answer_id(family.family_duid, "family")
    decide_answer(harness, actor, Action.ACCEPT, member, 0)
    decide_answer(harness, actor, Action.HANDLED, member, 1)
    # An unchanged answer between Handled and its undo keeps Handled in
    # force, so the undo still reads Back to review.
    resubmit(harness, cannot_serve=True)
    decide_answer(harness, actor, Action.BACK_TO_REVIEW, member, 2, note="Not yet")
    decide_answer(harness, actor, Action.HANDLED, member, 3)
    decide_answer(harness, actor, Action.IGNORE, whole, 0)
    # The Family clears the answer, then gives it again: a new To review.
    resubmit(harness, cannot_serve=False)
    resubmit(harness, cannot_serve=True)
    decide_answer(harness, actor, Action.ACCEPT, member, 4, note="Asked again")
    staff = Principal(uuid4(), frozenset({"staff"}), frozenset())
    lines, statements = reviews(harness.campaign.pk, family.pk, staff)
    # The history, then the Ministry names its responses' Ministry request
    # lines need (#2023): two more statements.
    assert statements == 3
    assert [what for what, _detail in lines] == [
        "Accepted",
        "Handled",
        "Back to review",
        "Handled",
        "Ignored",
        "Accepted",
    ]
    serve = [detail for _what, detail in lines[:4]] + [lines[5][1]]
    assert all(d.startswith("Cannot participate in any Ministries for ") for d in serve)
    assert lines[2][1].endswith("; by admin@example.org; note: Not yet")
    assert lines[4][1] == (
        "Cannot attend Mass or prayer services for the Family; by admin@example.org"
    )
    assert "note:" not in lines[5][1]


def test_parish_added_census_changes_and_the_responses_asks(response_service):
    """A census change the parish added reads Added by the parish, then
    each later step, with a Withdraw of an entered one as one line (#2023);
    the response's own changes are request lines, in the same statement."""
    harness, _, staff, changes = pipeline_prepared(response_service)
    campaign = campaign_of(changes)
    family_id = next(iter(changes.values())).submission.family_id
    addition, _ = add(
        harness, staff, campaign, member_form(changes, "nickname", "Dave")
    )
    key = f"add-{addition.pk}"
    worklist(harness, staff, campaign, "entered_by_hand", {key: 1})
    worklist(harness, staff, campaign, "back_to_review", {key: 2}, note="Typo")
    worklist(harness, staff, campaign, "entered_by_hand", {key: 3})
    worklist(harness, staff, campaign, "withdraw", {key: 4}, note="Gone")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        principal = current_principal(harness.service.store, staff)
    lines, statements = reviews(campaign, family_id, principal)
    assert statements == 1
    assert [what for what, _detail in lines] == [
        "Added by the parish",
        "Entered by hand",
        "Back to review",
        "Entered by hand",
        "Withdrawn",
    ]
    item = lines[0][1]
    assert item.startswith("Nickname for ") and item.endswith("; by staff@example.org")
    assert lines[2][1] == item + "; note: Typo"
    assert not any("Dave" in detail for _what, detail in lines)
    asked = requests(campaign, family_id)
    assert {what for what, _detail in asked} == {"Asked for a census change"}
    assert len(asked) == len(changes)
    assert ("Asked for a census change", "Home address for the Family") in asked


def test_a_parish_added_ministry_request_reads_added_by_the_parish(
    response_service,
):
    """Staff's Add on Ministry requests is Added by the parish, not Accepted,
    and is no request line: the Family did not ask for it (#2023)."""
    harness = ministry_setup(response_service)
    staff = ministry_actor(harness, {"staff"}, ())
    join, _leave = ministry_requests()
    family_id = join.submission.family_id
    ministry_decide(harness, staff, "ignore", join)
    # The Family answers again without the leave, which withdraws it, so
    # Ministry 4 is free to add (as the Add's own test does).
    form = ministry_revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [], "leave": []}
    ministry_respond(harness, form, answers)
    before = requests(harness.campaign.pk, family_id)
    fields = {
        "value_member": ["3"],
        "value_ministry": ["4"],
        "value_action": ["join"],
        "value_in_place_of": [str(join.pk)],
    }
    ministry_decide(harness, staff, "add", fields=fields)
    lines, _ = reviews(harness.campaign.pk, family_id)
    assert [what for what, _detail in lines][-2:] == ["Ignored", "Added by the parish"]
    assert lines[-1][1].startswith("Join ")
    assert requests(harness.campaign.pk, family_id) == before


def test_a_separate_withdraw_keeps_the_not_entered_yet_before_it(response_service):
    """Not entered yet and a later Withdraw with the very same note are two
    decisions, so both lines stay; only one Withdraw's own pair of steps
    reads as one line (matched by its request, #2023)."""
    harness, _, staff, changes = pipeline_prepared(response_service)
    campaign = campaign_of(changes)
    family_id = next(iter(changes.values())).submission.family_id
    addition, _ = add(
        harness, staff, campaign, member_form(changes, "nickname", "Dave")
    )
    key = f"add-{addition.pk}"
    worklist(harness, staff, campaign, "entered_by_hand", {key: 1})
    worklist(harness, staff, campaign, "back_to_review", {key: 2}, note="Same")
    worklist(harness, staff, campaign, "withdraw", {key: 3}, note="Same")
    lines, _ = reviews(campaign, family_id)
    assert [what for what, _detail in lines] == [
        "Added by the parish",
        "Entered by hand",
        "Back to review",
        "Withdrawn",
    ]


def test_a_reviewed_form_asking_again_for_an_ignored_join_is_listed(
    response_service,
):
    """The reviewed form's rule 2 (#1705): ticking an Ignored join again
    makes a new request To review over the decided one. The Family asked
    again, so it is one more request line (#2023)."""
    harness, staff = reviewed_ministry_setup(response_service)
    join = reviewed_requests()[9, "join"]
    family_id = join.submission.family_id
    decide_request(harness, staff, "ignore", join)
    reviewed_again(harness)
    before = requests(harness.campaign.pk, family_id)
    reviewed_again(harness, set_choices([9], [4]))
    join.refresh_from_db()
    assert join.state == "superseded"
    after = requests(harness.campaign.pk, family_id)
    assert len(after) == len(before) + 1
    joins = [detail for _what, detail in after if detail.startswith("Join ")]
    assert len(joins) == 2 and joins[0] == joins[1]
