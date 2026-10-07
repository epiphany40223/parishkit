"""The system logs screen reuses the shared browser server and real row shaping."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.audit.log_rows import (
    LEVEL_LABELS,
    LogQuery,
    audit_row,
    log_table,
    merge,
    operational_row,
    page_context,
)
from parishkit.stewardship.audit.log_views import ZONE_MESSAGE
from parishkit.stewardship.web.contracts import MESSAGES, ErrorCode

# Where the in-place cross-link tests serve System logs (#519 PR 3).
LIVE = "/logs-live"

# Entries whose context System logs explains in words (#633): a recovery,
# late scheduled work and a ParishSoft call that will be retried.
DETAILED = (
    (
        "INFO",
        "incident_recovered",
        "recovery",
        {
            "incident_id": str(UUID(int=600)),
            "incident_kind": "scheduler_lag",
            "log_id": str(UUID(int=402)),
            "elapsed_seconds": 1800,
            "count": 2,
        },
    ),
    (
        "WARNING",
        "source_provider_failed",
        "failure",
        {
            "failure": "provider_status",
            "status": 503,
            "task_id": str(UUID(int=601)),
            "attempt": 2,
            "attempt_limit": 5,
            "retry_seconds": 60,
            "outcome": "retry",
        },
    ),
    (
        "CRITICAL",
        "due_work_lag",
        "due_work",
        {
            "task_type": "outbox_delivery",
            "count": 977,
            "lag_seconds": 1020,
            "limit_seconds": 90,
        },
    ),
)


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

    def page(query, rows, *, number=1, total=None, action="/logs", linked=False):
        """Render the production context builder's output, as the view does.

        Navigator and heading forms post back to this fixture's own path, or
        to ``action`` (the fixture server's redirecting path, for #478).
        ``linked`` draws the page as a followed link's answer (#536).
        """
        table = log_table(
            query, rows, through=moment, action=action, number=number, total=total
        )
        return render_to_string(
            "stewardship/logs.html",
            context
            | {"admin_chrome": admin}
            | page_context(query, table, linked=linked),
        )

    everything = LogQuery.parse(
        {
            "applied": "yes",
            "debug": "yes",
            "info": "yes",
            "warning": "yes",
            "error": "yes",
            "critical": "yes",
            "audit": "yes",
            "correlation": str(UUID(int=200)),
            "size": "25",
        }
    )
    # Six of the seven entries, newest first: four levels and both audit
    # records, shown as the first page of a longer snapshot.
    rows = merge(operational, audit)[:6]
    older = LogQuery.parse(
        {"applied": "yes", "error": "yes", "audit": "yes", "size": "25", "page": "2"}
    )
    # The same snapshot oldest first: what the Time heading's POST form
    # returns, served to the in-place re-sort tests from a GET path (#478).
    oldest = replace(everything, sort="oldest")
    # Local-day filters as applied from a Los Angeles browser (#558): the
    # zone is carried with the days by every table control and the export.
    dated = LogQuery.parse(
        {
            "applied": "yes",
            "info": "yes",
            "error": "yes",
            "audit": "yes",
            "start": "2026-09-19",
            "end": "2026-09-19",
            "zone": "America/Los_Angeles",
            "size": "25",
        }
    )
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
        "/logs-empty": (
            "text/html",
            page(LogQuery.parse({"applied": "yes", "critical": "yes"}), []),
        ),
        "/logs-dated": ("text/html", page(dated, rows, total=30)),
        # One kind of entry at a time, as the Show choices select (#601).
        "/logs-audit": (
            "text/html",
            page(LogQuery.parse({"applied": "yes", "audit": "yes"}), audit),
        ),
        "/logs-operational": (
            "text/html",
            page(
                LogQuery.parse({"applied": "yes", "warning": "yes", "error": "yes"}),
                operational[2:4],
            ),
        ),
    }

    # The page as the in-place cross-links (#519 PR 3) need it: each answer
    # must be this same page, so it is served at the address its forms post
    # to. The menu tests already serve their own page at the real System
    # logs address, so this one lives at LIVE and its cross-links post there
    # (their action, the real address, is rewritten). Every kind of entry,
    # no identifier filter yet. The cross-links' answers follow: the audit
    # record's related entries and its campaign's audit records (each still
    # listing that record), and a Same actor answer that no longer lists the
    # entry it was chosen from.
    def live(query, rows, **options):
        """``page`` with every form posting to LIVE."""
        html = page(query, rows, action=LIVE, **options)
        # Reverse the real address so this follows any move of System logs.
        # The filter form posts there too, without a fragment (#536).
        real = reverse("admin:logs")
        return html.replace(f'action="{real}#', f'action="{LIVE}#').replace(
            f'action="{real}"', f'action="{LIVE}"'
        )

    ticks = {key: "yes" for key in ("applied", "info", "warning", "error", "audit")}
    shown = LogQuery.parse(ticks)
    result[LIVE] = ("text/html", live(shown, rows, total=30))
    for path, parameters, found in (
        ("/logs-related", {"correlation": str(UUID(int=302))}, [audit[0]]),
        (
            "/logs-campaign",
            {"applied": "yes", "audit": "yes", "campaign": str(UUID(int=303))},
            [audit[0]],
        ),
        ("/logs-actor", {"actor": str(UUID(int=900))}, [operational[1]]),
        ("/logs-live-oldest", ticks | {"sort": "oldest"}, rows[::-1]),
        # An applied search with a Ministry and an identifier (#536): the
        # address may carry the first two only.
        (
            "/logs-searched",
            ticks
            | {"text": "lag", "ministry": "42", "correlation": str(UUID(int=302))},
            [audit[0]],
        ),
    ):
        result[path] = ("text/html", live(LogQuery.parse(parameters), found))
    # A followed link whose days were applied in Tokyo (#536).
    result["/logs-linked-tokyo"] = (
        "text/html",
        live(
            LogQuery.parse({"start": "2026-09-01", "zone": "Asia/Tokyo"}),
            rows,
            linked=True,
        ),
    )
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
    # Any Admin page with the critical-events banner, whose System logs form
    # sends a From day with the browser's zone (#558).
    banner = admin | {
        "critical_count": 2,
        "critical_events": [{"label": "Source data failed checks", "count": 2}],
        "critical_since_day": "2026-09-17",
        "critical_shown": "signed",
        "critical_limit": 100,
    }
    result["/logs-critical-banner"] = (
        "text/html",
        render_to_string(
            "stewardship/logs.html",
            context
            | {"admin_chrome": banner}
            | page_context(
                LogQuery(), log_table(LogQuery(), [], through=moment, action="/logs")
            ),
        ),
    )
    # Serious entries and a recovery, each with its sentence (#633), under a
    # banner whose one problem has ended.
    detailed = [
        operational_row(
            dict(
                id=UUID(int=400 + index),
                created_at=moment - timedelta(minutes=index),
                level=level,
                event=event,
                actor_id=None,
                correlation_id=UUID(int=500),
                schema=schema,
                context=detail,
            )
        )
        for index, (level, event, schema, detail) in enumerate(DETAILED)
    ]
    for row in detailed:
        row["actor"] = None
    ended = banner | {
        "critical_count": 1,
        "critical_all_ended": True,
        "critical_events": [
            {
                "label": "Scheduled work is running late",
                "count": 1,
                "ended_at": moment - timedelta(minutes=1),
            }
        ],
    }
    result["/logs-detail"] = (
        "text/html",
        render_to_string(
            "stewardship/logs.html",
            context
            | {"admin_chrome": ended}
            | page_context(
                everything,
                log_table(everything, detailed, through=moment, action="/logs"),
            ),
        ),
    )
    # Dates that arrived without a zone the server knows (#558).
    result["/logs-error-zone"] = (
        "text/html",
        render_to_string(
            "stewardship/logs-error.html",
            context
            | {"admin_chrome": admin}
            | {"message": ZONE_MESSAGE, "invalid": False, "query_string": False},
        ),
    )
    return result
