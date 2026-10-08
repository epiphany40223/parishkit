"""Operational PostgreSQL settings, migration checks and isolated login admission.

No role string grants SQL access. Each online startup uses its actual authenticated
login and privileges; migration/initialization/recovery have separate offline
password files and never lend their extra authority to online services.
"""

import logging
import math
import time

from parishkit.config import ConfigError

from .accounts.key_files import read_private
from .deployment import ServiceRole
from .observability import Event, debug_logging_enabled, emit

# How long, in all, an online service waits at startup for its database
# (#453): first for a connection (await_database), then through its SQL
# admission (during_startup). Long enough for PostgreSQL to finish starting
# after a host reboot; each attempt's own connection limit is capped at what
# is left, so the whole wait stays at about this long.
STARTUP_DATABASE_WAIT_SECONDS = 60
# The first pause between attempts, doubled after each failure up to the cap,
# so a database that is ready a moment later is used at once.
STARTUP_RETRY_SECONDS = 0.5
STARTUP_RETRY_CAP_SECONDS = 5
# The reviewed name of that limit in the timeout line (observability).
STARTUP_DATABASE_WAIT = "startup_database_wait"
# When this process's startup wait began (time.monotonic), set by
# await_database; None in a process that never waited (operator commands,
# diagnostics), whose admission therefore never retries.
_startup_started = None


def require_internal_database(configuration):
    """This Compose profile has no external SQL/TLS trust configuration.

    Reject a hand-edited runtime document before libpq can negotiate an
    unverified remote connection. The renderer enforces the same private service
    address; remote databases require a separate verified-TLS deployment profile.
    Literal loopback is also admitted for isolated operator/test connections;
    Compose rendering still requires its exact internal service endpoint.
    """
    local = configuration.postgres.host in {"127.0.0.1", "::1"}
    compose = (
        configuration.postgres.host == "postgres"
        and configuration.postgres.port == 5432
    )
    if not (local or compose):
        raise ConfigError("Operational SQL requires the private Compose database.")


def database_settings(configuration):
    """Only an individual owner-only password file supplies a database secret."""
    require_internal_database(configuration)
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
        "OPTIONS": {"connect_timeout": db.connect_timeout, "sslmode": "disable"},
    }


# libpq's own texts for a server that never answered: refused, unreachable,
# not yet resolvable on the Compose network, gone mid-handshake, or too slow.
NO_REPLY_SIGNALS = (
    "Connection refused",
    "could not translate host name",
    "Name or service not known",
    "Temporary failure in name resolution",
    "server closed the connection unexpectedly",
    "timeout expired",
    "No route to host",
    "Network is unreachable",
    "Connection timed out",
    "Connection reset by peer",
)
# PostgreSQL's replies (in the English the pinned server image uses) that mean
# it answered but cannot take this connection yet: starting up, shutting down
# or recovering (57P03), restarting after an administrator's or a crash's
# termination (57P01, 57P02), or out of connection slots (53300).
NOT_READY_REPLIES = (
    "the database system is starting up",
    "the database system is shutting down",
    "the database system is not yet accepting connections",
    "the database system is not accepting connections",
    "the database system is in recovery mode",
    "terminating connection due to administrator command",
    "terminating connection because of crash of another server process",
    "sorry, too many clients already",
    "remaining connection slots are reserved",
    "too many connections for",
)
# The same conditions when the error carries its SQLSTATE (a failed query).
# Only 53300 (too many connections) of class 53: disk full, out of memory
# and the like are not a starting database and are refused at once.
NOT_READY_STATES = frozenset(
    {"57P01", "57P02", "57P03", "08000", "08003", "08006", "53300"}
)


def database_not_ready(error):
    """Whether a database error means "not available yet", not "refused".

    Only a closed list waits: psycopg's ConnectionTimeout, an error whose
    SQLSTATE is in NOT_READY_STATES, or, since libpq reports a
    failed connection without a SQLSTATE, a message containing one of
    NO_REPLY_SIGNALS (client-side: the server never answered) or
    NOT_READY_REPLIES (the server's own "not yet"). Everything else is a real
    refusal, refused at once: a wrong password, a missing role or database,
    no ``pg_hba`` entry, a client-side programming error, and any text this
    list does not know, such as a server or client speaking another
    language. The text is only matched, never logged.
    """
    import psycopg

    if not isinstance(error, psycopg.OperationalError):
        return False
    if isinstance(error, psycopg.errors.ConnectionTimeout):
        return True
    state = error.sqlstate
    if state is not None:
        return state in NOT_READY_STATES
    message = str(error)
    return any(text in message for text in NO_REPLY_SIGNALS + NOT_READY_REPLIES)


