"""The saved Daily digest pages, over retained snapshots (#1836).

Report emails replaced the Daily digest (#1786), but the page an emailed
digest linked to, its chart and its download still serve the digests sent
before. The snapshots here are seeded as an upgraded database holds them
(``digest_snapshot_builders``); the pages and their reads are the real ones,
on the restricted web login. A report email's own saved copy is tested with
report emails; when it replaces these pages these tests move with them.
"""

import re
import socket

import pytest
from django.db import DatabaseError
from django.test import Client
from django.urls import reverse

from parishkit.stewardship.accounts.templatetags.stewardship import arrows_hidden
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Outcome
from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.observability import Event
from parishkit.stewardship.reports import digest_views
from parishkit.stewardship.storage import StorageInvariantError

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .digest_snapshot_builders import DAILY_AT, png, seed_daily
from .report_builders import set_user
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import restricted_download_pool
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_recipient_suppressions_postgresql import refresh

pytestmark = pytest.mark.django_db(transaction=True)

# A retained Compared with past campaigns section as the digest stored it:
# invented figures and checksums, one Families chart (no Financial
# stewardship), as ``digest_comparison.section`` shapes it.
SECTION = {
    "campaign": "Stewardship 2054",
    "day": 9,
    "report_date": "2054-10-09",
    "compared_with": "2053 and 2052",
    "financial": False,
    "tiles": [
        {
            "label": "Families responded",
            "value": "1",
            "lines": [
                "Last year on day 9: 7 (↓ 85.7% less)",
                "Past average: 6 <b>Families</b> (↑ 100% more)",
            ],
        }
    ],
    "charts": [
        {
            "key": "comparison-families",
            "measure": "families",
            "title": "Families responded by day",
            "summary": "Cumulative Families responded, by campaign day.",
            "spec": {"mark": "line", "data": {"values": [{"day": 1, "families": 0}]}},
            "table": {"headings": ["Day", "Families"], "rows": [["1", "0"]]},
            "notes": [],
        }
    ],
    "archives": [
        {"year": 2053, "checksum": "a" * 64},
        {"year": 2052, "checksum": "b" * 64},
    ],
}


def read(browser, path):
    """Use an actual abortable transport through the complete response lifetime."""
    server, peer = socket.socketpair()
    try:
        response = browser.get(path, **{"gunicorn.socket": server})
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        body = b"".join(response.streaming_content)
        response.close()
        return response, body
    finally:
        server.close()
        peer.close()


def views(snapshot_id, outcome=None):
    """The page's view events for one snapshot (optionally of one outcome)."""
    events = AuditEvent.objects.filter(
        event_type="daily_digest_viewed", subject_id=snapshot_id
    )
    if outcome is not None:
        events = events.filter(auditcontext__context__outcome=outcome)
    return events


def test_report_and_chart_remain_exact_after_source_changes(
    family_mail,  # noqa: F811
    google,
    monkeypatch,
):
    """The page and chart show what was retained, not today's ParishSoft data."""
    with campaign_clock(DAILY_AT):
        seeded = seed_daily(family_mail, monkeypatch)
        browser, _ = signed_in()
        path = reverse("admin:daily_digest_snapshot", args=[seeded.snapshot_id])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, before = read(browser, path)
            _, chart = read(browser, path + "chart.png")
        assert b"Saved report" in before
        assert b"statistics_inputs" not in before
        assert chart == seeded.chart
        newer = response_source()
        newer.members[3]["emailAddress"] = "changed@example.org"
        refresh(family_mail, newer)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, after = read(browser, path)
            _, changed_chart = read(browser, path + "chart.png")
        # Chrome carries current activity times; the pinned data JSON does not.
        pattern = rb'<script[^>]+id="digest-chart-data"[^>]*>(.*?)</script>'
        assert re.search(pattern, before, re.S).group(1) == re.search(
            pattern, after, re.S
        ).group(1)
        assert changed_chart == chart
        # A Testing digest has no funnel, and the note its email had.
        note = b"Testing digests leave out the response funnel."
        assert note in before and note in after
        assert b"data-digest-funnel" not in before + after
        assert views(seeded.snapshot_id).count() == 8
        # The daily table pages and sorts on the server like the live
        # report; chart images still take no parameters.
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, sorted_page = read(browser, path + "?sort=date_desc&size=25")
        assert (
            b'aria-sort="descending"' in sorted_page and b"Page 1 of 1" in sorted_page
        )
        assert browser.get(path + "?sort=local_date").status_code == 400
        assert browser.get(path + "chart.png?sort=date_desc").status_code == 400


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_report_roles_apply_to_all_representations(
    family_mail,  # noqa: F811
    google,
    monkeypatch,
    role,
    settings,
):
    """Staff read the page, chart and download; a Ministry leader reads none."""
    with campaign_clock(DAILY_AT):
        seeded = seed_daily(family_mail, monkeypatch)
        set_user(family_mail.service.store, "reader@example.org", role)
        google[0]["email"] = "reader@example.org"
        browser, _ = signed_in()
        path = reverse("admin:daily_digest_snapshot", args=[seeded.snapshot_id])
        settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(
            ReadLimits(process_pool_size=1)
        )
        with restricted_download_pool(settings):
            for suffix in ("", "chart.png", "download.png"):
                if role == "staff":
                    response, body = read(browser, path + suffix)
                    if suffix == "":
                        # The saved report states the data age and the
                        # connection, as the email did (#510).
                        assert b"ParishSoft data as of" in body
                        assert b"<dt>Connection</dt>" in body
                    if suffix == "download.png":
                        assert response["Content-Disposition"].startswith("attachment;")
                else:
                    assert browser.get(path + suffix).status_code == 403
            assert browser.get(path + "?snapshot=current").status_code == (
                400 if role == "staff" else 403
            )
        for suffix in ("", "chart.png", "download.png", "?snapshot=current"):
            assert Client().get(path + suffix).status_code == 403


