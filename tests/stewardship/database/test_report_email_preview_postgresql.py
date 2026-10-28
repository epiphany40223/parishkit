"""Preview of a report email, on the restricted web login (#1786, #2035).

The report-emails spec's "Preview": a saved report's row action
(``report_email_render``), the editor's Preview of the unsaved form and
``report-email render`` draw the report from current data with the one
compiler a send uses, in the review dialog, and record only
``report_email_previewed``: no occurrence, preparation, outbox row,
snapshot, task, fact generation or fact pin. A Daily-shaped report
previewed at its send's clock draws exactly the email the send made; a
Weekly-shaped one draws it too, with the saved copy's links pointed at the
Report emails page and the live requests, since a preview has no saved
copy.
"""

import json
import re
import time
import zlib
from datetime import UTC, datetime
from html import unescape
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core import signing
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.reports.facts import FactBusy, FactUnavailable
from parishkit.stewardship.reports.models import (
    CampaignDailyFactSet,
    CampaignFactPin,
    CampaignFactPointer,
    CampaignFactRebuildDemand,
)
from parishkit.stewardship.reports.report_document import send_path
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailManualRequest,
    ReportEmailPreparation,
    ReportEmailSnapshot,
)
from parishkit.stewardship.reports.report_email_preview import (
    IMAGE_SECONDS,
    NoSource,
    preview_report,
)
from parishkit.stewardship.source.snapshot_models import SourceSnapshotPin

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .report_builders import (
    ORIGIN,
    WEEKLY_INSTANT,
    add_digest,
    allocated,
    set_user,
    weekly_allocated,
)
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_report_email_copy_postgresql import listed_request
from .test_report_email_editor_postgresql import fields, post, started
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "report_email_previewed"
PREVIEW = "parishkit.stewardship.reports.report_email_preview"
IMAGE = re.compile(r'src="([^"]*/report-emails/previews/[^"/]+/[^"/]+\.png)"')


def written():
    """The rows a preview must never write, counted: every send, fact and
    pin table, and every audit event but the preview's own."""
    return {
        "occurrences": ScheduleOccurrence.objects.count(),
        "manual_requests": ReportEmailManualRequest.objects.count(),
        "preparations": ReportEmailPreparation.objects.count(),
        "snapshots": ReportEmailSnapshot.objects.count(),
        "outbox": OutboxMessage.objects.count(),
        "tasks": TaskRun.objects.count(),
        "facts": CampaignDailyFactSet.objects.count(),
        "fact_pointers": CampaignFactPointer.objects.count(),
        "fact_pins": CampaignFactPin.objects.count(),
        "fact_rebuilds": CampaignFactRebuildDemand.objects.count(),
        "source_pins": SourceSnapshotPin.objects.count(),
        "other_events": AuditEvent.objects.exclude(event_type=EVENT).count(),
    }


