"""Explicit offline commands, never available through web or background queues."""

import json
import logging
import secrets
from importlib import import_module
from uuid import UUID, uuid4

from parishkit.config import ConfigError

from .deployment import ServiceRole, load_deployment
from .offline_boundaries import admit_offline_service
from .runtime_paths import RuntimeLayout
from .startup_interlock import StartupLease


def configure_operator_database(configuration):
    """Configure Django once for a non-HTTP process with no provider/session runtime."""
    import django
    from django.conf import settings

    from .runtime_database import database_settings, profile_settings

    if settings.configured:
        raise ConfigError("Operator startup requires a fresh process.")
    base = import_module("parishkit.stewardship.settings.base")
    values = {key: getattr(base, key) for key in dir(base) if key.isupper()}
    values.update(
        SECRET_KEY=secrets.token_urlsafe(48),
        DATABASES={"default": database_settings(configuration)},
        STEWARDSHIP_OPERATIONAL_POLICY=configuration.operational_alerts,
        **profile_settings(configuration),
    )
    settings.configure(**values)
    django.setup()


def _uuid(value):
    """Reject missing/noncanonical confirmations without echoing private input."""
    try:
        result = UUID(value)
        if str(result) != value:
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError):
        raise ConfigError("An explicit canonical UUID is required.") from None


def bootstrap_command(configuration, *, phase, deployment_id, admin_email):
    """Run one phase under the bootstrap-only kernel and lifecycle boundary."""
    from .bootstrap import (
        BootstrapIdentity,
        materialize_initial_files,
        provision_initial_files,
    )

    if admit_offline_service(configuration) is not ServiceRole.BOOTSTRAP:
        raise ConfigError("Bootstrap requires its explicit operator profile.")
    identity = BootstrapIdentity(_uuid(deployment_id), admin_email)
    if phase == "prepare":
        version = provision_initial_files(configuration, identity)
    elif phase == "import":
        from .runtime_database import admit_offline_database

        configure_operator_database(configuration)
        admit_offline_database(configuration)
        version = materialize_initial_files(configuration, identity)
    else:
        raise ConfigError("An explicit bootstrap phase is required.")
    return {
        "bootstrap_phase": phase,
        "configuration_version": str(version.version_id),
        "completed": True,
        "setup_complete": False,
    }


def migrate_command(configuration):
    """Run first-deployment migrations once, never race online or imply upgrade safety.

    Configured-deployment upgrades remain held until OPS-05 supplies verified
    backup evidence; directly invoking migrate must not bypass that future owner.
    """
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    if admit_offline_service(configuration) is not ServiceRole.MIGRATION:
        raise ConfigError("Migrations require their explicit operator profile.")
    with StartupLease(RuntimeLayout(configuration).interlock, offline=True) as lease:
        configure_operator_database(configuration)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,"
                "rolcreaterole,rolreplication,rolinherit FROM pg_roles "
                "WHERE rolname=current_user"
            )
            row = cursor.fetchone()
            if (
                row is None
                or row[:2] != ("pk_stewardship_migration",) * 2
                or any(row[2:])
            ):
                raise ConfigError(
                    "Migrations require their isolated schema-owner login."
                )
            cursor.execute(
                "SELECT to_regclass('public.stewardship_system_configuration')"
            )
            if cursor.fetchone()[0] is not None:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM "
                    "public.stewardship_system_configuration)"
                )
                from .backup import require_recent_backup

                # The v1 reduction of the deferred upgrade admission: a
                # configured deployment migrates only behind a recent backup.
                if cursor.fetchone()[0]:
                    require_recent_backup(cursor)
        from .runtime_database import require_role_capacity

        require_role_capacity(configuration)
        loader = MigrationLoader(connection)
        loader.check_consistent_history(connection)
        if set(loader.applied_migrations) - set(loader.disk_migrations):
            raise ConfigError("This application cannot migrate a newer database.")
        lease.check()
        call_command("migrate", interactive=False, verbosity=0)
        lease.check()
        from .runtime_database import require_current_schema

        require_current_schema()
        # This command has proved there is no configured deployment and holds
        # offline exclusion. Establish the selected initial budget through the
        # existing version/active-download SQL guard, never by disabling it.
        capacity = configuration.runtime_budget.download_capacity
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.stewardship_download_policy "
                "SET capacity=%s,version=version+1 WHERE id=1 AND capacity<>%s",
                [capacity, capacity],
            )
            cursor.execute(
                "SELECT capacity FROM public.stewardship_download_policy WHERE id=1"
            )
            if cursor.fetchone() != (capacity,):
                raise ConfigError("Initial download capacity was not established.")
        connection.close()
    return {"migrations_current": True}


