"""A daily digest reports the end of the previous campaign day, whenever it sends.

#721: the chart ended on the report day while the text counted live Families
at the send. A Family that responds after midnight but before the send must
appear in neither the chart's last day, nor the text, nor the as-of line.
"""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from parishkit.stewardship.reports.digest_models import DailyDigestSnapshot

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import live_response_service  # noqa: F401
from .test_daily_digest_building_postgresql import build
from .test_fact_materialization_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

# The fixture campaign runs in America/New_York (EDT, UTC-4, in October).
REPORT_DAY = date(2054, 10, 7)
AFTER_MIDNIGHT = datetime(2054, 10, 8, 9, tzinfo=UTC)  # 5:00 AM local
# A later report day (October 8) for the saved-page test: a send on the 9th,
# and activity after that day ends.
NEXT_SEND = datetime(2054, 10, 9, 10, tzinfo=UTC)  # 6:00 AM local
NEXT_MORNING = datetime(2054, 10, 9, 9, tzinfo=UTC)  # 5:00 AM local, after the cutoff
MORNING_SEND = datetime(2054, 10, 8, 10, tzinfo=UTC)  # 6:00 AM local
LATE_SEND = datetime(2054, 10, 8, 15, tzinfo=UTC)  # 11:00 AM local


@pytest.mark.parametrize("send", [MORNING_SEND, LATE_SEND], ids=["morning", "late"])
def test_digest_reports_previous_day_end_not_send_time(
    live_response_service,  # noqa: F811
    send,
):
    harness = live_response_service
    # Production planning waits for activation catch-up; this one has no work.
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(AFTER_MIDNIGHT):
        respond(harness)
    with campaign_clock(send):
        _, document, content = build(harness)
    # The schedule slot, not the send time, fixes the report day.
    assert document.covered_dates[-1] == REPORT_DAY
    assert document.participation.last_date == REPORT_DAY
    assert document.report_day.local_date == REPORT_DAY
    # The chart's last point excludes the after-midnight response ...
    day = document.report_day
    assert (day.first_responses, day.cumulative_responses) == (0, 0)
    assert day.cohort_denominator >= 1
    # ... while the retained send-time observation already counts it, which
    # is exactly the live number the email used to show.
    snapshot = DailyDigestSnapshot.objects.get(pk=document.snapshot_id)
    assert snapshot.through_date == REPORT_DAY
    assert snapshot.submission_watermark == 1
    assert document.statistics.active.responses == 1
    # Every figure and the as-of line describe the end of the report day.
    as_of = "All data is as of the end of October 7, 2054 (EDT)."
    total = f"{day.cohort_denominator:,}"
    assert f"Families that have responded: 0 out of {total}" in content.text
    assert "First submissions that day: 0" in content.text
    # One line per total (#720): the label, its bar, then the exact value.
    assert ">Families that have responded</td>" in content.html
    assert f"<strong>0 out of {total} (0%)</strong>" in content.html
    for body in (content.text, content.html):
        assert as_of in body
        assert f"1 out of {total}" not in body
        assert "Oct 8, 2054" not in body and "October 8, 2054" not in body
    # The response funnel is counted at the same report-day end (#477): the
    # after-midnight response is not yet Submitted, and the compiling worker
    # read it from durable timestamps.
    assert document.mode == "production" and document.funnel is not None
    assert document.funnel.as_of == datetime(2054, 10, 8, 4, tzinfo=UTC)
    assert document.funnel.stage("submitted") == 0
    assert "Response funnel" in content.html
    assert "Response funnel (Families):" in content.text
    assert "Submitted: 0 (" in content.text


