"""The response lists and their CSV over HTTP, read as the restricted web login.

The campaign is the response-metrics ``funnel`` fixture (#477): five eligible
Families, the corpus Family signed in through its rehearsal credential, with
two of the extra Families given the launch-day ParishSoft data problems (a
blank mailing name and envelope number 0). The lists are checked for
Administrators and Staff, in Production and Testing, with the read guard,
``no-store``, the CSV, the purge gate and the audit.
"""

import csv
import io
import re
from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import response_list_views
from parishkit.stewardship.source.models import SourceCurrent
from parishkit.stewardship.source.snapshot_names import snapshot_family_names

from ..policy_factory import address
from . import test_response_metrics_postgresql as metrics_tests
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import restricted_download_pool
from .test_information_followup_postgresql import search
from .test_report_workspace_postgresql import read as get
from .test_response_metrics_postgresql import funnel, open_form, respond  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

BLANK_MAILING, ENVELOPE_ZERO = 11, 12


@pytest.fixture
def quality_funnel(monkeypatch, request):
    """The funnel campaign with two Families' ParishSoft data to check."""
    original = metrics_tests.funnel_source

    def source():
        """The funnel's source with a blank mailing name and an envelope 0."""
        data = original()
        # The corpus has no mailing names; give every Family one but one.
        for duid, family in data.families.items():
            data.families[duid] = dict(family, mailingName=f"Household {duid}")
        data.families[BLANK_MAILING] = dict(
            data.families[BLANK_MAILING], mailingName="  "
        )
        data.families[ENVELOPE_ZERO] = dict(
            data.families[ENVELOPE_ZERO], envelopeNumber=0
        )
        return data

    monkeypatch.setattr(metrics_tests, "funnel_source", source)
    return request.getfixturevalue("funnel")


def listed_duids(body):
    """The Family DUID column of a rendered list, in order."""
    return [
        int(value)
        for value in re.findall(rb'</th><td class="numeric">(\d+)</td>', body)
    ]