def render_row(browser, report):
    """Post a row's Preview as its dialog's form does."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            reverse("admin:report_email_render"),
            {"report": str(report)},
            HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
        )


def events():
    """How many previews have been recorded."""
    return AuditEvent.objects.filter(event_type=EVENT).count()


def system_and_campaign():
    """The configuration row and the current campaign, as the page reads them."""
    system = SystemConfiguration.objects.select_related(
        "active_configuration__parish", "current_campaign__active_configuration"
    ).get()
    return system, system.current_campaign


# A Daily-shaped report added on the campaign's first day (October 1, 2054,
# New York time) sends its first report, for that day alone, after midnight.
ADDED = datetime(2054, 10, 1, 14, tzinfo=UTC)
FIRST_SEND = datetime(2054, 10, 2, 5, tzinfo=UTC)


def daily_sent(family_mail):  # noqa: F811
    """A Daily-shaped report and its first send, for one day: id and snapshot.

    Added on the campaign's first day, so its first send is a scheduled
    send of one report day, not a recovery send of the days before it.
    """
    store, campaign = family_mail.service.store, family_mail.campaign
    with campaign_clock(ADDED):
        identifier = add_digest(store, campaign)
    with campaign_clock(FIRST_SEND):
        preparation = allocated(family_mail, identifier=identifier)
    return identifier, ReportEmailSnapshot.objects.get(preparation=preparation)


def test_a_preview_draws_what_a_send_drew_and_writes_nothing(family_mail, google):  # noqa: F811
    """Previewed at its send's clock, the Daily draws the send's subject, HTML
    and text from the send's own fact generation, not Updating; the row's
    Preview answers the dialog's fragment, records one event and nothing
    else, and its chart image is drawn again for the Administrator only."""
    identifier, snapshot = daily_sent(family_mail)
    with campaign_clock(FIRST_SEND):
        values = snapshot.values
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            read_transaction(),
        ):
            system, campaign = system_and_campaign()
            preview = preview_report(values, system, campaign, public_origin=ORIGIN)
        assert not preview.updating
        assert preview.fact_set_id == snapshot.fact_set_id
        assert (preview.subject, preview.html, preview.text) == (
            snapshot.subject,
            snapshot.html,
            snapshot.text,
        )
        assert [image.content_id for image in preview.images] == [
            name for name, _png in snapshot.images
        ]
        assert preview.recipients == snapshot.recipients
        browser, _ = signed_in()
        before, seen = written(), events()
        answer = render_row(browser, identifier)
        assert answer.status_code == 200, answer.content[:400]
        assert answer["Cache-Control"] == "no-store"
        body = answer.content.decode()
        assert "data-review-answer" in body and "data-report-preview" in body
        # No signed preview: the dialog offers no Apply.
        assert 'name="preview"' not in body
        assert "Updating" not in body
        assert snapshot.subject in unescape(body)
        assert 'data-view-show="report-preview-text"' in body
        assert written() == before
        assert events() == seen + 1
        event = AuditEvent.objects.filter(event_type=EVENT).latest("created_at")
        assert event.subject_id == identifier
        (image,) = IMAGE.findall(body)
        path = unescape(image)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            drawn = browser.get(path)
        assert drawn.status_code == 200, drawn.content
        assert drawn["Content-Type"] == "image/png"
        assert drawn["Cache-Control"] == "no-store"
        assert drawn.content.startswith(b"\x89PNG")
        assert written() == before
        # An altered recipe draws nothing.
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            token, name = path.rsplit("/", 2)[-2:]
            # Signed, not encrypted: the readable recipe holds no free text.
            payload = signing.b64_decode(token.split(":")[0].lstrip(".").encode())
            recipe = json.loads(zlib.decompress(payload))
            assert set(recipe) == {"c", "f", "t", "w", "a", "b"}
            # The Preview's submission watermark, which holds its report
            # day against a response entered after the close (#2298).
            assert isinstance(recipe["w"], int)
            assert values["name"] not in json.dumps(recipe)
            altered = path.replace(token, token[:-6] + "AAAAAA")
            assert browser.get(altered).status_code == 404
            # A name that is no preview image's is refused before drawing.
            unknown = path.replace(name, "nothing@parishkit.png")
            with patch(f"{PREVIEW}.build_document") as build:
                assert browser.get(unknown).status_code == 404
            assert not build.called
            # The recipe expires after its fifteen minutes.
            later = time.time() + IMAGE_SECONDS + 60
            with patch("django.core.signing.time.time", return_value=later):
                assert browser.get(path).status_code == 404
        assert written() == before
        # Another Administrator cannot use this one's recipe.
        store = family_mail.service.store
        set_user(store, "second@example.org", "administrator", name="Second, Admin")
        # Another person: their own Google account, so their own identity.
        google[0].update(email="second@example.org", sub="synthetic-second-subject")
        second, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert second.get(path).status_code == 404
        # Staff are refused the image outright.
        set_user(store, "staff@example.org", "staff")
        google[0].update(email="staff@example.org", sub="synthetic-staff-subject")
        staff, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert staff.get(path).status_code == 403


@pytest.mark.parametrize(
    ("target", "error", "words"),
    [
        ("read_fact_set", FactBusy("busy"), "being updated right now"),
        ("digest_funnel", FactUnavailable("gone"), "being updated right now"),
        ("_observe", NoSource("none"), "no ParishSoft data has been loaded"),
    ],
)
def test_a_refusal_says_why(family_mail, google, target, error, words):  # noqa: F811
    """Figures locked or replaced while read say to try again, never that
    no data is loaded; only a campaign with no ParishSoft data says that.
    Nothing is recorded."""
    identifier, _snapshot = daily_sent(family_mail)
    with campaign_clock(FIRST_SEND):
        browser, _ = signed_in()
        seen, before = events(), written()
        with patch(f"{PREVIEW}.{target}", side_effect=error):
            answer = render_row(browser, identifier)
        assert answer.status_code == 404
        text = unescape(answer.content.decode())
        assert words in text, text[:400]
        assert (events(), written()) == (seen, before)


# A Weekly-shaped report added the day before a Wednesday sends its first
# report just after midnight.
WEEKLY_ADDED = datetime(2054, 10, 27, 14, tzinfo=UTC)
WEEKLY_SEND = datetime(2054, 10, 28, 5, tzinfo=UTC)


def relinked(drawn, send):
    """A send's drawn email with its saved copy's links pointed where a
    preview points them: requests to the live requests, the closing button
    to the Report emails page (spec, "Shared email layout")."""
    saved = ORIGIN + send_path(send)
    live = ORIGIN + reverse("admin:information_queue")
    return drawn.replace(f"{saved}items/", live).replace(
        saved, ORIGIN + reverse("admin:report_emails")
    )


def test_a_weekly_preview_draws_its_send_with_live_links(live_response_service, google):
    """Previewed at its send's clock, a Weekly (no fact block) draws the
    send's subject, HTML and text exactly but for the links: each request
    links the live request and "Open protected report" the Report emails
    page, never a saved copy. The row's dialog draws the same, read with the
    web login's own rights, and writes nothing."""
    harness = live_response_service
    respond(harness, "Please call about the parish picnic")
    with campaign_clock(WEEKLY_ADDED):
        admin = started(harness)
        identifier = add_digest(harness.service.store, harness.campaign, weekly=True)
    with campaign_clock(WEEKLY_SEND):
        preparation = weekly_allocated(harness, identifier=identifier)
        snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
        listed = listed_request(snapshot)
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            read_transaction(),
        ):
            system, campaign = system_and_campaign()
            preview = preview_report(
                snapshot.values, system, campaign, public_origin=ORIGIN
            )
        # The live campaign's first Weekly is a recovery send; a preview is
        # drawn as a Send now draws it, so its subject carries no such note.
        assert "recovery, " in snapshot.subject
        assert preview.subject == snapshot.subject.replace("recovery, ", "")
        assert preview.html == relinked(snapshot.html, preparation.pk)
        assert preview.text == relinked(snapshot.text, preparation.pk)
        emails = ORIGIN + reverse("admin:report_emails")
        live = ORIGIN + reverse("admin:information_item", kwargs={"item_id": listed})
        assert "/sends/" not in preview.html + preview.text
        assert f'href="{live}"' in preview.html
        assert f'href="{emails}"' in preview.html
        assert f"Open protected report (staff login required): {emails}" in (
            preview.text
        )
        assert f"\n{live}" in preview.text
        before = written()
        answer = render_row(admin, preparation.definition_id)
        assert answer.status_code == 200, answer.content[:400]
        body = unescape(answer.content.decode())
        assert "1 open request" in body
        assert "Please call about the parish picnic" in body
        # The page draws with the portal's own public origin.
        for target in (live, emails):
            assert re.search(
                f'href="https://[^"/]+{target.removeprefix(ORIGIN)}"', body
            )
        assert "/sends/" not in body
        assert written() == before


