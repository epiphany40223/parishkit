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
deployment?" without a shared host and without any way to email a real
Family or change ParishSoft.

It is:

- **not a production deployment.** By default it holds only the
  [synthetic parish](#synthetic-parish). The developer may choose to read
  the real ParishSoft instead (Administrator decision, 2026-10-15, on
  [#1399](https://github.com/epiphany40223/parishkit/issues/1399)); that
  real parish data may stay on the laptop but is never committed or copied
  off it, except as mail to the one [mail redirect](#safety-guarantees)
  address through a real mailbox, and inside the sealed, unreadable
  off-site backup copies ([Drive](#safety-guarantees)). Either way it can
  never email a real Family or write to ParishSoft, even after its campaign
  goes to Production (see [safety guarantees](#safety-guarantees));
- **not CI.** Nothing here runs Docker in CI. CI covers the local profile with
  fast tests, timeline tests and database tests that need no fake clock, and by
  rendering the local topology. Seed tests run in the VM (see
  [testing requirements](#testing-requirements));
- **not the Phase 0 development scaffold.** The
  [Compose scaffold](../../../development/stewardship-compose.md) (HTTP, no
  proxy, bind-mounted source) is superseded for running the real application.

**Shared code paths.** The local environment and Production share as much
code as possible, with as little LOCAL branching as possible
([#1399](https://github.com/epiphany40223/parishkit/issues/1399)). Differences
come from configuration and installed credentials, with Testing mode and the
configured mail redirect as the guard against reaching a real parishioner,
not from code that refuses or disables an integration in LOCAL. Every
remaining LOCAL branch is listed, with its reason, in
[remaining LOCAL branches](#remaining-local-branches), and a test fails when
the code and that table disagree.

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
would silently change meaning when a profile is added (the
[inventory test](#remaining-local-branches) pins the existing ones).

### Existing profile branches

Every existing profile comparison MUST be given an explicit LOCAL behaviour.
Line numbers are as of `main` on 2026-10-03 and are for orientation only.

| Location | Today | LOCAL behaviour |
| --- | --- | --- |
| `deployment._origin` (`deployment.py:260`) | PRODUCTION requires HTTPS; others require loopback HTTP | New LOCAL branch: exactly `https://localhost:8443` (see [origin](#origin-proxy-and-image)). Development and test unchanged. |
| default origin (`deployment.py:536`) | none for PRODUCTION, else `http://localhost:8000` | `https://localhost:8443` |
| proxy hops (`deployment.py:545`, `:551`) | one for PRODUCTION, else zero | one (`behind_proxy`). The "zero locally" message is reworded to name development and test. |
| authentication-limit warning (`deployment.py:628`) | PRODUCTION only | not issued (a laptop reached only on localhost) |
| `runtime_topology._image` (`runtime_topology.py:92-101`) | GHCR digest for PRODUCTION, else `parishkit-stewardship:development` | the local tag pattern only |
| source mounts (`runtime_topology.py:294`) | refused unless DEVELOPMENT | refused (unchanged; this existing `is not DEVELOPMENT` refuses, the safe direction) |
| web replica networking (`runtime_topology.py:426`) | PRODUCTION joins `proxy`; others publish web's port on loopback | joins `proxy` like Production; web publishes no host port (`behind_proxy`) |
| Caddy, Caddyfile and restart policy (`runtime_topology.py:434`) | PRODUCTION only | renders Caddy with the local Caddyfile and `unless-stopped` restarts (`behind_proxy`) |
| trusted proxy networks (`runtime_web.py:179`) | Caddy's `/32` for PRODUCTION | Caddy's `/32` (`behind_proxy`) |
| template reload (`runtime_web.py:191`) | DEVELOPMENT only | off |
| `web/security.py:57` `production` flag | secure cookies, CSRF cookie, HTTPS redirect and HSTS for PRODUCTION | secure cookies, CSRF cookie and HTTPS redirect on (`behind_proxy`); HSTS 0 (PRODUCTION only) |
| `runtime_ingress.production_hostname` (`runtime_ingress.py:15`) | refuses non-PRODUCTION | unchanged; LOCAL uses a separate local Caddyfile renderer that never calls it |
| `/app/src` mount exemption (`service_boundaries.py:250`) | DEVELOPMENT only | not admitted |
| enumerated mounts (`service_boundaries.validate_mounts`) | each role's mounts only; anything else refused | unchanged for every existing role; LOCAL adds rules only for the two local services (`fake-parishsoft`: its one configuration file, read-write for [publication writes](#fake-parishsoft-writes); `mailpit`: its store directory, read-write) |
| development reload (`runtime_process.py:45`, `:174`) | DEVELOPMENT only | off |
| existing `!= DEVELOPMENT` refusals (`services.py:25`, `cli.py:295`) | refuse outside DEVELOPMENT | unchanged (they refuse, the safe direction) |

A fast test MUST assert, for each row, that LOCAL never receives the
development or test result, and that every other profile's result is
unchanged.

### Remaining LOCAL branches

[#1399](https://github.com/epiphany40223/parishkit/issues/1399) has removed
the LOCAL branches that only refused or disabled an integration; pk-local now runs
Production's Slack, smoke, egress, ParishSoft, Google sign-in, Workspace mail
and Drive code. The branches that remain are listed here, each with the reason
it stays. A branch is any comparison with, or use of, the LOCAL profile in the
package code: `DeploymentProfile.LOCAL`, the helpers' `LOCAL_PROFILE`, or the
string `"local"` compared with, tested for membership in a set, tuple or list,
or matched by a `case`; `LOCAL` on an aliased import and
`DeploymentProfile("local")` count too. Shared `behind_proxy` tests are not
branches, because Production takes the same path. The operator scripts under
`tools/` are laptop tooling and are not listed.

The first column names each module (under `src/parishkit/`) and the top-level
functions, classes or module-level names that hold its branches. A fast test
(`tests/stewardship/test_local_branch_inventory.py`) MUST fail when the code
holds a branch in a function this table does not name, or the table names one
that holds none, so a new LOCAL branch needs a reason here before it merges.
The same test fails on a new negative test against another profile (`!=`,
`is not` or `not in` a `DeploymentProfile` member other than LOCAL), which
would give LOCAL the other branch without naming it; the four that predate
LOCAL are rows of [existing profile branches](#existing-profile-branches)
and refuse in the safe direction.

The final review for
[#1399](https://github.com/epiphany40223/parishkit/issues/1399) (2026-10-21)
checked every row below against the code: each is laptop topology, a safety
rule or a laptop tool, and none refuses or disables an integration that
Testing mode and the mail redirect guard instead.

Every remaining branch falls in one of three kinds:

- **laptop topology:** what a laptop needs that Production cannot have
  (the localhost origin, Caddy's local CA, the fake clock, the extra
  services);
- **safety rule:** a refusal in the safe direction, which keeps a LOCAL-only
  value out of Production or keeps LOCAL from writing to ParishSoft or
  mailing anyone but the redirect address;
- **laptop tool:** a command or page that exists for the developer only and
  refuses any other profile, which protects a real deployment.

Rendering Mailpit or the fake ParishSoft, the banner, or the test sign-in from
a deployment setting instead of the profile was considered and rejected: each
would make a laptop-only behaviour something a Production configuration could
switch on, and for the test sign-in that would be an authentication bypass.
The profile, which Production can never hold, stays the switch.

| Location | Branch | Kind and reason |
| --- | --- | --- |
| `stewardship/deployment.py`: `PROXIED_PROFILES`, `_origin`, `load_deployment` | One proxy hop; exactly the [localhost origin](#origin-proxy-and-image), and it is the default | Laptop topology: the browser reaches Caddy on `localhost` |
| `stewardship/web/security.py`: `browser_settings` | The invalid-origin message names the proxy, not Production | Laptop topology: wording only |
| `stewardship/origin_check.py`: `check_public_origin`; `stewardship/origin_check_worker.py`: `resolve` | Accepts exactly the localhost origin without a DNS lookup | Laptop topology: `localhost` has no public name to resolve |
| `stewardship/runtime_topology.py`: `_image`, `render_runtime`, `render_faketime_override`, `_project_name`, `_networks`, `_mailpit`, `_fake_parishsoft`, `_caddy` | The local image tags, the `mailpit` and `fake-parishsoft` services, the fake-clock override, the `parishkit-local` project, the no-masquerade `ingress` network, loopback-only Caddy | Laptop topology ([topology](#topology-and-web-differences)); Mailpit and the fake refuse any other profile |
| `stewardship/runtime_ingress.py`: `render_local_caddy`, `render_ingress` | The local Caddyfile with `tls internal` on `localhost` | Laptop topology: no public name, so no ACME |
| `stewardship/runtime_provisioning.py`: `provisioning_plan` | The fake-clock override document and the Mailpit store directory | Laptop topology |
| `stewardship/service_boundaries.py`: `_check_extra`; `stewardship/backup_boundaries.py`: `validate_backup_mounts`; `stewardship/offline_boundaries.py`: `validate_offline_mounts` | Admit the read-only [fake-clock](#fake-clock) mount | Laptop topology: the fake clock exists only here |
| `stewardship/deployment.py`: `load_deployment`, `recorded_mail_redirect` | `local_smtp_latency_ms` and `mail_redirect` are refused outside LOCAL; `parishsoft_publication` is refused in LOCAL unless it reads the fake | Safety rule: a rehearsal delay or a redirect would withhold real mail; LOCAL never writes to the real ParishSoft |
| `stewardship/mail_catcher.py`: `admit_real_workspace`, `workspace_transport`, `request_redirect`, `envelope`; `stewardship/provider_checks.py`: `check_candidate`; `stewardship/accounts/integration_views.py`: `_drive_in_local` | The mail catcher only in LOCAL; a real Workspace key, and Drive, in LOCAL only beside the [mail redirect](#safety-guarantees) on the normal clock; the redirect only in LOCAL | Safety rule: the redirect is what keeps real-data mail to one address |
| `stewardship/family_delivery_process.py`: `local_smtp_latency` | The modeled SMTP latency waits only before a mail-catcher submission | Safety rule: a real deployment never waits on purpose |
| `parishsoft_http_worker.py`: `default_base_url`, `admitted_base_url`, `contact_put_admitted` | The fake is LOCAL's default and admitted only in a local request; a local request may write a Member contact only to the fake, through the one `contact_put_admitted` rule that the deployment loader, the publication session, the Member contact smoke check and every helper request apply | Safety rule: [ParishSoft](#safety-guarantees) is read only from LOCAL |
| `stewardship/runtime_web.py`: `parse_google_client` | The sentinel OAuth client is refused outside LOCAL | Safety rule: the sentinel is not a real client |
| `stewardship/runtime_web.py`: `url_configuration`; `stewardship/accounts/local_sign_in.py`: `sign_in`, `admit_local_configuration` | The [test sign-in](#local-test-sign-in) route and command exist only in LOCAL | Laptop tool: Production's URL module never imports it |
| `stewardship/accounts/branding_context.py`: `local_environment`; `stewardship/web/error_pages.py`: `_is_local` | The LOCAL banner on every Admin and Family page; the Admin sign-in page's test sign-in instructions (with `google_sign_in` deciding whether Google is also offered); the sign-in wording on the Users and setup-step pages | Laptop tool: says which environment, and which ParishSoft, the developer is looking at, and how to sign in there |
| `stewardship/local/fake_parishsoft.py`: `execute_fake_parishsoft`; `stewardship/local/seeder.py`: `admit_seeder_mounts`, `configure_web_process`, `execute_local_seed`; `stewardship/local/spike.py`: `execute_spike` | The fake ParishSoft, the seeder and the spike check refuse any other profile | Laptop tool: they ship in the one image but are inert in Production |

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
- **Database sources (planned, #2111).** The seeder's web-identity steps
  get one LOCAL-only fixed `backend` address that the PostgreSQL source rules
  allow for the web and download logins; every other rule is Production's
  ([database connection sources](../operations/spec.md#database-connection-sources)).
- **TLS.** Caddy serves `localhost` with `tls internal` (its own local CA).
  There is no ACME account and no port 80 listener.
- **Published ports.** Caddy publishes only `127.0.0.1:8443` and Mailpit only
  `127.0.0.1:8025`, both inside the VM. Nothing binds `0.0.0.0`.
- **Networks.** `backend` and `proxy` are already `internal: true` in every
  profile, and outbound traffic leaves through the non-internal
  `application-egress` network (web, worker, mail-dispatch, the ParishSoft,
  Workspace and Slack installers, and the backup worker). LOCAL renders
  `application-egress` with exactly the same members as Production
  ([#1399](https://github.com/epiphany40223/parishkit/issues/1399)), so its
  integrations run through Production's code; what a LOCAL service may reach
  is limited by code, as the [safety guarantees](#safety-guarantees) say, not
  by the network. Caddy, Mailpit and the fake ParishSoft MUST NOT join
  `application-egress`. Caddy publishes through `ingress`; in the rendered
  topology only Caddy and Mailpit join it (the operator script's
  [launch-day spike](#launch-day-spike-check) shards attach to it too), and
  LOCAL creates it with
  `com.docker.network.bridge.enable_ip_masquerade: "false"`. Published ports
  still work, because Docker forwards inbound connections to them, but the
  containers on `ingress` get no source NAT and so no route to the internet.
  This is a Docker bridge setting, not a firewall, and it relies on the VM's
  own upstream NAT translating only the VM's address; a manual check in the
  VM (outbound requests from Caddy, Mailpit and the fake ParishSoft fail,
  and one from web succeeds) is part of the
  [testing requirements](#testing-requirements).
- **Project name.** The renderer's top-level Compose `name` is
  `parishkit-local` (Production's stays `parishkit-stewardship`), and the
  operator script passes `-p parishkit-local` to every Compose command.
- **Security headers.** As in the [branch table](#existing-profile-branches):
  secure cookies and the HTTPS redirect stay; HSTS is 0, so a browser does not
  pin HTTPS for every `localhost` service the developer runs.
- **Banner.** Every Admin and Family page shows a persistent, non-color-only
  LOCAL banner. It names the data: synthetic with no real ParishSoft by
  default, or real ParishSoft data, read only, when the deployment chose the
  real API ([ParishSoft](#safety-guarantees)). Emails are not altered, so
  what Mailpit shows is what a Family would receive, unless the
  [mail redirect](#safety-guarantees) is set.

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
  image but are inert: each refuses to run unless the profile is LOCAL. The
  `local-rehearsal-report` command reads only the files it is given and
  opens no connection;
- the [modeled SMTP latency](#mail-catcher) is refused outside LOCAL, so a
  Production mail parent never waits on purpose;
- the [mail redirect](#safety-guarantees) is refused outside LOCAL, and
  unset it changes no rendered document, helper request, envelope or
  message;
- the real Workspace key rule (`mail_catcher.admit_real_workspace`) admits
  a Google service account, and off-site Drive copies, in every other
  profile exactly as before; its redirect and clock terms apply only in
  LOCAL.

## Safety guarantees

Each guarantee below is enforced where the value is installed and again where
it is used, so that a misconfigured file cannot cross between LOCAL and
Production in either direction.

- **Mail.** The mail-catcher credential is a small JSON document with a
  distinct type marker and no secret; the SMTP endpoint itself is the code
  constant `LOCAL_SMTP_ENDPOINT = ("mailpit", 1025)`, never a document field.
  Every profile but LOCAL refuses the mail-catcher document. LOCAL admits it,
  and admits a real Google service account (#1399) only when the deployment
  sets the [mail redirect](#safety-guarantees) and the process runs on the
  normal [clock](#fake-clock): one rule, `mail_catcher.admit_real_workspace`,
  which `workspace_transport` applies at every intake and every use. The
  intakes are the `google_workspace` credential installer's provider check,
  the setup wizard's credential step and the Integrations page and
  `pk-admin integration` key change (`accounts/setup_credentials.py`
  `admit_candidate`, then `integration_candidates.workspace_info`). The uses
  are every Gmail call site (`family_delivery.SmtpSession`,
  `readiness_delivery.deliver_sample`, `provider_check_worker`, the
  mail-dispatch mailbox check, `smoke.py`, and the operational, report email
  and security delivery modules) and the off-site [Drive](#safety-guarantees)
  copies. Each selects its transport from the installed document type and
  refuses a document its profile, redirect and clock do not admit, before
  any token or connection. A LOCAL helper request carries `mail_redirect`
  beside `profile` only when it is set, so the environment-free helper
  applies the same rule. The clock term is checked at the intakes and in the
  faked processes that use the key in-process (web, the CLI, smoke and the
  backup worker's Drive copies and Test access). The mail helpers start
  with an empty environment and so always run on real time: a key installed
  on the normal clock keeps sending, only to the redirect, if the
  deployment later runs under the fake clock. That is correct, since the
  helpers' Google checks use the real date; the clock term is about the
  checks working, not about who is reached. With no redirect, a real key is
  refused at the intake as an invalid key, as before #1399.
- **Helper subprocesses.** Helpers start with `python -I` and an empty
  environment, so they cannot read the profile themselves. Each helper request
  MUST therefore carry an explicit `profile` field: `parishsoft_http_worker`
  (through `parishsoft_transport`), `provider_check_worker` (through
  `provider_checks`), the Family and readiness delivery workers, and any other
  helper that opens SMTP or ParishSoft connections. A ParishSoft request also
  carries `base_url`, the deployment's [ParishSoft choice](#safety-guarantees),
  and reaches that one base URL only. A helper admits
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
  unchanged. The safety argument rests neither on refusing activation nor
  on the data being synthetic: real addresses may be present once the
  developer reads the real ParishSoft. Real Families cannot be emailed
  because:
  - without the [mail redirect](#safety-guarantees), a Google service
    account is refused in LOCAL at every intake and every Gmail call site,
    so all mail goes to Mailpit through the mail-catcher transport, and
    Mailpit has no route out and renders no relay: a real address in a
    message reaches only Mailpit;
  - with the redirect, every SMTP submission's envelope holds only the
    redirect address, whatever the transport, routing class or campaign
    mode, so the real mailbox delivers to that one address alone.

  These code checks are the whole argument. Testing mode is not a further
  guard: it is off after activation, and operational and security alerts
  are exempt from it even before (see
  [mode routing](../background-processing/spec.md#mode-routing)).

  A test MUST show that, after LOCAL activation, every Family and
  administrative message is sent only through the mail-catcher transport,
  or with a redirect only to the redirect address.
- **Mail redirect** (#1399). The deployment setting `mail_redirect`
  (`PARISHKIT_STEWARDSHIP_MAIL_REDIRECT`) is one bare address, the
  developer's Testing recipient. When it is set, every SMTP submission
  replaces each envelope recipient with it, index for index (one RCPT,
  whose acceptance stands for every recipient; a refusal of the redirect
  address is a SYSTEMIC configuration fault, never a refusal recorded
  against the real addresses), and rewrites the `To` header to
  it with no `Cc`, so a copy neither shows nor lets reply-all reach the
  original addresses. It applies whatever the message's routing class
  (`normal`, `testing_override`, `operational`) or campaign mode, because
  it sits below them: one rule, `mail_catcher.envelope`, used by the only
  SMTP submission code in Stewardship (`family_delivery._submit` through
  `SmtpSession`, for Family, digest, weekly, operational and security mail;
  `readiness_delivery._submit` for readiness, setup test and campaign
  samples) and by `pk-stewardship smoke --send-to`, which sends over its
  own connection. It reuses Testing mode's rule (one configured address on
  the envelope) instead of forcing Testing mode, which is decided in SQL
  with no LOCAL branch and does not cover operational and security alerts.
  It fails closed: the loader and the recorded settings reader refuse it
  outside LOCAL; mail parents add `mail_redirect` to a helper request
  beside `profile` only when it is set, and a helper refuses a request that
  carries it with any other profile (the Family helper exits 1 with no
  outcome, the readiness helper answers `not_sent`); and in LOCAL, any
  transport other than the mail catcher without a redirect is refused
  before a connection is opened (a SYSTEMIC Family result, a `not_sent`
  sample, `smoke` exit status 2). The mail catcher alone needs no redirect,
  so by default Mailpit still shows the application's routing. The setting
  is an operational switch like the [modeled latency](#mail-catcher):
  rendered into the service documents only when set and never into the
  provisioning record.

  The redirect keeps real addresses from being reached; it does not keep
  real content on the laptop. With a real mailbox, every redirected message
  (names, giving details, access codes and links) goes through Google to
  the redirect inbox. This follows the Administrator decision of
  2026-10-17: when LOCAL holds real ParishSoft data, redirected mail MAY go
  out through the real mail provider, but only to the one redirect address,
  which the Administrator chooses. Every other recipient is still refused,
  and the redirect stays mandatory for any transport other than the mail
  catcher: LOCAL admits a real Workspace key only beside it (see Mail
  above). The redirect is set only with the operator script's
  `deploy --mail-redirect` (or the deployment YAML), never from the Admin
  portal, so no Admin setting can widen it. It SHOULD be the developer's
  Testing recipient, so Testing-mode mail and the redirect agree; the
  envelope rule does not depend on it. A LOCAL Workspace key SHOULD be a
  separate test service account for a test mailbox, never Production's.

  The redirect requirement is LOCAL's alone. The `development` and `test`
  profiles are plain-HTTP loopback profiles for unit tests and the Compose
  scaffold; they never run a real Workspace key in normal use and have no
  redirect requirement (#1448 review). They admit a service account and
  send to its real recipients as Production does, so a developer who wants
  real mail on a laptop uses LOCAL with the redirect, never those
  profiles.
- **Smoke tools.** `pk-stewardship smoke` (see the
  [smoke tools guide](../../../guides/stewardship-smoke-tools.md)) runs in
  LOCAL exactly as in Production, against whatever is installed: the fake
  ParishSoft, the mail catcher, the sentinel or a real OAuth client and a
  test Slack token, a real Workspace key beside the
  [mail redirect](#safety-guarantees). A `--send-to` goes over Gmail, so
  without a redirect it is refused before the key is read or any provider
  is contacted; with one and a real key it sends the one fixed smoke message
  to the redirect address alone, whatever `--send-to` names. A
  `backup_drive` check runs on the same terms as [Drive](#safety-guarantees)
  below. A Slack `--channel-id ... --send` posts the one fixed smoke message
  to the named channel, as in Production. The `parishsoft_contact` check
  (its probe and its `--send` round trip) applies the same
  `contact_put_admitted` rule as census publication (see ParishSoft below)
  before it reads anything, in every profile: a LOCAL deployment reading
  the fake runs it against the fake, whose Members can hold the check's
  `pager` and `fax` marker fields ([writes](#fake-parishsoft-writes)), and
  one reading the real API is refused before the tenant read.
- **Google sign-in.** `up` installs a sentinel `google_oauth` client
  document (`runtime_web.LOCAL_OAUTH_SENTINEL`) that holds no real client ID
  or secret. Every other profile refuses it, so it can never reach
  Production. With the sentinel, LOCAL's login page and fresh-authentication
  prompt offer only the [local test sign-in](#local-test-sign-in). A
  developer MAY replace the sentinel with a real Google OAuth client
  (#1399), which LOCAL admits by the same parser as every other profile.
  That client MUST be a separate one whose only authorized redirect URI is
  `https://localhost:8443/admin/oauth/callback`, never Production's. The
  sign-in pages then offer Google beside the test sign-in. What keeps such a
  sign-in on LOCAL:
  - the redirect URI is built from the request, which the canonical-host
    check holds to the configured origin's host, and the loader admits only
    `https://localhost:8443` as LOCAL's origin, so the sign-in only ever
    returns to the laptop; Google refuses a redirect URI that the client
    does not list, so Production's client cannot complete a LOCAL sign-in;
  - the ID token's audience is the client ID, so a token issued for one
    deployment's client is refused by another;
  - session and CSRF cookies name no domain, so a browser sends LOCAL's only
    to `localhost`, and each deployment has its own signing key and session
    table;
  - sign-in goes through the same `complete_identity` core as in Production,
    so only the deployment's
    [Admin portal users](../admin-portal/spec.md#portal-user-management) get
    in, with their roles and the same step-up rules.

  A real client needs the normal [clock](#fake-clock). Google sign-in checks
  the ID token's `iat` and `exp` and Google's TLS certificates against the
  process's clock, which the fake clock holds in the past. Web therefore
  refuses to start (`runtime_web.admit_google_clock`) when the installed
  client is not the sentinel and the process runs under libfaketime (its
  `FAKETIME_*` variables are set). This refusal's fixed text, which holds
  nothing from the client document, is the one admission failure web start
  shows as it is instead of its generic line, and the `google_oauth` smoke
  check prints it too; the seeder's one-shot containers refuse the same
  way. The operator script refuses first: every Compose selection under
  the fake-clock override refuses a client document other than the
  sentinel before anything starts. The sentinel runs under either clock.
  The rule is profile-independent and inert in Production, which never
  runs under libfaketime.
- **ParishSoft.** The deployment setting `parishsoft_base_url`
  (`PARISHKIT_STEWARDSHIP_PARISHSOFT_BASE_URL`) picks the one ParishSoft base
  URL a deployment reaches from exactly two values
  (`parishsoft_http_worker.BASE_URLS`): the real API
  (`parishkit.parishsoft.DEFAULT_API_BASE_URL`) or the fake
  (`LOCAL_SOURCE_BASE_URL`). Unset, each profile keeps its default: the fake
  in LOCAL and the real API everywhere else. The fake is admitted only in
  LOCAL, so Production cannot choose it; the loader, the recorded settings
  reader and every helper request check that again. The setting is an
  operational switch like the [modeled latency](#mail-catcher): it is
  rendered into the service documents only when it differs from the
  profile's default and never into the provisioning record, so Production's
  documents are unchanged. A LOCAL deployment that chose the real API reads
  real parish data; that is allowed on the laptop (#1399) and is never
  committed. LOCAL never writes to the real ParishSoft, whatever it reads
  ([#1884](https://github.com/epiphany40223/parishkit/issues/1884)): one rule,
  `parishsoft_http_worker.contact_put_admitted`, admits the Member contact
  PUT for every other profile exactly as before and for a `local` request
  only when its base URL is the fake. Three layers apply it independently:
  the loader refuses `parishsoft_publication` in LOCAL unless the deployment
  reads the fake, a publication session cannot be built for the `local`
  profile with the real API, and the helper refuses the PUT in any `local`
  request naming the real API. So a LOCAL deployment that reads the fake runs
  census publication (Update ParishSoft) through the same preflight, worker,
  gateway, session and helper as Production, and its writes reach only the
  fake, which [records them](#fake-parishsoft-writes). The gateway's contact
  read stays refused (`read_unconfirmed`) for the real API until that read is
  confirmed (see the
  [member requests guide](../../../guides/stewardship-member-requests.md#parishsoft-publication));
  for the fake it reads `GET members/{id}` and maps the fake's Member object
  onto the PUT's field names (`stewardship/local/fake_contact.py`). The
  operator script renders `parishsoft_publication` on for LOCAL while the
  deployment reads the fake (`up` and every `deploy`), never with the real
  API and never into a rollback. Testing mode still stops publication after
  its preflight, as in Production. The seeded campaign, `up` and the
  setup wizard's printed values use the fake; switching an existing
  deployment to the real API needs a real `parishsoft` key for the real
  organization.
- **Slack.** The `slack` credential target is admitted and checked in LOCAL
  by the same code as in every other profile (#1399). Slack carries only
  operational and readiness alerts to the configured channel, never Family
  mail. A LOCAL Slack token SHOULD be a separate test bot token whose app is
  installed only in a test channel, never Production's token. That rule is
  the developer's to keep: nothing in code tells a test token from
  Production's. A LOCAL deployment does not know Production's Slack
  workspace or channel, so it cannot refuse them, and its alerts carry no
  environment label (only their "Deployment mode" field), so with
  Production's token LOCAL's alerts would post to the Production channel
  looking much like Production's own. A configured environment label on
  alerts belongs with the later #1399 configuration work. With no Slack
  token, operational alerts are recorded and emailed only (to Mailpit).
- **Drive.** Off-site copies sign in with the Workspace key, so LOCAL
  reaches Google Drive on exactly the terms of a real key (#1399): with a
  mail redirect, on the normal clock (`mail_catcher.admit_real_workspace`).
  That is safe with real data too: each copy is the backup set sealed to the
  human-held recipient key plus a plaintext manifest of sizes and digests,
  so the folder holds nothing a Drive reader could open, and each set folder
  is tagged with the deployment's own runtime-row ID, so retention never
  touches another deployment's sets, even in a shared folder. The off-site
  folder SHOULD still be a separate test folder. Without those terms the
  target is refused, at the Backup destination page and `pk-admin
  integration` (reason `drive_not_in_local`, with what is missing), and again
  at use, whatever the configuration or the credentials tree holds: the
  backup's off-site copy (`backup_offsite.copy_offsite`) reports
  `not_configured` without reading its destination or key, the Drive session
  (`backup_drive.workspace_session`, which every copy, Test access check and
  smoke check builds with the caller's profile and redirect) refuses before
  it parses the key, and the `backup_drive` smoke check refuses before it
  reads anything. The mail-catcher document is never a Drive key. A
  reinstall gives the deployment a new tag, and a snapshot switch can leave
  complete set folders the restored database has no record of; retention
  never removes those, so the developer clears old LOCAL set folders from
  the test folder by hand.
- **Egress.** LOCAL's application services have Production's route out (see
  [topology](#topology-and-web-differences)), so the guarantees above rest on
  the code checks, enforced at install and at use, and not on the network.
  The real providers a LOCAL deployment can reach are Slack, with a token
  the developer installs, ParishSoft's read endpoints, when the developer
  chose the real API, Google sign-in, when the developer installed a real
  localhost-only OAuth client (see Google sign-in above), and Gmail and
  Drive, when the developer set the mail redirect and installed a real
  Workspace key (see Mail and Drive above). A fast test lists each of those
  use-time checks and asserts that LOCAL refuses its real provider without
  its configured guard, defaults to the fake ParishSoft and never writes to
  the real ParishSoft.
  Under the [fake clock](#fake-clock), which never runs ahead of real time, a
  faked process checks a real provider's TLS certificate against that past
  time: it refuses a certificate issued since then and accepts one that has
  expired since then. Today Slack and the real ParishSoft API are reached
  this way, so the exposure is a test Slack token or a real ParishSoft key.
  That key can write Member contact fields: what keeps LOCAL from writing
  is the code above, not the key. Under the fake clock a faked process
  accepts a ParishSoft certificate that expired within the offset (and
  refuses one issued within it, so reads fail as unavailable), so the guide
  recommends the normal clock for real-API runs and the host half warns
  when the real API is rendered under the fake clock. A real Google OAuth
  client is refused under the fake clock outright (see Google sign-in
  above), and so is a real Google Workspace key, whose token assertion's
  timestamps and Google's TLS checks cannot run on a past clock (see Mail
  above).

## Mail catcher

[Mailpit](https://mailpit.axllent.org/) receives every message the LOCAL
deployment sends:

- **Transport.** Plain SMTP to `LOCAL_SMTP_ENDPOINT` on the `backend` network,
  with no TLS and no authentication. Only the identities that already receive
  the `google_workspace` credential receive the mail-catcher document, under
  the same [mount rules](../operations/spec.md#runtime-storage).
- **What is captured.** Everything the deployment sends, including the
  seeder's past mail (see [mail history](#mail-history)): Family invitations, Reminders and receipts, readiness
  samples, report emails and operational and security alerts. Messages go to
  whatever address the application chooses, so
  [mode routing](../background-processing/spec.md#mode-routing) is visible
  unchanged, unless the [mail redirect](#safety-guarantees) is set: then
  every message goes to the redirect alone. Synthetic recipients are all at
  `@example.test`.
- **UI.** The Mailpit web UI is at `http://localhost:8025` on the laptop,
  forwarded from the VM's loopback. It needs no sign-in; it is reachable only
  from the laptop.
- **Image and storage.** Mailpit's image is pinned by multi-arch digest like
  the other third-party images. Its store persists under
  `run/persistent/mailpit` with a message cap (for example 50,000).
- **Modeled latency.** Mailpit accepts a message at once, where Gmail takes
  about 0.6 seconds, so a timing rehearsal sets the deployment setting
  `local_smtp_latency_ms` (`PARISHKIT_STEWARDSHIP_LOCAL_SMTP_LATENCY_MS`,
  0–5000, default 0). A LOCAL mail parent waits that long before each
  submission to the mail catcher, outside every transaction, so the wait
  counts in `submit_ms` as Gmail's would. It is tied to the mail catcher
  (#1399): a submission through a real mailbox beside the
  [mail redirect](#safety-guarantees) has Gmail's own latency and never
  waits on top of it. The loader refuses any non-zero value outside LOCAL, and
  the rendered documents carry it only when it is non-zero (never in the
  provisioning record), so every other deployment's rendering is unchanged.
  It is set with `deploy --smtp-latency-ms` (below) for the
  [bulk-send rehearsal](#operator-script).

## Fake ParishSoft service

A stand-in for ParishSoft's v2 API serves the [synthetic parish](#synthetic-parish)
to the unmodified source loaders, so setup loading, full refreshes and
giving reads all run through their real code.

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
  one mount: its own configuration file (below), read-write so it can record
  [publication writes](#fake-parishsoft-writes). It runs as the application
  uid, which owns that file, and joins only `backend`.
- **Base URL.** `LOCAL_SOURCE_BASE_URL = "http://fake-parishsoft:8080/api/v2"`.
  It is LOCAL's default ParishSoft base URL (see
  [ParishSoft](#safety-guarantees) for the setting that may choose the real
  API instead), threaded through every `ParishSoftConfig` call site and both
  helper subprocesses. Each request admits exactly the deployment's one
  chosen base URL, never both, and this constant only for a `local` request
  (see [helper subprocesses](#safety-guarantees)). The shared
  `parishkit.parishsoft_http_worker` owns the base-URL rule and the
  profile names, so the helper applies it without importing application code;
  a stewardship test keeps those names aligned with `DeploymentProfile`. A
  process whose runtime assembly recorded no profile cannot read the source at
  all.
- **Authentication.** Requests MUST carry `x-api-key` equal to the code
  constant `LOCAL_PARISHSOFT_KEY`, which `up` installs as the `parishsoft`
  credential; any other or missing key gets HTTP 401 with an empty body.
- **Reads and one write.** The fake implements the read endpoints below and
  the one Member contact PUT census publication sends (see
  [publication writes](#fake-parishsoft-writes)), and has no control
  endpoint. Any other method or path gets 404 or 405. Its data changes only
  through its configuration file's [operator edits](#fake-parishsoft-edits)
  and the publication writes it records there.

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
- `edits` (optional): the operator's changes to the generated parish, in
  order (see [fake ParishSoft edits](#fake-parishsoft-edits)); absent or
  empty serves the parish as generated; and
- `other_organizations` (optional): 0 (the default), 1 or 2, how many other
  organizations the fixed key also reaches
  ([#1554](https://github.com/epiphany40223/parishkit/issues/1554)). Above
  0, `organizations/search` lists organizations `organization + 1` onwards
  after the synthetic one, and the scoped searches and lists answer with
  some of each one's records after the synthetic parish's: two Members of a
  Family the parish does not have with their contact rows, a Ministry type,
  a Family WorkGroup, a Member WorkGroup, and a copy of the parish's newest
  pledge and contribution of each fund. Every one names its own organization,
  so a load must drop them all (see
  [ParishSoft organization scope](../data/spec.md#parishsoft-organization-scope)).
  Rosters and Family groups are unchanged. Each other organization also
  answers the requests scoped to it as a parish of its own, so whichever
  organization the setup wizard's
  [organization choice](../admin-portal/spec.md#parishsoft-organization-choice)
  offers can be loaded (pk-local finding of 2026-10-24): the first is a
  small generated parish ("Neighboring Parish 1", 12 Families and a
  late-added one, served at once) whose every collection the loads read
  (Families, Members, contact rows, Ministry types and rosters, Family and
  Member WorkGroups and their rosters, funds, pledges and contributions) is
  consistent with the others, every row naming that organization and every
  record identity shifted far from the synthetic parish's, so the requests
  that name no organization (rosters, `members/{id}`, the Family WorkGroup
  list) find its records too. The second holds nothing at all: each of its
  scoped requests answers an empty collection, so the lookup greys it and a
  load of it is refused as empty. `organizations 2` thus offers one
  selectable and one greyed organization.

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
`parishkit/parishsoft_http_worker.py`. Paths are relative to the base URL.

| Method and path | Paging fields (size, position) | Response shape | Identity field |
| --- | --- | --- | --- |
| `POST organizations/search` | none | array of the synthetic organization, then any `other_organizations` | `organizationID` (plus `organizationReportName`) |
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
| `GET members/{id}` | none | one Member object | `memberDUID` |
| `PUT members/{id}/contact` | none | empty body (200); see [publication writes](#fake-parishsoft-writes) | `memberDUID` |

Further rules:

- "Envelope" means exactly `{"data": [...], "pagingInfo": {"totalRecords",
  "totalPages", "pageSize", "pageNumber"}}` with internally consistent values
  on every page, as the envelope parser requires. Page numbers start at 1.
- Embedded row ordinals (`rowNumber`, `rowNum`) are contiguous across pages,
  and embedded totals are constant within a scan.
- Member rows and Member objects carry the `familyDUID` of their Family.
- Rows name their organization as the real DTOs do: Members `organizationID`
  and `ownerOrganizationID`, contact rows `ownerOrganizationID`, Ministry
  types `organizationId`, Family WorkGroups `ownerOrganizationID` and
  `sourceOrganizationID`, Member WorkGroups `ownerOrganizationId`, giving
  `organizationID`/`organizationId`. The generated parish itself does not
  carry them; the fake adds them when it answers.
- Tenant parameters are validated: an organization ID in a search body or path
  other than the synthetic organization's, or one of the
  `other_organizations`, gets 404.
- Malformed parameters get 400: a page size outside 1 to 500, a page position
  below the first page, a non-numeric paging field, or a POST body that is
  not a JSON object.
- The contact list uses its own field names (`dateOfBirth`, `gender`,
  `cellPhone`).

The change feed (`GET families/change/list`), its `change_feed`
configuration option and the household detail reads (`GET families/{id}`
and `GET families/{id}/member/list`) served only quick updates, which were
removed in [#1147](https://github.com/epiphany40223/parishkit/issues/1147)
(see
[quick updates removed](../background-processing/spec.md#quick-updates-removed)).
The fake no longer serves them, the HTTP worker's read allowlist no longer
admits those paths, and a configuration file that still sets `change_feed`
is refused as an unexpected field. The late-added Family reaches the
application through the next full refresh, as it would in Production.

### Fake ParishSoft edits

The review pipeline's ParishSoft refresh checks (Confirmed, Entered
incorrectly, Not seen, Conflict) need ParishSoft's data to change between a
Family's submission and a refresh, so pk-local lets the operator change the
fake's data ([#1780](https://github.com/epiphany40223/parishkit/issues/1780)).
An edit is recorded in the [fake configuration](#fake-configuration)'s
`edits` list; the fake replays the list in order on the generated parish
when it starts, then re-derives what the generator derives (each contact-list
row from its Member and Family, each Family's member count, each Ministry and
Member-workgroup roster row's Family and name, each Family-workgroup row's
name and email), so every endpoint serves one consistent edited parish. A
Family's own name, email and phone fields are not re-derived from its heads:
in ParishSoft they are kept separately, and a Family edit sets them.
Code: `stewardship/local/fake_edits.py`. The kinds:

- **Member fields**: the census fields, by the provider's name or a short
  name (`email`, `mobile`, `work`, `first`, `middle`, `last`, `nick`,
  `maiden`, `suffix`, `salutation` or `prefix`, `gender`, `language`,
  `birth`, `death`, `status`, `type`, `marital`), plus `homePhone`,
  `maritalStatusID`, the two publish flags and `pager` and `fax` (which
  the generated parish leaves unset; the
  [Member contact smoke check](#fake-parishsoft-writes) needs one of them
  filled in); `family=ID` moves the Member
  to another Family. An empty value clears a text or date field; dates are
  given as `YYYY-MM-DD` and served as ParishSoft's midnight timestamps;
  flags take `true` or `false`. A death is `death=DATE status=Deceased`, as
  ParishSoft records both.
- **Family fields**: likewise (`email`, `phone`, `mailing`, `address1`,
  `address2`, `city`, `state`, `postal`, `envelope`, `group`, and `no_mail`,
  the `sendNoMail` flag the census email opt-out is confirmed against), plus
  the names, `primaryPhone`, `primaryAddress3`, `primaryZipPlus`, the
  participation status, `registeredOrganizationID` (another organization
  makes the Family a non-parishioner) and the status and publish flags. A
  group must be one of the parish's Family groups.
- **Ministry roster**: add an active entry (role `Member` by default, or
  `Chairperson` or `Staff`; start date the anchor date by default), refused
  when the Member already has an active entry or an entry with the same role
  and start (the loaders' identity for an entry); or remove every entry of
  the Member on that Ministry.
- **Pledge**: set a Family's pledge amount for one fund and period start,
  keeping the pledge's identity when one exists; `none` removes it. The
  operator script defaults both to the current campaign's own (its first
  pledge fund and its financial period's start, read by the seeder's
  read-only `pledge-period` step), which is where a refresh reads pledges;
  without a campaign they must be given. The command's own fallback is the
  fund Create the campaign offers first (the first active fund by name) and
  1 January of the year after the anchor date.

Only those fields are editable, and each value is checked for the type the
source loaders accept, so an edit the command takes never makes a refresh
fail. Edits never name the held-back late-added Family or its Members (nor
move a Member into it): the fake hides that Family until `seed` releases it,
and the loaders refuse rows that refer to it.

`pk-stewardship fake-parishsoft-edit WORDS...` reads the configuration
document on stdin and writes the new document on stdout. It replays every
recorded edit and the new one on a freshly generated parish first, so an
unknown Member, Family, Ministry, fund or field, or a malformed value, is
refused (exit 2, a plain message naming the record or field) and never
recorded; the fake therefore always starts on a document it wrote. `undo`
drops the last edit and `clear` all of them; `show` prints the recorded
edits, and `show member|family|ministry ID` the record as the fake now
serves it (a Family with its Members and pledges, a Ministry with its
roster). `organizations N` sets the configuration's `other_organizations`
(0 removes it) and is not an edit of the parish: `undo` and `clear` leave it
alone ([#1554](https://github.com/epiphany40223/parishkit/issues/1554)). The
command opens no connection and writes no file. Replay
re-checks every stored edit and value the same way, so a hand-edited list the
parish cannot take stops the fake at start, as any invalid configuration
does.

The [operator script](#operator-script)'s `fake-parishsoft` command runs it
and, for an edit, rewrites the file and recreates only the `fake-parishsoft`
service (a container keeps a bind mount on the inode it started with).
The next full refresh sees an edit. Snapshots carry the
file, so a `reset` or `switch` restores that snapshot's edits.

### Fake ParishSoft writes

Census publication (Update ParishSoft) writes to the fake on pk-local
([#1884](https://github.com/epiphany40223/parishkit/issues/1884); the guards
that keep it off the real API are under [ParishSoft](#safety-guarantees)).
The fake answers `PUT members/{id}/contact` the way the live v2 write test of
2026-10-17 observed the real one (#528):

- It is a partial update: a field left out of the body, or sent as `null`,
  is left unchanged.
- Only the Member contact fields census publication may write are taken
  (`firstName`, `middleName`, `lastName`, `nickName`, `maidenName`,
  `dateOfBirth` as `YYYY-MM-DD`, `language`, `emailAddress`, `homePhone`,
  `cellPhone`, `workPhone`); each is stored on the Member under the fake's
  own name (`dateOfBirth` as `birthdate`, `cellPhone` as `mobilePhone`).
  It also takes `pager` and `fax`, stored under those names: census
  publication never writes them, but they are the only fields the
  [Member contact smoke check](../../../guides/stewardship-smoke-tools.md#member-contact-round-trip)'s
  round trip writes, so pk-local can run that check against the fake.
  An unknown, blank or invalid field, a body that is not a JSON object, a
  body with no field to write, or a Member [edits](#fake-parishsoft-edits)
  cannot name (the late-added Family's, once served) gets 400 and changes
  nothing. A Member the fake does not serve gets 404. If the fake cannot
  store the write, or the edits list has reached its bound of 500 edits
  (operator edits and recorded writes together), it answers 503 and changes
  nothing; the gateway reports that as `write_unavailable`, since the
  payload itself is fine, and at the bound the fake logs a line naming
  `fake-parishsoft undo` and `clear`, which free edits.
- An accepted PUT answers 200 with an empty body and is recorded as one
  ordinary `member` edit at the end of the configuration file's `edits`
  list, written back to the file in place, so it survives restarts and
  deploys, `fake-parishsoft show` lists it and `undo` drops it like any
  edit. A full refresh then reads the written values back, so the
  published changes become Confirmed.

The gateway's read of the fake (`stewardship/local/fake_contact.py`) maps the
Member object back onto the PUT's names: the same eleven fields plus
`gender` and `dateOfDeath`, a date as `YYYY-MM-DD`; `fax` and `pager` are
left out, since publication never writes them, so they are not read either.
The smoke check reads the raw `GET members/{id}` Member object instead, which
carries `pager` or `fax` once an [edit](#fake-parishsoft-edits) or a PUT has
set it. A value read back is exactly the value written, as the publish
worker's read-back check and the smoke check's confirmation require.

The operator script's `fake-parishsoft` edits replace the file and recreate
the service, so the script stops the fake before it reads the file: a PUT
sent while the fake is stopped fails to connect (`write_unavailable`, as in
any ParishSoft outage) instead of being answered and then lost with a file
about to be replaced. A refused edit starts the fake again on the unchanged
file. `seed` rewrites `release_at` only while every service, the fake
included, is stopped.

The write back is in place, not a rename, since the file is the service's
one bind mount, so it is not atomic: a fake stopped or out of disk partway
through a write can leave the file invalid, and the fake then refuses to
start. `reset --seeded` (or `reset NAME`) restores the file with its
snapshot.

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
| Family groups | fixed catalog (ParishSoft's own 18, IDs 1 to 18) | 18 | 18 |
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
- Every Ministry has exactly one leader, an active roster entry whose role is
  one of the
  [default Ministry leader roles](../data/spec.md#ministry-leaders):
  `Chairperson` (role id 2), or `Staff` (role id 3) for every fifth Ministry.
  Every other entry has the role `Member` (role id 1). A fresh local
  deployment therefore has Ministry leaders with no settings change, and both
  default roles appear in the role list
  ([#1150](https://github.com/epiphany40223/parishkit/issues/1150)).
- These proportions hold at any `--families` size.

The parish also includes heads with several semicolon-separated addresses, a
few data-quality cases such as a blank mailing name or envelope number 0, and
exactly one held-back late-added Family at every size. That Family is in
addition to `families`, so the initial load sees exactly `families` Families.
It is a couple with one child, new to the parish: it has no giving, workgroup
or Ministry rows.
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
  `parishkit-stewardship-local-faketime-<base>:<base digest or local tag>`
  (`deploy/stewardship/Dockerfile.faketime`, `FROM` a build argument).
  Production images, their digests and the production Dockerfile output are
  unchanged. LOCAL's image rule (see [origin](#origin-proxy-and-image)) also
  admits these derived tags. libfaketime is inert unless it is preloaded. The
  layer registers the library in `/etc/ld.so.preload` as well as the override
  setting `LD_PRELOAD`: PostgreSQL's entrypoint, run as a uid without a
  passwd entry, replaces `LD_PRELOAD` with its nss_wrapper and later unsets
  it, which left the server alone on real time; the loader honours
  `/etc/ld.so.preload` for every process regardless of its environment, and
  the `FAKETIME_*` variables still decide what the library does.
- **Fake-clock mode.** LOCAL renders a Compose override, `compose.faketime.json`,
  that sets these on every application service, `postgres` and `valkey`:
  - `LD_PRELOAD` set to libfaketime;
  - `FAKETIME_TIMESTAMP_FILE` pointing at `/run/parishkit-clock/offset`;
  - `FAKETIME_CACHE_DURATION=1`;
  - `FAKETIME_DONT_FAKE_MONOTONIC=1`.

  The override adds exactly one mount to each of those services:
  `run/local/clock/`, read-only at `/run/parishkit-clock`.
  `service_boundaries.validate_mounts`, the offline mount policy and the
  backup worker's policy admit exactly that path, read-only, only when the
  profile is LOCAL, and refuse it for every other profile; fast tests cover
  both directions. The install steps and the one-shot profiles run under the
  override too, so no row predates the clock. **Normal mode** renders without
  the override. Moving between modes is a restart of those services with or
  without the override. The seeder's clock-moving steps mount the same
  directory a second time, writable, at `/run/parishkit-clock-control`; see
  [seeder phases](#seeder-phases-and-identities).
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
- **Forward only.** Each new offset is target minus real now, and a target
  earlier than the current fake time is never written. The seeder itself runs
  in a faked container, so it obtains real now as its own (faked) clock minus
  the offset the file holds; in normal mode the offset is zero and the two
  agree. Because fake time never runs ahead of real time, every recorded
  timestamp is at or before real now.
- **When each mode applies.** An unseeded deployment always runs in fake-clock
  mode, at the offset `up` sets (below). Its pages show dates about 17 days in
  the past. A seeded deployment runs in normal mode.
- **Not faked.** Caddy, Mailpit and `fake-parishsoft` run on real time. The
  local certificate's validity is checked by Caddy and the browser on real
  time, and Mailpit SMTP and the fake ParishSoft are plain connections. The
  faked application services do validate a real provider's certificate
  (Slack, once a token is installed) against faked time, as the
  [egress guarantee](#safety-guarantees) describes. Mailpit therefore stamps
  messages with real time.
  `fake-parishsoft` compares `release_at` against real time.
- **Timers.** Monotonic clocks stay real (`FAKETIME_DONT_FAKE_MONOTONIC`), so
  sleeps, subprocess timeouts and work budgets behave normally. Valkey key
  expiry and PostgreSQL `now()` both follow the shared clock, so TTLs, leases
  and database timestamps agree.
- **Jumps.** A forward jump can make a held database lease look expired, so the
  seeder jumps only when work has [settled](#settled) and no task holds one.
  Container health is unaffected: probe heartbeat files record monotonic time,
  which libfaketime leaves real. After a jump to an occurrence instant the
  seeder waits for that instant's [evidence](#settled) before it acts. A jump
  can also make an in-flight statement with a timeout fail, such as a health
  probe's two-second `statement_timeout`. Settled work does not cover probes,
  so a probe may fail once around a jump; that is expected and is retried.
- **Browser cookies.** In fake-clock mode Django computes cookie `Expires`
  dates in fake time, about 17 days in the past. Browsers honour `Max-Age`
  over `Expires`, so sessions still work. Verified (PR 5b, 2026-10-04): the
  session middleware and the namespaced CSRF middleware set `Max-Age` on
  every Admin and Family cookie, and a response from the faked web through
  Caddy carried `Max-Age=31449600` with a fake-time `Expires`.

## Seeded campaign and responses

A seeder gives the local environment a campaign in progress with realistic
Family activity, so reports, progress pages and the planned response reporting
([#477](https://github.com/epiphany40223/parishkit/issues/477)) have a
meaningful data set. It requires a completed setup wizard and the one
campaign, rather than creating a second one. Setup collects no campaign
([#142](https://github.com/epiphany40223/parishkit/issues/142)): the campaign
is created by [Create the campaign](../admin-portal/spec.md#create-the-campaign)
and loaded by its full ParishSoft refresh. The seed,
`families`, `--response-scale` and `now` fully determine the timeline (see
[determinism](#seed-determinism)). A seeded campaign is live; the Testing-mode
review state is built separately from the post-setup snapshot by the
operator script's [`seed --testing`](#operator-script).

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
Each step runs as a one-shot container under an existing identity, with
exactly the mounts that identity's service already has in fake-clock mode,
plus, for the clock-moving steps alone, the clock directory a second time,
writable, at `/run/parishkit-clock-control`. The seeder admits its own mounts
by setting that one aside and passing every other mount through web's
ordinary policy, so it can carry no mount web may not; it then configures
itself exactly as the web service does after its mount admission
(`runtime_web.configure_web_runtime`: keyrings, SQL-role and
coherent-authority admissions, limiter, Family and Admin runtimes). No step
adds a login, role or grant. The operator script reads `seed`, `families` and
`anchor_date` from the [fake configuration](#fake-configuration) and passes
them to each step as arguments, together with `now`, `--response-scale` and
the Administrator's email. The web-identity steps are started with `docker
run` from the rendered web service definition (its image, user, hardening,
mounts, tmpfs and environment, plus the fake-clock override's when the clock
is fake), not `docker compose run web`: web holds a fixed address on the proxy
network, which a second container could not share. The long-running
scheduler, worker and `mail-dispatch` keep running in fake-clock mode
throughout, so occurrences fall due naturally.

| Order | Phase | Clock | Identities | Work |
| --- | --- | --- | --- | --- |
| 1 | prepare | Friday before the start Saturday, 09:00 | `web` (its requests are installed and run by the configuration installer, worker and `mail-dispatch`) | Configure the campaign, meet [Go live](#go-live-under-the-fake-clock) and take it to Production (`scheduled`) |
| 2 | drive | stepped through the timeline | `web` for Family steps; scheduler, worker and `mail-dispatch` running normally | Carry out every event before `now` in [historical order](#historical-ordering) |
| 3 | check | (services stopped; PostgreSQL and Valkey running) | offline `migration` | Run the [invariant check](#post-seed-invariant-check) |
| 4 | finish | real time, normal mode | `web` (the Admin's "Refresh now" and the Users page's leader import; the worker runs the refresh and the configuration installer applies the import) | After the restart without the preload, request the real full refresh that releases the late-added Family, then import the [seeded Ministry leader user](#seeded-ministry-leader-user) |

The clock's starting point must not be earlier than any row already in the
database. `up` therefore runs the whole install and the setup wizard in
fake-clock mode at an offset of 17 days behind real time. The Friday before
any seed's start Saturday is at most 16 days before that seed's `now`, so
phase 1's jump is always forward, including after `reseed` restores the
post-setup snapshot.

The `testing` step
([#1413](https://github.com/epiphany40223/parishkit/issues/1413)) is not part
of `seed`. It is the last part of `seed --testing`
([#1395](https://github.com/epiphany40223/parishkit/issues/1395)), which
runs it under `web` at real time in normal mode on the restored post-setup
data. It refuses unless the global mode is Testing, the campaign is a
`draft` that was never active and never started go-live, the campaign has no
Initial or Reminder schedule yet, and the Testing recipient is an
`@example.test` address. One configuration change request then sets campaign
dates from today to four weeks ahead and adds the Initial a week ahead and two
Reminders after it. The Family portal admits a Testing sign-in only while
the dates contain now, and a due schedule in Testing mode sends to every
Family, so every schedule is in the future. Next, readiness's full refresh
and the Initial email's sample run. A chosen-Family Testing send of the
Initial follows, through that page's own review and confirmation, to the
first five Portal-eligible Families with an email by DUID, and the step waits
until each message is delivered. Each Family then signs in with the rehearsal
code its message carried, read from its rehearsal credential with the web
keyring as the live code is read in `drive`. Three submit Testing responses,
one opens the form only, and one never signs in. The step stops before
go-live. Every message goes only to the Testing recipient.

A snapshot saved after the `testing` step is good for under a week of real
time. A week after it was built the Initial falls due, and the Testing
scheduler then sends one Testing message per Family to the Testing
recipient; after four weeks the dates no longer contain now and Testing
sign-ins stop. Rebuild it with `seed --testing` before then. The step's
answer names the Initial's day as `rebuild_before`.

### Seeded Ministry leader user

So that the Ministry leader flow can be tried at once, the finish phase makes
one ParishSoft Ministry leader a Ministry leader user
([#1150](https://github.com/epiphany40223/parishkit/issues/1150)). It reads
the [leaders without a user](../admin-portal/spec.md#parishsoft-ministry-leaders-without-a-user)
under the Administrator's session and picks the Ministry with the most open
follow-up requests (ties: the lowest Ministry DUID) that has a listed leader,
and that Ministry's first leader by address. When no led Ministry has an open
request, it picks the first leader by address. It imports that leader through
the Users page's own review and Apply, so the user has the Ministry leader
role and the change is one configuration request the installer applies. The
phase waits for that request.

The finish step's answer names the user as `leader_user`, and `seed` ends by
printing `Ministry leader user: <address>`. Sign in as that address
(`sign-in --email <address>`) to see the Ministry's open follow-up requests.
The address depends on the generator seed and on the seeded timeline, so it
can differ between installs and between reseeds. A leader who is already a
user is not listed, so a deployment where every leader is a user imports no
one (`leader_user` is null).

### Go-live under the fake clock

Phase 1 runs at the Friday before the start, while the campaign is still in
the future:

1. Set the clock to the Friday before the start Saturday, 09:00 campaign time.
2. Apply the campaign's real dates and every Family mail schedule through one
   configuration change request under the configuration installer: the
   Initial at Saturday 10:00 (added with the campaign's Initial email when
   the campaign has none, as Create the campaign leaves it), and Reminders
   Tuesday and Thursday 09:00 through the Monday end. Wait until no
   configuration request is pending.
3. Meet the readiness prerequisites with real code:
   - a full refresh against the fake, so the source is current within
     `source_stale_seconds`;
   - a successful sample of the Initial email to the Testing recipient at
     Mailpit (the campaign email page's "send a sample", which is what
     readiness checks for a Family template);
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
  campaign-local midnight for daily facts and report emails): wait until work has
  [settled](#settled), jump to the instant (always: an occurrence instant is
  never run early), wait for that instant's [evidence](#settled), and wait for
  the resulting work to settle. The scheduler plans and the worker and
  `mail-dispatch` prepare and send exactly as in Production, and current state
  decides eligibility. A Family that submitted before a Reminder is not sent
  it. A Family that submitted before its invitation was prepared has its
  invitation skipped.
- **Family events:** wait until work has settled, jump to the instant, and call
  the real web entry point under `web` for that Family's session, form
  baseline, presence step or submission. The seeder settles before every jump,
  including between consecutive Family events, because receipts and other
  results are asynchronous and their tasks hold leases. A Family event within
  150 seconds ahead of fake time runs without a jump (fake time runs at real
  speed, and a jump costs the cache wait and a settle), so a Family's presence
  steps, generated seconds apart, ride on its session's jump: a Family's form
  costs two jumps, its session and its submission. Such an event's recorded
  instant leads its intended one by at most those 150 seconds.
- **Late instants.** Fake time advances at real speed while work runs. If it
  has already passed an event's intended instant, the seeder runs that event
  at once rather than jumping back, so recorded instants may trail the
  intended ones by seconds or minutes. Order is always preserved.
- **Seeded now.** Phase 2 visits every event whose intended instant is at or
  before the requested `now`. The **seeded now** is the fake time at which
  phase 2 ends. It is never later than real time, and the
  [invariant check](#post-seed-invariant-check) uses it. Timeline tests use the
  requested `now`.

Verified (PR 5b, 2026-10-04, 100 Families): Family delivery was not held by
source staleness across jumps; the scheduled refreshes that fell due at each
jump kept the source current, every Initial and Reminder occurrence
succeeded with `smtp_accepted`, and no restore or staleness hold was
recorded. Occurrences falling due one at a time under the fake clock were
processed singly: the Initial produced 81 occurrences and the two past
Reminders 77 and 75, none coalesced; the only coalescing in the whole seed is
phase 4's `missed_family_recovery`, which folds the promoted late-added
Family's missed Reminders into one recovery send, as it does in Production.

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
  `succeeded` or `skipped`.

Settled alone cannot tell that the scheduler has seen a jump: before it plans,
nothing is queued. So after each jump to an occurrence instant the seeder
first waits for that instant's **evidence**, the row the real code writes for
it, read from the real schema: the campaign `active` at the start boundary; at
the Initial's or a Reminder's instant, production occurrences of this
campaign's definitions due at exactly that instant for at least every Family
then active and email-eligible with no effective submission (the scheduler
plans Families in pages across loops, so one row is not the plan; zero
expected is met; the planned set may also hold a skipped occurrence for a
Family that submitted just before, such as the early responder); and, at a
campaign-local midnight, the daily-fact row for the day that midnight closed. Only then does it wait for the resulting work to settle. (The
scheduler's probe heartbeat, an earlier design for this signal, lives in the
scheduler container's private `/tmp` and is unreachable from another
container.)

The seed fails immediately, without waiting, if any of these appears among
rows created since the seed began:

- an occurrence in `delivery_unknown`, `coalesced` or `failed` (phase 4
  tolerates `coalesced` in both the fatal and the settled checks: the
  late-added Family's missed Reminders are coalesced into its recovery send
  by design, and those rows are final, not unfinished);
- an outbox message in `delivery_unknown` or `permanent_failure`;
- a `TaskRun` in `failed` or `abandoned`.

Each wait has a limit (10 minutes by default). While it waits the seeder logs
what it is waiting for every 30 seconds; on expiry it stops, and it records
which condition was unmet, the limit and the elapsed time in its log before it
exits.

### Post-seed invariant check

Phase 3 stops the online services for the mode switch (PostgreSQL and Valkey
run on). The **seeded now** the check uses is the fake instant once every
service has stopped, rounded up to the second: the drive's own end instant is
a few seconds earlier, and the worker and scheduler keep writing (daily facts,
operational collection) until they have drained. It then runs, under the
offline `migration` identity, a `DO` block that raises unless:

- parent and child timestamps are monotone, in the order the real code writes
  them: each occurrence's due instant is at or before the message it produced,
  which is at or before the fulfillment that records its delivery; each Family
  session is at or before its form baseline, which is at or before its
  submission, which is at or before its receipt;
- a daily-fact row exists for exactly each elapsed campaign-local day from the
  start date;
- the per-table counts the timeline states without modelling eligibility
  equal what it implies ([#499](https://github.com/epiphany40223/parishkit/issues/499)):
  live submissions; receipts for live submissions (one each); live form
  baselines other than those a review refresh replaced (one per baseline
  event); and live Family engagement rows (one per Family with any event,
  since each signs in). Family session rows are not pinned, because they are
  deleted an hour after their last activity by design;
- each Production Initial and Reminder of the campaign that fell due by the
  seeded now mailed exactly the right Families
  ([#731](https://github.com/epiphany40223/parishkit/issues/731)). The
  timeline states no outbox or occurrence count, because who is mailed
  depends on eligibility the real code decides at each instant; the check
  derives the expected Families from the rows instead, as the planner's rule
  (active, Portal-eligible and email-eligible from before the instant, with no
  live submission created before it). The expected set is rebuilt from the
  rows as they are at check time, so it matches the planner only while a
  Family's eligibility does not change after the due instant, which holds
  for the seed's synthetic parish. A known false-failure window remains: a
  Family that submits after the due instant but before its email is sent is
  skipped at send time and still expected, so the check raises; under the
  seed's fake clock dispatch follows the due instant at once, so this is not
  expected to occur, and the full seed run would show it. Each such Family has
  exactly one
  occurrence of that revision in the campaign's current Production cycle with
  an outbox message of the definition's kind that is not skipped, coalesced
  or failed (a skipped or folded occurrence keeps its cancelled message, and a
  failed one its resend in a later recovery generation), and it was
  delivered; no other
  Family has one (the early responder's Initial occurrence is skipped, and the
  Family with no eligible email has none); and no Production Initial or
  Reminder message lacks its occurrence;
- no timestamp in the tables the seed writes through (sessions, baselines,
  submissions, receipts, outbox messages, occurrences, fulfillments, daily
  facts, Family engagement, task runs and audit events) is later than the
  seeded now, except the future-by-design columns on an explicit allowlist kept
  with the seeder: future occurrence and schedule instants, the campaign end
  date and closing boundary, and expiry and deadline columns (token, session,
  code and export expiries, retention due dates, `not_before` and provider
  deadlines). Phase 4's rows are written after the check.

### Seeding speed

Measured on the VM (2026-10-04, 100 Families, 81 Portal-eligible with email,
seed 3489829950): phase 1 took 14 seconds (configuration 2 s, refresh 0 s,
sample mail 2 s, cleanup 3 s, link preparation 2 s, activation 0 s); phase 2
took 936 seconds for 155 events, 44 of them clock jumps and 111 run without
one, 576 seconds of it settle waits; the invariant check 0.3 seconds; phase 4
(the refresh and the late-added Family's invitation) 37 seconds. The whole
seed, with its stop and restart cycles, took about 17 minutes and sent 329
messages to Mailpit (the sample, 81 Initials, 77 and 75 Reminders, 11
receipts, report emails and operational mail). Phase 2 dominates: the message rate
of about 46 a minute under the global work-order lock, the settle waits
around each jump and the sends after each occurrence. The earlier 5-minute
target no longer applies: the seed runs in the background (see
[operator script](#operator-script)),
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
- **Reminder upticks.** A Reminder's calendar day carries 1.6 times the taper
  baseline, and the following calendar day 1.2 times. (Counting by calendar
  day, not by 24-hour windows from 09:00, is what reproduces the table below
  exactly; the Reminder floors below still apply to the 24-hour windows.)
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
  realistic amount range, Ministry interest, census edits (a changed email, or
  a proposed new adult household Member, who also joins the first offered
  Ministry when the campaign offers Ministries, so the Chairperson review has
  new Members to review), a few email opt-outs, occasional additional
  information text, and the Limitations answers: about 4% of submissions
  cannot attend Mass, and, when the campaign offers Ministries, about 4% name
  one adult who cannot serve (a Member with current Ministries when the
  Family has one, otherwise its oldest Member when the form carries birth
  dates, else its lowest DUID; that Member stops every
  current Ministry and joins none, as the submit check requires), with
  some Families giving both. The Limitations answers draw from their own
  generator, so the other draws of a seed are unchanged. When the campaign
  collects census answers and the timeline has enough submissions, each kind
  appears at least once.
- **Edge cases.** At least:
  - re-submissions (later versions) from 3% of submitting Families, with a
    minimum of one;
  - one Family that submitted on the start Saturday before its invitation was
    prepared, so its invitation occurrence was skipped (the allocation gives
    the start Saturday at least one submission whenever there is any to
    give, so every seed with a submission before today has this case);
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
- every message during and after seeding, including Family, report email, security
  and operational mail, was sent only through the mail-catcher transport;
- go-live reached `scheduled` with no activation catch-up, and the campaign
  became `active` at its start boundary;
- the deployment is in normal mode at the end, with an offset of zero;
- `stewardship_ministry_leaders_v1()` returns roster leaders with no settings
  change, and the finish step's `leader_user` can sign in and sees open
  follow-up requests.

The first such run (2026-10-04, 100 Families) is recorded on
[#476](https://github.com/epiphany40223/parishkit/issues/476): the invariant
check passed; no Reminder went to a Family that had submitted and the early
responder's invitation was skipped; every message reached Mailpit; go-live
reached `scheduled` with no catch-up and the campaign became `active` at the
start boundary; the deployment ended in normal mode at offset zero.

**Testing-step tests** (no VM) pin the `testing` step's calendar (now inside
the dates, every schedule after now, across local midnight and a DST
change), its refusals, and its wiring (the refusal comes before any write,
and the parts run in order). A PostgreSQL test checks that its patch, from a
campaign with no schedules, installs through the real request path. Another sends a chosen-Family Testing send through the page's
service layer and follows the real ticket and message rows. It then checks
that the driver's rehearsal code is the one the message carries and that it
signs the Family in for a Testing submission. The step itself runs in the VM
as part of `seed --testing`.

The schema's guard and function SQL contains no LOCAL branch; that test stays
under [production invariants](#production-invariants).

## Local test sign-in

LOCAL starts with no Google sign-in (the [sentinel](#safety-guarantees)
OAuth client), so a local-only route signs an Admin in. It stays available
when a real OAuth client is installed.

- **Minting.** `pk-stewardship local-sign-in --config
  /opt/parishkit/config/services/web.yaml --email E`, run with
  `docker compose exec web` (the web service's own configuration, as the
  other in-container operator commands take), refuses unless the profile is
  LOCAL and the configured origin's host is `localhost`; it checks both
  before contacting Valkey. A missing option is a usage error naming it. It creates a random 256-bit token and stores, under
  `stewardship:auth:v1:local-sign-in:<sha256 of token>` with a 15-minute
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
    get-or-create and the disabled check, `issue_admin`, the session-guard
    `IntegrityError` handling and `rotate_token`. The local path MUST NOT
    call `issue_admin` directly. Sessions, audit and user roles therefore
    behave as after Google sign-in (see
    [identity and session security](../architecture/spec.md#identity-and-session-security)).
  - The subject stored in `PortalUser.google_subject` is
    `local-test:<normalized email>`, which cannot collide with Google's
    numeric subjects. The email must still be one of the deployment's
    [Admin portal users](../admin-portal/spec.md#portal-user-management),
    which is the only way anyone signs in.
  - The route is one of the access gate's authentication routes, so it
    works before setup completes and during restore review, when the
    developer needs it to reach the setup wizard. In LOCAL the login page
    describes the operator command (with the sentinel, instead of offering
    Google; with a real client, after it), and its text does not mention
    Google
    ([#619](https://github.com/epiphany40223/parishkit/issues/619)); other
    profiles keep their Google wording unchanged.
  - Every local sign-in, like a Google one, issues a new session and opens
    `/admin/`; no return path is recorded or honoured, and a link opened in
    an already signed-in browser replaces that session
    ([#1547](https://github.com/epiphany40223/parishkit/issues/1547)).
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
| `deploy [--schema-change] [--bulk on\|off] [--smtp-latency-ms N] [--parishsoft real\|fake] [--mail-redirect ADDRESS\|off]` | Build the image from the checkout and upgrade the running deployment to it by the Production upgrade's steps (below); `--schema-change` is that upgrade's `STEWARDSHIP_SCHEMA_CHANGE=1`; `--bulk` renders the bulk Family send on or off, `--smtp-latency-ms` the [modeled latency](#mail-catcher), `--parishsoft` which [ParishSoft](#safety-guarantees) it reads and `--mail-redirect` the one [mail redirect](#safety-guarantees) address (or `off`), each carried over when not given |
| `deploy --rollback` | That upgrade's image-only rollback to the image the last `deploy` replaced (below) |
| `snapshot [--seeded]` | Stop the services and save the runtime root as the post-setup snapshot, or with `--seeded` as the seeded snapshot, preserving numeric ownership and modes, then restart; each replaces its earlier copy |
| `snapshot save NAME [--replace]` | Save the runtime root as the named snapshot (below), after the [disk guard](#operator-script); an existing `NAME` needs `--replace` |
| `snapshot list` | Every snapshot with its record and what a switch to it would do, then the VM's and the Mac's free space |
| `snapshot delete NAME` | Delete one snapshot after the operator types its name, then trim the VM disk |
| `snapshot restore NAME` / `switch NAME` | The data-only switch (below): keep the deployed code and change to `NAME`'s data, migrated forward when it is older |
| `reset [--seeded \| NAME]` | Restore the post-setup snapshot and restart in fake-clock mode, or with `--seeded` the seeded snapshot in normal mode, or the named one, image and data together (each snapshot carries its clock-mode marker); with no post-setup snapshot, delete the root and run `up` again |
| `reset --reinstall` | Delete the root and run `up` again from the checkout even when a snapshot exists (snapshots are kept) |
| `wizard` | Complete the setup wizard unattended (below) |
| `finish-setup` | After the browser wizard stops at Finishing setup, do the runbook's post-wizard step for its frozen attempt (below) |
| `seed [--response-scale m]` | Seed the current deployment at the current time (below) |
| `seed --testing` | Switch to the post-setup snapshot and move it to real time: a Testing-mode draft campaign to review (below) |
| `reseed` | Restore the post-setup snapshot, then `seed` |
| `rehearse [--due-in MIN] [--send-only] [--timeout MIN] [--label NAME]` | Measure one scheduled Family send on a seeded deployment ([bulk-send rehearsal](#operator-script)) |
| `spike [--families N] [--sources S] [--during-send] [--wait MIN] [--label NAME]` | Measure Families opening their emailed links and submitting through Caddy on a seeded deployment ([launch-day spike check](#launch-day-spike-check)) |
| `status` | Show the VM, service health, Docker disk use and VM disk use, then the `snapshot list` summary |
| `down` | Stop the services; never removes data |
| `start` | Start a stopped deployment's services in the recorded clock mode |
| `sign-in --email E` | Print a [test sign-in](#local-test-sign-in) link |
| `fake-parishsoft member ID FIELD=VALUE...` / `family ID FIELD=VALUE...` / `roster add\|remove MINISTRY MEMBER [role=R] [start=D]` / `pledge FAMILY AMOUNT\|none [fund=F] [start=D]` / `undo` / `clear` / `organizations N` | Record one [fake ParishSoft edit](#fake-parishsoft-edits) (or, for `organizations`, the [multi-organization mode](#fake-configuration)) and recreate the fake (below) |
| `fake-parishsoft show [member\|family\|ministry ID]` | Print the recorded edits, or one record as the fake now serves it |
| `fake-parishsoft refresh` | Run the Admin's full ParishSoft refresh of the fake and wait for it (below) |
| `ca` | Copy Caddy's local root certificate to the laptop and print how to trust it |

**`up`.** It installs a LOCAL deployment from the checkout by the
[deployment runbook](../../../guides/stewardship-deployment-runbook.md#first-installation)'s
first-installation steps, with the `arm64` image built inside the VM under a
[local tag](#origin-proxy-and-image), the LOCAL deployment YAML and a
deployment record beside it (UUID, Administrator, image, synthetic-parish
inputs), the marker file (written as soon as provisioning, which requires an
empty root, has completed), the sentinel OAuth client, the backup recipient
key (a pair from the image's `backup-keygen`; only the public key is
installed as the `backup_data` credential, as the backup runbook has the
operator do, and the private key stays at `run/local/backup-key` in the
runtime root, which a backup never archives, so the pair travels with
snapshots and a local backup can be opened) and the
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
real setup wizard, since that is part of what the environment tests, and then
Create the campaign, pressing Load the campaign's ParishSoft data afterwards
(#142). `up` also
builds the [fake-clock](#fake-clock) images and does all of this in fake-clock
mode, with the clock 17 days behind real time, so that a later seed can start
its clock forward of every existing row.

Decision: `up` cannot seed before the interactive setup wizard is complete and
the campaign exists with its Family codes; it waits, then (on confirmation)
verifies setup and the campaign, takes the post-setup snapshot,
runs `seed`, then `snapshot --seeded`. Declining leaves an unseeded deployment
that `seed` can seed later.

**`seed`.** It refuses unless setup is complete, the campaign exists with its
Family codes, and the campaign has not been seeded since the post-setup
snapshot, so campaigns never stack. It clears
Mailpit (removing the wizard-era sample messages), runs the
[seeder phases](#seeder-phases-and-identities) in fake-clock mode at that `now`
(passing `--response-scale` through), then restores normal mode: offset zero,
the clock-mode marker set to `normal`, services restarted without the preload,
and `release_at` set for the final refresh. Last, it tags the seed's mail in Mailpit (see
[mail history](#mail-history)). It is safe to run in the background: it takes
a lock so only one seed runs at a time, prints one progress line per step with
fake time, elapsed real time and row counts, and appends the same lines to
`~/.parishkit-local/seed.log`. If a step fails before the switch to normal
mode it stops, leaves the deployment in fake-clock mode (an unseeded
deployment's mode) with its services running, and exits non-zero; a failure
after the switch (the finish phase) leaves a seeded deployment in normal mode
whose late-added Family may not be promoted, and `reseed` is the recovery
either way. `reseed [--response-scale m]` restores the post-setup snapshot
(which also restores the fake configuration) and then runs `seed`. The
post-setup snapshot is taken once the campaign exists with its Family codes
(after Create the campaign and its refresh, #142), so it always holds the
unseeded campaign the seeder requires. A
deployment set up in normal mode cannot be seeded: its rows are stamped at
real time and the seed's clock would have to start before them; `seed`
refuses when the clock-mode marker says `normal`.

**`seed --testing`.** A seeded campaign is live, and no supported path
returns a live campaign to Testing: Cancel go-live is offered only while the
campaign is `scheduled`, and Return to Testing needs an archived campaign.
Changing the global mode directly would bypass those guards. So the Testing
review state (#1395) is built forward from the post-setup snapshot's Testing
draft. `seed --testing` switches to the post-setup snapshot with the
data-only switch, so the running code is kept (a post-setup snapshot from
before #1395 on another image must first be recorded, as above). It then refuses (leaving the
post-setup data in place) unless setup is complete, the clock is fake, the
global mode is `testing`, the campaign is a `draft` and the Testing
recipient is an `@example.test` address. A recorded `meta` that is not a
Testing draft refuses before anything stops. It clears Mailpit, then moves
the clock forward to normal mode (offset zero, a forward jump, as the
seed's switch is) with every service stopped and recreated. The result is a
draft campaign in Testing mode at real time, for the Testing banner and
labels, schedules, chosen-Family Testing sends, Testing sign-in and
submissions, the Go live page with readiness, Testing cleanup and link
preparation, and the structural edits that only Testing allows. When the
deployed image's seeder offers a `testing` step (the automated Testing sends
and submissions, a follow-up), it runs last under the web identity. The
command ends by suggesting `snapshot save testing`. A failure while the
clock moves restarts the services and names `switch post-setup` as the way
back. Mail safety is unchanged:
every send goes through the mail-catcher transport to Mailpit.

**`wizard`.** For an unattended install (and the documented VM seed runs),
`wizard` completes the setup wizard through the wizard pages' own service
layer, in the wizard's page order (`pk-stewardship local-seed --step wizard`
under `web`: the parish, mail and Testing-recipient settings, the
mail-catcher document, the Slack setting, a generated logo through the real
branding staging, the access lists, the fake ParishSoft key and organization,
the source load, the setup test mail and the final
confirmation), then follows the deployment runbook's post-wizard
step: once the credential installers report `awaiting_ack`, it recreates
`worker` and `mail-dispatch` from `compose.json` and acknowledges each
request inside them, which lets the installer complete setup. Setup collects
no campaign (#142), so `wizard` then runs the seeder's `campaign` step
(`pk-stewardship local-seed --step campaign` under `web`):
[Create the campaign](../admin-portal/spec.md#create-the-campaign) through
that page's own form, review and confirmation (every module from the loaded
catalog, with its default pages and emails), a wait for the configuration
installer to apply it, and the campaign's full ParishSoft refresh, waited on
until the campaign has its Families and codes. Create the campaign adds no
mail schedules, so the seed's `prepare` phase adds the Initial schedule with
the Reminders when the campaign has none. The browser wizard and Create the
campaign remain the developer-facing path; `wizard` writes nothing those
pages would not.

**`finish-setup`.** The browser wizard stops at Finishing setup, "Waiting for
the server operator", until the post-wizard step has run, as in Production.
`finish-setup` is that step for the browser path, and `wizard` runs the same
VM-half function, so LOCAL has one implementation of it. It looks up the
frozen attempt's request ids (`pk-stewardship local-seed --step credentials`
under `web`, which writes nothing: the ids of the attempt's unscrubbed
`stewardship_setup_sealed_credential` rows for `parishsoft` and
`google_workspace`, the lookup `--step wizard` reports with), waits until both
`stewardship_secret_request` rows are `awaiting_ack`, recreates `worker` and
`mail-dispatch` from `compose.json` (with the fake-clock override in fake
mode), acknowledges each request inside them, waits for setup to complete and
restarts every service under the configured topology. Unlike `wizard` it
creates no campaign: setup collects none (#142), so Create the campaign
follows in the browser. It is idempotent: when
setup is already complete it says so and changes nothing, and a repeated
acknowledgement is a no-op, so a rerun after an interruption is safe. It
refuses, before any service is touched, when no attempt is frozen (the
wizard's pages are not finished) or the frozen attempt lacks either
credential. Like every long VM-half command it logs to `/var/log`. Production
keeps its runbook step; nothing here changes the production tooling, and the
web service never acknowledges a credential itself, so LOCAL exercises the
same split between the wizard and the operator.

**`fake-parishsoft`.** It changes, shows or refreshes the fake ParishSoft's
data ([fake ParishSoft edits](#fake-parishsoft-edits)). Every form refuses,
before any Docker call, unless the root carries the marker, the fake's
configuration file exists, and no service document names a
`parishsoft_base_url` (after `deploy --parishsoft real` the deployment reads
the real API, which an edit cannot change and a refresh from this command must
never read); a service document that cannot be read counts as naming one. With
no command, or a help word, it refuses. An edit or `undo`/`clear` first
selects the Compose files, and for a `pledge` without `fund=` and `start=`
reads the campaign's (above), refusing when that read fails; it then runs `fake-parishsoft-edit` in an
isolated one-shot container of the deployment's image (no network, read-only,
the document on stdin), writes the answer over the file (mode 0600, the
application uid) only when the command succeeds and answers with a
configuration document, and recreates the `fake-parishsoft` service alone;
`show` writes nothing. `refresh` requires completed setup and
runs the seeder's `refresh` step under `web` (`pk-stewardship local-seed
--step refresh`): as the seeded Administrator it asks for the full refresh
the Refresh from ParishSoft page asks for, waits for that task to succeed and
for the work it causes to settle (a coalesced occurrence is not a failure, as
in the seed's finish), and prints the task id. The step itself refuses unless
the admitted deployment is LOCAL and reads the fake. Pressing Refresh from
ParishSoft in the Admin portal after an edit is equivalent. Production has no
fake service, no such file and no marker, so none of this can reach it.

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

Snapshots are named (#1395). A name is 1 to 32 lowercase letters, digits or
`-`, starting with a letter or digit; `post-setup` and `seeded` are ordinary
names that `snapshot [--seeded]` and `reset [--seeded]` keep as aliases.
`snapshot save NAME` reads, while the services still run, the applied
migrations into the snapshot's `migrations` file and a `meta` record (taken
time, image and commit, deployment UUID, global mode, campaign state, Family
count, Testing recipient, clock mode, migration count, the seeded Ministry
leader user, size). Both are data, read as `key=value` lines and never
executed. A snapshot taken before #1395 has neither, and every command still
accepts it.

The disk guard: snapshots are full copies, the VM disk is a sparse file on
the Mac that grows with them, and the Mac must keep 20 GB free. Before
anything stops, a save refuses when the Mac's free space on the Lima home's
volume would fall below 20 GiB plus twice the copy's size, when the VM's
would fall below 5 GiB after the copy, or when there are already 8 other
snapshots (`PARISHKIT_LOCAL_SNAPSHOT_LIMIT` changes the cap). A replacement
counts the root's full size too, since its copy is written beside the old
one. The laptop half refuses when it cannot read the Mac's free space. The
refusal lists the snapshots by age and size and names `snapshot delete`;
nothing is pruned automatically, since snapshots are deliberate return
points. `snapshot delete` removes one snapshot after the operator types its
name, then runs `fstrim` in the VM so the disk file can give the space back;
a replacement trims after it removes the old copy.

A save refuses, before anything stops, when it cannot read the applied
migrations, since a switch relies on that record. It copies into a hidden
`.NAME.partial` directory (unchanged files hard-linked from an earlier copy
of the same name), writes `taken` last, and renames the result into place
only once it is whole; a snapshot without `taken` is not a snapshot. A failed
save therefore leaves no half copy and keeps a replaced snapshot whole, and
its exit trap starts the services again.

The data-only switch, `snapshot restore NAME` (alias `switch NAME`), keeps
the deployed code. It never modifies the snapshot, so switching back
migrates it again; `snapshot save NAME --replace` after a switch keeps the
migrated copy. Before anything stops it refuses:

- a snapshot of another install (another deployment UUID; `reset NAME`
  restores one whole);
- one whose recorded migrations include any the running database lacks,
  naming the commit the snapshot was taken on;
- one taken on another image with no migrations record (taken before
  #1395), since newer code cannot be ruled out: `reset NAME` restores it
  whole, and `snapshot save NAME --replace` then records it;
- any snapshot when the running database's migrations cannot be read.

Otherwise:

- When the snapshot was taken on the running image, its schema, grants and
  logins are the running code's. The switch stops the services and restores
  everything in the root except what the running deployment keeps: the
  rendered `config/services` and `config/deployment`, the `cache` static
  trees, Caddy's data (so the CA the browser trusts stays valid) and the
  provisioning records. The deployment YAML and record (`IMAGE`,
  `PREVIOUS_IMAGE`, the debug setting) are untouched and the services are
  recreated, with the fake-clock images built first for a fake-clock
  snapshot. The stores, the clock marker, the fake configuration, the
  installed configuration documents and the credentials the database pairs
  with come from the snapshot, so Mailpit shows that snapshot's mail.
- When it was taken on another image, matching migrations are not enough
  (grants, logins and credential files change without a migration), and the
  running image's required backup would refuse an older schema's pending
  migrations. So the switch restores the snapshot whole except Caddy's data,
  starts it on its own image (refusing beforehand if that image is gone from
  the VM), and runs the scripted upgrade's host half in local mode to the
  running image with `--schema-change`, as `deploy` does. That upgrade's
  check covers migrations, grants and logins alike. The switch carries the
  running bulk Family send and modeled-latency rendering and the debug
  setting, and passes the users upgrade's `STEWARDSHIP_USERS_*` variables
  (#952) when the operator sets them, as a `deploy` does. A snapshot from
  before that conversion needs them; without them its upgrade stops at the
  conversion with the maintenance page up. Afterwards the switch puts the
  running deployment's image record back.

A failure after the stop never starts services on mixed data. The switch's
exit trap names the recovery, `reset NAME` (the snapshot whole, image and
data together) or `reset` to another snapshot, and the deployment record
stays truthful throughout. A switch ends by printing fresh sign-in links,
since sessions live in the restored stores: one for the Administrator, one
for the seeded Ministry leader user when the data records one (`seed` writes
it to `run/local/leader-user`), and the Mailpit address.

**Trusting Caddy's CA.** The browser sees a certificate from Caddy's internal
CA. `ca` copies the root certificate from Caddy's data store in the runtime
root (`run/persistent/caddy/data/caddy/pki/authorities/local/root.crt`, the
container's `/data/caddy/pki/authorities/local/root.crt`) to
`~/.parishkit-local/caddy-root.crt`, after checking that it is a certificate,
and prints the macOS command to trust it in the login keychain. The script
never changes trust settings itself; accepting the browser's certificate
warning also works. A reinstall creates a new CA, which must be trusted again.

**`deploy`.** It upgrades the running deployment, data and all, to an image
built from the checkout, by the same steps as a Production upgrade: the
scripted upgrade's host half (`tools/stewardship-upgrade-host.sh`, which
`tools/stewardship-upgrade.sh` uploads and runs in its `production` mode on a
Production or Testing host) has a `local` mode, and `deploy` runs that mode
inside the VM. One file, two modes: a `deploy` exercises the code path a
Production upgrade takes (the deployment runbook's
[scripted upgrade](../../../guides/stewardship-deployment-runbook.md#scripted-upgrade)),
so the local environment rehearses it. A Production invocation MUST stay
byte-identical: the eight-argument form is production mode, and the tests pin
the upload and that invocation. Local mode differs in exactly these ways:

- it refuses unless the root carries the marker file and the deployment YAML
  says `profile: local`, so it can never run on another deployment;
- the image is the [local tag](#origin-proxy-and-image) built inside the VM
  from the packed checkout a moment before (the laptop half builds it while
  the deployment still runs, so a build failure changes nothing); nothing is
  pulled and no registry digest is checked;
- it uses project `parishkit-local` and the LOCAL rendering;
- it requires the pre-upgrade backup to succeed (not best effort), accepting
  an off-site copy state of `not_configured` (LOCAL copies off site only
  beside a mail redirect on the normal clock) alongside `uploaded`;
- it keeps the services in the mode recorded by the
  [clock-mode marker](#fake-clock): the fake-clock override is applied when
  the marker says `fake`, and the derived fake-clock image of the new
  application image is built after the retarget re-rendered the override;
- it keeps the deployment's debug-logging setting (the deployment record's,
  which `up` took from `PARISHKIT_LOCAL_DEBUG_LOGGING`) instead of
  Production's unconditional off;
- it probes the public origin through Caddy's own CA by the `Server` header,
  since before the setup wizard the application answers 503 as Caddy's
  maintenance page does;
- the retarget renders the bulk Family send as `--bulk` chose
  (`PARISHKIT_LOCAL_BULK_FAMILY_SEND=on|off` to the host half) and the
  [modeled latency](#mail-catcher) as `--smtp-latency-ms` chose
  (`PARISHKIT_LOCAL_SMTP_LATENCY_MS`), and the [ParishSoft](#safety-guarantees)
  base URL as `--parishsoft` chose (`PARISHKIT_LOCAL_PARISHSOFT=real|fake`),
  and the [mail redirect](#safety-guarantees) as `--mail-redirect` chose
  (`PARISHKIT_LOCAL_MAIL_REDIRECT=ADDRESS|off`, one plain address, checked
  before anything stops and again after the retarget); unset, each carries
  over, and a snapshot switch carries the running ones. A rollback renders
  neither the latency, the real ParishSoft API nor the redirect, since the
  previous release may not know the setting; without the redirect a real
  Workspace key is refused, so the rollback fails closed. Before a `deploy`
  of a build older than the setting, the operator sets the latency to 0,
  the ParishSoft choice to `fake` and the redirect to `off`. Production mode
  never reads these variables.

Everything else is the Production upgrade: the advisory upgrade check (an
answer other than `t` refuses unless `--schema-change` says the change is
expected), the background services stopped while web serves, the required
backup, web stopped, `retarget-image` with the bulk Family send switch
carried over, migration and grants when the check calls for them, the static
tree refreshed in place with the replaced tree kept, web first and then
everything else, and the checks. A deployment installed before `up` installed
the backup recipient key gets one from `deploy` first, since the required
backup needs it. A `deploy` refuses unless a snapshot exists
to return to (it names the `reset` that restores each one, oldest first,
before anything starts), records the new image and the one it replaced in the deployment
record, and ends by naming both ways back: `deploy --rollback`, the
upgrade's image-only rollback to the replaced image (refused, as in
Production, when the schema or a grant changed in between), and
`reset --seeded` (or `reset`), which restores a snapshot and so returns image
and data together. Restoring a snapshot restores the images its deployment
record names (the running image and the one a deploy replaced), since the
record travels with the snapshot.

**Bulk-send rehearsal.** `rehearse` measures one scheduled Family send for
the faster bulk Family send's
[rehearsal protocol](../../../plans/stewardship/background-processing.md#bg-12-faster-bulk-family-send)
(BG-12); the [developer guide](../../../guides/stewardship-local-environment.md#rehearsing-a-bulk-send)
is the how-to. The contract:

- it refuses unless the deployment is seeded (normal clock mode), set up,
  and runs an image with rehearsal support;
- its only write is one Reminder schedule, added through the seeder's
  `reminder` step as one configuration change request under the web
  identity, as an Administrator's edit;
- every query it makes itself is read-only with a statement limit, and the
  seeder's `measure` step runs under the offline migration identity (whose
  grants cover the outcome evidence the web login may not read) in a READ
  ONLY snapshot;
- whenever a `--send-only` run stopped `mail-dispatch`, its exit trap
  restarts it before any output (without errexit, ignoring SIGPIPE, writing
  its messages to the run's log file), and every wait or kill logs what,
  the limit and the time taken;
- it exits non-zero unless the report's correctness check passed and the
  send settled within `--timeout`.

**Safety rules.**

- Every destructive command acts only inside the VM, and only on a root that
  carries the marker file `/opt/parishkit/.parishkit-local`. A root without it
  is refused.
- No command removes Docker volumes or `run/persistent`; `down` never passes
  `--volumes`. Restoring a snapshot replaces the root only after the services
  are stopped.
- `reset` without a snapshot, and any other deletion of the root, requires the
  operator to type the instance name (`parishkit-local`) to confirm;
  `snapshot delete` requires the snapshot's name.
- A snapshot name is checked by both halves before it is used in a path.
- The script never targets a host other than the Lima alias.

### Launch-day spike check

On launch day the initial email lands in about a thousand inboxes over tens
of minutes, and Families click while the send is still going (#392 M3).
`pk-stewardship load-check` runs read-only reads inside the web container on
the validation and Production hosts, so it never exercises that path:
Caddy's TLS, gunicorn's slots, the per-source access bucket, Family
sessions, the baseline and submission writes, or any of it beside mail
dispatch. A check that submits real responses can run only here, against
seeded data. The
[developer guide](../../../guides/stewardship-local-environment.md#checking-the-launch-day-spike)
is the how-to. The contract:

- `spike` refuses unless the deployment is seeded (normal clock mode) and
  set up, and Caddy has written its local root certificate. `local-spike`
  refuses unless given `--profile local`. It talks only to its fixed
  endpoints: Caddy by its Compose name with the TLS server name `localhost`,
  verified against that certificate, and Mailpit.
- With `--during-send` it first adds one Reminder due three minutes ahead
  through the seeder's `reminder` step, exactly as `rehearse` does, and the
  Families read only mail that arrives after that. Without it they use the
  mail already in Mailpit.
- It starts `--sources` one-off containers (default 8) on the `ingress`
  network, each its own source address, as Families on different networks
  are, so the per-source limiter applies to each as in Production. The
  containers carry the run's label, and the script removes any left over
  when it ends or is interrupted.
- The containers share one count without talking to each other: every one
  reads the same Mailpit (sending a Host its `MP_ALLOWED_HOSTS` admits),
  takes the distinct Families (by link token) in the order their mail
  arrived, and runs the first `--families` (default 200) of them, each on
  the one container that owns its token. A Family with two emails (an
  invitation and a reminder) runs once. Each container reads each message
  once and stops paging at the first message older than the run. A Family
  starts a random 0 to 20 seconds after its mail was seen, on at most four
  threads per container. A container stops when it has started every
  Family it owns, or at `--wait` minutes (default 20), which it logs with
  the limit and elapsed time and records; Families still queued then are
  cancelled, so `--wait` bounds the run. Without `--during-send` the mail is
  all there already, so each container lists Mailpit once.
- Each Family runs the page's own five requests with its own cookies and
  the portal's CSRF token: the emailed link (`access`), the portal shell
  (`portal`), the form baseline (`form`), one presence heartbeat
  (`presence`) and the form submitted unchanged, as the page script builds
  it (`submit`, sent again once if review is required). Only a GET is ever
  resent on a dropped connection, never a POST. A step that does not answer
  as expected ends that Family's run and is counted as limited (429),
  unavailable (503), refused (any other answer), error (no answer) or
  timeout (30 seconds, logged with the limit and elapsed time).
- A container prints one document of fixed keys, counts and seconds: never a
  token, URL, cookie, address or name, which the document check refuses.
  The report (`local-spike-report`) runs offline over the shard documents and
  a run record (the `pg_stat_database` deadlock count before and after, and
  how many containers failed).
- A container that fails, or leaves a document that cannot be read or none
  at all, is counted once; the report still reads the others. The report
  also shows how many database deadlocks the run added.
- It exits non-zero unless every container reported, as many Families ran
  as were asked for, no deadlock occurred, every step of every Family answered as expected, and
  each step's p95 is within the
  [architecture](../architecture/spec.md) reference targets: 2 seconds for
  the pages and 3 seconds for the submission.

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
  topology: only loopback publications, `application-egress` joined by
  exactly Production's services and never by Caddy, Mailpit or the fake
  ParishSoft, `ingress` without masquerade and joined only by Caddy and
  Mailpit, the two extra services, a local image tag and no GHCR reference;
- **fake ParishSoft contract tests**: a fixed seed produces a fixed digest, an
  in-process fake passes the strict `CoherentParishSoftClient` loaders, a wrong
  key gets 401, and the late-added Family appears only after `release_at`;
- **fake ParishSoft edit tests**: no edits serve the generated parish
  unchanged; each edit kind reaches every endpoint that carries the field;
  every bad edit is refused before it is recorded; and a database test runs
  the real worker's full refresh against the fake twice, before and after
  edits, and finds the edits in the promoted corpus;
- **generator tests**: the scaling table at 100 and 1,100 Families; the
  adult and child mix and `memberType` values; Ministry rosters with no child,
  about 40% of non-child Members on at least one Ministry (about 30% of those
  on two or more, about 10% on three or more), some ended entries, every
  Ministry with an active Member, and every Ministry with one leader in a
  default leader role, both roles appearing, at both sizes;
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
  unchanged, and for the [mail redirect](#safety-guarantees): every typed
  mail, the batched session, readiness samples and `smoke --send-to` reach
  only the redirect; LOCAL beyond the mail catcher without one, and a
  redirect with any other profile, send nothing; and unset, requests,
  envelopes and messages are unchanged;
- **real-key tests** for `admit_real_workspace` at every intake and use
  (#1399): in LOCAL a Google service account is admitted only beside a
  redirect on the normal clock, at the web intakes, the provider and
  mailbox checks (whose helper requests carry the redirect only when set),
  smoke and the off-site Drive copy, Test access and smoke check, and every
  other profile is unchanged; the modeled latency waits only before the
  mail catcher;
- **absence tests** for the [local test sign-in](#local-test-sign-in);
- `shellcheck` on `tools/stewardship-local.sh`;
- **operator-script stand-in tests** (no VM) for the snapshot commands: name
  validation and `--replace`, the `--seeded` and `post-setup` aliases, the
  `meta` and `migrations` records, the cap and both disk refusals
  (replacements included), the refusals before any service stops (newer
  code, another install, no record, unreadable migrations, a missing image,
  no fake-clock override), what a data switch keeps, the other-image path
  through the host half with the schema change and the running image and
  rendering, failed saves and switches, the sign-in lines, and
  `seed --testing`'s checks and clock move; and for `fake-parishsoft`: the
  marker, fake-file and real-ParishSoft refusals before any Docker call, a
  refused edit leaving the file and the running fake alone, and the refresh
  step under `web`.

No Docker end-to-end run of the local environment belongs in CI, and neither
do the [seed tests](#seeder-tests). A developer runs them and verifies the full
stack in the VM, including the network check, before a pull request that
changes it, following the documented VM run.

The delivery plan is
[OPS-10](../../../plans/stewardship/operations.md#ops-10-local-laptop-environment),
which refines the PR sequence in #476.

## Non-goals and rejected alternatives

- **Docker Desktop bind mounts.** Rejected: VirtioFS does not store ownership
  (see [host virtual machine](#host-virtual-machine)).
- **Reworking the topology around named volumes** to suit Docker Desktop.
  Rejected: a large divergence from Production in the very code being tested.
- **Production backups.** The local environment never restores a
  Production backup: that would bring Production's credentials, sessions
  and history onto the laptop. Real ParishSoft data is a different matter,
  read through the real API when the developer chooses it
  ([ParishSoft](#safety-guarantees), #1399).
- **A real Google OAuth client as LOCAL's only Admin sign-in**, registered
  for `localhost`. Rejected as the default: every developer would need a
  Google Cloud client before the first sign-in, and the fake clock, on
  which every unseeded deployment runs, cannot check Google's tokens. The
  local test sign-in uses the same session path and needs no client. Since
  LOCAL gained Production's egress (#1399), a real localhost-only client is
  allowed as an option beside it (see
  [safety guarantees](#safety-guarantees)).
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
- **A control endpoint on the fake** for [edits](#fake-parishsoft-edits).
  Rejected: the fake stays read-only over HTTP, and `web` and `worker` reach
  it over `backend`, so application code could change "ParishSoft". Also
  rejected: editing the source snapshot tables in PostgreSQL (it bypasses the
  loaders and the promotion the refresh checks run in), a second edits file
  (the fake keeps exactly one mount), and re-reading the file per request
  (recreating one service takes seconds, as the late Family's release does).
