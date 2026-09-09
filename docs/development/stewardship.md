# Stewardship development baseline

For local container commands and runtime limitations, see the
[Compose scaffold guide](stewardship-compose.md).

## Scoped coverage gate

Start the [disposable PostgreSQL server](../guides/stewardship-database-tests.md),
then run the baseline and database suite with the same line/branch gates as CI:

```sh
python -m parishkit.stewardship.quality --postgresql --report /absolute/temporary/path/coverage.json
```

The report's parent must exist, and its path must be new and outside the checkout.
The runner rejects existing files and paths resolving into the checkout before
launching tests. Use a new report filename for each run; even a failed run may
leave an empty or partial report for diagnostics. A prior passing report cannot
stand in for missing new measurement, and existing files are never overwritten.
Raw coverage data is retained in a fresh `stewardship-coverage-*` directory beside
the JSON report. The child process ignores ambient `PYTEST_*`, `COVERAGE_*`, and
`COV_CORE_*` controls and uses that new raw-data destination, so shell settings
cannot silently deselect tests or overwrite an existing coverage database.
The runner explicitly selects the dummy-database baseline profile, then appends
the required PostgreSQL suite with its fixed disposable profile. A failed or
skipped required database run cannot pass using an earlier baseline report.
The runner always measures the complete stewardship package plus the exact
shared modules in `coverage-stewardship.toml`, derives pytest-cov targets from
that manifest, and independently requires at least 80% lines and 80% branches.
It rejects missing/duplicate/non-Python/outside-repository manifest paths,
symlink aliases, missing scoped report files, and reports without branch data.
Unrelated tool coverage and blended percentages cannot mask either failure.
Add every materially extended shared module to the manifest in the same change.

CI also checks migration drift and runs the opt-in Compose suite on a Linux
runner. This is baseline/database coverage and Compose enforcement, not completion of future
browser, transaction, acceptance, load, or release validation.

