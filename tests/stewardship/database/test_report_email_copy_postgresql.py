"""A report send's saved copy page and its requests' pages (#1786, #1946).

Every report email links its send's saved copy
(``/admin/mail/report-emails/sends/<send>/``). The page draws each block
from the send's snapshot as web components (report-emails spec, "Saved
copies"), keeps the email as it was sent, with its chart images, in a
disclosure, and gives each listed request its own page linking the live
request. Administrators and Staff may open them; each opening is audited
with the send only. Outside addresses are shown to Administrators only.
"""

import json
import re
from dataclasses import replace
from html import unescape
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.reports.report_email_models import ReportEmailSnapshot
from parishkit.stewardship.responses.models import AdditionalInformationItem

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .report_builders import (
    DAILY_INSTANT,
    WEEKLY_INSTANT,
    add_report,
    set_user,
    weekly_allocated,
)
from .report_builders import allocated as daily_allocated
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import submit
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

OUTSIDE = "outsider@example.net"


def copy_path(preparation):
    """The saved copy page of one send."""
    return reverse("admin:report_email_send", args=[preparation.pk])


def viewed(preparation):
    """How many openings of the send's copy or its requests were audited."""
    return AuditEvent.objects.filter(
        event_type="report_email_viewed", subject_id=preparation.pk
    ).count()


def blocks_region(body):
    """The page's drawn blocks, from its recipients panel to the as-sent email."""
    start = body.index('aria-labelledby="copy-recipients-heading"')
    return body[start : body.index("data-copy-email")]


def open_copy(path):
    """Open one saved copy page as the signed-in fixture Administrator."""
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.get(path)


def daily_with_outside(harness):
    """A report shaped as the converted Daily that also mails an outside address."""
    from parishkit.stewardship.accounts.reports_conversion import converted_values

    values = converted_values(
        {
            "campaign_id": str(harness.campaign.pk),
            "kind": "daily_digest",
            "time": "00:15:00",
            "weekday": None,
            "subject": "",
            "comparison": False,
        },
        None,
    )
    values["recipients"] = values["recipients"] + [{"address": OUTSIDE}]
    return add_report(harness.service.store, harness.campaign, **values)


def test_an_administrator_opens_the_blocks_and_the_email_as_sent(family_mail, google):  # noqa: F811
    """The blocks come from the snapshot, in its order; the email keeps its charts."""
    with campaign_clock(DAILY_INSTANT):
        preparation = daily_allocated(family_mail)
        snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
        browser, _ = signed_in()
        path = copy_path(preparation)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(path)
            assert page.status_code == 200
            assert page["Cache-Control"] == "no-store"
            body = page.content.decode()
            # Each block is drawn in the send's order.
            drawn = re.findall(r'data-copy-block="([a-z_]+)"', body)
            assert drawn == [entry["block"] for entry in snapshot.blocks]
            text = unescape(body)
            # The opening line without "yesterday", from the kept report
            # day; the fixture's first send covers every campaign day so far.
            assert "Daily report — October 1, 2054 through October 8, 2054" in text
            assert "All data is as of the end of October 8, 2054." in text
            assert "Scheduled, covering October 1, 2054 through October 8" in text
            assert "All Administrators (1 person)" in text
            assert "sent in Testing mode" in text
            # The Campaign totals are stat tiles in the email's words.
            ((_, totals),) = [
                (entry["block"], entry["data"])
                for entry in snapshot.blocks
                if entry["block"] == "campaign_totals"
            ]
            for _label, value, *_rest in totals["figures"]:
                assert value.partition(" out of ")[0] in text
            assert 'data-stat="responded"' in body
            # The participation graph through the chart engine, with no
            # retained image outside the email as sent.
            spec = re.search(
                r'<script id="daily-participation-spec" type="application/json">'
                r"(.*?)</script>",
                body,
                re.S,
            )
            # The snapshot records the charts the email drew (#1956): with
            # no choice, every chart this campaign offers (it has no
            # financial stewardship, so no pledges), and every one is drawn.
            assert next(
                entry["data"]["charts"]
                for entry in snapshot.blocks
                if entry["block"] == "participation_charts"
            ) == ["first_submissions", "cumulative_families"]
            titles = [
                panel["title"]["text"] for panel in json.loads(spec[1])["vconcat"]
            ]
            assert titles[:2] == [
                "First submissions that day",
                "Cumulative participating Families",
            ]
            # Its sentence names every panel's figures, as it always has.
            assert " of them for the first time that day." in chart_sentence(body)
            assert "chart-v1.js" in body
            # The email as it was sent, with its own chart images.
            sent = body[body.index("data-copy-email") :]
            names = re.findall(r'src="images/([^"]+)\.png"', sent)
            assert names == [cid for cid, _ in snapshot.images]
            assert "cid:" not in body
            image = browser.get(f"{path}images/{names[0]}.png")
            assert image.status_code == 200 and image["Content-Type"] == "image/png"
            assert image.content.startswith(b"\x89PNG")
            assert browser.get(f"{path}images/missing.png").status_code == 403
            # A query string is refused; an unknown send is denied.
            assert browser.get(path + "?view=x").status_code == 400
            unknown = reverse("admin:report_email_send", args=[uuid4()])
            assert browser.get(unknown).status_code == 403
        assert viewed(preparation) == 1


