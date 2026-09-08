# Stewardship PostgreSQL tests

These tests implement the [data plan's migration policy](../plans/stewardship/data.md#migration-policy).
They run only against a disposable PostgreSQL server, not parish data or a
production deployment. They use the same pinned PostgreSQL image as Compose;
no provider credentials, existing volumes, or host data mounts are used.

The ordinary `python -m pytest` baseline still uses Django's dummy database
backend and reports the database tests as explicit skips. The separate
`stewardship-postgresql` CI job must pass; a skipped baseline is not PostgreSQL
verification. Do not substitute SQLite for constraint or concurrency evidence.

## Local disposable server

Use an unused container name and loopback port. If the chosen name or port is
already occupied, investigate it; do not delete or stop an unrelated container.
This command creates only temporary test data in container tmpfs:

```sh
docker run --detach --name parishkit-storage-tests \
  --publish 127.0.0.1:55432:5432 \
  --env POSTGRES_USER=stewardship_tests \
  --env POSTGRES_DB=stewardship_tests \
  --env POSTGRES_PASSWORD=disposable-test-only \
  --tmpfs /var/lib/postgresql \
  postgres:18.6-trixie@sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280
docker exec parishkit-storage-tests pg_isready -U stewardship_tests
```

Wait until `pg_isready` reports accepting connections before running tests.
The documented password is synthetic, intentionally public, and used only for
this disposable loopback server. Never reuse it in a deployment.

```sh
python -m pytest tests/stewardship/database \
  --ds=parishkit.stewardship.settings.database_test --require-postgresql-tests -q
python -m django makemigrations --check --dry-run \
  --settings=parishkit.stewardship.settings.database_test
```

For a different host port, set `PARISHKIT_TEST_POSTGRES_PORT` consistently with
the Docker port mapping. The profile fixes the host to loopback and database/
role names to `stewardship_tests`. Pytest creates and drops
`test_stewardship_tests`; do not put retained data in either database. Do not
override database settings to target a real deployment.

After testing, stop and remove only the disposable container you created:

```sh
docker stop parishkit-storage-tests
docker rm parishkit-storage-tests
```

Stopping the container discards its synthetic tmpfs data. There is no retained
data to recover; the next run rebuilds the schema from migrations.

## Foundation boundaries

The initial records establish storage contracts, not usable authentication.
The AuditEvent envelope belongs to the deployment's one parish independently
of any campaign. Soft actor/subject UUIDs preserve attribution after session
cleanup and future campaign purge; they are not authorization references.
Business-owned relationships use explicit protected foreign keys. The audit
table's UPDATE/DELETE trigger complements ORM immutability; runtime roles must
not have TRUNCATE or schema-owner privileges, which OPS-02/OPS-04 implement
before production is enabled. Migration/test owners intentionally can reset a
disposable schema.

`mutate_record` holds a row lock and checks an expected version. Its callback
must check the owning workflow's current authorization and admission inside
that transaction. It is an internal storage primitive, not an authorization
API. Do not make external calls inside callbacks. Session expiry/revocation,
event-specific payload validation, configuration activation, and task claims
retain their existing package owners and are not enabled by these primitives.

New concrete MutableRecord tables must install the frozen `mutable_guard_v1`
migration builder (with any additional immutable binding columns). A PostgreSQL
test checks that every concrete subclass has its enabled row-level guard.
It also compares model-declared immutable/write-once columns with the installed
SQL predicates, so changing only the Python declaration fails verification.
ImmutableRecord tables use `immutable_guard_v1`; a companion all-model test
requires their BEFORE UPDATE OR DELETE row guard. Both SQL guard families raise
an integrity violation, independent of the ORM's StorageInvariantError.
Each table owns its generated function/trigger lifetime, so reversing one model
does not remove another model's protection. Change future guard semantics via a
new version and an explicit migration. Existing-row creation/write timestamps
are not rewritten; new default timestamps come from PostgreSQL's statement clock.
Explicit historical timestamps remain possible for controlled import/migration
code. The optimistic counter, not wall-clock monotonicity, orders mutations.

Every mutable session UPDATE must advance `version` by exactly one; a database
trigger rejects writes that leave the optimistic token unchanged and stamps
`updated_at` from PostgreSQL. Prefer `mutate_record`, which also checks the
caller's expected version and returns the refreshed write time. Initial record
correlation defaults reuse the current request/task scope; unscoped creation
starts a new correlation. Datetime fields accept aware datetime objects and
explicit-offset ISO strings, but never infer missing timezones.

The mutation helper binds its supplied correlation while running the callback,
so dependent audit records use the same operation ID even without an outer
request scope. It restores any outer scope on success and failure. Forbidden
history/identity mutations raise StorageInvariantError, not user-field
ValidationError. PortalSession's principal, session key, and authentication
instant are immutable bindings; a new login creates a new attribution record.
Once revoked_at is recorded, neither the helper nor a version-advancing SQL
update may clear or change it. A later authorization grant needs a new login,
not restoration of an old session's validity.

Do not schedule Django's standalone `clearsessions` against protected
PortalSession rows: one protected expired session aborts that entire sweep.
ARC-04 must install an ordered metadata/session cleanup service before enabling
authentication. A database regression documents this prerequisite; it is not
an implementation of the identity cleanup policy.
