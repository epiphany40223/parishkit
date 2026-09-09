"""Intake durability, immutable audit/checkpoints, actor keys, and real races."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_requests import (
    cancel_request,
    record_request,
    request_status,
)
from parishkit.stewardship.accounts.configuration_snapshots import prepare_snapshot
from parishkit.stewardship.accounts.request_models import (
    ConfigurationChangeRequest,
    ConfigurationRequestCheckpoint,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from ..configuration_factory import configuration_version, successor_document
from ..test_request_patch import parish_patch

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def intake():
    """Each test gets one prepared base and fresh actor/key, never live authority."""
    version = configuration_version()
    prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())
    return {
        "base_digest": version.digest,
        "patch": parish_patch(version, name="Updated"),
        "actor_id": uuid4(),
        "request_key": uuid4(),
        "correlation_id": uuid4(),
    }


def test_retry_cancel_and_status_preserve_intent_and_attribution(intake):
    """Receipts are never Applied, cancellation is durable and retries emit no audit."""
    first = record_request(**intake)
    assert (first.state, first.sequence) == ("staged", 1)
    assert not hasattr(first, "applied_digest")
    retry = record_request(**{**intake, "correlation_id": uuid4()})
    assert retry == first
    assert (
        ConfigurationChangeRequest.objects.get().correlation_id
        == intake["correlation_id"]
    )
    event = AuditEvent.objects.get(event_type="config_request_staged")
    assert event.subject_id == first.request_id
    assert event.actor_id == intake["actor_id"]
    assert event.correlation_id == intake["correlation_id"]
    cancelled = cancel_request(
        request_id=first.request_id,
        actor_id=intake["actor_id"],
        expected_sequence=1,
        correlation_id=uuid4(),
    )
    assert (cancelled.state, cancelled.sequence) == ("cancelled", 2)
    assert (
        cancel_request(
            request_id=first.request_id,
            actor_id=intake["actor_id"],
            expected_sequence=1,
            correlation_id=uuid4(),
        )
        == cancelled
    )
    assert record_request(**intake) == cancelled
    connections.close_all()
    assert (
        request_status(request_id=first.request_id, actor_id=intake["actor_id"])
        == cancelled
    )
    assert ConfigurationRequestCheckpoint.objects.count() == 2
    assert AuditEvent.objects.filter(subject_id=first.request_id).count() == 2


def test_key_conflict_and_actor_scoping(intake):
    """A key is unique within its actor and never authorizes another actor's lookup."""
    first = record_request(**intake)
    patch = [dict(intake["patch"][0], values={"name": "Different"})]
    with pytest.raises(ConfigError, match="bound"):
        record_request(**{**intake, "patch": patch})
    other = record_request(**{**intake, "actor_id": uuid4()})
    assert other.request_id != first.request_id
    for identifier in (first.request_id, uuid4()):
        with pytest.raises(LookupError, match="unavailable"):
            request_status(request_id=identifier, actor_id=uuid4())
        with pytest.raises(LookupError, match="unavailable"):
            cancel_request(
                request_id=identifier,
                actor_id=uuid4(),
                expected_sequence=1,
                correlation_id=uuid4(),
            )


def test_changed_base_rejects_same_key(intake):
    """Even identical desired values are a different intent against another base."""
    from parishkit.stewardship.accounts.authority import parse_version
    from parishkit.stewardship.accounts.configuration_models import (
        AppliedConfigurationVersion,
    )
    from parishkit.stewardship.accounts.configuration_schema import validate_sections

    record_request(**intake)
    base = parse_version(
        AppliedConfigurationVersion.objects.get().canonical_document,
        validate_sections=validate_sections,
    )
    successor = configuration_version(successor_document(base))
    prepare_snapshot(successor, actor_id=uuid4(), correlation_id=uuid4())
    with pytest.raises(ConfigError, match="bound"):
        record_request(**{**intake, "base_digest": successor.digest})


