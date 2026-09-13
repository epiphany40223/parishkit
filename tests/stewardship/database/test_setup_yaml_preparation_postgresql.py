"""Initial YAML preparation cannot masquerade as complete setup."""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_errors import (
    ConfigurationReadinessUnavailable,
)
from parishkit.stewardship.accounts.configuration_installation import (
    DatabaseMaterializer,
    install_request,
)
from parishkit.stewardship.accounts.configuration_service import (
    admit_configuration_database,
)
from parishkit.stewardship.accounts.credential_files import CredentialFiles
from parishkit.stewardship.accounts.credential_installation import (
    CredentialInstaller,
    acknowledge_loaded_credential,
)
from parishkit.stewardship.accounts.key_files import read_private
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.setup_cancellation import cancel_finalizing_setup
from parishkit.stewardship.accounts.setup_confirmation import freeze_setup
from parishkit.stewardship.accounts.setup_credential_installation import (
    stage_initial_credential,
)
from parishkit.stewardship.accounts.setup_install_models import SetupPreparationReceipt
from parishkit.stewardship.accounts.setup_readiness_models import SetupReadinessBinding
from parishkit.stewardship.deployment import ServiceRole

from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_handoff_discovery_postgresql import key
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_confirmation_postgresql import prepared
from .test_setup_credential_installation_postgresql import MATERIAL
from .test_setup_exchange_postgresql import target_login
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def install_inputs(service, monkeypatch, tmp_path, *, slack=False, acknowledge=True):
    """Real frozen input, target files and restricted consumers; fake provider only."""
    browser, attempt, token = prepared(service, monkeypatch, tmp_path, slack=slack)
    with web_login():
        request = freeze_setup(browser, service, preview_token=token)
    for target in sorted(MATERIAL) if slack else ["parishsoft", "google_workspace"]:
        folder = tmp_path / target
        folder.mkdir(mode=0o700)
        files = CredentialFiles(
            folder / "credential", key(target, material=MATERIAL[target])
        )
        installer = CredentialInstaller(files, validate=lambda value: True)
        with target_login(target):
            receipt = stage_initial_credential(files)
            assert installer.run_once().state == "awaiting_ack"
            value = read_private(files.path)
        if acknowledge:
            role = (
                ServiceRole.MAIL_DISPATCH
                if target == "google_workspace"
                else ServiceRole.WORKER
            )
            with task_login(role, exact=True):
                acknowledge_loaded_credential(
                    request_id=receipt.request_id,
                    consumer=role.value,
                    loaded_value=value,
                )
    return browser, attempt, request


@pytest.mark.parametrize("slack", [False, True])
def test_prepare_selects_exact_yaml_but_never_activates_database(
    setup_service, monkeypatch, tmp_path, config_role, slack
):
    """Repeated installer passes wait at YAML selection with all rollback intact."""
    _, _, request = install_inputs(setup_service, monkeypatch, tmp_path, slack=slack)
    base = setup_service.store.active()
    with as_config_installer():
        receipt = install_request(
            setup_service.store, request_id=request.request_id, correlation_id=uuid4()
        )
        assert receipt.state == "yaml_activated"
        assert SetupPreparationReceipt.objects.count() == 1
        assert (
            install_request(
                setup_service.store,
                request_id=request.request_id,
                correlation_id=uuid4(),
            )
            == receipt
        )
        assert (
            SystemConfiguration.objects.get().active_configuration_id == base.version_id
        )
    assert setup_service.store.active().digest == receipt.candidate_digest
    assert not setup_service.configured()


def test_missing_consumer_ack_keeps_original_manifest_and_staged_request(
    setup_service, monkeypatch, tmp_path, config_role
):
    """Accepted test delivery and installed bytes do not substitute for consumer ACK."""
    _, _, request = install_inputs(
        setup_service, monkeypatch, tmp_path, acknowledge=False
    )
    base = setup_service.store.active()
    with as_config_installer(), pytest.raises(ConfigurationReadinessUnavailable):
        install_request(
            setup_service.store, request_id=request.request_id, correlation_id=uuid4()
        )
    assert setup_service.store.active() == base


