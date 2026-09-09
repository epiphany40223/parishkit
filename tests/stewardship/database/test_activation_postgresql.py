"""Real PostgreSQL/file activation, interruption recovery, and exclusion."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import F

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import configuration_installation as installer
from parishkit.stewardship.accounts import configuration_requests as requests
from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.accounts.configuration_requests import (
    cancel_request,
    record_request,
    request_status,
)
from parishkit.stewardship.accounts.configuration_schema import (
    SchemaEnvironmentError,
    validate_sections,
)
from parishkit.stewardship.accounts.installation_lock import (
    ConfigurationBusy,
    installation_lock,
)
from parishkit.stewardship.accounts.request_models import (
    ConfigurationChangeRequest,
    ConfigurationRequestCheckpoint,
)
from parishkit.stewardship.accounts.runtime_models import (
    ConfigurationActivation,
    SystemConfiguration,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.storage import StorageInvariantError

from ..configuration_factory import configuration_version, successor_document
from ..test_request_patch import parish_patch

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def initialized(tmp_path):
    """Use synthetic configuration and disposable files, never mounted parish data."""
    store, root = AuthorityStore(tmp_path, validate_sections), configuration_version()
    actor = uuid4()
    installer.prepare_initial_configuration(
        store,
        root,
        testing_recipient="testing@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    return store, root, actor


def stage(root, actor, name="New parish name"):
    """Construct a fresh actor-scoped durable request against the chosen root."""
    return record_request(
        base_digest=root.digest,
        patch=parish_patch(root, name=name),
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )


def install(store, receipt):
    """Give each installer delivery fresh correlation but retain request identity."""
    return installer.install_request(
        store, request_id=receipt.request_id, correlation_id=uuid4()
    )


def test_initialization_is_testing_durable_and_idempotent(initialized):
    """Root initialization commits one immutable activation, never Production."""
    store, root, actor = initialized
    runtime = installer.coherent_configuration(store)
    assert runtime.mode == "testing"
    assert runtime.version == 2
    assert not runtime.restore_review_required
    assert runtime.testing_recipient == "testing@example.org"
    assert runtime.active_configuration_id == root.version_id
    activation = ConfigurationActivation.objects.get()
    assert activation.configuration_id == root.version_id
    assert activation.request_id is None
    installer.prepare_initial_configuration(
        store,
        root,
        testing_recipient="testing@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    connections.close_all()
    assert installer.coherent_configuration(store).version == 2
    assert ConfigurationActivation.objects.count() == AuditEvent.objects.count() == 1
    assert "testing@example.org" not in root.yaml_text()


@pytest.mark.parametrize(
    "recipient",
    [
        None,
        "",
        "private invalid",
        "a\r\nb@example.org",
        "a" * 255,
        "admin@localhost",
        '"a b"@example.org',
    ],
)
def test_invalid_recipient_has_no_runtime_side_effects(tmp_path, recipient):
    """Validation excludes raw input from errors and commits nothing on failure."""
    with pytest.raises(ConfigError, match="Testing recipient") as error:
        installer.prepare_initial_configuration(
            AuthorityStore(tmp_path, validate_sections),
            configuration_version(),
            testing_recipient=recipient,
            actor_id=uuid4(),
            correlation_id=uuid4(),
        )
    assert "private" not in str(error.value)
    assert not SystemConfiguration.objects.exists()
    assert not list(tmp_path.iterdir())


def test_changed_initialization_cannot_overwrite_runtime(initialized):
    """Neither another recipient nor a requestless successor is a bootstrap retry."""
    store, root, actor = initialized
    for version, recipient in (
        (root, "other@example.org"),
        (configuration_version(), "testing@example.org"),
        (configuration_version(successor_document(root)), "testing@example.org"),
    ):
        with pytest.raises(ConfigError):
            installer.prepare_initial_configuration(
                store,
                version,
                testing_recipient=recipient,
                actor_id=actor,
                correlation_id=uuid4(),
            )
    assert (
        installer.coherent_configuration(store).active_configuration_id
        == root.version_id
    )


@pytest.mark.parametrize("boundary", ["write_version", "select"])
def test_initial_interruption_does_not_freeze_runtime_recipient(
    tmp_path, monkeypatch, boundary
):
    """Runtime creation belongs to activation, not a prematurely committed setup row."""
    store, root = AuthorityStore(tmp_path, validate_sections), configuration_version()
    original = getattr(store, boundary)

    def interrupted(*args):
        """Leave the actual file effect durable before losing its acknowledgement."""
        original(*args)
        raise OSError("synthetic interruption")

    with monkeypatch.context() as patch:
        patch.setattr(store, boundary, interrupted)
        with pytest.raises(OSError):
            installer.prepare_initial_configuration(
                store,
                root,
                testing_recipient="mistyped@example.org",
                actor_id=uuid4(),
                correlation_id=uuid4(),
            )
    assert not SystemConfiguration.objects.exists()
    connections.close_all()
    installer.prepare_initial_configuration(
        store,
        root,
        testing_recipient="correct@example.org",
        actor_id=uuid4(),
        correlation_id=uuid4(),
    )
    assert (
        installer.coherent_configuration(store).testing_recipient
        == "correct@example.org"
    )


def test_prebootstrap_materializer_contract_and_request_remain_resumable(tmp_path):
    """Prepared-only storage must not cause an ORM error or terminal request failure."""
    store, root, actor = (
        AuthorityStore(tmp_path, validate_sections),
        configuration_version(),
        uuid4(),
    )
    installer.prepare_snapshot(root, actor_id=actor, correlation_id=uuid4())
    receipt = stage(root, actor)
    materializer = installer.DatabaseMaterializer(
        store, actor_id=actor, correlation_id=uuid4()
    )
    with materializer.lock():
        assert materializer.active_digest() is None
    with pytest.raises(ConfigError, match="not initialized"):
        install(store, receipt)
    assert (
        request_status(request_id=receipt.request_id, actor_id=actor).state == "staged"
    )
    installer.prepare_initial_configuration(
        store,
        root,
        testing_recipient="testing@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    assert install(store, receipt).state == "applied"


def test_corrupt_active_base_is_resumable_after_repair(initialized):
    """A damaged projection is not invalid intent; privileged repair enables retry."""
    store, root, actor = initialized
    receipt = stage(root, actor)
    # Simulate storage corruption using the disposable migration-owner role;
    # never provide this guard bypass through a runtime/operational API. DDL and
    # mutation roll back together if any statement fails; cleanup cannot leak.
    with transaction.atomic(durable=True), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_parish DISABLE TRIGGER "
            "stewardship_parish_immutable_guard_v1"
        )
        cursor.execute(
            "UPDATE stewardship_parish SET name = %s WHERE configuration_id = %s",
            ["corrupt", root.version_id],
        )
        cursor.execute(
            "ALTER TABLE stewardship_parish ENABLE TRIGGER "
            "stewardship_parish_immutable_guard_v1"
        )
    try:
        with pytest.raises(ConfigError, match="complete prepared"):
            install(store, receipt)
        assert (
            request_status(request_id=receipt.request_id, actor_id=actor).state
            == "staged"
        )
    finally:
        with transaction.atomic(durable=True), connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_parish DISABLE TRIGGER "
                "stewardship_parish_immutable_guard_v1"
            )
            cursor.execute(
                "UPDATE stewardship_parish SET name = %s WHERE configuration_id = %s",
                [
                    root.document()["sections"]["parish"][0]["values"]["name"],
                    root.version_id,
                ],
            )
            cursor.execute(
                "ALTER TABLE stewardship_parish ENABLE TRIGGER "
                "stewardship_parish_immutable_guard_v1"
            )
    assert install(store, receipt).state == "applied"


def test_unknown_installer_request_has_uniform_lookup_error(tmp_path):
    """An unknown UUID has the same safe missing-request contract as status lookup."""
    with pytest.raises(LookupError, match="Configuration request is unavailable"):
        installer.install_request(
            AuthorityStore(tmp_path, validate_sections),
            request_id=uuid4(),
            correlation_id=uuid4(),
        )


@pytest.mark.parametrize(
    "state,code",
    [("failed", ""), ("failed", "private-value"), ("validating", "invalid_candidate")],
)
def test_failure_code_check_rejects_direct_inserts(initialized, state, code):
    """Assert the named CHECK, not a coincidental transition-trigger rejection."""
    _, root, actor = initialized
    receipt = stage(root, actor)
    sequence = 2
    if state == "failed":
        ConfigurationRequestCheckpoint.objects.create(
            request_id=receipt.request_id,
            sequence=2,
            state="validating",
            actor_id=actor,
        )
        sequence = 3
    with (
        pytest.raises(IntegrityError, match="config_checkpoint_failure_code"),
        transaction.atomic(),
    ):
        ConfigurationRequestCheckpoint.objects.create(
            request_id=receipt.request_id,
            sequence=sequence,
            state=state,
            failure_code=code,
            actor_id=actor,
        )


def test_installer_state_check_is_independent_of_transition_trigger(initialized):
    """Controlled DDL isolation proves the CHECK itself; rollback restores the guard."""
    _, root, actor = initialized
    receipt = stage(root, actor)
    with (
        pytest.raises(IntegrityError, match="config_checkpoint_installer_states"),
        transaction.atomic(),
    ):
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_config_checkpoint DISABLE TRIGGER "
                "stewardship_request_checkpoint_v2"
            )
        ConfigurationRequestCheckpoint.objects.create(
            request_id=receipt.request_id,
            sequence=2,
            state="unknown",
            actor_id=actor,
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tgenabled FROM pg_trigger WHERE tgname = %s",
            ["stewardship_request_checkpoint_v2"],
        )
        assert cursor.fetchone()[0] == "O"


@pytest.mark.parametrize(
    "error",
    [
        ConfigError("synthetic domain"),
        OSError("synthetic file"),
        IntegrityError("synthetic database"),
    ],
)
def test_unlock_failure_preserves_original_body_error(error):
    """Failure classification survives simultaneous workflow and cleanup errors."""
    with pytest.raises(type(error)) as caught, installation_lock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [736212, 1])
        raise error
    assert caught.value is error
    assert connection.connection is None
    with installation_lock() as guard:
        guard.check()


def test_state_only_receipt_is_lazy_and_does_not_verify_projections(
    initialized, monkeypatch
):
    """Historical state remains cheap; explicit detail access alone validates values."""
    store, root, actor = initialized
    receipt = install(store, stage(root, actor))
    probe = Mock(side_effect=ConfigError("synthetic projection damage"))
    monkeypatch.setattr(requests, "intake_base", probe)
    status = request_status(request_id=receipt.request_id, actor_id=actor)
    assert status == receipt
    assert install(store, receipt) == receipt
    probe.assert_not_called()
    with pytest.raises(ConfigError, match="projection damage"):
        status.affected_values()
    probe.assert_called_once_with(receipt.applied_digest)


@pytest.mark.parametrize("changed", [False, True])
def test_coherence_reads_document_once_and_detects_manifest_change(
    initialized, monkeypatch, changed
):
    """The second atomic reference read detects changes without parsing YAML again."""
    store, root, _ = initialized
    read = Mock(wraps=store.read_version)
    reference = (root.version_id, root.digest)
    monkeypatch.setattr(store, "read_version", read)
    monkeypatch.setattr(
        store,
        "manifest_reference",
        Mock(side_effect=[reference, (uuid4(), "a" * 64) if changed else reference]),
    )
    if changed:
        with pytest.raises(ConfigError, match="recovery"):
            installer.coherent_configuration(store)
    else:
        assert (
            installer.coherent_configuration(store).active_configuration_id
            == root.version_id
        )
    read.assert_called_once_with(root.version_id)


def test_failed_corruption_probe_rolls_back_guard_ddl(initialized):
    """A failing synthetic UPDATE cannot leave immutability disabled for later tests."""
    _, root, _ = initialized
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(durable=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "ALTER TABLE stewardship_parish DISABLE TRIGGER "
            "stewardship_parish_immutable_guard_v1"
        )
        cursor.execute(
            "UPDATE stewardship_parish SET name = NULL WHERE configuration_id = %s",
            [root.version_id],
        )
        cursor.execute(
            "ALTER TABLE stewardship_parish ENABLE TRIGGER "
            "stewardship_parish_immutable_guard_v1"
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tgenabled FROM pg_trigger WHERE tgname = %s",
            ["stewardship_parish_immutable_guard_v1"],
        )
        assert cursor.fetchone()[0] == "O"


@pytest.mark.parametrize("activated_request", [False, True])
def test_downgrade_with_history_preserves_guards_and_migration_marker(
    initialized, activated_request
):
    """Reject before removing protections, including bootstrap-only history."""
    store, root, actor = initialized
    if activated_request:
        install(store, stage(root, actor))
    before = SystemConfiguration.objects.get().active_configuration_id
    with pytest.raises(IntegrityError, match="prevents this schema downgrade"):
        MigrationExecutor(connection).migrate(
            [
                ("stewardship_accounts", "0010_request_intake_guards"),
            ]
        )
    assert MigrationRecorder.Migration.objects.filter(
        app="stewardship_accounts", name="0013_activation_guards"
    ).exists()
    assert SystemConfiguration.objects.get().active_configuration_id == before
    with pytest.raises(IntegrityError), transaction.atomic():
        SystemConfiguration.objects.update(version=F("version") + 1)


def test_request_activation_status_and_historical_retry(initialized):
    """Only committed Applied returns values; repeat delivery adds no events."""
    store, root, actor = initialized
    receipt = stage(root, actor)
    assert receipt.applied_digest is receipt.affected_values() is None
    applied = install(store, receipt)
    assert applied.state == "applied"
    assert applied.sequence == 5
    assert applied.applied_digest == receipt.candidate_digest
    assert applied.applied_version_id == receipt.candidate_version_id
    values = applied.affected_values()
    assert values[0]["values"]["name"] == "New parish name"
    values[0]["values"]["name"] = "cannot mutate receipt"
    assert applied.affected_values()[0]["values"]["name"] == "New parish name"
    activation = ConfigurationActivation.objects.get(request_id=receipt.request_id)
    event = AuditEvent.objects.get(subject_id=activation.pk)
    assert event.actor_id == actor
    assert event.correlation_id == activation.correlation_id
    assert list(
        ConfigurationRequestCheckpoint.objects.filter(request_id=receipt.request_id)
        .order_by("sequence")
        .values_list("state", flat=True)
    ) == [
        "staged",
        "validating",
        "prepared",
        "yaml_activated",
        "applied",
    ]
    count = AuditEvent.objects.count()
    assert install(store, receipt) == applied
    connections.close_all()
    assert request_status(request_id=receipt.request_id, actor_id=actor) == applied
    assert AuditEvent.objects.count() == count
    successor = stage(store.active(), actor, name="Later name")
    install(store, successor)
    assert install(store, receipt) == applied  # historical, not current readiness


def test_stale_base_fails_without_writing_candidate_file(initialized):
    """Competing intents never silently rebase or overwrite the winning authority."""
    store, root, actor = initialized
    first, stale = stage(root, actor), stage(root, actor, "Stale name")
    install(store, first)
    failed = install(store, stale)
    assert (failed.state, failed.failure_code) == ("failed", "stale_base")
    assert failed.applied_digest is None
    assert not (store.root / f"{stale.candidate_version_id}.yaml").exists()
    assert install(store, stale) == failed
    assert store.active().digest == first.candidate_digest


def test_cancelled_request_never_installs(initialized):
    """Cancellation remains terminal even when delivery was already queued."""
    store, root, actor = initialized
    receipt = stage(root, actor)
    cancelled = cancel_request(
        request_id=receipt.request_id,
        actor_id=actor,
        expected_sequence=1,
        correlation_id=uuid4(),
    )
    assert install(store, receipt) == cancelled
    assert store.active() == root


@pytest.mark.parametrize(
    "boundary",
    ["file", "snapshot", "prepared", "selected", "yaml_checkpoint", "activated"],
)
def test_each_durable_boundary_recovers_exactly_once(
    initialized, monkeypatch, boundary
):
    """An interrupted delivery may leave mismatch, but never reports partial Applied."""
    store, root, actor = initialized
    receipt = stage(root, actor)

    def crash_after(original):
        """Raise after a real durable effect, simulating lost acknowledgement."""

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic interruption")

        return interrupted

    with monkeypatch.context() as patch:
        if boundary == "file":
            patch.setattr(store, "write_version", crash_after(store.write_version))
        elif boundary == "snapshot":
            patch.setattr(
                installer, "prepare_snapshot", crash_after(installer.prepare_snapshot)
            )
        elif boundary == "selected":
            patch.setattr(store, "select", crash_after(store.select))
        elif boundary == "activated":
            patch.setattr(
                installer.DatabaseMaterializer,
                "activate",
                crash_after(installer.DatabaseMaterializer.activate),
            )
        else:
            original = installer.DatabaseMaterializer.checkpoint
            target = "prepared" if boundary == "prepared" else "yaml_activated"

            def interrupted(self, state, **kwargs):
                """Interrupt just the requested append-only checkpoint."""
                original(self, state, **kwargs)
                if state == target:
                    raise OSError("synthetic interruption")

            patch.setattr(installer.DatabaseMaterializer, "checkpoint", interrupted)
        with pytest.raises(OSError, match="synthetic"):
            install(store, receipt)
    before = request_status(request_id=receipt.request_id, actor_id=actor)
    if boundary in {"selected", "yaml_checkpoint"}:
        with pytest.raises(ConfigError, match="recovery"):
            installer.coherent_configuration(store)
        assert before.applied_digest is None
    connections.close_all()
    applied = install(store, receipt)
    assert applied.state == "applied"
    assert (
        installer.coherent_configuration(store).active_configuration_id
        == receipt.candidate_version_id
    )
    assert (
        ConfigurationActivation.objects.filter(request_id=receipt.request_id).count()
        == 1
    )
    assert AuditEvent.objects.filter(subject_id=receipt.request_id).count() == 5


def test_activation_audit_failure_rolls_back_pointer_and_applied(initialized):
    """The SQL trigger's event failure rolls back every activation effect together."""
    store, root, actor = initialized
    receipt = stage(root, actor)
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_audit_event ADD CONSTRAINT no_activation_audit "
            "CHECK (event_type <> 'configuration_activated') NOT VALID"
        )
    try:
        with pytest.raises(IntegrityError, match="no_activation_audit"):
            install(store, receipt)
        assert (
            SystemConfiguration.objects.get().active_configuration_id == root.version_id
        )
        assert (
            request_status(request_id=receipt.request_id, actor_id=actor).state
            == "yaml_activated"
        )
        assert not ConfigurationActivation.objects.filter(
            request_id=receipt.request_id
        ).exists()
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_audit_event "
                "DROP CONSTRAINT no_activation_audit"
            )
    assert install(store, receipt).state == "applied"


