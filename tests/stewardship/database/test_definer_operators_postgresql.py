"""Operator lookup cannot be hijacked in the installed schema (#2166).

An unqualified operator is resolved through ``search_path``, and PostgreSQL
picks the closest argument-type match among every schema on the path, so a
look-alike operator in ``public`` could win over ``pg_catalog``'s inside a
function that runs with its owner's rights. What keeps that out is that no
role but the schema owner may create anything in an application schema, so
no operator exists outside ``pg_catalog`` except the ``pg_trgm`` extension's
own.
"""

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db

# pg_trgm (0117, #765) is the one extension, installed in public; its
# operators are similarity and distance operators on text, none of them a
# jsonb or containment operator.
TRIGRAM_OPERATORS = {
    "%",
    "<%",
    "%>",
    "<<%",
    "%>>",
    "<->",
    "<<->",
    "<->>",
    "<<<->",
    "<->>>",
}


def rows(sql, params=()):
    """Every row of one catalog query on the test's own connection."""
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def test_no_operator_exists_outside_pg_catalog_but_the_trigram_extensions():
    """Nothing but pg_trgm adds an operator any search path could find."""
    found = rows(
        "SELECT n.nspname, o.oprname, EXISTS(SELECT 1 FROM pg_depend d "
        "JOIN pg_extension e ON e.oid=d.refobjid "
        "WHERE d.classid='pg_operator'::regclass AND d.objid=o.oid "
        "AND d.refclassid='pg_extension'::regclass AND d.deptype='e' "
        "AND e.extname='pg_trgm') "
        "FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace "
        "WHERE n.nspname<>'pg_catalog'"
    )
    assert found, "pg_trgm's operators are expected in public"
    assert {(schema, trigram) for schema, _, trigram in found} == {("public", True)}
    assert {name for _, name, _ in found} == TRIGRAM_OPERATORS


# The application schemas: every schema but PostgreSQL's own.
APPLICATION = "n.nspname !~ '^pg_' AND n.nspname<>'information_schema'"


def test_only_schema_owners_may_create_in_an_application_schema():
    """No ordinary role may create an object in a schema or the database.

    PUBLIC, and every role that is neither a superuser nor a member of the
    schema's (or database's) owner, is refused, so no one but the schema
    owner (the migration login) could add a look-alike operator or function.
    """
    assert (
        rows(
            f"SELECT n.nspname FROM pg_namespace n WHERE {APPLICATION} "
            "AND has_schema_privilege('public', n.oid, 'CREATE')"
        )
        == []
    )
    assert (
        rows(
            "SELECT r.rolname, n.nspname FROM pg_roles r CROSS JOIN pg_namespace n "
            f"WHERE NOT r.rolsuper AND {APPLICATION} "
            "AND NOT pg_has_role(r.oid, n.nspowner, 'MEMBER') "
            "AND has_schema_privilege(r.oid, n.oid, 'CREATE')"
        )
        == []
    )
    assert (
        rows(
            "SELECT r.rolname FROM pg_roles r, pg_database d "
            "WHERE d.datname=current_database() AND NOT r.rolsuper "
            "AND NOT pg_has_role(r.oid, d.datdba, 'MEMBER') "
            "AND has_database_privilege(r.oid, d.oid, 'CREATE') "
            "UNION ALL SELECT 'PUBLIC' "
            "WHERE has_database_privilege('public', current_database(), 'CREATE')"
        )
        == []
    )
