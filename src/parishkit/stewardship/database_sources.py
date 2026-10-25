"""Prove which logins PostgreSQL accepts from where, without any password.

Operations spec, "Database connection sources". Two commands:

``database-sources`` runs inside one service's container and tries every
login in the inventory from there. PostgreSQL applies ``pg_hba`` before it
asks for a password, so a connection attempt that carries no password ends
either with the server asking for one (the source is allowed for that login;
libpq reports ``fe_sendauth: no password supplied``) or with the server's
refusal from the final ``host all all all reject`` (SQLSTATE 28000,
``pg_hba.conf rejects connection``). libpq reports a failed connection
without its SQLSTATE (see ``runtime_database.database_not_ready``), so the
server's message is what is matched; only that refusal produces it. The
result is compared with the row the renderer derived for this service.

``hba-check`` opens no database: like ``upgrade-check`` it prints one
read-only query, which the upgrade runs as the operator over the socket
after reloading PostgreSQL's configuration, to learn whether the rules in
force were loaded from the mounted file and that file is the rendered one.
"""

import hashlib
import os
import socket
import sys
import tempfile
from pathlib import Path

from parishkit.config import ConfigError

from .runtime_hba import HBA_FILE, OPERATOR, held_logins

ALLOWED = "fe_sendauth: no password supplied"
REFUSED = "pg_hba.conf rejects connection"
MARKERS = ("fe_sendauth", "pg_hba", "FATAL")
# What one probe found: the source is allowed (the server asked for a
# password), refused by pg_hba, accepted with no password at all (TCP trust,
# always a failure), or anything else (never matched, never printed).
OUTCOMES = ("allowed", "refused", "trust", "unknown")
# The hba check's answers besides "t", in the order the query tests them.
HBA_ANSWERS = (
    "hba_file_not_mounted",
    "hba_file_differs",
    "hba_file_invalid",
    "hba_reload_pending",
)


def literal(value):
    """Quote one code-owned string for SQL; standard_conforming_strings is on."""
    return "'" + str(value).replace("'", "''") + "'"


def inventory():
    """Every SQL login that may connect: ``database_identities()`` and the operator."""
    from .runtime_identities import database_identities

    return (*(entry[1] for entry in database_identities()), OPERATOR)


def service_name(configuration):
    """The Compose service a service document belongs to, for the report."""
    target = configuration.credential_target
    if target:
        return "credential-installer-" + target.replace("_", "-")
    return configuration.service_role.value


def source_address(configuration):
    """The local address this container reaches PostgreSQL from.

    A connected UDP socket asks the kernel for the route without sending
    anything, so PostgreSQL sees no extra connection attempt.
    """
    database = configuration.postgres
    family, kind, protocol, _, address = socket.getaddrinfo(
        database.host, database.port, type=socket.SOCK_DGRAM
    )[0]
    with socket.socket(family, kind, protocol) as route:
        route.connect(address)
        return route.getsockname()[0]


def probe(configuration, login, passfile):
    """Try ``login`` with no password; return one of ``OUTCOMES``.

    ``passfile`` is an empty file, and the caller has cleared libpq's
    environment, so libpq has no password to send: a connection that
    succeeds was accepted without one. Encryption is off, as on every
    internal connection, so libpq makes exactly one attempt.
    """
    import psycopg

    database = configuration.postgres
    try:
        connection = psycopg.connect(
            host=database.host,
            port=database.port,
            dbname=database.name,
            user=login,
            passfile=str(passfile),
            sslmode="disable",
            gssencmode="disable",
            connect_timeout=database.connect_timeout,
            application_name="pk-stewardship database-sources",
        )
    except psycopg.OperationalError as error:
        message = str(error)
        # Both markers at once (two addresses answering differently) is
        # never classified, and libpq's own prompt carries no server error,
        # so any FATAL means the server answered something else first.
        allowed, refused = ALLOWED in message, REFUSED in message
        if allowed and not refused and "FATAL:" not in message:
            return "allowed"
        if refused and not allowed:
            return "refused"
        return "unknown"
    connection.close()
    return "trust"


def clear_libpq_environment(environ=os.environ):
    """Remove every ``PG*`` variable, so no password, passfile or service applies.

    libpq reads ``PGPASSWORD``, ``PGPASSFILE`` and ``PGSERVICE`` (whose file
    may hold a password) from the process environment; this command's own
    process must never inherit one.
    """
    for name in [name for name in environ if name.startswith("PG")]:
        del environ[name]


