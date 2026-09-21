"""Closed runtime SQL grants for the implemented foundation, not future features.

Provisioning and startup share this reviewed registry. Future application tables
receive no automatic access. Offline recovery retains the config-installer OS
identity/implementation, but uses its own operator-only SQL login so revocation
and operator-request INSERT never become online config-installer privileges.
"""

from parishkit.config import ConfigError

from .deployment import ServiceRole

# Explicit snapshot/read vocabulary; no wildcard over future application tables.
WEB_READ_TABLES = frozenset(
    [
        "stewardship_setup_completion",
        "stewardship_campaign_mail_test",
        "stewardship_setup_prepared",
        "stewardship_setup_attempt",
        "stewardship_setup_draft_section",
        "stewardship_setup_config_intent",
        "stewardship_setup_config_abort",
        "stewardship_branding_bundle",
        "stewardship_branding_asset",
        "stewardship_provider_context",
        "stewardship_public_credential_handoff",
        "stewardship_source_current",
        "stewardship_snapshot_ministry",
        "stewardship_source_ministry",
        "stewardship_snapshot_fund",
        "stewardship_source_fund",
        "stewardship_snapshot_family",
        "stewardship_source_family",
        "django_migrations",
        "django_session",
        "socialaccount_socialapp",
        "stewardship_oauth_consumption",
        "stewardship_auth_incident",
        "stewardship_limiter_health",
        "stewardship_configuration_version",
        "stewardship_parish",
        "stewardship_applied_integration",
        "stewardship_domain_rule",
        "stewardship_address_rule",
        "stewardship_address_grant",
        "stewardship_ministry_assignment",
        "stewardship_ministry_activity",
        "stewardship_content_version",
        "stewardship_portal_user",
        "stewardship_assignment_overlay",
        "stewardship_policy_security_event",
        "stewardship_policy_security_ack",
        "stewardship_policy_epoch",
        "stewardship_admin_revocation",
        "stewardship_config_request",
        "stewardship_config_checkpoint",
        "stewardship_system_configuration",
        "stewardship_config_activation",
        "stewardship_secret_request",
        "stewardship_secret_checkpoint",
        "stewardship_credential_consumer_ack",
        "stewardship_portal_session",
        "stewardship_campaign_configuration",
        "stewardship_campaign",
        "stewardship_schedule_revision",
        "stewardship_credential_deployment",
        "stewardship_credential_key_state",
        "stewardship_family_campaign",
        "stewardship_family_eligibility",
        "stewardship_family_code_mac",
        "stewardship_family_token_generation",
        "stewardship_family_token",
        "stewardship_rehearsal_epoch",
        "stewardship_campaign_credentials",
        "stewardship_rehearsal_credential",
        "stewardship_rehearsal_code_mac",
        "stewardship_rehearsal_reservation",
        "stewardship_family_session",
        "stewardship_campaign_work_gate",
        "stewardship_campaign_transition",
        "stewardship_runtime_transition",
        "stewardship_campaign_config_intent",
        "stewardship_campaign_control",
        "stewardship_campaign_config_abort",
        "stewardship_campaign_boundary",
        "stewardship_activation_catchup",
        "stewardship_catchup_failure",
        "stewardship_catchup_checkpoint",
        "stewardship_schedule_definition",
        "stewardship_schedule_selection",
        "stewardship_schedule_occurrence",
        "stewardship_occurrence_transition",
        "stewardship_schedule_fulfillment",
        "stewardship_schedule_work_summary",
        "stewardship_restore_delivery_hold",
        "stewardship_restore_hold_resolution",
        "stewardship_postclose_resolution",
        "stewardship_task_run",
        "stewardship_family_mail_preparation",
        "stewardship_task_event",
        "stewardship_audit_event",
        "stewardship_audit_context",
        "stewardship_operational_log",
    ]
)

