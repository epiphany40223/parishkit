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
        "stewardship_portal_user",
        "stewardship_assignment_overlay",
        "stewardship_policy_security_event",
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
        "stewardship_restore_delivery_hold",
        "stewardship_restore_hold_resolution",
        "stewardship_postclose_resolution",
        "stewardship_task_run",
        "stewardship_task_event",
        "stewardship_audit_event",
        "stewardship_audit_context",
        "stewardship_operational_log",
    ]
)

WEB_INSERT_TABLES = frozenset(
    [
        "django_session",
        "stewardship_oauth_consumption",
        "stewardship_auth_incident",
        "stewardship_limiter_health",
        "stewardship_portal_user",
        "stewardship_portal_session",
        "stewardship_assignment_overlay",
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
        "django_session",
        "stewardship_auth_incident",
        "stewardship_limiter_health",
        "stewardship_portal_user",
        "stewardship_portal_session",
        "stewardship_assignment_overlay",
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
    from .accounts.configuration_service import CONFIGURATION_GRANTS
    from .accounts.credential_database import INSTALLER_GRANTS, INSTALLER_METADATA
    from .accounts.secret_models import SECRET_TARGETS

    role = _identity_role(role, target)

    if role is ServiceRole.CONFIG_INSTALLER:
        return {
            table: set(grants) for table, grants in CONFIGURATION_GRANTS.items()
        }, {}
    if role is ServiceRole.CREDENTIAL_INSTALLER:
        if target not in SECRET_TARGETS:
            raise ConfigError("A recognized installer target is required.")
        tables = {
            table: set(grants)
            for table, grants in INSTALLER_GRANTS.items()
            if table not in INSTALLER_METADATA
        }
        columns = {
            table: {"SELECT": set(names)} for table, names in INSTALLER_METADATA.items()
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
        # Login holds these current-epoch/credential rows against rotation and
        # cleanup. PostgreSQL requires an UPDATE privilege for a row lock;
        # id-only grants permit locking, not guarded credential mutation.
        for table in (
            "stewardship_credential_deployment",
            "stewardship_campaign_credentials",
            "stewardship_rehearsal_credential",
            "stewardship_family_token",
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
    elif role is ServiceRole.WEB:
        _identity(login_name(role))
        tables, columns = runtime_grants(role)
        allowed = {table: set(grants) for table, grants in tables.items()}
        for table, grants in columns.items():
            allowed.setdefault(table, set()).update(grants)
        admit_grants(allowed)
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
