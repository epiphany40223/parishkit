"""Audit ownership is inserted atomically and never retroactively reassigned."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.db.models.deletion import Collector, ProtectedError
from django.utils import timezone

from parishkit.stewardship.accounts import configuration_installation as installer
from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_models import Parish
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.accounts.request_models import ConfigurationRequestCheckpoint
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.accounts.runtime_models import ConfigurationActivation
from parishkit.stewardship.accounts.secret_requests import stage_secret_request
from parishkit.stewardship.audit.models import AuditEvent

from ..configuration_factory import configuration_version, successor_document
from ..test_request_patch import parish_patch

pytestmark = pytest.mark.django_db(transaction=True)
PREVIOUS = ("stewardship_audit", "0005_alter_auditevent_subject_id")
CURRENT = ("stewardship_audit", "0006_parish_ownership")


def initialize(tmp_path):
    """Install a synthetic root, generating the real database activation audit."""
    store, root = AuthorityStore(tmp_path, validate_sections), configuration_version()
    actor = uuid4()
    installer.prepare_initial_configuration(
        store,
        root,
        testing_recipient="test@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    return store, root, actor


@pytest.fixture
def initialized(tmp_path):
    """Start each configured scenario from its own durable synthetic root."""
    return initialize(tmp_path)


def advance(store, root, actor):
    """Exercise all real request checkpoints and the successor activation."""
    receipt = record_request(
        base_digest=root.digest,
        patch=parish_patch(root, name="Successor Parish"),
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    installer.install_request(
        store, request_id=receipt.request_id, correlation_id=uuid4()
    )
    return receipt


@pytest.mark.parametrize("prepared", [False, True])
def test_generic_events_without_active_runtime_remain_deployment(prepared):
    """An unactivated candidate does not implicitly become deployment authority."""
    if prepared:
        installer.prepare_snapshot(
            configuration_version(), actor_id=uuid4(), correlation_id=uuid4()
        )
    event = AuditEvent.objects.create(event_type="bootstrap_check")
    assert event.ownership_scope == "deployment"
    assert event.parish_id is event.campaign_reference is None
    connections.close_all()
    assert AuditEvent.objects.get(pk=event.pk).ownership_scope == "deployment"


def test_request_base_and_activation_profiles_remain_historical(initialized):
    """A request's final checkpoint retains its base, not its newly active target."""
    store, root, actor = initialized
    original = AuditEvent.objects.get()
    request = advance(store, root, actor)
    base = Parish.objects.get(configuration_id=root.version_id)
    successor = Parish.objects.get(configuration_id=request.candidate_version_id)
    assert base.record_id == successor.record_id and base.pk != successor.pk
    events = AuditEvent.objects.filter(subject_id=request.request_id)
    assert events.count() == 5
    assert set(events.values_list("ownership_scope", "parish_id")) == {
        ("parish", base.pk)
    }
    activation = ConfigurationActivation.objects.get(request_id=request.request_id)
    assert AuditEvent.objects.get(subject_id=activation.pk).parish_id == successor.pk
    original.refresh_from_db()
    assert original.parish_id == base.pk
    assert original.parish.name == "Example Parish"
    latest = AuditEvent.objects.create(event_type="current_check")
    assert latest.ownership_scope == "parish" and latest.parish_id == successor.pk


def test_prebootstrap_request_has_explicit_base_ownership():
    """Known request context has a Parish even before the runtime is activated."""
    root, actor = configuration_version(), uuid4()
    installer.prepare_snapshot(root, actor_id=actor, correlation_id=uuid4())
    request = record_request(
        base_digest=root.digest,
        patch=parish_patch(root, name="Requested Parish"),
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    event = AuditEvent.objects.get(subject_id=request.request_id)
    assert event.ownership_scope == "parish"
    assert event.parish.configuration_id == root.version_id


@pytest.mark.parametrize("bulk", [False, True])
def test_server_attribution_returned_without_refresh(initialized, bulk):
    """Django INSERT RETURNING exposes trigger-produced scope and profile."""
    _, root, _ = initialized
    kwargs = dict(event_type="report_viewed", campaign_reference=uuid4())
    if bulk:
        event = AuditEvent.objects.bulk_create([AuditEvent(**kwargs)])[0]
    else:
        event = AuditEvent.objects.create(**kwargs)
    assert event.ownership_scope == "parish"
    assert event.parish_id == Parish.objects.get(configuration_id=root.version_id).pk
    assert event.campaign_reference == kwargs["campaign_reference"]
    assert not AuditEvent._meta.get_field("campaign_reference").is_relation
    with pytest.raises(ProtectedError):
        Collector(using="default").collect([event.parish])
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_audit_event SET parish_id = NULL WHERE id = %s",
            [event.pk],
        )


