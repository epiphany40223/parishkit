"""Explicit operational read projection; no rendered mail or credential reads."""

import json
import re
from uuid import UUID

from django.db import connection
from django.db.models import Q

from parishkit.stewardship.web.tables import Sorting, bounded_count

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
PURPOSES = (
    "initial",
    "reminder",
    "receipt",
    "family_test",
    "daily_digest",
    "weekly_digest",
)
# Every Outgoing mail column sorts on the server. Recipient sorts by Family
# DUID (Administrator reports, which have no Family, sort last either way);
# Created is the default, newest first. Only the state filter is indexed
# (outbox_due, outbox_campaign_state); the orderings themselves are not, so
# a page is a top-N sort of the filtered messages. The default "all" view
# therefore scans the outbox, as its newest-first order always did; the
# outbox grows by about one message per Family per mailing and is not
# purged. id is the unique tiebreak.
DELIVERY_SORTING = Sorting.by_column(
    {
        "recipient": ("family__family_duid",),
        "purpose": ("purpose",),
        "mode": ("mode",),
        "state": ("state",),
        "attempts": ("attempt",),
        "changed": ("updated_at",),
        "created": ("created_at",),
    },
    default="-created",
    descending_first={"attempts", "changed", "created"},
    tiebreak=("id",),
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
    """Expose implemented delivery owners, never private rendered payloads."""
    return OutboxMessage.objects.filter(purpose__in=PURPOSES)


def unknown_count():
    """Count uncertainty, not failed Tasks or inferred non-acceptance."""
    return messages().filter(state="delivery_unknown").count()


def alert_counts(since):
    """Read independent indexed warning totals in one Admin-shell round trip.

    Returns ``({event: count}, delivery_unknown)``. CRITICAL operational events
    count when they are newer than ``since`` and have no shared
    acknowledgement. Matching exact rows rather than a time watermark means a
    CRITICAL row committed after an acknowledgement by a long transaction still
    appears. Scalar subqueries avoid multiplying log and outbox rows in a join.
    The immediate server-rendered warning must also work without browser
    polling.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT coalesce(jsonb_object_agg(event, total), '{}'::jsonb) "
            "FROM (SELECT log.event, count(*) AS total "
            "FROM stewardship_operational_log AS log "
            "WHERE log.level='CRITICAL' AND log.created_at>=%s AND NOT EXISTS "
            "(SELECT 1 FROM stewardship_critical_event_ack AS ack "
            "WHERE ack.log_id=log.id) "
            "GROUP BY log.event) AS grouped), "
            "(SELECT count(*) FROM stewardship_outbox_message "
            "WHERE state='delivery_unknown' AND purpose=ANY(%s))",
            (since, list(PURPOSES)),
        )
        events, unknown = cursor.fetchone()
    if isinstance(events, str):
        events = json.loads(events)
    return {str(key): int(value) for key, value in events.items()}, unknown


def listing(window, *, state, query, sort=DELIVERY_SORTING.default):
    """Accept a bounded exact Family DUID or delivery UUID, not arbitrary SQL.

    Returns (rows, has_next, total): ``total`` is a bounded count of every
    matching message (``web.tables.bounded_count``). ``sort`` is a
    DELIVERY_SORTING token the caller already validated.
    """
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
    rows, has_next = window.rows(DELIVERY_SORTING.order(selected, sort).values(*FIELDS))
    return rows, has_next, bounded_count(selected)


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
