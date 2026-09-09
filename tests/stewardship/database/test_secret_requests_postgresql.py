"""Synthetic secret staging: real constraints, cleanup crashes, history and races."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.db.models import F
from django.utils import timezone

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.secret_models import (
    SECRET_TARGETS,
    SecretReplacementRequest,
    SecretRequestCheckpoint,
)
from parishkit.stewardship.accounts.secret_requests import (
    cancel_secret_request,
    clean_secret_request,
    expire_secret_request,
    secret_request_status,
    stage_secret_request,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import SECRET_NAMES
from parishkit.stewardship.storage import StorageInvariantError

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def intent():
    """References and fingerprints are synthetic; no secret bytes exist."""
    return dict(
        request_id=uuid4(),
        target="parishsoft",
        staging_reference=uuid4(),
        actor_id=uuid4(),
        reauthenticated_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint="a" * 64,
        correlation_id=uuid4(),
    )


def cancel(intent):
    """Request cancellation through the actor-scoped storage entry point."""
    return cancel_secret_request(
        request_id=intent["request_id"],
        actor_id=intent["actor_id"],
        correlation_id=uuid4(),
    )


def clean(intent, callback):
    """Exercise the future target-store port with an explicit synthetic callback."""
    return clean_secret_request(
        request_id=intent["request_id"],
        target=intent["target"],
        correlation_id=uuid4(),
        remove_payload=callback,
    )


def test_roundtrip_cancel_cleanup_and_idempotent_history(intent):
    """Cleanup is durable, exactly-once in DB, and precedes target reuse."""
    staged = stage_secret_request(**intent)
    assert (staged.state, staged.version) == ("staged", 1)
    assert set(asdict(staged)) == {"request_id", "state", "version", "cleanup_reason"}
    assert staged.cleanup_reason == ""
    assert stage_secret_request(**{**intent, "correlation_id": uuid4()}) == staged
    pending = cancel(intent)
    assert (pending.state, pending.version) == ("cleanup_pending", 2)
    assert cancel(intent) == pending
    assert stage_secret_request(**intent) == pending
    deleted = []
    terminal = clean(intent, deleted.append)
    assert (terminal.state, terminal.version) == ("cancelled", 3)
    assert deleted == [intent["staging_reference"]]
    assert clean(intent, deleted.append) == terminal
    assert cancel(intent) == terminal
    assert len(deleted) == 1
    connections.close_all()
    assert (
        secret_request_status(request_id=staged.request_id, actor_id=intent["actor_id"])
        == terminal
    )
    assert stage_secret_request(**intent) == terminal
    record = SecretReplacementRequest.objects.get()
    assert record.scrubbed_at >= record.created_at
    assert list(
        record.checkpoints.order_by("sequence").values_list("state", flat=True)
    ) == ["staged", "cleanup_pending", "cancelled"]
    assert list(
        AuditEvent.objects.order_by("created_at").values_list("event_type", flat=True)
    ) == [
        "secret_request_staged",
        "secret_request_cleanup_pending",
        "secret_request_cancelled",
    ]
    successor = {**intent, "request_id": uuid4(), "staging_reference": uuid4()}
    assert stage_secret_request(**successor).state == "staged"


@pytest.mark.parametrize("state", ["staged", "cleanup_pending"])
def test_target_reservation_and_reference_reuse(intent, state):
    """A unique partial index protects both staged and failed-cleanup requests."""
    stage_secret_request(**intent)
    if state == "cleanup_pending":
        cancel(intent)
    with pytest.raises(ConfigError, match="pending"):
        stage_secret_request(
            **{**intent, "request_id": uuid4(), "staging_reference": uuid4()}
        )
    with pytest.raises(ConfigError, match="reference is already bound"):
        stage_secret_request(**{**intent, "request_id": uuid4(), "target": "slack"})
    with pytest.raises(IntegrityError), transaction.atomic():
        row = SecretReplacementRequest.objects.get()
        row.pk, row.staging_reference = uuid4(), uuid4()
        row.version, row.state, row.cleanup_reason = 1, "staged", ""
        row.save(force_insert=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "slack"),
        ("staging_reference", uuid4()),
        ("actor_id", uuid4()),
        ("expected_fingerprint", "b" * 64),
        ("expires_at", timezone.now() + timedelta(days=1)),
    ],
)
def test_identity_reuse_with_changed_intent_rejected(intent, field, value):
    """An opaque retry ID never authorizes a changed target, actor or payload."""
    stage_secret_request(**intent)
    with pytest.raises(ConfigError, match="already bound"):
        stage_secret_request(**{**intent, field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "private-credential"),
        ("expected_fingerprint", "private-credential"),
        ("staging_reference", "private-credential"),
        ("actor_id", None),
        ("expires_at", None),
        ("reauthenticated_at", "private-credential"),
        ("expires_at", timezone.now() - timedelta(days=1)),
        ("reauthenticated_at", timezone.now() + timedelta(days=1)),
    ],
)
def test_invalid_intake_is_atomic_and_sanitized(intent, field, value):
    """Typed metadata validation never persists or echoes submitted private text."""
    with pytest.raises((ConfigError, TypeError, ValidationError)) as error:
        stage_secret_request(**{**intent, field: value})
    assert "private-credential" not in str(error.value)
    assert not SecretReplacementRequest.objects.exists()
    assert not SecretRequestCheckpoint.objects.exists()
    assert not AuditEvent.objects.exists()


def test_scope_and_early_cleanup_denial(intent):
    """Neither another actor nor another target can act on this request."""
    stage_secret_request(**intent)
    for identifier in (intent["request_id"], uuid4()):
        with pytest.raises(LookupError, match="unavailable"):
            secret_request_status(request_id=identifier, actor_id=uuid4())
        with pytest.raises(LookupError, match="unavailable"):
            cancel_secret_request(
                request_id=identifier, actor_id=uuid4(), correlation_id=uuid4()
            )
    calls = []
    with pytest.raises(LookupError, match="unavailable"):
        clean({**intent, "target": "slack"}, calls.append)
    with pytest.raises(ConfigError, match="not entered cleanup"):
        clean(intent, calls.append)
    with pytest.raises(ConfigError, match="not expired"):
        expire_secret_request(
            request_id=intent["request_id"],
            target=intent["target"],
            correlation_id=uuid4(),
        )
    assert not calls


def test_callback_failure_keeps_target_reserved_and_retryable(intent):
    """External deletion is outside transactions; uncertainty cannot mean cleaned."""
    stage_secret_request(**intent)
    cancel(intent)

    def fail(reference):
        """Simulate unavailable target storage without credential-bearing output."""
        assert reference == intent["staging_reference"]
        assert not connection.in_atomic_block
        raise OSError("Synthetic unavailable staging")

    with pytest.raises(OSError):
        clean(intent, fail)
    assert SecretReplacementRequest.objects.get().state == "cleanup_pending"
    assert SecretRequestCheckpoint.objects.count() == 2
    assert clean(intent, lambda reference: None).state == "cancelled"


def test_post_delete_database_failure_retries_idempotent_removal(intent):
    """Lost terminal commit cannot release reservation or duplicate audit."""
    stage_secret_request(**intent)
    cancel(intent)
    attempts = []
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE FUNCTION secret_test_audit_failure() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$"
        )
        cursor.execute(
            "CREATE TRIGGER secret_test_audit_failure BEFORE INSERT ON "
            "stewardship_audit_event FOR EACH ROW "
            "EXECUTE FUNCTION secret_test_audit_failure()"
        )
    try:
        with pytest.raises(Exception, match="synthetic"):
            clean(intent, attempts.append)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "DROP TRIGGER secret_test_audit_failure ON stewardship_audit_event"
            )
            cursor.execute("DROP FUNCTION secret_test_audit_failure()")
    assert SecretReplacementRequest.objects.get().state == "cleanup_pending"
    assert SecretRequestCheckpoint.objects.count() == 2
    assert clean(intent, attempts.append).state == "cancelled"
    assert attempts == [intent["staging_reference"]] * 2
    assert AuditEvent.objects.count() == 3


def seed_expired(intent):
    """Seed an aged fixture without sleeps, restoring the guard transactionally."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_secret_request "
            "DISABLE TRIGGER stewardship_secret_state_v1"
        )
        now = timezone.now()
        SecretReplacementRequest.objects.create(
            id=intent["request_id"],
            actor_id=intent["actor_id"],
            requested_by_id=intent["actor_id"],
            target=intent["target"],
            staging_reference=intent["staging_reference"],
            reauthenticated_at=now - timedelta(minutes=3),
            created_at=now - timedelta(minutes=2),
            updated_at=now - timedelta(minutes=2),
            expires_at=now - timedelta(minutes=1),
        )
        cursor.execute(
            "ALTER TABLE stewardship_secret_request "
            "ENABLE TRIGGER stewardship_secret_state_v1"
        )


