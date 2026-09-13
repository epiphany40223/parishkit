"""Closed SQL vocabulary and startup refusal without live provider credentials."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from django.apps import apps
from django.test import RequestFactory

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole, load_deployment
from parishkit.stewardship.runtime_database import require_role_capacity
from parishkit.stewardship.runtime_grants import (
    DOWNLOAD_READ_TABLES,
    WEB_INSERT_TABLES,
    WEB_READ_TABLES,
    WEB_UPDATE_TABLES,
    admit_columns,
    login_name,
    runtime_grants,
)
from parishkit.stewardship.runtime_identities import database_identities
from parishkit.stewardship.web.responses import campaign_response


def test_grant_registry_names_existing_models_and_excludes_unrelated_download_data():
    """New tables never receive incidental access through an unrelated registry."""
    tables = {model._meta.db_table for model in apps.get_models()} | {
        "django_migrations",
        "stewardship_download_policy",  # Intentionally SQL-only singleton.
    }
    assert WEB_INSERT_TABLES <= WEB_READ_TABLES
    assert WEB_UPDATE_TABLES <= WEB_READ_TABLES
    assert DOWNLOAD_READ_TABLES < WEB_READ_TABLES
    for _, _, role, target in database_identities():
        if role is ServiceRole.MIGRATION:
            continue
        grants, columns = runtime_grants(role, target=target)
        assert set(grants) | set(columns) <= tables
    grants, _ = runtime_grants("download")
    for table in (
        "django_session",
        "stewardship_secret_request",
        "stewardship_family_token",
        "stewardship_audit_event",
    ):
        assert table not in grants


@pytest.mark.parametrize(
    "role,target",
    [
        ("unknown", None),
        (ServiceRole.CREDENTIAL_INSTALLER, "unknown"),
        (ServiceRole.MAIL_DISPATCH, None),
        (ServiceRole.WEB, "metrics"),
        (ServiceRole.CONFIG_INSTALLER, "metrics"),
        (ServiceRole.BOOTSTRAP, "metrics"),
        (ServiceRole.ADMIN_RECOVERY, "metrics"),
        ("download", "metrics"),
    ],
)
def test_unknown_grant_identities_fail_closed(role, target):
    """Reserved future profiles do not silently receive today's web authority."""
    with pytest.raises(ConfigError):
        runtime_grants(role, target=target)
    with pytest.raises(ConfigError):
        login_name(role, target=target)


@pytest.mark.parametrize(
    "role",
    [
        ServiceRole.WEB,
        ServiceRole.CONFIG_INSTALLER,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
    ],
)
def test_grant_and_login_resolvers_normalize_string_roles_identically(role):
    """The CLI string form has exactly the enum identity's authority."""
    assert login_name(role.value) == login_name(role)
    assert runtime_grants(role.value) == runtime_grants(role)


@pytest.mark.parametrize("role", [ServiceRole.WORKER, ServiceRole.SCHEDULER])
def test_background_grants_exclude_web_secrets_and_campaign_write_authority(role):
    """Reserved producer/consumer identities cannot inherit web or future tables."""
    tables, columns = runtime_grants(role)
    for table in (
        "stewardship_secret_request",
        "stewardship_portal_session",
        "stewardship_family_session",
        "stewardship_family_token",
        "stewardship_source_lease",
        "stewardship_domain_rule",
    ):
        assert table not in tables and table not in columns
    assert tables["stewardship_campaign"] == {"SELECT"}
    assert columns["stewardship_campaign"] == {"UPDATE": {"id"}}
    assert tables["stewardship_audit_event"] == {"INSERT"}
    if role is ServiceRole.WORKER:
        assert tables["stewardship_task_run"] == {"SELECT", "INSERT", "UPDATE"}
    else:
        assert tables["stewardship_task_run"] == {"SELECT", "INSERT"}
        assert columns["stewardship_task_run"] == {"UPDATE": {"id"}}


def test_family_runtime_lock_and_activity_grants_do_not_allow_source_writes():
    """The web login receives only the columns required by locks/activity effects."""
    tables, columns = runtime_grants(ServiceRole.WEB)
    assert "UPDATE" not in tables["stewardship_family_campaign"]
    assert columns["stewardship_family_campaign"] == {
        "UPDATE": {"last_activity_at", "version"}
    }
    assert columns["stewardship_credential_deployment"] == {"UPDATE": {"id"}}
    assert "INSERT" not in tables["stewardship_family_token"]


@pytest.mark.parametrize("actual", [None, (1,), (40,)])
def test_actual_sql_role_cap_must_match_deployment_budget(monkeypatch, actual):
    """A changed process/thread count cannot silently retain a stale role cap."""
    database = Mock()
    database.cursor.return_value.__enter__ = Mock(
        return_value=Mock(fetchone=lambda: actual)
    )
    database.cursor.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("django.db.connection", database)
    configuration = load_deployment(environ={})
    if actual == (40,):
        require_role_capacity(configuration)
    else:
        with pytest.raises(ConfigError, match="connection limit"):
            require_role_capacity(configuration)


def test_missing_operational_download_pool_fails_before_any_private_read(settings):
    """No fallback to the ordinary web SQL login or default capacity is allowed."""
    settings.STEWARDSHIP_DOWNLOAD_POOL = None
    response = campaign_response(
        RequestFactory().get("/admin/report"),
        [],
        authorize=lambda _: pytest.fail("authorized"),
        open_content=lambda: pytest.fail("read data"),
        filename="report.csv",
        content_type="text/csv",
    )
    assert response.status_code == 503


def test_password_reference_mapping_is_copied_and_immutable(tmp_path):
    """Caller-owned dictionaries cannot change paths between admission and use."""
    configuration = load_deployment(environ={})
    paths = {"web": tmp_path / "web"}
    selected = replace(configuration.postgres, password_files=paths)
    paths["web"] = tmp_path / "changed"
    assert selected.password_files["web"] == tmp_path / "web"
    with pytest.raises(TypeError):
        selected.password_files["web"] = tmp_path / "changed"


@pytest.mark.parametrize("actual", [[("id",)], [("id",), ("private",)]])
def test_column_admission_rejects_any_extra_column(actual):
    """Column-only grants cannot mask a whole-table grant of the same kind."""
    database = Mock()
    database.cursor.return_value.__enter__ = Mock(
        return_value=Mock(fetchall=lambda: actual)
    )
    database.cursor.return_value.__exit__ = Mock(return_value=False)
    if len(actual) == 1:
        admit_columns(database, {}, {"table": {"SELECT": {"id"}}})
    else:
        with pytest.raises(ConfigError, match="column grants"):
            admit_columns(database, {}, {"table": {"SELECT": {"id"}}})
