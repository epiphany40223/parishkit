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
  connection limit that `database-grants` requires of it, and shows none of
  the isolation drift a runtime login's admission refuses (the schema-owning
  migration login is exempt from the ownership check only);
* the operational log writer guard is this release's exact function body;
* every login already holds every privilege this image's registry grants it,
  and nothing beyond what `database-grants` would admit (for the backup
  login, also none of the authority its backup admission refuses);
* no operator exists outside `pg_catalog` except the `pg_trgm` extension's
  own, and no runtime login may create one, in an application schema or the
  database (#2166);
* the SELECT-only inspection login (#2148) exists exactly as provisioned and
  holds exactly the SELECT grants its catalog rule gives, so the first
  upgrade that carries it runs `database-grants`. When it owns objects
  (#2291), the query answers a sentence naming them and their clean-up
  instead of `f`.

Anything else, including a query error such as a missing table, means "run
both commands". The query checks the same conditions the commands check,
from the same registries, so a skip can never hide a change they would have
made or a refusal they would have raised. Every online startup still verifies
the schema, excess grants and its role's connection limit independently.
"""

import secrets
from importlib import import_module

from . import inspect_login
from .database_provisioning import (
    BACKUP_SEQUENCES,
    ISOLATION_DRIFT,
    READER_MEMBERSHIP,
    _writer_guard_digest,
    excess_authority_checks,
    migration_exemption,
    role_limit,
)
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


def role_matches(login, marker, limit, *, reader, exempt=frozenset()):
    """The row database_provisioning._check_role admits, and nothing else.

    The login must also show none of the ``ISOLATION_DRIFT`` that a runtime
    login's own admission refuses at startup (#389 L7), so an upgrade onto a
    drifted catalog stops here, before its services would all refuse to
    start. ``exempt`` names checks waived for this login: the migration
    login owns the schema, so it is exempt from ``OWNERSHIP`` only.
    """
    membership = literal(READER_MEMBERSHIP) if reader else "NULL"
    drift = "".join(
        f"AND NOT {check} "
        for name, check in ISOLATION_DRIFT.items()
        if name not in exempt
    )
    return (
        "EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname="
        f"{literal(login)} "
        f"AND shobj_description(r.oid,'pg_authid')={literal(marker)} "
        f"AND NOT r.rolsuper AND r.rolbypassrls={'true' if reader else 'false'} "
        "AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication "
        f"AND NOT r.rolinherit AND r.rolcanlogin AND r.rolconnlimit={int(limit)} "
        f"{drift}"
        "AND (SELECT string_agg(m.rolname||':'||am.inherit_option::text||':'"
        "||am.admin_option::text,',' ORDER BY m.rolname) FROM pg_auth_members am "
        "JOIN pg_roles m ON m.oid=am.roleid WHERE am.member=r.oid) "
        f"IS NOT DISTINCT FROM {membership})"
    )


def login_present(login, checks):
    """``checks`` for one login, answered only once the login is known to exist.

    The privilege functions raise for a role name that does not exist, and
    PostgreSQL may evaluate ``AND`` terms in any order, so a login a release
    adds (the off-site uploader, #235) would turn the whole query into an
    error rather than ``f``. The lookup in ``pg_roles`` comes first inside a
    ``CASE``, which evaluates its branches lazily, so a missing login answers
    ``f`` and the upgrade runs as a schema change.
    """
    joined = " AND ".join(f"({check})" for check in checks)
    return (
        f"CASE WHEN EXISTS(SELECT 1 FROM pg_roles WHERE rolname={literal(login)}) "
        f"THEN ({joined}) ELSE false END"
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
    or by its column grant. The backup login must also hold none of the
    definer EXECUTE, sequence-write or CREATE authority its backup admission
    refuses, so a catalog drifted that way stops the upgrade instead of
    failing the next backup.
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
        authority = excess_authority_checks(
            literal(login), text_array(()), literal(BACKUP_SEQUENCES)
        )
        result += "".join(f" AND NOT {check}" for check in authority.values())
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


# The ``excess_authority_checks`` that refuse creating objects, by name.
CREATE_AUTHORITY = ("CREATE on a schema", "CREATE on the database")


def no_foreign_operators():
    """No operator outside pg_catalog except the pg_trgm extension's (#2166).

    An unqualified operator is resolved through search_path by the closest
    argument-type match in any schema on it, so a look-alike in public could
    win over pg_catalog's in a function that runs with its owner's rights.
    pg_trgm (0117, #765), which the operator installs in public, adds only
    text similarity and distance operators; those, as members of that
    extension in its own schema, are the only ones admitted.
    """
    return (
        "NOT EXISTS(SELECT 1 FROM pg_operator o "
        "JOIN pg_namespace n ON n.oid=o.oprnamespace "
        "WHERE n.nspname<>'pg_catalog' AND NOT EXISTS(SELECT 1 FROM pg_depend d "
        "JOIN pg_extension e ON e.oid=d.refobjid "
        "WHERE d.classid='pg_operator'::regclass AND d.objid=o.oid "
        "AND d.refclassid='pg_extension'::regclass AND d.deptype='e' "
        "AND e.extname='pg_trgm' AND e.extnamespace=n.oid))"
    )


def no_create(login):
    """The login may create nothing in an application schema or the database.

    Every runtime login's admission refuses this at startup; checking it
    here too means an upgrade onto such a catalog stops before its services
    would, and that no runtime login could add an operator (#2166). The
    backup login's ``no_excess`` already renders the same checks.
    """
    authority = excess_authority_checks(literal(login), text_array(()), "'USAGE'")
    return " AND ".join(f"NOT {authority[name]}" for name in CREATE_AUTHORITY)


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
        no_foreign_operators(),
    ]
    for _, login, role, target in database_identities():
        reader = role is ServiceRole.BACKUP_WORKER
        login_checks = [
            role_matches(
                login,
                marker,
                role_limit(configuration, role),
                reader=reader,
                exempt=migration_exemption(role),
            )
        ]
        if role is not ServiceRole.MIGRATION:
            # The schema owner receives no runtime grants.
            tables, columns = runtime_grants(role, target=target)
            login_checks.append(grants_held(login, tables, columns))
            login_checks.append(no_excess(login, tables, columns, reader=reader))
            if not reader:
                login_checks.append(no_create(login))
        checks.append(login_present(login, login_checks))
    checks += [inspect_login.role_matches(marker), inspect_login.grants_exact()]
    proof = "\n AND ".join(f"({check})" for check in checks)
    # The answer is text, printed by psql exactly as the boolean was: t or
    # f. An inspection login that owns objects answers its cause instead
    # (#2291), which is not t either; coalesce evaluates left to right and
    # stops at the first non-NULL argument, so the cause comes first.
    return (
        f"SELECT coalesce({inspect_login.ownership_cause()},\n"
        f"CASE WHEN ({proof}) THEN 't' ELSE 'f' END);\n"
    )