def test_original_png_download_uses_restricted_read_connection(
    family_mail,  # noqa: F811
    google,
    monkeypatch,
    settings,
):
    """The download is the retained chart's exact bytes, as an attachment."""
    with campaign_clock(DAILY_AT):
        seeded = seed_daily(family_mail, monkeypatch)
        browser, _ = signed_in()
        settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(
            ReadLimits(process_pool_size=1)
        )
        with restricted_download_pool(settings):
            response, content = read(
                browser,
                reverse("admin:daily_digest_download", args=[seeded.snapshot_id]),
            )
        assert content == seeded.chart
        assert response["Content-Type"] == "image/png"
        assert response["Content-Disposition"].startswith("attachment;")


@pytest.mark.parametrize("suffix", ["", "chart.png", "download.png"])
def test_captured_but_unbuilt_report_is_temporarily_unavailable(
    family_mail,  # noqa: F811
    google,
    monkeypatch,
    settings,
    suffix,
):
    """A valid retained ID cannot turn unfinished rendering into a server error."""
    with campaign_clock(DAILY_AT):
        seeded = seed_daily(family_mail, monkeypatch, built=False)
        browser, _ = signed_in()
        settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(
            ReadLimits(process_pool_size=1)
        )
        with restricted_download_pool(settings):
            name = {
                "": "daily_digest_snapshot",
                "chart.png": "daily_digest_chart",
                "download.png": "daily_digest_download",
            }[suffix]
            response = browser.get(reverse(f"admin:{name}", args=[seeded.snapshot_id]))
        assert response.status_code == 503
        assert response["Cache-Control"] == "no-store"
        assert b"Saved report" not in response.content


def test_an_unknown_report_is_not_found_to_a_signed_in_administrator(
    family_mail,  # noqa: F811
    google,
):
    """No snapshot by that ID: the private denial, with no view recorded."""
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(
            reverse(
                "admin:daily_digest_snapshot",
                args=["0" * 8 + "-0000-4000-8000-" + "0" * 12],
            )
        )
    assert response.status_code == 403
    assert not AuditEvent.objects.filter(event_type="daily_digest_viewed").exists()


def test_the_retained_comparison_section_is_shown_as_saved(
    family_mail,  # noqa: F811
    google,
    monkeypatch,
):
    """The Compared with past campaigns section is the stored one, word for word."""
    with campaign_clock(DAILY_AT):
        seeded = seed_daily(
            family_mail, monkeypatch, comparison=SECTION, comparison_chart=png()
        )
        browser, _ = signed_in()
        path = reverse("admin:daily_digest_snapshot", args=[seeded.snapshot_id])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, page = read(browser, path)
        page = page.decode()
        assert "data-digest-comparison" in page
        assert "a" * 64 in page and "b" * 64 in page
        for tile in SECTION["tiles"]:
            for line in tile["lines"]:
                # Each saved line is drawn word for word, escaped and with
                # its direction arrows hidden from screen readers (#1637).
                assert arrows_hidden(line) in page
        assert 'id="comparison-families-spec"' in page
        assert "/admin/reports/comparison/?day=9" in page
        # The same digest with no section has none to show.
        bare = seed_daily(family_mail, monkeypatch)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, plain = read(
                browser,
                reverse("admin:daily_digest_snapshot", args=[bare.snapshot_id]),
            )
        assert b"data-digest-comparison" not in plain


