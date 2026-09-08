"""Durable record conventions shared by the Stewardship Django applications.

Historical actor/object identifiers are deliberately soft UUID references:
deleting a portal session or, eventually, purging a campaign must not cascade
into parish-owned audit history. Business relationships use explicit PROTECT
foreign keys in their owning models, not generic cascading relationships here.
"""

from datetime import UTC, datetime
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


class StaleRecordError(Exception):
    """An expected version no longer describes the locked durable record."""


class UTCDateTimeField(models.DateTimeField):
    """Reject naive writes instead of Django's implicit default-zone conversion."""

    def to_python(self, value):
        """Require typed aware instants even during model validation."""
        if value is None:
            return None
        if not isinstance(value, datetime) or timezone.is_naive(value):
            raise ValidationError("A timezone-aware instant is required.")
        return value.astimezone(UTC)

    def get_prep_value(self, value):
        """Apply the same guard to bulk writes and queryset updates."""
        return self.to_python(value)


class DurableRecord(models.Model):
    """Opaque identity, UTC creation time, and non-secret historical attribution."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    created_at = UTCDateTimeField(default=timezone.now, editable=False)
    actor_id = models.UUIDField(null=True, blank=True, editable=False)
    correlation_id = models.UUIDField(default=uuid4, editable=False, db_index=True)

    class Meta:
        abstract = True


class MutableRecord(DurableRecord):
    """Versioned records changed through lock-and-compare service operations.

    The mutation helper deliberately does not authorize callers. Owning services
    must recheck authorization and workflow admission inside its transaction.
    """

    updated_at = UTCDateTimeField(default=timezone.now, editable=False)
    version = models.PositiveBigIntegerField(default=1, editable=False)

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="%(app_label)s_%(class)s_positive_version",
            )
        ]


class ImmutableQuerySet(models.QuerySet):
    """Catch accidental ORM rewrites; concrete tables also need DB protection."""

    def update(self, **kwargs):
        """Reject bulk rewrites, including bulk_update's underlying UPDATE."""
        raise ValidationError("Historical records cannot be updated.")

    def delete(self):
        """Require an explicitly designed retention service, never generic delete."""
        raise ValidationError("Historical records cannot be deleted.")


class ImmutableRecord(DurableRecord):
    """Append-only ORM contract, complemented by concrete migration triggers."""

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """INSERT only, including for an explicitly supplied existing UUID."""
        if not self._state.adding or kwargs.get("force_update"):
            raise ValidationError("Historical records cannot be updated.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Historical row deletion is not an ordinary model operation."""
        raise ValidationError("Historical records cannot be deleted.")


def mutate_record(
    model, identifier, *, expected_version, actor_id, correlation_id, change
):
    """Apply one authorized callback under a row lock and optimistic version.

    ``change`` must validate the owning workflow's current authorization/admission
    before setting editable fields; it may create dependent rows in this same
    transaction. Exceptions roll back both the mutation and dependent writes.
    Database/queue/provider side effects must not escape this transaction.
    """
    if type(expected_version) is not int or expected_version < 1:
        raise StaleRecordError("A positive expected record version is required.")
    if not issubclass(model, MutableRecord):
        raise TypeError("Mutation requires a mutable durable record.")
    with transaction.atomic():
        record = model.objects.select_for_update().get(pk=identifier)
        if record.version != expected_version:
            raise StaleRecordError("The record changed; reload before retrying.")
        identity, created_at = record.pk, record.created_at
        change(record)
        if record.pk != identity or record.created_at != created_at:
            raise ValidationError("Record identity and creation time are immutable.")
        record.version = expected_version + 1
        record.updated_at = timezone.now()
        record.actor_id = actor_id
        record.correlation_id = correlation_id
        record.full_clean()
        record.save()
        return record
