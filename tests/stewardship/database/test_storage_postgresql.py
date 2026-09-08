"""PostgreSQL constraints, sessions, rollback, and concurrent writers."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.apps import apps
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core import serializers
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import (
    DatabaseError,
    IntegrityError,
    connection,
    connections,
    transaction,
)
from django.db.migrations.executor import MigrationExecutor
from django.db.models import F
from django.db.models.deletion import ProtectedError
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.observability import correlation, current_correlation
from parishkit.stewardship.storage import (
    MutableRecord,
    StaleRecordError,
    StorageInvariantError,
    mutate_record,
)


@pytest.fixture
def portal_session(db):
    """Each test owns independent session rows with only synthetic identities."""
    store = SessionStore()
    store["principal_id"] = str(uuid4())
    store.save()
    instant = datetime(2026, 9, 8, 12, tzinfo=UTC)
    return PortalSession.objects.create(
        session_id=store.session_key,
        principal_id=uuid4(),
        authenticated_at=instant,
        last_activity_at=instant,
        expires_at=instant + timedelta(hours=1),
    )


def test_durable_sessions_and_safe_audit_references(portal_session):
    """New DB connections retain session data; audits keep no credential key."""
    identifier = portal_session.pk
    event = AuditEvent.objects.create(
        event_type="portal_session_created", subject_id=identifier
    )
    assert isinstance(identifier, UUID)
    assert SessionStore(session_key=portal_session.session_id).load()["principal_id"]
    assert event.created_at.utcoffset() == timedelta(0)
    assert event.correlation_id
    with pytest.raises(ProtectedError):
        Session.objects.get(pk=portal_session.session_id).delete()
    portal_session.delete()
    Session.objects.get(pk=portal_session.session_id).delete()
    event.refresh_from_db()
    assert event.subject_id == identifier
    assert {field.name for field in event._meta.fields} == {
        "id",
        "created_at",
        "actor_id",
        "correlation_id",
        "event_type",
        "subject_id",
    }


def test_timezone_roundtrip_and_naive_bulk_denial(portal_session):
    """SQL persists the instant, and the custom field also guards queryset writes."""
    offset = timezone(timedelta(hours=-4))
    aware = datetime(2026, 9, 8, 8, 15, tzinfo=offset)
    PortalSession.objects.filter(pk=portal_session.pk).update(
        last_activity_at=aware, version=F("version") + 1
    )
    portal_session.refresh_from_db()
    assert portal_session.last_activity_at == datetime(2026, 9, 8, 12, 15, tzinfo=UTC)
    with pytest.raises(ValidationError):
        PortalSession.objects.filter(pk=portal_session.pk).update(
            last_activity_at=aware.replace(tzinfo=None)
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 0),
        ("expires_at", datetime(2026, 9, 8, 12, tzinfo=UTC)),
        ("last_activity_at", datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
def test_session_constraints_cannot_be_bypassed_by_update(portal_session, field, value):
    """Database checks apply even when model validation is skipped."""
    with pytest.raises(IntegrityError), transaction.atomic():
        PortalSession.objects.filter(pk=portal_session.pk).update(
            **{"version": F("version") + 1, field: value}
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("last_activity_at", datetime(2026, 9, 8, 13, tzinfo=UTC)),
        ("revoked_at", datetime(2026, 9, 8, 11, tzinfo=UTC)),
    ],
)
def test_remaining_chronology_checks_at_database(portal_session, field, value):
    """Activity cannot reach expiry and revocation cannot precede login."""
    with pytest.raises(IntegrityError), transaction.atomic():
        PortalSession.objects.filter(pk=portal_session.pk).update(
            **{field: value, "version": F("version") + 1}
        )


def test_queryset_update_requires_version_advance(portal_session):
    """Ordinary updates cannot leave an optimistic token silently valid."""
    with pytest.raises(IntegrityError, match="advance"), transaction.atomic():
        PortalSession.objects.filter(pk=portal_session.pk).update(
            last_activity_at=portal_session.last_activity_at + timedelta(minutes=1)
        )
    previous = portal_session.updated_at
    PortalSession.objects.filter(pk=portal_session.pk).update(version=F("version") + 1)
    portal_session.refresh_from_db()
    assert portal_session.updated_at > previous
    with pytest.raises(StaleRecordError):
        mutate_record(
            PortalSession,
            portal_session.pk,
            expected_version=1,
            actor_id=None,
            correlation_id=uuid4(),
            change=lambda record: None,
        )


@pytest.mark.parametrize(
    "column,value", [("id", uuid4()), ("created_at", datetime(2020, 1, 1, tzinfo=UTC))]
)
def test_mutable_identity_guard_at_database(portal_session, column, value):
    """Identity preservation applies to raw SQL as well as the service."""
    with pytest.raises(IntegrityError, match="immutable"), transaction.atomic():
        PortalSession.objects.filter(pk=portal_session.pk).update(
            **{column: value, "version": F("version") + 1}
        )


def test_mutation_does_not_prequery_database_checks(portal_session):
    """Lock only long enough for field/FK validation and the guarded UPDATE."""
    with CaptureQueriesContext(connection) as captured:
        result = mutate_record(
            PortalSession,
            portal_session.pk,
            expected_version=1,
            actor_id=None,
            correlation_id=uuid4(),
            change=lambda record: None,
        )
    # Locked read, ForeignKey field validation, then refreshed server write time.
    assert len([query for query in captured if query["sql"].startswith("SELECT")]) == 3
    portal_session.refresh_from_db()
    assert result.updated_at == portal_session.updated_at


def test_django_deserialization_accepts_aware_timestamps(portal_session):
    """The field supports real Django JSON serialization, not only hand parsing."""
    payload = serializers.serialize("json", [portal_session])
    restored = next(serializers.deserialize("json", payload)).object
    assert restored.authenticated_at == portal_session.authenticated_at


def test_standard_session_sweep_requires_ordered_metadata_cleanup(portal_session):
    """Make ARC-04's cleanup prerequisite executable before login is enabled."""
    Session.objects.filter(pk=portal_session.session_id).update(
        expire_date=datetime(2000, 1, 1, tzinfo=UTC)
    )
    with pytest.raises(ProtectedError):
        call_command("clearsessions")
    assert Session.objects.filter(pk=portal_session.session_id).exists()
    portal_session.delete()
    call_command("clearsessions")
    assert not Session.objects.filter(pk=portal_session.session_id).exists()