def test_validation_and_incomplete_base_leave_no_intent(intake):
    """Invalid inputs fail before request, checkpoint, or audit persistence."""
    with pytest.raises(ConfigError):
        record_request(**{**intake, "base_digest": "f" * 64})
    patch = [dict(intake["patch"][0], values={"secret": "private"})]
    with pytest.raises(ConfigError):
        record_request(**{**intake, "patch": patch})
    assert not ConfigurationChangeRequest.objects.exists()
    assert not ConfigurationRequestCheckpoint.objects.exists()
    assert not AuditEvent.objects.exists()


@pytest.mark.parametrize("operation", ["stage", "cancel"])
def test_receipts_cannot_be_rolled_back_by_outer_caller(intake, operation):
    """Both mutating entry points reject outer transactions and disabled autocommit."""
    first = record_request(**intake)

    def attempt():
        """Invoke the selected primitive without allowing test-wrapper savepoints."""
        if operation == "stage":
            return record_request(**intake)
        return cancel_request(
            request_id=first.request_id,
            actor_id=intake["actor_id"],
            expected_sequence=1,
            correlation_id=uuid4(),
        )

    with transaction.atomic(), pytest.raises(StorageInvariantError):
        attempt()
    connection.set_autocommit(False)
    try:
        with pytest.raises(StorageInvariantError):
            attempt()
    finally:
        connection.rollback()
        connection.set_autocommit(True)


def test_atomic_audit_failure_rolls_back_intake(intake):
    """A dependent audit INSERT failure cannot leave an unaudited accepted intent."""
    # A temporary CHECK affects only this disposable test and rejects the audit
    # inside the database trigger (not merely a mocked Python audit call).
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_audit_event "
            "ADD CONSTRAINT test_no_audit CHECK (false) NOT VALID"
        )
    try:
        with pytest.raises(IntegrityError, match="test_no_audit"):
            record_request(**intake)
        assert not ConfigurationChangeRequest.objects.exists()
        assert not ConfigurationRequestCheckpoint.objects.exists()
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_audit_event DROP CONSTRAINT test_no_audit"
            )


@pytest.mark.parametrize(
    "column,value",
    [("state", "applied"), ("sequence", 3), ("state", "staged"), ("actor_id", None)],
)
def test_checkpoint_edges_are_enforced_by_postgresql(intake, column, value):
    """Raw inserts cannot skip installer work, repeat staged, or lose attribution."""
    first = record_request(**intake)
    fields = {
        "request_id": first.request_id,
        "state": "cancelled",
        "sequence": 2,
        "actor_id": intake["actor_id"],
        "correlation_id": uuid4(),
        column: value,
    }
    with pytest.raises(IntegrityError), transaction.atomic():
        ConfigurationRequestCheckpoint.objects.create(**fields)
    assert ConfigurationRequestCheckpoint.objects.count() == 1


