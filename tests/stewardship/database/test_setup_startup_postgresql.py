"""An exact selected initial candidate can restart into recovery, never authority."""

# ruff: noqa: F811 -- imported pytest fixtures.

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.sessions import end_admin
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_startup import initial_setup_hold
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_background import matching_authority

from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_configuration_service_postgresql import config_role  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_expiry_postgresql import sweep
from .test_setup_final_loading_postgresql import prepared
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("role", [ServiceRole.WORKER, ServiceRole.SCHEDULER])
def test_restarting_background_can_expire_original_login_but_not_use_candidate(
    setup_service, monkeypatch, tmp_path, config_role, role
):
    """Loss of the original session must not strand a selected candidate forever."""
    browser, attempt, _ = prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        end_admin(browser)
    with task_login(role, exact=True):
        runtime = initial_setup_hold(setup_service.store)
        assert runtime.active_configuration_id == SetupAttempt.objects.get().base_id
        with pytest.raises(ConfigError, match="recovery"):
            matching_authority(setup_service.store)
    assert sweep() == 1
    assert SetupAttempt.objects.get(pk=attempt.attempt_id).state == "expired"
    with task_login(role, exact=True):
        assert initial_setup_hold(setup_service.store).pk == runtime.pk


def test_web_can_restart_to_existing_cancellation_surface_only(
    setup_service, monkeypatch, tmp_path, config_role
):
    """Startup recognition never replaces the normal request authority store."""
    prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        assert initial_setup_hold(setup_service.store).current_campaign_id is None
        with pytest.raises(ConfigError):
            matching_authority(setup_service.store)


def test_unrelated_manifest_and_raced_selection_cannot_enter_setup_hold(
    setup_service, monkeypatch, tmp_path, config_role
):
    """The exception is a selected bound candidate, not any bootstrap mismatch."""
    prepared(setup_service, monkeypatch, tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(
            setup_service.store, "manifest_reference", lambda: (uuid4(), "f" * 64)
        )
        with task_login(ServiceRole.SCHEDULER, exact=True), pytest.raises(ConfigError):
            initial_setup_hold(setup_service.store)
    base = setup_service.store.read_version(SetupAttempt.objects.get().base_id)
    setup_service.store.select(base)
    with task_login(ServiceRole.SCHEDULER, exact=True), pytest.raises(ConfigError):
        initial_setup_hold(setup_service.store)
