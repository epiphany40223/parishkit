"""Install exact application fixture grants with fewer SQL setup round trips."""

from collections import defaultdict

from django.db import transaction
from psycopg import sql


def grant_runtime(cursor, role_name, tables, columns):
    """Group identical table grants, never merge or widen column privileges.

    Call only as the disposable schema owner, before switching session identity.
    Each caller still creates and destroys its own role, runs real admission and
    owns reconnection handling. A short transaction installs the complete grant
    set before another connection can use it; no application work runs inside.
    """
    role = sql.Identifier(role_name)
    groups = defaultdict(list)
    for table, privileges in tables.items():
        groups[tuple(sorted(privileges))].append(table)
    with transaction.atomic(using=cursor.db.alias):
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        for privileges, names in sorted(groups.items()):
            cursor.execute(
                sql.SQL("GRANT {} ON {} TO {}").format(
                    sql.SQL(", ").join(sql.SQL(value) for value in privileges),
                    sql.SQL(", ").join(
                        sql.Identifier("public", name) for name in sorted(names)
                    ),
                    role,
                )
            )
        for table, privileges in sorted(columns.items()):
            clauses = [
                sql.SQL("{} ({})").format(
                    sql.SQL(privilege),
                    sql.SQL(", ").join(sql.Identifier(name) for name in sorted(names)),
                )
                for privilege, names in sorted(privileges.items())
            ]
            cursor.execute(
                sql.SQL("GRANT {} ON {} TO {}").format(
                    sql.SQL(", ").join(clauses), sql.Identifier("public", table), role
                )
            )