This is implementation evidence for
[ARC-01](../tasks/stewardship/architecture.md#arc-01-dependency-decisions-and-package-skeleton),
not a replacement for the [architecture specification](../specs/stewardship/architecture/spec.md).

## Supported dependency lines

The initial baseline was resolved on September 7, 2026. Python package ranges
live in `pyproject.toml`; exact transitive development/test versions live in
`requirements/stewardship.txt`. Stewardship remains an optional package extra
for users of other ParishKit tools. The repository development install includes
it so normal CI exercises the new code without provider credentials. The `dev`
extra includes the existing `google` and `stewardship` extras: installing `.[dev]`
also installs Django, the stewardship runtime dependencies, and the Google client
libraries used by credential-free tests. Their version ranges remain defined
once, in the owning extras; the base package still does not require them. No
credentials or live provider access are needed for the tests. Use the locked
checkout installation below for reproducible local/CI validation.

| Component | Selected line / initial pin | Upstream authority |
| --- | --- | --- |
| Python | 3.12 baseline; package minimum 3.12 | [Python lifecycle](https://devguide.python.org/versions/) |
| Django | 5.2 LTS / 5.2.17 | [Maintained releases](https://www.djangoproject.com/download/) |
| PostgreSQL | 18 / 18.6 | [PostgreSQL downloads](https://www.postgresql.org/download/) |
| Celery | 5.6 / 5.6.3 | [Celery changes](https://docs.celeryq.dev/en/stable/changelog.html) |
| Valkey | 9.1 / 9.1.2 | [Valkey releases](https://valkey.io/download/releases/) |
| Caddy | 2.11 / 2.11.4 | [Caddy releases](https://github.com/caddyserver/caddy/releases) |
| Gunicorn | 26 / lockfile patch | [Gunicorn releases](https://gunicorn.org/news/) |
| Psycopg | 3.3 / 3.3.5 | [Psycopg downloads](https://www.psycopg.org/download/) |
| Cryptography | 50 / 50.0.1 | [Cryptography documentation](https://cryptography.io/en/stable/) |
| PyNaCl | 1.6 / 1.6.2 | [PyNaCl changes](https://github.com/pyca/pynacl/blob/main/CHANGELOG.rst) |

The `redis` Python distribution in the lockfile is Celery's protocol client,
not a Redis server. The server choice remains Valkey. Gunicorn runs in Linux
containers; Windows host tests do not install it. Timezone data is explicitly
installed for Windows and minimal-container parity.

Browser support targets the latest two stable Chrome, Edge, Firefox, and Safari
major releases at each release candidate, including iOS Safari and Android
Chrome. ARC-08/OPS-09 record exact tested browser versions and accessibility
evidence; this scaffold does not claim browser certification.

Patch upgrades regenerate the lock and run all baseline checks. Minor/major
changes also require compatibility review of affected specifications, database
migrations, cryptographic formats, and Compose service behavior. Valkey upgrades
must pass the architecture's broker/cache/limiter/outage integration matrix.
Do not use unpinned `latest` images. OPS-01 records concrete image digests when
the container topology lands. A newer Python version needs its own full suite
before being described as a tested runtime.

Regenerate the universal dependency lock with uv 0.12.10:

```sh
uv pip compile pyproject.toml --all-extras --universal --python-version 3.12 --output-file requirements/stewardship.txt
uv pip compile requirements/stewardship-build.in --universal --python-version 3.12 --output-file requirements/stewardship-build.txt
```

## Checkout installation

In a Python 3.12 virtual environment, install from the checkout in this order:

```sh
python -m pip install -r requirements/stewardship-build.txt
python -m pip install --no-build-isolation -r requirements.txt
python -m pip check
```

The first command installs the exact build backend and editable-build helper
versions used by Docker. The second uses those installed tools rather than
creating an isolated build environment that independently resolves the open
`pyproject.toml` build requirements. Runtime/test constraints still come from
`requirements/stewardship.txt`. Repeat both install commands when either lock
changes. CI follows the same sequence; the release workflow also builds with
`python -m build --no-isolation` using the locked `build` frontend from the dev
dependencies. Editing that workflow does not authorize a release.

For an existing uv-managed environment without pip, the equivalent is:

```sh
uv pip install --python .venv/bin/python -r requirements/stewardship-build.txt
uv pip install --python .venv/bin/python --no-build-isolation -r requirements.txt
uv pip check --python .venv/bin/python
```

On Windows, replace `.venv/bin/python` with `.venv/Scripts/python.exe`.
Native Windows can run portable host tests, but authority filesystem durability
tests are explicitly skipped there: those service primitives require POSIX
directory fsync and hard-link semantics. Run the full suite in Linux Compose
or WSL for authority validation; macOS and Linux hosts run it natively. Do not
weaken the production durability guarantees with a no-op fsync on Windows.
These locks pin Python dependency/build-tool versions, not artifact hashes,
the installer itself, or the entire operating-system toolchain. Full artifact
reproducibility remains an OPS-01/OPS-09 responsibility. Editable installation
keeps local source changes immediately visible.

## Scaffold commands and limits

```sh
pk-stewardship --version
pk-stewardship --config scripts/pk-stewardship/example-config.yaml config-check
DJANGO_SETTINGS_MODULE=parishkit.stewardship.settings.development python -m django check
DJANGO_SETTINGS_MODULE=parishkit.stewardship.settings.development python -m django runserver 127.0.0.1:8000
python -m pytest
```

These shell environment-assignment examples are for POSIX hosts; use the same
environment variables through PowerShell or the development Compose
configuration on Windows.

Both stewardship CLI entry points use selective syntax-error redaction. Known
option names and valid choices come from authored public hints; supplied values
and converter exception text are never copied into diagnostics. Unknown tokens
are redacted in full, including option-looking tokens that may themselves contain
private data. Existing safe application-validation messages and help remain
available. Add an explicit public hint when introducing an argument; never pass
input values or raw exception text to the parser's trusted `usage_error` path.
Other ParishKit tools retain their existing shared-parser behavior. This protects
application output, not shell history or operating-system process listings.

The Family and Admin routes intentionally return 503 without collecting data.
Internal liveness returns 200 without dependency access, readiness remains 503,
and metrics returns 404 until its authentication is implemented. Bind the
development server only to loopback; it is not a public deployment. Internal
route ingress isolation is delivered by ARC-03/OPS-01.

No profile uses SQLite or sends mail. Pure/HTTP tests require no database;
database tests use disposable PostgreSQL 18. Production settings intentionally refuse
startup until ARC-02's deployment checks are available. This refusal is not a
substitute for the remaining configuration, cryptographic, and admission work.

The eight Django app labels use the `stewardship_` prefix. Treat these labels as
stable when adding migrations. Foundational account/configuration and audit
models now have PostgreSQL migrations.
The package's [decision index](../../src/parishkit/stewardship/DECISIONS.md)
records the canonical domain vocabulary and persisted compatibility boundaries.

## Logging scaffold

Django uses the shared ParishKit logging setup with a Stewardship JSON formatter
on stderr. Timestamps have explicit UTC offsets. Reviewed event enums and UUID
correlation/task identifiers are the only accepted message context at this stage;
arbitrary messages, exception text, paths, and unknown extras are suppressed.
The five standard severity levels remain available. Default Django request/server
handlers are removed so they cannot bypass redaction with raw access paths.

Each request gets a new internal correlation UUID, ignoring browser-supplied
tracing headers. Nested request/task scopes restore context even on failure.
Future workers must preserve this logging configuration when initializing their
frameworks. ARC-07 owns richer audited schemas; BG packages own task/service
integration, and operational notification delivery remains unimplemented.
