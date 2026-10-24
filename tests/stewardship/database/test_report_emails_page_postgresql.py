"""Mail › Report emails on the restricted web login (#1786, delivery item 5).

The page lists the current campaign's report emails with their status,
next and last send and row actions; Staff read it without recipients or
actions; a Ministry leader is refused. Pause, Resume and Delete record a
configuration change at once, which the real installer applies; a send in
progress blocks them. Each saved copy opens for Administrators and Staff.
"""

from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.report_email_models import ReportEmailManualRequest

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .report_builders import WEEKLY_INSTANT as INSTANT
from .report_builders import (
    add_digest,
    execute,
    produce,
    run_finalizers,
    set_user,
    settle_messages,
)
from .report_builders import weekly_allocated as allocated
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "admin:report_emails"
# Emailed reports and Send a weekly report now, retired with no redirect.
RETIRED = ("/admin/reports/emailed/", "/admin/reports/emailed/weekly/new/")


def apply_requests(store):
    """Install every recorded configuration request, as the installer does."""
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )
    from parishkit.stewardship.runtime_process import next_configuration_request

    settled = []
    while (identifier := next_configuration_request()) is not None:
        assert identifier not in settled, "the queue selected a request again"
        install_request(store, request_id=identifier, correlation_id=uuid4())
        settled.append(identifier)
    return settled


def report_values(identifier):
    """One report's applied values, or None once it is removed."""
    document = SystemConfiguration.objects.get().active_configuration.canonical_document
    return next(
        (
            row["values"]
            for row in document["sections"]["schedules"]
            if row["id"] == str(identifier)
        ),
        None,
    )


def weekly(harness):
    """A report shaped as the converted Weekly, its sends unblocked: its id."""
    identifier = add_digest(harness.service.store, harness.campaign, weekly=True)
    harness.campaign.refresh_from_db()
    if harness.campaign.state == "active":
        complete_empty_catchup(harness.campaign, uuid4())
    return identifier


def read(browser, query=""):
    """The page, read by the web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.get(reverse(PAGE) + query)


def post_change(browser, operation, identifier):
    """Post one row's Pause, Resume or Delete as its dialog does."""
    digest = SystemConfiguration.objects.get().active_configuration.digest
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            reverse("admin:report_email_changes"),
            {
                "base_digest": digest,
                "operation": operation,
                "report": str(identifier),
            },
            HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
        )


def test_the_page_lists_reports_for_administrators_and_staff(
    live_response_service, google
):
    """Administrators see recipients and actions; Staff read only."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        preparation = allocated(harness)
        settle_messages()
        admin, _ = signed_in()
        page = read(admin)
        assert page.status_code == 200 and page["Cache-Control"] == "no-store"
        html = page.content.decode()
        copy = reverse("admin:report_email_send", args=[preparation.pk])
        assert copy in html
        assert "All Administrators" in html
        for dialog in ("send-now", "pause", "delete"):
            assert f'data-confirm-open="report-{dialog}-dialog"' in html, dialog
        # Its name and Edit open Edit report email (#1786, item 6).
        edit = reverse("admin:report_email_edit", args=[preparation.definition_id])
        assert f'href="{edit}"' in html
        assert "data-reread-every" in html
        set_user(harness.service.store, "staff@example.org", "staff")
        google[0]["email"] = "staff@example.org"
        staff, _ = signed_in()
        page = read(staff)
        assert page.status_code == 200
        html = page.content.decode()
        assert copy in html and "All Administrators" not in html
        assert "data-confirm-open" not in html and f'href="{edit}"' not in html
        # Staff open the saved copy too (decision 11).
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert staff.get(copy).status_code == 200


def test_a_ministry_leader_and_unknown_options_are_refused(
    live_response_service, google
):
    """A leader reads no report emails (403); only ``requested`` is accepted."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        weekly(harness)
        admin, _ = signed_in()
        assert read(admin, "?search=x").status_code == 400
        assert read(admin, "?requested=not-an-id").status_code == 400
        assert read(admin, f"?requested={uuid4()}").status_code == 200
        set_user(harness.service.store, "leader@example.org", "ministry_leader")
        google[0]["email"] = "leader@example.org"
        leader, _ = signed_in()
        assert read(leader).status_code == 403


def test_past_sends_page_with_their_navigator(live_response_service, google):
    """Each report's past sends take their own page and size, on Report
    emails and on Edit; a malformed choice is refused (400), a page past the
    end shows the last one, and the refresh address keeps the choice."""
    from parishkit.stewardship.reports.report_email_list import sends_prefix

    harness = live_response_service
    with campaign_clock(INSTANT):
        preparation = allocated(harness)
        settle_messages()
        admin, _ = signed_in()
        prefix = sends_prefix(preparation.definition_id)
        page = read(admin, f"?{prefix}page=4&{prefix}size=50")
        assert page.status_code == 200
        html = page.content.decode()
        assert f'id="{prefix[:-1]}-table"' in html
        assert "Page 1 of 1" in html and 'aria-label="Past sends pages of' in html
        assert (
            f'data-refresh-url="/admin/mail/report-emails/?{prefix}page=4&amp;' in html
        )
        assert reverse("admin:report_email_send", args=[preparation.pk]) in html
        assert read(admin, f"?{prefix}size=7").status_code == 400
        assert read(admin, f"?{prefix}page=x").status_code == 400
        assert read(admin, f"?{prefix}sort=made").status_code == 400
        edit = reverse("admin:report_email_edit", args=[preparation.definition_id])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            answer = admin.get(f"{edit}?{prefix}page=2")
            assert answer.status_code == 200
            assert (
                f'id="{prefix[:-1]}-table" data-table-region' in answer.content.decode()
            )
            other = sends_prefix(uuid4())
            assert admin.get(f"{edit}?{other}page=2").status_code == 400