def test_terminal_checkpoint_and_history_are_immutable(intake):
    """Cancelled state is terminal; raw SQL cannot rewrite intent/history."""
    first = record_request(**intake)
    cancel_request(
        request_id=first.request_id,
        actor_id=intake["actor_id"],
        expected_sequence=1,
        correlation_id=uuid4(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ConfigurationRequestCheckpoint.objects.create(
            request_id=first.request_id,
            state="cancelled",
            sequence=3,
            actor_id=intake["actor_id"],
            correlation_id=uuid4(),
        )
    for table in ("stewardship_config_request", "stewardship_config_checkpoint"):
        for sql in (f"UPDATE {table} SET actor_id = actor_id", f"DELETE FROM {table}"):
            with (
                pytest.raises(IntegrityError, match="append-only"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(sql)


def test_stale_sequence_and_invalid_identifier_denial(intake):
    """Cancellation never silently applies to an unexpected checkpoint revision."""
    first = record_request(**intake)
    with pytest.raises(StaleRecordError):
        cancel_request(
            request_id=first.request_id,
            actor_id=intake["actor_id"],
            expected_sequence=99,
            correlation_id=uuid4(),
        )
    with pytest.raises(TypeError):
        cancel_request(
            request_id=first.request_id,
            actor_id=intake["actor_id"],
            expected_sequence=True,
            correlation_id=uuid4(),
        )
    with pytest.raises(TypeError):
        record_request(**{**intake, "actor_id": None})


def test_concurrent_same_key_and_cancel_are_exactly_once(intake):
    """Independent connections converge on one intent and one terminal checkpoint."""
    barrier = Barrier(2)

    def submit():
        """Race the same immutable key through independent database connections."""
        try:
            barrier.wait(timeout=10)
            return record_request(**intake)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert results[0] == results[1]
    first = results[0]

    def cancel():
        """A cancellation retry cannot create a duplicate transition or audit."""
        try:
            barrier.wait(timeout=10)
            return cancel_request(
                request_id=first.request_id,
                actor_id=intake["actor_id"],
                expected_sequence=1,
                correlation_id=uuid4(),
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancelled = list(pool.map(lambda _: cancel(), range(2)))
    assert cancelled[0] == cancelled[1]
    assert ConfigurationChangeRequest.objects.count() == 1
    assert ConfigurationRequestCheckpoint.objects.count() == 2
    assert AuditEvent.objects.count() == 2


def test_retry_uses_stored_schema_after_current_builder_changes(intake, monkeypatch):
    """Deploying a stricter future builder cannot reinterpret a retained intent."""
    from parishkit.stewardship.accounts import configuration_requests, request_patch

    first = record_request(**intake)

    def reject_new(*args, **kwargs):
        """Represent future input requirements without altering the old builder."""
        raise ConfigError("Synthetic new-schema rejection")

    monkeypatch.setattr(configuration_requests, "REQUEST_SCHEMA", "future-v2")
    monkeypatch.setattr(
        request_patch, "BUILDERS", {**request_patch.BUILDERS, "future-v2": reject_new}
    )
    assert record_request(**intake) == first
    with pytest.raises(ConfigError, match="new-schema"):
        record_request(**{**intake, "request_key": uuid4()})


@pytest.mark.parametrize("digest", [None, {}, "", "private", "A" * 64])
def test_invalid_base_digest_is_rejected_before_query(intake, digest):
    """Malformed base identifiers cannot be coerced into unbounded query values."""
    with pytest.raises(ConfigError, match="valid base"):
        record_request(**{**intake, "base_digest": digest})
    assert not ConfigurationChangeRequest.objects.exists()


def test_request_constraints_reject_forged_direct_inserts(intake):
    """Actor/key uniqueness, presence, schema, and digest shapes survive raw writes."""
    from django.forms.models import model_to_dict

    record_request(**intake)
    request = ConfigurationChangeRequest.objects.get()
    values = model_to_dict(request, exclude=["id", "base"])
    # Editable=False attribution fields are intentionally excluded by model_to_dict.
    values.update(
        base=request.base,
        actor_id=request.actor_id,
        correlation_id=request.correlation_id,
    )
    for changed in (
        {},
        {"actor_id": None},
        {"payload_fingerprint": "invalid"},
        {"candidate_digest": "invalid"},
        {"request_schema": "unknown"},
    ):
        candidate = {
            **values,
            "candidate_version_id": uuid4(),
            "candidate_digest": uuid4().hex * 2,
            **changed,
        }
        if changed:
            candidate["request_key"] = uuid4()
        with pytest.raises(IntegrityError), transaction.atomic():
            ConfigurationChangeRequest.objects.create(**candidate)
    assert ConfigurationChangeRequest.objects.count() == 1
    assert ConfigurationRequestCheckpoint.objects.count() == 1
    assert AuditEvent.objects.count() == 1
