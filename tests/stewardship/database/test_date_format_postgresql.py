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

from ..test_information_rendering import card_text
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_parish_views_postgresql import requested
from .test_policy_postgresql import user
from .test_response_http_postgresql import load_form
from .test_response_revisit_postgresql import revisit
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/parish/settings/"


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
    request = ConfigurationChangeRequest.objects.get(pk=requested(response))
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
            (
                dates.current(),
                list(card_text(information_rendering.information_records(document))),
            )
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
        label == "Requested at"
        and text.startswith(requested.strftime("%d.%m.%Y %H:%M"))
        for label, text in lines
    )


@pytest.mark.parametrize("role", [ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH])
def test_background_roles_can_read_the_active_date_format(live_response_service, role):
    """Workers and mail dispatch format dates with the Admin's chosen style."""
    choose(live_response_service.service.store, "eu_dot")
    with task_login(role, exact=True, reconnect=True):
        assert active_date_format() == "eu_dot"


# Background mail pins the date format of the configuration it captured (#280).
# Each test changes the Parish format after capture and lends the worker the
# new active style, as the broker does, so only a pinned render stays put.


def test_daily_digest_keeps_its_snapshot_date_format(response_service):
    """A daily digest compiled after a format change keeps the snapshot's style."""
    from functools import partial

    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.reports.daily_digest import render_daily_digest
    from parishkit.stewardship.reports.digest_building import (
        admit_daily_facts,
        begin_daily_facts,
        load_daily_document,
    )
    from parishkit.stewardship.reports.digest_capture import capture_daily_snapshot
    from parishkit.stewardship.reports.materialization import materialize_fact_set

    from .campaign_builders import campaign_clock
    from .test_daily_digest_capture_postgresql import prepare
    from .test_daily_digest_planning_postgresql import INSTANT

    harness = response_service
    with campaign_clock(INSTANT):
        claim = prepare(harness)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            capture_daily_snapshot(claim)
        choose(harness.service.store, "eu_dot")
        with task_login(ServiceRole.WORKER, exact=True):
            with work_transaction():
                facts = begin_daily_facts(claim)
            materialize_fact_set(
                facts.pk, claim, admit=partial(admit_daily_facts, claim)
            )
            with work_transaction():
                document = load_daily_document(claim, facts.pk)
            with dates.using(active_date_format()):
                assert dates.current() == "eu_dot"
                content = render_daily_digest(
                    document, public_origin="https://parish.example"
                )
    last = document.covered_dates[-1]
    assert document.date_format in (None, "us_long")
    assert content.subject.endswith(" — " + dates.format_date(last, "us_long")) or (
        content.subject.endswith(" through " + dates.format_date(last, "us_long"))
    )
    assert dates.format_date(last, "eu_dot") not in content.subject
    row = dates.format_date(last, "us_long", compact=True)
    assert row in content.text
    assert dates.format_date(last, "eu_dot", compact=True) not in content.text


def test_weekly_digest_keeps_its_snapshot_date_format(live_response_service):
    """A weekly digest compiled after a format change keeps the snapshot's style."""
    from .campaign_builders import campaign_clock
    from .test_weekly_capture_postgresql import INSTANT
    from .test_weekly_fanout_postgresql import captured, detached

    harness = live_response_service
    respond(harness, "Dated weekly request")
    with campaign_clock(INSTANT):
        claim, _ = captured(harness)
        choose(harness.service.store, "eu_dot")
        with dates.using("eu_dot"):
            page, [content] = detached(claim)
    [plan] = page.recipients
    document = plan.document
    zone = ZoneInfo(document.campaign_timezone)
    observed = document.observed_at.astimezone(zone)
    assert document.date_format in (None, "us_long")
    assert content.subject.endswith(dates.format_date(observed.date(), "us_long"))
    # The body names no capture time (#720); its rows' submitted times use
    # the pinned style, compact.
    submitted = document.information[0].submitted_at.astimezone(zone)
    assert dates.format_local(submitted, "us_long", compact=True) in content.text
    assert dates.format_local(submitted, "eu_dot", compact=True) not in content.text


def test_receipt_uses_its_pinned_configuration_date_format(live_response_service):
    """A receipt stamp follows its pinned configuration; a sent one never changes."""
    from parishkit.stewardship.family_delivery import FamilyDeliveryResult
    from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
    from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
    from parishkit.stewardship.jobs.outbox_models import OutboxMessage

    from .test_family_mail_dispatch_postgresql import claim
    from .test_receipt_dispatch_postgresql import begin, receipt

    harness = live_response_service
    store = harness.service.store
    message = receipt(harness, production=True)
    submitted = Submission.objects.get()
    timezone = harness.campaign.active_configuration.timezone
    choose(store, "eu_dot")
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        # A stale worker style (the broker's per-message cache) cannot leak in.
        with dates.using("us_long"):
            mail, _, configuration, _ = begin(message, execution)
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    assert configuration == SystemConfiguration.objects.get().active_configuration_id
    stamp = dates.format_instant(submitted.submitted_at, timezone, "eu_dot")
    assert f"Submitted: {stamp}" in mail.text
    sent = OutboxMessage.objects.select_related("render").get(pk=message.pk)
    assert sent.state == "delivered" and f"Submitted: {stamp}" in sent.render.text
    # A later change leaves the delivered receipt's retained render untouched.
    choose(store, "iso")
    again = OutboxMessage.objects.select_related("render").get(pk=message.pk)
    assert again.render.text == sent.render.text