WEB_INSERT_TABLES = frozenset(
    [
        "stewardship_setup_attempt",
        "stewardship_setup_draft_section",
        "stewardship_branding_bundle",
        "stewardship_branding_asset",
        "stewardship_provider_context",
        "django_session",
        "stewardship_oauth_consumption",
        "stewardship_auth_incident",
        "stewardship_limiter_health",
        "stewardship_portal_user",
        "stewardship_portal_session",
        "stewardship_config_request",
        "stewardship_config_checkpoint",
        "stewardship_secret_request",
        "stewardship_secret_checkpoint",
        "stewardship_credential_consumer_ack",
        "stewardship_family_session",
        "stewardship_audit_event",
        "stewardship_audit_context",
        "stewardship_operational_log",
        "stewardship_task_run",
        "stewardship_task_event",
        "stewardship_campaign_work_gate",
        "stewardship_campaign_transition",
        "stewardship_runtime_transition",
        "stewardship_campaign_config_intent",
        "stewardship_campaign_control",
        "stewardship_campaign_config_abort",
        "stewardship_activation_catchup",
    ]
)

WEB_UPDATE_TABLES = frozenset(
    [
        "stewardship_setup_attempt",
        "stewardship_setup_draft_section",
        "stewardship_branding_bundle",
        "django_session",
        "stewardship_auth_incident",
        "stewardship_limiter_health",
        "stewardship_portal_user",
        "stewardship_portal_session",
        "stewardship_config_request",
        "stewardship_secret_request",
        "stewardship_family_session",
        "stewardship_system_configuration",
        "stewardship_campaign",
        "stewardship_campaign_work_gate",
        "stewardship_campaign_control",
        "stewardship_task_run",
        "stewardship_activation_catchup",
    ]
)

# Only response-guard and fresh Admin authorization reads exist in this phase.
# Report owners add their data explicitly; credential/session-link inventories,
# audit payloads and worker queues do not belong to the streaming login.
DOWNLOAD_READ_TABLES = frozenset(
    {
        "stewardship_campaign",
        "stewardship_system_configuration",
        "stewardship_configuration_version",
        "stewardship_applied_integration",
        "stewardship_campaign_configuration",
        "stewardship_schedule_revision",
        "stewardship_parish",
        "stewardship_domain_rule",
        "stewardship_address_rule",
        "stewardship_address_grant",
        "stewardship_ministry_assignment",
        "stewardship_ministry_activity",
        "stewardship_content_version",
        "stewardship_portal_user",
        "stewardship_assignment_overlay",
        "stewardship_policy_epoch",
        "stewardship_admin_revocation",
        "stewardship_portal_session",
    }
)


def _identity_role(role, target):
    """Normalize both resolvers identically and reject ignored qualifiers."""
    from .deployment import SECRET_NAMES

    if role != "download":
        try:
            role = ServiceRole(role)
        except (TypeError, ValueError):
            raise ConfigError("Unknown runtime database identity.") from None
    if role is ServiceRole.CREDENTIAL_INSTALLER:
        if target not in SECRET_NAMES - {"handoff_private"}:
            raise ConfigError("A recognized installer target is required.")
    elif target is not None:
        raise ConfigError("Only credential installers accept a target.")
    return role


