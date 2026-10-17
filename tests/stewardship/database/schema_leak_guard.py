"""Name the transactional database test that leaves a schema change behind (#1489).

A ``django_db(transaction=True)`` test commits what it runs, and pytest-django's
flush between tests empties tables but never restores the schema. A test that
re-runs a frozen migration file, or alters a constraint, function or trigger to
prove a migration's check, and commits that change, breaks some later test in
the same CI partition instead of failing itself. The database conftest uses
``SchemaGuard`` to fail the leaking test at its own teardown.

Checking costs two tiers. ``catalog_digest`` hashes object identities and
catalog row versions (``xmin``) without deparsing anything; committed DDL
changes it, while a flush (TRUNCATE) or ANALYZE leave it alone. Only when it
changes does the guard take the schema-baseline test's full ``inventory`` and
compare definitions, so a ``CREATE OR REPLACE`` with an identical body passes.
"""

from contextlib import contextmanager

from parishkit.stewardship.schema_inventory import inventory

# pg_class rows get a new xmin on every TRUNCATE (a new relfilenode), so the
# digest lists their definitional columns instead. The other catalogs change
# only through DDL, so an object's oid and row version stand for its definition.
CATALOG_DIGEST = """
WITH ns AS (SELECT oid FROM pg_namespace WHERE nspname = 'public'),
rel AS (SELECT oid FROM pg_class WHERE relnamespace = (SELECT oid FROM ns))
SELECT md5(string_agg(entry, ',' ORDER BY entry)) FROM (
  SELECT concat_ws(':', 'c', oid, relname, relkind, relowner, relacl,
                   relrowsecurity, relforcerowsecurity, reloptions,
                   relpersistence) AS entry
    FROM pg_class WHERE relnamespace = (SELECT oid FROM ns)
  UNION ALL SELECT concat_ws(':', 'a', attrelid, attnum, xmin)
    FROM pg_attribute WHERE attrelid IN (SELECT oid FROM rel) AND attnum > 0
  UNION ALL SELECT concat_ws(':', 'd', oid, xmin)
    FROM pg_attrdef WHERE adrelid IN (SELECT oid FROM rel)
  UNION ALL SELECT concat_ws(':', 'k', oid, xmin)
    FROM pg_constraint WHERE connamespace = (SELECT oid FROM ns)
  UNION ALL SELECT concat_ws(':', 'i', indexrelid, xmin)
    FROM pg_index WHERE indrelid IN (SELECT oid FROM rel)
  UNION ALL SELECT concat_ws(':', 'p', oid, xmin)
    FROM pg_proc WHERE pronamespace = (SELECT oid FROM ns)
  UNION ALL SELECT concat_ws(':', 't', oid, xmin)
    FROM pg_trigger WHERE tgrelid IN (SELECT oid FROM rel)
  UNION ALL SELECT concat_ws(':', 'r', oid, xmin)
    FROM pg_rewrite WHERE ev_class IN (SELECT oid FROM rel)
  UNION ALL SELECT concat_ws(':', 'o', oid, xmin)
    FROM pg_policy WHERE polrelid IN (SELECT oid FROM rel)
  UNION ALL SELECT concat_ws(':', 's', seqrelid, xmin)
    FROM pg_sequence WHERE seqrelid IN (SELECT oid FROM rel)
) entries
"""

FIX = (
    "Run the change inside transaction.atomic() with "
    "transaction.set_rollback(True), or restore it exactly before the test "
    "ends: the per-test flush empties tables but keeps schema changes, so "
    "later tests in the same CI partition would run against it."
)


def catalog_digest(connection):
    """A cheap hash that changes whenever DDL commits in the public schema."""
    with connection.cursor() as cursor:
        cursor.execute(CATALOG_DIGEST)
        return cursor.fetchone()[0]


def describe(before, after):
    """Name each object ``after`` added, removed or changed, by inventory kind."""
    lines = []
    for kind in sorted(before.keys() | after.keys()):
        old, new = before.get(kind, {}), after.get(kind, {})
        for name in sorted(old.keys() | new.keys()):
            if name not in new:
                lines.append(f"{kind}: removed {name}")
            elif name not in old:
                lines.append(f"{kind}: added {name}")
            elif old[name] != new[name]:
                lines.append(f"{kind}: changed {name}")
    return lines


class SchemaGuard:
    """The schema as last seen, compared after each transactional test.

    ``start`` runs at a test's setup and ``finish`` at its teardown. Schema
    changes between tests (a module fixture's setup or restore) are taken
    silently at the next ``start``, so only a change made during a test is
    reported, and only against that test. After a report the drifted schema
    becomes the new baseline, so later tests are not blamed for the same leak.
    """

    def __init__(self):
        """Begin with nothing seen; the first ``start`` takes a full inventory."""
        self.digest = None
        self.objects = None

    def start(self, connection):
        """Record the schema as the test begins, reusing the last inventory."""
        digest = catalog_digest(connection)
        if digest != self.digest:
            self.digest, self.objects = digest, inventory(connection)

    def finish(self, connection):
        """Return the changes the test left behind, or an empty list."""
        digest = catalog_digest(connection)
        if digest == self.digest:
            return []
        objects = inventory(connection)
        changes = describe(self.objects, objects)
        self.digest, self.objects = digest, objects
        return changes


def failure(changes):
    """The teardown message naming every leaked change and how to fix it."""
    listed = "\n".join(f"  {line}" for line in changes)
    return f"This test left schema changes behind:\n{listed}\n{FIX}"


@contextmanager
def schema_connection(connection):
    """The connection the teardown check reads the catalogs on.

    The test's own connection, unless the test left it closed or inside an
    atomic block. Then a short-lived copy reads them instead, so the check
    never reopens a connection that a test's monkeypatch forbids (the
    stewardship conftest's autouse fixtures request ``monkeypatch`` first, so
    it is undone only after this teardown) and never reads a test's
    uncommitted work.
    """
    if connection.connection is not None and not connection.in_atomic_block:
        yield connection
        return
    probe = connection.copy(alias="schema-leak-guard")
    try:
        yield probe
    finally:
        probe.close()
