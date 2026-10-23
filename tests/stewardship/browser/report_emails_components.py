"""Mail › Report emails (#1786, delivery plan item 5) with synthetic rows.

Three reports in the statuses the action matrix tells apart: an Upcoming
converted Weekly (with past sends, one before the conversion), a Paused
Daily, and one whose send is in progress, so its actions are unavailable;
and one deleted report with a past send. The page shows names, schedules,
times and results only, never report data.
"""

import re
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.report_email_list import (
    ATTENTION_REASON,
    IN_PROGRESS_REASON,
    STATUS_LABELS,
)

PAGE = "/report-emails"
STAFF = "/report-emails-staff"
NOW = datetime(2054, 10, 20, 12, 0, tzinfo=UTC)
WEEKLY, DAILY, BUSY, GONE = (UUID(int=index) for index in (101, 102, 103, 104))


def _send(index, *, result="sent", kind="report", how="scheduled", count=None):
    """One past send."""
    return {
        "id": UUID(int=900 + index),
        "kind": kind,
        "made_at": datetime(2054, 10, 19 - index, 12, tzinfo=UTC),
        "how": how,
        "as_of": datetime(2054, 10, 19 - index, 12, tzinfo=UTC),
        "recipients": 3,
        "result": result,
        "count": count,
        "name": None,
        "copy": result == "sent",
    }


def _row(identifier, name, status, *, actions, sends, schedule, paused=None, **extra):
    """One report's row, as ``report_email_list.report_rows`` draws it."""
    return {
        "id": str(identifier),
        "name": name,
        "schedule": schedule,
        "recipients": "All Administrators and 2 others",
        "next": SimpleNamespace(due_at=datetime(2054, 10, 21, 12, tzinfo=UTC))
        if status != "paused"
        else None,
        "last": sends[0] if sends else None,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "paused_since": paused,
        "sends": sends,
        "actions": actions,
        "send_with": f"revision={UUID(int=7)}",
        "send_body": "It is sent now to its 3 recipients, outside its schedule.",
        "resume_body": None,
        "resume_day": None,
    } | extra


def rows():
    """The three reports in the page's order."""
    weekly_words = [
        ("text", "Every Wednesday at "),
        ("time", datetime(2054, 10, 21, 12, tzinfo=UTC)),
        ("text", ", from the campaign's first day until the first send after it ends."),
    ]
    daily_words = [
        ("text", "Every day at "),
        ("time", datetime(2054, 10, 21, 11, tzinfo=UTC)),
        ("text", ", from the day after the campaign starts until "),
        ("date", date(2054, 11, 1)),
        ("text", "."),
    ]
    return [
        _row(
            WEEKLY,
            "Weekly report",
            "upcoming",
            actions={
                "edit": None,
                "preview": None,
                "send_now": None,
                "pause": None,
                "delete": None,
            },
            sends=[
                _send(0, count=12),
                _send(1, how="manual", count=0),
                _send(2, kind="weekly"),
            ],
            schedule=weekly_words,
        ),
        _row(
            BUSY,
            "Finance council report",
            "sending",
            actions={
                "preview": None,
                "send_now": IN_PROGRESS_REASON,
                "pause": IN_PROGRESS_REASON,
                "delete": IN_PROGRESS_REASON,
            },
            sends=[_send(0, result="sending")],
            schedule=[("text", "Once, "), ("instant", NOW)],
        ),
        _row(
            DAILY,
            "Daily report",
            "paused",
            actions={
                "edit": None,
                "preview": None,
                "send_now": None,
                "resume": None,
                "delete": None,
            },
            sends=[_send(3, result="failed")],
            schedule=daily_words,
            paused=datetime(2054, 10, 18, 13, tzinfo=UTC),
            resume_body="Its next send is then on 2054-10-21T11:00:00+00:00. "
            "Sends missed while it was paused are not made.",
            resume_day="2054-10-21T11:00:00+00:00",
        ),
    ]


