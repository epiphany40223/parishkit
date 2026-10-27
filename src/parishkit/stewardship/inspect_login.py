"""The SELECT-only database login an operator inspects Production with (#2148).

Inspection (counts, mail-queue checks, pre/post-deploy checks) used to run
``psql`` as the cluster superuser, kept read-only only by a ``READ ONLY``
transaction that one statement can lift. ``pk_stewardship_inspect`` is a
login with no write privilege at all, so a mistaken statement fails in
PostgreSQL itself.

It is not part of ``runtime_identities.database_identities()``: that
inventory is the password-bearing service logins, each with a mounted
password file and a TCP source (#2111). This login has no password, so no
TCP rule (all ``scram-sha-256``) can ever admit it; it signs in only over
the PostgreSQL container's socket, by ``docker exec`` (the ``pk-inspect``
host wrapper). The operator creates it in ``database-roles`` and
``database-grants`` (the migration login cannot create roles) and grants
it SELECT in ``database-grants``; the upgrade check requires the same.

What it may read is decided from the live catalog at grant time: every
relation in ``public`` except the credential material below and anything
under row-level security, and no view that could show either. One SQL
definition (``expected_selects``) serves the grants, their admission and
the upgrade check, so they cannot disagree. See
docs/specs/stewardship/operations/spec.md#database-inspection-login.
"""

from psycopg import sql

from parishkit.config import ConfigError

INSPECT_LOGIN = "pk_stewardship_inspect"
# An interactive session plus a script or two at once. Like the operator's
# own sessions these are outside the services' connection budget, which is
# why the limit is small; superuser_reserved_connections still keeps the
# operator able to connect when every slot is in use.
INSPECT_CONNECTIONS = 3
# Role settings, as pg_db_role_setting stores them. Read-only by default;
# a short lock wait, so an inspection gives up instead of queueing behind
# (and so blocking everything behind) an upgrade's locks; bounded statements
# and idle sessions, so a forgotten terminal frees its connection.
INSPECT_SETTINGS = (
    "default_transaction_read_only=on",
    "search_path=pg_catalog, public",
    "statement_timeout=5min",
    "lock_timeout=5s",
    "idle_in_transaction_session_timeout=10min",
    "idle_session_timeout=60min",
)
# The one membership, as role:inherit:set:admin. pg_read_all_stats shows
# every session's activity and statistics; SET FALSE refuses SET ROLE to it,
# so the login cannot change identity at all.
INSPECT_MEMBERSHIP = "pg_read_all_stats:true:false:false"

# Whole tables never readable: live Django session keys and OAuth tokens.
# Every table under row-level security (the credential handoff tables) is
# excluded too, by the catalog rather than by name (``expected_selects``).
EXCLUDED_TABLES = frozenset({"django_session", "socialaccount_socialtoken"})
# Columns never readable; the rest of each table is, by column grants.
EXCLUDED_COLUMNS = {
    "account_emailconfirmation": frozenset({"key"}),
    "auth_user": frozenset({"password"}),
    "socialaccount_socialapp": frozenset({"secret", "key"}),
    # The automation session secret's verifier.
    "stewardship_automation_session": frozenset({"secret_digest"}),
    # Each session's Django session key, the live cookie value.
    "stewardship_portal_session": frozenset({"session_id"}),
    "stewardship_family_session": frozenset({"session_id"}),
    # Emailed Family codes and links, sealed or as their verifiers.
    "stewardship_family_campaign": frozenset({"code_ciphertext"}),
    "stewardship_family_token": frozenset({"ciphertext", "digest"}),
    "stewardship_family_code_mac": frozenset({"digest"}),
    "stewardship_rehearsal_code_mac": frozenset({"digest"}),
    "stewardship_rehearsal_reservation": frozenset({"digest"}),
    "stewardship_rehearsal_credential": frozenset(
        {"code_ciphertext", "token_ciphertext", "token_digest"}
    ),
    # A message's sealed link substitutions.
    "stewardship_outbox_message": frozenset({"sealed_substitutions"}),
    "stewardship_outbox_address_retry": frozenset({"sealed_substitutions"}),
    # Setup's sealed credential exchanges.
    "stewardship_setup_mail_exchange": frozenset({"ciphertext"}),
    "stewardship_setup_source_exchange": frozenset({"ciphertext"}),
}
# Column names that look like credential material but were reviewed and are
# not (identifiers, key ids, a public file token). A PostgreSQL test fails
# when a column matching CREDENTIAL_NAME is neither excluded nor listed here,
# so a migration cannot add such a column without a decision.
CREDENTIAL_NAME = (
    r"cipher|secret|password|passw|token|sealed|private|verifier|nonce|salt|hash"
)
REVIEWED_COLUMNS = frozenset(
    {
        ("stewardship_campaign", "active_token_generation_id"),
        ("stewardship_campaign_config_intent", "token_generation_id"),
        ("stewardship_campaign_transition", "token_generation_id"),
        ("stewardship_outbox_message", "token_generation_id"),
        ("stewardship_outbox_message", "sealed_key_id"),
        ("stewardship_outbox_address_retry", "token_generation_id"),
        ("stewardship_outbox_address_retry", "sealed_key_id"),
        # Hosted images' public URL token, shown in pages and emails.
        ("stewardship_hosted_file", "token"),
    }
)