def _debug_failure(error):
    """With debug logging on, record what failed while the service waited."""
    if debug_logging_enabled():
        logging.getLogger("parishkit.stewardship.debug").debug(
            "database not ready at startup", exc_info=error
        )


def _pause_or_give_up(delay, attempt_started):
    """Sleep ``delay`` seconds if the startup budget allows; else say so.

    Returns False when the pause would pass STARTUP_DATABASE_WAIT_SECONDS.
    The timeout line (what, limit, elapsed) is logged at ERROR when the
    failed attempt *started* inside the startup window, because only then did
    the limit stop the wait: an attempt that began at 57 seconds and used its
    whole connection timeout ends past 60 and must still be logged. A later
    admission (a web worker re-forked hours on) started outside the window
    and is an ordinary refusal with nothing waited.
    """
    elapsed = time.monotonic() - _startup_started
    if elapsed + delay <= STARTUP_DATABASE_WAIT_SECONDS:
        time.sleep(delay)
        return True
    if attempt_started - _startup_started <= STARTUP_DATABASE_WAIT_SECONDS:
        emit(
            Event.TASK_TIMED_OUT,
            level=logging.ERROR,
            timeout=STARTUP_DATABASE_WAIT,
            limit_seconds=STARTUP_DATABASE_WAIT_SECONDS,
            elapsed_seconds=round(elapsed),
        )
    return False


def await_database(configuration):
    """Wait, for about STARTUP_DATABASE_WAIT_SECONDS, until this login can connect.

    Every online service starts by admitting its SQL login, grants and schema,
    and any database error there ended the process. Started all at once, after
    a host reboot or possibly under load after a deploy or restore, a service
    could meet a PostgreSQL still starting or too busy to answer; it logged an
    ERROR ``startup_rejected`` and Docker restarted it (#453). This runs first,
    with the service's own login and before Django is configured: one plain
    connection and ``SELECT 1``, retried with a growing pause while the
    failure means "not yet" (database_not_ready). It also starts this
    process's startup budget, which during_startup shares for the admission
    that follows. A real refusal is raised at once, so a misconfiguration is
    still refused loudly; a wait that runs out logs the timeout line first.
    Either is a Django OperationalError with fixed text, so the caller's
    ``startup_rejected`` names ``database_unavailable`` and no server or
    exception text reaches the log.
    """
    import psycopg
    from django.db.utils import OperationalError

    global _startup_started
    settings = database_settings(configuration)
    _startup_started = time.monotonic()
    delay = STARTUP_RETRY_SECONDS
    while True:
        attempt_started = time.monotonic()
        left = STARTUP_DATABASE_WAIT_SECONDS - (attempt_started - _startup_started)
        try:
            with psycopg.connect(
                host=settings["HOST"],
                port=settings["PORT"],
                dbname=settings["NAME"],
                user=settings["USER"],
                password=settings["PASSWORD"],
                sslmode=settings["OPTIONS"]["sslmode"],
                # Never far past the budget; libpq's smallest real limit is 2.
                connect_timeout=min(
                    settings["OPTIONS"]["connect_timeout"], max(2, math.ceil(left))
                ),
                autocommit=True,
            ) as database:
                database.execute("SELECT 1")
            return
        except psycopg.Error as error:
            _debug_failure(error)
            if not database_not_ready(error):
                raise OperationalError(
                    "The database refused this service's connection."
                ) from None
        if not _pause_or_give_up(delay, attempt_started):
            raise OperationalError(
                "The database did not accept a connection in time."
            ) from None
        delay = min(STARTUP_RETRY_CAP_SECONDS, delay * 2)


def during_startup(step):
    """Run one SQL admission step, retrying it while the database is not ready.

    Admission (runtime_grants.admit_runtime_database) runs dozens of catalog
    queries; a database busy right after a deploy or a restore could fail one
    of them even after await_database connected. Only in a process that
    waited at startup, and only within the same budget, a Django
    OperationalError whose underlying error database_not_ready classifies as
    "not yet" closes the connection and runs the whole step again. Any other
    failure, or the same one after the budget, is raised as it was, so a
    refusal is never retried. The step must be safe to repeat: admission only
    reads.
    """
    from django.db import connection
    from django.db.utils import OperationalError

    delay = STARTUP_RETRY_SECONDS
    while True:
        attempt_started = time.monotonic()
        try:
            return step()
        except OperationalError as error:
            if _startup_started is None or not database_not_ready(error.__cause__):
                raise
            _debug_failure(error)
            connection.close()
            if not _pause_or_give_up(delay, attempt_started):
                raise
            delay = min(STARTUP_RETRY_CAP_SECONDS, delay * 2)


