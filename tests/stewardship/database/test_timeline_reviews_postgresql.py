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
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from parishkit.stewardship.accounts.models import PortalUser
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

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_census_edit_postgresql import edit
from .test_census_resolution_postgresql import prepared, resolve
from .test_census_review_postgresql import fresh, review
from .test_member_requests_postgresql import revisit, send
from .test_ministry_followup_postgresql import edit as follow_up
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for
from .test_response_submission_postgresql import form_and_answers, submit
from .test_roster_entries_postgresql import resolved, tick
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

# Text only the review statement holds, to count it among a page's statements.
REVIEW_SQL = "JOIN stewardship_proposal_review v ON v.proposal_id=p.id"


def reviews(campaign_id, family_id, reader=None):
    """The review lines as the restricted web login, and the statements run."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
        CaptureQueriesContext(connection) as queries,
    ):
        values = scope_values(ResponseScope(campaign_id), family_id)
        lines = read_reviews(values, reader)
    return [(str(what), detail) for _at, what, detail in lines], len(queries)


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
    """The timeline's (What happened, Details) pairs, in row order."""
    return [
        (re.sub(r"<[^>]+>", "", what), detail)
        for what, detail in re.findall(
            r'<th scope="row">(.*?)</th><td>(.*?)</td></tr>', body
        )
    ]


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
    # Sending an ignored change back needs a note (#1683's census review).
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


def test_a_resubmitted_change_keeps_its_carried_decision(response_service):
    """A resubmission that keeps an accepted answer carries the decision to
    its new change row with no history row; Back to review on that row
    reads as such, not as a step that changed nothing."""
    harness, admin, _staff, home, name = prepared(response_service)
    family_id = home.submission.family_id
    review(harness, admin, name, "approved")
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
            "Entered by hand",
            "Back to review",
        ]
        assert lines[1][1].endswith("; by admin@example.org; note: Asked the Family")
        assert lines[3][1] == (
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