def test_expiry_cleanup_uses_database_deadline(intent):
    """Expiry and acknowledgement use system attribution and the DB deadline."""
    seed_expired(intent)
    kwargs = dict(
        request_id=intent["request_id"], target=intent["target"], correlation_id=uuid4()
    )
    pending = expire_secret_request(**kwargs)
    assert expire_secret_request(**kwargs) == pending
    assert pending.state == "cleanup_pending"
    assert clean(intent, lambda reference: None).state == "expired"
    assert expire_secret_request(**kwargs).state == "expired"
    assert AuditEvent.objects.get(event_type="secret_request_expired").actor_id is None


@pytest.mark.parametrize("operation", ["stage", "cancel", "expire", "clean"])
def test_mutations_reject_outer_transactions_and_manual_autocommit(intent, operation):
    """No successful receipt or external deletion can escape a rolled-back caller."""
    stage_secret_request(**intent)
    actions = dict(
        stage=lambda: stage_secret_request(**intent),
        cancel=lambda: cancel(intent),
        expire=lambda: expire_secret_request(
            request_id=intent["request_id"],
            target=intent["target"],
            correlation_id=uuid4(),
        ),
        clean=lambda: clean(intent, lambda reference: pytest.fail("must not delete")),
    )
    with transaction.atomic(), pytest.raises(StorageInvariantError):
        actions[operation]()
    connection.set_autocommit(False)
    try:
        with pytest.raises(StorageInvariantError):
            actions[operation]()
    finally:
        connection.rollback()
        connection.set_autocommit(True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "slack"),
        ("staging_reference", uuid4()),
        ("requested_by_id", uuid4()),
        ("expected_fingerprint", "b" * 64),
        ("state", "applied"),
        ("state", "cancelled"),
        ("version", 99),
    ],
)
def test_sql_cannot_rewrite_bindings_or_skip_transitions(intent, field, value):
    """Even version-advancing raw updates must satisfy frozen state and identity."""
    stage_secret_request(**intent)
    values = {"version": F("version") + 1, field: value}
    with pytest.raises(IntegrityError), transaction.atomic():
        SecretReplacementRequest.objects.filter(pk=intent["request_id"]).update(
            **values
        )
    assert SecretRequestCheckpoint.objects.count() == 1