def test_mutation_rollback_and_stale_version(portal_session):
    """An exception restores dependent writes as well as the mutable record."""

    def reject(record):
        """Simulate a domain check failing after a dependent audit insert."""
        AuditEvent.objects.create(event_type="rolled_back", subject_id=record.pk)
        raise ValidationError("Rejected")

    with pytest.raises(ValidationError, match="Rejected"):
        mutate_record(
            PortalSession,
            portal_session.pk,
            expected_version=1,
            actor_id=None,
            correlation_id=uuid4(),
            change=reject,
        )
    assert AuditEvent.objects.count() == 0
    actor, correlation = uuid4(), uuid4()
    changed = mutate_record(
        PortalSession,
        portal_session.pk,
        expected_version=1,
        actor_id=actor,
        correlation_id=correlation,
        change=lambda record: None,
    )
    assert changed.version == 2
    assert changed.actor_id == actor
    assert changed.correlation_id == correlation
    with pytest.raises(StaleRecordError):
        mutate_record(
            PortalSession,
            portal_session.pk,
            expected_version=1,
            actor_id=actor,
            correlation_id=correlation,
            change=lambda record: None,
        )


@pytest.mark.parametrize(
    "field,value", [("id", uuid4()), ("created_at", datetime(2020, 1, 1, tzinfo=UTC))]
)
def test_mutation_preserves_identity_and_creation(portal_session, field, value):
    """Callbacks cannot swap the row being locked or rewrite creation history."""
    with pytest.raises(StorageInvariantError, match="immutable"):
        mutate_record(
            PortalSession,
            portal_session.pk,
            expected_version=1,
            actor_id=None,
            correlation_id=uuid4(),
            change=lambda record: setattr(record, field, value),
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE stewardship_audit_event SET event_type = 'changed' WHERE id = %s",
        "DELETE FROM stewardship_audit_event WHERE id = %s",
    ],
)
def test_audit_database_trigger_blocks_raw_sql(db, statement):
    """Using a cursor cannot bypass append-only ORM guards."""
    event = AuditEvent.objects.create(event_type="audit_created")
    with (
        pytest.raises(DatabaseError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement, [event.pk])
    event.refresh_from_db()
    assert event.event_type == "audit_created"


def test_explicit_existing_uuid_never_overwrites_audit(db):
    """Django's insert-or-update save behavior cannot replace historical rows."""
    event = AuditEvent.objects.create(event_type="original")
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEvent(id=event.pk, event_type="replacement").save()
    event.refresh_from_db()
    assert event.event_type == "original"


def test_duplicate_session_metadata_is_rejected(portal_session):
    """There is only one attribution record per Django session."""
    with pytest.raises(IntegrityError), transaction.atomic():
        PortalSession.objects.create(
            session_id=portal_session.session_id,
            principal_id=uuid4(),
            authenticated_at=portal_session.authenticated_at,
            last_activity_at=portal_session.last_activity_at,
            expires_at=portal_session.expires_at,
        )


@pytest.mark.parametrize(
    "event_type", ["", "Private User", "https://private.invalid/", "bad\n"]
)
def test_audit_identifier_constraint(db, event_type):
    """The event discriminator is an identifier, not a free-form log message."""
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEvent.objects.create(event_type=event_type)


@pytest.mark.django_db(transaction=True)
def test_new_connection_retains_session_and_audit(portal_session):
    """Closing every connection models a process losing all local ORM state."""
    identifier, key = portal_session.pk, portal_session.session_id
    event_id = AuditEvent.objects.create(
        event_type="session_persisted", subject_id=identifier
    ).pk
    connection.close()
    assert PortalSession.objects.get(pk=identifier).session_id == key
    assert SessionStore(session_key=key).load()["principal_id"]
    assert AuditEvent.objects.get(pk=event_id).subject_id == identifier


@pytest.mark.django_db(transaction=True)
def test_migrations_reverse_and_reapply():
    """Empty disposable tables reverse cleanly and reapply the audit guard."""
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("stewardship_accounts", None), ("stewardship_audit", None)])
        tables = connection.introspection.table_names()
        assert "stewardship_portal_session" not in tables
        assert "stewardship_audit_event" not in tables
    finally:
        MigrationExecutor(connection).migrate(leaves)
    event = AuditEvent.objects.create(event_type="after_migration")
    with (
        pytest.raises(DatabaseError, match="append-only"),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_audit_event WHERE id = %s", [event.pk])


@pytest.mark.django_db(transaction=True)
def test_concurrent_mutations_have_one_winner(portal_session):
    """Independent PostgreSQL connections serialize the row and detect staleness."""
    barrier = Barrier(2, timeout=10)
    identifier = portal_session.pk

    def write():
        """Give each thread its own connection and always close it afterward."""
        try:
            barrier.wait()
            mutate_record(
                PortalSession,
                identifier,
                expected_version=1,
                actor_id=None,
                correlation_id=uuid4(),
                change=lambda record: None,
            )
            return "written"
        except StaleRecordError:
            return "stale"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda unused: write(), range(2)))
    assert sorted(results) == ["stale", "written"]
    portal_session.refresh_from_db()
    assert portal_session.version == 2


