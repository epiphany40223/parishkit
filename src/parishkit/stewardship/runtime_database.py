"""Operational PostgreSQL settings, migration checks and isolated login admission.

No role string grants SQL access. Each online startup uses its actual authenticated
login and privileges; migration/initialization/recovery have separate offline
password files and never lend their extra authority to online services.
"""

from parishkit.config import ConfigError

from .accounts.key_files import read_private
from .deployment import ServiceRole


def database_settings(configuration):
    """Only an individual owner-only password file supplies a database secret."""
    db = configuration.postgres
    if db.password_file is None:
        raise ConfigError("An individual database password file is required.")
    try:
        password = read_private(db.password_file).decode("utf-8").removesuffix("\n")
        if not password or any(ord(char) < 32 for char in password):
            raise ValueError
    except (UnicodeError, ValueError):
        raise ConfigError("The database password file is invalid.") from None
    return {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": db.host,
        "PORT": db.port,
        "NAME": db.name,
        "USER": db.user,
        "PASSWORD": password,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        "OPTIONS": {"connect_timeout": db.connect_timeout},
    }


def require_current_schema():
    """Reject pending, inconsistent or newer schemas before starting any service."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    loader = executor.loader
    loader.check_consistent_history(connection)
    unknown = set(loader.applied_migrations) - set(loader.disk_migrations)
    if unknown or executor.migration_plan(loader.graph.leaf_nodes()):
        raise ConfigError("Database migrations do not match this application image.")


def require_capacity(configuration):
    """Verify live server limits and retain separate non-download connection classes."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('max_connections')::int, "
            "current_setting('superuser_reserved_connections')::int + "
            "current_setting('reserved_connections')::int"
        )
        maximum, reserved = cursor.fetchone()
    configuration.runtime_budget.validate_database(maximum=maximum, reserved=reserved)


def require_role_capacity(configuration):
    """Refuse a YAML process budget that no longer matches its actual SQL role."""
    from django.db import connection

    from .database_provisioning import role_limit

    with connection.cursor() as cursor:
        cursor.execute("SELECT rolconnlimit FROM pg_roles WHERE rolname=current_user")
        if cursor.fetchone() != (
            role_limit(configuration, configuration.service_role),
        ):
            raise ConfigError("Runtime SQL connection limit differs from its budget.")


def offline_grants(role):
    """The same config-writer engine has narrowly scoped offline SQL identities.

    Recovery needs INSERT of an operator request and session revocation, which
    the online config-installer explicitly must not possess. Bootstrap adds only
    creation of the initial singleton; schema migrations use the separate owner.
    """
    from .accounts.configuration_service import CONFIGURATION_GRANTS

    grants = {table: set(values) for table, values in CONFIGURATION_GRANTS.items()}
    if role is ServiceRole.BOOTSTRAP:
        grants["stewardship_system_configuration"].add("INSERT")
    elif role is ServiceRole.ADMIN_RECOVERY:
        grants["stewardship_config_request"].add("INSERT")
        grants["stewardship_portal_session"] = {"SELECT", "UPDATE"}
    else:
        raise ConfigError("This role has no offline configuration grants.")
    return grants


def admit_offline_database(configuration):
    """Offline host confirmation never bypasses SQL identity or schema admission."""
    from .accounts.credential_database import _identity, admit_grants

    role = configuration.service_role
    _identity("pk_stewardship_" + role.value.replace("-", "_"))
    admit_grants(offline_grants(role))
    require_no_temporary_authority()
    require_current_schema()
    require_capacity(configuration)
    require_role_capacity(configuration)


def require_no_temporary_authority(database=None):
    """Operational roles cannot create temp objects that shadow legacy SQL names."""
    if database is None:
        from django.db import connection

        database = connection
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT has_database_privilege(current_user,current_database(),'TEMP')"
        )
        if cursor.fetchone() != (False,):
            raise ConfigError(
                "Runtime database temporary-object authority is forbidden."
            )
