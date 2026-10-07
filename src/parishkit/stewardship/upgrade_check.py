"""Prove from the live database that an upgrade's migration and grants are no-ops.

An upgrade stops the online services, so every second of `migrate` and
`database-grants` is downtime (#162). When the new release changes neither
the schema nor any runtime grant, both commands admit the deployment and then
change nothing. This module lets the new image say, without a database
connection of its own, exactly what "change nothing" means for it: it renders
one read-only SQL query that the operator runs as the database superuser and
that answers `t` only when all of these hold at once:

* the applied migrations are exactly the image's migrations, so `migrate`
  has nothing to apply and no unknown migration to refuse;
* the download capacity already equals the budget that `migrate` would set;
* the database carries this deployment's provisioning marker;
* every foundation login exists with exactly the attributes, membership and
  connection limit that `database-grants` requires of it;
* the operational log writer guard is this release's exact function body;
* every login already holds every privilege this image's registry grants it,
  and nothing beyond what `database-grants` would admit.

Anything else, including a query error such as a missing table, means "run
both commands". The query checks the same conditions the commands check,
from the same registries, so a skip can never hide a change they would have
made or a refusal they would have raised. Every online startup still verifies
the schema, excess grants and its role's connection limit independently.
"""

import secrets
from importlib import import_module

from .database_provisioning import READER_MEMBERSHIP, _writer_guard_digest, role_limit
from .deployment import ServiceRole
from .runtime_identities import database_identities

TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
# The relations every grant admission inspects, as database_provisioning
# selects them: ordinary, partitioned, view, materialized view and foreign.
RELATIONS = (
    "n.nspname !~ '^pg_' AND n.nspname<>'information_schema' "
    "AND c.relkind IN('r','p','v','m','f')"
)


def literal(value):
    """Quote one code-owned string for SQL; standard_conforming_strings is on."""
    return "'" + str(value).replace("'", "''") + "'"


def text_array(values):
    """A text[] literal, typed so that an empty list is still valid SQL."""
    return "ARRAY[" + ",".join(literal(value) for value in values) + "]::text[]"


def code_settings():
    """Configure Django from code-owned settings only, as static collection does.

    The migration loader and grant registries need installed apps, but this
    command reads no deployment secret and opens no database. An already
    configured process (a test) keeps its own settings.
    """
    import django
    from django.conf import settings

    if not settings.configured:
        base = import_module("parishkit.stewardship.settings.base")
        values = {name: getattr(base, name) for name in dir(base) if name.isupper()}
        values["SECRET_KEY"] = secrets.token_urlsafe(48)
        settings.configure(**values)
        django.setup()


def disk_migrations():
    """Every (app, name) migration this image ships, from its code alone."""
    from django.db.migrations.loader import MigrationLoader

    return sorted(MigrationLoader(None, ignore_no_migrations=True).disk_migrations)


def migrations_match(migrations):
    """The applied set equals the image's set, in both directions."""
    image = (
        f"SELECT * FROM unnest({text_array(app for app, _ in migrations)},"
        f"{text_array(name for _, name in migrations)})"
    )
    applied = "SELECT app::text,name::text FROM public.django_migrations"
    return (
        f"NOT EXISTS(({applied}) EXCEPT ({image})) "
        f"AND NOT EXISTS(({image}) EXCEPT ({applied}))"
    )


def role_matches(login, marker, limit, *, reader):
    """The row database_provisioning._check_role admits, and nothing else."""
    membership = literal(READER_MEMBERSHIP) if reader else "NULL"
    return (
        "EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname="
        f"{literal(login)} "
        f"AND shobj_description(r.oid,'pg_authid')={literal(marker)} "
        f"AND NOT r.rolsuper AND r.rolbypassrls={'true' if reader else 'false'} "
        "AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication "
        f"AND NOT r.rolinherit AND r.rolcanlogin AND r.rolconnlimit={int(limit)} "
        "AND (SELECT string_agg(m.rolname||':'||am.inherit_option::text||':'"
        "||am.admin_option::text,',' ORDER BY m.rolname) FROM pg_auth_members am "
        "JOIN pg_roles m ON m.oid=am.roleid WHERE am.member=r.oid) "
        f"IS NOT DISTINCT FROM {membership})"
    )


def grants_held(login, tables, columns):
    """Every table and column privilege the registry grants is already held."""
    table_rows = sorted(
        (table, privilege)
        for table, privileges in tables.items()
        for privilege in privileges
    )
    column_rows = sorted(
        (table, column, privilege)
        for table, privileges in columns.items()
        for privilege, names in privileges.items()
        for column in names
    )
    relation = "to_regclass('public.'||quote_ident(g.t))"
    return (
        "NOT EXISTS(SELECT 1 FROM unnest("
        f"{text_array(t for t, _ in table_rows)},"
        f"{text_array(p for _, p in table_rows)}) g(t,p) "
        f"WHERE NOT coalesce(has_table_privilege({literal(login)},{relation},g.p),"
        "false)) "
        "AND NOT EXISTS(SELECT 1 FROM unnest("
        f"{text_array(t for t, _, _ in column_rows)},"
        f"{text_array(c for _, c, _ in column_rows)},"
        f"{text_array(p for _, _, p in column_rows)}) g(t,c,p) "
        "WHERE NOT EXISTS(SELECT 1 FROM pg_attribute a "
        f"WHERE a.attrelid={relation} AND a.attname=g.c AND a.attnum>0 "
        "AND NOT a.attisdropped "
        f"AND has_column_privilege({literal(login)},a.attrelid,a.attnum,g.p)))"
    )