def test_sql_denies_early_expiry_deletion_and_forged_checkpoints(intent):
    """The database complements services with deadline, history and binding guards."""
    stage_secret_request(**intent)
    row = SecretReplacementRequest.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        SecretReplacementRequest.objects.filter(pk=row.pk).update(
            version=2, state="cleanup_pending", cleanup_reason="expired"
        )
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_secret_request WHERE id = %s", [row.pk])
    with pytest.raises(IntegrityError), transaction.atomic():
        SecretRequestCheckpoint.objects.create(request=row, sequence=2, state="staged")
    for sql in (
        "DELETE FROM stewardship_secret_checkpoint",
        "UPDATE stewardship_secret_checkpoint SET state = 'expired'",
    ):
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(sql)


@pytest.mark.parametrize("same_id", [True, False])
def test_concurrent_intake_has_one_reservation(intent, same_id):
    """Independent connections serialize retry identity and absent-target races."""
    barrier = Barrier(2)

    def worker(index):
        """Each worker owns its connection and returns only a safe outcome."""
        connections.close_all()
        try:
            candidate = dict(intent)
            if index and not same_id:
                candidate.update(request_id=uuid4(), staging_reference=uuid4())
            barrier.wait(timeout=10)
            try:
                return stage_secret_request(**candidate).request_id
            except ConfigError:
                return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    assert results.count(None) == (0 if same_id else 1)
    assert SecretReplacementRequest.objects.count() == 1
    assert SecretRequestCheckpoint.objects.count() == AuditEvent.objects.count() == 1


def test_concurrent_cleanup_records_one_terminal_transition(intent):
    """Callbacks may repeat, but only one cleanup acknowledgement commits."""
    stage_secret_request(**intent)
    cancel(intent)
    barrier = Barrier(2)

    def worker(index):
        """Hold both callbacks after admission to force the terminal race."""
        connections.close_all()
        try:
            return clean(intent, lambda reference: barrier.wait(timeout=10))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    assert results[0] == results[1]
    assert SecretRequestCheckpoint.objects.count() == AuditEvent.objects.count() == 3