@pytest.mark.parametrize("stage", ["denied", "unavailable", "streamed"])
@pytest.mark.parametrize("error_type", [DatabaseError, StorageInvariantError])
def test_terminal_audit_failure_preserves_response_and_emits_safe_signal(
    family_mail,  # noqa: F811
    google,
    monkeypatch,
    caplog,
    stage,
    error_type,
):
    """Neither admission failure nor stream closure exposes audit exception text."""
    original = digest_views.record_action

    def record(*args, **kwargs):
        """Keep actual durable STARTED evidence but fail the terminal write."""
        if kwargs["context"]["outcome"] != Outcome.STARTED:
            raise error_type("private report data must not be logged")
        return original(*args, **kwargs)

    def denied(*args, **kwargs):
        """Simulate authority loss between intent and guarded admission."""
        raise PermissionError

    with campaign_clock(DAILY_AT):
        seeded = seed_daily(family_mail, monkeypatch)
        browser, _ = signed_in()
        monkeypatch.setattr(digest_views, "record_action", record)
        if stage == "denied":
            monkeypatch.setattr(digest_views, "campaign_response", denied)
        path = reverse("admin:daily_digest_chart", args=[seeded.snapshot_id])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            if stage == "streamed":
                _, body = read(browser, path)
                assert body == seeded.chart
            else:
                # Missing the server-owned socket exercises the real
                # sanitized unavailable path; denied instead simulates
                # current role loss.
                response = browser.get(path)
                assert response.status_code == (403 if stage == "denied" else 503)
                assert response["Cache-Control"] == "no-store"
        assert list(
            views(seeded.snapshot_id).values_list("auditcontext__context", flat=True)
        ) == [{"outcome": Outcome.STARTED}]
        assert sum(r.msg == Event.REPORT_AUDIT_FAILED for r in caplog.records) == 1
        assert "private report data" not in caplog.text


def test_a_production_saved_page_recounts_the_emails_funnel(
    live_response_service,
    google,
    monkeypatch,
):
    """A Production digest's page shows the funnel at the end of its report day.

    The funnel is counted again from durable timestamps, so a later
    submission leaves the saved page unchanged, and its chart images
    (removed by #1370) are not drawn.
    """
    from datetime import UTC, datetime, timedelta

    from .test_response_revisit_postgresql import revisit
    from .test_response_submission_postgresql import submit

    harness = live_response_service
    # A Family submits on the report day (October 9); the digest is
    # captured the next morning.
    with campaign_clock(datetime(2054, 10, 9, 12, tzinfo=UTC)):
        from .test_weekly_observation_postgresql import respond

        respond(harness, "Report day request")
    with campaign_clock(DAILY_AT):
        seeded = seed_daily(harness, monkeypatch)
        browser, _ = signed_in()
        path = reverse("admin:daily_digest_snapshot", args=[seeded.snapshot_id])

        def page_funnel():
            """The page's funnel panel as {label: families}, and the page."""
            with task_login(ServiceRole.WEB, exact=True, reconnect=True):
                _, content = read(browser, path)
            panel = re.search(rb"<[^>]*data-digest-funnel.*?</section>", content, re.S)
            assert panel, "The page has no funnel panel."
            rows = re.findall(
                rb'<th scope="row">([^<]+).*?</th><td>([^<]+)</td>',
                panel.group(0),
                re.S,
            )
            return {a.decode(): b.decode() for a, b in rows}, content

        before, content = page_funnel()
        assert before["Submitted"] == "1"
        assert b"response-funnel-spec" not in content
        assert b"response-activity-spec" not in content
    # A submission after the report day does not change the saved page.
    with campaign_clock(DAILY_AT + timedelta(hours=1)):
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = "Later request"
        submit(harness, form, answers)
        after, _ = page_funnel()
    assert after == before