def chart_sentence(body):
    """The sentence the saved copy shows under its participation chart."""
    caption = r'<figcaption id="daily-participation-summary"[^>]*>(.*?)<'
    return re.search(caption, body)[1]


def chart_titles(page):
    """The saved copy page's participation panels, top to bottom, by title,
    and the sentence under them."""
    assert page.status_code == 200
    body = unescape(page.content.decode())
    spec = re.search(
        r'<script id="daily-participation-spec" type="application/json">'
        r"(.*?)</script>",
        body,
        re.S,
    )
    titles = [panel["title"]["text"] for panel in json.loads(spec[1])["vconcat"]]
    return titles, chart_sentence(body)


def test_the_copy_draws_only_the_charts_the_snapshot_recorded(family_mail, google):  # noqa: F811
    """Charts the snapshot recorded (#1956) are the copy's only panels."""
    from parishkit.stewardship.reports.report_blocks import REGISTRY

    block = REGISTRY["participation_charts"]

    def one_chart(document, options):
        """Capture as #1956's block records a one-chart choice."""
        return block.capture(document, options) | {"charts": ["cumulative_families"]}

    with campaign_clock(DAILY_INSTANT):
        recorded = replace(block, capture=one_chart)
        with patch.dict(REGISTRY, {"participation_charts": recorded}):
            preparation = daily_allocated(family_mail)
        snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
        (kept,) = [
            entry["data"]
            for entry in snapshot.blocks
            if entry["block"] == "participation_charts"
        ]
        assert kept["charts"] == ["cumulative_families"]
        one, sentence = chart_titles(open_copy(copy_path(preparation)))
        assert one == ["Cumulative participating Families"]
        # The sentence names only that chart's figure (#1946).
        assert sentence.endswith(" Families had responded.")
        assert "first time" not in sentence and "pledges" not in sentence


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_staff_may_open_it_and_a_ministry_leader_may_not(
    family_mail,  # noqa: F811
    google,
    role,
):
    """Every block's reader role is Staff (decision 3); others are refused."""
    with campaign_clock(DAILY_INSTANT):
        preparation = daily_allocated(family_mail)
        set_user(family_mail.service.store, "reader@example.org", role)
        google[0]["email"] = "reader@example.org"
        response = open_copy(copy_path(preparation))
        assert response.status_code == (200 if role == "staff" else 403)
        assert Client().get(copy_path(preparation)).status_code == 403
        assert viewed(preparation) == (1 if role == "staff" else 0)


@pytest.mark.parametrize("role", ["administrator", "staff"])
def test_outside_addresses_are_shown_to_administrators_only(
    family_mail,  # noqa: F811
    google,
    role,
):
    """Staff see how many outside addresses there were, never the addresses."""
    with campaign_clock(DAILY_INSTANT):
        identifier = daily_with_outside(family_mail)
        preparation = daily_allocated(family_mail, identifier=identifier)
        snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
        assert OUTSIDE in {member["address"] for member in snapshot.recipients}
        if role == "staff":
            set_user(family_mail.service.store, "reader@example.org", "staff")
            google[0]["email"] = "reader@example.org"
        page = open_copy(copy_path(preparation))
        assert page.status_code == 200
        body = blocks_region(unescape(page.content.decode()))
        if role == "administrator":
            assert OUTSIDE in body
        else:
            assert OUTSIDE not in body
            assert "1 address; only Administrators see outside addresses." in body