# Views that read a table with excluded columns and were reviewed to show
# none of them. PostgreSQL records a whole-row reference (``SELECT t``,
# ``to_jsonb(t)``) only as a dependency on the whole table, and not at all
# when the view also names one of its columns, so the catalog cannot tell
# such a view apart: every other view over these tables gets no grant until
# it is reviewed and listed here. A change to a listed view's definition
# needs the same review.
REVIEWED_VIEWS = frozenset(
    {
        "stewardship_daily_digest_completion_ready",
        "stewardship_daily_digest_work_row",
        "stewardship_delivery_closed_coverage_summary",
        "stewardship_delivery_control_inventory",
        "stewardship_delivery_family_recovery",
        "stewardship_delivery_stranded",
        "stewardship_delivery_uncertain",
        "stewardship_hosted_file_use",
        "stewardship_report_email_completion_ready",
        "stewardship_report_email_work_row",
        "stewardship_schedule_work_row",
        "stewardship_weekly_digest_completion_ready",
        "stewardship_weekly_digest_work_row",
        "stewardship_withdrawal_inventory",
    }
)

# The relations inspection considers: ordinary, partitioned, views and
# materialized views in public (no foreign tables exist or are granted).
_RELATIONS = (
    "SELECT c.oid,c.relname::text AS relname,c.relrowsecurity FROM pg_class c "
    "JOIN pg_namespace n ON n.oid=c.relnamespace "
    "WHERE n.nspname='public' AND c.relkind IN('r','p','v','m')"
)


def _literal(value):
    """Quote one code-owned string for SQL; standard_conforming_strings is on."""
    return "'" + str(value).replace("'", "''") + "'"


def _array(values):
    """A text[] literal, typed so that an empty list is still valid SQL."""
    return "ARRAY[" + ",".join(_literal(value) for value in values) + "]::text[]"


