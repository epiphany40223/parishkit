"""Pure validation of durable storage conventions; SQL behavior has PG tests."""

from datetime import UTC, date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.storage import (
    StaleRecordError,
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


@pytest.mark.parametrize("version", [None, True, 0, -1, "1"])
def test_mutation_rejects_invalid_expected_version_before_database(version):
    """Malformed concurrency tokens cannot become unguarded writes."""
    with pytest.raises(StaleRecordError):
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
    with pytest.raises(ValidationError, match="updated"):
        row.save()
    with pytest.raises(ValidationError, match="deleted"):
        row.delete()
    with pytest.raises(ValidationError, match="updated"):
        AuditEvent.objects.all().update(event_type="changed")
    with pytest.raises(ValidationError, match="deleted"):
        AuditEvent.objects.all().delete()
