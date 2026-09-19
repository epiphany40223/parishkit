"""Staff authorization, lifecycle gates and current-source report parity."""

from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_tasks import load_document
from parishkit.stewardship.reports.information import InformationQuery, information_page
from parishkit.stewardship.reports.information_exports import create_information_export
from parishkit.stewardship.reports.weekly_observation import capture_weekly_observation
from parishkit.stewardship.responses.models import AdditionalInformationItem

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post
from .test_information_followup_postgresql import search
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read
from .test_source_families_postgresql import prepare, promote
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def test_staff_capability_is_rechecked_and_gates_preserve_read_history(
    live_response_service,
    google,
):
    """Actual Staff access does not grant leaders the queue or its write endpoint."""
    harness = live_response_service
    respond(harness, "Restricted household request")
    item = AdditionalInformationItem.objects.get()
    store = harness.service.store
    rule = address("reader@example.org", roles=("staff", "ministry_leader"))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    route = f"/admin/reports/{harness.campaign.pk}/information/"
    detail = route + f"{item.pk}/"
    values = {
        "expected_version": str(item.version),
        "request_key": str(uuid4()),
        "notes": "Staff note",
    }
    export_values = InformationQuery().form_values() | {
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert read(browser, route)[0].status_code == 200
        assert read(browser, detail)[0].status_code == 200
        assert post(browser, detail + "update", values).status_code == 302
        exported = post(browser, route + "export", export_values)
        assert exported.status_code == 302
        export_path = exported["Location"]
    # Install only the future purge owner's sentinel; no purge is initiated.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign=harness.campaign,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, detail)
        assert response.status_code == 200 and b"Campaign work is gated" in body
        assert b"<fieldset disabled>" in body
        assert post(browser, detail + "update", values).status_code == 403
        assert post(browser, route + "export", export_values).status_code == 403
        assert read(browser, export_path)[0].status_code == 200
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "login_rules",
                "id": rule["id"],
                "values": {
                    "roles": ["ministry_leader"],
                    "grants": {
                        "ministry_leader": rule["values"]["grants"]["ministry_leader"]
                    },
                },
            }
        ],
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for path in (route, detail, export_path):
            response, body = read(browser, path)
            assert response.status_code == 403
            assert (
                b"Restricted household request" not in body
                and b"Staff note" not in body
            )
        assert search(browser, route, {"search": "Restricted"})[0].status_code == 403
        assert post(browser, detail + "update", values).status_code == 403
        assert post(browser, route + "export", export_values).status_code == 403


def test_current_source_absence_keeps_submitted_request(live_response_service):
    """The Staff queue and weekly digest use the same current-source fallback."""
    harness = live_response_service
    respond(harness, "Keep this request")
    actor = user("admin@example.org").pk
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        request = create_information_export(
            harness.service.store,
            actor,
            campaign_id=harness.campaign.pk,
            query=InformationQuery(),
            history=False,
            format="csv",
            browser_timezone="UTC",
            request_key=uuid4(),
        )
        original = load_document(request)
    data = response_source()
    data.families.clear()
    data.members.clear()
    data.member_contactinfos.clear()
    data.ministry_type_memberships.clear()
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        report = information_page(harness.campaign.pk, InformationQuery())
        weekly = capture_weekly_observation(harness.campaign.pk)
        assert load_document(request) == original
        assert request.information_snapshot.source_id != snapshot.pk
    assert report["metadata"]["source_id"] == str(snapshot.pk)
    assert report["total"] == 1
    assert (
        report["rows"][0]["family_name"]
        == weekly.items[0].value.family_name
        == "Family"
    )
    assert (
        report["rows"][0]["text"] == weekly.items[0].value.text == "Keep this request"
    )