def test_unrelated_request_cannot_recover_selected_candidate(initialized, monkeypatch):
    """A mismatch pins recovery to its own request; no competing selection can occur."""
    store, root, actor = initialized
    first, other = stage(root, actor), stage(root, actor, "Other")
    with monkeypatch.context() as patch:
        patch.setattr(
            installer.DatabaseMaterializer,
            "activate",
            lambda *args: (_ for _ in ()).throw(OSError()),
        )
        with pytest.raises(OSError):
            install(store, first)
    with pytest.raises(ConfigError, match="Another"):
        install(store, other)
    assert store.active().digest == first.candidate_digest
    cancelled = cancel_request(
        request_id=other.request_id,
        actor_id=actor,
        expected_sequence=1,
        correlation_id=uuid4(),
    )
    assert install(store, first).state == "applied"
    assert install(store, other) == cancelled


def test_schema_environment_outage_is_retryable_not_invalid_candidate(
    initialized, monkeypatch
):
    """Installation problems are not recorded as bad user input or leaked in audit."""
    store, root, actor = initialized
    receipt = stage(root, actor)

    def unavailable(*args):
        """Simulate missing packaged schema data without altering retained records."""
        raise SchemaEnvironmentError("synthetic unavailable catalog")

    with monkeypatch.context() as patch:
        patch.setattr(installer, "intake_base", unavailable)
        with pytest.raises(SchemaEnvironmentError):
            install(store, receipt)
    assert (
        request_status(request_id=receipt.request_id, actor_id=actor).state == "staged"
    )
    assert install(store, receipt).state == "applied"


