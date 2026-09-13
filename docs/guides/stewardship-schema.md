# Stewardship schema baseline

[Database tests](stewardship-database-tests.md) ·
[Migration policy](../plans/stewardship/data.md#migration-policy) ·
[Consolidation evidence](stewardship-phase-2-simplification.md)

Migrations are Django's versioned instructions for building or updating a
database schema. They are still required to create tables, indexes, constraints,
functions and permissions reliably; they are not a requirement to retain every
intermediate development schema forever.

## Unreleased baseline

The September 2026 stewardship baseline replaces 183 unreleased migrations with
eight migrations: one atomic SQL installation and seven model-state migrations
across six Django apps. The extra accounts state step resolves cross-app foreign
keys. Django's own migrations are unchanged.

The SQL files under `src/parishkit/stewardship/schema/` install the final function
definitions, tables, seed sentinels and guards directly. Only function-body
validation is temporarily deferred within the installation transaction because
functions reference tables created later in that transaction. Normal execution
still enforces all constraints, triggers and row policies. The three seed rows
are empty-system sentinels; UUIDs and timestamps are generated on installation.
There are no captured parish records, credentials or deployment identifiers.

The state-only migrations describe the same models to Django's migration
autodetector. Keep SQL and model state in agreement; `makemigrations --check`
and the fresh PostgreSQL tests are both required. The SQL baseline deliberately
has no reverse operation. It is not a downgrade mechanism.

## Existing development databases

This baseline **does not upgrade the discarded development migration history**.
Do not use `--fake`, edit `django_migrations`, or apply it on top of an existing
development schema. No command in this change deletes or resets a database.
Keep the old database and its matching backed-up source if its data matters.
For this baseline, select a new empty development database or a separately
named new Compose volume through the deployment configuration; preserve the
old volume. Data transfer, if desired, is a separate explicitly authorized job.

This pre-production exception is not a promise that future production databases
can be discarded. Once production compatibility is declared, use reviewed
forward migrations and the upgrade/backup procedures in the operations spec.
Do not rewrite this baseline silently after deployments depend on it.

## Equivalence and regression evidence

The reference schema was installed from backed-up commit `5f686e3` into a newly
created empty PostgreSQL 18.6 database. A second fresh installation used the new
baseline. Comparison covered 123 relations, 1,387 columns, 1,974 constraints,
624 indexes, 286 functions, 298 triggers and 28 row policies. All matched after
accounting for 79 PostgreSQL dump/reparse renderings of the same literal
varchar-to-text arrays (69 constraints, eight partial indexes, two policies).
Only those pure literal array casts were normalized; predicates, literal values,
ordering, surrounding SQL and all other definitions had to match exactly.

`test_schema_baseline_postgresql.py` now compares strict catalog fingerprints
against a checked-in, value-free fixture. It does not normalize definitions or
regenerate expectations during tests. Existing current-state SQL, authorization,
concurrency and deletion-protection tests remain in the database suite. Tests
whose sole purpose was an obsolete intermediate upgrade/downgrade were retired.

The inventory includes relation/function owners and full ACLs, live column order,
and sequence parameters. Separate current-model checks compile complete empty
tables from Django declarations in a scratch schema that is always rolled back.
They compare column types, nullability, collations, defaults, generated
expressions, and complete constraint/index multisets in both directions. Index
signatures include operator classes, collations and ordering flags. Negative
probes verify changed definitions and removed defaults, keys, foreign keys,
field indexes and named constraints/indexes. Four explicitly named SQL-only
logging checks and constraint triggers are owned by the independent strict
catalog fingerprint, not inferred from model declarations. No installed table
is altered, and no historical schema is upgraded or downgraded. The test database
setup also verifies all three seed sentinels before fixtures can recreate them.

When intentionally changing schema or the pinned PostgreSQL major version,
inspect the per-object inventory with `schema_inventory.inventory(connection)`
and review the precise differences before updating the fingerprint. An unexplained
fingerprint failure must not be resolved by blindly recapturing the fixture.