def expected_selects():
    """SQL listing every SELECT grant the login must hold: ``(relname, attname)``.

    ``attname`` is NULL for a whole-table grant. A relation is hidden when it
    is excluded by name, is under row-level security, or is a view (or
    materialized view) whose rule reads a hidden relation, an excluded
    column, any column of a table with excluded columns (unless the view is
    in ``REVIEWED_VIEWS``: a whole-row reference leaves no column
    dependency), or calls a SECURITY DEFINER routine: a view reads with its
    owner's privileges, so the login's own column grants would not protect
    what it shows. A partition or inheritance child of a hidden table, or of
    a table with excluded columns (whose exclusions name the parent), is
    hidden too. The recursion follows views built on hidden relations and
    children of hidden tables.
    """
    columns = sorted(
        (table, column) for table, names in EXCLUDED_COLUMNS.items() for column in names
    )
    return (
        "WITH RECURSIVE rel AS (" + _RELATIONS + "), "
        "secret(relid,attnum) AS (SELECT a.attrelid,a.attnum FROM pg_attribute a "
        "JOIN rel ON rel.oid=a.attrelid WHERE a.attnum>0 AND NOT a.attisdropped "
        "AND (rel.relname,a.attname::text) IN (SELECT * FROM unnest("
        f"{_array(t for t, _ in columns)},{_array(c for _, c in columns)}))), "
        # A view's dependencies are its rewrite rule's (pg_depend classid
        # pg_rewrite); refobjsubid is the column, 0 for a whole relation.
        "uses AS (SELECT r.ev_class AS view,d.refclassid,d.refobjid,d.refobjsubid "
        "FROM pg_depend d JOIN pg_rewrite r ON r.oid=d.objid "
        "WHERE d.classid='pg_rewrite'::regclass AND d.refobjid<>r.ev_class), "
        # What hiding a relation also hides: the views reading it and its
        # partitions or inheritance children.
        "follows(child,parent) AS (SELECT view,refobjid FROM uses "
        "WHERE refclassid='pg_class'::regclass "
        "UNION SELECT inhrelid,inhparent FROM pg_inherits), "
        "hidden(oid) AS ("
        "SELECT oid FROM rel WHERE relrowsecurity "
        f"OR relname=ANY({_array(sorted(EXCLUDED_TABLES))}) "
        "UNION SELECT inhrelid FROM pg_inherits "
        "WHERE inhparent IN (SELECT relid FROM secret) "
        "UNION SELECT u.view FROM uses u WHERE u.refclassid='pg_class'::regclass "
        "AND ((u.refobjid,u.refobjsubid) IN (SELECT relid,attnum FROM secret) "
        "OR (u.refobjid IN (SELECT relid FROM secret) AND u.view NOT IN "
        "(SELECT oid FROM rel WHERE relname="
        f"ANY({_array(sorted(REVIEWED_VIEWS))})))) "
        "UNION SELECT u.view FROM uses u JOIN pg_proc p ON p.oid=u.refobjid "
        "WHERE u.refclassid='pg_proc'::regclass AND p.prosecdef "
        "UNION SELECT f.child FROM follows f JOIN hidden h ON h.oid=f.parent) "
        "SELECT rel.relname,NULL::text AS attname FROM rel "
        "WHERE rel.oid NOT IN (SELECT oid FROM hidden) "
        "AND rel.oid NOT IN (SELECT relid FROM secret) "
        "UNION ALL SELECT rel.relname,a.attname::text FROM rel "
        "JOIN pg_attribute a ON a.attrelid=rel.oid AND a.attnum>0 "
        "AND NOT a.attisdropped "
        "WHERE rel.oid NOT IN (SELECT oid FROM hidden) "
        "AND rel.oid IN (SELECT relid FROM secret) "
        "AND (a.attrelid,a.attnum) NOT IN (SELECT relid,attnum FROM secret)"
    )


# The documented clean-up (#2291) for the one kind of object the login can
# come to own: a large object, which PUBLIC may create with lo_create once a
# session lifts the read-only default. The operator runs it in the database
# the refusal names. DROP OWNED BY would also revoke the login's CONNECT and
# USAGE, which database-grants refuses rather than repairs, so it is not
# the clean-up.
CLEANUP = (
    "SELECT lo_unlink(oid) FROM pg_largeobject_metadata "
    f"WHERE lomowner={_literal(INSPECT_LOGIN)}::regrole"
)


