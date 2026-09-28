"""The Admin critical-events banner: plain-language summary and shared acknowledgement.

CRITICAL operational log rows from the last day are grouped by event for the
banner. An Administrator's acknowledgement records each CRITICAL row the banner
counts at that moment, so they stay hidden for every Admin while any other
CRITICAL row, including one committed later by a long-running transaction,
appears. Acknowledging changes no log row; it only records who saw what, with
its audit event, in the same transaction.
"""

from datetime import timedelta

from django.db import transaction

from parishkit.stewardship.observability import Event

from .models import CriticalEventAcknowledgement, OperationalLog
from .schemas import Action, ActorKind, Outcome
from .services import record_action

# The banner looks back one day; acknowledgement hides events inside it.
WINDOW = timedelta(hours=24)

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


def summary(counts):
    """Order the banner's groups by how often they happened, then by name."""
    return [
        {"label": label(event), "count": total}
        for event, total in sorted(
            counts.items(), key=lambda item: (-item[1], label(item[0]))
        )
    ]


def acknowledge(actor_id, *, since, parish_id):
    """Acknowledge every CRITICAL row the banner currently counts.

    Returns how many rows were acknowledged; zero records nothing. A row that
    another Administrator acknowledged concurrently is skipped by its unique
    log reference rather than refused, so both requests succeed.
    """
    with transaction.atomic():
        pending = list(
            OperationalLog.objects.filter(level="CRITICAL", created_at__gte=since)
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
