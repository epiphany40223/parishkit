"""Compare a backup's schema with this image's in a scratch database (#608).

``restore-check`` says whether a set's migrations are exactly an image's.
When they are not, and the Administrator wants to consider restoring the set
onto a code base with a different schema, an operator (or an LLM working
with one) needs to know what differs before planning anything. This command
answers that without touching the deployment:

1. It connects, as the superuser ``postgres``, to a throwaway PostgreSQL
   server (the pinned image on tmpfs, reachable only on an internal Docker
   network) and refuses unless that server holds no database but
   ``postgres`` and the templates. The live cluster always holds the
   deployment's database, so it is refused even if attached by mistake.
2. It creates three databases: :data:`BACKUP_DATABASE`, loaded from the
   set's decrypted dump with ``pg_restore`` (no owners or privileges: the
   scratch server has none of the deployment's roles);
   :data:`MIGRATED_DATABASE`, migrated by this image exactly as the
   ``migrate`` command does; and :data:`IMAGE_DATABASE`, a copy of the
   migrated one through the same dump and load, so both compared sides
   print their definitions alike.
3. It describes the backup and image databases from the catalog,
   value-free (tables, columns, applied migrations, every kind of schema
   object in ``OBJECTS``, and row counts for the tables that differ), and
   prints one JSON report of the differences.
4. It drops all three, unless ``--keep`` leaves the two compared ones for an
   operator-supervised session; the report names them.

What it does not compare: owners and privileges (``database-grants`` sets
them), schemas other than ``public`` (Stewardship has none), extensions,
and composite and range types (the schema defines none).

Nothing here changes the deployment, transforms data or decides anything:
that stays a separate, Administrator-approved step (see the backup runbook).
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from parishkit.config import ConfigError

from .observability import Event, configure_logging, emit, emit_failure

BACKUP_DATABASE = "pk_restore_backup"
IMAGE_DATABASE = "pk_restore_image"
# This image migrates this one; it is then copied into IMAGE_DATABASE with
# pg_dump and pg_restore, so both compared databases went through the same
# dump and load, which prints some expressions differently from a migrated
# catalog (an IN-list's casts, for one), and dropped.
MIGRATED_DATABASE = "pk_restore_migrated"
SCRATCH = (BACKUP_DATABASE, IMAGE_DATABASE, MIGRATED_DATABASE)
# The databases a fresh PostgreSQL server holds, and nothing else.
SCRATCH_DATABASES = frozenset({"postgres", "template0", "template1"})
SUPERUSER = "postgres"
CONNECT_SECONDS = 10
# How long pg_restore may take to load the dump. A set is a few hundred
# megabytes at most (#346's limits); the bound stops a hung or damaged load.
LOAD_SECONDS = 1800
# The reviewed names of the limits in the timeout line (observability).
LOAD = "restore_compare_load"
# Exit codes: the schemas are the same; they differ; the comparison was refused.
SAME, REFUSED, DIFFERENT = 0, 2, 3
HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")

# The value-free catalog description of one database's public schema.
TABLES = """
    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind IN ('r','p')
"""
COLUMNS = """
    SELECT c.relname, a.attname, format_type(a.atttypid,a.atttypmod),
           a.attnotnull, pg_get_expr(d.adbin,d.adrelid),
           a.attidentity::text, a.attgenerated::text, co.collname::text
    FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
    LEFT JOIN pg_collation co ON co.oid=a.attcollation
    WHERE n.nspname='public' AND c.relkind IN ('r','p')
      AND a.attnum>0 AND NOT a.attisdropped
