"""Multi-Ministry packet capture, scope and rendering under the real roles."""

import io
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction

from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_models import MinistryExportSnapshot
from parishkit.stewardship.reports.export_tasks import load_document
from parishkit.stewardship.reports.ministry_exports import packet_parameters
from parishkit.stewardship.reports.ministry_packets import (
    PacketDocument,
    render_packet,
)

from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post
from .test_ministry_exports_postgresql import create, leader
from .test_ministry_followup_postgresql import edit, requests
from .test_ministry_reports_postgresql import setup
from .test_ministry_responses_postgresql import respond, revisit
from .test_policy_postgresql import user
from .test_response_http_postgresql import answers_for

pytestmark = pytest.mark.django_db(transaction=True)


def packet(harness, actor, ministries=None, *, history=False):
    """Capture through the owning service under the real web database role."""
    return create(
        harness,
        actor,
        query=None,
        ministry_id=None,
        action="packet",
        ministries=ministries,
        history=history,
    )


def sections(request):
    """The immutable captured sections of one export request."""
    snapshot = MinistryExportSnapshot.objects.get(pk=request.ministry_snapshot_id)
    return snapshot, {entry["duid"]: entry for entry in snapshot.document["sections"]}


def test_packet_scope_history_privacy_and_rendering(response_service, google):
    """SQL intersects scope and selection; notes appear only for the outcome Other."""
    harness = setup(response_service)
    _, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    join, leave = requests()
    emailed = database_now() - timedelta(days=2)
    phoned = database_now() - timedelta(days=1)
    edit(
        harness,
        admin,
        join,
        assignee_id=head,
        state="assigned",
        notes="PRIVATE-JOIN-NOTE",
        contact_channel="email",
        contact_at=emailed,
    )
    join.refresh_from_db()
    edit(
        harness,
        admin,
        join,
        assignee_id=head,
        state="assigned",
        notes="PRIVATE-JOIN-NOTE",
        contact_channel="phone",
        contact_at=phoned,
    )
    edit(
        harness,
        admin,
        leave,
        state="resolved",
        outcome="other",
        notes="OTHER-REFERENCE moved parishes",
    )

    # Staff, every authorized Ministry: only unresolved latest requests by default.
    snapshot, found = sections(packet(harness, admin))
    assert sorted(found) == [4, 9] and snapshot.row_count == 1
    assert snapshot.authorization_scope["result_ministries"] == [4, 9]
    assert found[4]["rows"] == []  # Selected but empty still gets its section.
    (row,) = found[9]["rows"]
    assert (row["action"], row["state"], row["outcome"]) == ("join", "assigned", None)
    assert row["email_contact_at"][:10] == emailed.date().isoformat()
    assert row["phone_contact_at"][:10] == phoned.date().isoformat()
    # Staff see the operational contact; the leader capture below must not.
    assert "valid@example.org" in json.dumps(row["emails"])
    assert row["notes"] is None and "PRIVATE-JOIN-NOTE" not in json.dumps(
        snapshot.document
    )
    # Member 3 is the current Chairperson of Ministry 4 in the harness source,
    # and is also this packet's requester, so the two names must agree.
    assert found[4]["chairs"] == [row["member_name"]] and found[9]["chairs"] == []

    # The history option adds the resolved request, and only Other shows notes.
    snapshot, found = sections(packet(harness, admin, history=True))
    assert snapshot.row_count == 2
    (closed,) = found[4]["rows"]
    assert (closed["action"], closed["outcome"]) == ("leave", "other")
    assert closed["notes"] == "OTHER-REFERENCE moved parishes"
    assert "PRIVATE-JOIN-NOTE" not in json.dumps(snapshot.document)

    # A leader's packet is its own Ministries, with unpublished contacts withheld.
    limited = packet(harness, head)
    snapshot, found = sections(limited)
    assert sorted(found) == [9]
    assert snapshot.authorization_scope["operational"] is False
    (row,) = found[9]["rows"]
    assert row["email_visibility"] == "not_published" and row["emails"] is None
    assert "valid@example.org" not in json.dumps(snapshot.document)
    # The same leader does receive what the source publishes: this phone.
    assert row["phone_visibility"] == "available"
    assert "202-555-0123" in json.dumps(row["phones"])
    assert sorted(sections(packet(harness, head, (9,)))[1]) == [9]
    for outside in ((4,), (4, 9)):
        with pytest.raises(PermissionError):
            packet(harness, head, outside)
    with pytest.raises(ValueError):
        packet(harness, admin, (4, 9, 123))  # Not a Ministry of this campaign.

    recorded = AuditContext.objects.filter(
        event__event_type="export_requested", event__subject_id=limited.pk
    ).get()
    assert recorded.context["ministry_duids"] == [9]
    assert "PRIVATE" not in json.dumps(recorded.context)

    # SQL refuses what the service would never send, as the web role itself.
    base = dict(
        campaign_id=harness.campaign.pk,
        configuration_id=limited.configuration_id,
        correlation_id=uuid4(),
    )
    neutral = packet_parameters((9,), history=False)
    unavailable, selection = "inputs are unavailable", "Invalid Ministry"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # Positive control: the valid row passes the capture trigger, so each
        # refusal below is caused by its one changed value. The savepoint shows
        # the capture refuses at the statement, not the deferred binding trigger.
        with work_transaction(), transaction.atomic():
            MinistryExportSnapshot.objects.create(
                actor_id=head, parameters=neutral, **base
            )
            transaction.set_rollback(True)
        for actor, parameters, refusal in (
            (head, packet_parameters((4,), history=False), unavailable),
            (head, packet_parameters((4, 9), history=False), unavailable),
            (admin, packet_parameters((9, 123), history=False), unavailable),
            (admin, neutral | {"ministries": [9, 4]}, selection),
            (admin, neutral | {"ministries": [9, 9]}, selection),
            (admin, neutral | {"ministries": []}, selection),
            (admin, neutral | {"ministries": "all"}, selection),
            (admin, neutral | {"ministry": 9}, selection),
            (
                admin,
                neutral | {"filters": neutral["filters"] | {"search": "x"}},
                selection,
            ),
            (
                admin,
                neutral | {"filters": neutral["filters"] | {"state": "new"}},
                selection,
            ),
            (
                admin,
                {key: neutral[key] for key in ("filters", "ministry", "action")},
                "Invalid Ministry export parameters",
            ),
            (
                admin,
                neutral | {"action": "join", "ministry": 9},
                "Invalid Ministry export parameters",
            ),
        ):
            with (
                work_transaction(),
                pytest.raises(DatabaseError, match=refusal),
                transaction.atomic(),
            ):
                MinistryExportSnapshot.objects.create(
                    actor_id=actor, parameters=parameters, **base
                )

    # The worker renders only the retained capture, as a sectioned packet.
    complete = packet(harness, admin, history=True)
    complete.refresh_from_db()
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        document = load_document(complete)
        assert isinstance(document, PacketDocument)
        assert [len(section.rows) for section in document.sections] == [1, 1]
        output = io.BytesIO()
        render_packet(document, output, format="csv")
    text = output.getvalue().decode()
    assert "Other: OTHER-REFERENCE moved parishes" in text
    # The harness campaign is labelled 2027 but starts in 2026, so an exact match
    # proves SQL captured the configured label rather than the application
    # silently falling back to the start year after a lost or misread key.
    captured = complete.ministry_snapshot.document["metadata"]
    assert captured["year_label"] == "2027" and captured["start_date"][:4] == "2026"
    assert "Stewardship year,2027\r\n" in text
    assert "Stewardship year,2026" not in text
    assert "PRIVATE-JOIN-NOTE" not in text and text.count("Member,Member DUID") == 2


