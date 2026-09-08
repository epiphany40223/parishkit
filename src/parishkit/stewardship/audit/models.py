"""Parish-owned audit envelope; private payload services belong to ARC-07.

No free-form message, URL, session key, or submitted values are stored here.
Event-specific payload schemas and retention/export workflows are deliberately
not enabled by this foundation.
"""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class AuditEvent(ImmutableRecord):
    """Permanent event identity and soft historical subject/actor references."""

    event_type = models.CharField(max_length=64)
    subject_id = models.UUIDField(null=True)

    class Meta:
        db_table = "stewardship_audit_event"
        indexes = [
            models.Index(fields=["event_type", "created_at"], name="audit_event_time")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(event_type__regex=r"^[a-z][a-z0-9_]{0,63}$"),
                name="audit_event_type_identifier",
            )
        ]