def mismatches(results, expected):
    """The ``(login, outcome, why)`` triples that differ from the rendered row.

    ``results`` maps each login to its outcome and ``expected`` is the set
    of logins this service holds. A login allowed or accepted without a
    password where the rules refuse it is a security failure.
    """
    found = []
    for login, outcome in results.items():
        if outcome == "trust":
            found.append(
                (login, outcome, "accepted with no password (security failure)")
            )
        elif outcome == "unknown":
            found.append(
                (login, outcome, "an answer that is neither allowed nor refused")
            )
        elif outcome == "allowed" and login not in expected:
            found.append(
                (login, outcome, "the rendered rules refuse it (security failure)")
            )
        elif outcome == "refused" and login in expected:
            found.append((login, outcome, "the rendered rules allow it"))
    return found


def check_sources(configuration):
    """Probe the whole inventory from here, print the result, return the exit code.

    0 when every login's outcome is the rendered rules' answer for this
    service, 1 otherwise. Every line names the source address and the
    service, so an upgrade log says exactly which login and source differ.
    """
    from .runtime_database import require_internal_database

    require_internal_database(configuration)
    # The server quotes the database name in its messages, so a name holding
    # a marker could make one answer look like another.
    if any(text in configuration.postgres.name for text in MARKERS):
        raise ConfigError("The database name cannot be probed safely.")
    expected = set(held_logins(configuration))
    if not expected <= set(inventory()):
        raise ConfigError("This service holds a login outside the inventory.")
    where = f"{source_address(configuration)} ({service_name(configuration)})"
    clear_libpq_environment()
    with tempfile.TemporaryDirectory() as directory:
        passfile = Path(directory) / "pgpass"
        passfile.touch(mode=0o600)
        results = {
            login: probe(configuration, login, passfile) for login in inventory()
        }
    for login, outcome in results.items():
        print(f"{login} {outcome} from {where}")
    found = mismatches(results, expected)
    for login, outcome, why in found:
        print(f"MISMATCH: {login} is {outcome} from {where}: {why}", file=sys.stderr)
    if found:
        print(
            f"ERROR: database sources differ from the rendered rules from {where}",
            file=sys.stderr,
        )
        return 1
    allowed = sum(outcome == "allowed" for outcome in results.values())
    print(
        f"database sources match the rendered rules from {where}: "
        f"{allowed} allowed, {len(results) - allowed} refused",
    )
    return 0


def rendered_hba(configuration):
    """The ``pg_hba.conf`` bytes the provisioning record renders today.

    The same plan, and the same refusal of changed inputs, that
    ``retarget-image`` writes the mounted file with.
    """
    from .runtime_paths import RuntimeLayout
    from .runtime_retarget import recorded_plan

    _, plan = recorded_plan(configuration)
    resolved, documents = plan[0], plan[3]
    return documents[RuntimeLayout(resolved).hba_directory / "pg_hba.conf"]


def hba_query(rendered):
    """The read-only query that answers ``t`` or the first failed hba condition.

    In order: PostgreSQL's ``hba_file`` is the mounted path; the mounted
    file is byte-for-byte ``rendered``; ``pg_hba_file_rules`` reports no
    error (so the last load did not keep older rules); and the
    configuration was loaded after the psql variable ``since``, the
    ``clock_timestamp()`` the upgrade read just before it sent the reload
    (the file was written before then). Both times come from PostgreSQL's
    own clock, so a LOCAL fake clock shifts them alike, and both have
    microsecond precision, unlike ``pg_stat_file``'s whole-second mtime.
    Only the last answer is worth waiting on; the others stop the upgrade.
    The query must be fed to psql on its input, which interpolates
    ``:'since'``; ``since=infinity`` checks the file alone.
    """
    digest = hashlib.sha256(rendered).hexdigest()
    current = "current_setting('hba_file')"
    not_mounted, differs, invalid, pending = (literal(answer) for answer in HBA_ANSWERS)
    return (
        "SELECT CASE\n"
        f"  WHEN {current} <> {literal(HBA_FILE)} THEN {not_mounted}\n"
        f"  WHEN encode(sha256(pg_read_binary_file({current})), 'hex') <> "
        f"{literal(digest)} THEN {differs}\n"
        "  WHEN EXISTS (SELECT 1 FROM pg_catalog.pg_hba_file_rules "
        f"WHERE error IS NOT NULL) THEN {invalid}\n"
        f"  WHEN pg_conf_load_time() <= :'since'::timestamptz THEN {pending}\n"
        "  ELSE 't'\n"
        "END;\n"
    )


def execute(args):
    """Run ``database-sources`` or ``hba-check`` for the CLI; return the exit code.

    A refusal before any probe or rendering (no or an unusable
    configuration) is one generic line and exit 2, as the other operator
    commands keep configuration detail off the console.
    """
    from .deployment import load_deployment

    try:
        if args.config is None:
            raise ConfigError("Explicit deployment configuration required.")
        configuration = load_deployment(args.config)
        if args.command == "hba-check":
            query = hba_query(rendered_hba(configuration))
        else:
            return check_sources(configuration)
    except Exception:
        print(
            f"ERROR: {args.command} refused; check the configuration", file=sys.stderr
        )
        return 2
    print(query, end="")
    return 0
