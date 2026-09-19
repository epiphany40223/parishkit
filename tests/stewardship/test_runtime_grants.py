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
        "stewardship_current_chair",  # Narrow SQL-only source projection.
        "stewardship_schedule_work_summary",  # Counts-only delivery projection.
        "stewardship_withdrawal_inventory",  # Counts-only withdrawal projection.
        "stewardship_daily_digest_completion_ready",  # Opaque completed cohort IDs.
        "stewardship_weekly_digest_completion_ready",  # Opaque resolved interval IDs.
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


def test_web_only_reads_source_owned_assignment_overlays():
    """Browser requests cannot become source-reconciliation writers."""
    tables, columns = runtime_grants(ServiceRole.WEB)
    assert tables["stewardship_assignment_overlay"] == {"SELECT"}
    assert "stewardship_assignment_overlay" not in columns


def test_outbox_writers_can_plan_shared_conditional_metadata_reads():
    """Every direct writer covers the remaining trigger queries at column precision.

    These shared metadata reads are safe under generic plans. Optional pause
    and cleanup-domain reads instead require explicit PL/pgSQL branching;
    neither this inventory nor a false SQL predicate grants their authority.
    """
    reads = {
        "stewardship_campaign_credentials": {
            "campaign_id",
            "go_live_gate",
            "rehearsal_epoch_id",
        },
        "stewardship_system_configuration": {"current_campaign_id", "mode"},
        "stewardship_rehearsal_epoch": {"id", "campaign_id", "state"},
        "stewardship_family_token_generation": {
            "id",
            "campaign_id",
            "credential_epoch",
        },
        "stewardship_family_token": {"generation_id", "family_id"},
        "stewardship_parish": {"id"},
        "stewardship_family_campaign": {"id", "campaign_id"},
        "stewardship_schedule_occurrence": {
            "outbox_id",
            "revision_id",
            "definition_id",
            "state",
        },
        "stewardship_schedule_definition": {"id", "current_revision_id"},
        "stewardship_outbox_event": {"message_id", "action", "version"},
    }
    writers = set()
    for _, _, role, target in database_identities():
        if role is ServiceRole.MIGRATION:
            continue
        tables, columns = runtime_grants(role, target=target)
        table = "stewardship_outbox_message"
        if not (
            {"INSERT", "UPDATE"}
            & (tables.get(table, set()) | set(columns.get(table, {})))
        ):
            continue
        writers.add(role)
        for table, fields in reads.items():
            assert "SELECT" in tables.get(table, set()) or fields <= columns.get(
                table, {}
            ).get("SELECT", set()), (role, table, fields)
    assert writers == {ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH}


def test_statistics_metadata_is_read_only_and_not_given_to_downloads():
    """Counts/organization checks add no source writes or raw validation reads."""
    tables, columns = runtime_grants(ServiceRole.WEB)
    metadata = columns["stewardship_source_snapshot"]
    assert {"counts", "organization_id"} <= metadata["SELECT"]
    assert "validation" not in metadata["SELECT"]
    assert metadata["UPDATE"] == {"id"}
    assert "stewardship_source_snapshot" not in tables
    download_tables, download_columns = runtime_grants("download")
    assert "stewardship_source_snapshot" not in download_tables
    assert not {"counts", "organization_id"} & download_columns.get(
        "stewardship_source_snapshot", {}
    ).get("SELECT", set())


def test_recipient_evidence_writes_are_closed_across_all_installed_identities():
    """Future unrelated services cannot accidentally inherit suppression writes."""
    writes = {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}
    for _, _, role, target in database_identities():
        if role is ServiceRole.MIGRATION:
            continue
        grants, columns = runtime_grants(role, target=target)
        for table in (
            "stewardship_recipient_refusal",
            "stewardship_recipient_resolution",
        ):
            expected = (
                {"INSERT"}
                if (
                    role in {ServiceRole.WORKER, ServiceRole.WEB}
                    and table == "stewardship_recipient_resolution"
                )
                or (
                    role is ServiceRole.MAIL_DISPATCH
                    and table == "stewardship_recipient_refusal"
                )
                else set()
            )
            assert grants.get(table, set()) & writes == expected
            assert not set(columns.get(table, {})) & writes
    for role in (ServiceRole.BACKUP_WORKER, ServiceRole.TOKEN_KEY_ROTATION):
        with pytest.raises(ConfigError):
            runtime_grants(role)


def test_config_installer_can_replace_boundaries_without_audit_or_family_reads():
    """Trigger-owned replacement is writable without broadening private reads."""
    tables, columns = runtime_grants(ServiceRole.CONFIG_INSTALLER)
    assert tables["stewardship_campaign_boundary"] == {"SELECT", "INSERT", "UPDATE"}
    assert tables["stewardship_audit_context"] == {"INSERT"}
    assert "stewardship_audit_context" not in columns
    assert "stewardship_family_token" not in tables


def test_public_handoff_grants_separate_discovery_from_publication():
    """Only a target installer publishes; web reads and other services have no need."""
    table = "stewardship_public_credential_handoff"
    grants, columns = runtime_grants(ServiceRole.WEB)
    assert grants[table] == {"SELECT"} and table not in columns
    grants, columns = runtime_grants(
        ServiceRole.CREDENTIAL_INSTALLER, target="parishsoft"
    )
    assert grants[table] == {"SELECT", "INSERT"} and table not in columns
    for role in (
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        "download",
        ServiceRole.CONFIG_INSTALLER,
    ):
        grants, columns = runtime_grants(role)
        assert table not in grants and table not in columns