def test_the_production_saved_page_shows_the_emails_funnel(
    live_response_service,  # noqa: F811
    google,
):
    """The page recounts the email's funnel, and later activity can't change it.

    A real Production digest with a response on the report day: its email's
    funnel and the saved page's agree, with a non-zero Submitted; a second
    response after the cutoff leaves the page as it was.
    """
    import re
    from functools import partial

    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.reports.digest_building import retain_daily_content
    from parishkit.stewardship.reports.digest_fanout import fanout_daily
    from parishkit.stewardship.reports.digest_models import (
        DailyDigestReady,
        DailyDigestRecipient,
    )
    from parishkit.stewardship.web.digest_content import FUNNEL_CHART_IDS

    from .auth_builders import signed_in
    from .test_background_grants_postgresql import task_login
    from .test_daily_digest_fanout_postgresql import configure_content
    from .test_daily_digest_views_postgresql import read

    harness = live_response_service
    complete_empty_catchup(harness.campaign, uuid4())
    # A response on the report day, October 8.
    with campaign_clock(AFTER_MIDNIGHT):
        respond(harness)
    with campaign_clock(NEXT_SEND):
        claim, document, content = build(
            harness, configure=partial(configure_content, harness)
        )
        assert document.report_day.local_date == date(2054, 10, 8)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            ready = retain_daily_content(claim, document, content)
        assert document.funnel.stage("submitted") == 1
        assert "Submitted: 1 (" in content.text
        # The funnel's two chart images (#477 slice 2), drawn by the real
        # server renderer, are retained with the ready row and allocated
        # through the closed boundary with each Admin's mail.
        ready = DailyDigestReady.objects.get(pk=ready.pk)
        assert ready.images() == tuple(
            (image.content_id, image.data) for image in content.funnel
        )
        assert [name for name, _ in ready.images()] == list(FUNNEL_CHART_IDS)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert fanout_daily(claim).phase == "complete"
        recipient = DailyDigestRecipient.objects.select_related("outbox__render").get()
        assert recipient.outbox.render.html.endswith(ready.html)
        for name in FUNNEL_CHART_IDS:
            assert ready.html.count(f'src="cid:{name}"') == 1
        browser, _ = signed_in()
        path = f"/admin/reports/emailed/daily/{ready.snapshot_id}/"
        rows = re.compile(
            rb'<tr><th scope="row">([^<]+)(?:<small>[^<]*</small>)?</th>'
            rb"<td>([^<]+)</td><td>([^<]+)</td></tr>"
        )

        def page_funnel():
            """The saved page's funnel rows as (stage, families, compared)."""
            with task_login(ServiceRole.WEB, exact=True, reconnect=True):
                _, body = read(browser, path)
            section = re.search(
                rb'<table aria-labelledby="digest-funnel-heading">.*?</table>',
                body,
                re.S,
            )
            assert section is not None
            return [
                (label.strip().decode(), count.decode(), share.decode())
                for label, count, share in rows.findall(section[0])
            ]

        before = page_funnel()
        # The page draws the email's two charts in the browser, before the
        # funnel's table (visuals first, #720).
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, body = read(browser, path)
        panel = body[body.index(b"data-digest-funnel") :]
        assert (
            panel.index(b'id="response-funnel-spec"')
            < panel.index(b'id="response-activity-spec"')
            < panel.index(b'<table aria-labelledby="digest-funnel-heading">')
        )
        assert b"vega-lite" in body
        # The digest's stages: "Link followed" is left out (#1016).
        assert [row[1] for row in before] == [
            f"{stage.count:,}"
            for stage in document.funnel.stages
            if stage.key != "link_followed"
        ]
        assert before[-1][:2] == ("Submitted", "1")
    # Activity after the cutoff: the next morning the Family signs in again
    # (a submission ends its session) and submits a second time.
    from dataclasses import replace

    from .test_family_auth_postgresql import login

    with campaign_clock(NEXT_MORNING):
        client, response = login(harness.code)
        assert response.status_code == 302
        respond(replace(harness, client=client, request=response.wsgi_request))
    with campaign_clock(NEXT_SEND):
        # The later response is real: counted after the cutoff, it is the
        # Family's second submission ...
        from parishkit.stewardship.reports.digest_funnel import digest_funnel

        later = digest_funnel(harness.campaign.pk, "production", NEXT_SEND)
        assert later.submitted_again == 1
        # ... and the saved page, counted at the report-day end, is unchanged.
        assert page_funnel() == before


def test_a_production_digest_sent_without_its_charts(
    live_response_service,  # noqa: F811
    google,
):
    """Charts withheld (#477): the funnel's table is sent and saved, no charts.

    The compile the last attempt makes when a chart cannot be drawn
    (``digest_tasks._build``): the ready row keeps no funnel images, the
    allocated mail references none, and the saved page shows the funnel's
    table without the charts its email never had.
    """
    from dataclasses import replace
    from functools import partial

    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.reports.daily_digest import render_daily_digest
    from parishkit.stewardship.reports.digest_building import retain_daily_content
    from parishkit.stewardship.reports.digest_comparison import withheld
    from parishkit.stewardship.reports.digest_fanout import fanout_daily
    from parishkit.stewardship.reports.digest_models import (
        DailyDigestReady,
        DailyDigestRecipient,
    )

    from .auth_builders import signed_in
    from .test_background_grants_postgresql import task_login
    from .test_daily_digest_fanout_postgresql import configure_content
    from .test_daily_digest_views_postgresql import read

    harness = live_response_service
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(AFTER_MIDNIGHT):
        respond(harness)
    with campaign_clock(NEXT_SEND):
        claim, document, _ = build(
            harness, configure=partial(configure_content, harness)
        )
        document = replace(document, comparison=withheld(document.comparison))
        content = render_daily_digest(
            document, public_origin="https://parish.example", charts=False
        )
        assert content.charts_withheld and content.funnel == ()
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            ready = retain_daily_content(claim, document, content)
            assert fanout_daily(claim).phase == "complete"
        ready = DailyDigestReady.objects.get(pk=ready.pk)
        assert ready.funnel_chart is None and ready.images() == ()
        assert "cid:response-" not in ready.html
        assert "Email invites sent" in ready.html and "Email invites sent" in ready.text
        recipient = DailyDigestRecipient.objects.select_related("outbox__render").get()
        assert "cid:response-" not in recipient.outbox.render.html
        browser, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, body = read(
                browser, f"/admin/reports/emailed/daily/{ready.snapshot_id}/"
            )
        assert b'<table aria-labelledby="digest-funnel-heading">' in body
        assert b"response-funnel-spec" not in body
        assert b"response-activity-spec" not in body