def listed_request(snapshot):
    """The one request a send's Open additional information block listed."""
    (listed,) = [
        item[0]
        for block in snapshot.blocks
        if block["block"] == "additional_information" and block["data"]
        for item in block["data"]["items"]
    ]
    return listed


def test_a_listed_request_has_its_own_page_and_the_copy_never_changes(
    live_response_service, google
):
    """The copy and its request page keep the send's text after a new answer."""
    harness = live_response_service
    respond(harness, "Please call about the parish picnic")
    with campaign_clock(WEEKLY_INSTANT):
        preparation = weekly_allocated(harness)
        snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
        listed = listed_request(snapshot)
        path = copy_path(preparation)
        first = open_copy(path)
        assert first.status_code == 200
        before = blocks_region(unescape(first.content.decode()))
        assert "1 open request" in before
        assert "Please call about the parish picnic" in before
        assert f"{path}items/{listed}/" in before
        # The Family answers again, replacing the request; the copy does not.
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = "A different request"
        submit(harness, form, answers)
        again = open_copy(path)
        assert blocks_region(unescape(again.content.decode())) == before
        browser, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            item = browser.get(f"{path}items/{listed}/")
            assert item.status_code == 200
            assert item["Cache-Control"] == "no-store"
            text = unescape(item.content.decode())
            assert "Please call about the parish picnic" in text
            assert "A different request" not in text
            assert "Superseded by a later response" in text
            live = reverse("admin:information_item", kwargs={"item_id": listed})
            assert f'href="{live}"' in text
            # The trail leads to Report emails and the saved copy, by its
            # name; the heading is the current crumb (ui-conventions 3, 4).
            trail = re.search(r'<nav class="breadcrumbs".*?</nav>', text, re.S)[0]
            title = re.search(r"<h1>(.*?)</h1>", unescape(first.content.decode()))[1]
            crumb = r'<li>(?:<a href="([^"]*)">|<span[^>]*>)([^<]*)<'
            crumbs = re.findall(crumb, trail)
            assert crumbs[-3:] == [
                (reverse("admin:report_emails"), "Report emails"),
                (path, title),
                ("", "Saved request"),
            ]
            assert "<h1>Saved request</h1>" in text
            assert "Return to Report emails" not in text
            assert browser.get(f"{path}items/{listed}/?x=1").status_code == 400
            assert browser.get(f"{path}items/{uuid4()}/").status_code == 403
            # A request that is gone has no live page to offer.
            gone = AdditionalInformationItem.objects.none()
            with patch.object(AdditionalInformationItem.objects, "filter") as found:
                found.return_value = gone
                text = unescape(browser.get(f"{path}items/{listed}/").content.decode())
            assert "No longer available" in text
            assert "Open this request on Additional information" not in text
        assert viewed(preparation) == 4


@pytest.mark.parametrize("role", ["staff", "ministry_leader", None])
def test_a_request_page_admits_staff_only(live_response_service, google, role):
    """A listed request's page takes the saved copy's access rule (decision 11)."""
    harness = live_response_service
    respond(harness, "Please call about the parish picnic")
    with campaign_clock(WEEKLY_INSTANT):
        preparation = weekly_allocated(harness)
        snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
        page = f"{copy_path(preparation)}items/{listed_request(snapshot)}/"
        if role is None:
            response = Client().get(page)
        else:
            set_user(harness.service.store, "reader@example.org", role)
            google[0]["email"] = "reader@example.org"
            response = open_copy(page)
        assert response.status_code == (200 if role == "staff" else 403)
        assert viewed(preparation) == (1 if role == "staff" else 0)


def test_sent_mail_names_a_report_email(family_mail, google):  # noqa: F811
    """Sent mail lists and shows a report email by its purpose's name."""
    with campaign_clock(DAILY_INSTANT):
        daily_allocated(family_mail)
        message = OutboxMessage.objects.get(purpose="admin_report")
        browser, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True):
            for path in (
                "/admin/mail/sent/?state=all",
                f"/admin/mail/sent/{message.pk}/",
            ):
                response = browser.get(path)
                assert response.status_code == 200
                assert b"Report email" in response.content
                assert b"Unknown status" not in response.content
                assert b"<p>Family DUID:" not in response.content
