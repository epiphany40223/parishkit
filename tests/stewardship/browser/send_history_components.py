"""Render the real Family email sends page with sample sends (#432)."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.jobs.send_history import ListedSend, SendKey, SendRow
from parishkit.stewardship.jobs.send_progress import RATE_WINDOW, SendCounts, progress
from parishkit.stewardship.web.tables import paginate

PAGE = "/family-email-sends"


def _row(now, definition, kind, mode, *, number=None, live=False, **values):
    """One counted send observed at ``now``; a finished send by default."""
    counts = SendCounts(
        **{
            "kind": kind,
            "sent": 1087,
            "failed": 4,
            "prepare_failed": 1,
            "uncertain": 0,
            "remaining": 0,
            "waiting": 0,
            "unreachable": 3,
            "not_needed": 12,
            "started_at": now - timedelta(hours=2),
            "last_settled_at": now - timedelta(hours=1, minutes=34),
            "recent": 0,
            "since": now - RATE_WINDOW,
            "now": now,
        }
        | values
    )
    key = SendKey(
        UUID(int=definition),
        UUID(int=definition + 100),
        mode,
        1 if mode == "production" else 0,
    )
    return SendRow(
        listed=ListedSend(
            key, kind, now - timedelta(hours=2, minutes=5), number, mode == "testing"
        ),
        current=True,
        live=live,
        earlier=False,
        send=progress(counts),
        emails={
            "delivered": counts.sent,
            "permanent_failure": counts.outbox_failed,
            "cancelled": 9,
        },
    )


def components(context, admin):
    """The history with a reminder in progress and two finished invitations."""
    now = context["server_now"]
    rows = [
        _row(
            now,
            2,
            "reminder",
            "production",
            number=1,
            live=True,
            sent=480,
            remaining=600,
            last_settled_at=None,
        ),
        _row(now, 1, "initial", "production"),
        _row(now, 1, "initial", "testing", sent=5, failed=0, prepare_failed=0),
    ]
    chrome = admin | {
        "sections": [
            {
                "key": "campaign",
                "label": "Campaign",
                "current": True,
                "items": [
                    {"url": PAGE, "label": "Family email sends", "current": "page"}
                ],
            }
        ],
        "breadcrumbs": [
            {"label": "Home", "url": "/home"},
            {"label": "Campaign", "url": PAGE},
            {"label": "Family email sends", "url": None},
        ],
    }
    return {
        PAGE: (
            "text/html",
            render_to_string(
                "stewardship/family-email-sends.html",
                context
                | {
                    "admin_chrome": chrome,
                    "campaign": SimpleNamespace(pk=UUID(int=432)),
                    "table": paginate(rows, {}),
                },
            ),
        )
    }
