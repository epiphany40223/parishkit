# Stewardship local laptop environment

This specification defines a production-shaped Stewardship environment that a
developer runs on a laptop
([#476](https://github.com/epiphany40223/parishkit/issues/476)). It covers the
Linux VM that hosts it, the `local` deployment profile, the stand-ins for Gmail
and ParishSoft, the synthetic parish and seeded campaign, a local-only Admin
sign-in, and the operator script that drives it all. Compose topology, runtime
storage, ingress, startup and test rules that this environment shares with
Production stay in the [operations specification](../operations/spec.md); this
document states only how the local environment differs and what keeps those
differences away from Production.

## Purpose and scope

The local environment exists so that a developer can exercise an Admin or
Family change end to end, through the real setup wizard, real Caddy, real
background services and realistic data, before the change reaches the
Production deployment. It answers "does this behave correctly in a real
deployment?" without real parish data, real credentials or a shared host.

It is:

- **not a production deployment.** It never holds real parish data, never
  talks to Google, ParishSoft, Slack or Google Drive, and it can never email a
  real Family, even after its campaign goes to Production (see
  [safety guarantees](#safety-guarantees));
- **not CI.** Nothing here runs Docker in CI. CI covers the local profile with
  fast tests, timeline tests and database tests that need no fake clock, and by
  rendering the local topology. Seed tests run in the VM (see
  [testing requirements](#testing-requirements));
- **not the Phase 0 development scaffold.** The
  [Compose scaffold](../../../development/stewardship-compose.md) (HTTP, no
  proxy, bind-mounted source) is superseded for running the real application.

## Host virtual machine

The environment runs inside one Linux VM on the laptop, managed by
[Lima](https://lima-vm.io/), not directly on Docker Desktop.

**Why a VM.** The runtime depends on real Linux file ownership: private files
must be owned by uid 10001 with mode 0600 or 0700 (`runtime_paths.py`,
`accounts/key_files.py`), and PostgreSQL and Caddy check their own data
directories. The PR 0 spike in #476 found that Docker Desktop's VirtioFS bind
mounts on macOS keep mode bits but do not store `chown`: each container sees a
file as owned by whatever uid the container runs as. Ownership checks would
then pass or fail depending on which identity looks, and the separation
between the web, worker and installer identities could not be tested. Docker
named volumes keep real ownership, but the rendered topology and the host
tooling use absolute host-path bind mounts throughout (see
[runtime storage](../operations/spec.md#runtime-storage)). A Linux VM gives
native ext4 semantics with no change to either. The decision is about
ownership semantics, not CPU architecture.

The VM:

- MUST be a Lima instance named `parishkit-local` running Ubuntu 24.04 LTS
  for `arm64` on Apple Virtualization.framework (`vmType: vz`);
- SHOULD have 4 CPUs, 5 GiB of memory and a 40 GiB disk. Docker Desktop SHOULD
  be quit while the VM runs, to free its memory;
- MUST NOT share any host directory: the Lima `mounts` list is empty, which
  also removes Lima's default read-only home-directory mount. Source reaches
  the VM only as a packed archive over ssh (see
  [operator script](#operator-script));
- MUST forward exactly two ports, each from the VM's `127.0.0.1` to the
  laptop's `127.0.0.1`: 8443 (Caddy) and 8025 (Mailpit UI). Lima's automatic
  forwarding of other guest ports MUST be disabled with an ignore rule for
  every other port. Lima's own ssh forward is the only other listener;
- MUST run Docker Engine with the Compose plugin from Docker's apt repository
  inside the VM, not Lima's bundled containerd;
- MUST use `/opt/parishkit` inside the VM as the runtime root, exactly as a
  production host does.

The VM is reachable by ssh through Lima's generated host alias
(`lima-parishkit-local`), so ad hoc commands and the
[deployment runbook](../../../guides/stewardship-deployment-runbook.md)'s
inspection steps work against it as against a remote host. The pre-launch
`tools/stewardship-dev-deploy.sh` does **not** support LOCAL: it requires a
GHCR push and digest, assumes the production Compose project name and takes a
best-effort backup. Upgrading a local deployment is the operator script's
`deploy` command (see [operator script](#operator-script)).

## The local deployment profile

`DeploymentProfile.LOCAL` (`local`) is a fourth deployment profile beside
`development`, `test` and `production`. Development and test mean plain HTTP on
loopback with no proxy; LOCAL is production-shaped (HTTPS, one Caddy hop), so it
is a separate profile rather than a flag on either. Deployment profile still
does not set Testing or Production campaign mode; that stays
database-authoritative.

A helper `profile.behind_proxy` is true for PRODUCTION and LOCAL and false
otherwise. Every new code branch that distinguishes LOCAL MUST test `is LOCAL`
or `behind_proxy`; a new test such as `!= DEVELOPMENT` is prohibited, because it
would silently change meaning when a profile is added.

### Existing profile branches

Every existing profile comparison MUST be given an explicit LOCAL behaviour.
Line numbers are as of `main` on 2026-10-03 and are for orientation only.

| Location | Today | LOCAL behaviour |
| --- | --- | --- |
| `deployment._origin` (`deployment.py:260`) | PRODUCTION requires HTTPS; others require loopback HTTP | New LOCAL branch: exactly `https://localhost:8443` (see [origin](#origin-proxy-and-image)). Development and test unchanged. |
| default origin (`deployment.py:536`) | none for PRODUCTION, else `http://localhost:8000` | `https://localhost:8443` |
| proxy hops (`deployment.py:545`, `:551`) | one for PRODUCTION, else zero | one (`behind_proxy`). The "zero locally" message is reworded to name development and test. |
| authentication-limit warning (`deployment.py:628`) | PRODUCTION only | not issued (synthetic data) |
| `runtime_topology._image` (`runtime_topology.py:92-101`) | GHCR digest for PRODUCTION, else `parishkit-stewardship:development` | the local tag pattern only |
| source mounts (`runtime_topology.py:294`) | refused unless DEVELOPMENT | refused (unchanged; this existing `is not DEVELOPMENT` refuses, the safe direction) |
| web replica networking (`runtime_topology.py:426`) | PRODUCTION joins `proxy`; others publish web's port on loopback | joins `proxy` like Production; web publishes no host port (`behind_proxy`) |
| Caddy, Caddyfile and restart policy (`runtime_topology.py:434`) | PRODUCTION only | renders Caddy with the local Caddyfile and `unless-stopped` restarts (`behind_proxy`) |
| trusted proxy networks (`runtime_web.py:179`) | Caddy's `/32` for PRODUCTION | Caddy's `/32` (`behind_proxy`) |
| template reload (`runtime_web.py:191`) | DEVELOPMENT only | off |
| `web/security.py:57` `production` flag | secure cookies, CSRF cookie, HTTPS redirect and HSTS for PRODUCTION | secure cookies, CSRF cookie and HTTPS redirect on (`behind_proxy`); HSTS 0 (PRODUCTION only) |
| `runtime_ingress.production_hostname` (`runtime_ingress.py:15`) | refuses non-PRODUCTION | unchanged; LOCAL uses a separate local Caddyfile renderer that never calls it |
| `/app/src` mount exemption (`service_boundaries.py:250`) | DEVELOPMENT only | not admitted |
| enumerated mounts (`service_boundaries.validate_mounts`) | each role's mounts only; anything else refused | unchanged for every existing role; LOCAL adds rules only for the two local services (`fake-parishsoft`: its one configuration file, read-only; `mailpit`: none) |
| development reload (`runtime_process.py:45`, `:174`) | DEVELOPMENT only | off |
| existing `!= DEVELOPMENT` refusals (`services.py:25`, `cli.py:295`) | refuse outside DEVELOPMENT | unchanged (they refuse, the safe direction) |

A fast test MUST assert, for each row, that LOCAL never receives the
development or test result, and that every other profile's result is
unchanged.

### Origin, proxy and image

- **Origin.** LOCAL MUST have the public origin `https://localhost:8443`
  exactly. Any other host, scheme or port, including `127.0.0.1`, is refused at
  deployment validation. The Caddy publication, the Lima forward and printed
  links all use this one value.
- **Proxy hops.** LOCAL uses exactly one trusted proxy hop, as Production does
  (see [production ingress](../operations/spec.md#production-ingress-and-tls)).
- **Image.** LOCAL accepts only a tag matching
  `parishkit-stewardship-local:[0-9a-f]{40}(-dirty)?-[0-9]{10}`: the
  checkout's commit, `-dirty` when there are uncommitted edits, and the build
  time in Unix seconds. The production rule (one immutable GHCR digest) is
  unchanged and MUST reject such a tag; LOCAL MUST reject GHCR references and
  the development tag. LOCAL also admits the derived
  [fake-clock](#fake-clock) images built from that tag and from the pinned
  PostgreSQL and Valkey digests.

### Topology and web differences

The LOCAL rendering differs from Production only as follows:

- **Extra services.** `mailpit` ([mail catcher](#mail-catcher)) and
  `fake-parishsoft` ([fake ParishSoft](#fake-parishsoft-service)), both on the
  `backend` network.
- **TLS.** Caddy serves `localhost` with `tls internal` (its own local CA).
  There is no ACME account and no port 80 listener.
- **Published ports.** Caddy publishes only `127.0.0.1:8443` and Mailpit only
  `127.0.0.1:8025`, both inside the VM. Nothing binds `0.0.0.0`.
- **Networks.** `backend` and `proxy` are already `internal: true` in every
  profile; today outbound traffic leaves through the non-internal
  `application-egress` network (web, worker, mail-dispatch, the ParishSoft,
  Workspace and Slack installers, and the backup worker) and Caddy publishes
  through `ingress`. LOCAL MUST omit `application-egress` entirely. Only Caddy
  and Mailpit join `ingress`, which LOCAL creates with
  `com.docker.network.bridge.enable_ip_masquerade: "false"`. Published ports
  still work, because Docker forwards inbound connections to them, but
  containers on `ingress` get no source NAT and so no route to the internet.
  This is a Docker bridge setting, not a firewall; a manual check in the VM
  (an outbound request from the Caddy container fails) is part of the
  [testing requirements](#testing-requirements).
- **Project name.** The renderer's top-level Compose `name` is
  `parishkit-local` (Production's stays `parishkit-stewardship`), and the
  operator script passes `-p parishkit-local` to every Compose command.
- **Security headers.** As in the [branch table](#existing-profile-branches):
  secure cookies and the HTTPS redirect stay; HSTS is 0, so a browser does not
  pin HTTPS for every `localhost` service the developer runs.
- **Banner.** Every Admin and Family page shows a persistent, non-color-only
  LOCAL banner. Emails are not altered, so what Mailpit shows is what a Family
  would receive.

### Production invariants

LOCAL MUST NOT change Production. Specifically:

- the rendered Production Compose document and Caddyfile MUST be byte-identical
  before and after each local-environment change; a golden-file test pins them;
- rendered database grants and the Valkey ACL MUST be identical across
  profiles; LOCAL adds no login, role or grant;
- the production URL configuration MUST NOT import or route to any local
  module;
- the schema's guard and function SQL contains no LOCAL branch, which a test
  asserts;
- production images and digests are unchanged; libfaketime exists only in the
  [fake-clock](#fake-clock) derived images;
- the `fake-parishsoft` and `local-seed` commands ship in the one application
  image but are inert: each refuses to run unless the profile is LOCAL.

## Safety guarantees

Each guarantee below is enforced where the value is installed and again where
it is used, so that a misconfigured file cannot cross between LOCAL and
Production in either direction.

- **Mail.** The mail-catcher credential is a small JSON document with a
  distinct type marker and no secret; the SMTP endpoint itself is the code
  constant `LOCAL_SMTP_ENDPOINT = ("mailpit", 1025)`, never a document field. In
  LOCAL, every intake of the Workspace credential accepts only that document
  and refuses any Google service account; every other profile refuses the
  mail-catcher document. The intakes are the `google_workspace` credential
  installer and the setup wizard's credential step
  (`accounts/setup_credentials.py`, `integration_candidates.workspace_info`).
  Every Gmail call site (`family_delivery.SmtpSession`,
  `readiness_delivery.deliver_sample`, `provider_check_worker`, `smoke.py`, and
  the operational, digest, weekly and security delivery modules) selects its
  transport from the installed document type and refuses a mismatch with the
  profile.
- **Helper subprocesses.** Helpers start with `python -I` and an empty
  environment, so they cannot read the profile themselves. Each helper request
  MUST therefore carry an explicit `profile` field: `parishsoft_http_worker`
  (through `parishsoft_transport`), `provider_check_worker` (through
  `provider_checks`), the Family and readiness delivery workers, and any other
  helper that opens SMTP or ParishSoft connections. A helper admits
  `LOCAL_SOURCE_BASE_URL` or `LOCAL_SMTP_ENDPOINT` only when the request's
  profile is `local`, and refuses them for any other profile. A production
  helper therefore refuses the local endpoints even if a parent passed them.
- **Production activation is allowed** (Administrator decision, 2026-10-03,
  recorded on [#476](https://github.com/epiphany40223/parishkit/issues/476)).
  The real go-live flow works in LOCAL: readiness, Testing cleanup, link
  preparation and confirmation, so the go-live flow
  ([#462](https://github.com/epiphany40223/parishkit/issues/462)) can be tested
  locally. The only change is a LOCAL rule in the public-web-address check:
  `check_public_origin` and its `origin_check_worker` (reached through
  `go_live_commands.configured_origin` from both the go-live preview and
  `confirmation_commands`) accept exactly `https://localhost:8443` for the
  LOCAL profile instead of requiring a resolvable public DNS name. That rule
  does not live in `deployment._origin`, and every other profile's result is
  unchanged. The safety argument does not rest on refusing activation. Real
  Families cannot be emailed because:
  - Gmail and Google service-account credentials are refused in LOCAL;
  - the VM's application network has no internet egress;
  - all mail goes to Mailpit through the mail-catcher transport;
  - the data is synthetic, with only `@example.test` addresses.

  A test MUST show that, after LOCAL activation, every Family and
  administrative message is sent only through the mail-catcher transport.
- **Smoke tools.** `pk-stewardship smoke` (see the
  [smoke tools guide](../../../guides/stewardship-smoke-tools.md)) refuses
  LOCAL; there is no real provider to smoke-test.
- **Google sign-in.** In LOCAL the `google_oauth` installer accepts only a
  sentinel client document that other profiles refuse, and the LOCAL login page
  hides Google sign-in. The sentinel holds no real client ID or secret. Admin
  sign-in in LOCAL is the [local test sign-in](#local-test-sign-in).
- **ParishSoft.** In LOCAL the only reachable ParishSoft base URL is
  `LOCAL_SOURCE_BASE_URL`; the `parishsoft` installer and configuration refuse
  any other. Outside LOCAL the same checks refuse that constant.
- **Slack and Drive.** The `slack` credential target and the Google Drive
  backup target are refused in LOCAL. Operational alerts that would go to Slack
  are recorded and emailed only (to Mailpit).
- **No egress** (see [topology](#topology-and-web-differences)) backs these
  checks up: even a mistaken real credential has no route out.

## Mail catcher

[Mailpit](https://mailpit.axllent.org/) receives every message the LOCAL
deployment sends:

- **Transport.** Plain SMTP to `LOCAL_SMTP_ENDPOINT` on the `backend` network,
  with no TLS and no authentication. Only the identities that already receive
  the `google_workspace` credential receive the mail-catcher document, under
  the same [mount rules](../operations/spec.md#runtime-storage).
- **What is captured.** Everything the deployment sends, including the
  seeder's past mail (see [mail history](#mail-history)): Family invitations, Reminders and receipts, readiness
  samples, Admin digests and operational and security alerts. Messages go to
  whatever address the application chooses, so
  [mode routing](../background-processing/spec.md#mode-routing) is visible
  unchanged. Synthetic recipients are all at `@example.test`.
- **UI.** The Mailpit web UI is at `http://localhost:8025` on the laptop,
  forwarded from the VM's loopback. It needs no sign-in; it is reachable only
  from the laptop.
- **Image and storage.** Mailpit's image is pinned by multi-arch digest like
  the other third-party images. Its store persists under
  `run/persistent/mailpit` with a message cap (for example 50,000).

## Fake ParishSoft service

A stand-in for ParishSoft's v2 API serves the [synthetic parish](#synthetic-parish)
to the unmodified source loaders, so setup loading, delta and full refreshes,
giving and household reads all run through their real code.

- **Code.** `stewardship/local/fake_parishsoft.py` is a standard-library HTTP
  server (no new dependency); `stewardship/local/synthetic_parish.py` is the
  generator. The `fake-parishsoft` command, run from the application image as
  its own Compose service, refuses unless the profile is LOCAL, before binding
  any socket. The profile is the admitted deployment configuration's, resolved
  as every other service resolves it (`--config`, `PARISHKIT_STEWARDSHIP_PROFILE`,
  an explicit `--profile` override), and anything but a valid LOCAL deployment
  is refused; its own configuration file path is an explicit `--fake-config`,
  and `--port` (default 8080) is the listening port on every interface of its
  container.
- **Mounts and credentials.** The service receives no credential and exactly
  one mount: its own configuration file (below), read-only. It runs as the
  application uid, which owns that file, and joins only `backend`.
- **Base URL.** `LOCAL_SOURCE_BASE_URL = "http://fake-parishsoft:8080/api/v2"`.
  It is threaded through every `ParishSoftConfig` call site and both helper
  subprocesses. Each allowlist admits exactly the profile's one base URL: this
  constant for a `local` request and the real base URL for every other, never
  both (see [helper subprocesses](#safety-guarantees)). The shared
  `parishkit.parishsoft_http_worker` owns the profile-to-URL rule and the
  profile names, so the helper applies it without importing application code;
  a stewardship test keeps those names aligned with `DeploymentProfile`. A
  process whose runtime assembly recorded no profile cannot read the source at
  all.
- **Authentication.** Requests MUST carry `x-api-key` equal to the code
  constant `LOCAL_PARISHSOFT_KEY`, which `up` installs as the `parishsoft`
  credential; any other or missing key gets HTTP 401 with an empty body.
- **Read-only.** The fake implements only the read endpoints below and has no
  control endpoint. Any other method or path gets 404 or 405, so ParishSoft
  publication fails as unsupported in LOCAL.

### Fake configuration

`up` writes `run/local/fake-parishsoft.json` in the runtime root, mode 0600
and owned by the application uid (the uid the fake runs as),
containing:

- `seed`: the generator seed;
- `families`: the parish size, default 100 (see [synthetic parish](#synthetic-parish));
- `anchor_date`: the fake-clock local date at `up` (about 17 days before the
  real date; see [fake clock](#fake-clock)), fixed for the life of the
  deployment; and
- `release_at`: the instant from which the
  [late-added Family](#response-pattern) is served, or `null` (held back); and
- `change_feed` (optional): `empty` (the default) or `synthetic`, the
  [change feed's](#change-feed) behaviour.

The fake reads the file once at start, so restarts and deploys keep the same
data. `up` writes `release_at: null`, so the setup wizard's initial load does
not see that Family. `seed` sets `release_at` to real now and restarts the
fake at the start of its final phase, just before that phase's refresh in
normal mode. No seeder phase mounts
this file: the operator script reads it and passes its values as arguments (see
[operator script](#operator-script)). The fake serves the late-added Family only
while the current time is at or after `release_at`. Apart from that one
comparison, responses depend only on the file's contents. Ordering within every
collection is stable across pages and across calls. The fake does not inject
faults.

### Endpoint contract

The fake MUST satisfy the strict contracts that the application already
enforces; those code constants, not this table, are authoritative:
`POST_CONTRACTS` and `GET_CONTRACTS` in `parishkit/parishsoft_source.py`, the
envelope parser in `parishkit/parishsoft_pagination.py`, the allowlist in
`parishkit/parishsoft_http_worker.py`, the change feed in
`parishkit/parishsoft_changes.py`, and the detail reads in
`parishkit/parishsoft_households.py`. Paths are relative to the base URL.

| Method and path | Paging fields (size, position) | Response shape | Identity field |
| --- | --- | --- | --- |
| `POST organizations/search` | none | array of exactly one organization | `organizationID` (plus `organizationReportName`) |
| `POST families/search` | `pageSize`, `pageNumber` (body) | bare array; each row carries `totalResults` and `rowNumber` | `familyDUID` |
| `POST members/search` | `maximumRows`, `startRowIndex` (body) | bare array; each row carries `recordCount` and `rowNum`; position 0 and 1 both answer the first page | `memberDUID` |
| `POST members/contact/list` | `limit`, `offset` (body) | bare array; position 0 and 1 both answer the first page | `memberDUID` |
| `GET families/group/lookup/list` | none | bare array | `famGroupID` |
| `GET families/workgroup/list` | `PageSize`, `PageNumber` | bare array; each row carries `recordCount` and `rowNum` | `workgroupDUID` |
| `GET families/workgroup/{id}/list` | `PageSize`, `PageNumber` | envelope | `familyId` |
| `GET members/workgroup/lookup/list` | `PageSize`, `PageNumber` | envelope | `id` |
| `GET members/workgroup/{id}/list` | `PageSize`, `PageNumber` | envelope | `memberId` |
| `GET ministry/type/list` | `PageSize`, `PageNumber` | envelope | `id` |
| `GET ministry/{id}/minister/list` | `PageSize`, `PageNumber` | envelope | `memberId` with type, role, event and `startDate` |
| `GET offering/{organization}/funds` | none | bare array | `fundId` |
| `GET offering/pledge/list` | `PageSize`, `PageNumber` | envelope | `pledgeID` |
| `GET offering/contributiondetail/list` | `PageSize`, `PageNumber` | envelope | `contributionID` |
| `GET families/change/list` | `StartDate`, `EndDate` (no paging) | bare array, fewer rows than the caller's ceiling; see [change feed](#change-feed) | `family_DUID` with `currentParishID`, `previousParishID`, `logDate` |
| `GET families/{id}` | none | one Family object | `familyDUID` |
| `GET families/{id}/member/list` | none | bare array | `memberDUID` |
| `GET members/{id}` | none | one Member object | `memberDUID` |

Further rules:

- "Envelope" means exactly `{"data": [...], "pagingInfo": {"totalRecords",
  "totalPages", "pageSize", "pageNumber"}}` with internally consistent values
  on every page, as the envelope parser requires. Page numbers start at 1.
- Embedded row ordinals (`rowNumber`, `rowNum`) are contiguous across pages,
  and embedded totals are constant within a scan.
- Member rows and Member objects carry the `familyDUID` of their Family, and a
  Family's member list returns exactly the Members whose `familyDUID` matches.
- Tenant parameters are validated: an organization ID in a search body or path
  other than the synthetic organization's gets 404.
- Malformed parameters get 400: a page size outside 1 to 500, a page position
  below the first page, a non-numeric paging field, a change-feed request
  missing either date or with `StartDate` after `EndDate`, or a POST body that
  is not a JSON object.
- A Family's member list uses Member-object field names (`birthdate`, `sex`)
  plus `cellPhone`, as the household reader copies them; the contact list uses
  its own (`dateOfBirth`, `gender`, `cellPhone`).

### Change feed

The real `FamilyChangeList` has returned an empty array for every query made
from Production so far (494 of 494; a bug report to ParishSoft is pending, see
[#465](https://github.com/epiphany40223/parishkit/issues/465) and the
[API analysis](../../../parishsoft-api-analysis.md)), so the fake matches
reality by default. The configuration file's `change_feed` selects:

- `empty` (default): `families/change/list` always returns `[]`. Delta
  refreshes therefore reload nothing, and the late-added Family reaches the
  application through the next full refresh, as it would in Production.
- `synthetic`: the feed holds exactly one row, the late-added Family's, with
  `logDate` equal to `release_at`, served only once that instant has passed.
  A delta refresh whose window covers the release picks the Family up; the
  seeder can use this mode to exercise the delta path. There is no other
  history: the generator writes no change rows.

In both modes the fake validates `StartDate` and `EndDate` as the client's
rules require (both present, ISO dates, not inverted) and filters by the
request's window alone; the clock decides only whether the late Family's row
exists (`synthetic`), never which rows a window selects. It enforces no
server-side window limit, since the real API documents none.

## Synthetic parish

`synthetic_parish.py` generates one fictional parish from the configuration
file's `seed`, `families` and `anchor_date`. The same inputs MUST produce
byte-identical fake responses. It contains no real names or addresses: names
come from fixed built-in lists and every email address is at `@example.test`.

Decision (Administrator, 2026-10-03, recorded on #476): the parish defaults to
**100 Families** (`families: 100`), small enough to seed quickly and to read
whole lists by eye. `up --families N` (and the generator's `--families N`)
sets another size for occasional scale testing; about 1,100 matches the real
parish. The default parish has 25 Ministries with rosters, also an
Administrator decision; larger parishes scale up from it.

Every other collection scales with the Family count `N`, with a floor where a
proportional count would be too small to exercise the feature. The ratios
reproduce the earlier 1,100-Family design at `N = 1,100`. Counts are rounded to
the nearest integer; tests pin the exact values for a fixed seed.

| Collection | Rule | Default (`N = 100`) | At `N = 1,100` |
| --- | --- | --- | --- |
| Members | Family sizes 1 to 5: 20% one, 30% two, 20% three, 20% four, 10% five (mean 2.7); about 30% children | about 270 | about 2,970 |
| Inactive or non-Parishioner Families | 8% of `N` | 8 | 88 |
| Deceased Members | 2% of Members | about 5 | about 59 |
| Families whose heads have no email | 10% of `N` | 10 | 110 |
| Family groups | fixed catalog | 6 | 6 |
| Family workgroups | max(4, 8N / 1,100) | 4 | 8 |
| Member workgroups | max(5, 10N / 1,100) | 5 | 10 |
| Ministries with rosters | max(25, 40N / 1,100); rosters drawn only from active non-child Members, about 40% of whom are on at least one Ministry (see below) | 25 | 40 |
| Funds | max(4, 6N / 1,100) | 4 | 6 |
| Pledges | 0.64 N | 64 | 704 |
| Contributions over the 15 months ending on the anchor date | 13.6 N | 1,360 | 14,960 |

**Members.** Each Family has one or two heads with `memberType` `Head`, or
`Husband` and `Wife`; these are the values `parishsoft.HEAD_MEMBER_TYPES`
recognizes. Children have `memberType` `Child`, the value existing source tests
use, and a `birthdate` under 18 years before the anchor date. Other adults
(older children living at home, relatives) have `memberType` `Other` and a
`birthdate` at least 18 years before it. About 30% of Members are children,
and no single-Member Family is a child. A Member counts as a child if its
`memberType` is `Child` or its age at the anchor date is under 18.

**Ministry rosters** draw only from active non-child Members: heads, spouses
and other adults, never a child.

- About 40% of non-child Members are on at least one Ministry. Of those,
  about 30% are on two or more and about 10% on three or more.
- About 5% of roster entries are ended (they have an end date before the anchor
  date).
- Every Ministry has at least one active Member.
- These proportions hold at any `--families` size.

The parish also includes heads with several semicolon-separated addresses, a
few data-quality cases such as a blank mailing name or envelope number 0, and
exactly one held-back late-added Family at every size. That Family is in
addition to `families`, so the initial load sees exactly `families` Families.
It is a couple with one child, new to the parish: it has no giving, workgroup
or Ministry rows, and it is the only record the fake's
[change feed](#change-feed) can ever report. The generator writes no
change-feed history (see that section for why).
At very small sizes (fewer volunteers than Ministries) the first roster round,
which gives every Ministry a leader, puts more volunteers on two or more
Ministries than the stated share.

## Fake clock

The seeder builds history by time travel, not by rewriting rows (Administrator
decision, 2026-10-03, recorded on
[#476](https://github.com/epiphany40223/parishkit/issues/476)). In LOCAL, and
only there, the application containers and the PostgreSQL and Valkey containers
can run under [libfaketime](https://github.com/wolfcw/libfaketime) and read one
shared clock that the operator script controls.

- **Images.** A local-only image layer adds libfaketime. It is built inside the
  VM `FROM` each image it extends: the locally built application image, and the
  pinned PostgreSQL and Valkey digests. The derived images are tagged
  `parishkit-stewardship-local-faketime-<base>:<base digest or local tag>`.
  Production images, their digests and the production Dockerfile output are
  unchanged. LOCAL's image rule (see [origin](#origin-proxy-and-image)) also
  admits these derived tags. libfaketime is inert unless it is preloaded.
- **Fake-clock mode.** LOCAL renders a Compose override, `compose.faketime.json`,
  that sets these on every application service, `postgres` and `valkey`:
  - `LD_PRELOAD` set to libfaketime;
  - `FAKETIME_TIMESTAMP_FILE` pointing at `/run/parishkit-clock/offset`;
  - `FAKETIME_CACHE_DURATION=1`;
  - `FAKETIME_DONT_FAKE_MONOTONIC=1`.

  The override adds exactly one mount to each of those services:
  `run/local/clock/`, read-only. `service_boundaries.validate_mounts` admits
  exactly that path, read-only, only when the profile is LOCAL, and refuses it
  for every other profile; fast tests cover both directions. **Normal mode**
  renders without the override. Moving between modes is a restart of those
  services with or without the override.
- **Persisted mode.** The current mode is a marker file,
  `run/local/clock/mode`, containing `fake` or `normal`. Every command that
  starts services reads it and applies the override when it says `fake`: `up`,
  `down` followed by `up`, a VM restart, `deploy`'s local mode and `reset`. A
  missing marker is an error, not a default.
- **One shared clock.** The file holds a single offset from real time, such as
  `-1468800` (libfaketime's relative form, in seconds), so fake time runs at
  real speed from wherever the offset puts
  it. Every faked process reads the same file. After writing the file, the
  operator script waits two seconds, longer than the one-second cache, before
  it acts.
- **Forward only.** The script computes each new offset as target minus real
  now, and refuses any offset whose target is earlier than the current fake
  time. Because fake time never runs ahead of real time, every recorded
  timestamp is at or before real now.
- **When each mode applies.** An unseeded deployment always runs in fake-clock
  mode, at the offset `up` sets (below). Its pages show dates about 17 days in
  the past. A seeded deployment runs in normal mode.
- **Not faked.** Caddy, Mailpit and `fake-parishsoft` run on real time. TLS
  certificate validity is checked by Caddy and the browser on real time, and no
  faked process validates a certificate: Mailpit SMTP and the fake ParishSoft
  are plain connections. Mailpit therefore stamps messages with real time.
  `fake-parishsoft` compares `release_at` against real time.
- **Timers.** Monotonic clocks stay real (`FAKETIME_DONT_FAKE_MONOTONIC`), so
  sleeps, subprocess timeouts and work budgets behave normally. Valkey key
  expiry and PostgreSQL `now()` both follow the shared clock, so TTLs, leases
  and database timestamps agree.
- **Jumps.** A forward jump can make a held database lease look expired, so the
  seeder jumps only when work has [settled](#settled) and no task holds one.
  Container health is unaffected: probe heartbeat files record monotonic time,
  which libfaketime leaves real. After each jump the seeder waits for the
  scheduler signal described under [settled](#settled) before it acts. A jump
  can also make an in-flight statement with a timeout fail, such as a health
  probe's two-second `statement_timeout`. Settled work does not cover probes,
  so a probe may fail once around a jump; that is expected and is retried.
- **Browser cookies.** In fake-clock mode Django computes cookie `Expires`
  dates in fake time, about 17 days in the past. Browsers honour `Max-Age`
  over `Expires`, so sessions still work. PR 5b MUST verify this for the Admin
  and Family cookies.

## Seeded campaign and responses

A seeder gives the local environment a campaign in progress with realistic
Family activity, so reports, progress pages and the planned response reporting
([#477](https://github.com/epiphany40223/parishkit/issues/477)) have a
meaningful data set. It requires a completed setup wizard and reuses the
wizard's first campaign, rather than creating a second one. The seed,
`families`, `--response-scale` and `now` fully determine the timeline (see
[determinism](#seed-determinism)).

**Design.** Every seeded row is written by production code at its fake
instant: the real scheduler, worker, `mail-dispatch` and web code, under their
existing identities. There is no re-timing step, no trigger bypass, no
superuser session, no special go-live handling, and every guard stays active.
Timestamps, expiries, deadlines and daily facts are therefore consistent with
one another. Daily facts are produced by the real code on their own
campaign-local dates.

### Seeder phases and identities

The seeder is `pk-stewardship local-seed <step> [arguments]`. Every step
refuses unless the profile is LOCAL, and refuses before opening any connection.
Each step runs as a one-shot container under an existing identity, with exactly
the mounts that identity's service already has in fake-clock mode. No step adds
a login, role or grant. The operator script reads `seed`, `families` and
`anchor_date` from the [fake configuration](#fake-configuration) and passes
them to each step as arguments, together with `now` and `--response-scale`.
The long-running scheduler, worker and `mail-dispatch` keep running in
fake-clock mode throughout, so occurrences fall due naturally.

| Order | Phase | Clock | Identities | Work |
| --- | --- | --- | --- | --- |
| 1 | prepare | Friday before the start Saturday, 09:00 | configuration installer, `worker`, `web` | Configure the campaign, meet [go-live readiness](#go-live-under-the-fake-clock) and take it to Production (`scheduled`) |
| 2 | drive | stepped through the timeline | `web` for Family steps; scheduler, worker and `mail-dispatch` running normally | Carry out every event before `now` in [historical order](#historical-ordering) |
| 3 | check | (services stopped) | offline `migration` | Run the [invariant check](#post-seed-invariant-check) |
| 4 | finish | real time, normal mode | `worker` | Restart without the preload, then a real refresh that releases the late-added Family |

The clock's starting point must not be earlier than any row already in the
database. `up` therefore runs the whole install and the setup wizard in
fake-clock mode at an offset of 17 days behind real time. The Friday before
any seed's start Saturday is at most 16 days before that seed's `now`, so
phase 1's jump is always forward, including after `reseed` restores the
post-setup snapshot.

### Go-live under the fake clock

Phase 1 runs at the Friday before the start, while the campaign is still in
the future:

1. Set the clock to the Friday before the start Saturday, 09:00 campaign time.
2. Apply the campaign's real dates and every Family mail schedule through one
   configuration change request under the configuration installer: the
   Initial at Saturday 10:00, and Reminders Tuesday and Thursday 09:00 through
   the Monday end. Wait until no configuration request is pending.
3. Meet the readiness prerequisites with real code:
   - a full refresh against the fake, so the source is current within
     `source_stale_seconds`;
   - a successful Family test mail to Mailpit;
   - current provider-check receipts for `parishsoft` and `google_workspace`
     (the fake and the mail catcher).
4. Run the real go-live, in the same order the Admin pages use:
   `go_live_commands.verify_preview` and `start_cleanup`; the worker's cleanup
   and link-preparation tasks; then `confirmation_commands.verify_preview` and
   `confirm`, which enqueue the worker's real activation task.
   - The commands run under `web` with a real Django request object. It
     carries an Admin session that `authenticated_admin` admits, created and
     made fresh through the shared identity core of the
     [local test sign-in](#local-test-sign-in).
   - Each verify step is followed immediately by its act step, well inside the
     300-second preview window (`PREVIEW_SECONDS`). The clock does not jump
     between them.
   - Because the start is in the future, activation selects `scheduled`, and no
     activation catch-up runs.

### Historical ordering

The generator produces one timeline of intended events before `now`: the
campaign start boundary, the Saturday 10:00 Initial, each Family's sessions,
form baselines, presence steps and submissions, and each Tuesday and Thursday
Reminder. Phase 2 visits them in order of intended instant:

- **Occurrence instants** (start boundary, Initial, each Reminder, and each
  campaign-local midnight for daily facts and digests): wait until work has
  [settled](#settled), jump to the instant, and wait for the resulting work to
  settle. The scheduler plans and the worker and `mail-dispatch` prepare and
  send exactly as in Production, and current state decides eligibility. A
  Family that submitted before a Reminder is not sent it. A Family that
  submitted before its invitation was prepared has its invitation skipped.
- **Family events:** wait until work has settled, jump to the instant, and call
  the real web entry point under `web` for that Family's session, form
  baseline, presence step or submission. The seeder settles before every jump,
  including between consecutive Family events, because receipts and other
  results are asynchronous and their tasks hold leases.
- **Late instants.** Fake time advances at real speed while work runs. If it
  has already passed an event's intended instant, the seeder runs that event
  at once rather than jumping back, so recorded instants may trail the
  intended ones by seconds or minutes. Order is always preserved.
- **Seeded now.** Phase 2 visits every event whose intended instant is at or
  before the requested `now`. The **seeded now** is the fake time at which
  phase 2 ends. It is never later than real time, and the
  [invariant check](#post-seed-invariant-check) uses it. Timeline tests use the
  requested `now`.

PR 5b MUST also verify that Family delivery is not held by source staleness
across jumps. The scheduled refreshes that fall due at each jump are expected
to keep the source current. If delivery is held anyway, phase 2 runs a full
refresh before the affected occurrence instants.

PR 5b MUST verify that occurrences falling due one at a time under the fake
clock are processed singly, without missed-work coalescing. If not, that is a
design question for #476.

### Settled

Work has **settled** when all of the following hold. The checks read the real
schema:

- no `TaskRun` is `queued`, `running`, or `retry_wait` with `not_before` at or
  before the current fake time, for the task types that map to the general,
  mail and scheduler-produced work. The seeder uses the code's task-type to
  queue mapping, since `TaskRun` has no queue column;
- no outbox message is `pending`, `submitting`, or `retry_wait` with
  `not_before` at or before the current fake time;
- every schedule occurrence due at or before the current fake time is
  `succeeded` or `skipped`;
- **scheduler signal:** the scheduler's probe heartbeat file has been written
  at least twice after the monotonic instant at which the seeder wrote the
  jump. The scheduler writes the heartbeat once per loop, at the end, so a
  loop that began before the jump can write the first one; the second proves a
  whole planning loop ran under the new time. The monotonic clock is real and
  shared by every container in the VM.

The seed fails immediately, without waiting, if any of these appears:

- an occurrence in `delivery_unknown`, `coalesced` or `failed`;
- an outbox message in `delivery_unknown` or `permanent_failure`;
- a `TaskRun` in `failed` or `abandoned`.

Each wait has a limit (10 minutes by default). On expiry the seeder stops, and
it records which condition was unmet, the limit and the elapsed time in its log
before it exits.

### Post-seed invariant check

Phase 3 stops the online services for the mode switch. It then runs, under the
offline `migration` identity, a `DO` block that raises unless:

- parent and child timestamps are monotone: each occurrence is at or before
  its fulfillment, which is at or before its delivery; each Family session is
  at or before its form baseline, which is at or before its submission, which
  is at or before its receipt;
- one daily-fact row exists for each elapsed campaign-local day;
- per-table row counts equal the counts the timeline implies;
- no timestamp is later than the [seeded now](#historical-ordering), except
  rows written in phase 4 (the late-added Family's promotion and invitation,
  and anything after it) and the future-by-design columns on an explicit
  allowlist kept with the seeder: future occurrence and schedule
  instants, the campaign end date and closing boundary, and expiry and
  deadline columns (token, session, code and export expiries, and retention
  due dates).

### Seeding speed

At the default 100 Families, phase 2 dominates. It sends about 260 to 430 past
messages at the measured rate of about 46 a minute under the global work-order
lock, about 6 to 9 minutes. It makes about 100 Family-event jumps, each with a
settle wait, the two-second cache wait and the scheduler signal, at 5 to 8
seconds each, about 8 to 13 minutes. About 20 occurrence and midnight settle
points add 10 minutes or less. Phases 1, 3 and 4 add 2 to 3 minutes. The whole
seed is therefore estimated at 20 to 30 minutes. The earlier 5-minute target no longer
applies: the seed runs in the background (see [operator script](#operator-script)),
and set-based preparation with a lock-free send loop
([#447](https://github.com/epiphany40223/parishkit/issues/447)) would remove
most of the message time. A programmatic interface
([#463](https://github.com/epiphany40223/parishkit/issues/463)) would not help,
because it would call the same per-message code under the same lock. The
operator script prints each step's start time, fake time, elapsed real time and
row counts.

Real paths also stay exercised after seeding: future Reminders, and the
late-added Family's invitation requested by phase 4, are prepared and sent in
normal mode once the services restart.

### Campaign calendar

All dates and times are in the campaign timezone. "Now" is `--now`, defaulting
to the current time.

- **Start.** The campaign starts on the most recent Saturday at least 8 days
  before now's local date, so 8 to 14 days have elapsed.
- **Initial invitation.** One Initial schedule at 10:00 on the start Saturday.
- **End.** The campaign ends on the Monday 30 days after the start Saturday.
  The campaign therefore spans 31 local days (about a month), with 16 to 22 days
  remaining (about three weeks) and roughly a third of the time elapsed.
- **Reminders.** One Reminder schedule at 09:00 every Tuesday and Thursday from
  the first Tuesday after the start through the last Thursday before the end
  (eight Reminders).
- **Past versus future.** Phase 2 lets every occurrence before now fall due
  under the fake clock. Every occurrence at or after now is left for the real
  scheduler in normal mode.

### Response pattern

Responses follow the shape parishioners actually show. Counts are proportions
of the Portal-eligible Families (`E`), so the shape holds at every size. The
Administrator's "about a third" described elapsed campaign time, not the
share of Families responding. The opening weekend matches reality at about 5%
of `E`; the real launch tracked at about that level. `--response-scale m`
(default 1) multiplies every rate for denser report-testing data, capped so
that no more than 90% of `E` respond.

- **Opening weekend.** 5% of `E`: 2% on the start Saturday (after the 10:00
  invitation) and 3% on Sunday.
- **Monday.** Somewhat lower: 1.5%.
- **Taper.** 1.0% on Tuesday, declining linearly to 0.4% a day by the end of
  the second week and staying there.
- **Reminder upticks.** The 24 hours after each past Reminder carry 1.6 times
  the taper baseline for that window, and the following 24 hours 1.2 times.
- **Total by now.** The cumulative share submitted before today, at
  `--response-scale 1`, by days elapsed:

  | Days elapsed | 8 | 9 | 10 | 11 | 12 | 13 | 14 |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | Share of `E` | 12.4% | 13.1% | 13.7% | 14.6% | 15.3% | 16.0% | 16.5% |

  Today's activity up to now adds to it. At the default size (`E` about 92)
  that is about 11 to 15 submitted Families.
- **Allocation and floors.** Daily counts come from these rates by
  deterministic largest-remainder rounding, not random sampling. The floors
  are at least one submission in each complete 24-hour window after a past
  Reminder, and at least one session or submission today before now. They are
  applied inside the allocation: a submission a floor adds is taken from the
  nearest later day without a floor, so the cumulative totals in the table
  still hold. The seed chooses only which Families respond and at what times.
- **Times of day.** Mostly 07:00 to 22:00, with an after-Mass bump on Sunday
  late morning (about 10:30 to 13:00).
- **Funnel.** For #477's funnel, using the data that already distinguishes
  each stage: link followed (a Family session's `authenticated_at`), form
  opened (a form baseline row), progressed (`presence_section` past `welcome`)
  and submitted (a live submission and its receipt). Counts MUST be ordered
  link followed ≥ form opened ≥ progressed ≥ submitted. By default, link
  followed is 1.7 times submitted, form opened 1.4 times and progressed 1.2
  times, each capped at `E`. Include link-only sessions that look like
  prefetches, Families who stopped partway through the form, and submitted
  Families.
- **Answer content.** Varied submissions: pledges across the synthetic parish's
  realistic amount range, Ministry interest, census edits such as a proposed new
  Member or a changed email, a few email opt-outs, and occasional additional
  information text.
- **Edge cases.** At least:
  - re-submissions (later versions) from 3% of submitting Families, with a
    minimum of one;
  - one Family that submitted on the start Saturday before its invitation was
    prepared, so its invitation occurrence was skipped;
  - one Portal-eligible Family with no eligible email;
  - one late-added Family that phase 4 promotes, which then receives its
    invitation, live, through the
    [refresh reconciliation](../data/spec.md#parishsoft-refresh-reconciliation)
    path.
- **Today and nothing after now.** Activity includes now's local date, and no
  seeded timestamp is later than the [seeded now](#historical-ordering),
  except phase 4 rows and the allowlisted future-by-design columns (see
  [invariant check](#post-seed-invariant-check)).

### Mail history

Mailpit keeps the copies of past mail that the real paths sent during seeding.
They carry real-time stamps, because Mailpit is not faked. Past messages also
exist as outbox and delivery rows with their fake-time instants, and those
rows are what reports, progress pages and #477 read. The Administrator left
the choice to the spec
([#476](https://github.com/epiphany40223/parishkit/issues/476)); it keeps the
copies and tags them. After the seed, the operator script tags every message
received during the seed with `seed-history` through Mailpit's API. The tag is
local to Mailpit and does not change the messages, and it lets a developer
filter them out to see only mail sent after seeding.

### Seed determinism

The same seed, `families`, `--response-scale` and `now` MUST produce the same
timeline: the same Families chosen for each stage, the same intended instants,
the same answers and the same message set. Recorded instants may trail their
intended instants by the processing time described under
[historical ordering](#historical-ordering). Values that production code
generates from cryptographic randomness (Family codes, access tokens, keys)
and database surrogate keys are compared by role, not value.

### Seeder tests

**Timeline tests** (no Docker and no database; run in CI at 100 and 1,100
Families) pin `now`, the seed and the size, and assert that:

- the start is a Saturday, the end a Monday 30 days later, Reminders fall only
  on Tuesdays and Thursdays, and about a third of the campaign has elapsed;
- no event is later than now, and today has at least one session or submission
  before now;
- Sunday has more submissions than Monday, Saturday at least as many as
  Monday, and Monday at least as many as the median later day without a
  Reminder. At 100 Families this is checked with `--response-scale 4`, so the
  counts are not all zero or one;
- each complete 24-hour window after a past Reminder has at least one
  submission and at least the baseline rounded up. At 1,100 Families it has at
  least 1.25 times the baseline;
- submissions before today equal the cumulative share in the table times `E`,
  after the stated rounding with floors applied inside the allocation, for each
  elapsed-day value;
- the funnel is ordered;
- two runs with the same inputs produce identical timelines;
- the Friday 09:00 starting instant is later than `now` minus 17 days for
  every weekday and time of day of `now`, so phase 1's jump is always forward.

**Seed tests** (database and real paths under the fake clock, run in the VM
as a documented human run at 20 Families and at the default 100, never in CI)
assert that:

- the [invariant check](#post-seed-invariant-check) passes, and raises on a
  deliberately corrupted row;
- the historical-ordering cases hold: no Reminder is sent to a Family that
  submitted before it, and the early responder's invitation is skipped;
- every message during and after seeding, including Family, digest, security
  and operational mail, was sent only through the mail-catcher transport;
- go-live reached `scheduled` with no activation catch-up, and the campaign
  became `active` at its start boundary;
- the deployment is in normal mode at the end, with an offset of zero.

The schema's guard and function SQL contains no LOCAL branch; that test stays
under [production invariants](#production-invariants).

## Local test sign-in

Because LOCAL has no Google sign-in, a local-only route signs an Admin in.

- **Minting.** `pk-stewardship local-sign-in --config
  /opt/parishkit/config/services/web.yaml --email E`, run with
  `docker compose exec web` (the web service's own configuration, as the
  other in-container operator commands take), refuses unless the profile is
  LOCAL and the configured origin's host is `localhost`; it checks both
  before contacting Valkey. A missing option is a usage error naming it. It creates a random 256-bit token and stores, under
  `stewardship:auth:v1:local-sign-in:<sha256 of token>` with a 120-second
  expiry, the email and the current revocation epoch (no schema change). It
  prints `https://localhost:8443/admin/local/sign-in#<token>`. The token is in
  the fragment so it never reaches access logs or a `Referer` header.
- **Route.** The route lives in `local_urls.py`, which only LOCAL's
  `configure_web` selects; `urls.py` never imports it.
  - `GET` serves a CSRF-protected page. A static script file (inline script is
    not allowed by the content security policy) moves the fragment into a form
    field. A GET never consumes the token, so link prefetching cannot use it
    up.
  - `POST` re-checks the profile and that the request's `Host` matches the
    `localhost` origin, then consumes the token atomically with a Lua script
    (`EVAL`: read and delete in one step). The web Valkey ACL, which grants
    `EVAL` but not `GETDEL`, is not changed.
  - It then signs in through the same code as Google sign-in. The post-OAuth
    core of `accounts.authentication.complete_identity` MUST be refactored
    into one function that both paths call: the identity rate limiter, the
    recovery-epoch check (against the epoch stored with the token), `PortalUser`
    get-or-create and the disabled check, `reauthenticate_admin` or
    `issue_admin`, the session-guard `IntegrityError` handling and
    `rotate_token`. The local path MUST NOT call `issue_admin` or
    `reauthenticate_admin` directly. Sessions, audit, login rules and step-up
    therefore behave as after Google sign-in (see
    [identity and session security](../architecture/spec.md#identity-and-session-security)).
  - The subject stored in `PortalUser.google_subject` is
    `local-test:<normalized email>`, which cannot collide with Google's
    numeric subjects. The email must still satisfy the deployment's login
    rules. A local sign-in carries no hosted-domain evidence (there is no
    Google `hd` claim), so only email rules admit it; domain rules do not.
  - The route is one of the access gate's authentication routes, so it
    works before setup completes and during restore review, when the
    developer needs it to reach the setup wizard. In LOCAL the login page
    and the fresh-authentication prompt describe the operator command
    instead of offering Google; a new link opened in a signed-in browser
    is a step-up of that session in place.
- **Where the protection comes from.** The real guard is reachability: Caddy
  publishes only on the VM's loopback and Lima forwards only to the laptop's
  loopback. The `Host` check is defence in depth; behind Docker's published
  port and Lima's forward, the client address is not reliably loopback, so it
  is not checked.
- **Exception to the Google-only rule.** This is the sole non-Google
  authentication endpoint, and only in LOCAL; the architecture's prohibition of
  non-Google endpoints otherwise stands.

**Absence guarantees.** Tests MUST prove that:

- `configure_web` selects `local_urls` for LOCAL and the normal URL
  configuration for every other profile;
- no production URL resolves to `local_urls` or its view;
- calling the view directly outside LOCAL returns 404;
- the command refuses a production configuration before touching Valkey;
- rendered Production Compose and Caddy files contain no local service, port
  or route.

## Operator script

`tools/stewardship-local.sh` drives the environment from the laptop. It
requires Lima (`brew install lima`), git, tar and ssh, and runs under macOS's
own bash 3.2. It packs the checkout, uploads its VM half
(`tools/stewardship-local-vm.sh`) over ssh and runs it inside the VM as
root; that half does everything else, with `-p parishkit-local` on every
Compose command. The Lima instance name defaults to `parishkit-local` and
`PARISHKIT_LOCAL_VM` selects another existing instance. The
[developer guide](../../../guides/stewardship-local-environment.md) walks
through the commands, the first bring-up and the documented VM run; this
section is the contract.

| Command | Effect |
| --- | --- |
| `vm create` | Create the `parishkit-local` Lima instance with the [VM settings](#host-virtual-machine) and install Docker Engine |
| `vm start` / `vm stop` | Start or stop the VM |
| `up [--families N]` | First-time install (below), with a synthetic parish of `N` Families (default 100) |
| `deploy` | Rebuild from the checkout and upgrade the running deployment (below) |
| `snapshot [--seeded]` | Stop the services and save the runtime root as the post-setup snapshot, or with `--seeded` as the seeded snapshot, preserving numeric ownership and modes, then restart |
| `reset [--seeded]` | Restore the post-setup snapshot and restart in fake-clock mode, or with `--seeded` the seeded snapshot in normal mode (each snapshot carries its clock-mode marker); with no post-setup snapshot, delete the root and run `up` again |
| `reset --reinstall` | Delete the root and run `up` again from the checkout even when a snapshot exists (snapshots are kept); the stand-in for `deploy` until it lands |
| `seed [--response-scale m]` | Seed the current deployment at the current time (below) |
| `reseed` | Restore the post-setup snapshot, then `seed` |
| `status` | Show the VM, service health, Docker disk use and VM disk use |
| `down` | Stop the services; never removes data |
| `start` | Start a stopped deployment's services in the recorded clock mode |
| `sign-in --email E` | Print a [test sign-in](#local-test-sign-in) link |
| `ca` | Copy Caddy's local root certificate to the laptop and print how to trust it |

**`up`.** It installs a LOCAL deployment from the checkout by the
[deployment runbook](../../../guides/stewardship-deployment-runbook.md#first-installation)'s
first-installation steps, with the `arm64` image built inside the VM under a
[local tag](#origin-proxy-and-image), the LOCAL deployment YAML and a
deployment record beside it (UUID, Administrator, image, synthetic-parish
inputs), the marker file (written as soon as provisioning, which requires an
empty root, has completed), the sentinel OAuth client and the
[fake configuration](#fake-configuration). It ends with the services healthy
and the application answering the public origin through Caddy, and prints the
sign-in command and the values the setup wizard asks for: the fake ParishSoft
key and organization and the mail-catcher document, which the wizard's
credential step installs through the real installers. The services start with
debug logging on by default (`PARISHKIT_LOCAL_DEBUG_LOGGING=0` turns it off),
as pre-launch dev deploys did. Until the image carries the
[fake-clock](#fake-clock) override, `up` records `normal` clock mode; with it,
`fake` at 17 days behind real time. The step-by-step account is in the
[developer guide](../../../guides/stewardship-local-environment.md). The developer then completes the
real setup wizard, since that is part of what the environment tests. `up` also
builds the [fake-clock](#fake-clock) images and does all of this in fake-clock
mode, with the clock 17 days behind real time, so that a later seed can start
its clock forward of every existing row.

Decision: `up` cannot seed before the interactive setup wizard is complete; it
waits, then (on confirmation) verifies setup, takes the post-setup snapshot,
runs `seed`, then `snapshot --seeded`. Declining leaves an unseeded deployment
that `seed` can seed later.

**`seed`.** It refuses unless setup is complete and the campaign has not been
seeded since the post-setup snapshot, so campaigns never stack. It clears
Mailpit (removing the wizard-era sample messages), runs the
[seeder phases](#seeder-phases-and-identities) in fake-clock mode at that `now`
(passing `--response-scale` through), then restores normal mode: offset zero,
the clock-mode marker set to `normal`, services restarted without the preload,
and `release_at` set for the final refresh. Last, it tags the seed's mail in Mailpit (see
[mail history](#mail-history)). It is safe to run in the background: it takes
a lock so only one seed runs at a time, prints one progress line per step with
fake time, elapsed real time and row counts, and appends the same lines to
`~/.parishkit-local/seed.log`. If any step fails it stops, leaves the
deployment in fake-clock mode (an unseeded deployment's mode) with services
running, and exits non-zero. `reseed`
restores the post-setup snapshot (which also restores the fake configuration)
and then runs `seed`.

**Snapshots and fast reset.** Snapshots are uncompressed copies of the runtime
root inside the VM, under `/opt/parishkit-snapshots/<name>` together with the
deployment YAML and record, so a restore is self-contained. `reset --seeded`
stops the services, restores the seeded
snapshot with `rsync --delete` preserving numeric ownership, modes and hard
links, and restarts them; it SHOULD take seconds plus service start-up. Because
the seed is anchored to the time it was generated, a restored seeded snapshot
keeps its original dates and grows older each day. Re-seed (`reseed`, then
`snapshot --seeded`) only when fresh dates are needed; otherwise reset to the
seeded snapshot.

**Trusting Caddy's CA.** The browser sees a certificate from Caddy's internal
CA. `ca` copies the root certificate from Caddy's data store in the runtime
root (`run/persistent/caddy/data/caddy/pki/authorities/local/root.crt`, the
container's `/data/caddy/pki/authorities/local/root.crt`) to
`~/.parishkit-local/caddy-root.crt`, after checking that it is a certificate,
and prints the macOS command to trust it in the login keychain. The script
never changes trust settings itself; accepting the browser's certificate
warning also works. A reinstall creates a new CA, which must be trusted again.

**`deploy`.** It depends on the scripted Production upgrade work (#460, #461):
that upgrade script's host part moves into a host script with a `production`
and a `local` mode, and `deploy` runs its `local` mode. Production output MUST
stay byte-identical. Local mode differs in exactly these ways:

- it builds the image inside the VM from the packed checkout under a local tag
  and skips the registry push, pull and digest check;
- it uses project `parishkit-local` and the LOCAL rendering;
- it requires the pre-upgrade backup to succeed (not best effort), accepting
  "off-site copy not configured";
- it skips the refusal after Production activation, since a seeded LOCAL
  campaign is active by design;
- it restarts services in the mode recorded by the
  [clock-mode marker](#fake-clock), applying the fake-clock override when the
  marker says `fake`.

**Safety rules.**

- Every destructive command acts only inside the VM, and only on a root that
  carries the marker file `/opt/parishkit/.parishkit-local`. A root without it
  is refused.
- No command removes Docker volumes or `run/persistent`; `down` never passes
  `--volumes`. Restoring a snapshot replaces the root only after the services
  are stopped.
- `reset` without a snapshot, and any other deletion of the root, requires the
  operator to type the instance name (`parishkit-local`) to confirm.
- The script never targets a host other than the Lima alias.

## Testing requirements

Normal CI stays Docker-free and credential-free, as the
[automated tests](../operations/spec.md#automated-tests) section requires.
The local environment adds:

- **fast tests** for the [branch table](#existing-profile-branches), origin,
  image-tag pattern, `behind_proxy`, credential-type refusal in both directions
  at every intake, helper `profile` fields and allowlists, and the LOCAL
  `localhost` rule in the public-web-address check with every other profile
  unchanged;
- **golden-file tests** that the rendered Production Compose document and
  Caddyfile are byte-identical before and after, and that rendered grants and
  the Valkey ACL are identical across profiles;
- **rendering tests** that run the LOCAL renderer in CI and check the local
  topology: only loopback publications, no `application-egress`, `ingress`
  without masquerade and joined only by Caddy and Mailpit, the two extra
  services, a local image tag and no GHCR reference;
- **fake ParishSoft contract tests**: a fixed seed produces a fixed digest, an
  in-process fake passes the strict `CoherentParishSoftClient` loaders, a wrong
  key gets 401, and the late-added Family appears only after `release_at`;
- **generator tests**: the scaling table at 100 and 1,100 Families; the
  adult and child mix and `memberType` values; Ministry rosters with no child,
  about 40% of non-child Members on at least one Ministry (about 30% of those
  on two or more, about 10% on three or more), some ended entries, and every
  Ministry with an active Member, at both sizes;
- **timeline tests** at 100 and 1,100 Families, with no Docker or database
  (see [seeder tests](#seeder-tests));
- **database tests** (no fake clock): setup loading runs against the
  in-process fake; the check that guard and function SQL has no LOCAL branch;
- **fast tests** for the clock-mount admission: admitted exactly, read-only,
  only for LOCAL, and refused for every other profile;
- **rendering tests** for the fake-clock override: it sets the preload only on
  application services, `postgres` and `valkey`, adds only the read-only
  clock mount, and is absent from normal mode and from every non-LOCAL
  rendering;
- **fake-SMTP tests** for the mail-catcher transport, with the Gmail endpoint
  unchanged;
- **absence tests** for the [local test sign-in](#local-test-sign-in);
- `shellcheck` on `tools/stewardship-local.sh`.

No Docker end-to-end run of the local environment belongs in CI, and neither
do the [seed tests](#seeder-tests). A developer runs them and verifies the full
stack in the VM, including the no-egress check, before a pull request that
changes it, following the documented VM run.

The delivery plan is
[OPS-10](../../../plans/stewardship/operations.md#ops-10-local-laptop-environment),
which refines the PR sequence in #476.

## Non-goals and rejected alternatives

- **Docker Desktop bind mounts.** Rejected: VirtioFS does not store ownership
  (see [host virtual machine](#host-virtual-machine)).
- **Reworking the topology around named volumes** to suit Docker Desktop.
  Rejected: a large divergence from Production in the very code being tested.
- **Real data or backups.** The local environment never loads a production
  backup or real ParishSoft data; it uses only the synthetic parish.
- **A second, real Google OAuth client** registered for `localhost`. Rejected:
  it would put a real credential in a test environment and require Google
  egress; the local test sign-in uses the same session path instead.
- **The Phase 0 Compose scaffold.** Rejected for running the real application:
  it is HTTP-only with no proxy and does not exercise ingress, credential
  installers or ownership.
- **Extending `tools/stewardship-dev-deploy.sh`** to LOCAL. Rejected in favour
  of the scripted upgrade's local mode, which follows the production upgrade
  steps.
- **A set-based bulk writer of history.** Rejected: it duplicated what
  production code writes and needed a guard bypass for all of its content.
- **Real paths plus a local-only re-timing bypass** (a superuser session with
  `session_replication_role = replica` moving timestamps afterwards). Rejected
  for the [fake clock](#fake-clock):
  - go-live needs the Initial schedule before activation, which triggers bulk
    catch-up for a campaign that has already started;
  - daily facts are one row per date, so moving them needs inserts;
  - value-based re-timing is ambiguous for derived expiries and deadlines, and
    when steps overlap.
- **Real paths at the current time without backdating.** Rejected: all
  activity would be stamped today, so reports would show no history.
- **Production use.** LOCAL is never a supported way to run a parish.
- **CI end-to-end runs** of this environment are out of scope.
