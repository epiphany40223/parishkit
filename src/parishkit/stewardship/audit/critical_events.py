"""The Admin critical-events banner: plain-language summary and shared acknowledgement.

CRITICAL operational log rows from the last day are grouped by event for the
banner. The banner's Acknowledge form carries a signed list of the exact row
ids it counted when the page was rendered, and an acknowledgement records only
those rows. They then stay hidden for every Admin, while any other CRITICAL
row, whether recorded after the page was shown or committed late by a
long-running transaction with an earlier time, still appears: ids, not a time
watermark, decide what was seen. Acknowledging changes no log row; it only
records who saw what, with its audit event, in the same transaction.
"""

import re
from datetime import timedelta
from uuid import UUID

from django.core import signing
from django.db import transaction

from parishkit.stewardship.observability import Event

from .models import CriticalEventAcknowledgement, OperationalLog
from .schemas import Action, ActorKind, Outcome
from .services import record_action

# The banner looks back one day; acknowledgement hides events inside it.
WINDOW = timedelta(hours=24)
# At most this many row ids, oldest first, are signed into one banner form so
# a failure loop cannot bloat every Admin page. Rows past the limit stay
# counted and reappear after an acknowledgement, to be acknowledged next.
ACKNOWLEDGE_LIMIT = 500
SALT = "parishkit.stewardship.critical-events-acknowledge"
_HEX_ID = re.compile(r"[0-9a-f]{32}")

# Plain-language names for events that are recorded at CRITICAL level. Any
# other event falls back to its identifier with underscores spelled as words.
LABELS = {
    Event.SOURCE_INVALID.value: "ParishSoft data refresh failed",
    Event.SOURCE_TENANT_MISMATCH.value: "ParishSoft organization does not match",
    Event.SOURCE_DESTRUCTIVE_CHANGE.value: (
        "ParishSoft refresh would remove too much data"
    ),
    Event.SOURCE_HELD.value: "ParishSoft refresh is on hold",
    Event.SOURCE_CREDENTIAL_FAILED.value: "ParishSoft API key was refused",
    Event.SOURCE_PROVIDER_FAILED.value: "ParishSoft could not be reached",
    Event.MAIL_PROVIDER_FAILED.value: "Email sending failed",
    Event.TASK_FAILED.value: "Background task failed",
    Event.FACT_DRIFT.value: "Report figures did not verify",
    Event.DUE_WORK_LAG.value: "Scheduled work is running late",
    Event.BOUNDARY_LAG.value: "Campaign start or close is running late",
}


def label(event):
    """Name one event for staff; unknown identifiers still read as words."""
    return LABELS.get(event) or event.replace("_", " ").capitalize()


def summary(counts, ended=None):
    """Order the banner's groups by how often they happened, then by name.

    ``ended`` maps an event to when its problem ended (``alert_counts``), so
    the banner can say a problem is over rather than still going on (#633).
    """
    ended = ended or {}
    return [
        {"label": label(event), "count": total, "ended_at": ended.get(event)}
        for event, total in sorted(
            counts.items(), key=lambda item: (-item[1], label(item[0]))
        )
    ]


def sign(ids):
    """Sign the shown row ids into the Acknowledge form's token.

    Compact hex ids, compressed, keep the hidden field small; the signature
    (the project's secret key, with this module's salt) makes any edit to the
    list fail ``shown`` rather than widen what is acknowledged.
    """
    return signing.dumps(
        [UUID(str(value)).hex for value in ids], salt=SALT, compress=True
    )


def shown(token):
    """Return the row ids a genuine banner form signed, or raise BadSignature.

    A token that is missing, altered, signed for another purpose, or holding
    anything but a bounded list of ids is refused as a bad signature, so the
    view answers it like any other tampered form.
    """
    if type(token) is not str or not token:
        raise signing.BadSignature("Missing acknowledgement list.")
    # No max_age and no actor or session binding, on purpose: the signature
    # only proves some Administrator's rendered banner counted these ids, and
    # ``acknowledge`` rechecks each one (CRITICAL, inside the window, not yet
    # acknowledged; acknowledgements are append-only). A replayed, old or
    # another Administrator's token can therefore hide nothing that a banner
    # did not show, and a spent one records nothing.
    values = signing.loads(token, salt=SALT)
    if (
        type(values) is not list
        or not values
        or len(values) > ACKNOWLEDGE_LIMIT
        or not all(type(value) is str and _HEX_ID.fullmatch(value) for value in values)
    ):
        raise signing.BadSignature("Malformed acknowledgement list.")
    return [UUID(value) for value in values]


def acknowledge(actor_id, *, ids, since, parish_id):
    """Acknowledge the CRITICAL rows the banner showed, and no others.

    ``ids`` comes from ``shown``: the rows counted when the page was rendered.
    Only those still inside the window and not yet acknowledged are recorded,
    so a CRITICAL row recorded after the page was shown stays visible. Returns
    how many rows were acknowledged; zero records nothing. A row that another
    Administrator acknowledged concurrently is skipped by its unique log
    reference rather than refused, so both requests succeed.
    """
    with transaction.atomic():
        pending = list(
            OperationalLog.objects.filter(
                id__in=ids, level="CRITICAL", created_at__gte=since
            )
            .exclude(id__in=CriticalEventAcknowledgement.objects.values("log_id"))
            .values_list("id", flat=True)
        )
        if not pending:
            return 0
        CriticalEventAcknowledgement.objects.bulk_create(
            [
                CriticalEventAcknowledgement(log_id=log_id, actor_id=actor_id)
                for log_id in pending
            ],
            ignore_conflicts=True,
        )
        record_action(
            Action.CRITICAL_EVENTS_ACKNOWLEDGED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor_id,
            parish_id=parish_id,
            context={"outcome": Outcome.SUCCEEDED, "count": len(pending)},
        )
    return len(pending)
