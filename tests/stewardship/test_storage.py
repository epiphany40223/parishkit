"""Pure validation of durable storage conventions; SQL behavior has PG tests."""

from datetime import UTC, date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.observability import correlation, current_correlation
from parishkit.stewardship.storage import (
    StorageInvariantError,
    UTCDateTimeField,
    mutate_record,
)


@pytest.mark.parametrize(
    "value", [datetime(2026, 1, 1), date(2026, 1, 1), "2026-01-01", 1]
)
def test_instants_reject_implicit_timezone(value):
    """No host/default timezone may be inferred, even for a typed date."""
    field = UTCDateTimeField()
    for convert in (field.to_python, field.get_prep_value):
        with pytest.raises(ValidationError, match="timezone-aware"):
            convert(value)


def test_instants_normalize_aware_values_and_preserve_nullable_fields():
    """A timestamp's represented instant survives a non-UTC input offset."""
    field = UTCDateTimeField()
    assert field.get_prep_value(None) is None
    assert field.get_prep_value(
        datetime(2026, 1, 1, 1, tzinfo=timezone(timedelta(hours=1)))
    ) == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("value", ["2026-09-08T12:00:00Z", "2026-09-08T08:00:00-04:00"])
def test_aware_iso_strings_are_supported(value):
    """Django deserialization retains explicit offsets without guessing a zone."""
    assert UTCDateTimeField().to_python(value) == datetime(2026, 9, 8, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "value", ["2026-09-08T12:00:00", "2026-99-99T12:00:00Z", "private"]
)
def test_invalid_and_naive_iso_values_are_rejected_without_echo(value):
    """Rejected strings do not leak into validation diagnostics."""
    with pytest.raises(ValidationError) as error:
        UTCDateTimeField().to_python(value)
    assert value not in str(error.value)


def test_default_correlation_reuses_the_current_operation():
    """Implicit durable IDs match logs inside a scope, with safe unscoped IDs."""
    assert current_correlation() != current_correlation()
    with correlation() as operation:
        assert AuditEvent(event_type="test").correlation_id == operation
        assert PortalSession().correlation_id == operation


@pytest.mark.parametrize(
    "actor,correlation_id", [("private", uuid4()), (None, None), (None, "private")]
)
def test_bad_attribution_rejected_before_database_or_callback(actor, correlation_id):
    """Type validation happens before executing any domain callback."""
    with pytest.raises(TypeError, match="identifiers must be UUIDs"):
        mutate_record(
            PortalSession,
            uuid4(),
            expected_version=1,
            actor_id=actor,
            correlation_id=correlation_id,
            change=lambda record: None,
        )


@pytest.mark.parametrize("version", [None, True, 0, -1, "1"])
def test_mutation_rejects_invalid_expected_version_before_database(version):
    """Malformed concurrency tokens cannot become unguarded writes."""
    with pytest.raises(ValueError):
        mutate_record(
            AuditEvent,
            uuid4(),
            expected_version=version,
            actor_id=None,
            correlation_id=uuid4(),
            change=lambda record: None,
        )


def test_mutation_rejects_immutable_models():
    """The convenience mutation service is not an append-only bypass."""
    with pytest.raises(TypeError, match="mutable durable"):
        mutate_record(
            AuditEvent,
            uuid4(),
            expected_version=1,
            actor_id=None,
            correlation_id=uuid4(),
            change=lambda record: None,
        )


def test_immutable_orm_guards_without_database_access():
    """Accidental ordinary and bulk mutations fail before generating SQL."""
    row = AuditEvent(event_type="test_event")
    row._state.adding = False
    with pytest.raises(StorageInvariantError, match="updated"):
        row.save()
    with pytest.raises(StorageInvariantError, match="deleted"):
        row.delete()
    with pytest.raises(StorageInvariantError, match="updated"):
        AuditEvent.objects.all().update(event_type="changed")
    with pytest.raises(StorageInvariantError, match="deleted"):
        AuditEvent.objects.all().delete()


def test_storage_invariants_are_not_form_validation_errors():
    """Programming errors must not be mistaken for user-correctable input."""
    assert not issubclass(StorageInvariantError, ValidationError)
