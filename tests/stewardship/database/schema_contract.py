"""Compare current Django DDL bidirectionally with the installed fresh schema."""

from collections import Counter
from contextlib import contextmanager
from uuid import uuid4

from django.db import transaction

# These four reviewed SQL-only checks call the closed logging schema or protect
# its discriminator values. Together with constraint triggers, their exact DDL
# is covered by schema-baseline.json, not Django's model declarations. Do not
# derive this list from a live catalog or from the models under test.
SQL_ONLY_CHECKS = {
    "stewardship_audit_context": {
        "audit_context_actor_kind",
        "audit_context_schema_safe",
    },
    "stewardship_operational_log": {
        "operational_context_safe",
        "operational_event_safe",
    },
}


def index_signature(cursor, table, name):
    """Include operator classes, collations and ordering, not just key expressions."""
    cursor.execute(
        "SELECT x.indnkeyatts,x.indisunique,x.indisprimary,x.indisexclusion,"
        "x.indnullsnotdistinct,am.amname,i.reloptions,"
        "ARRAY(SELECT pg_get_indexdef(i.oid,n,true) "
        "FROM generate_series(1,x.indnatts) n),"
        "pg_get_expr(x.indpred,x.indrelid),pg_get_expr(x.indexprs,x.indrelid),"
        "ARRAY(SELECT ns.nspname||'.'||opc.opcname FROM unnest(x.indclass) "
        "WITH ORDINALITY k(oid,n) JOIN pg_opclass opc ON opc.oid=k.oid "
        "JOIN pg_namespace ns ON ns.oid=opc.opcnamespace ORDER BY n),"
        "ARRAY(SELECT CASE WHEN k.oid=0 THEN '' ELSE k.oid::regcollation::text END "
        "FROM unnest(x.indcollation) WITH ORDINALITY k(oid,n) ORDER BY n),"
        "x.indoption::smallint[] "
        "FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid "
        "JOIN pg_am am ON am.oid=i.relam "
        "WHERE x.indrelid=%s::regclass AND i.relname=%s",
        [table, name],
    )
    return cursor.fetchone()


def _freeze(value):
    """Make catalog arrays suitable for multiset comparison without losing order."""
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _catalog(cursor, table, model, schema):
    """Read all columns, constraints and indexes, retaining explicit model names."""
    cursor.execute(
        "SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
        "a.attidentity,a.attgenerated,a.attcollation::regcollation::text,"
        "pg_get_expr(d.adbin,d.adrelid) "
        "FROM pg_attribute a LEFT JOIN pg_attrdef d "
        "ON a.attrelid=d.adrelid AND a.attnum=d.adnum "
        "WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped",
        [table],
    )
    columns = Counter(cursor.fetchall())
    declared = {item.name for item in [*model._meta.constraints, *model._meta.indexes]}
    sql_only = SQL_ONLY_CHECKS.get(model._meta.db_table, set())
    assert not declared & sql_only
    cursor.execute(
        "SELECT conname,contype,condeferrable,condeferred,pg_get_constraintdef(oid) "
        # Django declares no constraint triggers. Their complete definitions and
        # bindings are checked independently by the strict baseline inventory.
        "FROM pg_constraint WHERE conrelid=%s::regclass AND contype<>'t'",
        [table],
    )
    rows = cursor.fetchall()
    if table.startswith("public."):
        assert sql_only <= {row[0] for row in rows}
    constraints = Counter(
        (
            name if name in declared else None,
            kind,
            deferred,
            initially,
            definition.replace("REFERENCES public.", "REFERENCES ").replace(
                f"REFERENCES {schema}.", "REFERENCES "
            ),
        )
        for name, kind, deferred, initially, definition in rows
        if name not in sql_only
    )
    cursor.execute(
        "SELECT i.relname FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid "
        "WHERE x.indrelid=%s::regclass",
        [table],
    )
    names = [row[0] for row in cursor.fetchall()]
    indexes = Counter(
        (
            name if name in declared else None,
            _freeze(index_signature(cursor, table, name)),
        )
        for name in names
    )
    return {"columns": columns, "constraints": constraints, "indexes": indexes}


@contextmanager
def model_catalogs(connection, model):
    """Compile current models independently in a transaction-rolled-back schema.

    No installed table is altered and no declaration is inherited with LIKE.
    Public tables may satisfy foreign-key references; the scratch table remains
    empty. Rollback removes only objects created by this transaction.
    """
    schema = "model_contract_" + uuid4().hex
    quote = connection.ops.quote_name
    table = model._meta.db_table
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"CREATE SCHEMA {quote(schema)}")
        cursor.execute(f"SET LOCAL search_path = {quote(schema)}, public, pg_catalog")
        with connection.schema_editor() as editor:
            editor.create_model(model)
        # The fresh baseline has been deparsed by pg_dump once. Reparse current
        # CHECKs once too, to normalize PostgreSQL's redundant literal-array casts.
        cursor.execute(
            "SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid=%s::regclass AND contype='c'",
            [f"{schema}.{table}"],
        )
        for name, definition in cursor.fetchall():
            cursor.execute(f"ALTER TABLE {quote(table)} DROP CONSTRAINT {quote(name)}")
            cursor.execute(
                f"ALTER TABLE {quote(table)} ADD CONSTRAINT {quote(name)} {definition}"
            )
        cursor.execute(
            "SELECT i.relname,pg_get_indexdef(i.oid) FROM pg_index x "
            "JOIN pg_class i ON i.oid=x.indexrelid "
            "WHERE x.indrelid=%s::regclass AND NOT EXISTS "
            "(SELECT 1 FROM pg_constraint c WHERE c.conindid=i.oid)",
            [f"{schema}.{table}"],
        )
        for name, definition in cursor.fetchall():
            cursor.execute(f"DROP INDEX {quote(schema)}.{quote(name)}")
            cursor.execute(definition)
        yield (
            _catalog(cursor, f"public.{table}", model, schema),
            _catalog(cursor, f"{schema}.{table}", model, schema),
        )
        transaction.set_rollback(True)


def assert_model_contract(connection, model):
    """Detect additions, changes and removals, including implicit field protections."""
    with model_catalogs(connection, model) as (actual, expected):
        assert actual == expected, {
            kind: {
                "installed_only": list((actual[kind] - expected[kind]).elements()),
                "model_only": list((expected[kind] - actual[kind]).elements()),
            }
            for kind in actual
            if actual[kind] != expected[kind]
        }
