"""Every runtime role's settings assembly records the admitted deployment profile.

Source reads and the LOCAL banner read ``STEWARDSHIP_DEPLOYMENT_PROFILE`` from
Django settings, and there is deliberately no default. The two assembly
functions (``configure_web`` for the web role, ``configure_operator_database``
for every other process) take the value from one shared helper, and every
``ServiceRole`` runner reaches one of them, so no role can forget.
"""

import inspect
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest

from parishkit.stewardship import (
    operator_commands,
    runtime_background,
    runtime_database,
    runtime_process,
    runtime_web,
)
from parishkit.stewardship.deployment import DeploymentProfile, ServiceRole

from .test_runtime_topology import configuration_at

# configure_web admits mounts, then configure_web_runtime assembles the settings
# (the LOCAL seeder calls the latter directly, #476).
ASSEMBLIES = (
    runtime_web.configure_web_runtime,
    operator_commands.configure_operator_database,
)


def test_profile_settings_name_the_admitted_profile(tmp_path):
    """The shared helper is the one place the setting's value comes from."""
    for profile in DeploymentProfile:
        configuration = replace(configuration_at(tmp_path), profile=profile)
        assert runtime_database.profile_settings(configuration) == {
            "STEWARDSHIP_DEPLOYMENT_PROFILE": profile.value
        }


def test_both_assemblies_take_the_setting_from_the_shared_helper():
    """Web and operator/background assembly both configure it before any work."""
    for assembly in ASSEMBLIES:
        source = inspect.getsource(assembly)
        assert "profile_settings(configuration)" in source, assembly.__name__
        assert source.index("profile_settings(configuration)") < source.index(
            "settings.configure("
        ), assembly.__name__


@pytest.mark.parametrize("profile", list(DeploymentProfile))
def test_operator_assembly_configures_the_profile_before_django_starts(
    tmp_path, monkeypatch, profile
):
    """Installers, background roles and operator commands record the profile."""
    recorded = {}
    fake = SimpleNamespace(configured=False, configure=lambda **v: recorded.update(v))
    monkeypatch.setattr("django.conf.settings", fake)
    monkeypatch.setattr("django.setup", lambda: None)
    monkeypatch.setattr(runtime_database, "database_settings", lambda c: {})
    configuration = replace(configuration_at(tmp_path), profile=profile)
    operator_commands.configure_operator_database(configuration)
    assert recorded["STEWARDSHIP_DEPLOYMENT_PROFILE"] == profile.value
    assert "SECRET_KEY" in recorded and "DATABASES" in recorded


def test_every_service_role_runner_reaches_an_assembly_that_records_it():
    """A future role cannot start serving without recording its profile."""
    runners = dict(
        re.findall(
            r"ServiceRole\.(\w+): (serve_\w+)", inspect.getsource(runtime_process)
        )
    )
    online = {
        ServiceRole.WEB,
        ServiceRole.CONFIG_INSTALLER,
        ServiceRole.CREDENTIAL_INSTALLER,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
    }
    assert {ServiceRole[name] for name in runners} == online
    # The web runner assembles inside each Gunicorn worker's loader; the
    # background runner delegates to the operator assembly.
    assemblies = {
        "configure_web(configuration)",
        "configure_operator_database(configuration)",
        "configure_background(",
        "load_web_application(configuration",
    }
    for name in runners.values():
        source = inspect.getsource(getattr(runtime_process, name))
        assert any(assembly in source for assembly in assemblies), name
    assert "configure_web(configuration)" in inspect.getsource(
        runtime_process.load_web_application
    )
    assert "configure_operator_database(configuration)" in inspect.getsource(
        runtime_background.configure_background
    )
    # Every other role is an operator command, a backup or a load check, all of
    # which configure Django through the same operator assembly.
    offline = set(ServiceRole) - online
    assert offline == {
        ServiceRole.BACKUP_WORKER,
        ServiceRole.TOKEN_KEY_ROTATION,
        ServiceRole.BOOTSTRAP,
        ServiceRole.MIGRATION,
        ServiceRole.ADMIN_RECOVERY,
        ServiceRole.DATABASE_PROVISION,
    }
