"""Minimal pre-wizard authority through real preparation, activation and recovery."""

from contextlib import nullcontext
from importlib import import_module
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.bootstrap_schema import bootstrap_version
from parishkit.stewardship.accounts.configuration_installation import (
    coherent_configuration,
    prepare_initial_configuration,
)
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
    AppliedIntegration,
    Parish,
)
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.accounts.configuration_snapshots import (
    is_prepared,
    prepare_snapshot,
)
from parishkit.stewardship.accounts.operator_recovery import recover_admin
from parishkit.stewardship.accounts.policy_models import AddressRule, AdminRevocation
from parishkit.stewardship.accounts.runtime_models import ConfigurationActivation
from parishkit.stewardship.audit.models import AuditEvent

from ..bootstrap_factory import bootstrap_fixture
from ..configuration_factory import configuration_document, configuration_version

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def bootstrapped(tmp_path):
    """Only synthetic login authority, no parish profile or external credentials."""
    deployment, actor = uuid4(), uuid4()
    store = AuthorityStore(tmp_path, validate_sections)
    root = bootstrap_version(deployment, "admin@example.org")
    prepare_initial_configuration(
        store,
        root,
        deployment_id=deployment,
        testing_recipient="admin@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    return store, root, deployment, actor


def test_initialization_has_stable_deployment_no_fake_parish(bootstrapped):
    """File/DB agreement and policy work without a fabricated setup-wizard profile."""
    store, root, deployment, actor = bootstrapped
    runtime = coherent_configuration(store)
    assert runtime.pk == deployment
    assert runtime.mode == "testing"
    assert runtime.current_campaign_id is None
    assert not Parish.objects.exists()
    assert not AppliedIntegration.objects.exists()
    assert AddressRule.objects.get().email == "admin@example.org"
    assert is_prepared(root.digest)
    prepare_initial_configuration(
        store,
        root,
        deployment_id=deployment,
        testing_recipient="admin@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    assert ConfigurationActivation.objects.count() == 1
    assert not AuditEvent.objects.exclude(parish_id=None).exists()
    with pytest.raises(ConfigError, match="does not match"):
        prepare_initial_configuration(
            store,
            root,
            deployment_id=uuid4(),
            testing_recipient="admin@example.org",
            actor_id=actor,
            correlation_id=uuid4(),
        )


def test_bootstrap_recovery_is_additive_and_replayable(bootstrapped):
    """Offline-only recovery commits normal activation/revocation/audit evidence."""
    store, _, deployment, _ = bootstrapped
    args = dict(
        operation_id=uuid4(),
        operator_name="Test operator",
        reason="Account lost",
        deployment_id=deployment,
        target_email="replacement@example.org",
        confirmed_email="replacement@example.org",
        correlation_id=uuid4(),
        offline_interlock=nullcontext,  # Internal fixture; CLI tests use real flock.
    )
    assert recover_admin(store, **args).state == "applied"
    assert recover_admin(store, **args).state == "applied"
    runtime = coherent_configuration(store)
    assert runtime.active_configuration.validation_schema == "bootstrap-policy-v1"
    assert set(
        AddressRule.objects.filter(
            configuration=runtime.active_configuration
        ).values_list("email", flat=True)
    ) == {"admin@example.org", "replacement@example.org"}
    assert AdminRevocation.objects.count() == 1
    assert not Parish.objects.exists()


def test_complete_successor_can_follow_bootstrap_but_not_regress(bootstrapped):
    """The later setup owner may introduce a real parish once, never remove it."""
    _, root, _, actor = bootstrapped
    document = configuration_document()
    document["predecessor_digest"] = root.digest
    document["sections"]["login_rules"] = root.document()["sections"]["login_rules"]
    successor = configuration_version(document)
    prepare_snapshot(successor, actor_id=actor, correlation_id=uuid4())
    assert is_prepared(successor.digest)
    document = root.document()
    document.update(version_id=str(uuid4()), predecessor_digest=successor.digest)
    with pytest.raises(ConfigError, match="predecessor"):
        prepare_snapshot(
            configuration_version(document), actor_id=actor, correlation_id=uuid4()
        )


@pytest.mark.parametrize("kind", ["parish", "integration"])
def test_sql_rejects_unrelated_later_projection(bootstrapped, kind):
    """Even insertion after commit cannot fabricate an implicit completed setup."""
    _, root, _, actor = bootstrapped
    fields = dict(
        configuration_id=root.version_id,
        record_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
    )
    with (
        pytest.raises(IntegrityError, match="unrelated projections"),
        transaction.atomic(),
    ):
        if kind == "integration":
            AppliedIntegration.objects.create(**fields, kind="parishsoft", settings={})
        else:
            Parish.objects.create(
                **fields,
                name="Fake",
                website="https://example.org",
                timezone="UTC",
                phone="+12025550123",
                large_logo_id=uuid4(),
                menu_logo_id=uuid4(),
                icon_logo_id=uuid4(),
                favicon_id=uuid4(),
            )


@pytest.mark.parametrize(
    "damage", ["sections", "domain", "roles", "missing-projection"]
)
def test_sql_rejects_bad_bootstrap_without_python_parser(damage):
    """Raw ORM insertion cannot bypass minimal shape or deferred policy completeness."""
    root = bootstrap_version(uuid4(), "admin@example.org")
    doc = root.document()
    if damage == "sections":
        doc["sections"]["parish"] = []
    elif damage == "domain":
        doc["sections"]["login_rules"][0]["values"]["kind"] = "domain"
    elif damage == "roles":
        doc["sections"]["login_rules"][0]["values"]["roles"].append("staff")
    with pytest.raises(IntegrityError), transaction.atomic():
        AppliedConfigurationVersion.objects.create(
            id=root.version_id,
            digest=root.digest,
            schema_version=1,
            canonical_document=doc,
            normalized_digest="a" * 64,
            validation_schema="bootstrap-policy-v1",
            actor_id=uuid4(),
            correlation_id=uuid4(),
        )


def test_populated_schema_downgrade_retains_protections(bootstrapped):
    """Preflight runs before any destructive backward operation."""
    migration = import_module(
        "parishkit.stewardship.accounts.migrations.0034_bootstrap_configuration_schema"
    )
    with (
        pytest.raises(IntegrityError, match="prevents schema downgrade"),
        connection.schema_editor() as editor,
    ):
        migration.backward(None, editor)
    assert is_prepared(bootstrapped[1].digest)


def test_complete_offline_file_and_database_protocol(tmp_path):
    """Both real interlocked phases retain the same root/deployment and safe audit."""
    from parishkit.stewardship.bootstrap import (
        materialize_initial_files,
        provision_initial_files,
    )

    configuration, identity = bootstrap_fixture(tmp_path)
    root = provision_initial_files(configuration, identity)
    assert not AppliedConfigurationVersion.objects.exists()
    assert materialize_initial_files(configuration, identity) == root
    store = AuthorityStore(configuration.paths["authority"], validate_sections)
    assert coherent_configuration(store).pk == identity.deployment_id
    with pytest.raises(ConfigError, match="already been initialized"):
        materialize_initial_files(configuration, identity)


def test_materialization_resumes_after_committed_activation(tmp_path, monkeypatch):
    """A crash before completion-marker publication does not duplicate activation."""
    from parishkit.stewardship import bootstrap

    configuration, identity = bootstrap_fixture(tmp_path)
    bootstrap.provision_initial_files(configuration, identity)
    write = bootstrap.write_private

    def crash_marker(path, value, **kwargs):
        """Simulate the only remaining file write after durable database activation."""
        if path.name == "bootstrap.json":
            raise OSError("simulated marker crash")
        return write(path, value, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(bootstrap, "write_private", crash_marker)
        with pytest.raises(OSError, match="simulated"):
            bootstrap.materialize_initial_files(configuration, identity)
    assert ConfigurationActivation.objects.count() == 1
    bootstrap.materialize_initial_files(configuration, identity)
    assert ConfigurationActivation.objects.count() == 1


def test_materialization_refuses_key_changed_between_phases(tmp_path):
    """A readable but nonmatching secret cannot gain initial database authority."""
    from parishkit.stewardship.accounts.key_files import write_private
    from parishkit.stewardship.bootstrap import (
        materialize_initial_files,
        provision_initial_files,
    )
    from parishkit.stewardship.runtime_paths import RuntimeLayout

    configuration, identity = bootstrap_fixture(tmp_path)
    provision_initial_files(configuration, identity)
    write_private(RuntimeLayout(configuration).credential("metrics"), b"changed")
    with pytest.raises(ConfigError, match="does not match"):
        materialize_initial_files(configuration, identity)
    assert not AppliedConfigurationVersion.objects.exists()
