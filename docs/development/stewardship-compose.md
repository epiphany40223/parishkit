# Stewardship Compose scaffold

[Developer overview](stewardship.md) ·
[Operations tasks](../tasks/stewardship/operations.md#ops-01-development-and-production-compose-topology)

This is the Phase 0 scaffold, not a usable campaign system or an approved
production deployment. HTTP serves intentional unavailable pages and liveness.
Readiness remains `503`. PostgreSQL and Valkey run independently; application
database/broker integration follows in Phase 1.

## Local development

Install current maintained Docker Engine/Desktop with BuildKit enabled, the
Buildx plugin, current Docker Compose, Python 3.12+, and the
[development environment](stewardship.md). Run from the repository root. The same
Linux image runs on Linux hosts and Docker Desktop for macOS/Windows. Docker
must be allowed to share the checkout and runtime directory.

BuildKit is the supported build path; do not set `DOCKER_BUILDKIT=0` or rely on
the [deprecated legacy builder](https://docs.docker.com/engine/deprecated/#legacy-builder-for-linux-images).
The checked baseline uses Docker 29.7.2, Buildx 0.36.0-desktop.1, and Compose
5.3.1. Compatibility with older Docker/Compose versions is not a project
requirement; no legacy JSON-output adapters are provided. Update tooling to
the validated baseline when older tooling fails these checks. The root ignore
policy below is defense in depth, not a promise of legacy-builder support.
The repository-root `.dockerignore`
provides a default-deny fallback when Dockerfile-specific exclusions are not
recognized. Its contents must stay synchronized with
`deploy/stewardship/Dockerfile.dockerignore`; normal tests enforce that equality.
Dockerfile-specific exclusions take
[precedence over root exclusions](https://docs.docker.com/build/concepts/context/#filename-and-location).
The root policy also applies to future builds from this checkout unless they
explicitly override it. Keep credentials out of allowlisted source/build inputs;
filename exclusions cannot recognize secrets embedded in those files.

Choose a new runtime directory outside the checkout whose parent already exists.
On Linux/macOS:

```sh
export PARISHKIT_ROOT=/absolute/path/to/new-stewardship-runtime
pk-stewardship prepare-development --runtime-root "$PARISHKIT_ROOT"
docker compose -f deploy/stewardship/compose.yaml -f deploy/stewardship/compose.development.yaml build web
docker compose -f deploy/stewardship/compose.yaml -f deploy/stewardship/compose.development.yaml up --wait web postgres valkey
```

In PowerShell, substitute these first two commands, followed by the same Compose
commands:

```powershell
$env:PARISHKIT_ROOT = 'C:/absolute/path/to/new-stewardship-runtime'
pk-stewardship prepare-development --runtime-root $env:PARISHKIT_ROOT
```

Provisioning refuses an existing target and generates a synthetic PostgreSQL
password file. With POSIX modes, directories are owner-only except the PostgreSQL
mount root, which allows traversal (0711), not listing or writing, by the
container's database UID. PostgreSQL 18 makes its nested database directory
owner-only; its vendor entrypoint does not change the mount root's ownership.
The enclosing host runtime tree remains owner-only (0700).
It creates no parish configuration, application keys, or provider credentials.
Protect the runtime directory with equivalent Windows ACLs. After partial
failure, inspect the newly created tree and choose another new target; the
command never deletes or replaces files.

Open `http://localhost:8000/`. `/admin/` and `/family/` return `503` until their
workflows exist. `/health/live` returns `200`; `/health/ready` returns `503`;
`/metrics` returns `404`. No login, provider calls, or application database writes
occur. Production ingress will deny these internal health/metrics URLs.

Web mounts only `src/` read-only at `/app/src`. Python changes trigger Django's
development reload; packaged template/static changes are visible to subsequent
reads. There is no whole-checkout mount. Dependencies/image instructions require
a rebuild. Never place credentials in package source files.

Set `STEWARDSHIP_HTTP_PORT` to change the localhost port (default `8000`). No
database or broker port is published. Application containers use non-root
numeric identities, an init process for signals, a read-only filesystem,
temporary `/tmp`, dropped capabilities, and no-new-privileges.
The development web command explicitly opts into `--bind-all-interfaces` inside
the container; the host port remains localhost-only. Direct host launches
without that flag listen only on `127.0.0.1:8000`. See the
[service command warning](../../scripts/pk-stewardship/README.md) before using
the opt-in outside Compose.

Stop without deleting durable data:

```sh
docker compose -f deploy/stewardship/compose.yaml -f deploy/stewardship/compose.development.yaml down
```

Restart with the same `PARISHKIT_ROOT`. Database/broker state survives container
replacement. Never use `run/persistent` for generic cleanup.

## Configuration and unfinished boundaries

All base stores default under `PARISHKIT_ROOT`, falling back to `/opt/parishkit`.
Paths must exist; Compose does not silently create missing bind-mount targets.
The scaffold offers explicit Compose inputs:

| Variable | Meaning |
| --- | --- |
| `STEWARDSHIP_POSTGRES_PATH` | PostgreSQL durable directory |
| `STEWARDSHIP_VALKEY_PATH` | Valkey durable directory |
| `STEWARDSHIP_CADDY_PATH` | Parent of Caddy `data/` and `config/` |
| `STEWARDSHIP_POSTGRES_PASSWORD_FILE` | Local PostgreSQL password file |
| `STEWARDSHIP_HTTP_PORT` | Localhost HTTP port |

These inputs are not a second parish configuration authority. OPS-02 will
connect the [typed CLI/YAML path model](stewardship-deployment.md) to Compose
rendering/provisioning, including media/config/export overrides, optional named
volumes, and service ownership validation. The development helper currently
provisions only the standard layout.

The topology reserves general worker, scheduler, configuration installer, one
credential-installer identity per secret target, isolated mail/backup workers,
token rotation, bootstrap, and migration. Only development web is implemented.
Pending identities have no data, authority, or secret mounts and exit `2` even
when explicitly invoked; they never claim jobs or report healthy. Ordinary
startup excludes `pending`, `bootstrap`, `migration`, and `token-key-rotation`
profiles. Valkey keeps protected mode enabled, with no application clients
until authenticated role-specific queue access exists.

ARC-06/OPS-02 will attach narrow mounts and queues alongside their enforcement.
OPS-04 owns bootstrap/online exclusion, migrations, and production validation.
Do not enable these profiles to bypass unfinished prerequisites.

## Production structure, not deployment instructions

The production overlay has no build context or checkout mount. Every app role
uses one required GHCR digest, assembled from `STEWARDSHIP_GHCR_REPOSITORY`
(`owner/repository`) and `STEWARDSHIP_IMAGE_SHA256` (64 hexadecimal characters).
The image name follows the [release specification](../specs/stewardship/operations/spec.md):
`ghcr.io/<owner>/<repository>/parishkit@sha256:<hex>`. For repository
`example/parishkit`, the `/parishkit/parishkit` suffix is intentional: the first
component is the GitHub repository and the second is its application image.
Docker rejects malformed digests. The overlay also requires
`STEWARDSHIP_HOSTNAME`, an operator-installed `STEWARDSHIP_CADDY_CONFIG_FILE`, and
`STEWARDSHIP_PRODUCTION_POSTGRES_PASSWORD_FILE`. Production cannot fall back to
the generated development password. Combine only one overlay with the base;
never combine development and production overlays.

Only Caddy publishes production ports (`80`/`443`). It is disabled behind the
`pending-ingress` profile until ingress/startup validation lands. Its container
hardening is also unfinished; the explicit implementation and validation
prerequisites are in
[OPS-03 items 1 and 5](../plans/stewardship/operations.md#ops-03-production-ingress-tls-and-network-security).
Do not enable ingress merely because the template-validation test passes.
The checked-in
[Caddyfile](../../deploy/stewardship/Caddyfile) is a reference template, not an
automatically installed configuration. It denies internal paths before proxying,
with access logging off pending tested redaction. Operators must copy that
template to their managed configuration location and set
`STEWARDSHIP_CADDY_CONFIG_FILE` to that file; Compose mounts only the supplied
file and does not enforce the template's contents. Installation and ingress
validation remain pending work, not a reason to enable this profile now.
Production services still refuse startup. TLS, static serving, and release
artifacts are not yet operational or approved.

Python, PostgreSQL, Valkey, and Caddy use multi-architecture image digests.
PostgreSQL 18 persists at `/var/lib/postgresql`, following its
[version-specific volume layout](https://hub.docker.com/_/postgres).
Build tools are pinned in `requirements/stewardship-build.txt`; app and test
dependencies use `requirements/stewardship.txt`. Phase 0 includes test tools
in the one image for parity. OPS-09 owns release scanning, provenance, SBOM,
and publication; no image has been published.

## Validation

Normal `python -m pytest` tests topology contracts without Docker or providers.
Run the same baseline in the built image:

```sh
docker compose -f deploy/stewardship/compose.yaml -f deploy/stewardship/compose.development.yaml run --rm --no-deps tests
```

The test-only profile mounts test/wrapper/tool fixtures read-only, not credentials
or the Docker socket. It does not inherit host Compose-smoke flags, preventing
recursive orchestration. Every test bind explicitly disables host-path creation;
a missing source fails mounting instead of leaving an empty directory in the
checkout. Structural and all-profile rendering tests enforce this invariant.

The test service also mounts read-only checkout reference copies of `README.md`,
`pyproject.toml`, `requirements/stewardship.txt`,
`requirements/stewardship-build.txt`, `.dockerignore`,
`deploy/stewardship/Dockerfile`, and its `Dockerfile.dockerignore` under
`/app/checkout-build-inputs`.
`PARISHKIT_TEST_CHECKOUT_ROOT` enables a freshness test comparing those copies
byte-for-byte with immutable copies under `/app/baked-build-inputs`, which no
test mount overlays. Missing, unreadable, or different inputs
fail with a request to rebuild the development image; compared contents are not
printed. The original image metadata and installed dependencies stay intact.
Rebuild after changing any of these seven inputs, including documentation-only
README edits, before running the in-image baseline. Host-only runs have no image
to compare; synthetic tests exercise both matching and stale/missing inputs.

Fixture files in mixed-use `scripts/` and `tools/` directories are enumerated
individually to exclude local operational artifacts. Dedicated specification,
plan, and deployment fixture trees are mounted as directories; never store
credentials or operational output in those trees.

The opt-in lifecycle check below compares the exact host and in-image collected
test IDs, including parameterized cases and duplicate occurrences. It fails with
missing/unexpected IDs if a new fixture was omitted from the narrow mounts, even
if the two environments collect the same number of tests. Collection order does
not matter. Host collection always covers the full baseline independently of
the outer pytest selection; the image manifest comes from its actual baseline
run. Collection errors, missing manifests, and empty collections fail closed.

Opt-in host checks render both overlays and exercise a disposable development
project (build the image first):

```sh
PARISHKIT_RUN_COMPOSE_TESTS=1 PARISHKIT_RUN_COMPOSE_SMOKE=1 python -m pytest tests/stewardship/test_compose.py -q -s
```

In PowerShell, set both variables through `$env:` before invoking pytest.
The `PARISHKIT_RUN_COMPOSE_TESTS` checks also validate the committed Caddy
template using the production overlay's pinned image and a synthetic hostname.
They run `caddy adapt --validate` and assert that all three internal-path 404
rules precede the catch-all proxy. The validation container has no network or
published ports, receives the template through stdin, and uses temporary Caddy
storage. This does not enable ingress or replace OPS-03's live boundary tests.
Image-freshness checks run the test service with temporary README, Dockerfile,
and both ignore-policy references, first matching and then deliberately stale,
and verify the rebuild diagnostic.
It never changes the checkout or starts application/provider services.
PostgreSQL traversal checks use Linux tmpfs with a different owner's UID to
exercise the pinned entrypoint's privilege drop, independent of Docker Desktop's
host bind-mount permission translation. They verify both the provisioned mode
and a deliberately inaccessible parent; no database or host mount is used.
Two additional build checks exercise the root and Dockerfile-specific ignore
files independently against synthetic allowed/private fixtures. They export a
scratch filesystem under pytest's temporary directory, without using real
credentials, pulling a base image, or publishing/loading an image.
The test uses a new UUID-named project and copied source tree, runs the in-image
baseline, checks routes/log suppression, verifies reload without rebuilding,
recreates PostgreSQL/Valkey with synthetic records, verifies persistence,
rejects production web startup, and restarts web. It tears down only its own
containers/networks; synthetic host data remains under pytest's temporary
directory. It never prunes volumes, deletes host data, starts production Caddy,
requests a certificate, or contacts a provider.
