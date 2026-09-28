"""The parish date format reaches Admin, Family, placeholder and export output."""

import re
from datetime import date
from html import unescape
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.accounts.branding_context import active_date_format
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.campaign_mail_values import (
    campaign_values,
    document_parish,
)
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.reports import information_rendering
from parishkit.stewardship.reports.export_services import TASK_TYPE
from parishkit.stewardship.reports.export_tasks import export_handler
from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.reports.information_exports import create_information_export
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.responses.page_content import public_substitutions
from parishkit.stewardship.web import dates

from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_policy_postgresql import user
from .test_response_http_postgresql import load_form
from .test_response_revisit_postgresql import revisit
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/configuration/parish"


def choose(store, style):
    """Apply one parish date format through the real configuration installer."""
    version = store.active()
    record = version.document()["sections"]["parish"][0]
    change(
        store,
        version,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": record["id"],
                "values": {"date_format": style},
            }
        ],
    )


def post(browser, values):
    """Post with the Admin namespace's real CSRF cookie."""
    return browser.post(
        URL, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def test_parish_settings_choose_the_format_for_admin_pages(auth_service, google):
    """The Admin picks a style by its example; pages then carry it for scripts."""
    store = auth_service.store
    browser, _ = signed_in()
    page = browser.get(URL).content.decode()
    assert 'data-date-format="us_long"' in page
    assert re.search(r'<option value="us_long" selected>January 1, 2027', page)
    version = store.active()
    profile = version.document()["sections"]["parish"][0]["values"]
    fields = {name: profile[name] for name in ("name", "website", "timezone", "phone")}
    preview = post(
        browser,
        fields
        | {
            "date_format": "eu_long",
            "base_digest": version.digest,
            "action": "preview",
        },
    )
    assert preview.status_code == 200, preview.content
    body = preview.content.decode()
    # The preview names both choices by their example, never by an internal code.
    assert "January 1, 2027" in body and "1 January 2027" in body
    proposal = unescape(re.search(r'name="preview" value="([^"]+)"', body).group(1))
    response = post(browser, {"action": "confirm", "preview": proposal})
    assert response.status_code == 302
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    assert install_request(store, request_id=request.pk, correlation_id=uuid4())
    values = store.active().document()["sections"]["parish"][0]["values"]
    assert values["date_format"] == "eu_long"
    assert 'data-date-format="eu_long"' in browser.get("/admin/").content.decode()


def test_family_pages_placeholders_mail_and_exports_follow_the_setting(
    live_response_service, google, tmp_path, settings, monkeypatch
):
    """One setting changes the Family page, email/page values and PDF exports."""
    harness = live_response_service
    respond(harness, "Dated request")
    store = harness.service.store
    choose(store, "eu_dot")
    parish = SystemConfiguration.objects.get().active_configuration.parish
    assert parish.date_format == "eu_dot" and active_date_format() == "eu_dot"

    # Family pages: the chrome names the style and server-side times use it.
    harness, _, _, _ = revisit(harness)
    assert b'data-date-format="eu_dot"' in harness.client.get("/family/").content
    submitted = Submission.objects.get()
    timezone = harness.campaign.active_configuration.timezone
    assert load_form(harness)["last_submitted_display"] == dates.format_instant(
        submitted.submitted_at, timezone, "eu_dot"
    )

    # Page placeholders (request side) and email values (worker side).
    campaign = harness.campaign.active_configuration
    start = date.fromisoformat(campaign.values["start_date"]).strftime("%d.%m.%Y")
    assert public_substitutions(parish, campaign)["campaign_start"] == start
    mail = campaign_values(
        parish=document_parish(store.active().document()), campaign=campaign.values
    )
    assert mail["campaign_start"] == start

    # Exports: the worker renders a PDF in the requesting configuration's style.
    signed_in()
    root = tmp_path / "reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    seen = []
    original = information_rendering.information_pdf

    def capture(document, output):
        """Record the style and the drawn lines, then render normally."""
        seen.append(
            (dates.current(), list(information_rendering.information_lines(document)))
        )
        return original(document, output)

    monkeypatch.setattr(information_rendering, "information_pdf", capture)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        request = create_information_export(
            store,
            user("admin@example.org").pk,
            campaign_id=harness.campaign.pk,
            query=InformationQuery(),
            history=False,
            format="pdf",
            browser_timezone="America/Detroit",
            request_key=uuid4(),
        )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            request.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: export_handler(store=store, root=root)},
        )
    [(style, lines)] = seen
    requested = request.created_at.astimezone(ZoneInfo("America/Detroit"))
    assert style == "eu_dot"
    assert any(
        line.startswith("Requested at: " + requested.strftime("%d.%m.%Y %H:%M"))
        for line in lines
    )