"""
MIGRATIONS = "SELECT app::text,name::text FROM public.django_migrations"
# Every other schema object of the public schema, as (name, definition)
# pairs, compared by name and definition (#792 review). Owners and
# privileges are not compared: the dump is loaded without them, and
# ``database-grants`` owns them on the deployment. A function is compared by
# the SHA-256 of its full definition (which includes SECURITY DEFINER and its
# settings), so an in-place SQL change to one shows as changed.
OBJECTS = {
    "constraints": """
        SELECT c.relname || '.' || k.conname,
               k.contype::text || ' ' || pg_get_constraintdef(k.oid, true)
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
    """,
    "indexes": """
        SELECT i.relname, pg_get_indexdef(i.oid)
        FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid
        JOIN pg_namespace n ON n.oid=i.relnamespace WHERE n.nspname='public'
    """,
    "functions": """
        SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
               encode(sha256(convert_to(pg_get_functiondef(p.oid), 'UTF8')), 'hex')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prokind IN ('f','p')
    """,
    "triggers": """
        SELECT c.relname || '.' || t.tgname,
               t.tgenabled::text || ' ' || pg_get_triggerdef(t.oid, true)
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal
    """,
    "policies": """
        SELECT tablename || '.' || policyname,
               concat_ws(' ', permissive, cmd, roles::text,
                         'USING', qual, 'CHECK', with_check)
        FROM pg_policies WHERE schemaname='public'
    """,
    "row_security": """
        SELECT c.relname, c.relrowsecurity::text || ' ' || c.relforcerowsecurity::text
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p')
    """,
    "views": """
        SELECT c.relname, c.relkind::text || ' ' || pg_get_viewdef(c.oid, true)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('v','m')
    """,
    "table_storage": """
        SELECT c.relname, concat_ws(' ', c.relpersistence::text,
               array_to_string(c.reloptions, ','))
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p')
    """,
    "types": """
        SELECT t.typname, t.typtype::text || ' ' || CASE t.typtype
            WHEN 'e' THEN (SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
                           FROM pg_enum e WHERE e.enumtypid=t.oid)
            ELSE concat_ws(' ', format_type(t.typbasetype, t.typtypmod),
                 t.typnotnull::text, t.typdefault,
                 (SELECT string_agg(pg_get_constraintdef(k.oid, true), ' '
                                    ORDER BY k.conname)
                  FROM pg_constraint k WHERE k.contypid=t.oid)) END
        FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        WHERE n.nspname='public' AND t.typtype IN ('d','e')
    """,
    "sequences": """
        SELECT c.relname, concat_ws(' ', format_type(s.seqtypid, NULL), s.seqstart,
               s.seqincrement, s.seqmin, s.seqmax, s.seqcache, s.seqcycle)
        FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
    """,
}
# Statement limits on the scratch server (#792 review), each logged on a
# kill: the catalog queries, the row counts, and each migration statement.
CATALOG_SECONDS = 60
COUNT_SECONDS = 300
MIGRATE_SECONDS = 600


class RestoreCompareRefused(ConfigError):
    """The comparison cannot run; the reason is a fixed sentence."""


def quoted(name):
    """One identifier quoted for SQL (catalog names only, never input)."""
    return '"' + name.replace('"', '""') + '"'


def describe(cursor):
    """The public schema's tables, columns and applied migrations.

    Returns ``{"tables": set, "columns": {(table, column): (type, not null,
    default, identity, generated, collation)}, "migrations": set, "objects":
    {category: {name: definition}}}`` with ``OBJECTS``' categories. A
    database with no ``django_migrations`` table is not a Stewardship
    database at all.
    """
    cursor.execute(TABLES)
    tables = {row[0] for row in cursor.fetchall()}
    if "django_migrations" not in tables:
        raise RestoreCompareRefused("A database holds no migration records.")
    cursor.execute(COLUMNS)
    columns = {
        (table, column): tuple(facts) for table, column, *facts in cursor.fetchall()
    }
    cursor.execute(MIGRATIONS)
    migrations = {tuple(row) for row in cursor.fetchall()}
    objects = {}
    for category, query in OBJECTS.items():
        cursor.execute(query)
        objects[category] = dict(cursor.fetchall())
    return {
        "tables": tables,
        "columns": columns,
        "migrations": migrations,
        "objects": objects,
    }


def object_difference(backup, image):
    """Per category: names only in the set, only in the image, and those whose
    definition differs (with both definitions; a function's is its digest)."""
    report = {}
    for category in OBJECTS:
        ours, theirs = backup.get(category, {}), image.get(category, {})
        report[category] = {
            "only_in_backup": sorted(ours.keys() - theirs.keys()),
            "only_in_image": sorted(theirs.keys() - ours.keys()),
            "changed": [
                {"name": name, "backup": ours[name], "image": theirs[name]}
                for name in sorted(ours.keys() & theirs.keys())
                if ours[name] != theirs[name]
            ],
        }
    return report


def _facts(facts):
    """A column's facts as the report prints them: type, nullability,
    default, identity (``a`` always, ``d`` by default, empty for none),
    generated (``s`` stored, empty for none) and collation."""
    kind, not_null, default, identity, generated, collation = facts
    return {
        "type": kind,
        "not_null": not_null,
        "default": default,
        "identity": identity,
        "generated": generated,
        "collation": collation,
    }


def _column(table, column, facts):
    """One column, named, as the report prints it."""
    return {"table": table, "column": column, **_facts(facts)}


def difference(backup, image):
    """The report's differences between two descriptions (see ``describe``).

    Columns of a table that exists on one side only are reported with the
    table, not again column by column. A column on both sides whose type,
    nullability or default differs is ``columns_changed``.
    """
    shared = backup["tables"] & image["tables"]
    only_backup = {
        key: facts
        for key, facts in backup["columns"].items()
        if key[0] in shared and key not in image["columns"]
    }
    only_image = {
        key: facts
        for key, facts in image["columns"].items()
        if key[0] in shared and key not in backup["columns"]
    }
    changed = [
        {
            "table": key[0],
            "column": key[1],
            "backup": _facts(backup["columns"][key]),
            "image": _facts(image["columns"][key]),
        }
        for key in sorted(backup["columns"].keys() & image["columns"].keys())
        if backup["columns"][key] != image["columns"][key]
    ]
    return {
        "not_in_backup": sorted(map(list, image["migrations"] - backup["migrations"])),
        "unknown_to_image": sorted(
            map(list, backup["migrations"] - image["migrations"])
        ),
        "tables_only_in_backup": sorted(backup["tables"] - image["tables"]),
        "tables_only_in_image": sorted(image["tables"] - backup["tables"]),
        "columns_only_in_backup": [
            _column(*key, facts) for key, facts in sorted(only_backup.items())
        ],
        "columns_only_in_image": [
            _column(*key, facts) for key, facts in sorted(only_image.items())
        ],
        "columns_changed": changed,
        "objects": object_difference(
            backup.get("objects", {}), image.get("objects", {})
        ),
    }


def is_same(report):
    """Whether a report lists no difference at all: migrations, tables,
    columns and every object category (row counts are not differences)."""
    return not any(
        value for key, value in report.items() if key not in {"rows", "objects"}
    ) and not any(
        entries
        for category in report.get("objects", {}).values()
        for entries in category.values()
    )


def _statement_timeout(error):
    """Whether PostgreSQL stopped ``error``'s statement at statement_timeout
    (SQLSTATE 57014 with that message; a deliberate cancel shares the code).
    Django wraps the driver's error, so the cause chain is followed."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        diag = getattr(error, "diag", None)
        if getattr(error, "sqlstate", None) == "57014" and "statement timeout" in (
            getattr(diag, "message_primary", None) or ""
        ):
            return True
        error = error.__cause__ or error.__context__
    return False


@contextmanager
def statement_limit(seconds):
    """Log a scratch statement stopped at its ``seconds`` limit, then refuse.

    The limit itself is set on the connection (``_connect``, the migrate
    settings, ``row_counts``); this names it in the timeout line with the
    elapsed time of the step it stopped (the timeout rule).
    """
    started = time.monotonic()
    try:
        yield
    except Exception as error:
        if not _statement_timeout(error):
            raise
        emit(
            Event.TASK_TIMED_OUT,
            level=logging.WARNING,
            timeout="statement_timeout",
            limit_seconds=int(seconds),
            elapsed_seconds=int(time.monotonic() - started),
        )
        raise RestoreCompareRefused(
            "A scratch statement ran past its time limit."
        ) from None


def differing_tables(report):
    """The backup's tables a planner needs row counts for: those that exist
    only in the backup or whose columns differ."""
    tables = set(report["tables_only_in_backup"])
    for key in ("columns_only_in_backup", "columns_only_in_image", "columns_changed"):
        tables.update(entry["table"] for entry in report[key])
    return sorted(tables)


def row_counts(cursor, tables):
    """How many rows each named table holds: counts only, never values."""
    counts = {}
    cursor.execute(f"SET statement_timeout = '{COUNT_SECONDS}s'")
    try:
        for table in tables:
            cursor.execute(f"SELECT count(*) FROM public.{quoted(table)}")
            counts[table] = cursor.fetchone()[0]
    finally:
        cursor.execute(f"SET statement_timeout = '{CATALOG_SECONDS}s'")
    return counts


def require_scratch_server(cursor, *, allowed=SCRATCH_DATABASES):
    """Refuse a server that holds any database but a fresh server's own.

    This is what keeps the command off the deployment's cluster: that one
    always holds the deployment's database. Leftover scratch databases from
    an interrupted run are refused too; the server is meant to be thrown
    away, so start a new one.
    """
    cursor.execute("SELECT datname FROM pg_database")
    present = {row[0] for row in cursor.fetchall()}
    if not present <= allowed:
        raise RestoreCompareRefused("The server is not an empty scratch server.")
    cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
    if cursor.fetchone() != (True,):
        raise RestoreCompareRefused("The scratch login is not its superuser.")


def _environment(password):
    """The PostgreSQL client tools' environment: the password, never in an
    argument, and the connect limit."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PGPASSWORD": password,
        "PGCONNECT_TIMEOUT": str(CONNECT_SECONDS),
        "PGSSLMODE": "disable",
    }


