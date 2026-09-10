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
    subject_id = models.UUIDField(null=True, blank=True)
    ownership_scope = models.CharField(max_length=16, db_default="deployment")
    parish = models.ForeignKey(
        "stewardship_accounts.Parish",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_default=None,
        related_name="audit_events",
    )
    # Deliberately not a Campaign FK: retained parish history survives purge.
    campaign_reference = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "stewardship_audit_event"
        indexes = [
            models.Index(fields=["event_type", "created_at"], name="audit_event_time")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(event_type__regex=r"^[a-z][a-z0-9_]{0,63}$"),
                name="audit_event_type_identifier",
            ),
            models.CheckConstraint(
                condition=models.Q(ownership_scope="parish", parish__isnull=False)
                | models.Q(
                    ownership_scope="deployment",
                    parish__isnull=True,
                    campaign_reference__isnull=True,
                ),
                name="audit_ownership_shape",
            ),
        ]


class AuditContext(ImmutableRecord):
    """Reviewed structured context never contains the values of a viewed report."""

    event = models.OneToOneField(AuditEvent, on_delete=models.PROTECT)
    actor_kind = models.CharField(max_length=16)
    schema = models.CharField(max_length=16)
    context = models.JSONField(default=dict)

    class Meta:
        db_table = "stewardship_audit_context"


class OperationalLog(ImmutableRecord):
    """Queryable diagnostics with closed event/schema values, not free-form text."""

    level = models.CharField(max_length=8)
    event = models.CharField(max_length=64)
    schema = models.CharField(max_length=16)
    context = models.JSONField(default=dict)

    class Meta:
        db_table = "stewardship_operational_log"
        indexes = [
            models.Index(fields=["level", "created_at"], name="operational_level_time")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    level__in=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
                ),
                name="operational_log_level",
            )
        ]
