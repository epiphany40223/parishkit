# Stewardship operations and quality

This specification defines developer/production Compose environments,
credentials and durable volumes, release images, backup/restore, monitoring,
and validation. Shared ParishKit repository/release rules remain in the
[top-level specification](../../intro/spec.md).

## Compose files and images

The repository provides a base Compose definition plus a development overlay.
Running the documented development command on Linux, macOS, or Windows starts
a usable HTTP environment without TLS. Source, templates, and static inputs
are bind-mounted from the checkout so ordinary changes reload without
rebuilding the application image.

Production Compose is not checked in: the runtime provisioning command renders
it from the typed deployment configuration into the runtime root, and the
image-retargeting command is the only way its image changes afterward (see
[startup and upgrades](#startup-and-upgrades)). It references one immutable
GHCR image digest, never a host
checkout. It includes web, general worker, dedicated mail-dispatch worker,
scheduler, dedicated backup worker, configuration installer, target-specific
credential installers, the explicit token-key-rotation profile, PostgreSQL,
Valkey, and Caddy services from the
[architecture](../architecture/spec.md#technology-and-component-model). Only
Caddy publishes host ports. PostgreSQL/Valkey are on an internal network;
workers/scheduler have no inbound public ports. Development Compose preserves
the same key-mount and queue separation.

One application image contains package code/static build and supplies web,
worker, scheduler, migration, bootstrap, backup, and restore entry commands.
It runs as a non-root UID, has a read-only root filesystem where practical,
uses `/tmp`/declared volumes for writes, includes health checks, and handles
termination signals. Third-party database/proxy/broker images are official,
pinned major/minor lines, and updated to supported security patch releases.
Every rendered service that runs the application image, one-shot profiles
included, sets `oom_score_adj: 500`, while PostgreSQL, Valkey and Caddy keep
the default, so under host memory exhaustion the kernel kills an application
process (an online service restarts; a one-shot run fails and is re-run)
before the database (#392). No service has a hard memory or CPU limit until
peak use per service is measured: a limit set too low would kill web or a
large export mid-campaign.

Release-tag workflow builds the application image from the tagged commit
after that commit's full validation, pushes
`ghcr.io/<repository>/stewardship:<version>` plus the immutable commit tag, and
records the pushed digest in the GitHub Release, since the deployment names
the image by digest. Before the digest is recorded, the workflow pulls the
pushed digest back and runs it offline (its version, `collect-static` and the
bundled `pg_dump`), then attests its build provenance; a failure of either
publishes no release (#392). The release helper and the upgrade runbook
verify that attestation with `gh attestation verify` before the digest is
deployed. It preserves existing Python sdist/wheel and GitHub
Release behavior. A human still explicitly authorizes release-tag push. For
v1 the image is `linux/amd64` only; the `linux/arm64` image, SBOM and
vulnerability scanning are
[deferred](../../../plans/stewardship/v1-launch.md#cut-from-v1) past the
launch. The
[release image guide](../../../guides/stewardship-release-image.md#the-pushed-digest-is-smoke-tested-and-attested)
holds the details.

## Runtime storage

`PARISHKIT_ROOT` or `/opt/parishkit` remains the default path root:

| Path | Stewardship use |
| --- | --- |
| `config/` | Deployment YAML, versioned Stewardship authority, active manifest, and non-secret Compose/operator files |
| `credentials/` | Google, ParishSoft, Slack, application, backup secrets |
| `cache/` | Tenant-scoped short-lived ParishSoft/cache artifacts |
| `logs/` | Optional JSONL/container log exports |
| `reports/` | Authorized temporary generated exports |
| `run/` | Locks, bootstrap markers, health/runtime state, isolated persistent service storage |
| `backups/` | Sealed [backup](#backup) sets, one directory per run, kept under the backup runbook's [retention](../../../guides/stewardship-backup-runbook.md#retention) |

PostgreSQL data, Valkey state, Caddy ACME state, and uploaded media default to
durable host bind mounts at `<root>/run/persistent/postgresql`,
`<root>/run/persistent/valkey`, `<root>/run/persistent/caddy`, and
`<root>/run/persistent/media`. The root is resolved before Compose renders, so
changing `PARISHKIT_ROOT` relocates every default store. Provision these paths
for their respective service UIDs; generic run/temporary cleanup must never
traverse `run/persistent`. Each store remains independently overridable by
deployment CLI/YAML, including explicit opt-in Docker-managed named volumes.
Container replacement/restart/upgrade must not remove durable storage.

The export root and its subdirectories use owner-only mode `0700`; generated
exports and their temporary files use owner-only mode `0600` from creation,
including before atomic rename. Export generation, authorized download, and
cleanup services use the owning application UID for these mounts. Provisioning
and startup validate ownership/modes on configured export paths. The proxy has
no export-storage mount. These rules also apply to overridden paths and
development storage; no intermediate export file may use permissive defaults.

Credential files/directories use the shared restrictive modes. Compose mounts
each secret read-only only into services that need it; mounting the whole
credentials directory into an application service is prohibited. In
particular, token public keys are available to `web`/general workers, while
token private keys are mounted only into `mail-dispatch` or the explicit
`token-key-rotation` profile, except for initial provisioning by the
[offline bootstrap profile](#offline-bootstrap-profile), and Google mail-provider credentials only into
`mail-dispatch`. The scheduler, web, general
worker, report/export jobs, and ordinary maintenance commands cannot read the
private token-key path. Images, Compose files, logs, exceptions, backup
metadata, and support bundles never contain credential values.

Among online services, only `config-installer` mounts
`<root>/config/stewardship` read-write; the sole initial-provisioning exception
is the [offline bootstrap profile](#offline-bootstrap-profile). Other
online services either mount its active manifest/versions read-only for startup
verification or consume the matching PostgreSQL materialization. Each
`credential-installer-*` instance mounts a separate target subdirectory read-
write plus only its own handoff private key and queue. Consumers mount the
resulting individual credential file read-only, with one exception: `worker` and
`mail-dispatch` mount the ParishSoft, Google Workspace and Slack target
subdirectories read-only, so a running consumer sees a replacement the
installer renames into place. They mount them in every rendered topology,
`compose-initial.json` included, from before setup's first key exists, so
setup needs no recreation either
([setup credential pickup](#setup-credential-pickup)). Each service still
mounts only the targets it consumes: `worker` the ParishSoft and Slack
directories, `mail-dispatch` the Google Workspace one. That subdirectory
holds only the target's working credential and its sealed replacement
journal, which the consumer cannot
decrypt. See
[replacing an integration key](../../../guides/stewardship-credential-installers.md#replacing-an-integration-key-from-the-web).
Neither installer class receives
the whole credentials directory, broad host paths, Docker socket, campaign
answers, or unrelated secrets. Compose and runtime tests inspect these mounts
and service identities.

The application backup container receives only its target credential and active
data-backup encryption key as individual read-only mounts. It receives no OAuth,
ParishSoft, mail, Slack, Django, general-encryption, or token-key secret. The
backup manifest records credential/key fingerprints and key IDs only. All
credential files, including every retained data-backup decryption key version,
and collision-only MAC keys required by RehearsalCodeReservation records,
are protected by the separately authorized operator secret-escrow process below;
copying them into an application database, report, or ordinary backup task is
prohibited.

Scheduled, operator-requested, and purge-triggered application backups are
claimed only by `backup-worker` through its dedicated queue. The web and general
worker may create an authorized durable backup TaskRun but never receive target
credentials or the data-backup key. The backup worker cannot claim source,
mail, publication, export, purge, or configuration-installer tasks.

`pk-stewardship backup-secrets` runs only in an explicit operator profile with
the credential files mounted read-only and no application database access. It
creates a versioned manifest and encrypted secret bundle at a distinct off-host
escrow target. The bundle is encrypted to one or more operator-controlled
recovery public keys or an equivalent external recovery service; the
corresponding private recovery material is never stored on the application VM
and the bundle is never encrypted solely by a key contained within itself.
Restore drills verify that an authorized operator can combine a data-backup
manifest with the matching secret bundle without exposing secret values in
logs. Normal application services cannot invoke this profile.

### Secret escrow recovery verification

Before approving an exceptional purge, an authorized operator uses an isolated
off-host recovery environment to retrieve and verify the selected data backup
and matching escrow bundle. Using independently held recovery private material,
the operator verifies that the bundle decrypts, contains every credential/key
version required by the backup manifest, matches their fingerprints, and
provides the key that successfully decrypts and verifies the selected data
backup. Merely possessing a recovery public key or uploading an encrypted
bundle is insufficient. Verification must not connect to production providers
or write to ParishSoft; this check is not a production restore or release.

The operator-only verification tooling produces a strict, non-secret summary
of backup/bundle references and manifest digests, required credential/key and
recovery-key fingerprints, completion time, and pass/fail. It never prints key
values, decrypted records, or credentials; temporary plaintext follows the
isolated recovery environment's restrictive storage and cleanup procedure.
The Admin records successful evidence through the
[purge workflow](../admin-portal/spec.md#campaign-purge).
Normal web/worker/backup services receive neither recovery private material nor
permission to invoke the escrow profile. The application checks manifest
binding and freshness, while trusting the named operator's attestation of the
off-host check. The runbook requires reporting lost escrow/recovery access so
pending evidence can be invalidated before deletion.

## Production ingress and TLS

Caddy terminates TLS, redirects HTTP to HTTPS, obtains/renews public
certificates automatically through ACME/Let's Encrypt, serves static assets,
and proxies dynamic traffic. Production requires a DNS hostname pointing to the
VM and inbound ports 80/443. Caddy's data/config volumes persist account and
certificate state across upgrades.

Static assets keep fixed names (for example `ui-v1.js`), so Caddy sends them
with `Cache-Control: no-cache`: browsers may keep a copy but revalidate it
against the file server's `ETag` on every use, and a mid-campaign fix to a
script or stylesheet reaches returning Families on their next page load.

Caddy has explicit highest-priority matchers that return the ordinary public
not-found response for `/health/live`, `/health/ready`, and `/metrics` before
the catch-all application reverse proxy. Container health checks and authorized
metrics clients call the application service directly over the internal Compose
network.

When no web replica answers, for example while an upgrade has web stopped,
Caddy answers every request with its own self-contained maintenance page ("We're
updating the site"): HTTP 503 with `Retry-After: 120` and `Cache-Control:
no-store`, inline styles only (drawn in the
[shared design tokens](../ui-conventions/spec.md#shared-visual-style)), no
scripts or application assets, and a strict Content-Security-Policy. Only
the proxy's own upstream failures (502 or 503) select it: a refused dial or
no available upstream, as during an upgrade, but
also an upstream connection reset mid-request (a worker killed or out of
memory), whose 502 is shown as this 503 page and recorded as 503 in the access
log. An application response, including an application 500, passes through
unchanged, and an upstream timeout (504) is not treated as an upgrade. (A later
refinement could match only the connection-refused error.) A failed upgrade
leaves the page up until the upgrade is re-run or rolled back. Caddy holds no startup interlock, so it keeps running through offline
upgrade work; web still holds the interlock, so nothing behind Caddy runs
during that work.

The application trusts forwarded scheme/client information only from the
single configured proxy hop. Caddy access logs redact `/access/<token>` path
segments and do not log cookies/query secrets or request bodies. Exact Family-
code searches are POST-body-only and therefore never enter access-log URLs.
Application logs hold the same line even with debug logging on: the shared log
formatter replaces the `/access/` segment (also when %-encoded) and every
query-string value with `[redacted]`, keeping the route and parameter names, as
in `/access/[redacted]`. Within a request it also scrubs that request's link
token and its `code`, `state` and `token` query values wherever they appear in
free text, including exception text.
Upload/body/time limits protect the app without blocking configured logo/export
workflows. Two routes have a larger body limit: hosted-file uploads (11 MB),
defined with the buffering of `/files/` responses by
[hosted files](../hosted-files/spec.md#operations), and the past-campaign
archive Review (21 MiB, one file per request), defined by
[campaign history](../campaign-history/spec.md#validation). Both refuse an
oversize `Content-Length` before reading the body; the 30-second body-read
timeout is shared by every route. The official stock
Caddy image is used without third-party rate-limit modules; coarse and specific
administration-login limits are application middleware defined by the
[identity security policy](../architecture/spec.md#identity-and-session-security).

Local development binds an unprivileged HTTP port and uses localhost Google
OAuth redirect registration. TLS remains optional locally.

## Database connection sources

**Status: shipped in stewardship-v1.25.0 (#2111).** Before 1.25.0,
PostgreSQL ran with the stock image's `pg_hba.conf`, which accepts any login
with its password from any address, so a container that could read another
login's password could connect as that login. The backup worker reads
every login's password, the cluster superuser's included, because it
archives the credentials tree (see the
[backup guide](../../../guides/stewardship-backup.md#the-whole-trees-read-only-by-one-identity)).
Since 1.25.0, PostgreSQL accepts each login only from the container
network identity of the service that uses it, so a stolen password is
useless from anywhere else. The per-login passwords, `scram-sha-256`, the
grants and every login's [grant admission](#installer-idle-polling) stay as
they are; the source rules are a second, independent check in front of them.
The [dedicated off-site uploader](#dedicated-off-site-uploader-planned-post-launch)
builds on these rules and gets its own source when it lands.

### Logins and their sources

The logins are the closed inventory in `runtime_identities.py` plus the
operator superuser; nothing else may connect over TCP. The
[inspection login](#database-inspection-login) is outside that inventory:
it has no password and connects only through the socket.

| Login | Used by | Allowed source |
| --- | --- | --- |
| `pk_stewardship_web`, `pk_stewardship_download` | `web` (it holds both passwords by design) | `web`'s backend address |
| `pk_stewardship_worker` | `worker` (both processes) | `worker`'s backend address |
| `pk_stewardship_mail_dispatch` | `mail-dispatch` | its backend address |
| `pk_stewardship_scheduler` | `scheduler` | its backend address |
| `pk_stewardship_config_installer` | `config-installer` | its backend address |
| `pk_stewardship_credential_<target>` (one per target) | `credential-installer-<target>` | that installer's backend address |
| `pk_stewardship_bootstrap` | the `bootstrap` profile | the `bootstrap` network |
| `pk_stewardship_migration` | the `migration` profile (also LOCAL's seeder check step) | the `migration` network |
| `pk_stewardship_admin_recovery` | the `admin-recovery` profile | the `admin-recovery` network |
| `pk_stewardship_backup_worker` | the `backup-worker` profile | the `backup-worker` network |
| `pk_stewardship_operator` (superuser) | the `database-provision` profile (`database-roles`, `database-grants`) | the `database-provision` network; locally, the socket inside the PostgreSQL container |

**Superuser scope, narrowed from #2111.** The issue lists migration,
`database-roles`, backup and restore as superuser users. Only
`database-provision` keeps a TCP source for the operator: migration and the
backup worker connect as their own logins (rows above), and `pk-restore`,
the upgrade check and the runbook's commands reach the operator through the
socket by `docker exec`. No other profile may use the operator over TCP.

**`token-key-rotation`.** It is a `ServiceRole` and an architecture-spec
profile, but `render_runtime` does not render it and it has no SQL login in
`database_identities()`, so it gets no rule today. When it is rendered it
gets its own login (an inventory and grant change in its own release) and
its own one-shot network like the rows above; the renderer refuses an
inventory login without a source, so it cannot ship without one.

Every rule names the deployment's one database (`postgres.name`), never
`all`, so no login reaches the `postgres` or template databases over TCP.

**Source identity.** Docker gives no per-container authentication, so the
source address is the identity, and the renderer makes it fixed:

- **Long-running services** keep the one `backend` network and get a fixed
  `ipv4_address` on it, as web and Caddy already have on `proxy`. The
  addresses are fixed offsets from the backend subnet's network address,
  computed by `RuntimeNetwork` (like `web(replica)`) and valid for every
  allowed prefix (/24 to /26): PostgreSQL, Valkey, web (one offset per
  replica), the configuration installer, worker, mail-dispatch, scheduler,
  then the credential installers in sorted target order, then LOCAL's
  `mailpit`, `fake-parishsoft` and seeder. The offsets start at 2, skipping
  the network address and the gateway at offset 1, and stay in the lower
  half of the subnet; the renderer refuses a `backend` member without one
  and an offset outside that half.
- **Dynamic addresses on `backend`.** Compose services all have fixed
  addresses, but any `docker run --network <project>_backend` without
  `--ip` still gets a dynamic one. While a fixed-address service is stopped,
  such a container could take its address (the service then cannot start)
  and with it that login's source. So the `backend` network's IPAM sets an
  `ip_range` of the upper half of the subnet: Docker allocates dynamic
  addresses only there, which no rule allows. Starting such a container
  needs host root (see [what source rules cannot stop](#what-source-rules-cannot-stop)).
  pk-local's seeder is the one such container today; it passes its own
  fixed address with `--ip`, and its web-identity steps never run two at
  once because every seed holds the seed lock and runs its steps in turn
  ([seeder phases](../local-environment/spec.md#seeder-phases-and-identities)).
- **One-shot profiles** (`bootstrap`, `migration`, `admin-recovery`,
  `database-provision`, `backup-worker`) leave `backend`. Each gets its own
  internal network with a small fixed subnet, joined only by that profile
  and PostgreSQL. A one-shot is started with `docker compose run`,
  sometimes twice at once (a cron backup during an upgrade's backup), so
  one fixed address would make the second container fail to start; a range
  does not. Each network is a /28 whose IPAM sets the gateway at offset 1,
  PostgreSQL's fixed address at offset 2, and an `ip_range` of the upper
  /29 (offsets 8 to 15) for the profile's containers. The rule allows the
  profile's login from that /29 only, so the gateway, which an internal
  bridge network still places on the host's bridge interface, is never an
  allowed source. The implementation verifies the gateway placement and
  the `ip_range` allocation on the Docker Engine version Production runs.
  The subnets are /28 slices, in a fixed order, of one new `runtime_network`
  block (`offline`, default `172.29.242.0/24`, a /24 or /25 so it holds
  every profile's slice, private and disjoint from `backend` and `proxy`
  like them). None of these profiles uses Valkey or
  another backend service; the backup worker keeps
  `application-egress`. A side effect is that the backup worker, which
  reads the Valkey ACL, can no longer reach Valkey.
- **No spoofing.** A container can claim another address only by changing
  its interface (`NET_ADMIN`) or crafting packets (`NET_RAW`). Every
  container on `backend` or a one-shot network already drops all
  capabilities; the renderer test makes that, and no `network_mode: host`,
  an invariant.

**The rules.** One rule per login and source, `host` with
`scram-sha-256` (the backend is an internal network without TLS; adding
`hostssl` is a separate decision), then a final `host all all all reject`. The
socket gets `local all pk_stewardship_operator trust`, then
`local "<postgres.name>" pk_stewardship_inspect trust` for the
[inspection login](#database-inspection-login) (its one database only),
then `local all all reject`: the socket lives on a mode-0700 tmpfs inside the
PostgreSQL container, so only `docker exec` (host root) reaches it, which is
how the upgrade check, `pk-restore`, the operations scripts and the
runbook's `ALTER ROLE` commands already connect. The socket rule names `all`
databases, not the deployment's, because first-time setup needs the
`postgres` database: the stock entrypoint passes the command's `-c` options,
`hba_file` included, to its temporary init server, then connects as the
operator over the socket to the `postgres` database to check for and create
`POSTGRES_DB`. That happens on every empty store: a fresh install, a
`pk-restore` onto an empty store, a pk-local reset and CI's operational
Compose job. Only the operator, and only through the socket, gets that. The stock file's in-container
`127.0.0.1` trust lines are dropped; nothing uses them. The rules carry no
`trust` over TCP and no `md5`.

**Generation and installation.** The renderer builds the file from the same
topology that renders Compose (`runtime_topology.py`), so a service, login or
address cannot change in one and not the other. Provisioning and
`retarget-image` write it beside the rendered Compose documents, owner-only
and readable by the PostgreSQL UID, in its own directory. PostgreSQL mounts
that directory read-only and starts with `-c hba_file=<mounted path>`. The
directory, not the file, is mounted so a renamed-in replacement is seen
without recreating PostgreSQL, the same reason rotating credentials use
directory mounts. The stock `pg_hba.conf` inside the data directory is never
edited, which is what keeps rollback simple.

**When PostgreSQL reads the rules.** PostgreSQL reads `hba_file` only when
it starts and when it reloads its configuration (SIGHUP); a new file on
disk changes nothing until then, and a reload that finds an invalid file
logs the error and keeps the previous rules. So the scripted upgrade, after
step 3's `retarget-image` writes the file, requires `pg_hba_file_rules` to
report no error row and then runs `SELECT pg_reload_conf()` as the operator
over the socket by `docker exec`. When the retarget also changed
PostgreSQL's command (the first rollout), the recreated container reads
the new file when it starts instead. The LOCAL fake-clock override
swaps only PostgreSQL's image and keeps the mount and the command.

`pg_reload_conf()` only signals the postmaster and returns before the
reload happens, so the upgrade then waits, bounded, for the reload to take
effect: it reads PostgreSQL's own `clock_timestamp()` just before it sends
the reload, after the retarget wrote the file, and polls about once a second
until `pg_conf_load_time()` is later than that moment, for at most 30
seconds. Both times come from PostgreSQL's clock, with microsecond
precision, so LOCAL's fake clock shifts them alike; the file's
`pg_stat_file` modification time would come from the writer's clock and has
whole-second precision, so it is not used. A timeout stops the run (see the hba check below) and, per the
timeout-logging rule, writes one persistent line to the upgrade log with
what timed out (the wait for the hba reload), the limit (30 seconds) and the
elapsed seconds; a wait that succeeds logs its elapsed seconds too. Both
conditions are needed: `pg_conf_load_time()` advances even when the hba part
of a reload fails and the old rules stay, so only the pairing with "no error
row" proves the rules in force were loaded from this file. Before it reloads,
the upgrade runs the same check against no moment at all, so a file that is
not the rendering or does not parse stops the run without a reload.

### Checks, rollout and rollback

**Drift.** `pg_hba_file_rules` and a digest describe the file on disk, not
the rules in memory, and the upgrade check (step 1) runs against the
PostgreSQL that is still running the previous release, before the retarget.
So the hba conditions must not be part of that check's `t`/`f` answer: on the
first rollout the old PostgreSQL has no `hba_file`, and on a later release
that changes the rendered file the old mounted file differs from the new
rendering, so either would refuse a correct run at step 1, and an `f` at
step 4 runs migrations and grants, which cannot fix hba drift. The upgrade
check keeps only what can hold before the reload (the database, extension
and identity checks).

The hba conditions are their own **hba check**, a read-only query that
the target image renders (`pk-stewardship hba-check`, which, like
`upgrade-check`, opens no database and re-renders the file from the
provisioning record as `retarget-image` does) and that answers `t` or the
name of the first failed condition. `stewardship-upgrade-host.sh` runs it
once before the reload, when only the load may still be pending, and again
after step 3's reload (or, when the first
rollout or the full stop recreated PostgreSQL, after that restart) and
before step 4, as the operator over the socket. It requires that
`SHOW hba_file` is the mounted path, that the mounted file's digest matches
the rendered one, that `pg_hba_file_rules` reports no error row (so the
last load did not keep older rules), and that `pg_conf_load_time()` is later
than the moment the upgrade read just before its reload (after the bounded
wait above), so the rules in force were loaded from this file. A failure stops the
run before step 4 with the maintenance page still up, names the failed
condition, and leaves the fixed-forward or rollback decision to the operator.
This is still indirect; only `database-sources` below proves which rules are
in force. Online services need no new
check: a refused source fails their database wait at once (a `pg_hba`
refusal is not on the "not available yet" list in
[online service startup](#online-service-startup)), as `database_unavailable`.

**Source check.** A new `pk-stewardship database-sources` command proves
the matrix on a host without any password. PostgreSQL decides `pg_hba`
before it asks for a password, so a connection attempt with no password
ends either with the server asking for one (the source is allowed for that
login, which libpq reports as `fe_sendauth: no password supplied`) or with
the server's refusal (SQLSTATE `28000`). Because the file ends in an
explicit `host all all all reject`, every refusal matches a line, so the
server's message is `pg_hba.conf rejects connection for host ...`, never
`no pg_hba.conf entry`; both are `28000`. libpq reports a failed
connection without its SQLSTATE (as
[online service startup](#online-service-startup) also relies on), so the
command classifies by those two messages, which only this refusal and
libpq's own password prompt produce; any other answer is reported as
unknown and fails the check. Each probe forces an empty password: it
clears every `PG*` variable (no `PGPASSWORD`, `PGPASSFILE` or `PGSERVICE`)
and points libpq's password file at an empty file, so a mounted password
can never turn a probe into a real sign-in; a connection that succeeds
anyway was accepted without a password and is a security failure. The
command tries every login from where it runs and prints, per login,
`allowed` or `refused` with the source address (the local address the
kernel routes to PostgreSQL from) and the service, then compares with the
rendered matrix and exits non-zero on any difference. The expected row is
the logins the service's own document holds, from the same function the
renderer derives that service's rules from. The scripted upgrade runs it by `docker exec` in every running
online service and by `compose run` in each one-shot profile, after step 6
starts the services; restores, drills and pk-local run the same check. A
login allowed from a source the matrix does not list is reported as a
security failure; the operator decides between a fix-forward release and the
rollback below.

**Tests (CI and pk-local).** Unit tests, with no PostgreSQL, cover the
renderer: every login in the inventory has exactly its sources, there is one
final reject and no TCP `trust`, the operator is reachable over TCP only from
`database-provision`, addresses are unique and inside their subnets, every
member of `backend` has a fixed address and drops all capabilities, and the
Production file is pinned by the golden-file test with the Compose document.
The operational Compose job (`test_operational_compose.py`, real Docker)
runs `database-sources` after its bootstrap, and adds the negative cases:
from `worker` with web's password, and from `backup-worker` with the
operator's, the connection fails with SQLSTATE `28000` and
`pg_hba.conf rejects connection`. The
PostgreSQL test shards keep the stock rules, because they test SQL, not the
network. pk-local renders the same rules; its only difference is the
seeder's fixed address, allowed for the web and download logins and only in
LOCAL, because the seeder's web-identity steps start with `docker run` from
web's definition while web runs
([seeder phases](../local-environment/spec.md#seeder-phases-and-identities));
the seeder passes that address with `--ip`.

**Rollout.** The change ships alone, in a release with no migration. Its
one grant change is the [inspection login](#database-inspection-login)
(#2148), which the previous release's upgrade check does not look at, so
the application-only
[rollback](../../../guides/stewardship-deployment-runbook.md#rollback) stays
available. Because of that grant change the upgrade check answers `f`, so
the upgrade runs as a schema change (`STEWARDSHIP_SCHEMA_CHANGE=1`,
pk-local's `deploy --schema-change`) and step 4 runs `database-grants`.
`retarget-image` renders the new topology: fixed `backend`
addresses, the `backend` network's new `ip_range`, the one-shot networks
and PostgreSQL's changed command. Compose cannot apply that to a running
project: it can neither recreate a network that a running container is on
(it fails with "active endpoints" and leaves the service it was starting
removed) nor attach a stopped container to the recreated network, so the
containers must be removed, not only stopped. That one upgrade therefore
needs a full stop, and `stewardship-upgrade-host.sh` owns it, right after
step 3 (the retarget and any fake-clock image builds) and before step 4.
It compares the retargeted Compose file with what Docker runs: for every
rendered network that already exists, its `internal` flag and, where the
file renders its address block, its subnet, its `ip_range` and (when
rendered) its gateway, since Docker reports the subnet and gateway it chose
for a network that renders none; and the set of networks the running
PostgreSQL container is on. Any difference, or a PostgreSQL that is not
running, triggers the full stop. It refuses, stopping nothing and naming
them, while a one-off container of the project is unfinished (running,
paused or restarting: a cron backup), so a backup is never killed
mid-copy, and checks again before it removes any network. Otherwise it stops PostgreSQL and
Valkey through Compose (for their stop grace periods), stops and removes
every other container of the project except Caddy, never removing a volume
(every data path is a bind mount), removes every network of the project
Caddy is not on (read from Caddy's own container, so a stopped Caddy keeps
its networks too), starts PostgreSQL and Valkey on the rendered networks,
and compares again, which must find no difference. The inspections it
compares go to files, never to a command line. Any failure stops the run
with the maintenance page up, and the run is safe to repeat: a repeat
finds web down and, when PostgreSQL is stopped, removed or on other
networks than the file the earlier run retargeted renders, finishes the
full stop before its step 1 needs PostgreSQL, so step 1's check and backup
(and a rollback's check, which would otherwise point at a database
restore) see a running database. The hba check runs on the recreated PostgreSQL, then step 4 (the
upgrade check, `pg_trgm`, the migration, which finds nothing to apply, and
`database-grants`, which creates the inspection login) runs against it, and step 6 starts the other
services and runs the source check. Caddy stays up throughout: it is only
on `ingress` and `proxy`, which do not change, and it keeps serving the
maintenance page. The stop adds a minute or two inside the maintenance
window (PostgreSQL's shutdown and start). Later upgrades find nothing to
change and skip it. The off-site copy and cron backups need no change; a
cron backup that starts during the stop fails as it would in any upgrade's
offline phase. The runbook's
[database connection sources](../../../guides/stewardship-deployment-runbook.md#database-connection-sources-2111)
section says how to tell success from failure.

`pk-restore` runs the same comparison and full stop (one block of shell
shared with the upgrade, kept identical by a test) in its step 7, right
after `retarget-image` and the pull and before `revoke-automation-sessions`
and `restore-begin` need the `admin-recovery` network: a set rendered
before this change (whose step 5 or running deployment started PostgreSQL
on the old networks), or a restore with an image from before it, gets
PostgreSQL and Valkey recreated on the rendered networks first. Recreated
or not, since `retarget-image` may have rewritten the rules, it then runs
the upgrade's reload and hba check (a second shared block, also kept
identical by that test): the hba check must answer `t` before any one-off
connects, and a failure stops the restore in step 7. After web's health
check it runs the hba check again and the source check from web and from
`admin-recovery` (a drill starts nothing else): the probes alone cannot
tell the rendered method from another, which the hba check's digest of the
rendered file proves. An image whose rendering has no rules (one from
before this change) runs neither. A difference fails the run with web
still up, and the Restore review waits for a fix.
The local laptop environment's `deploy` and `deploy --rollback` run the
upgrade's host half, full stop included. Its `reset`, and a
`snapshot restore` of a snapshot taken on another image, remove the
containers and networks (never a volume) before they put the snapshot's
root back, since a snapshot taken before this change renders the old
networks. A `snapshot restore` of a snapshot taken on the running image
only stops the services and recreates them: the same image renders the
same networks. Once pk-local runs the new topology, only a checkout with
this change may deploy, roll back, reset or restore it (the local
environment's [operator script](../local-environment/spec.md#operator-script)
says why).

**Rollback.** Under the
fail-forward policy a wrong rule is
fixed in a new release. A service whose own login is refused cannot start,
so if the site is down the operator runs the scripted rollback, from a
checkout of **this** release or later, never of the previous one: the
upload of the host half comes from the invoking checkout, and only this
release's host half has the full stop. The previous image's
`retarget-image` renders Compose without `hba_file` and with the old
networks, so the comparison above differs again (the `backend` network
loses its `ip_range` and PostgreSQL its one-shot networks), and the same
full stop removes the containers and the one-shot networks and starts
PostgreSQL on the untouched stock file in its data directory, with the
services on dynamic addresses again. The rollback therefore needs the same
full stop, in the same place (after step 3). The previous release's own
tooling cannot do it: its step 6 `up web` fails with "active endpoints"
while PostgreSQL runs, leaving web removed and the maintenance page up, and
a PostgreSQL left running would keep the new rules, which refuse the old
release's services on their dynamic addresses. An operator who used it by
mistake repeats the rollback from this release's checkout, which finds and
finishes the partly changed project. Because the full stop recreates
`backend` from the previous rendering, the previous release never runs on a
network with the `ip_range`, so the `backend` network keeps its `ip_range`
(decision 12's fallback is not needed). Nothing in the database changed, so
no restore is ever needed; a test on real Docker renders the Production
topology, runs the shared block for a rollout and then a rollback, and
proves that the rows, Caddy's container and the stock rules come back
unchanged. The new `runtime_network.offline` field is written to the
provisioning record (and to the service documents) only when the
deployment YAML sets it to a block other than the default, so the previous
release's `retarget-image` accepts the record; the implementation proves
this with the previous release's image before the release ships.

### What source rules cannot stop

- A compromised container acting as its own login: the grants and
  admissions limit that, not `pg_hba`. Web keeps both web and download.
- Valkey: it has no source-address rule. Each online service still mounts
  only its own Valkey password, and after this change the backup worker,
  which reads them all, has no route to Valkey.
- Anyone with host root or `docker exec`: the socket's operator `trust` and
  every container are theirs already. That includes starting a container on
  `backend` with another service's fixed address (`--ip`) while that
  service is stopped.
- Reading data in transit on `backend`: there is no TLS on the internal
  network.
- Sessions already open when the rules change: PostgreSQL applies
  `pg_hba` only when a session starts (the rollout recreates everything).
- The backup worker still holds every password in its archive; it can no
  longer use them from its network, but whatever reads a backup set's
  plaintext could. Sets are sealed to an off-host key, which is unchanged.
- The checked-in development Compose file, which uses one login, is out of
  scope.

## Database inspection login

**Status: shipped in stewardship-v1.25.0 (#2148).** Read-only inspection of Production (counts,
mail-queue checks, pre- and post-deploy checks) uses
`pk_stewardship_inspect` through the `tools/stewardship-ops/pk-inspect` host
wrapper, never the operator superuser, whose `READ ONLY` transaction one
statement can lift. The login holds no table write privilege, so a mistaken
or pasted statement fails in PostgreSQL itself. Its definition is
`inspect_login.py`.

- **Attributes.** `LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB
  NOCREATEROLE NOREPLICATION`, connection limit 3 (outside the services'
  connection budget, like the operator's own sessions, so kept small), the deployment's provisioning marker, and **no password**: every
  TCP rule is `scram-sha-256`, so it can never sign in over TCP, whatever
  the rules say. It connects only through the PostgreSQL container's
  socket, which needs `docker exec` (host root). The stock rules trust the
  socket for every login; the [source rules](#logins-and-their-sources) add
  one socket line for it.
- **Settings.** `default_transaction_read_only = on`,
  `search_path = pg_catalog, public`, `statement_timeout = 5min`,
  `lock_timeout = 5s` (an inspection gives up rather than queue behind, and
  so block, an upgrade's locks), `idle_in_transaction_session_timeout =
  10min` and `idle_session_timeout = 60min` (a forgotten terminal frees its
  connection). These are defaults a session can lift, and, as every
  PostgreSQL login may, the login can change its own password and role
  settings (`ALTER ROLE` on itself), which lasts beyond the session; lifting
  the read-only default still leaves every table write refused by the
  grants.
- **Authority.** `CONNECT` (no `TEMP`), `USAGE` on `public`, SELECT as
  below, and one membership, `pg_read_all_stats` `WITH INHERIT TRUE, SET
  FALSE`, for `pg_stat_activity` and the statistics views. `SET ROLE` and
  `SET SESSION AUTHORIZATION` are refused. No sequence privilege, no
  `EXECUTE` on any SECURITY DEFINER routine, and none of the isolation drift
  every login is refused for.
- **What it reads.** SELECT on every relation in `public`, decided from the
  live catalog each time `database-grants` runs, except credential material:
  Django's session table, OAuth tokens, every table under row-level security
  (the credential handoff tables), and named columns (password and secret
  fields, the Django session key in the portal and Family session records,
  the automation secret's digest, Family code and link ciphertexts and
  verifiers, sealed link substitutions, setup exchange ciphertexts). A table
  with an excluded column gets column grants on the rest. A view reads
  with its owner's privileges, so a view or materialized view that reads an
  excluded table or column, a hidden view or a SECURITY DEFINER routine gets
  no grant, and neither does any view over a table with excluded columns
  unless it is listed as reviewed (`REVIEWED_VIEWS`): PostgreSQL records a
  whole-row reference such as `to_jsonb(t)` beside a named column as that
  column alone, so the catalog cannot rule it out. A change to a reviewed
  view's definition needs the same review. Partitions and inheritance
  children of such tables are hidden too. Everything else is readable, names
  and email addresses included. A PostgreSQL test fails when a column whose
  name looks like credential material is neither excluded nor reviewed.
- **Provisioning.** The migration login cannot create roles, so the operator
  does, with no migration: `database-roles` creates the login (it must exist
  before a [restore](#restore) loads a dump that carries its grants) and
  `database-grants` creates it where it is missing (an existing deployment's
  first upgrade with it) and grants it. An existing login that differs in any
  attribute, password, setting, membership or write or other-schema grant is
  refused, never repaired, as for every other login. A SELECT the catalog
  rule no longer gives (a granted view replaced to read a secret, a table put
  under row-level security, a newly excluded column) is revoked by
  `database-grants`, since that only narrows what the login reads; no
  migration has to name a login that may not exist. The [upgrade check](#production-upgrades)
  also requires the login and exactly its grants, so the first upgrade that
  carries it answers `f` and runs `database-grants`. A restore of a set from
  before it leaves the login without grants until `database-grants` runs.
- **Wrapper.** `pk-inspect [psql options]` runs `docker exec` into the
  project's PostgreSQL container (`PK_INSPECT_PROJECT`, default
  `stewardship`, pk-local's `parishkit-local`, or `PK_INSPECT_CONTAINER`)
  and opens `psql -X` as the login on the container's `POSTGRES_DB`. The
  connection options come after the caller's, so they are the ones psql
  uses; `--`, after which psql would take them from positional arguments,
  is refused. It also passes `PGOPTIONS` with
  `-c default_transaction_read_only=on` last (after any `PGOPTIONS` the
  caller sets, which is kept), so every session starts read-only even if
  the login's own read-only default was reset (#2291).

**What it does not stop.** It is a guard against mistakes, not a write
boundary, and not a guard against host root (#2291): inside `psql`,
`\connect` to the operator still works through the socket's operator
`trust`, and anyone with `docker exec` holds every login already.
`pg_read_all_stats` shows other sessions' current statement text, and
Django binds parameters on the client, so a statement running at that
moment can show a literal value (a code being checked, for example). Like
any login it can hold `ACCESS SHARE` locks, `LISTEN` and `NOTIFY`, and take
advisory locks: never take the application's own keys (the backup,
installer and review locks and the download slots), which would block that
work until the session ends; its timeouts are only defaults a session can
lift. With the read-only default lifted it can also create large objects
(`PUBLIC` may run `lo_create`). These functions stay executable by
`PUBLIC`: revoking them would mean re-granting them to every service that
uses them, which is more invasive than the mistakes it would prevent.

An object the login owns is the ownership drift every login is refused for,
so the next `database-grants` (and `database-roles`) refuses and the
[upgrade check](#production-upgrades) does not answer `t` until the operator
removes it. Both name the cause instead of only "differs from initial
provisioning": what the login owns, per database and kind, how many, and the
documented clean-up, which drops only the login's own large objects
(`inspect_login.ownership_cause`). The upgrade check answers that sentence
in place of `f`, and the scripted upgrade refuses it before anything stops.
Any other difference, such as a password or setting the login gave itself,
keeps the generic refusal, which points to recreating the login. Both steps
are in the
[deployment runbook](../../../guides/stewardship-deployment-runbook.md#clean-up-when-the-inspection-login-owns-objects).
The operations scripts (`tools/stewardship-ops/lib.sh` `ops_psql`) still use
the operator until the release with this login is deployed; switching them is
follow-up work.

## Startup and upgrades

Documented first deployment order is:

1. Create operator-owned config/credential/volume locations.
2. Start PostgreSQL/Valkey and verify health.
3. Run the pre-migration phase of `pk-stewardship bootstrap` through the
   [offline bootstrap profile](#offline-bootstrap-profile) if not restoring;
   it creates/validates deployment configuration, the minimal initial-Admin
   Stewardship YAML authority, installer handoff keys, Django signing/general-
   encryption keyring, Family-code MAC keyring, and email-link sealed-box
   public/private keyring, and the `/metrics` bearer credential without
   requiring application tables. Provisioning is
   idempotent, owner-only, and never overwrites a nonmatching existing keyring.
4. Run `pk-stewardship migrate` as a one-shot container, then let bootstrap
   validate the migrated empty database and import the initial applied YAML
   snapshot/Admin marker.
5. Start web/worker/scheduler/installers/proxy.
6. Complete the first-Admin wizard.

### Online service startup

Every online service's refusal is one ERROR `startup_rejected` line whose
`failure_kind` names the kind of check that refused (for example
`database_unavailable` or `configuration_unavailable`), never exception text;
the detail reaches the log only with debug logging on. The web service is the
exception: a Gunicorn worker that fails admission is wrapped as a
configuration failure, so web reports `configuration_unavailable` whatever
the worker's cause.

The rendered topology has no start ordering, so after a host reboot (and
possibly under load after a deploy or a restore) services can start while
PostgreSQL is still starting or too busy to answer (#453). Each online
service therefore waits for its database for about 60 seconds in all, with a
short growing pause between attempts and no ERROR while it waits: first for
its own login to connect and answer `SELECT 1`, then, within the same budget,
through its SQL admission. It waits only while the failure is on a closed
list meaning "not available yet": no server reply (refused, unreachable, not
yet resolvable or timed out), or the server's reply that it is starting up,
shutting down, restarting or out of connection slots (SQLSTATE `53300`
only; disk full or out of memory refuse). libpq gives a failed
connection no SQLSTATE, so the reply text decides, and any text not on the
list (a wrong password, a missing role or database, or a reply in another
language) is refused at once. A waiting service holds the online startup
lease, so offline work still cannot start meanwhile. The first pause logs
one INFO `startup_waiting` line per process (`startup_database_wait`, its
limit and the seconds already spent), never one per retry, so a slow start is
visible without debug logging (#541). When the database then answers, an INFO
`startup_wait_ended` line gives the seconds waited. A wait that runs out
logs an ERROR `task_timed_out` (`startup_database_wait`, its limit and the
elapsed seconds), then the refusal. A wrong password and a database that
stays down therefore both end as `failure_kind` `database_unavailable`; the
preceding `task_timed_out` line is what marks the database that stayed down.
The connection wait comes before the service's cheaper mount and credential
checks, so such a problem met while the database is also down reports
`database_unavailable` after about 60 seconds, and the problem itself only
once the database answers.
The wait closes the reboot race; for the burst of starts after a deploy or a
restore it is a partial mitigation, since a database that becomes too busy
after admission is not covered.

### Setup credential pickup

Setup finishes with no server step
([#2228](https://github.com/epiphany40223/parishkit/issues/2228)). The
consumers of setup's provider credentials pick them up and acknowledge them
themselves, by the same mechanism that already acknowledges a key replaced
from the web after setup:

1. **Mounts from the start.** `provision-runtime` renders `worker` and
   `mail-dispatch` identically in `compose-initial.json`, `compose.json` and
   `compose-slack.json`: the same per-service configuration document and the
   same read-only target directory mounts
   ([runtime storage](#runtime-storage)). The `initial` provider mode stops
   removing the ParishSoft, Google Workspace and Slack credentials from these
   services. Before setup the directories hold no working credential, so a
   consumer admits with a missing ParishSoft or Google Workspace file in its
   mounted directory, the credential absent from its loaded receipts, only on
   the bootstrap evidence background admission already checks: an active
   configuration in Testing, no restore review, no current campaign and a
   bootstrap validation schema. ParishSoft and Google Workspace are not added
   to the always-optional credentials (Slack): outside that evidence, in
   admission and in the receipt inventory alike, a configured deployment still
   refuses a consumer whose credential is missing. A process admitted through
   this exception records that it was.
2. **Acknowledgement in place.** Each consumer's idle pass, between tasks and
   every 30 seconds, finds the `awaiting_ack` requests that name it as a
   required consumer. When the bytes it reads from its own mount match the
   request's installed fingerprint, it first parses them as its re-admission
   will (for ParishSoft, the same source-credential check admission runs) and
   refuses to acknowledge bytes that would fail it; then it records the
   acknowledgement through its own SQL login, under the same SQL guard as the
   operator command, and republishes its loaded receipts. A setup request and
   a later key replacement are acknowledged the same way. The web process,
   the installers and the scheduler never acknowledge for a consumer, and no
   acknowledgement is recorded for bytes the consumer has not read itself. A
   read or parse failure is handled per request: the pass logs it as below
   and keeps serving.
3. **Re-admission after the configured marker.** A process admitted through
   the bootstrap exception skipped the tasks that need its missing credential
   (the worker's ParishSoft refresh and publication tasks; mail-dispatch
   registers only setup mail). Only such a process, once its idle pass sees
   the configured marker (as the end of the bootstrap evidence above, which
   the marker's own transaction ends by activating the parish's
   configuration; the mail-dispatch login cannot read the marker itself),
   has the service's runtime supervisor stop taking
   new messages, drain every consumer process in the container, log one INFO
   `consumer_readmission` line (reason `setup_completed`) and exit with status
   0. A missing Slack file never triggers this. Compose's
   `restart: unless-stopped`, which every online service has in Production
   and LOCAL, starts the same container again: its mounts are unchanged and
   now hold the installed files, so the fresh process admits with every
   credential and registers its full handler set. This happens once per
   deployment; a consumer admitted with its credentials never exits for this
   reason. The exit waits for the setup finalization task to commit, since
   the pass runs only between messages. The development profile renders no
   restart policy, so there the process does not exit; restart it by hand
   after setup.
4. **Cancellation and expiry.** When the installers roll an unfinished setup
   back, the directory mounts show the removal, so no consumer needs
   recreating before another attempt, and the pass drops that target from
   the receipts it publishes. The SQL guard already refuses an
   acknowledgement after the request's expiry.

No application container gains the Docker socket, a host path or any power
over another service: a consumer restarts only itself, by exiting, and the
restart is Compose's own policy. Each credential stays mounted read-only only
into the services that consume it, and the handoff private keys stay outside
the mounted directories.

**Failure stays visible.** A consumer that cannot read or parse its mounted
file, or reads bytes that do not match a request that names it 10 minutes
after the installation, logs `installer_request_failed` at ERROR with
`failure_kind` `credential_pickup_overdue`, at most hourly per request in each
process, and Finishing setup says the step is taking longer than expected
([admin portal](../admin-portal/spec.md#bootstrap-and-first-admin-wizard)). A
consumer that fails re-admission logs its ordinary `startup_rejected` line and
Compose keeps restarting it, as for any other startup refusal. The request's
own expiry still restores the previous state, and the
[runtime guide's fallback](../../../guides/stewardship-runtime.md#first-database-and-application-startup)
(recreate the consumers and run `acknowledge-credential` inside them) still
works and is the only path for a credential with an individually overridden
path, which keeps a single-file mount.

### Installer idle polling

The configuration installer and the credential installers are idle almost
all the time, so their loops are built to cost little while idle (#639; the
measurement is in
[#629](https://github.com/epiphany40223/parishkit/issues/629)).

- **Wakes and health.** Each loop wakes every 2 seconds and publishes its
  health heartbeat on every wake, however long ago its last full pass ran.
  The container probe's 90-second limit therefore still means "the loop has
  stopped", and a pass that hangs still turns the container unhealthy.
- **One connection.** A loop keeps its database connection open between
  passes. The connection budget already reserves it: each installer login's
  connection limit is the rollout overlap, one connection for each of the
  old and new container while a rollout overlaps them, and the background
  connection budget counts every installer process times that overlap.
  Outside a rollout the second slot stays free for the process's private
  timeout-log connection. While old and new containers overlap, each keeps
  one connection, so both slots are used: a timeout entry written then
  cannot open its private connection, loses its durable copy, and is kept
  only in the process log, as for any
  [entry that cannot be written](#observability-and-health). The overlap is
  short and the installers' passes rarely log timeouts. Each
  wake first drops a kept connection that no longer answers (one
  `SELECT 1`), so after a PostgreSQL restart the wake reconnects quietly
  instead of logging a failure. Any failure also closes the connection, and
  the next wake reconnects. A failed pass is retried in full after the usual
  failure backoff (doubling from 2 seconds, at most 60 seconds).
- **Cheap work check.** On each wake a credential installer first runs one
  cheap check that takes no lock: whether its target has a pending
  replacement request or an interrupted replacement journal and, for the
  setup targets, whether a setup exchange awaits a reply (ParishSoft and
  Google Workspace), a Slack test is queued or sending (Slack), a backup
  "Test access" check is waiting (Google Workspace), or a frozen setup
  attempt awaits an initial installation not yet recorded for this target
  (all three). The relay checks can match an exchange that is no longer
  live, because the live proof needs the work-order lock; such a row lasts
  only until its setup attempt ends and keeps the loop at its 2-second pace
  meanwhile. When it finds work,
  the full pass runs on that wake. Otherwise the full pass runs only after
  an idle gap that doubles after each pass that finds nothing, from
  2 seconds up to 30 seconds, and drops back to 2 seconds after a pass that
  does work. The configuration installer has no such check: its idle pass is
  already two small reads, so it keeps a full pass on every wake.
- **The global lock.** The setup steps of the ParishSoft, Google Workspace
  and Slack installers make the same cheap read before joining the
  [work-order lock](../architecture/spec.md#work-order-lock-scope), and skip
  the lock when there is nothing to do. An installer past setup never takes
  that lock while idle.
- **Latency.** Everything that setup and the Admin portal wait on (setup
  relays, Slack tests, initial installation, key replacements and backup
  access checks) is seen by the cheap check, so it starts on the next wake,
  within 2 seconds, as before: the change adds no wait that a person sees.
  Only work the check cannot see would wait for the idle gap, at most
  30 seconds after the previous full pass, and the check covers every queue
  the full pass serves.
- **Grant admission.** Every queue operation still checks the actual login:
  the exact role, still able to log in and not past its `VALID UNTIL`, with
  no superuser, bypass, membership or schema-create authority. The same
  identity check, which every runtime login's admission runs and not only
  the installers' (online services, download, offline profiles and
  backup), also refuses another role's membership in the login, an object
  of any kind that the login owns, foreign-data wrapper or server
  `USAGE` (#389), and a `SET` or `ALTER SYSTEM` parameter privilege granted
  to the login, to `PUBLIC` or to a role the login belongs to (#845; `SET`
  on `session_replication_role` would skip the row guards), and its
  refusal names the checks that failed. The backup
  login also has its grants compared, as far as `pg_read_all_data` leaves
  anything to compare, as the
  [backup guide](../../../guides/stewardship-backup.md#the-whole-trees-read-only-by-one-identity)
  describes. PostgreSQL
  checks the login attribute only when a session starts, and `VALID UNTIL`
  only when it authenticates a password, so this check is what makes the
  installer's own code stop on a kept connection once its login is
  disabled or expired. The full grant
  inspection reads the whole catalog, so a credential installer runs it at
  startup, on every new connection (including each reconnect), when the
  login's role is not the one it last admitted, and otherwise again each
  time 5 minutes have passed since the last one. The cheap check admits
  first, so the interval holds even while full passes are backed off. A
  refused inspection is never remembered: every later call inspects again.
- **The admission window.** An excess grant made on a live database can
  therefore go unnoticed for at most 5 minutes (plus one 2-second wake)
  before the installer refuses to continue. Grants change only through
  `database-grants` at a deploy or an upgrade, which stops and restarts
  every installer, so the startup inspection sees every planned change at
  once. A change outside that procedure needs the database operator or a
  superuser, who already holds more authority than the installer would
  gain. PostgreSQL itself enforces every grant and every revocation on each
  statement; the inspection only detects authority beyond the closed list,
  as defence in depth. The 5-minute default is chosen; the Administrator's
  decision on it is open on #639.
- **Containing a compromised installer.** The identity check's refusal of
  a disabled login only stops well-behaved code: a compromised process can
  skip it and keep using its session. A revoked grant is enforced by
  PostgreSQL on the next statement, but neither change ends the kept
  session. To cut it off, first disable the login
  (`ALTER ROLE … NOLOGIN`) or revoke the grants, so that the next wake
  cannot reconnect, and then end its backend with `pg_terminate_backend` on
  the installer login's process, found in `pg_stat_activity`; that needs a
  superuser or a role with `pg_signal_backend`. Stopping the installer's
  container also ends it.
- **Timeouts.** These loops stop no work at a time limit and kill nothing.
  The idle gap and the failure backoff only delay the next pass, so they
  write no timeout entry; the provider checks the passes run keep their own
  [timeout entries](#observability-and-health).

### Pre-production development policy

This policy ended for the live deployment when Production went live on
October 3, 2026; the [post-launch schema policy](#post-launch-schema-policy)
now governs schema changes. It stays here as the record of how the
fresh-install baseline was built and of what it never authorized.

Human decision, September 13, 2026: all remaining stewardship implementation is
pre-production until the human explicitly activates production-readiness work.
Maintain a current fresh-install schema baseline. Do not build or test database
or application upgrade/downgrade paths, preserve intermediate development schema
compatibility, or add legacy compatibility code solely for old development
installations. Update the baseline and its model state as functionality evolves,
with fresh-install and current-functionality regression evidence. This standing
policy supersedes upgrade-related acceptance work elsewhere in the plans and
tasks while pre-production continues; do not block phase delivery on it.

Fresh installation, model/schema agreement, current constraints and permissions,
transactions, restart/persistence, failure recovery and current-format backup/
restore tests remain relevant. Business workflows such as campaign transitions,
credential rotation, configuration versioning and cancelling an unapplied setup
are current application behavior, not obsolete software upgrade paths.

This policy never authorizes deleting existing development databases, faking
migration state or claiming compatibility with discarded development schemas.
Keep existing databases/volumes intact and use separately selected empty ones
when required. See the [schema baseline guide](../../../guides/stewardship-schema.md).

The [v1 launch scope](../../../plans/stewardship/v1-launch.md#production-readiness-activation-and-schema-freeze)
records when this policy ends for the live deployment and the schema freeze
that precedes the first live campaign.

Application containers do not race to run migrations. Current startup refuses
an unsupported schema or missing credential file and reports a sanitized,
actionable error. These fresh-install and restart protections remain required
throughout pre-production.

### Post-launch schema policy

Production went live on October 3, 2026. From then on every schema change
reaches the live database through a forward Django migration, while CI, the
local environment and a restore-drill host install the current tree from
scratch. A fresh install runs the baseline and then every forward migration,
so the baseline plus all migrations *is* the current schema, and both paths
must end in the same catalog. The rules:

- **Applied migrations are never edited.** A migration a release has shipped,
  and the SQL it installs, is immutable; a correction is a new numbered
  migration. Never `--fake` a migration or edit `django_migrations`.
- **Each forward migration installs one frozen SQL file**, under
  `src/parishkit/stewardship/schema/migrations/`, that the migration reads
  whole through `Path(__file__)` and never parses; inline SQL is not used. A
  forward migration (any migration after the baseline) must not read the
  `schema/*.sql` baseline files, whose later edits would change what it
  installs. The file starts with a `--` header comment, then
  `SET LOCAL check_function_bodies = false;` (so a body naming an object a
  later migration owns cannot break it) and `SET LOCAL search_path = public;`,
  and ends with a `DO` block that raises unless everything the file installs
  is present, so a partial install fails the migration instead of surfacing
  later. `tests/stewardship/test_schema_migration_files.py` pins each frozen
  file's digest. `campaigns/migrations/0002_family_engagement.py` and its
  `0002_family_engagement.sql` are the model.
- **File names form one repository-wide sequence.** Django numbers migrations
  per app, so two apps can both have a `0002`; the frozen files must not. Name
  each file `NNNN_topic.sql` with the next unused four-digit prefix across the
  directory, whatever the Django migration's own number, so the files sort in
  apply order; make the new Django migration depend on the migration that
  installed the previous frozen file so the database applies them in that
  order too. `0002_family_engagement.sql` (campaigns' `0002`) is already
  applied and keeps its name; the next file, for example
  `stewardship_source.0002_refresh_tick_times`, is `0003_refresh_tick_times.sql`.
- **New objects live only in their migration's frozen file.** A table, index,
  constraint, trigger or function that a migration creates is not added to the
  `schema/*.sql` baseline files (`stewardship_family_engagement` is not in
  `tables.sql`): a fresh install creates it by running the migration. A new
  function uses plain `CREATE FUNCTION`.
- **Objects a migration alters get their final definition in the baseline.**
  When a migration changes a function, view, constraint or column the baseline
  installs, the `schema/*.sql` file is edited to the final definition and the
  migration re-creates the object idempotently (`CREATE OR REPLACE FUNCTION`,
  drop-and-add constraint), copied verbatim. For functions, the generic test in
  `test_schema_migration_files.py` checks that the latest frozen file's
  `CREATE OR REPLACE FUNCTION` copy equals the baseline's definition, and that
  a replaced function exists in the baseline or was created by an earlier
  frozen file (a migration-owned function is replaced freely, as every path
  runs its migrations). It checks a replaced view's latest text against the
  baseline the same way (see the next rule); constraints have no such text
  check, and only the upgrade-parity test catches their drift.
- **A view that comes to name a migration's object keeps its baseline
  body.** The baseline creates views with their bodies checked, before any
  migration runs, so a view whose new body names a table or function only a
  frozen file creates cannot take that body in `schema/*.sql`. It keeps the
  last body the baseline can install, and the frozen file's
  `CREATE OR REPLACE VIEW` is its final definition on every install path
  (fresh installs run the migrations too).
  `test_schema_migration_files.py` lists each such view by file
  (`MIGRATION_FINAL_VIEWS`, first used by report emails,
  [#1786](https://github.com/epiphany40223/parishkit/issues/1786)) and
  checks every other replaced view's latest text against the baseline.
- **A replaced function keeps the baseline's attributes.** `CREATE OR
  REPLACE FUNCTION` keeps the owner and grants but resets every attribute
  the command does not name. When the baseline sets one with a separate
  `ALTER FUNCTION` (such as `SECURITY DEFINER` in
  `schedule_reconciliation.sql`), the frozen file repeats that statement
  after the replacement and its `DO` block checks it;
  `test_schema_migration_files.py` enforces this for `SECURITY DEFINER`.
  The upgrade-parity test cannot catch the omission, because a fresh install
  runs the same migration.
- **New SQL names `pg_catalog`'s jsonb and containment operators.** An
  unqualified operator is resolved through `search_path`, and PostgreSQL
  chooses the closest argument-type match among every schema on the path, so
  `pg_catalog` first in a function's fixed path does not by itself keep a
  look-alike in `public` from winning. Every frozen file after 0178 therefore
  writes `?`, `?|`, `?&`, `@>` and `<@` as `OPERATOR(pg_catalog.?)` and so on
  (a replaced baseline function's copy equals its latest frozen copy, so the
  baseline follows), for example
  `settings OPERATOR(pg_catalog.?) 'organization_id'`;
  `test_schema_migration_files.py` refuses a bare one in those frozen files
  (comments and string literals aside), and the files up to 0178 keep theirs,
  since applied files are never edited. Migration 0179
  ([#2166](https://github.com/epiphany40223/parishkit/issues/2166)) did this
  for every `SECURITY DEFINER` function, and a database test refuses a
  definer function that uses one bare. The control that matters is that no
  runtime login can create an operator at all (the upgrade check below);
  qualifying is defense in depth. The rule names these five, the subject of
  #2166; every other unqualified operator (`=`, `||`, `->` and so on) and
  unqualified function name has the same exposure, and so do the invoker
  functions that keep their bare operators until a migration replaces them
  for another reason, which run with the owner's rights when a definer
  function calls them or a definer's write fires them. All of these rest on
  the no-`CREATE` control alone.
- **Upgrade parity is tested.** `tests/stewardship/database/test_upgrade_parity_postgresql.py`
  installs the previous release tag's tree (the nearest annotated
  `stewardship-v*` or older `v*` tag reachable from HEAD; see
  [Versioning and releases](../../intro/spec.md#versioning-and-releases))
  in a second database, migrates it with the current tree and compares the schema inventory with a fresh install of the current
  tree built in a third database; any difference fails CI, whose database
  shards therefore check out the full history. Downgrade remains by database
  restore only.

Adding a forward migration, in order:

1. Write the frozen file with the header, `SET LOCAL` lines and closing `DO`
   block described above, named by the sequence rule.
2. Pin its digest in `tests/stewardship/test_schema_migration_files.py` and
   add its allowlist line to both `.dockerignore` and
   `deploy/stewardship/Dockerfile.dockerignore`.
3. Write the Django migration after `0002_family_engagement.py`: `RunSQL` of
   the file read through `Path(__file__)`, and the model-state operations.
   Its `dependencies` name its own app's latest migration *and* the Django
   migration that installed the previous frozen file, so the files apply in
   prefix order; for example `stewardship_source.0002_refresh_tick_times`
   depends on `("stewardship_source", "0001_initial")` and
   `("stewardship_campaigns", "0002_family_engagement")`. A test in
   `test_schema_migration_files.py` checks that chain.
4. Edit the `schema/*.sql` baseline files only for objects the migration
   alters, as above.
5. Run `python -m django makemigrations --check --dry-run` (as CI does) to
   prove the model state matches the models.
6. Regenerate `tests/stewardship/database/schema-baseline.json`; the
   [schema baseline guide](../../../guides/stewardship-schema.md#equivalence-and-regression-evidence)
   describes that fingerprint.
7. Announce the change in the release notes, which tells operators to run the
   [scripted upgrade](../../../guides/stewardship-deployment-runbook.md#scripted-upgrade)
   with `STEWARDSHIP_SCHEMA_CHANGE=1`.

### Production upgrades

Production is upgraded by the deployment runbook's
[upgrade](../../../guides/stewardship-deployment-runbook.md#upgrade)
procedure, normally through its
[scripted upgrade](../../../guides/stewardship-deployment-runbook.md#scripted-upgrade),
under the [post-launch schema policy](#post-launch-schema-policy). Only the
broader checks named at the end of this section stay deferred.

A production upgrade pulls the pinned image while the site is up, then
stops the background services and takes a backup, abandoning the upgrade
if that backup has no recorded off-host copy; only then does it stop web,
retarget the image, run migrations and grants when the upgrade check says
they are needed, and restart services. The image of a provisioned deployment
changes only through the image-retargeting command, which rewrites the rendered
topologies and the provisioning record under the offline startup exclusion and
refuses any other change; the operator then runs the migration profile and the
grants command, or skips both when the new image's upgrade check proves from
the live database that neither would change anything, and starts the
services, as the
[release image guide](../../../guides/stewardship-release-image.md) and the
runtime guide describe. The upgrade check also refuses the identity drift
that every runtime login's admission refuses (see **Grant admission**
above), and the backup login's definer, sequence and `CREATE` authority
that its backup admission refuses, so an upgrade onto a drifted catalog
stops before its services would all refuse to start or its next backup
would fail. It also answers false on `CREATE` on an application schema or
the database for any runtime login, and on any operator outside `pg_catalog`
other than the `pg_trgm` extension's own (#2166), so a release with no
schema change stops on either; a schema-change upgrade runs the commands
anyway, and then only each runtime login's admission (which refuses
`CREATE`, not operators) stands guard. The migration login has no runtime admission, so the
`database-roles` and `database-grants` commands' role check and the
migration profile's own identity check refuse the same drift for it (#389).
In those checks and in the upgrade check, the migration login owns the
schema and is exempt from the ownership check, and only from that one.
The commands' role check refuses the same drift for every other login too.
That upgrade check and the upgrade-parity test are the
automated upgrade checks in v1; broader migration checks and upgrade readiness
checks remain deferred. Migrations must be forward-safe for the
declared rollout; destructive column removal follows expand/migrate/contract
across releases.

Rollback instructions distinguish application rollback (only when schema is
compatible) from database restore.

Every upgrade pulls a new release image and none is ever removed by Docker
itself, so after a successful scripted upgrade or rollback the host prunes
old release images (#1661). It removes only images of the stewardship
release repository, one at a time by image ID and without force, so Docker
also refuses an image a container uses or another repository shares. It
keeps every image a container on the host uses, running or stopped, the
release just started, the replaced release (the image an application-only
[rollback](../../../guides/stewardship-deployment-runbook.md#rollback) moves
back to), and the newest few other release images (four by default,
configurable), so a catastrophic rollback under the fail-forward policy
finds the previous release on the host without a registry pull. It
then clears Docker's build cache, which holds no images or volumes and which
a host that only pulls never needs. It never removes a volume, never
removes an image of another repository and never uses Docker's blanket
prune commands for images, containers or volumes. It logs each image it
removes or keeps and the disk space freed, offers a dry run that removes
nothing, and a pruning failure is a warning that never fails the upgrade.
It prunes only when the upgrade knows the replaced release as a release
digest different from the one just started; otherwise (an unreported
replaced release, or a re-run of the same digest) it skips pruning and
says so. Any failure while finding what to keep, other than a protected
image that is not on the host, stops it before anything is removed. Real
removal is the default; the dry run and skipping the pruning are opt-in.
The Administrator approved that default (#1671) after reviewing a
Production dry-run log.
The local laptop environment's deploy does not prune: its images are built
in its VM and its build cache speeds the next build.

### Users upgrade from sign-in rules

The [Users revamp](../admin-portal/spec.md#portal-user-management)
([#952](https://github.com/epiphany40223/parishkit/issues/952)) replaces
hosted-domain and exact-address sign-in rules with one list of users, each
with one role. Production converts its rules once, in two releases, under the
[post-launch schema policy](#post-launch-schema-policy); each deploy needs the
Administrator's approval as usual. These are default decisions, which the
Administrator may override.

1. **Read-only preview first.** An earlier release adds
   `pk-admin users preview-upgrade` and an **Upgrade preview** panel on the
   Portal users page. Both run one read-only function, the same one
   `upgrade-users --preview` prints, and list exactly what the conversion
   will do. For it, `database-grants` also lets the web login execute the two
   narrow read functions of step 3 (#952 slice 2):
   - first, every address that holds Administrator today, each kept as an
     Administrator user (the list the operator confirms in step 3);
   - each domain rule, and every recorded Google identity it admits today
     that has signed in before (enabled, its email suffix and recorded
     hosted-domain claim matching the rule, and no address rule for its
     address), with the user each becomes;
   - each address rule's roles and the role it will keep;
   - each empty-role (deny) rule, which will be dropped, and by address and
     Name each recorded domain identity it overrides today; with no user, that
     address stays denied after the upgrade; and
   - what will not carry over: each domain rule's people who have never
     signed in, and disabled identities, which are not converted.

   The Administrator can add any missing address as an ordinary user (an
   address rule) before the upgrade, so nobody they expect is locked out.
   Losing any other user is acceptable: in Production only the parish's
   existing Administrator access must survive, and anyone else can be
   re-created as a user afterwards. So the conversion carries over what the
   preview shows and needs no special handling for any other case.
2. **Conversion as one configuration change.** The conversion follows the
   preview's rules exactly:
   - every Google identity that a domain rule admits today and that has signed
     in before becomes a user with that domain rule's highest role (Staff or
     Ministry leader); its Name is the
     [default from ParishSoft](../admin-portal/spec.md#name-from-parishsoft),
     or the email's local part when no Member lists the address;
   - every address rule becomes a user with its highest role, and keeps its
     address; its Name is chosen the same way;
   - deny rules are dropped: with domain rules gone, an address with no user
     is already denied.

   It writes one new configuration version through the configuration
   installer's normal base-digest, schema-validation, prepare/manifest and
   activation protocol, with audit; stored versions are never edited. No
   section-upgrade mechanism exists today, so the code slice that brings users
   builds one for the `login_rules` section, on the installer implementation,
   not as a second YAML writer. Domain rules could never hold Administrator, so
   every Administrator is already an address rule and keeps that role, and
   converted identities get at most Staff, so no Administrator alert fires.
   The last-Administrator guard always holds: activation is rechecked, and the
   conversion refuses to activate, failing closed with the preview's
   explanation, rather than leave no Administrator. One audit event records
   the conversion's counts. A configuration already in the users form is left
   unchanged, so a repeat or resumed run is a no-op.
3. **Who runs it, and when.** The conversion is part of the deploy of the
   release that brings users, in the same offline step as its migrations,
   while web, workers, scheduler, online installers and proxy are stopped
   (the [production upgrade](#production-upgrades) already stops them). It
   runs as `pk-stewardship upgrade-users` in the `admin-recovery` one-shot
   profile, under the same offline interlock, installer service identity and
   narrow authority-directory mount as
   [offline Admin-access recovery](#offline-admin-access-recovery). It
   requires a named operator and reason, and its tagged operator-upgrade
   ConfigurationChangeRequest records that operator as its actor, not a
   fabricated PortalUser. The order is fixed:
   1. **Preview:** `upgrade-users --preview` prints the same preview as step
      1 and changes nothing. The operator must confirm the list of
      Administrators to be kept, not just the run: the conversion takes it
      as `--confirm-administrators` (comma-separated) and is refused if it
      differs, never confirmed by default.
   2. **Convert:** the first migration of the release has already added the
      new columns while every SQL function still joins
      `stewardship_domain_rule`, and `database-grants` has given the
      `admin-recovery` login the two narrow read functions the conversion
      uses (recorded identities, and the ParishSoft Member names of only the
      active rules' and recorded identities' addresses, with no address
      argument, so it is no lookup for any other address); the command
      then activates the converted configuration. A compromised web
      database login can still insert a portal user (a recorded Google
      identity) for any address and then read that address's names. That is
      weaker than its existing ability to match an Admin rule, and each guess
      is recorded. New user's
      [Name from ParishSoft](../admin-portal/spec.md#name-from-parishsoft)
      lookup (migration 0053) answers any typed address. Its definer answers
      only for an Admin session key that names a live session (begun after
      the latest Admin-access recovery) whose user is an Administrator at
      that moment, records its own audit event for each lookup (that
      Administrator and the number of names returned, never the address)
      and refuses a 61st lookup by one Administrator within an hour, counted
      from those events. Since migration 0057
      ([#995](https://github.com/epiphany40223/parishkit/issues/995)) the
      database holds only a digest of each Admin session key and the
      definer compares the digest of the key it is handed, so a compromised
      web database login cannot replay a session value it reads from
      `stewardship_portal_session` or `django_session`; it would need a key
      from a live browser cookie. Against a login that holds such a key,
      the per-Administrator cap and the audit still hold: at most 60
      addresses' names an hour for each Administrator with a live session,
      each lookup traced to that Administrator. The Users page's ParishSoft
      Ministry leaders table (migration 0054, replaced by 0057 the same
      way) has the same session check and audit but takes no address: with
      a live Administrator's session key, it shows the leaders without a
      user (names, Member DUIDs, addresses and Ministries), exactly what the
      page shows that Administrator. The page
      also limits
      each Administrator's lookups per minute and refuses them while the
      limiter is unavailable. SQL's activation guard
      refuses it, and any other activation into the users form, unless every
      address that held Administrator keeps an Administrator user.
   3. **Swap functions:** the second migration replaces the SQL functions.
      Its self-verifying `DO` block raises, so the migration fails, unless the
      active configuration is in the users form.
   4. **Start:** only then are the grants applied and the services started.
      The release's startup validation also refuses to start web, workers or
      scheduler on an unconverted configuration.

   If the conversion fails or is refused (an unresolved installation or a
   YAML/database mismatch, which the existing installer protocol must recover
   first, or the last-Administrator guard), the services stay down: the
   deployment fails closed and never runs the new SQL functions on an
   unconverted configuration. An interrupted run resumes by operation ID.
   When SQL's activation guard refuses the conversion after the YAML manifest
   already names it, the refusal is recorded as the request's failure in the
   activation transaction and the installer selects the base manifest again,
   as for any other refused activation (#952 slice 3). The configuration
   stays coherent on its sign-in rules, so
   [offline Admin-access recovery](#offline-admin-access-recovery) still
   runs; a failed receipt is terminal, so a new attempt needs a new
   operation ID. Any other abort of that activation transaction (a
   deadlock, a statement timeout, any error other than SQL's refusal) is
   recorded and restored the same way: the upgrade runs offline, so a retry
   would only fail again. A crash or lost connection between the YAML switch
   and the activation records nothing; `upgrade-users` then refuses a new
   operation with an instruction to resume the interrupted one by its
   operation ID or to run `recover-admin`, which records that upgrade failed
   and selects its base again before it begins.

   The order runs as one offline step of the deploy (#952 slice 4).
   `pk-stewardship migrate --users-upgrade-base` applies every pending
   migration except the swap and any migration that depends on it, and
   never unapplies one: once the swap is applied, it is the full migration.
   `database-grants` then gives the conversion its read functions,
   `upgrade-users` previews and converts, and the full `migrate` and
   `database-grants` follow. A function or table that a migration depending
   on the swap creates is granted only once that migration is applied, as the
   applied migrations record it, so the grants on the base skip it and the
   final grants add it. Likewise, a column grant that such a migration
   revokes (mail dispatch's token ciphertext reads, #389) is still held on
   the base, so the grants there admit it without granting it until that
   migration is applied. The scripted upgrade
   (`tools/stewardship-upgrade-host.sh`) runs exactly this in its step 4
   when the operator supplies the conversion's inputs (operation ID,
   operator, reason and the confirmed Administrators, all four or none, never
   for a rollback); without them, a release whose swap refuses the
   unconverted configuration stops at its migration with the maintenance
   page up, nothing of the swap committed, and the run is repeated with
   them. A repeat after a base migration that stopped partway, which leaves
   migrations outside the swap pending, reuses the first run's step-1
   backup as its restore point (`backup --users-upgrade-resume`, within the
   24 hours `migrate` requires) and logs that it did; each step-4 command
   runs in a named one-off container that a time limit or a signal kills
   and removes by name. The
   [deployment runbook](../../../guides/stewardship-deployment-runbook.md#users-upgrade-952)
   gives the commands.

   **Recovery in every state.** `upgrade-users`, `recover-admin` and
   `preview-admin-recovery` admit a database whose only pending migrations
   are the swap and its dependents, as well as a current one, so
   [offline Admin-access recovery](#offline-admin-access-recovery) works
   before the conversion, after a refused or interrupted one (above), after
   it with the swap still pending, and after the swap. The `backup` command
   admits that state too, so a repeated deploy's step-1 backup runs
   and its set restores exactly that state (`restore-check` reports such a
   set as `swap_pending`, below). Every other command and service
   still requires the current schema. Offline Admin-access
   recovery never depends on the conversion's grants: only `upgrade-users`
   requires them, and it refuses with an instruction to run
   `database-grants` when they are missing.

   Considered and rejected: shipping the new SQL functions with a domain-join
   fallback until an Administrator converts online after startup. The
   application would then carry both authorization models in Python and SQL,
   and the Users page would run on an unconverted configuration for an
   unbounded window, so lockout and privilege bugs could hide in the mixed
   state. The read-only preview in an earlier release already gives the
   Administrator time to review and fix the outcome, so the offline step only
   applies what was previewed.
4. **Schema migrations** (two consecutive free numbers, assigned centrally):
   - the first adds `name` and `role` to `stewardship_address_rule`, with a
     check constraint that ties `roles` to the cumulative set of `role`
     ([data model](../data/spec.md#administration-user-and-policy)); both are
     empty only on the immutable rows of configurations activated before the
     upgrade;
   - the second replaces the SQL functions that decide access by joining
     `stewardship_domain_rule` (session admission, export authorization,
     Ministry scope, the role-derived leader scope and policy activation's
     security alerts and denial-namespace reset) so they read only the users
     projection. Each replacement starts from its latest prior definition with
     only that change, keeps its attributes (the leader scope stays SECURITY
     DEFINER), and the file ends with the self-verifying `DO` block, which
     also refuses unless the active configuration is in the users form. A
     database with no active configuration (a fresh install) and a deployment
     still in initial setup, judged as startup judges it, have nothing to
     convert and pass. The policy projection and completeness triggers stay
     as they are: they validate rows and grant nothing, and initial setup
     still wrote an exact-address rule until the setup wizard wrote users
     (migration 0055, #993, below); the cleanup migration removes their
     hosted-domain branches with the tables;
   - the Ministry leaders function of migration 0042 (PR #939) needs no
     migration to become callable by the web login: EXECUTE is a
     `database-grants` entry, added with the leaders import if it needs it;
   - nothing writes `stewardship_domain_rule` or `stewardship_address_grant`
     any more.

   A later cleanup migration drops those tables and the retired Ministry
   assignment tables once nothing reads them. Their history stays in the audit
   log and the immutable configuration versions.

Acceptance criteria for the upgrade (Administrator decision, #952):

- every address that holds Administrator before the upgrade can still sign
  in as Administrator after it;
- the conversion refuses, and the deployment stays down, rather than drop the
  last Administrator or any Administrator on the confirmed list; and
- if anything still goes wrong, the documented
  [offline Admin-access recovery](#offline-admin-access-recovery) is the
  fallback, and it works on a converted configuration.

Once the conversion has activated, an application rollback to the previous
release is not compatible: its code and SQL functions expect the sign-in
rules. Under the fail-forward policy, a problem is fixed in a new release; only
a catastrophic failure restores the pre-upgrade backup, the database and the
YAML configuration manifest together, plus the previous release.

Once the configuration holds users,
[offline Admin-access recovery](#offline-admin-access-recovery) writes and
previews Administrator users in the same form; the Name is optional
(`--name`) and defaults to the email's local part. A users configuration
has its own request formats (`users-patch-v10` for Admin edits,
`integration-credential-users-v10` for a credential update and
`operator-recovery-users-v10` for recovery; #952 slice 3), each keeping every
rule of its `source-cadence-v8` counterpart with the users list in place of
the sign-in rules. The same three formats build a configuration with
[remembered reminder series](../data/spec.md#schedule-revisions-and-fulfillment)
(`campaign-series-v11`, migration 0075,
[#469](https://github.com/epiphany40223/parishkit/issues/469)). Its candidate
uses that schema when the base, or an Admin edit's patch, has the series
section; the base and patch alone decide, so a retry rebuilds the same
candidate. Recovery and credential updates keep the series unchanged, and the
`upgrade-users` command treats such a configuration as already converted.

Bootstrap (`bootstrap --admin-email`) writes a users bootstrap
(`bootstrap-users-v10`, [#993](https://github.com/epiphany40223/parishkit/issues/993)):
its only record is the initial Administrator user, Named by the email's local
part. The setup wizard's request over it (`initial-setup-users-v10`) activates
a `users-v10` configuration, so a fresh install starts its services after setup
with no `upgrade-users` step. Offline recovery during setup uses
`operator-recovery-users-v10`, adds an Administrator user and keeps the users
bootstrap, so setup can still finish. Migration 0055 admits both names and
teaches each bootstrap and setup guard both forms, binding each setup format
to its own bootstrap form. A deployment bootstrapped before #993 keeps its
exact-address bootstrap and the `initial-setup-patch-v7` format: the users
release's startup refusal of sign-in rules still exempts it while it is in
initial setup, and it runs `upgrade-users` once setup finishes. Running
`upgrade-users` automatically at setup completion was rejected: it is an
offline, operator-confirmed conversion with its own audit trail.

Such a deployment may be part-way through setup with an Administrative access
step that the earlier release saved. The migration leaves that saved step as
it was. If the step granted a Staff or Ministry-leader hosted domain, Review
and Finish refuse the draft. The refusal says that domains no longer grant
access and links to the access step. The Administrator lists each person's
address and saves the step again. A domain is never silently dropped, because
that would quietly remove people's access. A step saved with empty domain
lists is still read as it is.

### Report email conversion

Report emails replace the Daily and Weekly Admin digests in one release
([#1786](https://github.com/epiphany40223/parishkit/issues/1786); the
[report-emails spec](../report-emails/spec.md#migration-of-the-daily-and-weekly-digests)
says what converts). The release's frozen migration rewrites the shared
guards and functions; the rows are converted afterwards, offline, by
`pk-stewardship upgrade-reports` in the `admin-recovery` one-shot profile,
because the configuration document is in the YAML authority store too. Its
operator upgrade request (operator `system`, reason "#1786 conversion",
request schema `operator-upgrade-reports-v12`) installs the converted
configuration through the installer's journaled path, and its activation
retypes each digest definition in place. It needs no inputs and does
nothing once nothing is left to convert, so the scripted upgrade runs it in
step 4 after the migrations and the grants on every upgrade. Web, workers,
scheduler and mail dispatch refuse to start while the applied
configuration still holds a digest.

- **Precheck, before the window.** Schedule the window away from the
  digests' send times, and run, read-only with the operator login, the
  migration's three counts: digest occurrences `pending`, `running` or
  `delivery_unknown`; digest messages not `delivered`,
  `permanent_failure` or `cancelled`; and digest tasks `queued`,
  `running`, `retry_wait` or `abandoned`. Resolve any old stuck one
  (Sent mail, Background work) before the window: any count above zero
  makes the migration refuse. Also list the digest occurrences that
  failed without a fulfillment: each digest with one gets one coalesced
  catch-up report send to its Administrators right after the upgrade. The
  deployment runbook gives both queries.
- **If the migration refuses.** Its own changes are rolled back, but the
  migrations before it in the release are committed, and the previous
  release refuses a schema newer than its own. So restore the pre-upgrade
  backup with the previous release, as
  [production upgrades](#production-upgrades) allow, let the send finish,
  and upgrade again. The precheck exists to avoid this.
- **If `upgrade-reports` fails after the migration.** Fail forward: the
  migrated schema no longer admits digest writes, so the previous release
  cannot send its digests. Fix the cause and repeat the run, whose
  conversion step resumes; only a catastrophic failure restores the
  pre-upgrade backup with the previous release, as
  [production upgrades](#production-upgrades) allow.
- **The digest retirement, a later release.** A later release's migration
  ([#1834](https://github.com/epiphany40223/parishkit/issues/1834);
  [database functions](../report-emails/spec.md#database-functions))
  drops the digest kinds and the digest write functions. It refuses, naming
  the count, while any schedule definition is still a digest, so upgrade
  to it only from a release whose conversion has already run. The release
  helper refuses to tag a tree with the retirement file (0171) unless the
  previous release already had the conversion's (0161)
  ([operator scripts](../../../guides/stewardship-operator-scripts.md#releases)),
  so the two never ship in one release. The scripted
  upgrade checks this in its step 1, before it stops anything: when the new
  image has the retirement file (0171) and the database has not applied
  the conversion's migration (`stewardship_reports.0041`) or still has a
  digest definition, it refuses with nothing changed. Deploy the
  conversion's release first (its run includes `pk-stewardship
  upgrade-reports`), then the later one. If the migration refuses anyway,
  the migrations before it (the conversion's 0161 and any later one up to
  the one that refused) may already have committed in the same run: do
  not run the conversion on that database; restore the
  pre-upgrade backup with the previous release, as
  [production upgrades](#production-upgrades) allow, upgrade to the
  conversion's release first, and then to the later one.

### Offline bootstrap profile

The `bootstrap` Compose profile is an explicitly operator-invoked, one-shot
provisioning command, excluded from ordinary service startup and never callable
from the web UI, scheduler, or task queue. It is the initial-provisioning
exception to online installer-only write authority, not a general configuration
repair or credential-rotation interface. Both bootstrap phases run before
web, workers, scheduler, installers, and proxy start; PostgreSQL and Valkey may
run for readiness checks, migration, and initial database materialization.
The deployment startup workflow must enforce mutual exclusion between
bootstrap and online application services, including concurrent start attempts,
without granting bootstrap Docker-socket access.

Use a dedicated one-shot service identity with operator-provisioned ownership
and restrictive file/directory modes. Its write mounts are limited to the
deployment-config target, the Stewardship-authority directory, and separately
enumerated credential target subdirectories for initial installer handoff keys,
Django signing/general-encryption keys, Family-code MAC keys, email-link
public/private keys, and the metrics bearer credential. Mount existing Google
OAuth and database credentials individually read-only as needed; these are
operator-provided inputs, not permission to rewrite unrelated credentials.
Any startup-interlock storage must likewise be narrowly scoped. Do not mount
the whole config or credentials tree, broad host paths, campaign files, or a
Docker socket. Honor configured path overrides and final consumer ownership;
never relax permissions to accommodate provisioning.

Pre-migration provisioning requires no application tables. After migration,
bootstrap may import only the initial configuration/Admin marker into the
empty application database. Matching partial provisioning can be resumed
idempotently; nonmatching existing configuration/key material or an already
configured/restored deployment is refused without overwriting it. Remove the
one-shot container after completion; its write mounts are never inherited by
online services. Test the rendered mount/identity boundaries, offline startup
interlock, partial-run recovery, nonmatching-input refusal, and both bootstrap
phases in local and production Compose configurations.

### Offline Admin-access recovery

`pk-stewardship recover-admin` is a separate operator-only command for loss of
usable Google Admin access, including account rename or deactivation. Invoke
it explicitly through an `admin-recovery` one-shot Compose profile with host
operator authority; it is absent from ordinary startup and cannot be invoked
by a web route, task queue, or scheduler. It creates no application session,
password, recovery token, or alternate authentication provider. An operator must
independently establish the parish's authorization to grant the replacement
Google email; the command does not claim to verify account ownership offline.

Stop web, all workers, scheduler, online installers, and proxy before recovery.
Use the same deployment-wide offline/startup interlock as bootstrap, including
concurrent start exclusion; PostgreSQL/Valkey may remain running. This profile
runs the existing config-installer implementation with its service identity
and narrow authority-directory write mount, not a second general-purpose YAML
writer. Mount only deployment configuration and required database credentials
read-only plus narrowly scoped interlock storage. Grant no credential-target
write mounts, whole-directory secrets, provider/token private keys, campaign
files, or Docker socket. No online service gains this command's operator
authorization; bootstrap retains its empty-deployment-only behavior.

Require an initialized, supported database and matching active YAML/database
configuration. An unresolved configuration installation or mismatch must be
recovered through the existing installer protocol first, never overwritten.
One mismatch is that protocol's own unfinished step and never blocks
recovery: a manifest naming the candidate of a request whose activation was
refused and recorded as failed (such as a users upgrade SQL refused), over
that request's base. Another is a users upgrade interrupted between its YAML
switch and its activation, the database still on its base: recovery records
that upgrade failed. In both cases recovery selects the base manifest again
before it begins, and its preview shows that base (#952).
The command displays deployment/parish identity, current Administrator users,
the exact normalized target email, and a minimal diff. Require a named
operator, reason, and explicit confirmation of deployment identity and target
email; unattended invocation must supply equivalent explicit confirmations,
never default to yes. The permitted patch only raises the
[user](../admin-portal/spec.md#portal-user-management) with that address to
Administrator, or creates that user as an Administrator when absent (its Name
is `--name` when given, otherwise the email's local part). An existing user
keeps its Name, so a different `--name` is refused, by the preview as by
recovery itself; a resumed operation previews the Name its stored request
gives. Preserve every
other user, all existing Administrators, and all unrelated configuration.
Account rebinding, removal, or general configuration edits are not accepted;
removal of an obsolete Admin is a later normal authenticated action.

Create a tagged operator-recovery ConfigurationChangeRequest and apply a new
immutable YAML version through the same base-digest, schema-validation,
prepare/manifest/activation, and crash-recovery protocol as the installer.
Its authority is the explicit offline operator workflow, not a fabricated
PortalUser or bypass flag accepted by ordinary configuration APIs. Repeated
execution/resume of one recovery operation is idempotent and cannot emit
duplicate grants/events. Preserve prior configuration and audit history.

Matching database activation also revokes all administration-portal sessions
and pending administrative OAuth state, records a parish-owned
audit with operator identity/reason, target, operation ID, and before/after
digests/roles, and creates a persistent unacknowledged security event. Queue
operational notifications to preexisting Admins and the replacement address
under normal notification/routing policy; the offline command sends no email
itself and delivery failure cannot erase the audit or roll back recovery.
Family sessions, campaign state, submissions, and purge/restore/other admission
gates remain unchanged. A failed/interrupted activation stays fail-closed and
resumes by operation ID; never report success until YAML/database digests and
the session revocation/security event agree.

After removing the one-shot container and restarting services, the replacement
Admin must complete ordinary Google authentication. The runbook verifies that
login and reviews the security event before any obsolete access is removed.
This workflow cannot repair a Google outage, inaccessible database, or missing
required configuration/credentials; report those blockers without weakening
authentication or clearing maintenance gates.

### Download capacity and timeouts

Deployment YAML defines a finite guarded-download cap, bounded download pool,
and separate finite connection budgets for interactive traffic, background
services, and operator/recovery work. Follow the default cap and admission
behavior in [campaign read guards](../data/spec.md#campaign-read-guards).
Download pool capacity may not exceed its admission cap or spill into other
pools. Budget all processes, replicas, rollout overlap, and auxiliary database
connections; their combined configured maxima plus PostgreSQL-reserved slots
must fit the database connection limit. Each non-download class retains
positive dedicated headroom sized and load-tested for its configured worker
concurrency and the architecture's reference load. Downloads must also leave
web execution capacity for interactive requests; limiting database connections
alone is insufficient if streaming occupies every web worker/thread.

Startup/deployment validation rejects an inconsistent pool, process, database,
or timeout budget. The admission cap remains deployment-wide during scaling,
restarts, and rolling upgrades; a per-process semaphore alone is insufficient.
Expose active downloads, busy rejections, pool utilization, and guard timeouts
without recording downloaded data.

### Kept mail connections

Each mail-dispatch consumer keeps its task connection (one of its three
budgeted connections) open between messages (#365); every other service
closes its connection after each task. A kept connection is not reused once
it is 5 minutes old, and it may stay open, idle, until the consumer's next
message, which then reconnects. It is closed at once instead after a failed
message, an open transaction or a database error. Before each message, and
again after SMTP before the outcome is recorded, a consumer drops a kept
connection that no longer answers (one `SELECT 1`, as the installers do
since #639), so a PostgreSQL restart costs a reconnect, not a failed or
uncertain message. TCP keepalives on the mail login's connections let the host notice
a dead peer on an idle one.

Operators should expect:

- **Connection count.** At rest, `pg_stat_activity` shows two idle sessions
  for the mail login (one per mail consumer) that used to close after each
  message. This is inside the login's limit of six and the background
  budget, which already counted the task connection.
- **Cutting off the mail login.** Because a session survives `ALTER ROLE ...
  NOLOGIN`, run `ALTER ROLE pk_stewardship_mail_dispatch NOLOGIN`, then
  `pg_terminate_backend` each of its sessions. The consumers' next
  reconnect is then refused. Before #365 a session ended within one
  message.
- **Why 5 minutes.** The age bounds how long a session runs between
  PostgreSQL login checks (the connect-time role and superuser refusal in
  `accounts/credential_database.py`). A restart or a terminated session is
  already handled by the unanswered-connection check above, so the age does
  not need to be short for liveness. Five minutes keeps the login checks
  frequent while a send reuses each session for a few hundred messages.

The [Family mail dispatch guide](../../../guides/stewardship-family-mail-dispatch.md#two-mail-consumers)
covers the mail consumers.

For the dedicated download connections, set a finite
`idle_in_transaction_session_timeout` longer than the total download deadline
but shorter than the purge reader-drain timeout. With the five-minute download
and six-minute drain defaults, use five minutes 30 seconds. Any applicable
database transaction/session, application, or proxy timeout must likewise allow
the declared download lifetime; do not globally disable database hardening for
other traffic. The application still enforces the hard total response deadline
and closes the stream/transaction; idle timeouts are only a fallback, not the
continuous-transfer time limit. Guard-connection loss aborts the response.
Validate these relationships when overriding any deadline or scaling services.

## Backup

The [v1 launch scope](../../../plans/stewardship/v1-launch.md#reduced-for-v1)
reduces this section for the first live campaign: the
[v1 backup](../../../guides/stewardship-backup.md) is an operator-run
one-shot profile that seals a `pg_dump` and the configuration and credentials
trees to a human-held key, records each completed run, raises the overdue
incident below, and admits configured upgrades behind a recent run; the
off-host copy is the operator's own job or the optional
[copy to Google Drive](../../../guides/stewardship-backup-runbook.md#off-site-copies-to-google-drive)
an Administrator configures in the portal (for which the backup profile, which
reads the whole credentials tree and all database data, has application
egress, a wider exfiltration surface the v1 scope accepts), and the
consistent manifests, isolated
backup-worker routing, purge-triggered backup, revalidation, escrow workflow
and key rotation described here are deferred past the launch. In their
place, an Administrator may replace the public key new backups are sealed to,
after proving they hold its private key, as the
[Admin portal specification](../admin-portal/spec.md#parish-and-integration-configuration)
describes under **Backup encryption key**; old private keys stay with the
operator until the backups sealed to them expire.

The application provides a shared backup service invoked by its scheduled task,
operator command, or guarded campaign-purge web workflow, or for a request
an Administrator makes on the
[System health](../admin-portal/spec.md#take-a-backup-now) page. Each
invocation
creates one consistent backup set containing:

- PostgreSQL logical/custom-format dump and schema/version metadata;
- uploaded media/branding required by retained campaigns, and the
  [hosted file library](../hosted-files/spec.md#operations), which lives in
  the same media tree;
- deployment configuration plus every retained Stewardship YAML version and
  active manifest needed to match database configuration snapshots; and
- an explicit credential/key manifest and fingerprint list, never credential
  values or files.

Backups are encrypted before leaving the VM and transferred to an
operator-configured off-host target. Defaults retain 30 daily and 12 monthly
successful backups (v1 also keeps every backup from the last seven days; see
the backup runbook's
[retention](../../../guides/stewardship-backup-runbook.md#retention)). Failure to complete a successful backup within 24 hours is
CRITICAL. Backup logs contain sizes/digests/durations, never contents/secrets.

Each data-backup manifest records its encryption-key ID. Rotation stages a new
data-backup key, successfully escrows and verifies the updated credential set,
then activates the key for new backups. Every old decryption key remains in
verified off-host escrow until all backups using it expire or are re-encrypted;
retirement is blocked otherwise. The data-backup key never encrypts its own
secret-escrow bundle.

Backup creation uses PostgreSQL-supported consistency; copying a live data
directory is prohibited. A manifest has application version, schema migration,
database-snapshot instant, files, and cryptographic digests. Partial uploads
never appear as successful backup references. In v1 the manifest records the
application version, the provisioning record's image reference and the
applied `(app, name)` migration set, read in the backup's own session under
the shared startup lease
([#608](https://github.com/epiphany40223/parishkit/issues/608)).

The purge web action can enqueue this service only for an Admin-owned
PurgeRequest that has reached quiescence. The web process never receives backup
credentials or performs the backup inline; `backup-worker` reads the existing
credential reference. Purge-triggered backups follow ordinary
retention and are additionally referenced immutably by the PurgeRequest.
The same isolated service supports guarded revalidation of the selected backup
without creating a new backup or resetting retention, as defined by the
[purge workflow](../admin-portal/spec.md#campaign-purge). Verification failures
are recorded durably with sanitized diagnostics; no backup credentials or
decrypted contents are returned to the web process.

### Dedicated off-site uploader (planned, post-launch)

Planned by [#235](https://github.com/epiphany40223/parishkit/issues/235);
not yet implemented. It removes the v1 exception above: the backup profile,
which reads all database data and the whole credentials tree, loses its
network route out again, and a separate one-shot `backup-uploader` profile
copies finished sets to Google Drive. The copy's behavior does not change:
ciphertext and manifest only, the same catch-up window, budgets, retries,
deadline and Drive retention, no work-order lock or long transaction during
an upload, the same outcome rows, the same `backup_offsite_failed` alert and
the same Admin status (see the
[runbook](../../../guides/stewardship-backup-runbook.md#off-site-copies-to-google-drive)).
It builds on the [database connection sources](#database-connection-sources)
(#2111), which are in force, and on the queued **Test access** re-check
(#269), whose definer it reuses.

What it does not change: who can read the Google Workspace key, and with it
the domain-wide delegation of the full `drive` scope. The backup worker
keeps reading it after the split, because it archives the whole credentials
tree, which holds the key; what it loses, in the contract release, is its
route out, so it can no longer *use* the key. `mail-dispatch` and the Google
Workspace credential installer also mount the key, and both have egress.
The split therefore narrows which container can combine the key with the
database and the plaintext credentials *and* reach Google, not which
containers can read it. Narrowing the key itself is the separate Drive-only
identity under **Drive scope** below.

What it stops, with the source rules: the backup worker still reads every
login's password, the cluster superuser's included, because it archives the
credentials tree, but PostgreSQL accepts each login only from its own
service's source, so from the backup worker's own network those passwords
are useless; it also cannot reach Valkey. What it keeps until this change is
its own login's authority and its route out. The split removes the route
out, proved on the real Docker Engine rather than assumed (**No route out**
below). The contract release also takes away two things from the backup's
own login: its INSERT on the off-site outcome rows, and its say over a run
row's completion time. After it, a compromised backup image cannot send
anything out itself. It cannot record a set as copied, and so silence the
off-site alert. And a set it stages cannot take the place of a copy already
on Drive (**Handoff** below).

What it still does not stop: the backup image acts as its own login. It
writes `backups/` and the backup run rows, so it can stage a *new*
directory, plaintext included, that the uploader copies into the parish's
own folder as the newest set. Later, Drive retention keeps such sets in
place of real ones, exactly as it would keep the image's own bad backups:
recovering from a compromised backup image means restoring a set from before
the compromise. Retention keeps those sets (the daily and monthly anchors),
and a set retention did trash stays in Drive's trash for 30 days, from which
an Administrator can restore it. The rest is listed under
[what source rules cannot stop](#what-source-rules-cannot-stop).

- **Networks and source.** `backup-uploader` is a one-shot profile, the
  sixth entry of `runtime_network.OFFLINE_PROFILES`. Appending keeps every
  earlier profile's slice, and a /25 `offline` block holds eight. It
  therefore gets its own internal /28 slice of `runtime_network.offline` (by
  default `172.29.242.80/28`), with PostgreSQL at offset 2 and its
  containers in the upper /29 (by default `172.29.242.88/29`), and it also
  joins `application-egress`, like every other provider caller
  (`runtime_topology._join_egress`). It never joins `backend`: it needs no
  Valkey and no other service. The source rules gain one line,
  `host "<postgres.name>" pk_stewardship_backup_uploader <upper /29>
  scram-sha-256`, rendered by `runtime_hba.source_rules` from the same
  topology as every other line; the renderer refuses an inventory login
  without a source, so the login cannot ship without its rule. The backup
  worker keeps its own network and its rule unchanged; in the contract
  release it leaves only `application-egress`. Restricting the uploader's
  egress to Google's API hosts needs an egress proxy or host firewall rules
  and stays later hardening, tracked on its own. The service's Compose
  `command` is `database-sources --config <its document>` in E1, since
  E1 has no copy to run and the upgrade's source loop runs each one-shot
  profile's `<command> --config <document>` form; E2 changes it to
  `upload`.
- **No route out.** The contract release's claim rests on Docker's
  `internal` networks, which this design does not take on faith. Three
  routes are checked: direct TCP, DNS through Docker's embedded resolver
  (`127.0.0.11`, which `dockerd` answers and may forward from the host's
  own network, so no container firewall rule sees it), and the network's
  gateway address, which belongs to the host, so any host service
  listening there could relay.
  The contract release adds `egress-probe` to the backup profile. It reads
  no file and connects to nothing but what is listed below, each with a
  five-second limit:
  - TCP 443 to two fixed public addresses, and to a Google API address the
    host resolved and passed in;
  - a DNS query for a public name through the container's resolver, and one
    sent straight to a public resolver;
  - TCP and UDP to the gateway, on every port the host listens on at a
    wildcard address or at that gateway (the script passes `ss -Htlnu`'s
    list).

  Each TCP attempt and DNS query must be refused or go unanswered. UDP is
  weaker evidence: it has no handshake, so a host service can receive a
  datagram (a syslog listener, say) and never answer, and "unanswered"
  then proves nothing. So a UDP port the host listens on at a wildcard
  address or the gateway passes only when the host's `INPUT` drop below is
  in place, which the script checks on the host (`iptables -C`); the probe's
  UDP sends are then a check that the rule works, not the proof. The probe
  prints the Docker Engine version with its answers. It runs in three
  places:
  1. the operational Compose job on real Docker (the CI evidence);
  2. the contract upgrade's preflight on Production, in a container of the
     new image joined only to the existing `backup-worker` network, before
     anything stops;
  3. every later upgrade's preflight, so an Engine upgrade that changes
     this behavior is caught.

  A probe that reaches anything refuses the upgrade before anything stops,
  and names what answered. The remedies depend on the route:
  - *Gateway listeners: the `INPUT` drop is effectively required on
    Production.* The gateway address belongs to the host, so any host
    service listening on every address answers there. On Production that
    always includes Caddy's published ports: Docker publishes `80` and
    `443` by listening on the host's wildcard address (its default
    `docker-proxy`), so the probe reaches them through the gateway and
    refuses. (Caddy only proxies to `web`, but that is still a path out
    to a service with egress.) `sshd` usually listens there too. So on
    Production this is not a contingency: the contract release's upgrade
    cannot pass without a persisted host `INPUT` drop for the `offline`
    block (traffic from it to any host address), and the runbook installs
    it before the contract upgrade (decision 17). That rule does not touch
    PostgreSQL's traffic, which stays on the bridge between containers,
    and does not touch Caddy's public traffic, which does not come from
    the `offline` block. The backup runbook gets the rule, with how to keep
    it across reboots, beside the drill's `DOCKER-USER` rule.
  - *DNS through the embedded resolver.* `dockerd` answers `127.0.0.11`
    itself. When the host's own resolver is on a loopback address (as
    `systemd-resolved`'s `127.0.0.53` is on Ubuntu), it forwards a query
    it cannot answer from the host's network namespace, outside any host
    rule. For internal networks that forwarding was a vulnerability,
    CVE-2024-29018 (GHSA-mq39-4gv4-mvpx), fixed in Moby 26.0.0 (and
    backported to 25.0.5 and 23.0.11): a container attached *only* to
    internal networks no longer has its queries forwarded. The backup
    worker after contract is on its internal network alone, so the floor
    is an Engine with that fix, and the probe is what shows the installed
    one behaves so. (Checked against that advisory, 2026-10-28; the
    advisory's own workaround, a per-service `dns:` address, which makes
    the container's lookups go from its own namespace and so fail, is the
    fallback for an Engine that cannot be upgraded at once.)

  The contract's evidence records the CI Engine version and Production's.
- **Mounts.** The uploader has its own closed inventory, admitted from
  kernel mount evidence at startup as `backup_boundaries` does for the
  backup profile: a read-only root filesystem; its service configuration
  document and its database password file (read-only); the startup
  interlock (held shared only as **Scheduling** describes); the Google
  Workspace credential's read-only rotating directory, as `mail-dispatch`
  mounts it, so a key an Administrator replaces is read on the next run with
  no recreation; and the backups directory, read-only. It mounts neither the
  configuration, credentials, media or authority trees, nor `backup_data`,
  the provisioning record or any Valkey secret. The backup worker keeps its
  inventory (it archives those trees).
- **Uploader password.** Its file is an ordinary runtime login password,
  `runtime_paths.database_password("backup-uploader")` (by default under
  `credentials/database/`), overridable through `postgres.password_files`
  like every other. The backup worker can read it, as it reads every login's,
  but the source rules refuse that login from the backup worker's network,
  which is the protection every other login's password has relied on
  since #2111. (Before the source rules, the design kept this one password
  outside the archived trees under its own path with a regenerate option;
  the source rules made that unnecessary.) It is the first login an
  existing deployment gains *with a password*, so its file and the
  cluster's stored verifier can disagree in two ways no other login meets.
  First, a same-host restore of a set from before the login replaces the
  credentials tree with one that lacks the file, while the login stays in
  the cluster: roles are not in the dump. Second, the file can be lost.
  - *Verified, not trusted.* When `database-grants` creates this login it
    sets the password from the file. When it admits an existing login, it
    reads the stored SCRAM verifier from `pg_authid` (the operator is the
    superuser) and checks the file against it. The verifier is parsed
    strictly, as exactly
    `SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>`
    (a decimal iteration count from 1 to 10,000,000, so a forged count
    cannot stall the check; strict base64 for the other three parts;
    32-byte keys); anything else, an `md5` verifier or a null password
    included, is a mismatch. From the file it computes the salted password
    (PBKDF2-HMAC-SHA-256 with that salt and count), then `StoredKey`
    (SHA-256 of the HMAC of `Client Key`) and `ServerKey` (the HMAC of
    `Server Key`), and compares *both* with the stored ones in constant
    time. A mismatch, or a missing file, refuses before any grant. The
    message names the login and points to the lost-file recovery below.
    It does not connect *as* the login to check, as `database-roles` does
    for an existing login: the source rules refuse the uploader from the
    `database-provision` network.
  - *Checked before an upgrade stops anything.* `database-grants` runs in
    upgrade step 4, after the full stop, so its refusal alone would leave a
    stopped deployment. So the upgrade script gains a read-only preflight,
    with its other checks and before step 1 stops anything. As the
    operator, in a read-only session through `docker compose exec postgres
    psql` (as the digest-retirement check already asks the database), it
    reads `rolpassword` for `pk_stewardship_backup_uploader` from
    `pg_authid`. No row: nothing to check (the expand upgrade creates the
    login). A row: the uploader's password file must exist, and the new
    image's `uploader-password-check` command must answer that it
    matches. That command is new in E1. It reads the uploader's password
    file and the stored verifier on standard input, applies the same
    parse and comparison as above, and prints only `match` or `mismatch`,
    never the file or the verifier. The script runs it in its isolated
    container (no network), with the operator's deployment YAML and the
    runtime root mounted read-only, as it runs the upgrade check, so the
    command finds the file wherever `postgres.password_files` puts it
    inside the runtime root. A file outside the root is not mounted, so
    the check fails and the upgrade refuses before anything stops (the
    backup runbook says so).
    A missing file or any other answer refuses with the lost-file
    recovery's name.
    The query's own failure refuses too: a failed `pg_authid` query (the
    service is down, the session is refused, the output is not a single
    row or an empty result) is never read as "no row", so the script does
    not copy the digest-retirement check's `2>&1 || true` pattern here.
    It tells "no row" from failure by the query's exit status and by
    selecting the row's existence explicitly (`count(*)`, one line), and
    anything else refuses before step 1.
    An image without the uploader (a rollback past the expand release) has
    no use for the login, so the check is skipped there, as the left-over
    login is under **Rollback**. The script knows whether the *new* image
    has the uploader from the command-list probe that E1 adds to the
    upgrade script (see **Scripted upgrades**, *The command probe*): one
    `--help` capture and `has_command()` per image, run against both the
    new image and the running one, so E2 reuses it rather than adding its
    own. The uploader is present when the image lists
    `uploader-password-check`, the comparison command above, which no
    release before E1 has. `smoke` cannot be the marker: it has been a
    command since v1.0.0, so every image lists it. A probe that fails
    refuses before anything stops.
  - *Same-host restore: keep the host's file.* In step 4, when the set's
    credentials tree has no uploader password file and the tree it just
    moved aside has one, `pk-restore` copies that one file into the
    restored tree (same owner, `0600`, `O_NOFOLLOW`, a regular file), logs
    that it did, and changes nothing else. Root does the copy and both
    trees came out of a set's tar, which a compromised backup image could
    have written, so no link is followed anywhere on the way: a linked
    tree or `database/` directory, in the source or the restored tree,
    refuses, as does an existing or linked temporary name
    (`backup-uploader.copying`), and the ownership change does not follow
    links (`chown -h`). Only the default path
    (`credentials/database/backup-uploader`) is kept: for an image with
    the uploader, a rendering or deployment YAML whose
    `postgres.password_files` moves the file refuses before step 1, and
    such a deployment is restored by hand. Otherwise step 7's
    `database-grants` would refuse with the services down. That file is the one the
    cluster's login was created from, so it matches. Dropping the login
    instead would also work (`database-grants` then recreates it from a new
    file), but it would make `pk-restore` the first script that drops a
    cluster role. It would also leave a restore that stops between the drop
    and the grants with a deployment missing a login. Copying back one file
    is reversible and needs nothing new. A set with its own file needs
    nothing: the password never changes, except after the recovery below.
    In that case the next `database-grants` refuses the mismatch, and the
    recovery applies. A resumed run (`--from 4`) moves aside the tree the
    failed run put in place, which may lack the file if that run stopped
    between the move and the copy. So when the tree just moved aside has
    no uploader file, `pk-restore` takes it from the newest other
    `credentials.pre-restore-*` tree that has one, and logs which. That is
    the failed run's moved-aside tree, the host's own before the restore.
    A wrong pick (an older restore's tree, after a lost-file recovery
    since) cannot do harm: the verifier check refuses it in
    `database-grants`, and the lost-file recovery applies. No tree with a
    file means the host never had one; the login, if the cluster has it,
    is then the lost-file case.
  - *Replacement and drill restores.* A new host has no login and no
    moved-aside tree. `pk-restore --replacement` (and `--drill`) runs
    `database-roles` in step 5, before step 7's retarget, and
    `provision_roles` reads every identity's password file before it
    changes anything (the `database-provision` container also mounts each
    file by itself, read-only, and Docker refuses a missing source). But
    step 5 runs from the *set's own* rendered Compose file (`pk-restore`'s
    `dc`), so its `database-roles` is the set's image. For a set from
    before the uploader that image has no uploader identity: it neither
    reads the file nor creates the login, and step 5 succeeds. The file
    and the login therefore come after step 7, not before step 5: step
    7's retarget with the new image creates the absent file
    (`RETARGET_CREATES`), and `pk-restore` then runs `database-grants`
    (below), which creates the login from it. A set from after the
    uploader carries its file in its credentials tree, and its own image's
    `database-roles` creates the login in step 5. Creating the file before
    step 5 instead (a create-only retarget mode) was considered and is not
    needed while step 5 runs the set's image; if a later change made step
    5 run the target image, it would be. E1's tests pin this: a
    replacement and a drill restore of a pre-uploader set with the E1
    image end with the login present, its file matching the verifier, and
    `database-sources` from the `backup-uploader` profile matching the
    rendered rules. That pair, not `smoke`, proves the login after a
    restore: plain `smoke` never touches the database.
  - *Lost-file recovery* (backup runbook, new subsection in E1).
    `retarget-image` and `database-grants` are offline commands, and this
    `database-grants` keeps the recent-backup rule (**Restore admission**
    applies only with `--after-restore`). So first confirm that a backup
    completed within `REQUIRED_WITHIN` (24 hours), or take one. Comment
    out the uploader's and the backup's cron lines, then stop every online
    service but caddy, postgres and valkey, as upgrade steps 1 and 2 do
    (the background services, then web; caddy keeps the maintenance
    page), since every running service holds the startup lock shared. As
    the operator, through the socket, run
    `DROP OWNED BY pk_stewardship_backup_uploader;` and
    `DROP ROLE pk_stewardship_backup_uploader;`. The login owns nothing (its
    grant admission refuses ownership), so `DROP OWNED` only revokes its
    grants in this database and its `CONNECT` on it; its outcome rows stay.
    Move any stale file aside. Then run `retarget-image` with the running
    image, which creates the absent file (**Deploying** step 1), and
    `database-grants`, which creates the login from it. Then prove the
    login: `database-sources` from the `backup-uploader` profile (its
    source rule) and `uploader-password-check` against the stored
    verifier (its password). Then start the services again and put the
    cron lines back. Nothing else changes, and no other login's password
    is touched.
- **Handoff.** A set is finished when its `manifest.json` exists; the backup
  writes it last, after `fsync`. The uploader considers only finished sets
  whose manifest digest has a `stewardship_backup_run` row, and it still
  checks each file against the manifest's SHA-256 before trusting the copy.
  That row check guards against stray directories on the host (a hand copy,
  a half-restored set), not against a compromised backup image: the backup
  login writes both `backups/` and the run rows. Three more rules bound what
  such an image can do to the copies already on Drive:
  - *Names bound to the database's clock.* The set's directory name must be
    its run row's `started_at` (to the second, UTC). The row's
    `completed_at` may be at most `REQUIRED_WITHIN` (24 hours) after
    `started_at`, and the table's check already keeps it from being
    earlier. `completed_at` is the column's default,
    `STATEMENT_TIMESTAMP()` (Django's `Now()` renders as that on
    PostgreSQL, and `schema/backup.sql` shows it): the start of the
    inserting *statement*. It is deliberately not `now()` or
    `CURRENT_TIMESTAMP`, the start of the *transaction*, which an image
    could push back as far as it likes by opening a transaction early and
    inserting later. A statement can still be held open (a sleeping
    `SELECT` feeding the `INSERT`), so the bound is not exact; what that
    buys is limited by the next rule, since a set must also be newer than
    the newest copy.
    From the contract release on, the backup login's INSERT on
    `stewardship_backup_run` is a column grant that leaves out
    `completed_at`, so it cannot choose that time. A set named for a past
    day, or a future one, is therefore refused. Today's insert,
    `BackupRun.objects.create(**facts)`, would break under that grant:
    Django 5.2 names every concrete field in the `INSERT`, writing
    `DEFAULT` for a `db_default` one, and PostgreSQL requires INSERT
    privilege on every column the statement names, `DEFAULT` or not. So
    the contract PR changes the backup's `record` to a fixed SQL `INSERT`
    that names the other seven columns and not `completed_at`, with no
    `RETURNING`, and adds a PostgreSQL test that, connected as the real
    backup login with the contract's grants, records a run (its
    `completed_at` set by the default) and that the old `create()` is
    refused with `42501`. The insert ships in the same release as the
    revoke; the step-1 backup of the contract upgrade runs the previous
    image while its table grant is still in force.
    Several rows can share one `manifest_digest` (the column is not
    unique). A set is admitted when *at least one* row with its digest
    binds its name this way; rows that do not are ignored. A row that
    binds is written only by an insert whose `completed_at` the database
    chose, so extra rows can neither admit a set nor hold a real one back.
  - *Never older than the newest copy.* A set whose name is not newer than
    the newest set with an `uploaded` row for this folder is not pending:
    `pending_sets()` leaves it out, as it leaves out a set already copied.
    It is not a refusal and records nothing. A `verification` row for it
    would repeat every five minutes, become the newest outcome
    (`_newest_state()`, which the off-site alert and the pages read) and
    take the interlock on every cron run, though the newest set is
    copied. So such a set is silent: no row, no interlock, and an idle
    cron run still exits `0` with no output. A staged set therefore cannot
    become an earlier day's or month's retention anchor. It also cannot
    reuse a copied set's name, which would make `upload_set` trash the
    good copy and write the impostor in its place. The cost: an older set
    whose copy failed while a newer one then copied is not retried. The
    newest set is the one a restore needs, the old set stays on the host,
    and its `failed` row stays in the history. `verification` refusals
    remain only for the name-binding rule above and the regular-file
    rule below, which mean the set itself is wrong.
    Those refusals are not silent while the set is the newest: each cron
    pass logs the refusal and takes the startup lock, every five minutes,
    until a newer set copies (at most 12 hours), after which the refused
    set is no longer pending and drops out without a word. That is intended: the set is genuinely wrong
    (a hash mismatch counts too), and the repeated refusal is how the
    operator learns of it.
  - *A clock that jumped.* "The newest copy" counts only `uploaded` rows
    whose set name is not more than `CLOCK_SKEW` after the database's
    current time (the margin `backup.retention_paused` uses). A host
    clock that jumped forward and back can leave a copy on record named in
    the future; without this, every later set would be "not newer" and
    silently skipped until that time came. Ignoring such a copy is safe,
    because no *new* row can carry a future name: `started_at` may not be
    later than `completed_at`, which is the database's own time. An
    uploader pass that ignores one logs a WARNING,
    `backup_offsite_future_copy`, naming the set (only on a pass that has
    sets pending, so an idle cron run stays silent). While that folder is
    on Drive, the existing clock guard (`backup.retention_paused`) skips
    Drive pruning and logs its own warning, so nothing is thinned on a
    wrong clock; the backup runbook says to trash that folder (and the
    local set) by hand once a correct set has copied, which lets pruning
    resume.
  - *Regular files only.* Each set file is opened `O_RDONLY|O_NOFOLLOW` and
    must be a regular file by `fstat` on that descriptor. The same
    descriptor is hashed and uploaded, so a symlink planted in a set (to the
    uploader's own key, say, with a matching SHA-256 in the manifest) is
    never followed. The manifest is read through the same descriptor up to
    a fixed cap (64 KiB, far above a real one) before it is parsed. A set
    that breaks this rule or the name binding is refused as
    `verification`, like a mismatched one.

  None of this stops a compromised image from staging a *new* set, named
  for now and recorded in a run row. That set is uploaded as the newest
  one (see what it still does not stop, above).
- **Locking.** The per-host copy lock becomes an exclusive `flock` on a
  descriptor of the backups directory itself, opened
  `O_RDONLY|O_DIRECTORY|O_NOFOLLOW` (today it is the `.offsite-copy.lock`
  file, which a read-only mount cannot create), so the uploader writes
  nothing on the host; `flock` works on a read-only bind mount and across
  containers, since they share the host inode. The uploader takes it before
  it connects to the database and before the startup interlock.
  - *Expand transition.* An old-release copy can still be running after the
    upgrade (a cron backup that was uploading at retarget, or one created on
    the old image that waited out the offline interlock) and holds only the
    file lock. So in the expand release both copiers take the legacy file
    lock first, when the file exists (the uploader opens it read-only; the
    backup profile creates it as today), and then the directory lock, always
    in that order. The contract release drops the file lock; a leftover
    `.offsite-copy.lock` file is harmless, since retention looks only at
    directories.
  - *Retention.* Local retention in the backup profile takes the directory
    lock without waiting and, while a copy holds it, skips pruning for that
    run, so it can never remove a set mid-upload. A skip is an INFO line,
    `backup_retention_paused_copy`, since it is routine and the next backup
    prunes. When four or more complete sets are already past the retention
    rule's prune point (two days of skipped prunes at two backups a day),
    the line is a WARNING instead, so a stuck lock shows in the cron output
    before the disk fills; a Drive outage behind it already raises
    `backup_offsite_failed`.
  - *Offline work.* The scripted upgrade (after step 1), restore and drill
    hold the copy lock from the host for their whole offline phase, and the
    manual restore and drill procedures take it in their first step (or
    confirm that no `backup-uploader` container is running). An upload
    already running is therefore finished, or the procedure refuses, before
    a restore starts, so a restored database never receives a later
    upload's outcome rows, and a cron uploader exits at the lock without
    reaching the interlock. In the expand release the host takes *both*
    locks, in the copiers' order: the legacy file lock (creating the file,
    which the host may do) and then the directory lock. An old-release copy
    still running holds only the file lock, so taking only the directory
    lock would not wait for it. From the contract release on, the host takes
    the directory lock alone. The host acquires each lock with
    `flock -w SECONDS`: 60 seconds for restore and drill, and the step-1
    wait (below) for the upgrade. An expiry refuses with "an off-site copy
    is still running" before anything stops, and logs a durable timeout
    line with what it waited for, the limit and the elapsed time, as every
    other host-side limit does.
  - *Stopping a running uploader.* When a restore must start and the copy
    lock stays held, the backup runbook's procedure is: comment out the
    uploader's cron line (the restore disables it anyway); find the
    container with `docker ps --filter
    label=com.docker.compose.service=backup-uploader`; wait for it to exit,
    or stop that exact container by name with `docker stop` (never by
    pattern). A stopped upload records no outcome row and leaves at most
    an incomplete Drive folder, which the next copy of that set trashes
    and writes again (`upload_set`), and which Drive pruning trashes if
    the set is never copied. Then run the restore, which takes the lock.
- **Scheduling.** The uploader runs from the host's cron every five
  minutes, beside request mode, and follows request mode's idle pattern
  (see [Take a backup now](../admin-portal/spec.md#take-a-backup-now)):
  1. It admits its mounts and takes the copy lock. A cron run that finds the
     lock held exits `0` with no output; only a run with `--wait-lock
     SECONDS` (the upgrade script, or an operator by hand) reports `busy`.
  2. It reads the destination and the pending sets, without the startup
     interlock. With nothing pending it exits `0` silently, never reads the
     key and leaves no trace. A database it cannot reach, or one not on the
     current schema, is a quiet exit too; one that refuses the login (a
     missed `database-grants`, or a source rule not yet in force) logs one
     WARNING, as request mode does.
  3. Only with sets pending does it take the interlock shared, for its
     identity admission and current-schema check; if offline work holds the
     interlock it exits quietly. It releases the interlock before any
     upload, as today's copy runs after the backup's lease, so a long upload
     never holds offline work back.

  One schedule therefore covers scheduled backups, **Take a backup now**,
  manual runs and catch-up after an outage, and the existing
  `OFFSITE_GRACE` check (six hours without an outcome for the newest set)
  alerts if the cron line is missing. `smoke --target backup_drive` moves
  to the uploader profile in two parts. In E1 it is probe-only there
  (write and trash one small file): the uploader profile refuses
  `--send`. A `--send` copy needs the copy lock, which in the uploader is
  E2's directory lock, and it reads the set tag through the backup login's
  admission. `smoke --send` from the uploader profile arrives with E2.
- **Scripted upgrades.** Step 1 runs `backup-worker` as today. Whenever the
  *running image* (the previous one, which step 1 runs) has the uploader's
  `upload` command, and the topology the host runs has a
  `backup-uploader` service, it then runs the
  uploader with `--wait-lock 1800 --require-digest D`, where `D` is the
  step-1 set's manifest digest (or the reused backup's, on a resumed users
  upgrade), whatever the backup's own line said: on an expand topology the
  backup's copy can report `busy` because the uploader holds the lock.
  - *Success* is a `stewardship_backup_upload` row with state `uploaded`,
    digest `D` and the currently applied folder, whichever run wrote it (a
    cron uploader, or a copy already in flight, may have copied the set
    first and leave this run nothing to do). The uploader looks the row up
    after its own pass and reports that answer; the script reads that field,
    not the pass's own counts. LOCAL also accepts `not_configured`, as
    step 1 accepts it from the backup line today.
  - *An expired wait* (30 minutes) abandons the upgrade, as `busy` does
    today. The message says an off-site copy is still running and to try
    again later; the wait cannot cover a whole catch-up, whose budget is
    four hours. The expiry is a durable timeout entry with
    `what='lock_timeout'`, the limit and the elapsed time, as the backup
    lock's wait logs, so no allowlist changes.
  - Without both (a release before the split, or one with the login and
    profile but not yet the copy), step 1 keeps today's rule on the backup
    line's `offsite` field, so the same script upgrades from any of them.
    Keying on the command, not only on the topology, means a release cut
    between the expand PRs, or a rollback to one, never has step 1 run a
    command the image lacks.
  - *The command probe is new.* `tools/stewardship-upgrade-host.sh` has no
    command-list probe today (only `pk-restore` has one: it captures the
    image's `--help` and greps it in `has_command()`). E1 adds the same two
    lines to the upgrade script (so the password preflight can tell whether
    the new image has the uploader, by `uploader-password-check`,
    **Uploader password**), as one helper
    run against both images: the *new* image, for that preflight, and the
    *running* image (the rendered Compose file's `web` image, which the
    script already reads as `previous`). Step 1 runs the running image's
    uploader, so for step 1 that is the image whose `upload` command
    matters; E2 only adds the `upload` test on the probe's result. A probe
    that fails (the image is gone, or `--help` does not answer within its
    limit) refuses before anything stops.
  - *Full stop.* The host holds the copy lock from the end of step 1, so a
    cron uploader that starts during a later full stop exits at the lock
    within seconds. If the full stop's one-off check sees it running, the
    run refuses with `One-off containers of stewardship are still running
    (backup-uploader)` before it stops anything and is repeated, exactly as
    for a cron backup today; the shared full-stop block does not change.
  - The upgrade's source check (step 6) runs `database-sources` in every
    one-shot profile, so it covers the uploader with no new step.
- **Database login.** A new `pk_stewardship_backup_uploader` login, created
  like every other runtime login (NOINHERIT, NOBYPASSRLS, no memberships, no
  `pg_read_all_data`, the operator-connection limit the backup login has).
  Its registry in `runtime_grants`:
  - SELECT and INSERT on `stewardship_backup_upload` (its outcome rows; the
    table's guard already admits INSERT only);
  - SELECT on `stewardship_backup_run (manifest_digest, started_at,
    completed_at)`, for the handoff's row and name checks;
  - SELECT on `stewardship_system_configuration (id)`, the set tag's source
    (`backup_offsite.set_tag`), and nothing else of that table;
  - INSERT on `stewardship_operational_log`, for timeout events only (the
    writer trigger's existing timeout clause gains this login);
  - EXECUTE on `stewardship_backup_destination_v1()` and the debug-off read
    every runtime login has.

  Startup admits the login's exact attributes, grants and mounts before any
  Drive call, as the backup command admits its own; the upgrade check
  refuses the same drift. In the contract release the backup login loses
  INSERT on `stewardship_backup_upload`, and the `disabled` outcome moves to
  the uploader with the rest of the copy. Only INSERT is revoked. The backup
  login is a member of `pg_read_all_data`, so it reads every table whatever
  its own grants say. Dropping the table SELECT from its registry entry
  would only tidy the registry, not narrow anything, so the contract
  leaves it alone. Its table INSERT on `stewardship_backup_run` also
  becomes a column INSERT on every column except `completed_at` (`id`,
  `started_at`, the two sizes, `manifest_digest`, `recipient_fingerprint`,
  `application_version`), so the database alone sets the completion time
  (**Handoff**, which also says why the backup's insert must stop naming
  `completed_at` in the same PR).
- **Destination function.** Today `destination()` and `set_tag()` read the
  whole canonical configuration document and the runtime row through
  `pg_read_all_data`. The re-check for
  [#269](https://github.com/epiphany40223/parishkit/issues/269) already
  added `stewardship_backup_destination_v1()` in its frozen migration 0176:
  a stable SECURITY DEFINER with a fixed `search_path` and no PUBLIC
  EXECUTE that returns the applied backup folder link (`backup_target`) and
  delegated mailbox (`delegated_email`), each null when not configured. Its
  body refuses every session login except the Google Workspace installer's
  and members of the schema owner, so the uploader cannot call it yet. The
  uploader's migration (0193, below) replaces it with the same signature
  and output columns, so `CREATE OR REPLACE` is allowed, and a caller check
  that also admits `pk_stewardship_backup_uploader`. The runtime ID is not
  added as an output column, which would change the return type and so need
  a drop or a new version; the column grant above gives the uploader that
  one value. The uploader parses the link as today, so an unparsable link is
  still a configuration failure, not "off".
- **Schema.** Two forward migrations with frozen SQL files under the
  [post-launch schema policy](#post-launch-schema-policy):
  - *0193, in the preparation release*, inert until the login exists (a
    function body may name a role that does not exist yet). It replaces
    `stewardship_backup_destination_v1()` as above, states
    `ALTER FUNCTION ... SECURITY DEFINER` and `REVOKE ALL ... FROM PUBLIC`
    again (the repository's rule for every replaced definer), and replaces
    `stewardship_operational_log_writer_v1()` with the uploader added to the
    timeout clause (`CREATE OR REPLACE` with no `ALTER`: the writer is not
    `SECURITY DEFINER`, and must not become one). Both get the same final
    text in their baseline files. The closing `DO` block raises unless the
    destination function is still `prosecdef`, `provolatile` `s`, with the
    pinned `search_path` and no PUBLIC EXECUTE; unless the log writer is
    still *not* `prosecdef`; and unless both bodies name the uploader. It
    is tested against the 0176 definitions and the new ones.
  - *The contract migration* (number assigned when it is scheduled), in the
    contract release: the backup login's `REVOKE INSERT` on
    `stewardship_backup_upload` and on `stewardship_backup_run` (the table
    grant). `database-grants` then grants the run table's column INSERT
    from the registry; the backup's own insert no longer names
    `completed_at` (**Handoff**). Until the migration applies, the backup
    login's admission tolerates exactly those two old grants through
    `CLOSURE_REVOKES`. Today that tolerance holds column grants only, and
    the backup login's admission (`_admit_reader`) does not consult it. The
    contract PR extends both, for table privileges and for that login.

  The new login, its grants and its source rule need no migration: the
  migration login cannot create roles, and grants come from the registry.
- **Drive scope.** `drive.file` stays rejected. It grants access only to
  files the app created or a user opened with it through the Google Picker,
  and a server-side service account cannot use the Picker, so the uploader
  could not write into the folder an Administrator chose, nor could **Test
  access** check it. Making it work would mean the application creates and
  owns its own backup folder, a different setup flow and a move of the
  existing sets; it would also still be delegated, so a leaked key would
  reach every user's app-created files. The narrower identity worth having
  is a dedicated, Drive-only service account *without* domain-wide
  delegation, made a member of the shared drive that holds the backup
  folder: a leaked uploader key then reaches only that shared drive, and
  the Workspace key drops the `drive` scope. That needs Google Admin work by
  the Administrator, a new credential and installer target, and its own
  review, so it is a separate follow-up, not part of this change.
- **Deploying a new login on an existing host.** It follows the
  [inspection login](#database-inspection-login)'s path (#2148), plus a
  password file:
  1. `retarget-image` today refuses a missing once-generated password. It
     gains one exception, for a closed constant list of identities,
     `RETARGET_CREATES = {"backup-uploader"}`, not for whatever identity a
     release adds. For an identity on that list that the running release
     also has (`runtime_identities.database_identities`, so the preparation
     release, which lacks it, does nothing), it creates the password file when, and
     only when, the file is absent (`lstat`: a dangling link or any other
     entry is not absent and still refuses). The file is generated as
     provisioning does, written create-only (`O_CREAT|O_EXCL|O_NOFOLLOW`)
     and owner-only under the offline interlock, never replaces anything,
     and is named in the result. The absence check comes first in
     `retarget_image`, before the loop that reads every password file
     (which would refuse the missing one) and before the early return for
     "nothing stale, same image" (`runtime_retarget.py`, the `if not stale
     and not changed` return): a file to create counts as a change, so a
     retarget to the image already recorded, which is what the lost-file
     recovery runs, still takes the interlock and creates it instead of
     reporting no change. Every other identity's missing file still
     refuses, as does every other refusal. A later login that needs the
     same path is added to the list by its own reviewed change.
  2. The upgrade check requires the login and exactly its grants, so the
     first upgrade that carries it answers `f` and runs as a schema change
     (`STEWARDSHIP_SCHEMA_CHANGE=1`). The expand release's check looks the
     login up in `pg_roles` before any privilege function names it, so a
     missing login answers `f` rather than failing with an error. The
     script never infers a missing login from error text. Where its message
     names that cause, it matches the exact psql line
     (`ERROR:  role "pk_stewardship_backup_uploader" does not exist`),
     never a substring such as `does not exist`, which other failures share.
  3. Step 4's `database-grants` creates the login where it is missing, with
     that password, and grants it, as it creates the inspection login. It
     does so before the per-identity loop that today refuses a missing role
     ("Database roles must be provisioned before grants"). An existing
     login's password is checked against its stored verifier (**Uploader
     password**). `database-roles` creates the login too, on a fresh
     install, or on a replacement or drill host restoring a set that has
     the uploader (a pre-uploader set gets it from step 7's
     `database-grants`, **Uploader password**). An existing login that
     differs is refused, never repaired, as for every login.
  4. The same release adds a network PostgreSQL must join, so the upgrade's
     existing comparison after step 3 finds PostgreSQL's networks changed
     and runs the full stop (the deployment runbook's
     [database connection sources](../../../guides/stewardship-deployment-runbook.md#database-connection-sources-2111)
     says what it prints), then the reload, the hba check and, in step 6,
     the source check. Later upgrades find nothing to change.
     Docker refuses to create a network whose subnet overlaps another one,
     and it would refuse only at the full stop's `up`, after PostgreSQL has
     stopped. So the expand release's preflight, before anything stops,
     compares the uploader's new `/28` with every existing Docker network's
     subnets (other than the project's own offline networks, which the
     renderer already keeps apart) and with the host's routes. Any overlap
     refuses, naming the conflicting network or route, and its remedy is to
     move or remove that network or route. It does not suggest changing
     `runtime_network.offline`: on an existing deployment `retarget-image`
     refuses any change to the deployment inputs (only the image may
     change), and moving the block would move every one-shot network,
     PostgreSQL's addresses and the hba rules. The preflight runs before
     the retarget,
     so it computes the `/28` itself: slice index 5 of the rendered
     `runtime_network.offline` (`DEFAULT_OFFLINE` when the record does not
     set it), and a test ties that index to the uploader's position in
     `OFFLINE_PROFILES`.

  A same-host restore of a set from before the login leaves the login (a
  cluster role, not in the dump) but without grants, as for the inspection
  login, until `database-grants` runs. The expand release makes `pk-restore`
  keep the host's password file for such a set (**Uploader password**).
  In every mode, when the image has the uploader identity, step 7 then
  runs `database-grants --after-restore SET` (`SET` the set's name) from
  the retargeted rendering, after the retarget, the full stop and the hba
  reload and before `restore-begin`: it creates a missing login from the
  file (replacement and drill) or checks an existing one against its
  verifier (same host).
  `pk-restore` detects "the image has the uploader identity" with its
  existing `has_command()` on the image's `--help` list, testing for
  `uploader-password-check` (the upgrade script's marker), and E1 adds
  `database-grants` to the `needed` command list for exactly those images
  (an older image has the command anyway, but the list is where a missing
  one is refused before anything changes). An image without that command
  skips the grants step. For the same images, step 8's source check also
  runs `database-sources` from the `backup-uploader` profile, beside web
  and admin-recovery: with step 7's verifier check, that is the restore's
  proof of the login. On a drill host that one-off joins
  `application-egress` and mounts the Workspace key like any uploader
  run, which is acceptable there: the runbook's `DOCKER-USER` rule blocks
  every container's egress before a drill, and the one-off runs only
  `database-sources`, which connects to nothing but PostgreSQL.
  `pk-restore`'s step 7 already does the full stop for a set rendered on
  the old networks.

  *Restore admission.* On a configured database `database-grants`
  admits the operator only behind a backup row completed within
  `REQUIRED_WITHIN` (24 hours, `backup.require_recent_backup`). A
  restored database never holds its own set's row (the dump was taken
  before the row was written), so its newest row is the backup before
  the set, and that rule would refuse most restores at step 7 with the
  services down. With `--after-restore SET`, and only then, the rule is
  replaced by evidence that the set is loaded and the site has not been
  reopened since: the public schema's comment is exactly
  `pk-restore:SET:<64 lowercase hex>` for that `SET` (written last in
  step 6's transaction, the evidence `pk-restore`'s own `set_loaded`
  trusts), no `stewardship_backup_run` row started at or after the set's
  start, and no released restore review of that set. The last uses the
  review's existing records, not a new marker: `restore-begin` writes a
  `restore_begin` runtime transition with the set's time as `backup_at`,
  and releasing the review writes `restore_release` with the same
  `backup_at`. A `restore_release` row for `SET` means the site was
  reopened and has taken live writes; without that check, a backup
  schedule that keeps failing would leave the waiver open for as long as
  no backup succeeds. An open review (`restore_begin` only) still
  passes, so `pk-restore --from 7` keeps working. `SET` must be a set
  name (`YYYYMMDDTHHMMSSZ` in ASCII digits, a real UTC time). Anything
  else refuses before any grant: another set's marker, no marker, a
  later backup row (the marker alone survives into later backups and
  restores), a released review of the set, or a missing run or
  transition table, whether or not the database is configured (every set
  has both). The flag is not a way around the
  rule: it never relaxes anything when the restore marker is absent, so
  an ordinary upgrade's `database-grants`, which never passes it, keeps
  the recent-backup rule. Everything else in `database-grants` is
  unchanged (the operator, provisioning marker and writer-guard checks,
  every login's check, the grants and their excess-grant refusals). An
  admission by this path is logged as a WARNING process-log line with the
  failure category `upgrade_backup_waived_after_restore` (a closed word
  on an existing event, so no durable event and no migration; the process
  log admits no free text, so the line cannot name the set), and the
  command's JSON result carries `"after_restore": "SET"`, which
  `pk-restore` writes, with its own line naming the set, to its durable
  restore log. `pk-restore` passes the flag only from step 7, after `set_loaded`
  answered yes; the backup runbook's manual restore never needs it,
  since no `database-grants` step follows there.

  The checking has a runbook consequence. A same-host restore of a set from
  before a lost-file recovery brings back a credentials tree whose uploader
  file (or absence) no longer matches the cluster's login, so step 7's
  `database-grants` refuses with the services down. The restore stops at
  step 7; the operator runs the lost-file recovery's first half (the two
  `DROP`s and moving the stale file aside), then resumes with
  `pk-restore --from 7`, whose retarget creates the file and whose
  `database-grants --after-restore SET` creates the login. A by-hand
  `database-grants` would refuse there, since the restored database has
  no recent backup row. The backup runbook's restore section says so.
- **Delivery: preparation, then expand, then contract.**
  1. The preparation release ships migration 0193 and retarget's
     absent-password exception. Neither does anything on its own.
  2. The expand release ships the login, its source and network, the
     uploader profile (with its `upload` command, `--wait-lock` and
     `--require-digest`, and `smoke`), `uploader-password-check`, the
     directory lock with the transition's double lock, and upgrade step
     1's uploader run, with no migration of its own, while the backup
     profile keeps copying as today. It is two PRs (E1: login and network,
     `uploader-password-check` and a probe-only `smoke` in the uploader
     profile; E2: the copy, `upload` and `smoke --send` there). Both are
     meant for one
     train, but correctness does not depend on that: step 1 is keyed on the
     running image's `upload` command (**Scripted upgrades**), so a release
     tagged between them is safe to deploy and to roll back to. Its upgrade
     is a schema change with the one-time full stop, in a normal nightly
     window. The operator then proves the new identity on Production by
     hand (`database-sources` from the uploader profile, the probe-only
     `smoke`, then one `upload`, which reports nothing pending or copies
     the newest set), with the old path still in place, and then
     installs the uploader's cron line (an Administrator decision; default:
     during expand). Both copiers then run, serialized by the copy lock;
     whichever copies a set first records it and the other finds nothing
     pending, so the cron path is exercised on Production while the
     backup's own copy is still the fallback, and copies never stop between
     the contract deploy and a later cron change. Because the cron line can
     exist from the expand release on, that release (E2), not the contract,
     updates the crontab lists that a restore or drill disables and checks,
     in
     [Restore drill](../../../guides/stewardship-backup-runbook.md#restore-drill),
     [Restore for real](../../../guides/stewardship-backup-runbook.md#restore-for-real)
     and the
     [scripted restores](../../../guides/stewardship-backup-runbook.md#scripted-same-host-restore),
     naming the uploader line: on a drill host it alone could upload into
     the live folder and prune it. `pk-restore`'s job scan already refuses
     that line, since it names `backup-uploader` and the scan matches
     `backup`; E2 adds a test for it. The same release's rollback section
     says to remove the uploader cron line first (**Rollback**), and the
     scripted expand rollback refuses, before anything stops, while an
     uncommented uploader line is installed. That check needs the
     crontab collection on the upgrade side, so E2, not the contract,
     moves `pk-restore`'s collection (root's and `SUDO_USER`'s crontabs,
     `/etc/crontab`, `/etc/cron.d`, with its `PK_RESTORE_CRONTAB` and
     `PK_RESTORE_CRON_DIR` test overrides) into a shared block kept
     identical in `pk-restore` and `tools/stewardship-upgrade-host.sh`,
     compared by a test as the database network layout block already is
     (`tests/stewardship/test_database_layout.py`). `pk-restore`'s
     `check_jobs` keeps its own `backup|rsync|rclone` filter over the
     shared collection; the upgrade script filters it for a line that
     runs the `backup-uploader` service's `upload`.
  3. The contract release removes the copy and egress from the backup
     profile, the legacy file lock and the backup line's `offsite` field,
     adds retention's lock check and the egress probe, and carries the
     contract migration (the backup login's REVOKEs). It changes no network
     PostgreSQL is on, so it needs no full stop. On Production its upgrade
     refuses, in its preflight before anything stops, while the host has no
     uncommented uploader cron line. That preflight is the only crontab
     change the contract makes: it uses E2's shared crontab block and
     uploader-line filter, inverted (refuse when there is *no* such line).
     It also updates, by link rather than copy, every other place that
     describes the copy:
     - the backup runbook's
       [off-site section](../../../guides/stewardship-backup-runbook.md#off-site-copies-to-google-drive)
       (the backup line's `offsite` field, the cron line, the egress probe
       and its `INPUT` remedy);
     - the deployment runbook's
       [database connection sources](../../../guides/stewardship-deployment-runbook.md#database-connection-sources-2111)
       check after a rollout, which proves the off-site copy through a
       backup run, and this spec's
       [one-shot profiles](#logins-and-their-sources) paragraph, which says
       the backup worker keeps `application-egress` (the expand release adds
       the uploader's row to the sources table);
     - the service list in the architecture spec's
       [component model](../architecture/spec.md#technology-and-component-model),
       where `backup-worker` holds "backup-target credentials" and
       `backup-uploader` is missing;
     - the Admin portal spec's
       [backup key pick-up](../admin-portal/spec.md#parish-and-integration-configuration)
       ("as it already does for the off-site folder") and
       [Take a backup now](../admin-portal/spec.md#take-a-backup-now)
       ("the off-site upload that follows").
- **Ordering with the credential redesign.**
  [#144](https://github.com/epiphany40223/parishkit/issues/144) (after the
  campaign) may later change how credentials are stored and mounted,
  including the rotating Google Workspace directory the uploader reads. That
  ordering is accepted: the uploader mounts the key exactly as
  `mail-dispatch` does, so #144 rewires it with the other consumers and
  nothing here needs to wait for it.
- **Rollback.** The preparation and contract releases carry a migration, so
  an application-only rollback of either is refused (the previous release
  does not know the migration); under the fail-forward policy only a
  catastrophic failure restores the pre-upgrade backup with the previous
  release, per the
  [deployment runbook](../../../guides/stewardship-deployment-runbook.md#rollback).
  The expand release has no migration. Rolling it back starts by removing
  the uploader's cron line, if it is installed: the previous release
  renders no `backup-uploader` service, so the line would fail every five
  minutes, and the backup profile's own copy is the copier again. The
  rollback renders the previous networks, so the scripted rollback runs the
  same full stop in the other direction (PostgreSQL leaves the uploader's
  network), from the expand release's checkout as the runbook says for
  every network change. The rollback refuses, before anything stops, while
  an uncommented uploader line is in the crontabs (E2's shared crontab
  block). An application-only rollback is allowed only once the
  *previous release's own image* has been shown to run `upgrade-check`
  and `database-grants` cleanly against an expanded database (a pk-local
  or CI step pinned to the previous release's tag, part of the expand
  evidence; the expand release's own tests run only its own code). The
  inspection login suggests it will (the previous check ignored that
  login), but it must be shown; without
  that evidence, rolling it back is a restore. The left-over login has no
  rule in the previous rendering, so the final `reject` refuses it, and its
  password file is unused. A failure of the uploader alone is not
  catastrophic: in the expand release the backup profile still copies;
  after the contract release, the local sets and their records are kept,
  the alert fires and the copy catches up once the uploader is fixed
  forward.
- **Tests.** Topology, golden Compose and golden `pg_hba` files (the sixth
  one-shot network and its slice, the uploader's one rule, PostgreSQL's
  address on it, the backup worker's egress gone after contract); mount and
  identity admission, positive and each refusal; the grants registry and its
  PostgreSQL admission, including the backup login's INSERTs gone (the run
  table's INSERT then a column grant without `completed_at`) and the
  uploader holding no `pg_read_all_data` and only `id` of the runtime row;
  `database-grants` refusing a missing or mismatched uploader password file
  against the stored verifier, and accepting the right one; the verifier
  parser refusing every malformed form (wrong prefix, an out-of-range or
  non-decimal count, loose base64, short keys, `md5`, null) and a file
  that matches `StoredKey` but not `ServerKey`; `uploader-password-check`
  printing only `match` or `mismatch` (never the file or the verifier);
  the upgrade script's
  read-only verifier preflight refusing a missing or mismatched file
  before step 1, refusing a failed `pg_authid` query, and passing with no
  login; the marker (an image that lists `smoke` but not
  `uploader-password-check` counts as having no uploader); the uploader
  profile's `smoke` probing without `--send` and refusing `--send` (E1);
  `pk-restore`
  keeping the host's file for a set without one (also on `--from 4`, from
  the failed run's tree), running `database-grants --after-restore SET`
  in step 7 (on PostgreSQL: it admits a loaded set whose newest backup
  row is older than 24 hours, also while its review is open, and refuses
  another set's marker, no marker, a run row started at or after the set,
  a released review of the set, and a malformed `SET`;
  without the flag the same database is still refused), a
  replacement and a drill restore of a pre-uploader set with the new
  image ending with the login and a matching file, and the lost-file
  recovery run end to end on PostgreSQL (its retarget to the recorded
  image creating the file, not returning early);
  the backup's fixed `INSERT` as the real backup login under the
  contract's grants (the row recorded, `completed_at` from the default)
  and `BackupRun.objects.create` refused with `42501`;
  the handoff's refusals (a name that is not the row's `started_at`, a
  `completed_at` more than 24 hours later, a symlinked or non-regular set
  file, an oversized manifest), a set admitted by one binding row among
  several with its digest; and the silent skips (a name not newer than
  the newest copy, a reused copied name: not pending, no row, no
  interlock, an idle exit), with a future-named copy on record ignored
  with its WARNING so newer sets still copy;
  the frozen file's digest, chain and `DO` block (against both the 0176 and
  the new definitions, including the writer's `prosecdef` being false), the
  log-writer allowlist and the upgrade-parity test;
  retarget creating a password file only for `backup-uploader` and only
  when absent (a dangling link refuses), never for another identity;
  the upgrade script's command probe of the running image and its
  capability key (an image with the profile but no `upload` keeps today's
  step 1), its subnet-overlap preflight, the shared crontab block's two
  copies compared (E2), the contract's cron-line preflight and the expand
  rollback's, both refusing before anything stops; the host's bounded,
  logged copy-lock wait, taking both locks in the expand release;
  the egress probe on real Docker (external TCP, the embedded resolver and
  a direct DNS query, and a listener on the gateway each refused, plus a
  positive control that sees a deliberately reachable listener, a
  published port reached through the gateway without the `INPUT` drop,
  and a UDP listener refused without the drop even though it never
  answers);
  the destination function answering the uploader and the installer and
  refusing every other login and PUBLIC; the source check's uploader row,
  and on real Docker (the operational Compose job) the negative cases: the
  uploader's password refused with `28000` from `backup-worker`, and the
  backup login's from `backup-uploader`; the off-site copy tests run as the
  uploader (an unrecorded set directory is never uploaded; a held lock exits
  silently from cron and reports `busy` with `--wait-lock`, with no
  connection; an idle run never takes the interlock; the expand double lock
  excludes an old-release copier; retention skips pruning while the lock is
  held and warns past the threshold); `--require-digest` accepting another
  run's row and refusing one for another folder; the upgrade check's
  missing-login answer and `database-grants` creating the login; the
  upgrade script's step 1 (success by row, LOCAL `not_configured`, an
  expired wait abandoning) against both a pre-split and a split topology;
  and the expand rollout and rollback full stop on real Docker, as #2111's
  were proved. No test needs real Google credentials.

## Restore

In v1 the restore itself is the backup runbook's operator procedure,
[restore for real](../../../guides/stewardship-backup-runbook.md#restore-for-real):
the operator opens and checks the set and restores the database and files
by hand, so the two restore modes, their automated precondition checks and
the secret-escrow step below are not application commands, and the
restricted maintenance queues are named and consumed (`jobs/queues.py`), but
nothing routes work to them yet (v1 launch scope,
[cut item 2](../../../plans/stewardship/v1-launch.md#cut-from-v1)). What
the application does provide is the compatibility check and comparison
below, `restore-begin` and its restore gate, the held-email review and the
[restore release workflow](../admin-portal/spec.md#restore-release)
([#537](https://github.com/epiphany40223/parishkit/issues/537)).

Restore is operator-driven and unavailable as an ordinary web action. It has two
explicit modes. Empty-target restore is the default and refuses any existing
application data. In-place disaster recovery requires application downtime, an
explicit replace-existing option, exact target identity and backup selection,
and a separate destructive confirmation; it never infers permission from a
non-empty target.

Before either restore mode begins, the operator restores the manifest-matching
credential set from independently held secret escrow and verifies fingerprints
without printing values. Missing escrow, recovery material, or a required
historical data-backup key blocks restore with a sanitized diagnostic.

In v1 the application/schema compatibility check is the read-only
`pk-stewardship restore-check`, run in the target image with no database
before anything is restored: it compares the set's migration set (from its
manifest, or for older sets from its decrypted dump's `django_migrations`
rows) with the image's migration files, exits 0 on an exact match and 3 on
a mismatch, and names the set's own image to use. A set whose only gap is
the users swap and its dependents (#952) is no mismatch: it exits 4,
`swap_pending`, and restores under this image into the state between the
[users upgrade](#users-upgrade-from-sign-in-rules)'s two migrations (see
the backup runbook's
[Restore for real](../../../guides/stewardship-backup-runbook.md#restore-for-real)).
When the Administrator considers restoring a set onto a release with another
schema, `pk-stewardship restore-compare` reports, value-free, how the set's
schema differs from the target image's: it loads the set into a scratch
PostgreSQL server that must hold no database of its own (never the
deployment's cluster), migrates a second with the image and copies it
through the same dump and load into a third, and compares the set's catalog
with that copy's: migrations, tables, columns (type, nullability, default,
identity, generated, collation), constraints, indexes, function
definitions, triggers, row-level security, views, sequences, table storage
options, domains and enums (owners, privileges, other schemas and
extensions excepted). It transforms nothing; any plan built on it
needs the Administrator's approval, the full verification pass and a fresh
backup, and never changes a Family credential (see the runbook's
[Comparing a set with another release](../../../guides/stewardship-backup-runbook.md#comparing-a-set-with-another-release)).

A restore onto the deployment's own host can be run by the host-side
`tools/stewardship-ops/pk-restore`, which follows the runbook's steps in
order, refuses before anything stops unless the deployment UUID, the set's
digests and `restore-check` all agree, never deletes a replaced tree, and
stops for the Restore review (see the runbook's
[Scripted same-host restore](../../../guides/stewardship-backup-runbook.md#scripted-same-host-restore)).
With `--replacement` it also prepares a new host and creates the roles,
refusing unless the host holds no deployment and the set's own runtime row
names the deployment. With `--drill`, used only on a disposable host that
carries the drill marker for its own name and is not the public origin's
host, it starts `web` alone with no route out, so a drill restore of a
Production set can send nothing (see the runbook's
[Scripted replacement and drill restore](../../../guides/stewardship-backup-runbook.md#scripted-replacement-and-drill-restore)).

Both modes verify manifest/digests, application/schema compatibility, credential
availability, and the target-mode precondition before writing. They restore
database/media/config, run permitted forward migrations, and require the
restored active Stewardship YAML digest to match an applied/prepared database
configuration snapshot. A recoverable installer checkpoint is completed
idempotently; an unexplained mismatch blocks readiness and requires operator
diagnosis rather than choosing either copy. Restore then validates one parish
and checks the expected ParishSoft organization without mutation. The mode
stays as restored: the restore gate, not Testing, keeps the scheduler, ordinary
worker admission, production outbox dispatch and Family mail off. Before
exposing any web route, restore invalidates
all restored administration and Family sessions and pending OAuth state,
and revokes every
[automation session](../admin-automation/spec.md#session-rules) (`restore`,
as `revoke-automation-sessions` does in the v1 manual restore).
A new Google sign-in is required for Admin,
Staff, and Ministry leaders; no saved cookie survives restore. Before `web` starts, the
operator runs `pk-stewardship restore-begin --backup-at SET_NAME` in the
`admin-recovery` profile. It records a `restore_begin` runtime transition that
sets the durable `restore_review_required` gate with a new restore id and the
backup's time, and changes nothing else. Restarting recovery keeps that
review: running `restore-begin` again with the same backup while its review
is open writes nothing and reports the open review, and the runtime
transition guard refuses a second `restore_begin` for that backup, so the
review's restore id and its held-email cutoff never move (issue 799). Such a
re-run is a success: it exits 0 and prints `restore_review_started: false`
with `restore_review_already_open: true` and the open review's restore id and
backup time; the operator continues the recovery. Performing another
restore, from a different backup, starts a new review. A restore never creates,
replaces or cancels a Family code or link: the codes and links Families were
emailed keep working once the site is released (the Administrator's hard
rule, issue 537). The
[restore release workflow](../admin-portal/spec.md#restore-release) says how a
link emailed after the backup is handled. A backup taken under a release
older than migration 0017 has no `restore-begin` and cannot use the review;
the backup runbook's limitations give the manual steps for it. A backup taken
in Testing and restored after go-live releases into Testing, and the next
go-live invites every Family again. That gate
blocks every Family authentication, access-token exchange, form, and submit
route regardless of campaign dates or Testing behavior; public requests receive
a neutral parish-branded maintenance page without Family-specific information.

Administration login and the restore-readiness workflow remain available. The
restricted maintenance pool uses existing services and their unchanged secret
mounts, not an additional privileged worker. During the gate, the owning
services claim the following dedicated queues:

| Service | Restore queue | Allowed maintenance work |
| --- | --- | --- |
| `worker` | `restore-general` | Tenant validation, read-only full refresh, public-key-only restore token preparation and stale-credential scrubbing, provider-independent integration checks and mail rendering, hold inventory, integrity diagnostics, non-mail operational notifications, and explicitly authorized interrupted-purge recovery |
| `mail-dispatch` | `restore-mail` | Provider checks, Testing-recipient mail, and operational email using the normal private-key/provider boundary |
| `backup-worker` | `restore-backup` | Backup verification using only its existing backup credentials/keys |

Queues hold references to the same authoritative TaskRun/outbox records; routing
does not copy message state or grant access to another service's secrets.
Provider-dependent integration checks follow their owning service above.
Configuration/credential installers retain only their existing authorized
target-specific workflow queues. The scheduler may recover admissible
maintenance hints but may not materialize live campaign schedules. Services
must not claim ordinary restored work, dispatch `production` outbox rows,
write to ParishSoft, or generate ordinary campaign exports while gated. Every
maintenance task carries a type checked at creation, claim, and external-effect
boundaries; a queue name alone never authorizes work. On release the existing
services resume their ordinary queue admission under the same credential mounts.

The gate can be cleared only by a freshly authenticated Admin through the
[restore release workflow](../admin-portal/spec.md#restore-release), as a
`restore_release` runtime transition, refused while any email still needs a
hold. Clearing the gate is one audited transaction; mode and the current
campaign stay as restored. Failed or abandoned review leaves the restore gate,
Family access, and live delivery disabled while restricted maintenance work
remains available.

Restore review holds every Production invitation and reminder that was due at
the restore and may have gone out after the backup, as durable
`RestoreDeliveryHold` rows whose window runs from the backup's time to the
restore. The
[restore release workflow](../admin-portal/spec.md#restore-release) says
exactly which emails are held. The invitation is held for every Family in the
campaign, whatever its restored eligibility, so a Family that became eligible
after the backup is not invited twice. A Family first added to ParishSoft after
the backup is not in the restored data; if it was invited before the restore,
a refresh after release invites it again. Holds never apply to operational
notifications. An undecided hold keeps its own email back for good. An
undecided invitation also keeps back every reminder of that Family, so the
release preview counts those Families. A hold left undecided can still be
decided after release, as the
[restore release workflow](../admin-portal/spec.md#restore-release) says.
The Admin can resolve each hold as assumed delivered or authorize resend after
acknowledging duplicate risk; neither the restore command nor the release may
globally treat unknown delivery as provider success.

Quarterly restore drills restore to an isolated environment, run integrity and
application checks, and record success/failure metadata. The target recovery
point objective is 24 hours; recovery time is documented/measured rather than
promised as HA. Between drills, CI rehearses a restore whenever its
operational job runs (a manual dispatch, or a non-draft pull request whose
changed paths can affect the operational scenarios): a backup taken by the
backup profile of the image CI builds from the release image's Dockerfile is
opened, checked and compared by that image's own tools and restored into a
fresh PostgreSQL, as the backup runbook's
[restore drill](../../../guides/stewardship-backup-runbook.md#restore-drill)
describes ([#305](https://github.com/epiphany40223/parishkit/issues/305)).

Campaign purge accepts only the verified, post-quiescence backup created for
that PurgeRequest. Ordinary scheduled backup references never satisfy purge
readiness.

## Temporary retention and housekeeping

Generated export files are point-in-time: they are kept only for a short
download window, as [point-in-time exports](#point-in-time-exports) below
defines.

The example Families that a refused large ParishSoft change records (DUID
and directory name only, in `stewardship_source_drop_example`) are deleted
under the rules of
[accept a large ParishSoft change once](../admin-portal/spec.md#accept-a-large-parishsoft-change-once)
(ADM-13): when the refusal is superseded, promoted over or accepted, and at
most seven days after the refusal by default. The worker's hourly
housekeeping deletes them, and no reader shows one whose deletion is due.
After a restore, the restore procedure deletes them too. The worker's
hourly housekeeping, the
[maintenance task](../admin-automation/spec.md#maintenance-task), removes the
[service status record](../admin-portal/spec.md#service-status-records) of
a process that has not reported for a day.

Slot decision records, both `skipped` and `held` (see
[skipped around Family emails](../background-processing/spec.md#skipped-around-family-emails)),
are deleted eight days after their due time, by the same idempotent
housekeeping; nothing reads them further back.

ParishSoft HTTP cache follows configured freshness and bounded size. Upload
staging, failed wizard staging, old static bundles, expired sessions, worker
results, and rotated operational logs have documented cleanup jobs.

Cleanup is idempotent, scoped to explicit subdirectories/records, and cannot
follow unsafe symlinks or broad/unresolved paths. Generic operational-cache
cleanup never deletes source snapshots, submissions, audit history, or backups.
Only the dedicated source-compaction service may thin unprotected snapshot
corpora, under the normative retention and reference guards in the
[data specification](../data/spec.md#source-snapshot).

Each refresh runs this retention first (`source/compaction.py`), under the
source lease's `compaction` phase and outside the work-order lock, within a
60-second time budget. It removes nothing while a restore review is open,
and a failure in it is logged but never fails the refresh:

- report fact cleanup (the derived-fact compaction below) goes first and has
  at most half of it;
- report fact cleanup waits no more than about a second for a row lock;
- each reclaim statement has its own time limit;
- the compaction lease is released even while the worker stops gracefully.

A stop at any of these limits is recorded in the timeout log, and the next
refresh resumes the work. Stops that keep happening without progress open
the [retention incident](../admin-portal/spec.md#parishsoft-refresh-panel)
([#833](https://github.com/epiphany40223/parishkit/issues/833)).

Derived-fact compaction runs as that first share of each refresh's
retention, in bounded batches (one generation per short transaction), under
the [derived fact retention policy](../data/spec.md#derived-fact-retention);
it has no schedule or interval of its own, so it runs as often as refreshes
do. It removes only eligible superseded generations, respects campaign purge/
restore gates, and resumes safely after interruption. Record counts, duration,
last successful completion, and eligible backlog without copying report values;
surface failures through normal task monitoring. Do not put fact deletion in
unrestricted generic cache cleanup. This is an explicit exception for disposable
derived calculations, not a change to source/submission or pinned-report
retention.

Live campaign data otherwise remains indefinitely until the Admin web purge
defined by the [Admin specification](../admin-portal/spec.md#campaign-purge).

### Point-in-time exports

An export is a point-in-time function: it is prepared, offered for download
for a short time, then gone. The parish keeps no copy of earlier exports, and
neither the Admin portal nor the command line keeps a history of them; a
person who wants to keep a file keeps the copy they downloaded (the
Administrator, 2026-10-15,
[#1398](https://github.com/epiphany40223/parishkit/issues/1398)). This is the
normative rule for every export in the shared lifecycle of the
[export worker](../background-processing/spec.md#exports-and-graph-rendering),
including Past campaigns'
[Download campaign archive](../campaign-history/spec.md#download-campaign-archive)
and the command line's `export` commands; other specs link here rather than
restating it. Downloads that stream their file in the response store no copy
and are point-in-time already.

- **Download window.** A finished file can be downloaded, as often as
  needed, for one window after it is ready: the window starts when its
  publication is created and lasts 1 hour by default. A new publication
  records that end as its `expires_at`. The window ends at the earlier of
  `expires_at` and the publication's creation time plus the current
  setting, so a shorter setting applies at once to files already published,
  and a longer one never extends them. Each download still uses its own
  short-lived, single-use grant. Repeat downloads within the window cover a
  transfer the browser did not finish, since a grant used up on the server
  is no proof that the file arrived.
- **The window setting.** Deployment YAML sets the window in whole minutes
  (`export_download_window_minutes`, default 60). Startup validation refuses
  a value below 15 or above 240, since the plaintext exception below relies
  on the window staying short.
- **When an export ends,** the worker's export cleanup deletes its file, if
  it has one, and the scheduler, within a minute, its saved data: the
  capture of what the request read
  (which can hold private contact or giving detail) is discarded, its
  fact-generation and source-snapshot pins are released, and the request no
  longer names the fact generation it read, so that generation can be
  compacted. An export ends at the first of:
  - its download window ending, for a published file;
  - its cancellation, for a cancelled request (its saved data goes once its
    work has stopped, so a render at its safe point never loses its capture);
  - its latest failed attempt's time plus the current setting, for a failed
    request that has not been retried by then. Retry is offered only until then, so a
    failed request that can still be retried keeps its saved data, and one
    that can no longer be retried keeps nothing.

  Nothing can rebuild a file afterwards, so there is no Regenerate: a person
  who needs the file again exports it again from its report, which reads the
  data as it is then. A failed cleanup is retried as
  [export cleanup recovery](../background-processing/spec.md#export-cleanup-recovery)
  says.
- **What stays.** The request and its attempt, publication, grant, download,
  cancellation and cleanup records stay, without the saved data, as
  operational and audit history. Every audit event whose subject is an
  export request carries the context keys `report` (the request's report
  kind, as `export status` prints it; a latest-data export is
  `participation`) and `format` (`csv`, `xlsx`, `pdf` or `png`):
  `export_requested`, `export_cancelled`, `export_downloaded`,
  `campaign_comparison_exported`, `campaign_archive_exported` and each
  `admin_cmd_export_*` event. So for events recorded under this rule, "who
  exported what, and when" needs no export row. Events recorded before it
  lack those keys and still rely on the request rows, which is one reason
  the rows stay.
- **No history.** No page or command lists earlier exports, and an export's
  status page is only its preparing step; what that page shows, during and
  after the window, is the portal-wide
  [point-in-time downloads](../ui-conventions/spec.md#point-in-time-downloads)
  rule.
- **Exports made before this rule** follow it too. Their window ends by the
  same rule, which counts from when the publication was created, so the
  seven-day `expires_at` their immutable publications recorded no longer
  keeps them; an older cancelled or failed request ends by the rules
  above. The forward migration that brings the rule in, under the
  [post-launch schema policy](#post-launch-schema-policy), makes cleanup use
  that end. At deploy, cleanup then deletes the saved data and releases the
  pins of every export that has already ended, including those whose file
  expired earlier and whose capture was kept only for Regenerate. No applied
  migration is edited, and no table, row of history or database is dropped.

Exports, including Family-code and mail-merge exports, are intentionally
stored without application-layer encryption during their download window.
Plaintext exposure to the owning application account and privileged host
operators is an accepted product risk; the low-sensitivity classification of
campaign codes does not make the accompanying parishioner information public.
Owner-only storage under [runtime storage](#runtime-storage), authenticated
downloads, and the short window are the selected controls. Export encryption
or a shorter code-specific window is not required. This exception does not
change encrypted backup or database-field encryption requirements.

## Observability and health

Containers log structured JSON to stdout/stderr with correlation IDs and safe
context. A failure line may also carry `error_class`, the `module.qualname` of
the exception's type (for example `builtins.AttributeError`): a name the code
controls, never the exception's text, accepted only beside a `failure_kind`
and only as a bounded dotted identifier; a name that is not one is left
out, and the line is still logged. Only a caller whose failure has no
other trace adds it, such as the
[admin command line](../admin-automation/spec.md#correlation-and-logging).
Application operational/audit storage is separately queryable in the
Admin UI. Metrics include request latency/error, sessions, queue depth/age,
task duration/failure, scheduler lag, outbox age/delivery, ParishSoft snapshot
age, database/broker health, disk usage, backup age, and TLS expiry. In v1
`/metrics` (`runtime_health.py`) exposes only readiness per dependency, free
bytes in the reports and media stores, the database's connection count, and
HTTP responses by status class with a request-duration histogram; the rest
are deferred with the
[Phase 7 hardening cuts](../../../plans/stewardship/v1-launch.md#reduced-for-v1),
and Administrators see backup age, ParishSoft data age and the mail
sender's state on [System health](../admin-portal/spec.md#system-health)
instead.

Every time limit that stops work leaves an operational log entry that says
what was stopped, which limit stopped it and after how long, using the
`timeout` context (task id, type and attempt when known, limit and elapsed
seconds, a reviewed name for the limit, and an occurrence count when one
entry summarizes several). A read deadline, a PostgreSQL
statement, lock or transaction timeout (including the lease renewal's own
lock and statement limits and its drain limit), or a helper process killed at its
deadline is an ERROR (`task_timed_out`, `helper_timed_out`). One exception
is a lease renewal's lock or statement timeout that is retried while the
lease keeps its margin: a WARNING with outcome `retry`; the timeout that
ends the run is the ERROR, with outcome `failed`. Those WARNINGs are the
rollout measure for the retry: their count per day (System logs, level
WARNING, event `task_timed_out`) says how often renewals queue behind the
work-order lock, and any ERROR among them says a run was lost to it. A
ParishSoft load stopped at its own time bound is a WARNING too
(`source_load_budget`), since the refresh retries it as a
[slow provider](../background-processing/spec.md#full-cycle). A helper entry is
written right after the kill and names the helper (a mail, ParishSoft or provider
check helper) and the task it served, when a worker runs it. A web worker
killed by Gunicorn's master is a `helper_timed_out` ERROR as well, which the
master writes just before the kill on the web login's own connection, without
loading Django. It is `web_drain` when a worker is still serving at the end of
a stop's grace, with how many workers were killed. That grace is the
container's stop grace less a margin, so the kill and its entry come before
Docker's, as for the
[worker's sibling processes](../background-processing/spec.md#worker-queues-and-processes). It is
`web_heartbeat` when a worker was silent for longer than the server timeout.
The master waits at most five seconds for the entry before it kills. A lost worker
lease is a WARNING, and an ERROR with the failed outcome when it ends the
task's last attempt (`task_lease_lost`). Housekeeping that stops at its time
budget and resumes on its next run is INFO for retention and WARNING for the
off-site backup copy (`work_budget_reached`). Entries that precede a process
exit or a rolled-back transaction are written on their own short-lived
database connection; recording never raises, so a failure to record cannot
replace the original outcome. The same facts (the limit's name, the limit
and the elapsed seconds) go to the process log first, before the durable
write. Each process writes one durable entry at a time, so a consumer
process holds at most its task, lease-renewal and one timeout-log
connection; an entry that waits more than five seconds for its turn is not
written durably, and only the process log records it, with that first
line and a WARNING `task_timed_out` for `timeout_log_slot` giving that
limit and the wait (see the
[Family mail dispatch guide](../../../guides/stewardship-family-mail-dispatch.md#two-mail-consumers)).
A [service status record](../admin-portal/spec.md#service-status-records)
write stopped by its own statement or lock limit is a WARNING
`task_timed_out` that names no task, even when the write ran on a task's
thread, because no task was stopped. Each one is recorded; a process tries
the write at most once a minute, which spaces them. There are three
exceptions to the durable entry, which go to the process log only:

- an online service's `startup_database_wait` timeout, because the database
  is what is unavailable (see [online service startup](#online-service-startup));
- a configuration or credential installer's service status write timeout,
  because the installers' logins have no operational-log grant, and the
  record is for display only, so it does not justify giving them one;
- a refused Admin input's durable write stopped by its own limits
  (`refusal_log_write`), because a second durable write would most likely
  meet the same limit and delay the refused answer again (see
  [Admin input refusals](#admin-input-refusals)).

Each runtime login may write only the operational log entries its own code
writes (#389). The table's writer trigger holds a closed list of (schema,
event, level) per login: every login may record the four timeout events at
INFO, WARNING or ERROR; web adds only an unusable source Member, a Family
engagement failure, a failed
[Admin command-line command](../admin-automation/spec.md#correlation-and-logging)
(#617) and a refused Admin input (`admin_input_refused`, schema
`refusal`, at INFO or WARNING; see
[Admin input refusals](#admin-input-refusals)), and never writes CRITICAL;
the worker and scheduler add the entries of their own failure, health and
lag paths. No direct entry may name an actor, except a refused Admin
input, which must name the user it refused: the trigger refuses those two
web tuples with no actor, so the database itself keeps anonymous refusals
out. That exception holds for those two tuples only, and moves no trust
boundary, because the web login already names an actor on every audit
record it writes. Each entry gets the
database's own time. Entries written inside
SECURITY DEFINER functions run as the schema owner and are not limited by it.
A new write path therefore needs a forward migration that extends the list.

`/health/live` confirms the web process loop only. `/health/ready` confirms the
database, migrations, Valkey limiter store, and configuration needed for the
deployment's current setup phase; it must not call external services per probe.
A bootstrapped but product-unconfigured deployment is ready when it can safely
serve login and the first-Admin wizard, even though campaign/integration
readiness is incomplete. After the wizard commits the configured marker,
readiness additionally requires the critical credential references and durable
configuration for normal operation. Worker/scheduler health uses heartbeats and
queue-lag records.

Container health checks use `/health/live`, not `/health/ready`. A failing
check marks the container unhealthy but restarts nothing; Docker restarts a
container only when its process exits. A web server that stays unhealthy
raises the CRITICAL web alert instead
([unresponsive web server](../background-processing/spec.md#unresponsive-web-server)).
Readiness is an operator and alerting signal only; no proxy or orchestrator
removes this single application instance from traffic when it fails. Admission
middleware independently fails closed for guessable-credential authentication
when Valkey is unavailable, without restarting an otherwise live web process;
existing authenticated sessions and opaque-token exchange can remain available
as specified by the architecture.

The two HTTP health routes are internal-only and return no phase or reason
detail. `pk-stewardship health` provides detailed operator diagnostics on the VM
without creating a public endpoint. `pk-stewardship source-form-check` is a
read-only web-container diagnostic that lists, by Family DUID, Member DUID and
field name only, each ParishSoft Member or contact value or record that would
refuse a current Family's form; the [deployment
runbook](../../../guides/stewardship-deployment-runbook.md#staff-validation-checklist)
says when to run it. Administrators see the same list, from the same check, on
the [Families the form cannot open](../admin-portal/spec.md#system-health-page)
page linked from System health. `pk-stewardship engagement-backfill` is admitted
the same way and fills the [Family engagement
record](../data/spec.md#family-engagement) once after the release that
introduces it.

Administrators see this health in plain words, and fix its routine
problems, on the Admin portal's
[System health](../admin-portal/spec.md#system-health) page (ADM-13). The
page works through durable requests that the services act on, so the web
process gains no Docker access, backup credential or host command.

The application exposes Prometheus-compatible metrics only at `/metrics` on its
internal Compose interface. The route requires an `Authorization: Bearer`
credential, compares it in constant time, and returns the ordinary not-found
response when authentication fails. Bootstrap generates the random credential
as an owner-only file; Compose mounts that individual file read-only only into
`web` and an explicitly authorized metrics client. Rotation atomically replaces
the credential through its target-specific credential installer, and clients
must acknowledge the new safe fingerprint before the old value is retired.
Neither the credential nor its hash appears in URLs, logs, metrics, support
bundles, or the database.

No health or metrics endpoint exposes parish names, Family/Member data, emails,
tokens, campaign content, or credentials. Caddy never proxies any of these three
internal paths, even when a caller supplies a valid metrics credential.

### Admin input refusals

Every Admin request whose input is refused leaves an operational log entry
that says what was refused and why
([#1403](https://github.com/epiphany40223/parishkit/issues/1403)). One
event, `admin_input_refused` (schema `refusal`), covers every kind of
refusal, with a closed reason code saying which, so a new kind needs no
migration. A refusal is a 400, 403, 404, 409, 410 or 422 answer to a request
that resolved to an Admin page: a field or form error, a stale page,
unexpected or duplicated form fields, an expired or altered
[review](../ui-conventions/spec.md#review-dialog) preview, a denial, or a
CSRF failure. A page that answers a refusal in place with 200 (such as
Automation access refusing a pairing code) marks its answer as
a refusal, and it is recorded without a status. A 404 for an address
that names no Admin page is not input and is not recorded. A control drawn
[unavailable](../ui-conventions/spec.md#prerequisite-gates) is not a
refusal, since nothing was submitted, and neither is opening a page its
menu greys out right now
([unavailable menu pages](../ui-conventions/spec.md#unavailable-menu-pages)):
that page explains a visit, not refused input. A request forged past either
is a refusal. The
[admin command line](../admin-automation/spec.md#refusal-entry) records its
refusals as the same event.

One shared hook records them: a middleware that sees each Admin response
before it becomes a page for people, so no view makes a logging call of its
own. The reason comes from what the refusal already carries: its closed
`ErrorCode` values and field identifiers; a closed reason code on the
refusal itself, which the shared refusals fix (`stale_page`,
`unexpected_fields`, `expired_preview`, `bad_signature`, `csrf_failed`,
`denied`, and `gone` for a retired action or another campaign's address;
`fresh_authentication_required` stays in the set only so entries written
before [#1547](https://github.com/epiphany40223/parishkit/issues/1547)
still read, and nothing emits it now), or which a refusal names
for itself (such as `campaign_zone_not_parish`, `image_too_large` or the
Off-site backups page's Test access refusals); and the error codes of a
refused form: Django's own (`required`, `invalid_choice`, `max_length`
and so on) and the closed code every validation error of the package's
own forms carries (`too_large`, `quarter_hour`, `commitment_before_campaign_end` and
so on), which every Admin form records through one shared form base. A
page that answers a refusal with a body of its own (a report's error page,
a page script's JSON answer, an in-place region's refused step) gives that
answer its closed codes, at least the code its status stands for
(`invalid` for 400, `denied` for 403, `stale_version` for 409 and `gone`
for 410; a 404 or 422 has no such code and records its status and any
codes of its own).
A refusal with no reason of its own has reason `unclassified`. When an
exception refused it, `exception_class` names that exception's class; a
form drawn again with its errors has no exception, and names its form,
fields and codes instead. Django's CSRF reason text is never used.
An entry that says nothing about why (reason `unclassified` with no
`exception_class` and no codes) is a defect: under the test settings
(`STEWARDSHIP_REFUSAL_LOG_STRICT`) the hook notes it, never raising, and the
test hook fails the test whose answer made it once that test ends, so every
Admin page's refusal must carry a reason, codes, a
typed error or an exception class, and the existing page tests enforce
it. A page drawn again with a refusal of its own, whose form may or may
not hold errors, gives a default: the code its status stands for, which
restates the status, and any further code the page names (such as
`in_use`), recorded only when no typed error, code of its own or form
error gives one. Production records such an entry as it is.

**Allow-list.** The context holds closed values only, mirrored by
`stewardship_safe_context_v1`. Anything not on the list is dropped, not
redacted:

- `route`, the resolved page name (such as `admin:campaign_create`), never
  the path or query, with `method` and `status`; or, from the command line,
  `command`, its catalog name. Every entry carries exactly one of `route`
  and `command`;
- `form` and `fields`, server-owned form and field names, at most 10.
  `form` is the first refused form's class name in lowercase snake form
  (`CampaignForm` is `campaign_form`). `fields` comes from the form's own
  declared fields, never from the keys a request submitted. A formset field
  keeps its prefix and index (`slots-0-time`), as `web.contracts.FieldError`
  field identifiers allow;
- `codes`, the `ErrorCode` and Django error codes, at most 10. The
  refusal's own typed errors come first, then the errors of every Admin
  form the request validated, as the forms hold them when the answer
  leaves the view. A formset row marked for deletion is left out;
- `reason`, the closed reason code, always present. It also keys the
  sentence [System logs](../admin-portal/spec.md#activity-groups) shows, so
  an entry reads the same in any language and after a message's wording
  changes;
- `exception_class`, only with reason `unclassified`: the exception type's
  own class name in lowercase snake form (`ValueError` is `value_error`,
  `StaleRecordError` is `stale_record_error`), never its module or text;
- `expected` and `actual`, only for reasons whose values are allow-listed
  settings: enum words, IANA zone names, counts, sizes, content types and
  versions (for example a campaign zone refused because the parish's stored
  zone differs). Each is at most 64 characters of `A-Z`, `a-z`, `0-9` and
  `_+./-`, so never an `@`. Only Python decides which reasons may carry
  them; the database checks their shape, not their reason. Two reasons
  carry them: `campaign_zone_not_parish` (the parish's zone and the zone
  chosen, both from the server's zone catalog) and `image_too_large` (the
  limit and the file's size, in bytes);
- `unexpected_count`, for reason `unexpected_fields`, the number of
  submitted field names that are not the form's own, when the check that
  refused them passes what was sent and the names it allows. Those names
  are client-supplied and are never recorded. A field of a refused form's
  own (or one such a check allows) that was sent twice is named in
  `fields`, with the code `duplicate`;
- `mode`, `testing` or `production`, the global mode when the refusal was
  made; and
- `capped`, below.

Identifier values (`route`, `method`, `form`, `fields`, `codes`, `reason`,
`exception_class`) match `^[a-z][a-z0-9_:-]{0,63}$`; `method` is stored in
lowercase. `command` is a catalog name, at most 64 characters matching
`^[a-z][a-z-]*( [a-z][a-z-]*){0,3}$` (such as `refresh start`), the same
shape `admin_command_failed` accepts. Python holds the closed sets, as it
does for `failure_kind`, so a new reason code needs no migration. The
entry's correlation is the request's. Its actor is the signed-in user's
internal `PortalUser` id, never an address; this is the one operational
entry written directly that names an actor, and the writer trigger refuses
one without an actor (see the writer list above). The web code asserts that
id; the column has no foreign key, as on every operational entry. The entry
never holds submitted values, keys, credentials, tokens, codes, email
addresses, names, postal addresses, phone numbers, notes or other free text,
file contents or file names, request bodies, query strings or exception
text.

**Process-log line.** Every refusal, anonymous or capped included, writes
one `admin_input_refused` line to the process log, at the entry's level,
with the same allow-listed keys and nothing else. It names the actor's id
only for a signed-in user. For a signed-in user it is written after the
advisory lock and the count below, because it must say whether the entry
was capped, and before the insert; when a limit stops the lock or the
count, it is written then, without `capped`. An anonymous refusal writes it
at once.
It carries `capped: true` exactly when the entry does (the 30th of a
budget, which is still written) and when the cap kept the entry out.

**Levels.** The reason decides the level, not the status, so the page and
the command line agree:

| Reasons | Level |
| --- | --- |
| `denied` (the fixed reason of `UserFacingDenied` and of the access checks' refusals), `csrf_failed`, and each reason whose cause is server state or configuration rather than the input | WARNING |
| `unclassified` | WARNING when the exception is a `PermissionError` or any subclass of it, INFO otherwise |
| Every other reason | INFO |

Python keeps the WARNING reasons in one closed set.
`audit.log_contract.REQUIRED` requires `reason` on a
WARNING entry
([what went wrong](../background-processing/spec.md#what-went-wrong-and-recovery)).

**Who gets a durable entry.** Only a refusal for a signed-in Admin portal
user is written to the operational log. An anonymous one (a CSRF failure on
the sign-in page, an unauthenticated probe) has the process-log line only,
so public traffic cannot allocate permanent rows, as for the sampled
[sign-in denials](../admin-portal/spec.md#sign-in-activity).

**Cap.** Each actor has two budgets of 30 durable entries per window, one
for INFO entries and one for WARNING entries, so ordinary refusals cannot
use up the room the denials need. A window is a fixed 10 minutes of UTC
database time,
`date_bin('10 minutes', statement_timestamp(), '2000-01-01 00:00+00')`, so
it starts on the UTC hour and every 10 minutes after it whatever the
server's zone. The count computes it from `statement_timestamp()` after the
advisory lock is held, so it agrees with the database time the writer
trigger stamps on the row, rather than with the transaction's start, which
may come before a lock wait. The 30th entry of a budget in a window carries
`capped: true`; later refusals of that
level in that window have the process-log line only. Every durable row is
kept for good, so both budgets bound them. The count and the insert run
in one short transaction that first takes a transaction advisory lock for
that actor and level, so concurrent requests are serialised and the cap is
exact. The count reads only that actor's refusal entries in the window,
through a partial index on (`actor_id`, `created_at`) of
`admin_input_refused` rows.

**Writing.** The durable write runs after the view's transaction has ended,
so a rolled-back view cannot take it with it: the hook writes only when the
request's connection is in no atomic block, and otherwise leaves the
process-log line only. It uses the request's own database connection, in a
new short transaction of its own that sets `statement_timeout` to 2 seconds
and `lock_timeout` to 1 second with `SET LOCAL`, the limits of
`audit.timeouts`. It opens no second connection and does not use
`audit.timeouts`' writer slot. The web login's connection limit allows one
connection per request thread plus three (`database_provisioning`), and a
private connection per refused request could go past it during a burst of
refusals, failing other requests and the readiness probe. The request's
connection is normally still open, because signing the user in used it;
when it is not, Django opens it again within the deployment's
`connect_timeout`. The transaction has three statements: the advisory lock
(waiting at most 1 second), the count and the insert (at most 2 seconds
each). The write therefore adds at most about 5 seconds to a refused
answer, plus that reconnect when one is needed. It never raises, so a
failure to record cannot change the answer. When a limit stops it, the
entry is not written durably, and the process log records a WARNING
`task_timed_out` naming the limit (`refusal_log_write`), the limit and the
elapsed seconds, after the refusal's own line. That record stays in the
process log only, one of the
[exceptions to the durable entry](#observability-and-health) above.

**Streamed pages.** A refused page drawn again as a streamed response
under its campaign read guard (a report's filters refused at their field,
[#1332](https://github.com/epiphany40223/parishkit/issues/1332)) still
holds the guard's read-only transaction when it leaves the view. The hook
builds its record then, from the same response, and writes the line and
the entry when the response closes, after the guard has ended that
transaction ([#1484](https://github.com/epiphany40223/parishkit/issues/1484)),
under the request's correlation id. Each refusal is still written once:
a response that is not streamed is written at once, as above. The web
server closes a response whether its stream finished or the person's
browser went away, and a stream stopped by the read guard's deadline is
closed too, so those still leave the entry. Only a server process that
stops before closing the response loses the record, line included.

**Not duplicated.** Refusals that already leave a record keep it and get no
second one: refused sign-ins (`admin_login_refused` and the sampled
`admin_login_denied`) and refused automation sessions (`automation_session_refused`). The code
that writes such a record marks the request or response as already
recorded, and the hook skips a marked one, so each of those paths gives
its own record and no `admin_input_refused` entry or line. Debug logging is unchanged, and the
browser sends no report of its own: the server already knows every refusal
it makes.

**Not an audit event.** A refusal changed nothing. An audit record is
written in the owning transaction, which a refused request often rolls
back, carries ownership rules that do not fit, and is the record of
effects, which refusals would flood.

**Retention.** The operational log has no deletion path: its immutable
guard refuses UPDATE and DELETE, and the cleanup jobs under
[temporary retention](#temporary-retention-and-housekeeping) cover rotated
log files only. Refusal entries are therefore kept indefinitely, like audit
events ([retention and deletion](../data/spec.md#retention-and-deletion)).
That is why they hold closed values only, only for signed-in users, and
under the cap. If their volume ever needs a retention rule, that rule is a
separate change with its own migration.

### ParishSoft data age and connection

"Fresh ParishSoft data" used to mean only that ParishSoft had answered
recently: an empty quick update promoted a snapshot and reset the 30-minute
staleness clock, while ParishSoft's change list returned nothing for every
query ([#510](https://github.com/epiphany40223/parishkit/issues/510)). Three
facts replace it, everywhere the system describes or alarms on ParishSoft
data. The facts come from records already kept: each snapshot's kind, start
and promotion, each refresh run's outcome, and the slot decision records
(skipped and held). Quick updates (delta refreshes) were removed in
[#1147](https://github.com/epiphany40223/parishkit/issues/1147) (see
[quick updates removed](../background-processing/spec.md#quick-updates-removed)),
and a stored schedule's quick settings play no part in any of these facts.

- **Last full refresh.** The start time of the newest promoted full
  refresh. Only this drives the alarm below, because only a full refresh
  re-reads everything.
- **Data as of.** What pages, report emails and the command line show: the
  start time of the last full refresh. Until #1147 a later quick update
  that changed at least one record also moved it, and the page could show
  both times.
- **Connection.** Whether ParishSoft answers. Only refresh runs that
  actually called ParishSoft count, scheduled or manual; a
  refresh cancelled because a newer one superseded it, a held or skipped
  slot, a run refused before calling ParishSoft, and a quick update
  retired without calling it (#1147) are not attempts. One
  line is shown, the first that applies:
  1. "Failing since 09:15" when the newest attempt failed; the time is the
     start of the earliest failed attempt after the newest successful one.
  2. "Not checked since 02:00" when no attempt has succeeded within the
     schedule's longest gap between two full times plus the lateness
     margin; the time is the newest success. Time during which
     scheduled refreshes were held for a send or skipped around a Family
     email does not count toward that gap, so a send alone never makes the
     line read "not checked".
  3. "Working" with the time ParishSoft last answered. A past quick update
     that found nothing new and so was recorded
     [unchanged](../data/spec.md#source-snapshot) instead of promoted
     counted as an answer.

  Connection problems keep their existing alarms (repeated refresh failure,
  a refused organization or a refused large change); the connection line
  adds no new alarm.

**When data is out of date.** The **overdue full slot** is the first
counted scheduled full due time (see below) after the last full refresh's
start. The data is out of date when that slot is more than the **lateness
margin** overdue. The margin is the deployment's existing
`source_stale_seconds` (30 minutes by default), which now means "how late a
scheduled full refresh may be", not "how old the data may be" (#510
changed its meaning). A due time counts only if:

- it is a full time of the effective
  [refresh schedule](../background-processing/spec.md#refresh-schedule) in
  the applied configuration;
- it falls after the current schedule first became effective, so saving a
  schedule never alarms at once over times that were not yet scheduled. That
  is the activation of the earliest configuration in the latest unbroken run
  of activations whose schedule settings equal the current ones, found the
  way `initial_source_at` in `source/health.py` finds a tenant's first
  activation; other configuration changes do not move it;
- its slot was not recorded as
  [skipped around a Family email](../background-processing/spec.md#skipped-around-family-emails).
  A window never stays open past its email's due time plus the two-hour send
  allowance, so a stuck or retrying send cannot keep refreshes skipped and
  the alarm silent; from then on held refreshes count as due, with the
  bulk-send hold's allowance; and
- it did not fall while a go-live was
  [holding scheduled refreshes](../background-processing/spec.md#refreshes-wait-for-go-live).
  The hold starts when the Administrator starts a go-live attempt and lasts
  at most 3 hours; once it ends, held slots count as due again, with the
  lateness margin measured from the hold's end.

With the nightly refresh alone at 02:00, the data is out of date at 02:30 if
that refresh has not promoted. With full refreshes at 00:00 and every two
hours from 08:00 to 20:00, a missed 10:00 refresh is reported at 10:30. The
source-staleness alarm (`source_stale`) sounds on exactly this condition,
and the [bulk-send hold](../background-processing/spec.md#daytime-refreshes-wait-for-a-bulk-family-send)
measures its allowance from the same point. Its recovery requires a
promoted **full** refresh with no newer failure; before #510 any promoted
snapshot, including an empty quick update, recovered it.

**Changing the schedule never hides a late refresh.** A full slot is
**already overdue** when its due time has passed and no full refresh that
started at or after that due time has promoted, whether or not the lateness
margin has run out yet. If one is already overdue (or held) when a new
schedule takes effect, it stays the overdue slot until a full refresh
promotes, so saving a schedule cannot silence or reset the alarm; only the
new schedule's own due times before it took effect are ignored. The old slot
may not be a time of the new schedule, so the scheduler could never create
it. Instead, the first scheduler loop under the new schedule requests a
**schedule-change catch-up**: one full refresh with cause `catch_up`, due at
the instant the new schedule took effect, its slot key derived from the
scope, time zone, cause and that instant, with no configured time. Its tick
stores the nightly time in the required `nightly_time` column, as a quick
update's did. The tick guard admits at most one, at exactly that instant,
which it finds in SQL by walking the configuration activations, as
`initial_source_at` does. It is treated as a daytime full refresh: a send in
progress holds it by its cause, recorded as held, and then it runs as the
catch-up with the hold's allowance. If the guard refuses it (the
activations moved between the scheduler's decision and its insert), only the
catch-up is rolled back and logged once; the loop's other slots still run.
Two edges are accepted rather than prevented: a catch-up requested while a
full refresh that started before the change is still running, which then
runs one extra full refresh; and a second schedule change before any full
refresh promotes, which requests a second catch-up at the second instant.
Both cost one extra full refresh and never hide a late one. The refresh
status on the settings page also says when a full refresh is more than the
lateness margin late and not running, and offers **Run a full refresh now**
beside that notice.

For example, the old schedule has full refreshes at 00:00 and every two
hours from 08:00 to 20:00, and a send that started at 09:30 is holding the
10:00 full refresh. At 10:05 the Administrator switches to the new default
(the nightly refresh at 02:00). The 10:00 slot is
already overdue and stays the overdue slot. The first loop under the new
schedule requests the catch-up due at 10:05; the send holds it, so it is
recorded as held and the hold's allowance continues (the alarm waits until
12:30). When the send ends at 11:00, the catch-up runs and promotes at
about 11:07, and the alarm never sounds. The next
scheduled full refresh is 02:00 tomorrow.

**What uses which fact.**

- The alarm, the bulk-send hold and the Admin home page's problem line use
  the last full refresh and the out-of-date rule.
- Home, the refresh page, the ParishSoft page, report emails (their saved copies) and
  the `source` member of `pk-admin status` (see the
  [automation guide](../../../guides/stewardship-admin-automation.md#status))
  show "data as of" and the connection line, and, when the data is out of
  date, which scheduled refresh is late and by how long, or that a bulk
  Family send is holding it. The System health page's
  [ParishSoft refresh panel](../admin-portal/spec.md#parishsoft-refresh-panel)
  shows the same.
  `pk-stewardship health` reports only dependency checks and does not
  carry these facts.
- The settings page no longer refuses a schedule for its gaps: it states the
  longest wait for new data and when the alarm would sound (see
  [cost and freshness summary](../admin-portal/spec.md#cost-and-freshness-summary)).
- Before Start, Go live requires a completed full refresh of the
  current organization and campaign window, of any age: one merely older
  than `source_stale_seconds` does not block Check or Start, because Start
  queues the go-live's own refresh. After Start, the go-live's
  checks require the attempt's refresh (or a later full refresh) and expire
  at the attempt's hold end instead; see
  [freshness during a go-live](../admin-portal/spec.md#go-live-page).

Pages show these times in the browser's time zone and email and the command
line in the parish's, each sentence in one format, for example "ParishSoft
data as of Tue 8:00 AM. Connection: working (last answered 10:15 AM)." and
"The 10:00 AM full refresh is 45 minutes late."

## Automated tests

Normal CI requires no real ParishSoft, Google, email, Slack, backup, or other
external credential and makes no live network calls. Dependencies are injected
and external responses use fakes/redacted fixtures.

`pytest-cov` always measures `src/parishkit/stewardship` plus the exact shared
`src/parishkit` module paths listed in the checked-in
`coverage-stewardship.toml` manifest. The manifest may add shared modules but
cannot remove the stewardship package; missing, duplicate, non-Python, or
out-of-repository paths fail CI. Any shared module implemented or materially
extended for stewardship must be added to the manifest in the same change;
pre-existing unrelated tools remain excluded.

Coverage runs with branch measurement. CI reads machine-readable coverage
output and independently requires at least 80% line coverage and at least 80%
branch coverage across the combined manifest scope; a blended percentage cannot
mask either failure. Authorization, Family credential verification and
access-token exchange, submission transaction, three-way reconciliation,
outbox idempotency, ParishSoft publication, encryption/signing-key rotation,
secret replacement, rate limiting, and purge state transitions receive
exhaustive branch-oriented tests.

Required suites include:

- direct-activation catch-up tests at 5,000 Families with many overdue schedules,
  measuring confirmation/global-lock duration and concurrent Family response
  latency against architecture targets. Assert a constant-size demand/task
  insertion rather than per-Family materialization during confirmation. Test
  bounded batches even for one large coalescing group, atomic checkpoints,
  repeated activation/lost hints, worker restart, changed schedules/eligibility,
  competing scheduler/source producers, close/restore/pause races, hold-aware
  dispatch, and unfinished-demand archive/purge exclusion. Final completion
  must release only its own hold with no duplicate semantic delivery;
- Family-form concurrency tests for unrelated versus relevant source promotion,
  canonical equivalence, household/Member and Ministry-option additions/removals,
  financial changes, disabled sections, relevant form-definition changes, and
  another effective Family response. Verify trusted session-bound baseline
  reconstruction, forged/expired reference denial, source-pin/compaction races,
  pin transfer on commit, promotion/Submit serialization, current eligibility/
  campaign checks, and in-memory conflict review preserving actual edits without
  storing drafts or exposing inaccessible data;
- offline Admin-recovery tests for sole-account rename/deactivation, an
  existing lower-role user as target, a new user as target, and preservation of
  other users/unrelated configuration. Verify host-profile-only invocation,
  narrow installer mounts, startup mutual exclusion, confirmation, stale
  digests, uninitialized/mismatched configuration refusal, crash/resume
  idempotency, session/OAuth revocation, durable audit/security notifications,
  unchanged campaign/maintenance gates, and mandatory normal Google login.
  No test or command may mint a session or rebind an existing Google account;
- exhaustive TaskRun/ScheduleOccurrence transition tests using the canonical
  background tables, including rejection of every unlisted edge and idempotent
  repeated commands. Check each state against archive, purge quiescence,
  schedule replacement, and recovery; cover abandoned-owner fencing, unknown
  external effects, terminal failure versus semantic coverage, concurrent
  explicit retry deduplication, linked attempts with unchanged occurrence/
  delivery identity, and immutable prior failure/uncertainty history;
- rehearsal-reservation tests for atomic issue/reserve rollback, concurrent
  epoch issuance, duplicate retry, and collision with an earlier epoch's code
  after credential cleanup and MAC rotation. Verify reservation records contain
  no Family/epoch links or recoverable codes, collision-only keys never enter
  authentication lookup, missing keys fail issuance closed, and retirement is
  blocked until all reservation/backup dependencies are satisfied. Test purge
  of one versus the last referencing campaign and restore with the required
  historical collision keys; no test may rekey an HMAC-only reservation by
  assuming deleted plaintext is still available;
- purge-reader tests that pause a report between queries and a download between
  chunks, claim purge concurrently, and prove no deletion starts until their
  shared guards release. Verify new readers are denied after claim, first-batch
  prerequisites are rechecked after drainage, stale deadlines never substitute
  for the exclusive guard, timeouts leave all rows/files intact, and crashes
  before the first checkpoint repeat drainage. Cover lazy queries, streaming
  transaction scope, guard-connection loss, multi-campaign lock order, slow
  transfers, cancellation, and post-deletion retry with admission still closed;
- download-capacity tests across multiple web processes, restart/rollout
  overlap, and slow clients. Saturate the download limit while running the
  reference Family workload and background jobs; verify interactive response
  targets, reserved connection/web execution capacity, prompt retryable busy
  responses without file bytes, and no pool spillover. Exercise disconnects,
  timeout/connection loss, stale admission leases, and purge drainage to prove
  slot recovery never precedes guard/stream closure. Reject conflicting pool
  and timeout configurations, including overridden deadlines;
- purge-recovery evidence tests for missing/wrong escrow, unavailable recovery
  private material, missing historical keys, failed backup decryption, manifest
  mismatch, future timestamps, and exact 60-minute expiry. Verify new evidence
  does not overwrite history, replacing a backup or relevant key reference
  invalidates dependent evidence, expiry at queued worker claim prevents all
  deletion, and expiry after committed deletion does not prevent safe resume.
  Exercise manifest-change/claim races, operator attestation authorization,
  strict non-secret fields, redacted verification output, and unchanged service
  secret mounts using synthetic keys/backups only;
- pure unit tests for validation, normalization, dates/DST including
  midnight-gap/fold campaign boundaries, money, percentages, role precedence,
  state machines, merge/diff, report calculations, and export escaping;
- scheduler/transaction tests for idempotent campaign-boundary occurrence
  creation, exact interval gating despite state lag, start/close lock races,
  end-date replacement, retry, and overdue recovery after scheduler outage;
- recovery tests for close-before-start delivery when both boundaries are
  overdue, atomic ordered restore catch-up, and re-emission of lost broker
  hints for already-existing due records without redispatching unknown mail;
- purge/successor exclusion tests covering every nonterminal request state,
  creation/confirmation/claim races, and permitted successor creation after
  successful purge despite its retained tombstone gate;
- report security tests for non-identifying cursors, requester/scope reuse,
  application-only file serving, and owner-only storage without a proxy mount;
- outbox tests for paused direct receipts without schedule membership, durable
  hold/version restoration, resume cancellation, and stale dispatch hints;
- source-compaction integration tests for every age/anchor tier, each protected-
  reference class, promotion-lease exclusion, a late reference winning under
  row locks, idempotent restart, payload garbage collection only after the last
  reference, and coherent reconstruction of every retained snapshot;
- participation-fact integration tests for idempotent rebuild hints, interrupted
  generation invisibility, exact-input atomic publication, pinned generation/
  source protection, deterministic recalculation, and drift detection;
  fake-clock tests cover burst/sustained debounce, claim/completion races,
  duplicate hints, recovery preserving newer demand, fixed pinned cutoffs and
  priority, and prevention of interactive-pointer regression;
- fact-compaction tests for many successive generations and both population
  scopes, hourly scheduling, bounded batches, and crash/retry idempotency.
  Preserve current/stale-display, pinned, building/recoverable, and actively
  consumed generations; race cleanup against pointer publication, new pins,
  reader selection, drift verification, and task claims. Verify export-file
  expiry does not release retained-parent pins, purge/restore gates block
  ordinary compaction, removed generations cannot be partially served, and
  task/audit history plus underlying submissions/protected source inputs are
  unchanged. After protection ends, eligible daily rows are reclaimed;
- archive/Return tests for report email sends due by archive and not yet
  settled or materialized, report sends still to come, explicit audited skips, empty/no-recipient coverage, coalesced
  replacements, failed and unknown deliveries, and new input racing final
  inventory verification; verify scheduler retries, schedule revisions, and
  unarchive/reopen do not resurrect resolved coverage or suppress newer inputs;
- reopen preparation tests for interrupted batches, idempotent retry,
  cancellation, changed source/eligibility/configuration/key inputs, stale
  completion, exact Family coverage, and rejection of inactive generation
  tokens by lookup/dispatch; measure final confirmation at reference scale and
  prove it performs no token-generation/encryption/per-Family insertion work;
- Django request tests for every role/denial/object-scope and CSRF/session
  boundary;
- authentication tests proving that only a user's exact normalized address
  admits a person, at that user's role level, and that no hosted-domain claim
  or email suffix grants a role;
- configuration-authority tests for canonical YAML, schema migration, stale
  base-digest denial, immutable version/manifest activation, crash at every
  installer checkpoint, exact YAML/database digest recovery, fail-closed
  unexplained mismatch, and rollback-as-new-version;
- administration-login tests for early-middleware token-bucket and specific
  application thresholds, trusted source address handling, rejection before
  OAuth state/session allocation, keyed identity counters, progressive
  `Retry-After`, distributed-abuse notification, recovery after window expiry,
  fail-closed limiter-store outage, empty-window recovery after counter loss,
  and denial-counter namespace reset after an authorizing rule change without
  clearing IP counters;
- security tests for Family code normalization, reduced-alphabet generation,
  full-A-Z lookup candidates, malformed-attempt accounting, IP/code-pair and
  per-IP throttling, distributed-guessing detection and recovery, successful
  access during an attack from other addresses, fail-closed limiter-store
  outage, continued opaque-token access with 120-per-minute/burst-30 limiting
  and bounded per-process fallback, generic/audited invalid-token handling,
  access-token exchange and revocation plus digest uniqueness/index use,
  MAC dual-read rotation/backfill/cross-key collision/retirement, encryption and
  signing-key rotation/migration/retirement, target-key-sealed secret staging,
  expiry/destruction, cross-target claim denial, consumer fingerprint
  acknowledgement, absence of plaintext from storage/logs, and atomic secret
  replacement rollback;
- distributed Family-code detector tests proving that 100 invalid attempts
  from 20 IPs within five minutes trigger even when candidates repeat, while
  either unmet threshold does not trigger; include limiter-rejected attempts
  exactly once, boundary expiry, diagnostic-only candidate diversity, and
  deduplicated WARNING/CRITICAL escalation and recovery;
- shared-network Family-code tests covering distinct Families behind one IP,
  successful requests below its failure limit, enforcement at 100 failures per
  ten minutes, the independent five-failure IP/code-pair limit per 15 minutes,
  configured overrides, and recovery as each sliding window expires;
- restore-token tests that take a backup, rotate/revoke a link, restore, and
  prove every pre-restore link is rejected while fresh links and unchanged
  manual codes work after active release. Cover scheduled release, crossing
  close during preparation, ineligible-Family reactivation, draft go-live,
  repeated restore versus process restart, stale prepared generations, changed
  manifests, failed/retried preparation, and no partial activation. Verify
  restored sealed outbox credentials are never sent, authorized resealing
  preserves delivery identity/history/holds, ambiguous attempts are not resent
  automatically, and token replacement alone creates no email;
- rehearsal-credential tests proving Testing email never contains Production
  codes/tokens; rehearsal code/link login succeeds only in the current Testing
  epoch with ordinary acknowledgments and date/eligibility gates. Verify
  mode-disjoint formats, same-code collision prevention across rehearsal epochs,
  namespace/epoch dispatch checks, no missing-credential fallback, and that
  credentials captured from delivered Testing messages plus their sessions fail
  after gate acquisition and Production activation. Cover cancelled cleanup,
  withdrawal/new rehearsal, future campaigns, stale mail workers, and unchanged
  Production codes; verify sensitive cleanup, retained unlinked reservations,
  and HMAC-key migration without old credential revival;
- reusable-token tests for digest-only exchange, public-key sealing,
  dispatch/rotation-only private-key mounts and decryption, failure to decrypt
  from web/general-worker service profiles, repeat-mail rendering, atomic token
  rotation, ineligibility/reactivation, and ciphertext destruction plus new-
  token issuance across close/reopen;
- code-report tests for Admin/Staff-only direct display, exact-code lookup,
  no-store responses, bulk CSV/XLSX/PDF inclusion, report/export audit without
  code values, denial to Ministry leaders, continued availability during
  limiter-store outage, and manual-code inclusion in the no-deliverable-email
  mail-merge export;
- security-content tests using sanitizer allow/deny corpora, upload signature
  and media-type rejection, image re-encoding/decompression bounds, and
  template placeholder validation for both correct substitution and unknown-
  placeholder rejection;
- session tests distinguishing passive heartbeats from the untrusted activity
  keepalive, including CSRF enforcement, hostile-client rate limiting, idle
  renewal, empty payload enforcement, and absolute-expiry denial;
- PostgreSQL integration tests for constraints, transactions, concurrent
  submissions, task claims, source-mutation lease fencing/heartbeat/takeover,
  stale-owner promotion/PUT denial, snapshot promotion, publication, and purge
  rollback;
- worker tests for retry/idempotency, partial failure, missed schedules, and
  abandoned-task recovery, plus schedule replacement/removal races proving
  atomic cancellation, sealed-credential scrubbing on every terminal outbox
  state, direct-activation coalescing, and cross-revision fulfillment;
- Valkey integration tests using the production major/minor line for Celery
  broker delivery, cache operations, atomic sliding-window/token-bucket scripts,
  expiry, restart with accepted counter loss, and fail-closed outage behavior;
- email rendering/routing tests for live/testing/recipient/privacy behavior,
  provider acceptance followed by timeout, provider-status reconciliation,
  idempotent safe retry, unresolved `delivery_unknown`, authorized resend, and
  immutable operational-notification classification/bypass in Testing;
- delivery-pause race tests for pre-provider recheck, continued live submission,
  held receipt creation, immutable operational/test exemption, no Testing
  reroute, atomic backlog coalescing/resume, close-during-pause cancellation,
  and post-close held-message release/cancellation before archive;
- browser tests for setup, Admin/Staff/leader workflows and the full responsive
  Family path, including stale submit and repeat visit;
- browser and authorization tests proving that user changes (New, Edit,
  Delete and Import Ministry leaders) save only through Review and Apply,
  while enforcing CSRF, optimistic concurrency,
  current-Admin authorization, last-Administrator protection, and complete
  audit records; that the Users tables sort in place; and that their pages
  have no horizontal page scroll at 320 px and 1280 px;
- users-upgrade tests proving the preview and the conversion agree, and that
  the conversion refuses to leave no Administrator; a stale base digest
  is refused and never silently rebased past the last-Admin or CSRF guards;
  the function-swap migration and service startup refuse an unconverted
  configuration, a failed or refused conversion leaves the services down, and
  a repeated or resumed conversion is a no-op; every pre-upgrade
  Administrator signs in as Administrator afterwards, a confirmed
  Administrator list that differs from the preview is refused, and offline
  Admin-access recovery works on a converted configuration;
- limiter tests proving that pre-verification IP rejections contribute once
  without OAuth state allocation, provider calls, raw token retention, or
  duplicate counting, and that post-verification identity-limit rejections
  contribute once after signed identity validation without retaining raw
  callback/provider tokens;
- accessibility automation plus keyboard/screen-reader-oriented manual checks;
- CSV/XLSX/PDF/PNG structure/content tests without committing generated reports;
- backup/restore manifest and isolated restore smoke tests, including
  uncertainty-window inventory, newly discovered Families, atomic release-time
  hold creation, catch-up suppression, assumed-delivered accounting, and
  duplicate-aware authorized resend; and
- Compose startup, health, migration, bind-mount reload, and production image
  smoke tests, including proof that both internal health routes succeed, an
  authenticated internal `/metrics` request succeeds, missing/invalid metrics
  credentials fail without disclosure, and Caddy does not proxy any of the
  three internal paths even with a valid metrics credential.

## Acceptance scenarios

At minimum, end-to-end tests demonstrate:

1. Empty deployment through bootstrap/wizard, aborted wizard rollback, restored
   deployment startup with Family access gated, restricted maintenance refresh/
   test-send/hold work while ordinary work remains blocked, and atomic Admin-
   approved release of restored `draft`, future `scheduled`, current `active`,
   expired-to-`closed`, already `closed`, archived-current-pointer, historical
   archived, purged, and no-current cases. The results exactly match the Admin
   restore mapping: in particular, archived-current preserves its pointer and
   Production until separate Return to Testing, while interrupted purge state
   blocks release pending explicit recovery.
   First-Admin setup additionally proves that watchdog/expiry cleanup
   prevents a late worker from restoring discarded staging. A simulated two-hour overrun is treated as a failed/stuck import and
   leaves redacted diagnostic correlation for operator investigation.
   Wizard and later Admin edits additionally prove that authoritative YAML and
   the active PostgreSQL snapshot expose one matching digest, every induced
   installer interruption is recoverable without partial configuration, and
   sealed credential replacement cannot be claimed by the wrong target.
2. Google allow/deny by user email only, the role ladder, last-Admin guard,
   immediate role revocation after applied-YAML activation, and immediate
   Administrator grants upon activation with durable dashboard event and
   preexisting-Admin notification/retry; Ministry scoping from ParishSoft
   roles, user deletion and demotion through configuration requests, and the
   users upgrade from sign-in rules (#952).
3. Testing email rerouting, the Family-facing Testing banner,
   segregated test submission, blocked transition with in-flight test delivery,
   aggregate creation, go-live admission gating, resumable bounded cleanup of
   test submissions/outbox detail, irreversible cancellation semantics, short
   atomic final activation, structural lock, pre-start `draft`-to-`scheduled`,
   in-interval direct `draft`-to-`active` with exactly-once live catch-up, and
   at/after-close rejection on Production transition;
   guarded pre-start withdrawal cancels future live work, returns atomically to
   Testing/draft, and unlocks structural settings, while an active campaign and
   unresolved provider-submitting/delivery-unknown work cannot be withdrawn.
   Production readiness locks the Campaign timezone snapshot; later Parish
   timezone changes do not alter its boundaries, schedules, report emails, or
   historical report buckets.
4. Full refresh success, interrupted/invalid load retaining prior truth,
   new/inactive/reactivated Family behavior, and non-overlap/manual coalescing;
   compaction at every retention boundary preserves every protected reference,
   retains deterministic anchors, remains idempotent after interruption, and
   reconstructs each uncompacted snapshot exactly.
5. No-change Family submission, every census field, proposed/terminal Member,
   Ministry request, zero/positive pledge, additional information, repeat
   submission, and stale concurrent submit.
6. Upstream catches up, three-way conflict, Admin edited approval, preflight,
   partial ParishSoft write failure/retry, read-after-write, and unsupported
   manual resolution.
7. Every report's access, counts/percentages, inactive/test exclusion, privacy
   columns, filters, consistent historical/current participation-chart scopes,
   atomic/pinned CampaignDailyFactSet generation and drift verification, chart
   parity, and CSV/XLSX/PDF/PNG output.
8. Missed initial/reminder/digest occurrence, per-Family and report email
   recovery coalescing, worker/broker restart, systemic email failure,
   deduplicated CRITICAL notification, active-campaign delivery pause with
   continued live submission and held receipts, pre-provider race checks,
   atomic coalescing/resume, and post-close held-message resolution.
9. Closed campaign explicit reopen directly to `active`, atomic access-token/
   Production activation, no replay of work skipped while closed, archive,
   guarded post-archive return to Testing, denial of successor draft before
   both steps complete (successor drafts are now refused outright in the Admin
   portal, per the
   [navigation decisions](../admin-portal/spec.md#navigation-decisions)), guarded unarchive to `closed`, and denial of unarchive
   after purge preparation begins.
10. Web purge blocked for every non-archived campaign, stale backup, or wrong
    confirmation; atomic gate acquisition; concurrent admission rejection;
    cancellation of queued/retrying work; drain/reconciliation of running and
    externally uncertain work; post-quiescence inventory/backup freshness;
    durable preparation/resumption/cancellation; request/Campaign state
    consistency; gate release only on cancellation/pre-delete failure; atomic
    worker-claim recheck; pre-delete rollback; successful resumable batched
    purge and atomic visible tombstone transition; interrupted-batch recovery;
    retryable database/file cleanup; retained parish-owned audit of read-only
    report access without inventory invalidation; and failure notification.

## CI and local validation

The existing required commands remain:

```text
python -m ruff check .
python -m ruff format --check .
python -m pymarkdown --config .pymarkdown.json scan $(git ls-files '*.md')
python -m pytest
```

CI adds the coverage threshold, migration drift check, frontend static/build
check, browser/accessibility job, Compose smoke job, and image build/scan. Fast
unit checks remain useful locally; slow integration/browser/container jobs are
documented and reproducible before a pull request is considered complete.

See also the [local laptop environment](../local-environment/spec.md) for
exercising a change end to end in a production-shaped deployment on a laptop
before it reaches Production; it adds fast, database and rendering tests to CI
but no Docker run.
