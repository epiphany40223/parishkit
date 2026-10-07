"""Closed human-readable labels for the dedicated delivery Admin interface."""

from django import template
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.jobs.delivery_metadata import STATE_LABELS

register = template.Library()
# Outbox states use the shared plain words (STATE_LABELS), the same table the
# Family timeline reads, so one email reads alike a click apart (#589).
# Attempt-history actions below describe events, not outcomes, and keep
# their own wording.
LABELS = STATE_LABELS | {
    "all": _("All"),
    "initial": _("Initial invitation"),
    "reminder": _("Reminder"),
    "daily_digest": _("Daily Administrator report"),
    "weekly_digest": _("Weekly Administrator report"),
    "receipt": _("Submission receipt"),
    "family_test": _("Selected-Family test"),
    "production": _("Production"),
    "testing": _("Testing"),
    "note": _("Evidence note"),
    "accept": _("Delivery confirmed"),
    "confirm_unsent": _("Provider confirmed not sent"),
    "resend": _("Resend authorized"),
    "retry_failed": _("Failed delivery retry"),
    "retry_unsent": _("Unaccepted delivery retry"),
    "mark_unknown": _("Acceptance unknown"),
    "authorize_resend": _("Duplicate-risk resend authorized"),
    "created": _("Created"),
    "prepared": _("Current content prepared"),
    "hold": _("Delivery held"),
    "release_hold": _("Delivery hold released"),
    "submit": _("Provider submission started"),
    "retry_unaccepted": _("Not accepted; retry scheduled"),
    "fail_unaccepted": _("Not accepted; delivery failed"),
    "cancel_unsent": _("Unaccepted delivery cancelled"),
    "retry_idempotent": _("Idempotent retry scheduled"),
    "verified_admin": _("Verified by an Administrator"),
    "source_changed": _("Corrected by source refresh"),
}
# A background task's states. Two share a key with outbox states
# (retry_wait, cancelled) but mean something else for a task, so tasks
# have their own table and filter.
TASK_LABELS = {
    "queued": _("Queued"),
    "running": _("Running"),
    "retry_wait": _("Waiting to retry"),
    "abandoned": _("Lease expired"),
    "succeeded": _("Succeeded"),
    "failed": _("Failed"),
    "cancelled": _("Cancelled"),
}
# Admin evidence reuses a provider outcome action (confirm_unsent records
# fail_unaccepted), so history names such events by their Admin reason.
EVENT_REASONS = {
    "admin_confirmed_unsent": _("Not sent, per provider records; no resend"),
}


@register.filter
def delivery_label(value):
    """Unknown internal values never become untranslated implementation jargon."""
    return LABELS.get(value, _("Unknown status"))


@register.filter
def delivery_task_label(value):
    """A background task's state, never read as an email's delivery state."""
    return TASK_LABELS.get(value, _("Unknown status"))


@register.filter
def delivery_event_label(event):
    """Label one attempt event, never presenting an Admin record as the provider's."""
    return EVENT_REASONS.get(event.get("reason")) or delivery_label(event.get("action"))