def ownership_cause():
    """SQL text: why the login is refused for owning objects, or NULL.

    Owning any object is isolation drift (``ISOLATION_DRIFT``), which
    ``role_matches`` refuses. This names what is owned, per database and
    kind ("large objects in database stewardship: 2"), and the documented
    clean-up when only large objects are owned (otherwise, to investigate),
    so ``database-grants`` and the upgrade check can say more than
    "differs". It is NULL when the login owns nothing or is absent.
    pg_shdepend is cluster-wide, so an object in another database counts
    too, as the drift check counts it. Run as the operator.
    """
    large = "d.classid='pg_largeobject'::regclass"
    kind = (
        f"CASE WHEN {large} THEN 'large objects' "
        "ELSE 'objects in '||d.classid::regclass::text END"
    )
    database = "coalesce('database '||b.datname,'the cluster')"
    return (
        "(SELECT 'The inspection login owns objects ('"
        "||string_agg(o.what||': '||o.n,'; ' ORDER BY o.what)||'). '"
        # Only large objects have a documented clean-up; anything else
        # means authority the login should never have had.
        "||CASE WHEN bool_and(o.large) THEN 'As the operator, in each named "
        "database, run: " + CLEANUP.replace("'", "''") + "; then repeat this step' "
        "ELSE 'Only large objects have a documented clean-up: investigate "
        "before removing anything' END"
        "||' (deployment runbook, Clean-up when the inspection login owns "
        "objects).' "
        f"FROM (SELECT {kind}||' in '||{database} AS what,count(*) AS n,"
        f"bool_and({large}) AS large "
        "FROM pg_shdepend d LEFT JOIN pg_database b ON b.oid=d.dbid "
        "WHERE d.refclassid='pg_authid'::regclass "
        f"AND d.refobjid=to_regrole({_literal(INSPECT_LOGIN)}) AND d.deptype='o' "
        "GROUP BY 1) o)"
    )


def role_matches(marker):
    """SQL boolean: the login exists exactly as provisioning creates it.

    Its attributes and connection limit, no password, exactly its settings
    and its one membership, no TEMP on the database, none of the isolation
    drift every login is refused for (a member role, an owned object,
    foreign-data USAGE, a parameter privilege) and none of the excess
    authority the backup login is refused for (definer EXECUTE, a sequence
    privilege, CREATE on a schema or the database). Run as the operator:
    only a superuser reads pg_authid's password column.
    """
    from .database_provisioning import ISOLATION_DRIFT, excess_authority_checks

    login = _literal(INSPECT_LOGIN)
    authority = excess_authority_checks(login, _array(()), "'USAGE,UPDATE,SELECT'")
    checks = [
        f"shobj_description(r.oid,'pg_authid')={_literal(marker)}",
        "NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcreatedb",
        "NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolinherit",
        f"r.rolcanlogin AND r.rolconnlimit={INSPECT_CONNECTIONS}",
        "(SELECT a.rolpassword IS NULL FROM pg_authid a WHERE a.oid=r.oid)",
        "(SELECT array_agg(x ORDER BY x) FROM pg_db_role_setting s, "
        "unnest(s.setconfig) x WHERE s.setrole=r.oid) IS NOT DISTINCT FROM "
        f"(SELECT array_agg(x ORDER BY x) FROM unnest({_array(INSPECT_SETTINGS)}) x)",
        "NOT EXISTS(SELECT 1 FROM pg_db_role_setting s "
        "WHERE s.setrole=r.oid AND s.setdatabase<>0)",
        "(SELECT string_agg(m.rolname||':'||am.inherit_option::text||':'"
        "||am.set_option::text||':'||am.admin_option::text,',' ORDER BY m.rolname) "
        "FROM pg_auth_members am JOIN pg_roles m ON m.oid=am.roleid "
        f"WHERE am.member=r.oid)={_literal(INSPECT_MEMBERSHIP)}",
        f"has_database_privilege({login},current_database(),'CONNECT')",
        f"NOT has_database_privilege({login},current_database(),'TEMP')",
        *(f"NOT {check}" for check in ISOLATION_DRIFT.values()),
        *(f"NOT {check}" for check in authority.values()),
    ]
    return (
        f"EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname={login} AND "
        + " AND ".join(f"({check})" for check in checks)
        + ")"
    )


