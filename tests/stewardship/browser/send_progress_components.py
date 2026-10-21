"""Render the real Mail queue templates with sample counts (#413, #1562).

The Mail queue page carries the Family send panel that was the Family email
progress page; these components give it a small queue of every kind too.
"""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.jobs.delivery_metadata import other_mode
from parishkit.stewardship.jobs.mail_queue import (
    QUEUE_PURPOSES,
    KindCounts,
    QueueCounts,
)
from parishkit.stewardship.jobs.mail_queue_views import (
    GIVE_UP_MILLISECONDS,
    POLL_MILLISECONDS,
    _announcement,
    _tiles,
)
from parishkit.stewardship.jobs.send_progress import RATE_WINDOW, SendCounts, progress

PAGE = "/mail-queue"
FINISHED = "/mail-queue-finished"
STATUS = "/mail-queue/status"
# A launch in flight: invitations queued, sending and retrying, one daily
# report waiting, and a Testing email left over in the other mode.
QUEUE = QueueCounts(
    mode="production",
    kinds=tuple(
        KindCounts(
            purpose,
            *{"initial": (590, 4, 6, 0), "daily_digest": (1, 0, 0, 0)}.get(purpose, ()),
        )
        for purpose in QUEUE_PURPOSES
    ),
    other_mode_queued=1,
)
EMPTY = QueueCounts(
    mode="production",
    kinds=tuple(KindCounts(purpose) for purpose in QUEUE_PURPOSES),
)


def queue_values(send, queue=QUEUE):
    """The template values the Mail queue view adds for ``send`` and ``queue``."""
    return {
        "campaign": SimpleNamespace(pk=UUID(int=413)),
        "testing": False,
        "paused": False,
        "queue": queue,
        "tiles": _tiles(queue, False),
        "other_mode": other_mode(queue.mode),
        "send": send,
        "upcoming": False,
        # As the view does: keep checking whenever the system is available.
        "follow": True,
        "announcement": _announcement(send),
        "poll_interval": POLL_MILLISECONDS,
        "give_up": GIVE_UP_MILLISECONDS,
    }


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
    return render_to_string(
        "stewardship/mail-queue-status.html", queue_values(_send(now, **values))
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
                "key": "mail",
                "label": "Mail and Family portal",
                "current": True,
                "items": [{"url": PAGE, "label": "Mail queue", "current": "page"}],
            }
        ],
        "breadcrumbs": [
            {"label": "Home", "url": "/home"},
            {"label": "Mail and Family portal", "url": PAGE},
            {"label": "Mail queue", "url": None},
        ],
    }

    def values(send, queue=QUEUE, **extra):
        """Template context the view would build for ``send`` and ``queue``."""
        return context | {"admin_chrome": chrome} | queue_values(send, queue) | extra

    return {
        PAGE: (
            "text/html",
            render_to_string(
                "stewardship/mail-queue.html",
                values(running, status_url=STATUS),
            ),
        ),
        FINISHED: (
            "text/html",
            render_to_string("stewardship/mail-queue.html", values(finished, EMPTY)),
        ),
        # What the next poll of the running page reads: the send has finished
        # and the queue has emptied.
        STATUS: (
            "text/html",
            render_to_string(
                "stewardship/mail-queue-status.html", values(finished, EMPTY)
            ),
        ),
    }