def test_native_packet_request_form(response_service, google):
    """A leader queues packets only for its own Ministries through a CSRF form."""
    harness = setup(response_service)
    browser, head, _, _ = leader(harness, google)
    route = f"/admin/reports/{harness.campaign.pk}/ministries/packet/"

    def form(**values):
        """A complete valid request; each case changes only what it tests."""
        return {
            "request_key": str(uuid4()),
            "format": "pdf",
            "browser_timezone": "UTC",
        } | values

    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert browser.post(route, form()).status_code == 403  # No CSRF token.
        assert post(browser, route + "?format=pdf", form()).status_code == 400
        assert post(browser, route, form()).status_code == 302
        # No ticked Ministry means every authorized one; any tick is exact.
        chosen = form(ministries=["9"], history="yes")
        assert post(browser, route, chosen).status_code == 302
        assert post(browser, route, chosen).status_code == 302  # Replay.
        # Another Ministry is denied exactly like any other unavailable report.
        outside = form(ministries=["4"])
        assert post(browser, route, outside).status_code == 403
        for invalid in (
            form(ministries=["09"]),
            form(ministries=["9", "9"]),
            form(ministries=["0"]),
            form(ministries=["9"], history="no"),
            form(selection="all"),
            form(format="zip"),
            form(browser_timezone="Mars/Olympus"),
            form(unexpected="field"),
        ):
            assert post(browser, route, invalid).status_code == 400
    captured = MinistryExportSnapshot.objects.filter(actor_id=head).order_by(
        "created_at"
    )
    assert [row.parameters["ministries"] for row in captured] == [None, [9]]
    assert [row.parameters["filters"]["history"] for row in captured] == [
        "current",
        "all",
    ]


def test_packet_contact_dates_follow_a_same_intent_resubmission(response_service):
    """A Family resubmission replaces the rows; recorded follow-up still prints."""
    harness = setup(response_service)
    admin = user("admin@example.org").pk
    join, leave = requests()
    emailed = database_now() - timedelta(days=3)
    phoned = database_now() - timedelta(days=2)
    edit(harness, admin, join, contact_channel="email", contact_at=emailed)
    edit(harness, admin, leave, contact_channel="phone", contact_at=phoned)
    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": [4]}
    respond(harness, form, answers)
    successors = {row.ministry_duid: row for row in requests()}
    assert {join.pk, leave.pk}.isdisjoint(row.pk for row in successors.values())

    _, found = sections(packet(harness, admin))
    (joined,), (left,) = found[9]["rows"], found[4]["rows"]
    # The rows are the successors, carrying dates recorded on their predecessors.
    assert joined["id"] == str(successors[9].pk) and left["id"] == str(successors[4].pk)
    assert joined["email_contact_at"][:10] == emailed.date().isoformat()
    assert joined["phone_contact_at"] is None
    assert left["phone_contact_at"][:10] == phoned.date().isoformat()
    assert left["email_contact_at"] is None
    # History never resurrects the superseded predecessors as extra rows.
    _, everything = sections(packet(harness, admin, history=True))
    assert [len(everything[duid]["rows"]) for duid in (4, 9)] == [1, 1]
