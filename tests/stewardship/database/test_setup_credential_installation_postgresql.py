"""Frozen setup credential intake retains rollback until the configured marker."""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from unittest.mock import Mock

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.accounts.credential_files import CredentialFiles
from parishkit.stewardship.accounts.credential_installation import (
    CredentialInstaller,
    acknowledge_loaded_credential,
)
from parishkit.stewardship.accounts.key_files import read_private, write_private
from parishkit.stewardship.accounts.provider_models import ProviderValidationContext
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.accounts.secret_requests import _transaction, _transition
from parishkit.stewardship.accounts.setup_confirmation import freeze_setup
from parishkit.stewardship.accounts.setup_credential_installation import (
    stage_initial_credential,
)
from parishkit.stewardship.accounts.setup_install_models import (
    SetupCredentialInstallation,
)
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.deployment import ServiceRole

from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_handoff_discovery_postgresql import key
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_confirmation_postgresql import prepared
from .test_setup_exchange_postgresql import target_login
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
MATERIAL = {"parishsoft": b"s" * 32, "google_workspace": b"g" * 32, "slack": b"n" * 32}


def setup_installer(service, monkeypatch, tmp_path, target, *, previous=False):
    """Prepare actual reviewed readiness and a private target directory."""
    request, attempt, token = prepared(
        service, monkeypatch, tmp_path, slack=target == "slack"
    )
    with web_login():
        freeze_setup(request, service, preview_token=token)
    folder = tmp_path / target
    folder.mkdir(mode=0o700)
    path = folder / "credential"
    if previous:
        write_private(path, b"synthetic-previous")
    files = CredentialFiles(path, key(target, material=MATERIAL[target]))
    return request, attempt, CredentialInstaller(files, validate=lambda value: True)


@pytest.mark.parametrize("target", sorted(MATERIAL))
def test_initial_intake_preserves_namespace_and_holds_rollback_after_consumer_ack(
    setup_service, monkeypatch, tmp_path, target
):
    """Real target files and real restricted SQL implement the initial hold barrier."""
    _, _, installer = setup_installer(setup_service, monkeypatch, tmp_path, target)
    with target_login(target):
        staged = stage_initial_credential(installer.files)
        assert stage_initial_credential(installer.files) == staged
        binding = SetupCredentialInstallation.objects.get()
        assert binding.request_id == binding.credential_id == staged.request_id
        result = installer.run_once()
        assert result.state == "awaiting_ack"
        installed = read_private(installer.files.path)
    role = (
        ServiceRole.MAIL_DISPATCH
        if target == "google_workspace"
        else ServiceRole.WORKER
    )
    with task_login(role, exact=True):
        acknowledge_loaded_credential(
            request_id=staged.request_id, consumer=role.value, loaded_value=installed
        )
    with target_login(target):
        assert installer.run_once().state == "awaiting_ack"
        with installer.files.lock():
            assert installer.files.pending_request() == staged.request_id
        assert read_private(installer.files.path) == installed
    assert not setup_service.configured()


@pytest.mark.parametrize("previous", [False, True])
def test_original_cancellation_restores_predecessor_or_removes_wizard_only_file(
    setup_service, monkeypatch, tmp_path, previous
):
    """A pre-marker cancellation cleans installed files without abandoning rollback."""
    request, attempt, installer = setup_installer(
        setup_service, monkeypatch, tmp_path, "slack", previous=previous
    )
    with target_login("slack"):
        staged = stage_initial_credential(installer.files)
        assert installer.run_once().state == "awaiting_ack"
    with web_login():
        cancel_setup(request, setup_service, attempt.attempt_id)
    with target_login("slack"):
        assert installer.run_once().state == "expired"
        assert installer.run_once() is None
    if previous:
        assert read_private(installer.files.path) == b"synthetic-previous"
    else:
        assert not installer.files.path.exists()
    assert SecretReplacementRequest.objects.get(pk=staged.request_id).state == "expired"