def test_invalid_retained_candidate_fails_without_private_error(initialized):
    """Even a direct malformed request INSERT cannot bypass reconstruction checks."""
    store, root, actor = initialized
    request = ConfigurationChangeRequest.objects.create(
        actor_id=actor,
        request_key=uuid4(),
        request_schema="parish-integrations-patch-v1",
        base_id=root.version_id,
        patch=parish_patch(root, name="private input"),
        payload_fingerprint="b" * 64,
        candidate_version_id=uuid4(),
        candidate_digest="c" * 64,
    )
    failed = installer.install_request(
        store, request_id=request.pk, correlation_id=uuid4()
    )
    assert failed.failure_code == "invalid_candidate"
    assert "private input" not in repr(failed)
    assert store.active() == root


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "production"},
        {"testing_recipient": "replacement@example.org"},
        {"restore_review_required": True},
        {"active_configuration_id": None},
        {},
    ],
)
def test_runtime_cannot_be_mutated_without_owning_workflow(initialized, changes):
    """Version advancement alone never authorizes runtime/pointer changes."""
    with pytest.raises(IntegrityError), transaction.atomic():
        SystemConfiguration.objects.update(version=F("version") + 1, **changes)


def test_runtime_singleton_and_delete_guards(initialized):
    """Raw-style ORM operations cannot duplicate/remove initialized runtime."""
    with pytest.raises(IntegrityError), transaction.atomic():
        SystemConfiguration.objects.create(testing_recipient="testing@example.org")
    with pytest.raises(IntegrityError), transaction.atomic():
        SystemConfiguration.objects.all().delete()