def test_the_editor_previews_the_unsaved_form(live_response_service, google):
    """Preview posts the form as it is, saving nothing; a refused field is
    answered at its field (400) with nothing recorded."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        admin = started(harness)
        path = reverse("admin:report_email_new")
        version = SystemConfiguration.objects.get().active_configuration_id
        seen, before = events(), written()
        answer = post(admin, path, fields(render="yes", name="Unsaved report"))
        assert answer.status_code == 200, answer.content[:400]
        assert "data-report-preview" in answer.content.decode()
        assert "Unsaved report" in unescape(answer.content.decode())
        assert events() == seen + 1
        event = AuditEvent.objects.filter(event_type=EVENT).latest("created_at")
        assert event.subject_id == harness.campaign.pk
        assert SystemConfiguration.objects.get().active_configuration_id == version
        assert written() == before
        refused = post(admin, path, fields(render="yes", name="", block=[]))
        assert refused.status_code == 400
        assert 'id="report-email-form"' in refused.content.decode()
        assert events() == seen + 1
        # Staff are refused the editor's Preview, with nothing recorded.
        set_user(harness.service.store, "staff@example.org", "staff")
        google[0]["email"] = "staff@example.org"
        staff, _ = signed_in()
        assert post(staff, path, fields(render="yes")).status_code == 403
        assert events() == seen + 1


def test_only_administrators_preview(live_response_service, google):
    """Staff are refused (403), and an id that is no report's is refused as an
    unknown request (400); neither records a preview."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        admin = started(harness)
        identifier = add_digest(harness.service.store, harness.campaign, weekly=True)
        seen = events()
        assert render_row(admin, uuid4()).status_code == 400
        set_user(harness.service.store, "staff@example.org", "staff")
        google[0]["email"] = "staff@example.org"
        staff, _ = signed_in()
        assert render_row(staff, identifier).status_code == 403
        assert events() == seen


def test_render_prints_the_preview(
    live_response_service,
    google,
    cli,  # noqa: F811
):
    """``report-email render`` is the row's Preview: subject, text and
    recipients, never the HTML; an unknown report is not_available, not
    recorded."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        started(harness)
        identifier = add_digest(harness.service.store, harness.campaign, weekly=True)
        run = cli()
        seen, before = events(), written()
        code, document = run("report-email", "render", str(identifier))
        assert code == 0, document
        result = document["result"]
        assert result["id"] == str(identifier)
        assert result["name"] == "Weekly report"
        assert "Open additional information requests" in result["text"]
        # The same live links the dialog draws, never a send that is not.
        assert "/sends/" not in result["text"]
        assert re.search(
            r"Open protected report \(staff login required\): "
            r"\S+/admin/mail/report-emails/$",
            result["text"],
            re.M,
        ), result["text"]
        assert result["as_of"] == "send_time" and result["updating"] is False
        assert result["recipients"]["groups"][0]["name"] == "All Administrators"
        assert "html" not in result
        assert events() == seen + 1
        assert written() == before
        code, document = run("report-email", "render", str(uuid4()))
        assert code == 1 and document["error"]["code"] == "not_available", document
        assert events() == seen + 1