def test_all_concrete_mutable_records_have_enabled_guard(db):
    """New model subclasses cannot silently omit the migration-side contract."""
    models = [model for model in apps.get_models() if issubclass(model, MutableRecord)]
    assert models
    with connection.cursor() as cursor:
        for model in models:
            table = model._meta.db_table
            cursor.execute(
                "SELECT p.proname, t.tgtype FROM pg_trigger t "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE t.tgrelid = %s::regclass AND t.tgname = %s "
                "AND t.tgenabled = 'O' AND NOT t.tgisinternal",
                [table, f"{table}_mutable_guard_v1"],
            )
            # BEFORE UPDATE FOR EACH ROW; statement triggers do not protect rows.
            assert cursor.fetchone() == (f"{table}_mutable_v1", 19), table


def test_insert_and_update_use_database_clock(portal_session, monkeypatch):
    """Application clock skew cannot stamp the default creation/write instants."""
    key = portal_session.session_id
    portal_session.delete()
    monkeypatch.setattr(
        "django.utils.timezone.now", lambda: datetime(2100, 1, 1, tzinfo=UTC)
    )
    record = PortalSession.objects.create(
        session_id=key,
        principal_id=uuid4(),
        authenticated_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_activity_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    event = AuditEvent.objects.create(event_type="server_clock")
    assert record.created_at == record.updated_at
    assert record.created_at.year < 2100
    assert event.created_at.year < 2100
    changed = mutate_record(
        PortalSession,
        record.pk,
        expected_version=1,
        actor_id=None,
        correlation_id=uuid4(),
        change=lambda record: None,
    )
    assert record.created_at <= changed.updated_at < datetime(2100, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("field", ["principal_id", "session_id", "authenticated_at"])
def test_session_bindings_are_immutable_in_service_and_sql(portal_session, field):
    """The same attribution UUID cannot be rebound to a new login identity."""
    value = {
        "principal_id": uuid4(),
        "session_id": "synthetic-new-session",
        "authenticated_at": portal_session.authenticated_at - timedelta(minutes=1),
    }[field]
    with pytest.raises(StorageInvariantError):
        mutate_record(
            PortalSession,
            portal_session.pk,
            expected_version=1,
            actor_id=None,
            correlation_id=uuid4(),
            change=lambda record: setattr(record, field, value),
        )
    with pytest.raises(IntegrityError, match="immutable"), transaction.atomic():
        PortalSession.objects.filter(pk=portal_session.pk).update(
            **{field: value, "version": F("version") + 1}
        )


@pytest.mark.parametrize("fail", [False, True])
def test_mutation_callback_inherits_and_restores_correlation(portal_session, fail):
    """Dependent audit events share the operation; nested context never leaks."""
    operation = uuid4()

    def callback(record):
        """Create dependent history using the operation's ordinary default."""
        event = AuditEvent.objects.create(event_type="dependent", subject_id=record.pk)
        assert event.correlation_id == operation
        if fail:
            raise ValidationError("synthetic rollback")

    with correlation() as outer:
        if fail:
            with pytest.raises(ValidationError):
                mutate_record(
                    PortalSession,
                    portal_session.pk,
                    expected_version=1,
                    actor_id=None,
                    correlation_id=operation,
                    change=callback,
                )
        else:
            result = mutate_record(
                PortalSession,
                portal_session.pk,
                expected_version=1,
                actor_id=None,
                correlation_id=operation,
                change=callback,
            )
            assert result.correlation_id == operation
        assert current_correlation() == outer
    assert AuditEvent.objects.filter(event_type="dependent").count() == int(not fail)


@pytest.mark.parametrize("insert", [True, False])
def test_activity_after_revocation_is_rejected(portal_session, insert):
    """The chronology contract holds for both initial rows and later updates."""
    values = {
        "last_activity_at": portal_session.authenticated_at + timedelta(minutes=2),
        "revoked_at": portal_session.authenticated_at + timedelta(minutes=1),
    }
    if insert:
        key = portal_session.session_id
        portal_session.delete()
        with pytest.raises(IntegrityError), transaction.atomic():
            PortalSession.objects.create(
                session_id=key,
                principal_id=uuid4(),
                authenticated_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
                expires_at=datetime(2026, 9, 8, 13, tzinfo=UTC),
                **values,
            )
    else:
        with pytest.raises(IntegrityError), transaction.atomic():
            PortalSession.objects.filter(pk=portal_session.pk).update(
                **values,
                version=F("version") + 1,
            )