def profile_settings(configuration):
    """The Django settings that record the admitted deployment profile.

    Every settings assembly (``configure_web`` and the operator assembly every
    other role uses) spreads this into ``settings.configure``. Source reads
    choose their ParishSoft base URL and helper profile from it, and the LOCAL
    banner reads it too; there is no default anywhere else, so a role whose
    assembly skipped this would fail closed rather than reach the real API.
    """
    values = {"STEWARDSHIP_DEPLOYMENT_PROFILE": configuration.profile.value}
    if configuration.local_smtp_latency_ms:
        # The local rehearsal's modeled provider latency (BG-12), which the
        # loader admits only in the local profile; mail parents read it.
        values["STEWARDSHIP_LOCAL_SMTP_LATENCY_MS"] = (
            configuration.local_smtp_latency_ms
        )
    return values


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
    creation of the initial singleton and key inventories; schema migrations use
    the separate owner. Bootstrap cannot UPDATE or rotate credential inventories.
    """
    from .accounts.configuration_service import CONFIGURATION_GRANTS

    grants = {table: set(values) for table, values in CONFIGURATION_GRANTS.items()}
    if role is ServiceRole.BOOTSTRAP:
        grants["stewardship_system_configuration"].add("INSERT")
        grants["stewardship_credential_key_state"] = {"SELECT", "INSERT"}
        grants["stewardship_credential_deployment"] = {"SELECT", "INSERT"}
    elif role is ServiceRole.ADMIN_RECOVERY:
        from .accounts.automation_grants import add_automation_recovery_grants

        grants["stewardship_config_request"].add("INSERT")
        # Session revocation is column-limited (offline_columns, #389 L6).
        # The restore's revocation of every automation session (ADM-11).
        add_automation_recovery_grants(grants, {})
    else:
        raise ConfigError("This role has no offline configuration grants.")
    return grants


def offline_columns(role):
    """The column grants of an offline login, declared beside its table grants.

    Admin recovery ends automation sessions through the session table's ending
    columns only (``revoke-automation-sessions``); bootstrap has none.

    Recovery also revokes every live Admin session: the activation trigger
    (``stewardship_recovery_activation_v1``) runs as this login and sets
    ``revoked_at``, ``version``, ``actor_id`` and ``correlation_id`` on rows
    with no ``revoked_at``, reading ``last_activity_at``. It gets exactly
    those columns (#389 L6), never the session key, so a compromised
    recovery run cannot read or rewrite a live session. Its SELECT columns
    are the configuration installer's (``SESSION_COLUMNS``) plus ``version``.
    """
    if role is ServiceRole.BOOTSTRAP:
        return {}
    if role is ServiceRole.ADMIN_RECOVERY:
        from .accounts.automation_grants import add_automation_recovery_grants
        from .accounts.setup_exchange_grants import SESSION_COLUMNS

        columns = {
            "stewardship_portal_session": {
                "SELECT": set(SESSION_COLUMNS) | {"version"},
                "UPDATE": {"revoked_at", "version", "actor_id", "correlation_id"},
            }
        }
        add_automation_recovery_grants({}, columns)
        return columns
    raise ConfigError("This role has no offline configuration grants.")


def admit_offline_database(configuration):
    """Offline host confirmation never bypasses SQL identity or schema admission.

    Column grants are declared by ``offline_columns`` and verified exactly, as
    the online logins' are: a column privilege is admitted only for a column
    it names.
    """
    from django.db import connection

    from .accounts.credential_database import _identity, admit_grants
    from .runtime_grants import admit_columns

    role = configuration.service_role
    _identity("pk_stewardship_" + role.value.replace("-", "_"))
    tables, columns = offline_grants(role), offline_columns(role)
    allowed = {table: set(grants) for table, grants in tables.items()}
    for table, grants in columns.items():
        allowed.setdefault(table, set()).update(grants)
    admit_grants(allowed)
    admit_columns(connection, tables, columns)
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