def _page(context, admin, *, administrator):
    """The page for an Administrator, or for Staff (no recipients or actions)."""
    shown = rows()
    if not administrator:
        for row in shown:
            row |= {"recipients": None, "actions": {}}
    return render_to_string(
        "stewardship/report-emails.html",
        context
        | {
            "admin_chrome": admin,
            "campaign": SimpleNamespace(pk=UUID(int=5)),
            "administrator": administrator,
            "refresh_url": "/admin/mail/report-emails/",
            "rows": shown,
            "deleted": [{"id": str(GONE), "name": "Old report", "sends": [_send(4)]}],
            "archived": False,
            "requested": None,
            "base_digest": "0" * 64,
            "configuration_id": UUID(int=8),
            "command_id": UUID(int=9),
            "change_url": "/admin/mail/report-emails/changes/",
            "send_now_url": "/admin/mail/report-emails/manual-sends/",
            "render_url": RENDER,
        },
    )


def components(context, admin):
    """The Report emails page for an Administrator and for Staff."""
    return {
        PAGE: ("text/html", _page(context, admin, administrator=True)),
        STAFF: ("text/html", _page(context, admin, administrator=False)),
    }


# Kept beside the components so the tests name the reasons the rows show.
REASONS = (IN_PROGRESS_REASON, ATTENTION_REASON)


# Where a row's Preview posts (#2035); the test routes its answer.
RENDER = "/admin/mail/report-emails/previews/"
# The synthetic preview's chart image address, which the test routes too.
PREVIEW_IMAGE = "/admin/mail/report-emails/previews/synthetic/participation.png"


def _weekly_email(mode):
    """A Weekly-shaped email: one open request and the Response funnel.

    Its request row and closing button link where a preview links them (the
    live request, the Report emails page), as ``preview_report`` draws them.
    """
    from parishkit.stewardship.reports.report_blocks.compiler import compile_report
    from parishkit.stewardship.reports.report_document import ReportDocument
    from parishkit.stewardship.reports.weekly_digest import WeeklyInformation

    from ..test_daily_digest_content import funnel_metrics

    document = ReportDocument(
        send_id=UUID(int=77),
        name="Weekly report",
        as_of="send_time",
        as_of_at=NOW,
        campaign_timezone="America/New_York",
        mode=mode,
        covered_dates=(date(2054, 10, 20),),
        # The sample funnel is Production's; a Testing report draws none.
        funnel=funnel_metrics() if mode == "production" else None,
        information=(
            WeeklyInformation(UUID(int=3), 1234, "A & B", NOW, "Please call us."),
        ),
    )
    report = compile_report(
        document,
        ["additional_information", "response_funnel"],
        public_origin="https://parish.example",
        copy_path="/admin/mail/report-emails/",
        requests_path="/admin/reports/information/",
    )
    return report.html, report.text


def _daily_email(mode):
    """A Daily-shaped email: every fact block but the server-drawn comparison.

    The opening line's dashboard link, the section headings, the totals'
    bars, the funnel's and day-by-day tables' headers and the chart, whose
    image is pointed at ``PREVIEW_IMAGE`` as the view points it.
    """
    from dataclasses import replace

    from parishkit.stewardship.reports.daily_digest import DAILY_BLOCKS
    from parishkit.stewardship.reports.report_blocks.compiler import compile_report

    from ..test_daily_digest_content import document, funnel_metrics

    value = replace(document(), funnel=funnel_metrics(), mode=mode)
    keys = [key for key in DAILY_BLOCKS if key != "compared_with_past_campaigns"]
    report = compile_report(value, keys, public_origin="https://parish.example")
    html = re.sub(r'src="cid:[^"]+"', f'src="{PREVIEW_IMAGE}"', report.html)
    return html, report.text


def preview_answer(*, shape="weekly", updating=False, testing=False):
    """The Preview dialog's answer, from the real template and compiler.

    ``shape`` is "weekly" (``_weekly_email``) or "daily" (``_daily_email``):
    a real report email compiled by the one report compiler, so the page's
    styles meet its real headings, links and tables.
    """
    from django.utils.safestring import mark_safe

    mode = "testing" if testing else "production"
    html, text = (_daily_email if shape == "daily" else _weekly_email)(mode)
    name = "Daily report" if shape == "daily" else "Weekly report"
    preview = SimpleNamespace(
        subject=f"{name} — October 20, 2054 (Annual campaign, Example Parish)",
        text=text,
        updating=updating,
        fact_set_id=None,
        mode=mode,
    )
    return render_to_string(
        "stewardship/report-email-preview.html",
        {
            "preview": preview,
            "name": name,
            "recipients": {
                "users": ["Pat Doe"],
                "groups": [{"name": "All Administrators", "people": 2}],
                "outside": ["council@example.org"],
                "outside_count": 1,
            },
            "email": mark_safe(html),
        },
    )