def _excess(*, reads):
    """SQL boolean over ``expected`` (a CTE of expected_selects): excess held.

    Any write, TRUNCATE, REFERENCES or TRIGGER anywhere, or any privilege in
    another schema, and with ``reads`` also any SELECT on a hidden relation
    or an excluded column. A whole-table grant shows as SELECT on each of
    its columns, so a column SELECT is excess only when neither its table
    nor that column is expected.
    """
    login = _literal(INSPECT_LOGIN)
    table = "e.attname IS NULL"
    column = "(e.attname IS NULL OR e.attname=a.attname::text)"
    excess = "p<>'SELECT' OR n.nspname<>'public'"

    def read_excess(match):
        """The clause that also counts an unexpected SELECT, when asked."""
        if not reads:
            return ""
        return (
            " OR NOT EXISTS(SELECT 1 FROM expected e "
            f"WHERE e.relname=c.relname::text AND {match})"
        )

    return (
        "EXISTS(SELECT 1 FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE',"
        "'REFERENCES','TRIGGER']) p WHERE n.nspname !~ '^pg_' "
        "AND n.nspname<>'information_schema' AND c.relkind IN('r','p','v','m','f') "
        f"AND has_table_privilege({login},c.oid,p) "
        f"AND ({excess}{read_excess(table)})) "
        "OR EXISTS(SELECT 1 FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
        "CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) p "
        "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' "
        "AND c.relkind IN('r','p','v','m','f') "
        f"AND has_column_privilege({login},c.oid,a.attnum,p) "
        f"AND ({excess}{read_excess(column)}))"
    )


# The login's own explicit SELECT grants in public that ``expected`` no
# longer gives (a view replaced to read a secret, a table put under
# row-level security, a newly excluded column), as ``(relname, attname)``
# rows, attname NULL for a whole-table grant. database-grants revokes them:
# that only narrows what the login reads.
_REVOCABLE = (
    "SELECT c.relname::text,NULL::text FROM pg_class c, aclexplode(c.relacl) x "
    "WHERE c.relnamespace='public'::regnamespace "
    "AND c.relkind IN('r','p','v','m','f') AND x.privilege_type='SELECT' "
    f"AND x.grantee=to_regrole({_literal(INSPECT_LOGIN)}) "
    "AND NOT EXISTS(SELECT 1 FROM expected e WHERE e.relname=c.relname::text "
    "AND e.attname IS NULL) "
    "UNION ALL SELECT c.relname::text,a.attname::text FROM pg_class c "
    "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped, "
    "aclexplode(a.attacl) x WHERE c.relnamespace='public'::regnamespace "
    "AND c.relkind IN('r','p','v','m','f') AND x.privilege_type='SELECT' "
    f"AND x.grantee=to_regrole({_literal(INSPECT_LOGIN)}) "
    "AND NOT EXISTS(SELECT 1 FROM expected e WHERE e.relname=c.relname::text "
    "AND (e.attname IS NULL OR e.attname=a.attname::text))"
)
# Any SELECT in ``expected`` the login does not hold yet.
_MISSING = (
    "EXISTS(SELECT 1 FROM expected e WHERE NOT CASE WHEN e.attname IS NULL "
    f"THEN has_table_privilege({_literal(INSPECT_LOGIN)},"
    "('public.'||quote_ident(e.relname))::regclass,'SELECT') "
    f"ELSE has_column_privilege({_literal(INSPECT_LOGIN)},"
    "('public.'||quote_ident(e.relname))::regclass,e.attname,'SELECT') END)"
)


def grants_exact():
    """SQL boolean: the login holds exactly ``expected_selects``, nothing more."""
    return (
        f"(WITH expected AS ({expected_selects()}) "
        f"SELECT NOT ({_MISSING}) AND NOT ({_excess(reads=True)}))"
    )