def runtime_grants(role, *, target=None):
    """Return fresh table/column maps so callers cannot broaden the shared policy."""
    from .accounts.configuration_service import (
        CONFIGURATION_COLUMNS,
        CONFIGURATION_GRANTS,
    )
    from .accounts.credential_database import installer_permissions
    from .accounts.secret_models import SECRET_TARGETS

    role = _identity_role(role, target)

    if role is ServiceRole.MAIL_DISPATCH:
        from .accounts.setup_mail_grants import mail_runtime_grants

        tables, columns = mail_runtime_grants()
        tables["stewardship_delivery_message_release"] = {"SELECT"}
        tables["stewardship_postclose_current"] = {"SELECT"}
        columns.setdefault("stewardship_postclose_resolution", {}).setdefault(
            "SELECT", set()
        ).update({"id", "campaign_id", "mode", "obligation_key", "occurrence_id"})
        return tables, columns
    if role in {ServiceRole.WORKER, ServiceRole.SCHEDULER}:
        from .jobs.grants import task_runtime_grants

        tables, columns = task_runtime_grants(role)
        tables["stewardship_delivery_message_release"] = {"SELECT"}
        tables["stewardship_postclose_current"] = {"SELECT"}
        columns.setdefault("stewardship_postclose_resolution", {}).setdefault(
            "SELECT", set()
        ).update({"id", "campaign_id", "mode", "obligation_key", "occurrence_id"})
        if role is ServiceRole.WORKER:
            tables.setdefault("stewardship_delivery_pause_hold", set()).add("SELECT")
        return tables, columns
    if role is ServiceRole.CONFIG_INSTALLER:
        return {table: set(grants) for table, grants in CONFIGURATION_GRANTS.items()}, {
            table: {privilege: set(names) for privilege, names in columns.items()}
            for table, columns in CONFIGURATION_COLUMNS.items()
        }
    if role is ServiceRole.CREDENTIAL_INSTALLER:
        if target not in SECRET_TARGETS:
            raise ConfigError("A recognized installer target is required.")
        installer_tables, installer_metadata = installer_permissions(target)
        tables = {
            table: set(grants)
            for table, grants in installer_tables.items()
            if table not in installer_metadata
        }
        columns = {
            table: {"SELECT": set(names)} for table, names in installer_metadata.items()
        }
        return tables, columns
    if role in {ServiceRole.BOOTSTRAP, ServiceRole.ADMIN_RECOVERY}:
        from .runtime_database import offline_grants

        return offline_grants(role), {}
    if role not in {ServiceRole.WEB, "download"}:
        raise ConfigError(
            "This service's runtime database authority is not implemented."
        )
    tables = {
        table: {"SELECT"}
        for table in (
            WEB_READ_TABLES if role is ServiceRole.WEB else DOWNLOAD_READ_TABLES
        )
    }
    tables["stewardship_download_policy"] = {"SELECT"}
    # The short pre-stream FOR SHARE claim needs one UPDATE privilege; its SQL
    # guard rejects an id-only update, and the actual stream is READ ONLY.
    columns = {"stewardship_download_policy": {"UPDATE": {"id"}}}
    if role is ServiceRole.WEB:
        # Login incidents and critical diagnostics retain atomic safe intent,
        # never rendered messages, recipients or provider mutation authority.
        tables["stewardship_ops_incident"] = {"SELECT", "INSERT"}
        # An Administrator's acknowledgement of a security event is one
        # append-only row; the event itself stays readable and immutable.
        tables["stewardship_policy_security_ack"] = {"SELECT", "INSERT"}
        columns["stewardship_ops_incident"] = {
            "UPDATE": {
                "signal_level",
                "action",
                "version",
                "actor_id",
                "correlation_id",
            }
        }
        # Setup progress observes live source ownership, never mutates the lease.
        columns["stewardship_source_lease"] = {
            "SELECT": {
                "owner_id",
                "task_fence",
                "worker_id",
                "expires_at",
                "heartbeat_at",
            }
        }
        # Readiness verifies the full-observation anchor using permanent public
        # manifest metadata. Cursor load evidence contains completeness counts,
        # not source payloads; validation/content/corpus columns remain private.
        columns["stewardship_source_snapshot"] = {
            "SELECT": {
                "id",
                "organization_id",
                "kind",
                "state",
                "started_at",
                "completed_at",
                "promoted_at",
                "cursor",
            }
        }
        # Login holds these current-epoch/credential rows against rotation and
        # cleanup. PostgreSQL requires an UPDATE privilege for a row lock;
        # id-only grants permit locking, not guarded credential mutation.
        for table in (
            "stewardship_credential_deployment",
            "stewardship_campaign_credentials",
            "stewardship_rehearsal_credential",
            "stewardship_family_token",
            "stewardship_family_token_generation",
        ):
            columns[table] = {"UPDATE": {"id"}}
        columns["stewardship_family_campaign"] = {
            "UPDATE": {"last_activity_at", "version"}
        }
        # The statement-level Family update trigger executes this UPDATE even
        # for activity-only changes; eligibility predicates and row guards still
        # prevent web from changing source-population authority.
        columns["stewardship_campaign_credentials"]["UPDATE"].update(
            {"population_dirty", "version"}
        )
        for privilege, names in (
            ("INSERT", WEB_INSERT_TABLES),
            ("UPDATE", WEB_UPDATE_TABLES),
        ):
            for table in names:
                tables.setdefault(table, set()).add(privilege)
        tables["django_session"].add("DELETE")
        tables["stewardship_sealed_credential_staging"] = {"INSERT"}
        columns["stewardship_sealed_credential_staging"] = {
            "SELECT": {"reference", "request_id", "target", "fingerprint"}
        }
        tables["stewardship_setup_sealed_credential"] = {"INSERT"}
        columns["stewardship_setup_sealed_credential"] = {
            "SELECT": {
                "id",
                "created_at",
                "updated_at",
                "version",
                "actor_id",
                "correlation_id",
                "attempt_id",
                "target",
                "fingerprint",
                "settings",
                "scrubbed_at",
            },
            "UPDATE": {
                "ciphertext",
                "fingerprint",
                "settings",
                "scrubbed_at",
                "actor_id",
                "correlation_id",
                "version",
            },
        }
        from .accounts.setup_exchange_grants import (
            add_exchange_cleanup_grants,
            add_web_setup_catalog_grants,
        )

        add_exchange_cleanup_grants(columns)
        add_web_setup_catalog_grants(tables, columns)
        from .accounts.setup_mail_grants import add_setup_mail_cleanup_grants

        add_setup_mail_cleanup_grants(tables, columns, read_payload=True)
        tables["stewardship_setup_mail_delivery"].add("INSERT")
        tables["stewardship_campaign_mail_test"].add("INSERT")
        tables["stewardship_setup_slack_delivery"].add("INSERT")
        tables["stewardship_setup_config_intent"].add("INSERT")
        tables["stewardship_setup_readiness_binding"] = {"SELECT", "INSERT"}
        tables["stewardship_setup_credential_install"] = {"SELECT"}
        from .responses.grants import add_response_web_grants

        add_response_web_grants(tables, columns)
        # Verified clearance is append-only and independently admitted by SQL;
        # its trigger owns the one-Family eligibility effect, not the web login.
        tables["stewardship_recipient_refusal"] = {"SELECT"}
        tables["stewardship_recipient_resolution"] = {"SELECT", "INSERT"}
        tables["stewardship_delivery_resolution"] = {"SELECT", "INSERT"}
        from .jobs.family_dispatch_grants import METADATA_FIELDS
        from .reports.digest_grants import add_digest_web_grants
        from .reports.weekly_grants import add_weekly_web_grants

        add_digest_web_grants(tables, columns)
        add_weekly_web_grants(tables, columns)

        columns["stewardship_outbox_message"] = {
            "SELECT": set(METADATA_FIELDS) | {"created_at", "updated_at", "finished_at"}
        }
        columns["stewardship_outbox_event"] = {
            "SELECT": {
                "id",
                "message_id",
                "created_at",
                "version",
                "state",
                "action",
                "attempt",
                "actor_id",
                "reason",
                "run_id",
            }
        }
        from .campaigns.cleanup_grants import (
            add_cleanup_web_commands,
            add_cleanup_web_reads,
        )

        add_cleanup_web_reads(tables, columns)
        add_cleanup_web_commands(tables, columns)
        tables["stewardship_activation_impact"] = {"SELECT"}
        tables["stewardship_production_confirmation"] = {"SELECT", "INSERT"}
        tables["stewardship_production_withdrawal"] = {"SELECT", "INSERT"}
        tables["stewardship_withdrawal_inventory"] = {"SELECT"}
        tables["stewardship_delivery_control"] = {"SELECT", "INSERT"}
        tables["stewardship_delivery_control_inventory"] = {"SELECT"}
        tables["stewardship_delivery_control_health"] = {"SELECT"}
        tables["stewardship_delivery_family_recovery_summary"] = {"SELECT"}
        tables["stewardship_delivery_digest_recovery_summary"] = {"SELECT"}
        tables["stewardship_delivery_closed_coverage_summary"] = {"SELECT"}
        for table in (
            "stewardship_production_tokens",
            "stewardship_production_token_cancel",
        ):
            tables[table] = {"SELECT", "INSERT"}
    from .reports.export_grants import add_export_grants

    add_export_grants(
        tables, columns, role=role.value if role is ServiceRole.WEB else role
    )
    if role == "download":
        from .reports.digest_grants import add_digest_download_grants

        add_digest_download_grants(columns)
    if role is ServiceRole.WEB:
        tables["stewardship_delivery_message_release"] = {"SELECT"}
        tables["stewardship_postclose_current"] = {"SELECT"}
    return tables, columns