def test_target_vocabulary_matches_reserved_deployment_services():
    """An explicit storage migration is needed if deployment grows new targets."""
    assert set(SECRET_TARGETS) == SECRET_NAMES - {"handoff_private"}


def test_populated_downgrade_preserves_guards_and_history(intent):
    """Refuse downgrade before any guard removal when request history exists."""
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder

    stage_secret_request(**intent)
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        with pytest.raises(IntegrityError, match="prevents downgrade"):
            MigrationExecutor(connection).migrate(
                [("stewardship_accounts", "0014_secret_request_records")]
            )
        assert MigrationRecorder.Migration.objects.filter(
            app="stewardship_accounts", name="0015_secret_request_guards"
        ).exists()
        assert (
            SecretRequestCheckpoint.objects.count() == AuditEvent.objects.count() == 1
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            SecretReplacementRequest.objects.update(state="expired", version=2)
    finally:
        MigrationExecutor(connection).migrate(leaves)


@pytest.mark.parametrize("transition", ["expiry", "cancelled", "expired"])
def test_sql_denies_fabricated_human_cleanup_attribution(intent, transition):
    """Expiry and both terminal outcomes cannot forge immutable human audit."""
    if transition == "cancelled":
        stage_secret_request(**intent)
        cancel(intent)
    else:
        seed_expired(intent)
        if transition == "expired":
            expire_secret_request(
                request_id=intent["request_id"],
                target=intent["target"],
                correlation_id=uuid4(),
            )
    before = SecretRequestCheckpoint.objects.count()
    state = "cleanup_pending" if transition == "expiry" else transition
    reason = "expired" if transition == "expiry" else transition
    with (
        pytest.raises(IntegrityError, match="system attribution"),
        transaction.atomic(),
    ):
        SecretReplacementRequest.objects.update(
            version=F("version") + 1,
            state=state,
            cleanup_reason=reason,
            actor_id=uuid4(),
        )
    assert (
        SecretRequestCheckpoint.objects.count() == AuditEvent.objects.count() == before
    )


@pytest.mark.parametrize("first_reason", ["cancelled", "expired"])
def test_first_cleanup_reason_survives_opposite_request_and_terminal_retry(
    intent, first_reason
):
    """A losing caller sees the winning reason without changing durable attribution."""
    seed_expired(intent)

    def expire():
        """Both reasons are admissible for this aged synthetic request."""
        return expire_secret_request(
            request_id=intent["request_id"],
            target=intent["target"],
            correlation_id=uuid4(),
        )

    actions = {"cancelled": lambda: cancel(intent), "expired": expire}
    second_reason = "expired" if first_reason == "cancelled" else "cancelled"
    winning = actions[first_reason]()
    assert actions[second_reason]() == winning
    assert winning.cleanup_reason == first_reason
    record = SecretReplacementRequest.objects.get()
    assert record.cleanup_reason == first_reason
    expected_actor = intent["actor_id"] if first_reason == "cancelled" else None
    assert record.actor_id == expected_actor
    assert (
        AuditEvent.objects.get(event_type="secret_request_cleanup_pending").actor_id
        == expected_actor
    )
    terminal = clean(intent, lambda reference: None)
    assert terminal.state == terminal.cleanup_reason == first_reason
    assert actions[second_reason]() == terminal
    assert (
        secret_request_status(
            request_id=intent["request_id"], actor_id=intent["actor_id"]
        )
        == terminal
    )
    assert (
        AuditEvent.objects.get(event_type="secret_request_" + first_reason).actor_id
        is None
    )
    assert SecretRequestCheckpoint.objects.count() == AuditEvent.objects.count() == 3


def test_concurrent_cancel_and_expiry_preserve_winning_reason(intent):
    """Independent writers observe one cleanup reason and one attributed event."""
    seed_expired(intent)
    barrier = Barrier(2)

    def worker(expiry):
        """Race the two admissible transitions through independent connections."""
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            if expiry:
                return expire_secret_request(
                    request_id=intent["request_id"],
                    target=intent["target"],
                    correlation_id=uuid4(),
                )
            return cancel(intent)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(worker, [False, True]))
    assert outcomes[0] == outcomes[1]
    winner = outcomes[0].cleanup_reason
    assert winner in {"cancelled", "expired"}
    assert AuditEvent.objects.get(
        event_type="secret_request_cleanup_pending"
    ).actor_id == (intent["actor_id"] if winner == "cancelled" else None)
    assert clean(intent, lambda reference: None).state == winner
    assert SecretRequestCheckpoint.objects.count() == AuditEvent.objects.count() == 3