def ensure_inspect_login(cursor, marker, database):
    """Create the login when absent; refuse one that differs from provisioning.

    Runs as the operator in ``database-roles`` and ``database-grants``. An
    existing role is never repaired: a differing one (attributes, password,
    settings, membership, drift, authority) is refused for the operator to
    investigate, as every other login is; when it owns objects, the refusal
    is ``ownership_cause``'s sentence. A new one gets no password, so it
    can sign in only where the socket trusts it.
    """
    cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=%s)", [INSPECT_LOGIN]
    )
    if cursor.fetchone()[0]:
        cursor.execute("SELECT " + role_matches(marker))
        if cursor.fetchone() != (True,):
            # An owned object is the difference a stray inspection session
            # most likely causes (#2291), so name it and its clean-up. Any
            # other (a password or role setting the login changed on
            # itself, or an operator's change) is fixed by recreating it.
            cursor.execute("SELECT " + ownership_cause())
            raise ConfigError(
                cursor.fetchone()[0]
                or "The existing inspection login differs from initial "
                "provisioning (deployment runbook, Recreating the inspection "
                "login)."
            )
        return
    login = sql.Identifier(INSPECT_LOGIN)
    cursor.execute(
        sql.SQL(
            "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB "
            "NOCREATEROLE NOREPLICATION CONNECTION LIMIT {}"
        ).format(login, sql.Literal(INSPECT_CONNECTIONS))
    )
    cursor.execute(
        sql.SQL("COMMENT ON ROLE {} IS {}").format(login, sql.Literal(marker))
    )
    for setting in INSPECT_SETTINGS:
        name, value = setting.split("=", 1)
        cursor.execute(
            sql.SQL("ALTER ROLE {} SET {} = {}").format(
                login, sql.Identifier(name), sql.Literal(value)
            )
            if name != "search_path"
            # A list setting: quoted as one literal it would be one schema.
            else sql.SQL("ALTER ROLE {} SET search_path = pg_catalog, public").format(
                login
            )
        )
    cursor.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), login
        )
    )
    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(login))
    cursor.execute(
        sql.SQL("GRANT pg_read_all_stats TO {} WITH INHERIT TRUE, SET FALSE").format(
            login
        )
    )


def grant_inspect(cursor):
    """Grant the login exactly ``expected_selects``.

    Any write or other-schema privilege is refused, never repaired, so the
    operator investigates it, as for every other login. An explicit SELECT
    the catalog rule no longer gives (a granted view replaced to read a
    secret, a table put under row-level security, a newly excluded column)
    is revoked first, because that only narrows what the login reads and a
    frozen migration could not revoke it where the login does not exist.
    The final check refuses anything left over, such as a PUBLIC grant.
    """
    cursor.execute(
        f"WITH expected AS ({expected_selects()}) SELECT {_excess(reads=False)}"
    )
    if cursor.fetchone() != (False,):
        raise ConfigError(
            "Existing inspection login grants exceed provisioning intent."
        )
    login = sql.Identifier(INSPECT_LOGIN)
    cursor.execute(f"WITH expected AS ({expected_selects()}) {_REVOCABLE}")
    for table, columns in sorted(_by_table(cursor.fetchall()).items()):
        cursor.execute(_select_on("REVOKE", "FROM", table, columns, login))
    cursor.execute(expected_selects())
    for table, columns in sorted(_by_table(cursor.fetchall()).items()):
        cursor.execute(_select_on("GRANT", "TO", table, columns, login))
    cursor.execute(
        f"WITH expected AS ({expected_selects()}) SELECT {_excess(reads=True)}"
    )
    if cursor.fetchone() != (False,):
        raise ConfigError(
            "Existing inspection login grants exceed provisioning intent."
        )


def _by_table(rows):
    """``(relname, attname)`` rows as table -> sorted columns, [] for whole.

    A whole-table row wins over column rows of the same table: revoking the
    whole-table SELECT also revokes its column SELECTs.
    """
    tables, whole = {}, set()
    for table, column in rows:
        names = tables.setdefault(table, [])
        if column is None:
            whole.add(table)
        else:
            names.append(column)
    return {
        table: [] if table in whole else sorted(names)
        for table, names in tables.items()
    }


def _select_on(verb, direction, table, columns, login):
    """``GRANT``/``REVOKE SELECT [(columns)] ON public.table TO/FROM login``.

    Revoking a whole-table SELECT also revokes the table's column SELECTs;
    the grants that follow restore the expected ones.
    """
    if columns:
        privilege = sql.SQL("SELECT ({})").format(
            sql.SQL(", ").join(sql.Identifier(name) for name in columns)
        )
    else:
        privilege = sql.SQL("SELECT")
    return sql.SQL(verb + " {} ON {} " + direction + " {}").format(
        privilege, sql.Identifier("public", table), login
    )