def login_name(role, *, target=None):
    """Resolve only implemented runtime identities, never caller-supplied SQL names."""
    from .deployment import SECRET_NAMES

    role = _identity_role(role, target)

    if role is ServiceRole.CREDENTIAL_INSTALLER:
        if target not in SECRET_NAMES - {"handoff_private"}:
            raise ConfigError("A recognized installer target is required.")
    elif (
        role
        not in {
            ServiceRole.WEB,
            ServiceRole.WORKER,
            ServiceRole.SCHEDULER,
            ServiceRole.MAIL_DISPATCH,
            ServiceRole.CONFIG_INSTALLER,
            ServiceRole.BOOTSTRAP,
            ServiceRole.ADMIN_RECOVERY,
            "download",
        }
        or target is not None
    ):
        raise ConfigError("Unknown runtime database identity.")
    suffix = (
        "credential_" + target
        if role is ServiceRole.CREDENTIAL_INSTALLER
        else str(role).replace("-", "_")
    )
    return "pk_stewardship_" + suffix


def admit_runtime_database(configuration):
    """Validate actual login/table/column authority before any online operation."""
    from .accounts.configuration_service import admit_configuration_database
    from .accounts.credential_database import (
        _identity,
        admit_grants,
        admit_installer_database,
        admit_web_staging_grants,
    )
    from .runtime_database import (
        require_capacity,
        require_current_schema,
        require_no_temporary_authority,
        require_role_capacity,
    )

    role = configuration.service_role
    if role is ServiceRole.CREDENTIAL_INSTALLER:
        admit_installer_database(configuration.credential_target)
    elif role is ServiceRole.CONFIG_INSTALLER:
        admit_configuration_database()
    elif role in {
        ServiceRole.WEB,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
    }:
        _identity(login_name(role))
        tables, columns = runtime_grants(role)
        allowed = {table: set(grants) for table, grants in tables.items()}
        for table, grants in columns.items():
            allowed.setdefault(table, set()).update(grants)
        admit_grants(allowed)
        if role is ServiceRole.WEB:
            admit_web_staging_grants()
        from django.db import connection

        admit_columns(connection, tables, columns)
    else:
        raise ConfigError("This online runtime is not implemented.")
    require_no_temporary_authority()
    require_current_schema()
    require_capacity(configuration)
    require_role_capacity(configuration)