def _restore_command(database, *, host, port, source=None):
    """pg_restore into one scratch database: one transaction, first error
    stops it, without the deployment's owners and privileges (the scratch
    server has none of its roles), subscriptions or publications (a set
    never has them; refusing them keeps a dump from reaching out of the
    scratch server). ``source`` is the dump file, or None for standard input.
    """
    binary = shutil.which("pg_restore")
    if binary is None:
        raise RestoreCompareRefused("pg_restore is not installed in this image.")
    command = [
        binary,
        "--no-owner",
        "--no-acl",
        "--no-subscriptions",
        "--no-publications",
        "--exit-on-error",
        "--single-transaction",
        "--host",
        host,
        "--port",
        str(port),
        "--username",
        SUPERUSER,
        "--dbname",
        database,
    ]
    return command if source is None else [*command, str(Path(source))]


def _timed_out(seconds, started):
    """Log a load stopped at its limit (the timeout rule), then refuse."""
    emit(
        Event.TASK_TIMED_OUT,
        level=logging.WARNING,
        timeout=LOAD,
        limit_seconds=int(seconds),
        elapsed_seconds=int(time.monotonic() - started),
    )
    raise RestoreCompareRefused("Loading the dump took too long.") from None


def load_dump(dump, *, host, port, password, seconds=LOAD_SECONDS):
    """Load the decrypted dump into :data:`BACKUP_DATABASE` with pg_restore.

    Killed at ``seconds``, the kill logged with that limit and the time it
    ran. Its messages are discarded: they can quote the dump.
    """
    command = _restore_command(BACKUP_DATABASE, host=host, port=port, source=dump)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=seconds,
            env=_environment(password),
            check=False,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run has killed and reaped pg_restore by now.
        _timed_out(seconds, started)
    if completed.returncode != 0:
        raise RestoreCompareRefused("The dump could not be loaded.")