def recover_admin_command(
    configuration,
    *,
    deployment_id,
    operation_id,
    target_email,
    confirmed_email,
    operator_name,
    reason,
):
    """Apply only explicit additive recovery, retaining the ordinary Google login."""
    from .accounts.authority import AuthorityStore
    from .accounts.configuration_schema import validate_sections

    if admit_offline_service(configuration) is not ServiceRole.ADMIN_RECOVERY:
        raise ConfigError("Admin recovery requires its explicit operator profile.")
    configure_operator_database(configuration)
    from .accounts.operator_recovery import recover_admin
    from .runtime_database import admit_offline_database

    admit_offline_database(configuration)
    store = AuthorityStore(configuration.paths["authority"], validate_sections)
    result = recover_admin(
        store,
        deployment_id=_uuid(deployment_id),
        operation_id=_uuid(operation_id),
        target_email=target_email,
        confirmed_email=confirmed_email,
        operator_name=operator_name,
        reason=reason,
        correlation_id=uuid4(),
        offline_interlock=lambda: StartupLease(
            RuntimeLayout(configuration).interlock, offline=True
        ),
        before_apply=lambda preview: print(
            json.dumps({"recovery_preview": preview}, sort_keys=True)
        ),
    )
    if result.state != "applied":
        raise ConfigError("Admin recovery did not reach an applied durable receipt.")
    return {
        "recovery_applied": True,
        "operation_id": operation_id,
        "ordinary_google_login_required": True,
    }


def preview_recovery_command(configuration, *, deployment_id, target_email):
    """Display coherent current rules and the exact proposed change without writing."""
    if admit_offline_service(configuration) is not ServiceRole.ADMIN_RECOVERY:
        raise ConfigError("Recovery preview requires its explicit operator profile.")
    with StartupLease(RuntimeLayout(configuration).interlock, offline=True):
        configure_operator_database(configuration)
        from .accounts.authority import AuthorityStore
        from .accounts.configuration_installation import coherent_configuration
        from .accounts.configuration_schema import validate_sections
        from .accounts.operator_recovery import recovery_preview
        from .accounts.request_admission import intake_base
        from .runtime_database import admit_offline_database

        admit_offline_database(configuration)
        runtime = coherent_configuration(
            AuthorityStore(configuration.paths["authority"], validate_sections)
        )
        if runtime.pk != _uuid(deployment_id):
            raise ConfigError("The confirmed deployment does not match.")
        _, version = intake_base(runtime.active_configuration.digest)
        return {"recovery_preview": recovery_preview(version, runtime.pk, target_email)}


def revoke_automation_command(configuration, *, reason):
    """End every live Admin automation session, offline (restore or operator).

    Runs as the offline admin-recovery login, with every online service
    stopped, after a restored database and before ``web`` starts (see the
    Admin automation specification, "Session rules"). ``reason`` is
    ``restore`` or ``revoked_by_operator``. Prints only a count.
    """
    if reason not in {"restore", "revoked_by_operator"}:
        raise ConfigError("The reason is restore or revoked_by_operator.")
    if admit_offline_service(configuration) is not ServiceRole.ADMIN_RECOVERY:
        raise ConfigError("Revocation requires the admin-recovery profile.")
    with StartupLease(RuntimeLayout(configuration).interlock, offline=True):
        configure_operator_database(configuration)
        from django.db import connection

        from .accounts.automation_sessions import revoke_all_offline
        from .runtime_database import admit_offline_database

        try:
            admit_offline_database(configuration)
            count = revoke_all_offline(reason, correlation_id=uuid4())
        finally:
            connection.close()
    return {"automation_sessions_revoked": count, "end_reason": reason}


