"""Closed human-readable labels for the dedicated delivery Admin interface."""

from django import template
from django.utils.translation import gettext_lazy as _

register = template.Library()
LABELS = {
    "all": _("All"),
    "delivery_unknown": _("Delivery unknown"),
    "permanent_failure": _("Failed delivery"),
    "pending": _("Pending"),
    "retry_wait": _("Waiting to retry"),
    "submitting": _("Submitting"),
    "delivered": _("Delivered"),
    "cancelled": _("Cancelled"),
    "initial": _("Initial invitation"),
    "reminder": _("Reminder"),
    "daily_digest": _("Daily Administrator report"),
    "weekly_digest": _("Weekly Administrator report"),
    "receipt": _("Submission receipt"),
    "family_test": _("Selected-Family test"),
    "production": _("Production"),
    "testing": _("Testing"),
    "queued": _("Queued"),
    "running": _("Running"),
    "failed": _("Failed"),
    "succeeded": _("Succeeded"),
    "abandoned": _("Lease expired"),
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
def delivery_event_label(event):
    """Label one attempt event, never presenting an Admin record as the provider's."""
    return EVENT_REASONS.get(event.get("reason")) or delivery_label(event.get("action"))
