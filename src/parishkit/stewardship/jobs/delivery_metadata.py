"""Explicit operational read projection; no rendered mail or credential reads."""

from uuid import UUID

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


def listing(window, *, state, query):
    """Accept a bounded exact Family DUID or delivery UUID, not arbitrary SQL."""
    if state not in STATES or type(query) is not str or len(query) > 64:
        raise ValueError("Invalid delivery filter.")
    selected = messages()
    if state != "all":
        selected = selected.filter(state=state)
    if query:
        if query.isascii() and query.isdecimal() and 0 < int(query) < 2**63:
            selected = selected.filter(family__family_duid=int(query))
        else:
            try:
                identifier = UUID(query)
            except ValueError:
                raise ValueError("Use an exact Family DUID or delivery ID.") from None
            selected = selected.filter(Q(pk=identifier) | Q(family_id=identifier))
    return window.rows(selected.order_by("-created_at", "-id").values(*FIELDS))