def parse_backup_time(value):
    """Read the restored backup's time: its set's name or an ISO 8601 instant.

    A set is named for its start in UTC (``20261007T020000Z``); an ISO 8601
    value must carry its offset, so a local time is never guessed.
    """
    from datetime import UTC, datetime

    if not isinstance(value, str):
        raise ConfigError("--backup-at is required (the backup set's name).")
    try:
        if len(value) == 16 and value.endswith("Z") and value[8] == "T":
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        instant = datetime.fromisoformat(value)
    except ValueError:
        raise ConfigError("--backup-at must be a set name or ISO 8601 time.") from None
    if instant.tzinfo is None:
        raise ConfigError("--backup-at needs a UTC offset or a trailing Z.")
    return instant


def restore_begin_command(configuration, *, backup_at, reason):
    """Close the site for a restore review (#537), as the admin-recovery login.

    Run after the database is restored and before web starts. Family access,
    Family mail and ordinary background work stay stopped until an
    Administrator releases the site from the Restore review page. No Family
    code or link changes. Prints the new review's id and the backup time.
    """
    instant = parse_backup_time(backup_at)
    if admit_offline_service(configuration) is not ServiceRole.ADMIN_RECOVERY:
        raise ConfigError("A restore review starts from the admin-recovery profile.")
    with StartupLease(RuntimeLayout(configuration).interlock, offline=True):
        configure_operator_database(configuration)
        from django.db import connection

        from .campaigns.restore_review import begin_review
        from .runtime_database import admit_offline_database

        try:
            admit_offline_database(configuration)
            transition = begin_review(
                backup_at=instant,
                reason=reason or "restore",
                correlation_id=uuid4(),
            )
        finally:
            connection.close()
    return {
        "restore_review_started": True,
        "restore_id": str(transition.restore_id),
        "backup_at": instant.isoformat(),
    }


def execute_operator(args):
    """Report reviewed status fields only; arbitrary exception messages stay private."""
    import sys

    try:
        if args.config is None:
            raise ConfigError("An explicit operator configuration file is required.")
        configuration = load_deployment(args.config)
        if args.command == "bootstrap":
            result = bootstrap_command(
                configuration,
                phase=args.phase,
                deployment_id=args.deployment_id,
                admin_email=args.admin_email,
            )
        elif args.command == "migrate":
            result = migrate_command(configuration)
        elif args.command in {"database-roles", "database-grants"}:
            from .database_provisioning import provision_grants, provision_roles

            if (
                admit_offline_service(configuration)
                is not ServiceRole.DATABASE_PROVISION
            ):
                raise ConfigError(
                    "SQL provisioning requires its operator-only profile."
                )
            with StartupLease(RuntimeLayout(configuration).interlock, offline=True):
                configure_operator_database(configuration)
                procedure = (
                    provision_roles
                    if args.command == "database-roles"
                    else provision_grants
                )
                result = procedure(configuration, _uuid(args.confirm_deployment))
        elif args.command == "preview-admin-recovery":
            result = preview_recovery_command(
                configuration,
                deployment_id=args.confirm_deployment,
                target_email=args.target_email,
            )
        elif args.command == "revoke-automation-sessions":
            result = revoke_automation_command(configuration, reason=args.reason)
        elif args.command == "restore-begin":
            result = restore_begin_command(
                configuration, backup_at=args.backup_at, reason=args.reason
            )
        elif args.command == "recover-admin":
            result = recover_admin_command(
                configuration,
                deployment_id=args.confirm_deployment,
                operation_id=args.operation_id,
                target_email=args.target_email,
                confirmed_email=args.confirm_email,
                operator_name=args.operator_name,
                reason=args.reason,
            )
        else:
            raise ConfigError("Unsupported operator command.")
    except Exception as error:
        from .backup import RecentBackupRequired

        if type(error) is RecentBackupRequired:
            # The one refusal whose remedy is always the same: name it in the
            # process log with a reviewed event the log formatter keeps.
            from .observability import Event, FailureKind, emit

            emit(
                Event.STARTUP_REJECTED,
                level=logging.ERROR,
                failure_kind=FailureKind.BACKUP_REQUIRED,
            )
        print(
            "ERROR: offline operation refused or failed; verify profile, inputs, "
            "permissions, interlock and database readiness",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0