def test_raw_insert_defaults_and_matching_explicit_owner(initialized):
    """Omitted columns and explicit matching attribution both work for SQL callers."""
    parish = Parish.objects.get()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_audit_event (id, correlation_id, event_type) "
            "VALUES (%s, %s, 'raw_check') RETURNING ownership_scope, parish_id",
            [uuid4(), uuid4()],
        )
        assert cursor.fetchone() == ("parish", parish.pk)
    event = AuditEvent.objects.create(
        event_type="explicit_check",
        ownership_scope="parish",
        parish=parish,
    )
    assert event.parish_id == parish.pk


def test_search_path_cannot_shadow_attribution_tables(initialized):
    """Temporary relation names cannot select a false deployment/profile context."""
    _, root, actor = initialized
    parish = Parish.objects.get()
    activation = ConfigurationActivation.objects.get()
    request = record_request(
        base_digest=root.digest,
        patch=parish_patch(root, name="Next Parish"),
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    with transaction.atomic(durable=True), connection.cursor() as cursor:
        cursor.execute("SET LOCAL search_path = pg_temp, public")
        cursor.execute(
            "CREATE TEMP TABLE stewardship_system_configuration "
            "(active_configuration_id uuid) ON COMMIT DROP"
        )
        cursor.execute(
            "CREATE TEMP TABLE stewardship_parish "
            "(id uuid, configuration_id uuid) ON COMMIT DROP"
        )
        cursor.execute(
            "CREATE TEMP TABLE stewardship_config_activation "
            "(id uuid, configuration_id uuid) ON COMMIT DROP"
        )
        cursor.execute(
            "CREATE TEMP TABLE stewardship_config_request "
            "(id uuid, base_id uuid) ON COMMIT DROP"
        )
        for event_type, subject in (
            ("shadow_check", None),
            ("configuration_activated", activation.pk),
            ("config_request_staged", request.request_id),
        ):
            event = AuditEvent.objects.create(event_type=event_type, subject_id=subject)
            assert event.ownership_scope == "parish" and event.parish_id == parish.pk


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize(
    "event_type", ["config_request_staged", "configuration_activated"]
)
def test_reserved_events_require_real_subject(tmp_path, configured, event_type):
    """A typo or absent subject must not silently get deployment/current ownership."""
    if configured:
        initialize(tmp_path)
    before = AuditEvent.objects.count()
    with (
        pytest.raises(IntegrityError, match="context is missing"),
        transaction.atomic(),
    ):
        AuditEvent.objects.create(event_type=event_type, subject_id=uuid4())
    assert AuditEvent.objects.count() == before


@pytest.mark.parametrize("scope", [None, "campaign", "private invalid"])
def test_invalid_scope_is_rejected_without_echoing_value(initialized, scope):
    """Only the two admitted ownership scopes exist in this foundation."""
    with (
        pytest.raises(IntegrityError, match="Invalid audit ownership") as error,
        transaction.atomic(),
    ):
        AuditEvent.objects.create(event_type="check_scope", ownership_scope=scope)
    assert "private invalid" not in str(error.value)


def test_caller_cannot_choose_unactivated_or_stale_profile(initialized):
    """Caller ownership metadata cannot override the context inferred in SQL."""
    _, root, actor = initialized
    successor = configuration_version(successor_document(root))
    installer.prepare_snapshot(successor, actor_id=actor, correlation_id=uuid4())
    wrong = Parish.objects.get(configuration_id=successor.version_id)
    with (
        pytest.raises(IntegrityError, match="Invalid audit ownership"),
        transaction.atomic(),
    ):
        AuditEvent.objects.create(event_type="forged_owner", parish=wrong)


@pytest.mark.parametrize(
    "kwargs", [{"ownership_scope": "parish"}, {"campaign_reference": uuid4()}]
)
def test_deployment_event_cannot_claim_parish_or_campaign(kwargs):
    """The database shape check also applies to bulk/raw-style inserts."""
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEvent.objects.create(event_type="invalid_owner", **kwargs)


def test_secret_checkpoint_uses_active_parish(initialized):
    """The unchanged secret SQL emitter gets ownership in its own transaction."""
    _, root, actor = initialized
    request_id = uuid4()
    stage_secret_request(
        request_id=request_id,
        target="parishsoft",
        staging_reference=uuid4(),
        actor_id=actor,
        reauthenticated_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint=None,
        correlation_id=uuid4(),
    )
    event = AuditEvent.objects.get(subject_id=request_id)
    assert event.ownership_scope == "parish"
    assert event.parish.configuration_id == root.version_id


def test_missing_projection_fails_closed_and_rolls_back(initialized):
    """Even damaged operator state cannot create an unattributed event."""
    _, root, _ = initialized
    before = AuditEvent.objects.count()
    with (
        pytest.raises(IntegrityError, match="projection is missing"),
        transaction.atomic(durable=True),
        connection.cursor() as cursor,
    ):
        # Corruption is synthetic and transaction-local: failure restores the
        # row and every trigger before subsequent tests use this database.
        cursor.execute(
            "ALTER TABLE stewardship_parish DISABLE TRIGGER "
            "stewardship_parish_immutable_guard_v1"
        )
        cursor.execute(
            "DELETE FROM stewardship_parish WHERE configuration_id = %s",
            [root.version_id],
        )
        AuditEvent.objects.create(event_type="damaged_projection")
    assert Parish.objects.get(configuration_id=root.version_id)
    assert AuditEvent.objects.count() == before
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tgenabled FROM pg_trigger WHERE tgrelid = "
            "'stewardship_parish'::regclass AND tgname = %s",
            ["stewardship_parish_immutable_guard_v1"],
        )
        assert cursor.fetchone()[0] == "O"