@pytest.mark.parametrize("atomic", [False, True])
def test_standalone_runtime_cannot_commit_without_activation(atomic):
    """Even direct SQL-style insertion cannot freeze an unactivated recipient."""
    with pytest.raises(IntegrityError, match="atomic root activation"):
        if atomic:
            with transaction.atomic(durable=True):
                SystemConfiguration.objects.create(testing_recipient="test@example.org")
                assert SystemConfiguration.objects.exists()
        else:
            SystemConfiguration.objects.create(testing_recipient="test@example.org")
    assert not SystemConfiguration.objects.exists()


@pytest.mark.parametrize("replace_wrapper", [False, True])
def test_failed_unlock_discards_only_owning_connection(replace_wrapper):
    """Unlock failure permits retry and never closes an already-replaced connection."""
    replacement = None
    with (
        pytest.raises(StorageInvariantError, match="lost"),
        installation_lock() as guard,
    ):
        raw = guard.raw
        with raw.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [736212, 1])
        if replace_wrapper:
            connection.connection = None
            connection.ensure_connection()
            replacement = connection.connection
    assert raw.closed
    if replacement is not None:
        assert connection.connection is replacement and not replacement.closed
    else:
        assert connection.connection is None
    with installation_lock() as guard:
        guard.check()


def test_direct_driver_close_does_not_strand_wrapper():
    """A lost raw socket is discarded even without an unlock statement to fail."""
    with installation_lock() as guard:
        guard.raw.close()
        with pytest.raises(StorageInvariantError, match="lost"):
            guard.check()
    assert connection.connection is None
    with installation_lock() as guard:
        guard.check()


