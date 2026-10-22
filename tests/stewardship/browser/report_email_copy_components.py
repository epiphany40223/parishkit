"""A report send's saved copy page and a listed request's page (#1946).

Synthetic snapshot data, captured by the blocks' own captures from the daily
digest test document (with a Production funnel and the Compared with past
campaigns section) plus two kept requests, drawn by the real saved copy
renderers and templates. No database is needed.
"""

import re
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from parishkit.stewardship.reports.daily_digest import render_daily_digest
from parishkit.stewardship.reports.digest_comparison import section
from parishkit.stewardship.reports.report_blocks.compiler import capture_report
from parishkit.stewardship.reports.report_blocks.registry import CopyFrame
from parishkit.stewardship.reports.report_email_copy import (
    CopyFacts,
    copy_blocks,
    recipients_by_kind,
)
from parishkit.stewardship.web import dates

from ..test_daily_digest_content import document, funnel_metrics
from ..test_digest_comparison import found as comparison_fixture

PAGE = "/report-email-copy"
STAFF = "/report-email-copy-staff"
ITEM = "/report-email-copy-item"
SEND = "/admin/mail/report-emails/sends/00000000-0000-0000-0000-000000000007/"
MADE = datetime(2054, 10, 9, 11, 0, tzinfo=UTC)
LONG = (
    "We would like to help with the parish picnic next summer, and our two "
    "teenagers can set up tables, but we are away most of June and would "
    "need someone to tell us the dates well ahead of time so we can plan "
    "around them; please call us in the evening."
)
ITEMS = [
    [
        "00000000-0000-0000-0000-0000000000a1",
        "1001",
        "Smith, Ann and Bo",
        "2054-10-02T13:15:00+00:00",
        "Please call about volunteering.",
    ],
    [
        "00000000-0000-0000-0000-0000000000a2",
        "1002002",
        "Hernández-Villanueva, María José and Juan Pablo",
        "2054-10-05T22:40:00+00:00",
        LONG,
    ],
]
COHORT = [
    {"address": "a@example.org", "kind": "group", "ref": "administrators"},
    {"address": "b@example.org", "kind": "group", "ref": "administrators"},
    {"address": "c@example.org", "kind": "user", "ref": "u1"},
    {"address": "finance.council.chair@example.net", "kind": "outside", "ref": None},
]


def snapshot():
    """The kept blocks of a send of every block, and its email as sent."""
    value = replace(
        document(),
        funnel=funnel_metrics(),
        mode="production",
        comparison=section(comparison_fixture()),
    )
    keys = (
        "campaign_totals",
        "response_funnel",
        "participation_charts",
        "day_by_day",
        "compared_with_past_campaigns",
    )
    entries = [{"block": key, "version": 1, "options": {}} for key in keys]
    blocks = capture_report(value, entries)
    blocks.insert(
        0, {"block": "additional_information", "version": 1, "data": {"items": ITEMS}}
    )
    content = render_daily_digest(value, public_origin="https://parish.example")
    # Every inline chart points at the component server's one chart image.
    html = re.sub(r'src="cid:[^"]+"', 'src="/digest-chart.png"', content.html)
    return SimpleNamespace(blocks=blocks), value.participation, html


def _page(context, admin, *, administrator):
    """The saved copy page for an Administrator, or for Staff."""
    kept, participation, html = snapshot()
    frame = CopyFrame(SEND, participation=participation, requests={ITEMS[0][0]: 5})
    with dates.using("us_long"):
        blocks = copy_blocks(kept, frame)
        facts = CopyFacts(
            "Daily report — October 8, 2054",
            None,
            MADE,
            MADE,
            "scheduled",
            "",
            "production",
            recipients_by_kind(
                COHORT, {"u1": "Smith, Ann"}, administrator=administrator
            ),
        )
        return render_to_string(
            "stewardship/report-email-copy.html",
            context
            | {
                "admin_chrome": admin,
                "facts": facts,
                "as_of_day": "October 8, 2054",
                "age": None,
                "blocks": blocks,
                "charts": True,
                "email": mark_safe(html),
            },
        )


def _item(context, admin):
    """One listed request's page."""
    with dates.using("us_long"):
        return render_to_string(
            "stewardship/report-email-copy-item.html",
            context
            | {
                "admin_chrome": admin,
                "title": "Daily report — October 8, 2054",
                "row": {
                    "family_id": UUID(int=5),
                    "family_name": ITEMS[1][2],
                    "family_duid": ITEMS[1][1],
                    "submitted_at": datetime.fromisoformat(ITEMS[1][3]),
                    "text": LONG,
                },
                "current": "Current actionable request",
                "live_url": "/admin/reports/information/",
            },
        )


def components(context, admin):
    """The saved copy for an Administrator and for Staff, and one request."""
    return {
        PAGE: ("text/html", _page(context, admin, administrator=True)),
        STAFF: ("text/html", _page(context, admin, administrator=False)),
        ITEM: ("text/html", _item(context, admin)),
    }