def close_purge_gate(campaign):
    """Simulate only the eventual purge owner's gate in this disposable database."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign=campaign,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")


def test_lists_and_downloads_for_admin_and_staff(
    quality_funnel, auth_service, google, settings, monkeypatch
):
    """Each list from the funnel rows; CSV complete and neutralized; all audited.

    The web login and the dedicated download pool are the real restricted
    logins (``restricted_download_pool``): the download pool's login reads no
    campaign data, so a CSV must be read on the web connection, never there.
    """
    harness, _epoch = quality_funnel
    # restricted_download_pool saves and restores the setting it replaces, so
    # the setting must exist; inside it, the pool is the real download login.
    settings.STEWARDSHIP_DOWNLOAD_POOL = None
    open_form(harness)
    respond(harness)
    base = f"/admin/reports/{harness.campaign.pk}/responses/"
    snapshot = SourceCurrent.objects.get().snapshot_id
    name = snapshot_family_names(snapshot, [1], "Family")[1]
    admin, login = signed_in()
    assert login.status_code == 302
    with restricted_download_pool(settings):
        # Production has no responses yet: the Family responded in Testing.
        response, body = get(admin, base + "submitted/")
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        assert b"No Families on this list." in body
        assert (base + "submitted/?mode=testing").encode() in body
        # The rehearsal's submission is listed in Testing, by name and DUID.
        response, body = get(admin, base + "submitted/?mode=testing")
        assert response.status_code == 200
        assert listed_duids(body) == [1]
        assert f'">{name}</a></th>'.encode() in body
        assert b"never counted in Production" in body
        # Submitted without a delivered invitation: no invitation was sent.
        _, body = get(admin, base + "submitted/?mode=testing&show=invited")
        assert listed_duids(body) == []
        _, body = get(admin, base + "submitted/?mode=testing&show=uninvited")
        assert listed_duids(body) == [1]
        # A submission implies the form was opened, so it is not "started".
        _, body = get(admin, base + "started/?mode=testing")
        assert listed_duids(body) == []
        # ParishSoft data to check lists the campaign's active Families.
        _, body = get(admin, base + "data-quality/")
        assert listed_duids(body) == [BLANK_MAILING, ENVELOPE_ZERO]
        assert b"Blank mailing name" in body and b"Envelope number 0" in body
        _, body = get(admin, base + "data-quality/?show=envelope&sort=-duid")
        assert listed_duids(body) == [ENVELOPE_ZERO]
        for invalid in (
            "submitted/?mode=live",
            "submitted/?show=envelope",
            "submitted/?sort=name",
            "submitted/?search=smith",
            "submitted/?size=7",
        ):
            assert get(admin, base + invalid)[0].status_code == 400
        assert get(admin, base + "everyone/")[0].status_code == 404
        # Until #145 any campaign but the current one is gone (410).
        assert (
            get(admin, f"/admin/reports/{uuid4()}/responses/submitted/")[0].status_code
            == 410
        )
        # The download is the complete filtered list, in the page's order.
        response, body = search(
            admin,
            base + "data-quality/csv/",
            {"sort": "-duid", "timezone": "America/New_York"},
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert response["Cache-Control"] == "no-store"
        assert "stewardship-responses-data-quality-" in response["Content-Disposition"]
        table = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        assert table[0][:4] == [
            "Family",
            "Family DUID",
            "Envelope number",
            "Mailing name",
        ]
        assert [row[1] for row in table[1:]] == [str(ENVELOPE_ZERO), str(BLANK_MAILING)]
        assert body.endswith(b"\r\n")
        response, body = search(
            admin, base + "submitted/csv/", {"mode": "testing", "timezone": "UTC"}
        )
        assert response.status_code == 200
        table = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        assert [row[2] for row in table[1:]] == ["1"]
        # The shared CSV time text: ISO 8601 with a space separator.
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\+00:00", table[1][0])
        for invalid in (
            {"timezone": "Mars/Base"},
            {"size": "all"},
            {"show": "bad"},
        ):
            assert search(admin, base + "submitted/csv/", invalid)[0].status_code == 400
        # A purge gate closing as a download starts (between the check before
        # the guard and the recheck inside it) is the same 409.
        checks = iter((False, True))
        paused = response_list_views.downloads_paused
        monkeypatch.setattr(
            response_list_views, "downloads_paused", lambda campaign: next(checks)
        )
        response, body = search(admin, base + "started/csv/", {"timezone": "UTC"})
        assert response.status_code == 409 and b"prepared for purge" in body
        monkeypatch.setattr(response_list_views, "downloads_paused", paused)
    # A closed purge gate refuses new downloads; the list stays readable.
    close_purge_gate(harness.campaign)
    with restricted_download_pool(settings):
        response, body = search(admin, base + "submitted/csv/", {"timezone": "UTC"})
        assert response.status_code == 409
        assert b"prepared for purge" in body
        response, body = get(admin, base + "submitted/")
        assert response.status_code == 200
        assert b"Downloads are paused" in body
        assert b'<button type="submit" disabled>Download CSV</button>' in body
    events = AuditEvent.objects.filter(subject_id=harness.campaign.pk)
    assert events.filter(event_type="response_submitted_list_viewed").count() == 5
    assert events.filter(event_type="response_data_quality_list_viewed").count() == 2
    assert events.filter(event_type="response_data_quality_list_exported").count() == 1
    assert events.filter(
        event_type="response_submitted_list_exported",
        auditcontext__context__count=1,
    ).exists()
    # Each records its mode, Show choice and the snapshot its names came
    # from (#556), and nothing naming a Family.
    assert events.filter(
        event_type="response_submitted_list_viewed",
        auditcontext__context__contains={
            "report_mode": "testing",
            "report_filter": "uninvited",
            "snapshot_id": str(snapshot),
            "count": 1,
        },
    ).exists()
    assert events.get(
        event_type="response_data_quality_list_exported"
    ).auditcontext.context == {
        "outcome": "succeeded",
        "count": 2,
        "report_mode": "production",
        "report_filter": "all",
        "snapshot_id": str(snapshot),
    }
    assert events.filter(
        event_type="response_data_quality_list_viewed",
        auditcontext__context__report_filter="envelope",
    ).exists()
    for context in events.values_list("auditcontext__context", flat=True):
        assert name not in str(context)
    # Refused downloads are audited too, as failed.
    for event_type in (
        "response_submitted_list_exported",
        "response_started_list_exported",
    ):
        assert events.filter(
            event_type=event_type, auditcontext__context__outcome="failed"
        ).exists()
    # Staff read the Production lists but never Testing responses.
    store = auth_service.store
    staff = address("staff@example.org", roles=("staff",))
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "login_rules", **staff}],
        ).state
        == "applied"
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, base + "data-quality/")
        assert response.status_code == 200 and b"mode=testing" not in body
        assert listed_duids(body) == [BLANK_MAILING, ENVELOPE_ZERO]
        assert get(browser, base + "submitted/?mode=testing")[0].status_code == 403
        assert (
            search(browser, base + "submitted/csv/", {"mode": "testing"})[0].status_code
            == 403
        )
        # The dashboard links each list with its length.
        _, body = get(browser, base)
        assert (base + "submitted/").encode() in body
        assert b"Families that submitted</a>: 0" in body