def test_partial_initial_intake_rolls_back_every_public_and_private_row(
    setup_service, monkeypatch, tmp_path
):
    """A failed final insert cannot strand a request or copied sealed candidate."""
    _, _, installer = setup_installer(setup_service, monkeypatch, tmp_path, "slack")
    monkeypatch.setattr(
        ProviderValidationContext.objects, "create", Mock(side_effect=DatabaseError)
    )
    with target_login("slack"), pytest.raises(DatabaseError):
        stage_initial_credential(installer.files)
    assert not SetupCredentialInstallation.objects.exists()
    assert not SecretReplacementRequest.objects.exists()
    assert not installer.files.path.exists()


def test_consumer_ack_cannot_discard_initial_rollback_before_configuration_commit(
    setup_service, monkeypatch, tmp_path
):
    """The SQL transition rejects bypassing the installer's initial hold barrier."""
    _, _, installer = setup_installer(setup_service, monkeypatch, tmp_path, "slack")
    with target_login("slack"):
        staged = stage_initial_credential(installer.files)
        installer.run_once()
        value = read_private(installer.files.path)
    with task_login(ServiceRole.WORKER, exact=True):
        acknowledge_loaded_credential(
            request_id=staged.request_id, consumer="worker", loaded_value=value
        )
    with (
        target_login("slack"),
        pytest.raises(DatabaseError, match="rollback must remain"),
        _transaction(),
    ):
        row = SecretReplacementRequest.objects.get(pk=staged.request_id)
        _transition(
            row,
            "cleanup_pending",
            actor_id=None,
            correlation_id=row.correlation_id,
            reason="applied",
        )
    assert (
        SecretReplacementRequest.objects.get(pk=staged.request_id).state
        == "awaiting_ack"
    )


def test_cancellation_before_initial_installation_has_no_file_effect(
    setup_service, monkeypatch, tmp_path
):
    """A queued initial credential loses its authority with its original attempt."""
    request, attempt, installer = setup_installer(
        setup_service, monkeypatch, tmp_path, "slack"
    )
    with target_login("slack"):
        staged = stage_initial_credential(installer.files)
    with web_login():
        cancel_setup(request, setup_service, attempt.attempt_id)
    with target_login("slack"):
        assert stage_initial_credential(installer.files) is None
        assert installer.run_once().state == "expired"
    assert not installer.files.path.exists()
    assert SecretReplacementRequest.objects.get(pk=staged.request_id).state == "expired"


def test_initial_binding_is_append_only_and_other_target_cannot_read_private_intake(
    setup_service, monkeypatch, tmp_path
):
    """Frozen provenance and separate target ciphertext keep their boundaries."""
    _, _, installer = setup_installer(setup_service, monkeypatch, tmp_path, "slack")
    with target_login("slack"):
        staged = stage_initial_credential(installer.files)
    with target_login("parishsoft"), connection.cursor() as cursor:
        cursor.execute(
            "SELECT ciphertext FROM stewardship_sealed_credential_staging "
            "WHERE request_id=%s",
            [staged.request_id],
        )
        assert cursor.fetchall() == []
    with (
        pytest.raises(DatabaseError, match="append-only"),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_setup_credential_install")


@pytest.mark.parametrize("reason", ["failed", "cancelled"])
def test_live_initial_rollback_cannot_bypass_original_setup_owner(
    setup_service, monkeypatch, tmp_path, reason
):
    """A prepared receipt cannot be invalidated by a separate child cancellation."""
    _, _, installer = setup_installer(setup_service, monkeypatch, tmp_path, "slack")
    with target_login("slack"):
        staged = stage_initial_credential(installer.files)
        assert installer.run_once().state == "awaiting_ack"
        with pytest.raises(DatabaseError, match="original setup"):
            installer._advance(
                staged.request_id, "awaiting_ack", "cleanup_pending", reason=reason
            )
        assert installer.run_once().state == "awaiting_ack"