def copy_migrated(*, host, port, password, seconds=LOAD_SECONDS):
    """Copy :data:`MIGRATED_DATABASE` into :data:`IMAGE_DATABASE` through
    pg_dump piped into pg_restore, as the set itself was dumped and loaded.

    Both are killed at ``seconds`` together and the kill is logged; neither
    one's messages are kept. Either one failing refuses the comparison.
    """
    binary = shutil.which("pg_dump")
    if binary is None:
        raise RestoreCompareRefused("pg_dump is not installed in this image.")
    dump = [
        binary,
        "--format=custom",
        "--host",
        host,
        "--port",
        str(port),
        "--username",
        SUPERUSER,
        "--dbname",
        MIGRATED_DATABASE,
    ]
    restore = _restore_command(IMAGE_DATABASE, host=host, port=port)
    environment = _environment(password)
    started = time.monotonic()
    with (
        subprocess.Popen(
            dump,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        ) as producer,
        subprocess.Popen(
            restore,
            stdin=producer.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        ) as consumer,
    ):
        # Only the consumer reads the pipe now.
        producer.stdout.close()
        try:
            consumer.wait(timeout=seconds)
            producer.wait(timeout=max(1, seconds - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            for process in (producer, consumer):
                process.kill()
                process.wait()
            _timed_out(seconds, started)
    if producer.returncode != 0 or consumer.returncode != 0:
        raise RestoreCompareRefused("The migrated schema could not be copied.")


def _connect(host, port, password, database):
    """One autocommit connection to the scratch server."""
    import psycopg

    return psycopg.connect(
        host=host,
        port=port,
        user=SUPERUSER,
        password=password,
        dbname=database,
        connect_timeout=CONNECT_SECONDS,
        sslmode="disable",
        autocommit=True,
        options=f"-c statement_timeout={CATALOG_SECONDS}s",
    )


def migrate_image_database(host, port, password):
    """Run this image's migrations on :data:`MIGRATED_DATABASE`, in this process.

    Exactly the ``call_command("migrate")`` the ``migrate`` command runs,
    with Django configured from code-owned settings and the scratch
    database. The process must be fresh (no settings yet).
    """
    import secrets
    from importlib import import_module

    import django
    from django.conf import settings
    from django.core.management import call_command

    if settings.configured:
        raise RestoreCompareRefused("The comparison needs a fresh process.")
    base = import_module("parishkit.stewardship.settings.base")
    values = {name: getattr(base, name) for name in dir(base) if name.isupper()}
    values["SECRET_KEY"] = secrets.token_urlsafe(48)
    values["DATABASES"] = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": host,
            "PORT": port,
            "NAME": MIGRATED_DATABASE,
            "USER": SUPERUSER,
            "PASSWORD": password,
            "CONN_MAX_AGE": 0,
            "OPTIONS": {
                "connect_timeout": CONNECT_SECONDS,
                "sslmode": "disable",
                "options": f"-c statement_timeout={MIGRATE_SECONDS}s",
            },
        }
    }
    settings.configure(**values)
    django.setup()
    call_command("migrate", interactive=False, verbosity=0)
    from django.db import connection

    connection.close()


