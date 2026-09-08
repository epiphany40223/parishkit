"""Frozen v1 PostgreSQL guard builder for concrete mutable record migrations.

Keep this version's semantics stable after deployment. A future guard change
must use a new builder/version and an explicit migration, not rewrite historical
migrations by silently changing this function. Each table owns its generated
function, so reversing one app cannot remove another app's guard.
"""

import re

from django.db import migrations


def _identifier(value):
    """Allow only short internal SQL names, leaving room for guard suffixes."""
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,39}", value) is None
    ):
        raise ValueError("Guard identifiers must be short internal SQL names.")
    return value


def mutable_guard_v1(table, *, frozen_fields=(), write_once_fields=()):
    """Build reversible, table-independent version and immutable-field guards."""
    table = _identifier(table)
    fields = tuple(dict.fromkeys(("id", "created_at", *frozen_fields)))
    predicates = [
        f'NEW."{_identifier(name)}" IS DISTINCT FROM OLD."{name}"' for name in fields
    ]
    predicates.extend(
        f'(OLD."{_identifier(name)}" IS NOT NULL AND '
        f'NEW."{name}" IS DISTINCT FROM OLD."{name}")'
        for name in write_once_fields
    )
    predicate = " OR ".join(predicates)
    function = f"{table}_mutable_v1"
    trigger = f"{table}_mutable_guard_v1"
    return migrations.RunSQL(
        sql=f"""
            CREATE FUNCTION "{function}"()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF {predicate} THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER "{trigger}"
            BEFORE UPDATE ON "{table}"
            FOR EACH ROW EXECUTE FUNCTION "{function}"();
        """,
        reverse_sql=f"""
            DROP TRIGGER "{trigger}" ON "{table}";
            DROP FUNCTION "{function}"();
        """,
    )


def immutable_guard_v1(table):
    """Build an append-only guard with the same SQLSTATE as mutable invariants.

    TRUNCATE/schema-owner privileges belong only to migrations and disposable
    tests, not runtime roles. Exceptional retention needs its own explicit
    reviewed migration/service rather than silently disabling this guard.
    """
    table = _identifier(table)
    function = f"{table}_immutable_v1"
    trigger = f"{table}_immutable_guard_v1"
    return migrations.RunSQL(
        sql=f"""
            CREATE FUNCTION "{function}"()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;
            CREATE TRIGGER "{trigger}"
            BEFORE UPDATE OR DELETE ON "{table}"
            FOR EACH ROW EXECUTE FUNCTION "{function}"();
        """,
        reverse_sql=f"""
            DROP TRIGGER "{trigger}" ON "{table}";
            DROP FUNCTION "{function}"();
        """,
    )
