"""Explicit operational read projection; no rendered mail or credential reads."""

import re
from uuid import UUID

from django.db import connection
from django.db.models import Q

from .outbox_models import OutboxMessage

FIELDS = (
    "id",
    "campaign_id",
    "family_id",
    "family__family_duid",
    "semantic_key",
    "purpose",
    "mode",
    "state",
    "version",
    "attempt",
    "task_id",
    "created_at",
    "updated_at",
    "finished_at",
)
STATES = (
    "all",
    "delivery_unknown",
    "permanent_failure",
    "pending",
    "retry_wait",
    "submitting",
    "delivered",
    "cancelled",
)


def messages():
    """Keep later receipt/digest owners outside this increment's operational view."""
    return OutboxMessage.objects.filter(purpose__in=("initial", "reminder"))


def unknown_count():
    """Count uncertainty, not failed Tasks or inferred non-acceptance."""
    return messages().filter(state="delivery_unknown").count()


def alert_counts(since):
    """Read independent indexed warning totals in one Admin-shell round trip.

    A scalar subquery avoids multiplying log and outbox rows in a join. The
    immediate server-rendered warning must also work without browser polling.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT count(*) FROM stewardship_operational_log "
            "WHERE level='CRITICAL' AND created_at>=%s), "
            "(SELECT count(*) FROM stewardship_outbox_message "
            "WHERE state='delivery_unknown' AND purpose IN ('initial','reminder'))",
            (since,),
        )
        return cursor.fetchone()


def listing(window, *, state, query):
    """Accept a bounded exact Family DUID or delivery UUID, not arbitrary SQL."""
    if state not in STATES or type(query) is not str or len(query) > 64:
        raise ValueError("Invalid delivery filter.")
    selected = messages()
    if state != "all":
        selected = selected.filter(state=state)
    if query:
        if query.isascii() and (query.isdecimal() or "," in query):
            selected = selected.filter(family__family_duid=family_duid(query))
        else:
            try:
                identifier = UUID(query)
            except ValueError:
                raise ValueError("Use an exact Family DUID or delivery ID.") from None
            selected = selected.filter(Q(pk=identifier) | Q(family_id=identifier))
    return window.rows(selected.order_by("-created_at", "-id").values(*FIELDS))


def family_duid(value):
    """Accept copied US-grouped identifiers as well as their canonical digits."""
    if type(value) is not str or len(value) > 25:
        raise ValueError("Use an exact Family DUID.")
    if re.fullmatch(r"[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+", value) is None:
        raise ValueError("Use an exact Family DUID.")
    result = int(value.replace(",", ""))
    if not 0 < result < 2**63:
        raise ValueError("Use an exact Family DUID.")
    return result
