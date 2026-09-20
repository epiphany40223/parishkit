"""The system logs screen reuses the shared browser server and real row shaping."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.audit.log_rows import (
    EVENTS,
    LEVEL_LABELS,
    SOURCES,
    LogQuery,
    audit_row,
    merge,
    operational_row,
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
                context={"outcome": "failed", "count": index, "note": "<b>safe</b>"},
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

    def page(query, rows, following):
        """Render exactly the context the view builds."""
        return render_to_string(
            "stewardship/logs.html",
            context
            | {"admin_chrome": admin}
            | {
                "rows": rows,
                "query": query,
                "query_fields": query.form_values(),
                "following": following,
                "levels": [
                    (level.lower(), LEVEL_LABELS[level], level in query.levels)
                    for level in LEVEL_LABELS
                ],
                "sources": SOURCES,
                "events": EVENTS,
            },
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
        }
    )
    # Six of the seven entries: four levels and both audit records, one older.
    rows, following = merge(operational, audit, size=6)
    older = LogQuery.parse({"applied": "yes", "error": "yes"} | following)
    result = {
        "/logs": ("text/html", page(everything, rows, following)),
        "/logs-default": ("text/html", page(LogQuery(), operational[1:3], None)),
        "/logs-older": ("text/html", page(older, [*operational[4:], audit[1]], None)),
        "/logs-empty": (
            "text/html",
            page(LogQuery.parse({"applied": "yes"}), [], None),
        ),
    }
    for code, status in ((ErrorCode.INVALID, 400), (ErrorCode.UNAVAILABLE, 503)):
        result[f"/logs-error-{status}"] = (
            "text/html",
            render_to_string(
                "stewardship/logs-error.html",
                context | {"admin_chrome": admin} | {"message": MESSAGES[code]},
            ),
        )
    return result
