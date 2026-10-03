"""The system logs screen reuses the shared browser server and real row shaping."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.audit.log_rows import (
    LEVEL_LABELS,
    LogQuery,
    audit_row,
    log_table,
    merge,
    operational_row,
    page_context,
)
from parishkit.stewardship.web.contracts import MESSAGES, ErrorCode


def components(context, admin):
    """Production shaping over sample entries, so the page and its rows agree."""
    moment = datetime(2026, 9, 19, 15, 4, 5, 123456, tzinfo=UTC)
    operational = [
        operational_row(
            dict(
                id=UUID(int=100 + index),
                created_at=moment - timedelta(minutes=index),
                level=level,
                event="task_failed",
                actor_id=None,
                correlation_id=UUID(int=200 + index),
                # A reviewed field whose text must still be shown as text.
                context={"outcome": "failed", "count": index, "reason": "<b>safe</b>"},
            )
        )
        for index, level in enumerate(LEVEL_LABELS)
    ]
    audit = [
        audit_row(
            {
                "id": UUID(int=300),
                "created_at": moment - timedelta(seconds=30),
                "event_type": "dashboard_viewed",
                "actor_id": UUID(int=301),
                "correlation_id": UUID(int=302),
                "campaign_reference": UUID(int=303),
                "subject_id": UUID(int=304),
                "auditcontext__context": {"outcome": "succeeded", "count": 7},
            }
        ),
        # A bare login event has no context row, and its actor has since left.
        audit_row(
            {
                "id": UUID(int=310),
                "created_at": moment - timedelta(seconds=90),
                "event_type": "admin_login",
                "actor_id": UUID(int=311),
                "correlation_id": UUID(int=312),
                "campaign_reference": None,
                "subject_id": None,
                "auditcontext__context": None,
            }
        ),
    ]
    audit[0]["actor"] = "admin@example.org"
    audit[1]["actor"] = None
    for row in operational:
        row["actor"] = None
    # The first operational entry was recorded by a background worker whose
    # task is its subject, as task entries are; the view marks both.
    operational[0]["actor_id"] = UUID(int=900)
    operational[0]["actor_worker"] = True

    def page(query, rows, *, number=1, total=None, action="/logs"):
        """Render the production context builder's output, as the view does.

        Navigator and heading forms post back to this fixture's own path, or
        to ``action`` (the fixture server's redirecting path, for #478).
        """
        table = log_table(
            query, rows, through=moment, action=action, number=number, total=total
        )
        return render_to_string(
            "stewardship/logs.html",
            context | {"admin_chrome": admin} | page_context(query, table),
        )

    everything = LogQuery.parse(
        {
            "applied": "yes",
            "debug": "yes",
            "info": "yes",
            "warning": "yes",
            "error": "yes",
            "critical": "yes",
            "correlation": str(UUID(int=200)),
            "size": "25",
        }
    )
    # Six of the seven entries, newest first: four levels and both audit
    # records, shown as the first page of a longer snapshot.
    rows = merge(operational, audit)[:6]
    older = LogQuery.parse(
        {"applied": "yes", "error": "yes", "size": "25", "page": "2"}
    )
    # The same snapshot oldest first: what the Time heading's POST form
    # returns, served to the in-place re-sort tests from a GET path (#478).
    oldest = replace(everything, sort="oldest")
    result = {
        "/logs": ("text/html", page(everything, rows, total=30)),
        "/logs-oldest": ("text/html", page(oldest, rows[::-1], total=30)),
        # The same page whose controls post to the server's redirecting path.
        "/logs-expired": (
            "text/html",
            page(everything, rows, total=30, action="/redirect-to-login"),
        ),
        "/logs-default": ("text/html", page(LogQuery(), operational[1:3])),
        "/logs-older": (
            "text/html",
            page(older, [*operational[4:], audit[1]], number=2, total=27),
        ),
        "/logs-empty": ("text/html", page(LogQuery.parse({"applied": "yes"}), [])),
    }
    # The three error states: a refused filter value, a refused query string
    # and an outage, which is also what a denied reader sees.
    errors = {
        "400": (ErrorCode.INVALID, False),
        "query": (ErrorCode.INVALID, True),
        "503": (ErrorCode.UNAVAILABLE, False),
    }
    for name, (code, query_string) in errors.items():
        result[f"/logs-error-{name}"] = (
            "text/html",
            render_to_string(
                "stewardship/logs-error.html",
                context
                | {"admin_chrome": admin}
                | {
                    "message": MESSAGES[code],
                    "invalid": code is ErrorCode.INVALID and not query_string,
                    "query_string": query_string,
                },
            ),
        )
    return result
