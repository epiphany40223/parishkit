"""Durable record conventions shared by the Stewardship Django applications.

Historical actor/object identifiers are deliberately soft UUID references:
deleting a portal session or, eventually, purging a campaign must not cascade
into parish-owned audit history. Business relationships use explicit PROTECT
foreign keys in their owning models, not generic cascading relationships here.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.functions import Now
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .observability import correlation, current_correlation


class StaleRecordError(Exception):
    """An expected version no longer describes the locked durable record."""


class StorageInvariantError(RuntimeError):
    """A programming error attempted a structurally forbidden history mutation."""


class UTCDateTimeField(models.DateTimeField):
    """Reject naive writes instead of Django's implicit default-zone conversion."""

    def to_python(self, value):
        """Accept aware instants/ISO timestamps without inferring missing zones."""
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = parse_datetime(value)
            except ValueError:
                value = None
        if not isinstance(value, datetime) or timezone.is_naive(value):
            raise ValidationError("A timezone-aware instant is required.")
        return value.astimezone(UTC)

    def get_prep_value(self, value):
        """Apply the same guard to bulk writes and queryset updates."""
        return self.to_python(value)


class DurableRecord(models.Model):
    """Opaque identity, UTC creation time, and non-secret historical attribution."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    created_at = UTCDateTimeField(db_default=Now(), editable=False)
    actor_id = models.UUIDField(null=True, blank=True, editable=False)
    correlation_id = models.UUIDField(
        default=current_correlation, editable=False, db_index=True
    )

    class Meta:
        abstract = True


class MutableRecord(DurableRecord):
    """Versioned records changed through lock-and-compare service operations.

    The mutation helper deliberately does not authorize callers. Owning services
    must recheck authorization and workflow admission inside its transaction.
    """

    immutable_fields = ("id", "created_at")
    write_once_fields = ()
    updated_at = UTCDateTimeField(db_default=Now(), editable=False)
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
        raise StorageInvariantError("Historical records cannot be updated.")

    def delete(self):
        """Require an explicitly designed retention service, never generic delete."""
        raise StorageInvariantError("Historical records cannot be deleted.")


class ImmutableRecord(DurableRecord):
    """Append-only ORM contract, complemented by concrete migration triggers."""

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """INSERT only, including for an explicitly supplied existing UUID."""
        if not self._state.adding or kwargs.get("force_update"):
            raise StorageInvariantError("Historical records cannot be updated.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Historical row deletion is not an ordinary model operation."""
        raise StorageInvariantError("Historical records cannot be deleted.")


def validate_mutation(record):
    """Validate service values while preserving nullable storage fields.

    Django's form-oriented blank check is stricter than null=True. Only actual
    None values in nullable columns are excluded; model clean() still runs and
    SQL remains authoritative for unique and cross-field constraints.
    """
    optional = {
        field.name
        for field in record._meta.concrete_fields
        if field.null and getattr(record, field.attname) is None
    }
    record.full_clean(
        exclude=optional, validate_unique=False, validate_constraints=False
    )


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
        raise ValueError("A positive expected record version is required.")
    if not isinstance(correlation_id, UUID) or (
        actor_id is not None and not isinstance(actor_id, UUID)
    ):
        raise TypeError("Actor and correlation identifiers must be UUIDs.")
    if not issubclass(model, MutableRecord):
        raise TypeError("Mutation requires a mutable durable record.")
    with correlation(correlation_id), transaction.atomic():
        record = model.objects.select_for_update().get(pk=identifier)
        if record.version != expected_version:
            raise StaleRecordError("The record changed; reload before retrying.")
        frozen = {name: getattr(record, name) for name in record.immutable_fields}
        frozen.update(
            (name, getattr(record, name))
            for name in record.write_once_fields
            if getattr(record, name) is not None
        )
        change(record)
        if any(getattr(record, name) != value for name, value in frozen.items()):
            raise StorageInvariantError("Record identity and bindings are immutable.")
        record.version = expected_version + 1
        record.actor_id = actor_id
        record.correlation_id = correlation_id
        # Keep field/domain validation, but let SQL enforce uniqueness and CHECKs
        # without issuing one validation SELECT for every database constraint.
        validate_mutation(record)
        record.save()
        # Concrete mutable tables install a trigger that owns the write instant.
        record.refresh_from_db(fields=["updated_at"])
        return record