def test_deployment_history_survives_downgrade_roundtrip(tmp_path):
    """Nonempty deployment history can reverse without losing original values."""
    event = AuditEvent.objects.create(event_type="bootstrap_check")
    before = AuditEvent.objects.values().get(pk=event.pk)
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([PREVIOUS])
        old_model = executor.loader.project_state([PREVIOUS]).apps.get_model(
            "stewardship_audit", "AuditEvent"
        )
        older = old_model.objects.values().get(pk=event.pk)
        assert older == {key: before[key] for key in older}
        assert "ownership_scope" not in older
        MigrationExecutor(connection).migrate(leaves)
        assert AuditEvent.objects.values().get(pk=event.pk) == before
        _, root, _ = initialize(tmp_path)
        assert AuditEvent.objects.get(pk=event.pk).ownership_scope == "deployment"
        assert (
            AuditEvent.objects.create(
                event_type="restored_guard"
            ).parish.configuration_id
            == root.version_id
        )
    finally:
        MigrationExecutor(connection).migrate(leaves)


@pytest.mark.parametrize("configured", [False, True])
def test_secret_downgrade_cannot_remove_audit_ownership(tmp_path, configured):
    """An unrelated secret-schema refusal leaves audit columns and guard intact."""
    if configured:
        initialize(tmp_path)
    stage_secret_request(
        request_id=uuid4(),
        target="parishsoft",
        staging_reference=uuid4(),
        actor_id=uuid4(),
        reauthenticated_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint=None,
        correlation_id=uuid4(),
    )
    before = list(AuditEvent.objects.order_by("id").values())
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        with pytest.raises(
            IntegrityError, match="Secret request history prevents downgrade"
        ):
            MigrationExecutor(connection).migrate(
                [("stewardship_accounts", "0014_secret_request_records")]
            )
        assert MigrationRecorder.Migration.objects.filter(
            app=CURRENT[0], name=CURRENT[1]
        ).exists()
        assert list(AuditEvent.objects.order_by("id").values()) == before
        new = AuditEvent.objects.create(event_type="secret_downgrade_refused")
        assert new.ownership_scope == ("parish" if configured else "deployment")
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_legacy_upgrade_preserves_original_rows_without_guessing(tmp_path):
    """ADD COLUMN defaults label legacy history without updating immutable rows."""
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([PREVIOUS])
        _, root, _ = initialize(tmp_path)
        old_model = executor.loader.project_state(
            [PREVIOUS, ("stewardship_accounts", "0015_secret_request_guards")]
        ).apps.get_model("stewardship_audit", "AuditEvent")
        before = old_model.objects.values().get()
        MigrationExecutor(connection).migrate(leaves)
        after = AuditEvent.objects.values().get(pk=before["id"])
        assert {key: after[key] for key in before} == before
        assert after["ownership_scope"] == "deployment" and after["parish_id"] is None
        new = AuditEvent.objects.create(event_type="after_upgrade")
        assert new.parish.configuration_id == root.version_id
    finally:
        MigrationExecutor(connection).migrate(leaves)