@pytest.mark.parametrize(
    "role,target",
    [
        ("unknown", None),
        (ServiceRole.CREDENTIAL_INSTALLER, "unknown"),
        (ServiceRole.BACKUP_WORKER, None),
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
def test_background_grants_keep_initial_completion_separate_from_general_authority(
    role,
):
    """Setup and boundary SQL guards constrain grants without private read access."""
    tables, columns = runtime_grants(role)
    for table in ("stewardship_sealed_credential_staging",):
        assert table not in tables and table not in columns
    # Consumer acknowledgement needs target-scoped metadata and a row lock,
    # never candidate ciphertext or ordinary replacement mutation authority.
    assert tables["stewardship_secret_request"] == {"SELECT"}
    assert columns["stewardship_secret_request"] == {"UPDATE": {"id"}}
    assert tables["stewardship_credential_consumer_ack"] == {"SELECT", "INSERT"}
    if role is ServiceRole.SCHEDULER:
        assert "stewardship_setup_sealed_credential" not in tables
        assert (
            "ciphertext" not in columns["stewardship_setup_sealed_credential"]["SELECT"]
        )
        assert columns["stewardship_setup_sealed_credential"]["UPDATE"] == {
            "settings",
            "ciphertext",
            "scrubbed_at",
            "actor_id",
            "correlation_id",
            "version",
        }
    else:
        assert "stewardship_setup_sealed_credential" not in tables
        assert columns["stewardship_setup_sealed_credential"] == {
            "SELECT": {
                "id",
                "attempt_id",
                "target",
                "version",
                "fingerprint",
                "settings",
                "scrubbed_at",
            },
            "UPDATE": {
                "settings",
                "ciphertext",
                "scrubbed_at",
                "actor_id",
                "correlation_id",
                "version",
            },
        }
        assert tables["stewardship_setup_source_exchange"] == {"SELECT", "INSERT"}
    assert tables["stewardship_campaign"] == (
        {"SELECT", "INSERT"} if role is ServiceRole.WORKER else {"SELECT"}
    )
    assert tables["stewardship_domain_rule"] == {"SELECT"}
    assert columns["stewardship_campaign"] == {
        "UPDATE": {
            "id",
            "active_configuration_id",
            "state",
            "structural_locked",
            "ever_active",
            "active_token_generation_id",
            "readiness_revision",
            "version",
            "actor_id",
            "correlation_id",
        }
        if role is ServiceRole.WORKER
        else {"id"}
    }
    assert tables["stewardship_audit_event"] == {"INSERT"}
    if role is ServiceRole.WORKER:
        assert "stewardship_family_session" not in tables
        assert columns["stewardship_family_session"] == {
            "SELECT": {"family_id", "revoked_at", "last_activity_at", "version"},
            "UPDATE": {"revoked_at", "version"},
        }
        assert tables["stewardship_task_run"] == {"SELECT", "INSERT", "UPDATE"}
        assert "stewardship_portal_session" not in tables
        assert columns["stewardship_portal_session"] == {
            "SELECT": {
                "id",
                "principal_id",
                "revoked_at",
                "expires_at",
                "last_activity_at",
            }
        }
    else:
        assert "stewardship_family_session" not in tables
        assert "stewardship_family_session" not in columns
        assert "stewardship_portal_session" not in tables
        assert columns["stewardship_portal_session"] == {
            "SELECT": {
                "id",
                "principal_id",
                "revoked_at",
                "expires_at",
                "last_activity_at",
            }
        }
        assert tables["stewardship_task_run"] == {"SELECT", "INSERT"}
        from parishkit.stewardship.source.grants import SCHEDULER_CANCEL_COLUMNS

        assert columns["stewardship_task_run"] == {
            "UPDATE": set(SCHEDULER_CANCEL_COLUMNS)
        }
        assert tables["stewardship_source_lease"] == {"SELECT"}
        assert "stewardship_family_token" not in tables
    if role is ServiceRole.WORKER:
        assert tables["stewardship_family_token"] == {"INSERT"}
        assert "ciphertext" not in columns["stewardship_family_token"]["SELECT"]
        assert "digest" not in columns["stewardship_family_token"]["SELECT"]


def test_family_runtime_lock_and_activity_grants_do_not_allow_source_writes():
    """The web login receives only the columns required by locks/activity effects."""
    tables, columns = runtime_grants(ServiceRole.WEB)
    assert "UPDATE" not in tables["stewardship_family_campaign"]
    assert columns["stewardship_family_campaign"] == {
        "UPDATE": {
            "last_activity_at",
            "version",
            "first_live_submission_id",
            "effective_submission_id",
        }
    }
    assert columns["stewardship_credential_deployment"] == {"UPDATE": {"id"}}
    assert "INSERT" not in tables["stewardship_family_token"]


@pytest.mark.parametrize("actual", [None, (1,), (40,), (44,)])
def test_actual_sql_role_cap_must_match_deployment_budget(monkeypatch, actual):
    """A changed process/thread count cannot silently retain a stale role cap."""
    database = Mock()
    database.cursor.return_value.__enter__ = Mock(
        return_value=Mock(fetchone=lambda: actual)
    )
    database.cursor.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("django.db.connection", database)
    configuration = load_deployment(environ={})
    if actual == (44,):
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