def test_pause_resume_and_delete_apply_in_place(live_response_service, google):
    """Each records one change; once applied the row shows it."""
    harness = live_response_service
    store = harness.service.store
    with campaign_clock(INSTANT):
        identifier = weekly(harness)
        admin, _ = signed_in()
        response = post_change(admin, "pause", identifier)
        assert response.status_code == 302, response.content
        assert response["Location"].startswith("/admin/changes/")
        assert len(apply_requests(store)) == 1
        assert report_values(identifier)["paused_ranges"][-1]["end"] is None
        html = read(admin).content.decode()
        assert 'data-report-status="paused"' in html
        assert 'data-confirm-open="report-resume-dialog"' in html
        # Pausing again is refused: nothing is recorded.
        requests = ConfigurationChangeRequest.objects.count()
        assert post_change(admin, "pause", identifier).status_code == 400
        assert ConfigurationChangeRequest.objects.count() == requests
        assert post_change(admin, "resume", identifier).status_code == 302
        apply_requests(store)
        assert report_values(identifier)["paused_ranges"][-1]["end"] is not None
        assert post_change(admin, "delete", identifier).status_code == 302
        apply_requests(store)
        assert report_values(identifier) is None
        assert "This campaign has no report emails." in read(admin).content.decode()


def test_a_send_in_progress_greys_the_rows_actions(live_response_service, google):
    """While a send is being prepared, or its emails delivered, the row's
    actions stay in place, unavailable, with why (the action matrix); the
    server's own refusal of blocking work is the shared work summary's."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        weekly(harness)
        statuses = produce()  # The scheduled send is allocated, not yet run.
        assert statuses
        admin, _ = signed_in()
        for _step in ("prepared", "delivered"):
            html = read(admin).content.decode()
            assert 'data-report-status="sending"' in html
            assert "A send of this report is in progress" in html
            for dialog in ("send-now", "pause", "delete"):
                assert f'data-confirm-open="report-{dialog}-dialog"' not in html
            # Prepare the send: its emails are then being delivered.
            for status in statuses:
                execute(status)
            statuses = []
        settle_messages()
        run_finalizers()  # The send completes once its emails are delivered.
        html = read(admin).content.decode()
        status = html.split('data-report-status="')[1].split('"')[0]
        assert status == "upcoming", status
        assert 'data-confirm-open="report-pause-dialog"' in html


def test_a_stale_page_and_unknown_fields_are_refused(live_response_service, google):
    """A change drawn from an older configuration, or a crafted post, changes
    nothing."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        identifier = weekly(harness)
        admin, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            token = admin.cookies["pk_admin_csrf"].value
            path = reverse("admin:report_email_changes")
            stale = admin.post(
                path,
                {"base_digest": "0" * 64, "operation": "pause", "report": identifier},
                HTTP_X_CSRFTOKEN=token,
            )
            assert stale.status_code == 409
            crafted = admin.post(
                path,
                {"base_digest": "0" * 64, "operation": "edit", "report": identifier},
                HTTP_X_CSRFTOKEN=token,
            )
            assert crafted.status_code == 400
        assert apply_requests(harness.service.store) == []


def test_the_quiet_re_read_never_extends_the_sign_in(live_response_service, google):
    """The page's 30-second re-read renews no idle deadline (#1586)."""
    from parishkit.stewardship.accounts.models import PortalSession

    harness = live_response_service
    with campaign_clock(INSTANT):
        weekly(harness)
        admin, _ = signed_in()
        assert read(admin).status_code == 200
        seen = dict(PortalSession.objects.values_list("pk", "last_activity_at"))
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            again = admin.get(reverse(PAGE), HTTP_X_PARISHKIT_READ="passive")
        assert again.status_code == 200
        assert dict(PortalSession.objects.values_list("pk", "last_activity_at")) == seen


def test_an_ordinary_open_admits_a_session_whose_roles_changed(
    live_response_service, google
):
    """A user whose role changed while signed in still opens the page: an
    ordinary open refreshes the session as every page does."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        weekly(harness)
        set_user(harness.service.store, "staff@example.org", "staff")
        google[0]["email"] = "staff@example.org"
        reader, _ = signed_in()
        # Promoted while signed in: the session's roles are now out of date.
        set_user(harness.service.store, "staff@example.org", "administrator")
        page = read(reader)
        assert page.status_code == 200
        assert b'data-confirm-open="report-pause-dialog"' in page.content


def test_the_retired_pages_are_gone(live_response_service, google):
    """Emailed reports and Send a weekly report now answer 404, no redirect."""
    with campaign_clock(INSTANT):
        admin, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for path in RETIRED:
                assert admin.get(path).status_code == 404, path


def test_staff_may_not_change_or_send(live_response_service, google):
    """Staff read the page; its changes and Send now are refused (403)."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        identifier = weekly(harness)
        set_user(harness.service.store, "staff@example.org", "staff")
        google[0]["email"] = "staff@example.org"
        staff, _ = signed_in()
        assert read(staff).status_code == 200
        requests = ConfigurationChangeRequest.objects.count()
        assert post_change(staff, "pause", identifier).status_code == 403
        configuration = SystemConfiguration.objects.get().active_configuration_id
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = staff.post(
                reverse("admin:report_email_send_now"),
                {
                    "report": str(identifier),
                    "revision": str(uuid4()),
                    "command_id": str(uuid4()),
                    "configuration_id": str(configuration),
                },
                HTTP_X_CSRFTOKEN=staff.cookies["pk_admin_csrf"].value,
            )
        assert response.status_code == 403
        assert ConfigurationChangeRequest.objects.count() == requests
        assert not ReportEmailManualRequest.objects.exists()