@pytest.mark.parametrize("history", ["activation", "secret", "checkpoint"])
def test_broad_downgrade_preserves_upgraded_legacy_ownership(tmp_path, history):
    """An older accounts guard must not strand an already-reversed audit schema."""
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([PREVIOUS])
        if history == "activation":
            initialize(tmp_path)
        elif history == "checkpoint":
            root, actor = configuration_version(), uuid4()
            installer.prepare_snapshot(root, actor_id=actor, correlation_id=uuid4())
            intent = build_candidate(
                root, parish_patch(root, name="Next Parish"), candidate_id=uuid4()
            )
            # Historical fixtures must use historical models: current request
            # services now include offline-recovery columns absent at this leaf.
            request_model = executor.loader.project_state(
                [PREVIOUS, ("stewardship_accounts", "0015_secret_request_guards")]
            ).apps.get_model("stewardship_accounts", "ConfigurationChangeRequest")
            request = request_model.objects.create(
                base_id=root.version_id,
                patch=intent.patch(),
                actor_id=actor,
                request_key=uuid4(),
                correlation_id=uuid4(),
                request_schema="parish-integrations-patch-v1",
                payload_fingerprint=intent.payload_fingerprint,
                candidate_version_id=intent.candidate.version_id,
                candidate_digest=intent.candidate.digest,
            )
            ConfigurationRequestCheckpoint.objects.create(
                request_id=request.pk,
                actor_id=actor,
                state="validating",
                sequence=2,
            )
            assert not ConfigurationActivation.objects.exists()
        else:
            stage_secret_request(
                request_id=uuid4(),
                target="parishsoft",
                staging_reference=uuid4(),
                actor_id=uuid4(),
                reauthenticated_at=timezone.now() - timedelta(minutes=1),
                expires_at=timezone.now() + timedelta(minutes=10),
                expected_fingerprint=None,
                correlation_id=uuid4(),
            )
        MigrationExecutor(connection).migrate(leaves)
        before = list(AuditEvent.objects.order_by("id").values())
        assert all(row["ownership_scope"] == "deployment" for row in before)
        with pytest.raises(IntegrityError, match="history prevents"):
            MigrationExecutor(connection).migrate(
                [("stewardship_accounts", "0010_request_intake_guards")]
            )
        assert MigrationRecorder.Migration.objects.filter(
            app=CURRENT[0], name=CURRENT[1]
        ).exists()
        assert list(AuditEvent.objects.order_by("id").values()) == before
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tgenabled FROM pg_trigger WHERE tgname = %s",
                ["stewardship_audit_ownership_v1"],
            )
            assert cursor.fetchone()[0] == "O"
        event = AuditEvent.objects.create(event_type="after_refusal")
        assert event.ownership_scope == (
            "parish" if history == "activation" else "deployment"
        )
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_reverse_without_optional_secret_schema():
    """The minimum supported dependency graph has no secret table to inspect."""
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        MigrationExecutor(connection).migrate(
            [("stewardship_accounts", "0013_activation_guards")]
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.stewardship_secret_request')")
            assert cursor.fetchone()[0] is None
        assert MigrationRecorder.Migration.objects.filter(
            app=CURRENT[0], name=CURRENT[1]
        ).exists()
        MigrationExecutor(connection).migrate([PREVIOUS])
        assert not MigrationRecorder.Migration.objects.filter(
            app=CURRENT[0], name=CURRENT[1]
        ).exists()
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_populated_downgrade_preserves_ownership_and_guard(initialized):
    """Refusal precedes trigger/field removal and is not hidden by restoration."""
    before = AuditEvent.objects.values().get()
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        with pytest.raises(IntegrityError, match="Parish audit history prevents"):
            MigrationExecutor(connection).migrate([PREVIOUS])
        assert MigrationRecorder.Migration.objects.filter(
            app=CURRENT[0], name=CURRENT[1]
        ).exists()
        assert AuditEvent.objects.values().get() == before
        assert (
            AuditEvent.objects.create(event_type="guard_survived").parish_id
            == before["parish_id"]
        )
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_concurrent_activation_never_reassigns_prior_event(initialized):
    """Independent connections see committed profiles and preserve prior attribution."""
    store, root, actor = initialized
    inserted, release = Event(), Event()

    def reader():
        """Keep an event transaction open across a separately committed activation."""
        connections.close_all()
        try:
            with transaction.atomic():
                old = AuditEvent.objects.create(event_type="before_activation")
                inserted.set()
                assert release.wait(10)
                new = AuditEvent.objects.create(event_type="after_activation")
                return old.pk, new.pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(reader)
        try:
            assert inserted.wait(10)
            request = advance(store, root, actor)
        finally:
            release.set()
        old_id, new_id = future.result(timeout=10)
    assert AuditEvent.objects.get(pk=old_id).parish.configuration_id == root.version_id
    assert (
        AuditEvent.objects.get(pk=new_id).parish.configuration_id
        == request.candidate_version_id
    )
