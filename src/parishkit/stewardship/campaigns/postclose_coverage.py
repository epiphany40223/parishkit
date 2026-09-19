"""Semantic digest skips remain excluded across scheduler/revision restarts."""

from django.db.models.functions import Substr

from .schedule_models import PostCloseMailResolution


def resolved_slots(definition_id, mode, *, slots=None):
    """Return a SQL subquery of original slots, without loading coverage payloads."""
    prefix = f"schedule:{definition_id}:"
    rows = PostCloseMailResolution.objects.filter(
        mode=mode, obligation_key__startswith=prefix
    ).annotate(slot=Substr("obligation_key", len(prefix) + 1))
    if slots is not None:
        rows = rows.filter(slot__in=slots)
    return rows.values("slot")