def no_excess(login, tables, columns, *, reader):
    """Nothing beyond what _admit_existing_grants (or _admit_reader) admits.

    The backup login reads every table through pg_read_all_data, so, as in
    _admit_reader, only its non-SELECT privileges are compared. For every
    login a column write is admitted by a whole-table grant of that privilege
    or by its column grant.
    """
    allowed = sorted(
        (table, privilege)
        for table, privileges in tables.items()
        for privilege in privileges
    )
    allowed_columns = sorted(
        (table, privilege, column)
        for table, privileges in columns.items()
        for privilege, names in privileges.items()
        for column in names
    )
    whole = (
        "(c.relname::text,p) IN (SELECT * FROM unnest("
        f"{text_array(t for t, _ in allowed)},{text_array(p for _, p in allowed)}))"
    )
    table_privileges = TABLE_PRIVILEGES[1:] if reader else TABLE_PRIVILEGES
    column_privileges = (
        ("INSERT", "UPDATE", "REFERENCES")
        if reader
        else ("SELECT", "INSERT", "UPDATE", "REFERENCES")
    )
    by_column = (
        "(c.relname::text,p,a.attname::text) IN (SELECT * FROM unnest("
        f"{text_array(t for t, _, _ in allowed_columns)},"
        f"{text_array(p for _, p, _ in allowed_columns)},"
        f"{text_array(c for _, _, c in allowed_columns)}))"
    )
    result = (
        "NOT EXISTS(SELECT 1 FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"CROSS JOIN unnest({text_array(table_privileges)}) p "
        f"WHERE {RELATIONS} AND has_table_privilege({literal(login)},c.oid,p) "
        f"AND (n.nspname<>'public' OR NOT {whole})) "
        "AND NOT EXISTS(SELECT 1 FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid=c.oid "
        f"CROSS JOIN unnest({text_array(column_privileges)}) p "
        f"WHERE {RELATIONS} AND a.attnum>0 AND NOT a.attisdropped "
        f"AND has_column_privilege({literal(login)},c.oid,a.attnum,p) "
        f"AND (n.nspname<>'public' OR NOT ({whole} OR {by_column})))"
    )
    if reader:
        result += f" AND pg_has_role({literal(login)},'pg_read_all_data','MEMBER')"
    return result


def writer_guard_installed():
    """The same trigger and function body database_provisioning requires."""
    return (
        "EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid "
        "WHERE t.tgname='stewardship_operational_log_writer_v1' "
        "AND t.tgrelid=to_regclass('public.stewardship_operational_log') "
        "AND t.tgenabled='O' AND t.tgtype=7 "
        "AND t.tgfoid="
        "to_regprocedure('public.stewardship_operational_log_writer_v1()') "
        f"AND md5(p.prosrc)={literal(_writer_guard_digest())})"
    )


def upgrade_noop_query(configuration, deployment_id):
    """Render the one query answering whether migrate and grants are both no-ops.

    `configuration` supplies the database name and the runtime budget behind
    every connection limit, exactly as database-grants reads them; the
    deployment UUID is the provisioning marker it confirms.
    """
    from .runtime_grants import runtime_grants

    code_settings()
    marker = "parishkit-stewardship:" + str(deployment_id)
    checks = [
        f"current_database()={literal(configuration.postgres.name)}",
        "shobj_description((SELECT oid FROM pg_database "
        f"WHERE datname=current_database()),'pg_database')={literal(marker)}",
        migrations_match(disk_migrations()),
        "(SELECT capacity FROM public.stewardship_download_policy WHERE id=1) "
        f"IS NOT DISTINCT FROM {int(configuration.runtime_budget.download_capacity)}",
        writer_guard_installed(),
    ]
    for _, login, role, target in database_identities():
        reader = role is ServiceRole.BACKUP_WORKER
        checks.append(
            role_matches(login, marker, role_limit(configuration, role), reader=reader)
        )
        if role is ServiceRole.MIGRATION:
            continue  # The schema owner receives no runtime grants.
        tables, columns = runtime_grants(role, target=target)
        checks.append(grants_held(login, tables, columns))
        checks.append(no_excess(login, tables, columns, reader=reader))
    return "SELECT " + "\n AND ".join(f"({check})" for check in checks) + ";\n"