def test_forged_activation_and_checkpoint_are_rejected(initialized):
    """Neither direct checkpoint INSERT nor a requestless successor implies Applied."""
    store, root, actor = initialized
    receipt = stage(root, actor)
    for state in ("prepared", "yaml_activated", "applied"):
        with pytest.raises(IntegrityError), transaction.atomic():
            ConfigurationRequestCheckpoint.objects.create(
                request_id=receipt.request_id,
                sequence=2,
                state=state,
                actor_id=actor,
            )
    successor = configuration_version(successor_document(root))
    installer.prepare_snapshot(successor, actor_id=actor, correlation_id=uuid4())
    with pytest.raises(IntegrityError), transaction.atomic():
        ConfigurationActivation.objects.create(
            configuration_id=successor.version_id,
            predecessor_id=root.version_id,
            sequence=2,
            actor_id=actor,
        )
    assert store.active() == root


def test_lock_blocks_other_connections_and_releases_after_error():
    """The deployment-wide claim spans transactions, but cannot leak after an error."""
    entered, release = Event(), Event()

    def owner():
        """Use a truly independent connection and retain its lock until signaled."""
        connections.close_all()
        try:
            with installation_lock() as guard:
                with transaction.atomic(durable=True):
                    guard.check()
                entered.set()
                assert release.wait(10)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(owner)
        try:
            assert entered.wait(10)
            with pytest.raises(ConfigurationBusy), installation_lock():
                pytest.fail("competing installer entered")
        finally:
            release.set()
        future.result(timeout=10)
    with pytest.raises(OSError), installation_lock():
        raise OSError("synthetic")
    with installation_lock() as guard:
        guard.check()


def test_lost_connection_and_nested_transactions_fail_closed():
    """A reconnect must not silently replace the session owning serialization."""
    with (
        transaction.atomic(),
        pytest.raises(StorageInvariantError),
        installation_lock(),
    ):
        pytest.fail("nested transaction accepted")
    with installation_lock() as guard:
        with pytest.raises(StorageInvariantError), installation_lock():
            pytest.fail("independent nested claim accepted")
        connection.close()
        connection.ensure_connection()
        with pytest.raises(StorageInvariantError, match="lost"):
            guard.check()
    with pytest.raises(StorageInvariantError, match="lost"):
        guard.check()


def test_unlocked_materializer_and_unconfigured_coherence_fail_closed(tmp_path):
    """Neither a prepared row nor constructing a materializer implies readiness."""
    store = AuthorityStore(tmp_path, validate_sections)
    materializer = installer.DatabaseMaterializer(
        store, actor_id=uuid4(), correlation_id=uuid4()
    )
    with pytest.raises(StorageInvariantError):
        materializer.active_digest()
    with pytest.raises(ConfigError):
        installer.coherent_configuration(store)
    assert not AppliedConfigurationVersion.objects.exists()
