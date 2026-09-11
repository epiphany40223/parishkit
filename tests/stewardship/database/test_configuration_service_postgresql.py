"""The configuration service uses actual restricted SQL grants, not a role hint."""

from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.configuration_service import (
    CONFIGURATION_GRANTS,
    ConfigurationInstaller,
    admit_configuration_database,
)
from parishkit.stewardship.deployment import ServiceRole, load_deployment

from ..test_request_patch import parish_patch
from .campaign_builders import draft_campaign, initialized

pytestmark = pytest.mark.django_db(transaction=True)
ROLE = "pk_stewardship_config_installer"


@pytest.fixture
def config_role():
    """Provision only this test's role and reject pre-existing cluster identities."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", [ROLE])
        assert cursor.fetchone() is None
        cursor.execute(
            f'CREATE ROLE "{ROLE}" LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS '
            "NOCREATEDB NOCREATEROLE NOREPLICATION"
        )
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{ROLE}"')
            for table, grants in CONFIGURATION_GRANTS.items():
                # Identifiers/privileges are the fixed reviewed registry only.
                cursor.execute(
                    f'GRANT {", ".join(sorted(grants))} ON "{table}" TO "{ROLE}"'
                )
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(f'DROP OWNED BY "{ROLE}"')
            cursor.execute(f'DROP ROLE "{ROLE}"')


@contextmanager
def as_config_installer():
    """Exercise current_user=session_user without a misleading SET ROLE shortcut."""
    with connection.cursor() as cursor:
        cursor.execute(f'SET SESSION AUTHORIZATION "{ROLE}"')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@pytest.mark.parametrize("current_campaign", [False, True])
def test_restricted_installer_applies_real_yaml_and_retries(
    tmp_path, config_role, monkeypatch, current_campaign
):
    """An admitted service installs an exact digest without private-data reads."""
    if current_campaign:
        store, campaign, actor = draft_campaign(tmp_path)
        root = store.active()
        from parishkit.stewardship.campaigns.models import ScheduleDefinition

        schedule = ScheduleDefinition.objects.get(campaign=campaign)
        patch = [
            *parish_patch(root, name="Changed parish"),
            {
                "operation": "update",
                "section": "schedules",
                "id": str(schedule.pk),
                "values": {"subject": "Updated invitation"},
            },
        ]
    else:
        store, root, actor = initialized(tmp_path)
        patch = parish_patch(root, name="Changed parish")
    request = record_request(
        base_digest=root.digest,
        patch=patch,
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    configuration = load_deployment(environ={})
    configuration = replace(
        configuration,
        service_role=ServiceRole.CONFIG_INSTALLER,
        paths=replace(
            configuration.paths,
            values={
                **configuration.paths.values,
                "authority": tmp_path,
            },
        ),
    )
    # Linux kernel proof has its separate actual-container suite. This host test
    # replaces only that platform boundary; SQL/file/activation behavior is real.
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.configuration_service.admit_online_service",
        lambda value: value.service_role,
    )
    with as_config_installer():
        admit_configuration_database()
        service = ConfigurationInstaller.from_configuration(configuration)
        result = service.run_request(request.request_id)
        assert result.state == "applied"
        assert service.run_request(request.request_id) == result
        with pytest.raises(ConfigError):
            service.run_request("not-a-uuid")
        with pytest.raises(ConfigError):
            ConfigurationInstaller.from_configuration(
                replace(configuration, service_role=ServiceRole.WEB)
            )
        for table in (
            "stewardship_family_campaign",
            "stewardship_family_token",
            "stewardship_sealed_credential_staging",
            "django_session",
        ):
            with pytest.raises(DatabaseError), connection.cursor() as cursor:
                cursor.execute(f'SELECT * FROM "{table}" LIMIT 1')


@pytest.mark.parametrize(
    "grant",
    ["SELECT ON stewardship_family_campaign", "INSERT ON stewardship_config_request"],
)
def test_excess_grants_rejected_before_any_install(config_role, grant):
    """Even unused excess authority fails closed before selecting a request."""
    with connection.cursor() as cursor:
        cursor.execute(f'GRANT {grant} TO "{ROLE}"')
    with as_config_installer(), pytest.raises(ConfigError, match="excessive"):
        admit_configuration_database()


def test_superuser_and_role_impersonation_are_not_admitted(config_role):
    """A configured role label does not establish an isolated SQL login."""
    with pytest.raises(ConfigError, match="identity"):
        admit_configuration_database()
    with connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{ROLE}"')
    try:
        with pytest.raises(ConfigError, match="identity"):
            admit_configuration_database()
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")


@pytest.mark.parametrize("extra", ["table", "definer", "sequence", "schema_create"])
def test_nonpublic_or_indirect_installer_grants_are_rejected(config_role, extra):
    """An auxiliary schema or definer routine cannot hide privilege expansion."""
    schema = "installer_test_" + uuid4().hex
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA "{schema}"')
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{ROLE}"')
            if extra == "table":
                cursor.execute(f'CREATE TABLE "{schema}".private_data (id integer)')
                cursor.execute(f'GRANT SELECT ON "{schema}".private_data TO "{ROLE}"')
            elif extra == "definer":
                cursor.execute(
                    f'CREATE FUNCTION "{schema}".private_reader() RETURNS integer '
                    "LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
                )
            elif extra == "sequence":
                cursor.execute(f'CREATE SEQUENCE "{schema}".private_sequence')
                cursor.execute(
                    f'GRANT USAGE ON SEQUENCE "{schema}".private_sequence TO "{ROLE}"'
                )
            else:
                cursor.execute(f'GRANT CREATE ON SCHEMA "{schema}" TO "{ROLE}"')
        with as_config_installer(), pytest.raises(ConfigError, match="excessive"):
            admit_configuration_database()
    finally:
        # This test created the exact UUID namespace above; no existing schema
        # or application object is discovered, adopted or removed here.
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA "{schema}" CASCADE')