def test_original_cancel_after_real_preparation_restores_only_unapplied_yaml(
    setup_service, monkeypatch, tmp_path, config_role
):
    """The existing abort owner handles actual initial selection, not a fake marker."""
    browser, attempt, request = install_inputs(setup_service, monkeypatch, tmp_path)
    base = setup_service.store.active()
    with as_config_installer():
        assert (
            install_request(
                setup_service.store,
                request_id=request.request_id,
                correlation_id=uuid4(),
            ).state
            == "yaml_activated"
        )
    with web_login():
        cancel_finalizing_setup(browser, setup_service, attempt.attempt_id)
    with as_config_installer():
        assert (
            install_request(
                setup_service.store,
                request_id=request.request_id,
                correlation_id=uuid4(),
            ).state
            == "failed"
        )
    assert setup_service.store.active() == base
    assert not setup_service.configured()


def test_config_installer_reads_liveness_but_not_session_credentials(config_role):
    """The setup original-login check does not broaden access to browser secrets."""
    with as_config_installer(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT id,principal_id,last_activity_at FROM stewardship_portal_session"
        )
    with (
        as_config_installer(),
        pytest.raises(DatabaseError),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT session_id FROM stewardship_portal_session")


def test_prepared_receipt_sql_rechecks_acknowledgements_without_python_preflight(
    setup_service, monkeypatch, tmp_path, config_role
):
    """A selected manifest and forged service call cannot invent consumer proof."""
    _, _, status = install_inputs(
        setup_service, monkeypatch, tmp_path, acknowledge=False
    )
    request = ConfigurationChangeRequest.objects.select_related("base").get(
        pk=status.request_id
    )
    candidate = build_candidate(
        setup_service.store.active(),
        request.patch,
        candidate_id=request.candidate_version_id,
        request_schema=request.request_schema,
    ).candidate
    materializer = DatabaseMaterializer(
        setup_service.store,
        request=request,
        actor_id=request.actor_id,
        correlation_id=uuid4(),
    )
    with as_config_installer(), materializer.lock():
        materializer.checkpoint("validating")
        setup_service.store.write_version(candidate)
        materializer.prepare(candidate)
        setup_service.store.select(candidate)
        materializer.checkpoint("yaml_activated")
        with pytest.raises(DatabaseError, match="consumer acknowledgements"):
            SetupPreparationReceipt.objects.create(
                readiness=SetupReadinessBinding.objects.get(),
                configuration_id=candidate.version_id,
                actor_id=request.actor_id,
            )
    assert not SetupPreparationReceipt.objects.exists()


def test_config_installer_rejects_excessive_session_column_access(config_role):
    """Safe liveness access never makes a later private-column grant acceptable."""
    with connection.cursor() as cursor:
        cursor.execute(
            "GRANT SELECT (session_id) ON stewardship_portal_session "
            "TO pk_stewardship_config_installer"
        )
    with as_config_installer(), pytest.raises(ConfigError, match="excessive"):
        admit_configuration_database()


def test_cancel_before_preparation_finishes_request_without_manifest_restore(
    setup_service, monkeypatch, tmp_path, config_role
):
    """An expired unprepared request cannot permanently block subsequent setup."""
    browser, attempt, token = prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        request = freeze_setup(browser, setup_service, preview_token=token)
        cancel_finalizing_setup(browser, setup_service, attempt.attempt_id)
    base = setup_service.store.active()
    with as_config_installer():
        result = install_request(
            setup_service.store, request_id=request.request_id, correlation_id=uuid4()
        )
        assert result.state == "failed"
        assert (
            install_request(
                setup_service.store,
                request_id=request.request_id,
                correlation_id=uuid4(),
            )
            == result
        )
    assert setup_service.store.active() == base


def test_crash_after_initial_yaml_selection_resumes_without_database_activation(
    setup_service, monkeypatch, tmp_path, config_role
):
    """Retained prepared bytes own recovery after a crash during selection."""
    _, _, request = install_inputs(setup_service, monkeypatch, tmp_path)
    original = setup_service.store.select

    def crash(version):
        """Lose the process after the real atomic rename, before its checkpoint."""
        original(version)
        raise RuntimeError("Synthetic post-selection crash")

    monkeypatch.setattr(setup_service.store, "select", crash)
    with as_config_installer(), pytest.raises(RuntimeError, match="Synthetic"):
        install_request(
            setup_service.store, request_id=request.request_id, correlation_id=uuid4()
        )
    monkeypatch.setattr(setup_service.store, "select", original)
    with as_config_installer():
        result = install_request(
            setup_service.store, request_id=request.request_id, correlation_id=uuid4()
        )
        assert result.state == "yaml_activated"
    assert not setup_service.configured()