def admit_columns(database, tables, columns):
    """A column-only grant must not hide a whole-table privilege of the same kind."""
    for table, privileges in columns.items():
        for privilege, allowed in privileges.items():
            if privilege in tables.get(table, set()):
                continue
            with database.cursor() as cursor:
                cursor.execute(
                    "SELECT a.attname FROM pg_attribute a JOIN pg_class c "
                    "ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relname=%s AND a.attnum>0 "
                    "AND NOT a.attisdropped "
                    "AND has_column_privilege(current_user,c.oid,a.attnum,%s)",
                    [table, privilege],
                )
                if {row[0] for row in cursor.fetchall()} - allowed:
                    raise ConfigError("Runtime database column grants are excessive.")


def admit_download_database(configuration, database):
    """The dedicated stream role cannot mutate application data or resize its cap."""
    from .accounts.credential_database import _identity, admit_grants
    from .runtime_database import require_no_temporary_authority

    _identity(login_name("download"), database=database)
    require_no_temporary_authority(database)
    tables, columns = runtime_grants("download")
    allowed = {table: set(grants) for table, grants in tables.items()}
    for table, grants in columns.items():
        allowed.setdefault(table, set()).update(grants)
    admit_grants(allowed, database=database)
    admit_columns(database, tables, columns)
    budget = configuration.runtime_budget
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT capacity FROM public.stewardship_download_policy WHERE id=1"
        )
        if cursor.fetchone() != (budget.download_capacity,):
            raise ConfigError("Download capacity requires offline reconciliation.")
        cursor.execute("SELECT rolconnlimit FROM pg_roles WHERE rolname=current_user")
        expected = (
            budget.replicas
            * budget.rollout_overlap
            * budget.web_processes
            * budget.download_pool_per_process
        )
        if cursor.fetchone() != (expected,):
            raise ConfigError("Download SQL connection limit differs from its budget.")