def compare(dump, *, host, port, password, keep=False, migrate=None, copy=None):
    """Load, migrate, copy, describe and diff; return (exit code, report).

    ``migrate`` and ``copy`` stand in for :func:`migrate_image_database` and
    :func:`copy_migrated` in tests. The scratch databases are dropped
    afterwards, whatever happened; ``keep`` keeps the two compared ones (the
    migrated original is always dropped) once the comparison has finished.
    """
    migrate = migrate_image_database if migrate is None else migrate
    copy = copy_migrated if copy is None else copy
    with _connect(host, port, password, "postgres") as server:
        with server.cursor() as cursor:
            require_scratch_server(cursor)
        kept = False
        try:
            with server.cursor() as cursor:
                for name in SCRATCH:
                    cursor.execute(f"CREATE DATABASE {quoted(name)}")
            load_dump(dump, host=host, port=port, password=password)
            with statement_limit(MIGRATE_SECONDS):
                migrate(host, port, password)
            copy(host=host, port=port, password=password)
            with (
                statement_limit(CATALOG_SECONDS),
                _connect(host, port, password, IMAGE_DATABASE) as image_db,
                image_db.cursor() as cursor,
            ):
                image = describe(cursor)
            with (
                _connect(host, port, password, BACKUP_DATABASE) as backup_db,
                backup_db.cursor() as cursor,
            ):
                with statement_limit(CATALOG_SECONDS):
                    backup = describe(cursor)
                report = difference(backup, image)
                with statement_limit(COUNT_SECONDS):
                    report["rows"] = row_counts(cursor, differing_tables(report))
            same = is_same(report)
            report = {"result": "same" if same else "different", **report}
            if keep:
                kept = True
                report["kept_databases"] = [BACKUP_DATABASE, IMAGE_DATABASE]
            return (SAME if same else DIFFERENT), report
        finally:
            with server.cursor() as cursor:
                for name in (MIGRATED_DATABASE,) if kept else SCRATCH:
                    cursor.execute(
                        f"DROP DATABASE IF EXISTS {quoted(name)} WITH (FORCE)"
                    )


def _password(path):
    """The scratch superuser's password, from an owner-only file."""
    from .accounts.cryptography import CryptographicError
    from .accounts.key_files import read_private

    try:
        password = read_private(Path(path)).decode("utf-8").removesuffix("\n")
    except (CryptographicError, OSError, UnicodeError):
        # read_private refuses a file that is not owner-only, or is too large
        # or missing, with CryptographicError (#792 review).
        raise RestoreCompareRefused(
            "The scratch password file is unreadable."
        ) from None
    if not password or any(ord(char) < 32 for char in password):
        raise RestoreCompareRefused("The scratch password file is unreadable.")
    return password


def _port(value):
    """A TCP port number from its option text."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        port = 0
    if not 0 < port < 65536:
        raise RestoreCompareRefused("The scratch port is not a port number.")
    return port


def execute_restore_compare(args):
    """``restore-compare``: print the report.

    Exits 0 when the schemas are the same, 3 when they differ, and 2 with a
    fixed reason when the comparison cannot run (the process log names only
    the reviewed failure category, never a path, host or file content).
    """
    configure_logging()
    try:
        if args.dump is None or args.scratch_host is None:
            raise RestoreCompareRefused("A dump and a scratch host are required.")
        if args.scratch_password_file is None:
            raise RestoreCompareRefused("A scratch password file is required.")
        if not HOST.match(args.scratch_host) or args.scratch_host == "postgres":
            # "postgres" is the deployment's own Compose database service.
            raise RestoreCompareRefused("The scratch host is not a scratch server.")
        code, report = compare(
            args.dump,
            host=args.scratch_host,
            port=_port(args.scratch_port or "5432"),
            password=_password(args.scratch_password_file),
            keep=args.keep is True,
        )
    except Exception as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        reason = (
            str(error).rstrip(".")
            if type(error) is RestoreCompareRefused
            else "the scratch server or the dump could not be used"
        )
        print(
            f"ERROR: restore comparison refused: {reason}; see the backup "
            "runbook's Comparing a set with another release",
            file=sys.stderr,
        )
        return REFUSED
    print(json.dumps(report, sort_keys=True))
    return code
