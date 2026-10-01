"""Render the real Family email progress templates with sample counts (#413)."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.jobs.send_progress import RATE_WINDOW, SendCounts, progress
from parishkit.stewardship.jobs.send_progress_views import (
    GIVE_UP_MILLISECONDS,
    POLL_MILLISECONDS,
    _announcement,
)

PAGE = "/family-email-progress"
FINISHED = "/family-email-progress-finished"
STATUS = "/family-email-progress/status"


def _send(now, **values):
    """A launch-size invitation send observed at ``now``."""
    counts = SendCounts(
        **{
            "kind": "initial",
            "sent": 480,
            "failed": 10,
            "uncertain": 10,
            "prepare_failed": 0,
            "remaining": 600,
            "waiting": 0,
            "unreachable": 3,
            "not_needed": 7,
            "started_at": now - timedelta(minutes=10),
            "last_settled_at": None,
            "recent": 500,
            "since": now - RATE_WINDOW,
            "now": now,
        }
        | values
    )
    return progress(counts)


def status(now, **values):
    """The polled status fragment for a send with these counts."""
    send = _send(now, **values)
    return render_to_string(
        "stewardship/family-email-progress-status.html",
        {
            "campaign": SimpleNamespace(pk=UUID(int=413)),
            "testing": False,
            "paused": False,
            "send": send,
            "upcoming": False,
            # As the view does: keep checking while there is a campaign.
            "follow": True,
            "announcement": _announcement(send),
            "poll_interval": POLL_MILLISECONDS,
            "give_up": GIVE_UP_MILLISECONDS,
        },
    )


def components(context, admin):
    """The running page (polling a finished fragment) and the finished page."""
    now = context["server_now"]
    running = _send(now)
    finished = _send(
        now,
        sent=1080,
        remaining=0,
        recent=0,
        last_settled_at=now - timedelta(minutes=1),
    )
    chrome = admin | {
        "sections": [
            {
                "key": "campaign",
                "label": "Campaign",
                "current": True,
                "items": [
                    {"url": PAGE, "label": "Family email progress", "current": "page"}
                ],
            }
        ],
        "breadcrumbs": [
            {"label": "Home", "url": "/home"},
            {"label": "Campaign", "url": PAGE},
            {"label": "Family email progress", "url": None},
        ],
    }

    def values(send, **extra):
        """Template context the view would build for ``send``."""
        return (
            context
            | {
                "admin_chrome": chrome,
                "campaign": SimpleNamespace(pk=UUID(int=413)),
                "testing": False,
                "paused": False,
                "send": send,
                "upcoming": False,
                "follow": True,
                "announcement": _announcement(send),
                "poll_interval": POLL_MILLISECONDS,
                "give_up": GIVE_UP_MILLISECONDS,
            }
            | extra
        )

    return {
        PAGE: (
            "text/html",
            render_to_string(
                "stewardship/family-email-progress.html",
                values(running, status_url=STATUS),
            ),
        ),
        FINISHED: (
            "text/html",
            render_to_string(
                "stewardship/family-email-progress.html", values(finished)
            ),
        ),
        # What the next poll of the running page reads: the send has finished.
        STATUS: (
            "text/html",
            render_to_string(
                "stewardship/family-email-progress-status.html", values(finished)
            ),
        ),
    }
