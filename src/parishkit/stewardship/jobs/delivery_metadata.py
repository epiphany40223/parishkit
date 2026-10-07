"""Explicit operational read projection; no rendered mail or credential reads."""

import json
import re
from uuid import UUID

from django.db import connection
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.tables import Sorting, bounded_count, read_window

from .outbox_models import OutboxMessage
from .send_history import email_ids

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
# How each outbox state reads to an Administrator, in the plain words the
# Family-question pages use (#523, #589). The Family timeline shows the
# outcome; Outgoing mail and the Mail message page show the state, which
# adds where a "Still sending" email is, so a delivery problem (waiting to
# retry) stays visible. "Delivered" means the mail service accepted the
# email; the pages' About panels say that does not prove it reached the inbox.
OUTCOMES = {
    "delivered": _("Delivered"),
    "permanent_failure": _("Failed"),
    "delivery_unknown": _("Not sure it arrived"),
    "pending": _("Still sending"),
    "retry_wait": _("Still sending"),
    "submitting": _("Still sending"),
    "cancelled": _("Not sent (cancelled)"),
}
STATE_LABELS = OUTCOMES | {
    "pending": _("Still sending (queued)"),
    "retry_wait": _("Still sending (waiting to retry)"),
    "submitting": _("Still sending (handing to the mail service)"),
}


def messages():
    """Expose implemented delivery owners, never private rendered payloads."""
    return OutboxMessage.objects.filter(purpose__in=PURPOSES)


def unknown_count():
    """Count uncertainty, not failed Tasks or inferred non-acceptance."""
    return messages().filter(state="delivery_unknown").count()


def alert_counts(since, *, limit):
    """Read independent indexed warning totals in one Admin-shell round trip.

    Returns ``({event: count}, [log id], delivery_unknown, {event: ended})``.
    CRITICAL operational events count when they are newer than ``since`` and
    have no shared acknowledgement. The ids, oldest first and at most
    ``limit``, are the counted rows the banner's Acknowledge form may
    acknowledge; reading them in the same statement keeps them consistent
    with the counts. Matching exact rows rather than a time watermark means a
    CRITICAL row committed after an acknowledgement by a long transaction
    still appears. Scalar subqueries avoid multiplying log and outbox rows in
    a join. The immediate server-rendered warning must also work without
    browser polling.

    ``ended`` names, for each event whose problem has ended, when it ended
    (#633): every counted row was taken in by an operational incident (its
    receipt) and every such incident has resolved. A row not yet taken in,
    or an incident still open, means the problem may be going on.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "WITH pending AS (SELECT log.id, log.event, log.created_at "
            "FROM stewardship_operational_log AS log "
            "WHERE log.level='CRITICAL' AND log.created_at>=%s AND NOT EXISTS "
            "(SELECT 1 FROM stewardship_critical_event_ack AS ack "
            "WHERE ack.log_id=log.id)), "
            "grouped AS (SELECT pending.event, count(*) AS total, "
            "bool_and(incident.resolved_at IS NOT NULL) AS ended, "
            "max(incident.resolved_at) AS ended_at FROM pending "
            "LEFT JOIN stewardship_ops_log_receipt AS receipt "
            "ON receipt.log_id=pending.id "
            "LEFT JOIN stewardship_ops_incident AS incident "
            "ON incident.id=receipt.incident_id GROUP BY pending.event) "
            "SELECT (SELECT coalesce(jsonb_object_agg(event, total), '{}'::jsonb) "
            "FROM grouped), "
            "(SELECT coalesce(array_agg(id ORDER BY created_at, id), '{}') "
            "FROM (SELECT id, created_at FROM pending "
            "ORDER BY created_at, id LIMIT %s) AS oldest), "
            "(SELECT count(*) FROM stewardship_outbox_message "
            "WHERE state='delivery_unknown' AND purpose=ANY(%s)), "
            "(SELECT coalesce(array_agg(event ORDER BY event), '{}') "
            "FROM grouped WHERE ended), "
            "(SELECT coalesce(array_agg(ended_at ORDER BY event), '{}') "
            "FROM grouped WHERE ended)",
            (since, limit, list(PURPOSES)),
        )
        events, ids, unknown, ended_events, ended_times = cursor.fetchone()
    if isinstance(events, str):
        events = json.loads(events)
    counts = {str(key): int(value) for key, value in events.items()}
    ended = dict(zip(ended_events, ended_times, strict=True))
    return counts, [UUID(str(value)) for value in ids], unknown, ended


def listing(window, *, state, query, sort=DELIVERY_SORTING.default, send=None):
    """Accept a bounded exact Family DUID or delivery UUID, not arbitrary SQL.

    Returns (window, rows, has_next, total): ``total`` is a bounded count of
    every matching message (``web.tables.bounded_count``), and ``window`` the
    page actually read (the last one when the requested page is past it).
    ``sort`` is a DELIVERY_SORTING token the caller already validated.
    ``send``, a ``send_history.SendKey``, keeps only that Family email send's
    emails: each Family's newest one, exactly those its history counts read.
    """
    if state not in STATES or type(query) is not str or len(query) > 64:
        raise ValueError("Invalid delivery filter.")
    selected = messages()
    if send is not None:
        # About one id per Family, read through the definition index; the
        # messages are then found by primary key.
        selected = selected.filter(pk__in=email_ids(send))
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
    total = bounded_count(selected)
    window, rows, has_next = read_window(
        window, DELIVERY_SORTING.order(selected, sort).values(*FIELDS), total
    )
    return window, rows, has_next, total


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
