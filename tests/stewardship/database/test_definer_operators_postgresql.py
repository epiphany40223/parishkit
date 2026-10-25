"""Operator lookup cannot be hijacked in the installed schema (#2166, 0179).

An unqualified operator is resolved through ``search_path``, and PostgreSQL
picks the closest argument-type match among every schema on the path, so a
look-alike operator in ``public`` could win over ``pg_catalog``'s inside a
function that runs with its owner's rights. Two controls keep that out: no
role but the schema owner may create anything in an application schema (so
no operator exists outside ``pg_catalog`` except the ``pg_trgm`` extension's
own), and every SECURITY DEFINER function names ``pg_catalog``'s jsonb and
containment operators explicitly (migration 0179).
"""

import hashlib
import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from ..sql_operators import bare_operators

pytestmark = pytest.mark.django_db

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = SCHEMA / "migrations/0179_definer_operators.sql"
QUALIFIED = re.compile(r"OPERATOR\(pg_catalog\.([^)]+)\)")
REPLACED = re.compile(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", re.M)
# The one replaced function no frozen file defined before 0179: its earlier
# statement is the baseline's (production.sql before #2166, as CREATE OR
# REPLACE), pinned by its sha256 since the baseline now carries 0179's body.
BASELINE_ONLY = {
    "stewardship_production_checkpoint_v1": (
        "893361bd5b67e61a671db51bd7c9bdc8fd6043a01691c741d59200e520969402"
    ),
}
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


def statements(text):
    """Map each function 0179 replaces to its whole replacement statement."""
    found = {}
    for match in REPLACED.finditer(text):
        quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, match.start())
        end = text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
        found[match[1]] = text[match.start() : end]
    return found


def earlier_statement(name):
    """The latest definition of ``name`` before 0179, as its file wrote it.

    A frozen file replaced or created it, or (for a function no frozen file
    touched before 0179) only the baseline defines it. Returned as a
    ``CREATE OR REPLACE`` statement, or None when only the baseline has it:
    the baseline now carries 0179's body.
    """
    latest = None
    for path in sorted((SCHEMA / "migrations").glob("*.sql")):
        if path.name >= FROZEN.name:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            rf"^CREATE (OR REPLACE )?FUNCTION public\.{name}\(", text, re.M
        ):
            latest = (text, match.start())
    if latest is None:
        return None
    text, start = latest
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, start)
    end = text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
    statement = text[start:end]
    return statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


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


def test_every_definer_function_names_pg_catalogs_operators():
    """No installed SECURITY DEFINER function uses ?, ?|, ?&, @> or <@ bare."""
    definers = rows(
        "SELECT p.oid::regprocedure::text, p.prosrc FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public' AND p.prosecdef"
    )
    assert len(definers) > 14
    for signature, body in definers:
        assert bare_operators(body) == [], signature


def test_0179_changes_only_the_operators_of_each_earlier_body():
    """Each replacement is its earlier statement with operators qualified."""
    replaced = statements(FROZEN.read_text(encoding="utf-8"))
    assert len(replaced) == 14
    for name, statement in replaced.items():
        assert bare_operators(statement) == [], name
        assert QUALIFIED.search(statement), name
        earlier = earlier_statement(name)
        unqualified = QUALIFIED.sub(r"\1", statement)
        if earlier is None:
            digest = hashlib.sha256(unqualified.encode()).hexdigest()
            assert digest == BASELINE_ONLY[name], name
        else:
            assert name not in BASELINE_ONLY
            assert unqualified == earlier, name


def test_the_do_block_passes_installed_and_refuses_each_old_definition():
    """0179's closing block passes as installed and refuses each older state.

    Each refusal runs in a transaction that rolls back, so the installed
    definitions stay. The old definition is 0179's statement with its
    operators unqualified again, which the test above proves is the earlier
    file's text; it keeps its rights, so only the body differs.
    """
    text = FROZEN.read_text(encoding="utf-8")
    block = text[text.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(block)
    undo = [
        # The earlier body with 0179's rights statement after it, so the
        # body alone differs.
        QUALIFIED.sub(r"\1", body)
        + "\n"
        + re.search(
            rf"^ALTER FUNCTION public\.{name}\([^)]*\) SECURITY DEFINER;$", text, re.M
        )[0]
        for name, body in statements(text).items()
    ]
    function = "public.stewardship_delivery_resolve_closed_v1(uuid)"
    undo += [
        f"ALTER FUNCTION {function} SECURITY INVOKER",
        f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC",
        f"ALTER FUNCTION {function} RESET search_path",
        f"ALTER FUNCTION {function} SET search_path TO public, pg_catalog",
    ]
    for statement in undo:
        with (
            pytest.raises(DatabaseError, match="Migration 0179"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
            cursor.execute(block)
