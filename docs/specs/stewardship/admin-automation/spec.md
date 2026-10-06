# Stewardship Admin automation interface

This specification defines a programmatic interface to the Admin portal
([#463](https://github.com/epiphany40223/parishkit/issues/463)). An operator,
script or AI assistant on the deployment host can start any action and read
any state the Admin portal offers, without clicking through pages, under the
same authorization checks, database guards, previews and audit trail as the
page, acting as a named Administrator who approved it once in the browser. The
Admin pages themselves stay defined by the
[administration portal specification](../admin-portal/spec.md), and the role
model by the [overview](../spec.md#actors-and-authorization); this document
states only how the same actions are reached without a browser.

Implementation is the [ADM-11 work package](../../../plans/stewardship/admin-portal.md#adm-11-admin-automation-interface).

## Administrator decisions

The Administrator decided these on October 4, 2026 (recorded on #463); what
changed from the first proposal, and why, is in [decisions](#decisions).
Items 12 to 15 came later that day, on the Administrator's reasoning that the
command line runs only on the deployment host, so anyone able to use it has
already wholly compromised the system, and it should be as useful as possible
to the real Administrator. Items 16 to 19, also on October 4, settled the
last open questions; no decision remains open.

1. **Interface:** host command line only; HTTP API rejected; MCP deferred to
   its own issue.
2. **Identity:** the command line acts as a named Administrator through a
   session that Administrator approves once in a signed-in browser; no service
   account and no installed standing token.
3. **Scopes:** read-only and full.
4. **Who may pair:** Administrators only.
5. **Fresh-gated and irreversible actions** (the
   [fresh-gated actions](#fresh-gated-actions-from-the-command-line), such as
   the final Production confirmation and pre-start withdrawal, and the
   irreversible Testing cleanup): no browser step-up and no per-action browser
   approval. A full-scope session may run them; the command line asks for
   confirmation at the prompt, which `--yes` skips. Every action is audited
   under the approving Administrator.
6. **Emergency pause:** no step-up either, as in 5.
7. **Secret replacement:** browser-only for now (reversed by decision 15).
8. **Family-level personal data:** only in owner-only export files from a
   full-scope session; terminal output shows counts and summaries only.
9. **Session lifetime:** independent of the approving browser session; up to
   30 days, chosen at pairing (default and maximum 30); revocable at any time
   from the portal; ends immediately when the approving Administrator loses
   the Administrator role or is removed. Read-only and full sessions follow the
   same rules.
10. **Order:** live-campaign commands first, go-live and withdrawal last.
11. **Session file:** an owner-only (0600) file on the host, passed in to the
    container, so sessions survive deploys and container restarts.
12. **No rate limits:** no per-session limits and no limit on unknown-secret
    lookups. Applying the same rationale, review also dropped the attempt
    limit on the approval page's user code.
13. **High-impact changes:** a full-scope session may make the
    [high-impact changes](#high-impact-changes) (granting Administrator,
    domain sign-in rules, Staff on a domain rule, removing or disabling
    another Administrator), with the same authorization and audit as the
    portal.
14. **Notices and audit are kept:** they inform; they do not restrict.
15. **Secret replacement is allowed** from the command line (reverses 7): the
    secret is read from a file or standard input on the host, never from an
    argument, never echoed or logged, with the same validation,
    confirmation and audit as the portal (see
    [secret replacement](#secret-replacement)).

16. **Approval-page scope and lifetime:** pre-filled from the request,
    lowerable, never raisable (see [pairing](#pairing)).
17. **Notification channels:** a dashboard notice for every event; email and
    Slack, through the operational alert routes, for approval, refused use,
    user, role and rule changes, key and notification changes, and the three
    irreversible actions (see [notifications](#notifications)).
18. **Host binding:** a session is bound to an application-specific digest of
    the host's machine identity, so a host rebuild that changes it ends every
    session, and the wrapper exits 2 when `/etc/machine-id` is missing (see
    [session file](#session-file)).
19. **Role grants need no fresh sign-in** (#383): the portal keeps its
    low-friction role changes without a fresh Google sign-in, and sessions
    follow the portal.

## Purpose and scope

On launch day (October 3, 2026) the operator and an AI assistant on the host
repeatedly had to ask the human Administrator to click through pages: starting
full ParishSoft refreshes, moving the Initial invitation schedule, the go-live
readiness, cleanup, link preparation and confirmation steps, a Family test
email, and reading progress pages. The assistant could observe only through
read-only SQL, and had to guess state from tables and re-check UI labels in
templates.

This interface gives them:

- **parity:** every Admin action and read reachable through one host command,
  `pk-stewardship admin`, calling the same application code as the page;
- **identity:** every command runs as the named Administrator who approved its
  session, with that Administrator's current roles;
- **unattended operation:** once approved, a session runs any action its
  scope allows, including the fresh-gated ones, without the Administrator at a
  browser, for at most 30 days;
- **safeguards:** previews, version checks and the database guards wherever
  the page has them, a confirmation prompt for fresh-gated and irreversible
  actions, and notices to every Administrator when a session is approved and
  when it takes such an action;
- **readable state:** versioned JSON documents for status and progress, and a
  machine-readable command catalog, so automation neither scrapes HTML nor
  queries tables.

It is not a speed-up. A command runs the same per-message code under the same
locks as the page; the throughput work is
[#476](https://github.com/epiphany40223/parishkit/issues/476) (seeding) and
[#447](https://github.com/epiphany40223/parishkit/issues/447) (bulk send).

## Decision summary

- A **host command line** runs inside the web container with
  `docker compose exec -T web pk-stewardship admin …`, through a host wrapper
  that feeds it the session file. There is no HTTP JSON API and no MCP server
  in this work (see [interface choice](#interface-choice)).
- Commands act through an **automation session**: a durable database record,
  approved by one Administrator in a freshly signed-in browser, that lives up
  to 30 days. Its secret stays in an owner-only file on the host; the database
  keeps only its SHA-256 digest (see [automation sessions](#automation-sessions)).
- Each invocation opens a short-lived **command session**, an ordinary Admin
  session row linked to the automation session, so every existing session-bound
  check and SQL guard sees a live Admin session.
- Commands use the **web's restricted SQL login** and a narrow, web-equivalent
  Django assembly with the web's signing keyring, so every SQL guard and grant
  applies as for the page.
- **Fresh-gated actions accept a full-scope automation session** in place of a
  recent Google sign-in, including integration key and backup key
  replacement. Six SQL guards (plus any ADM-13 System health guard already
  installed) and the Python freshness checks change to say so
  explicitly; only the one-time setup wizard stays browser-only (see
  [SQL guards that accept automation sessions](#sql-guards-that-accept-automation-sessions)).
- Previews become **preview and confirm commands** carrying the same signed
  tokens as the page; the typed value or acknowledgement the page asks for is
  asked at the prompt, and `--yes` supplies it.
- **Automation notices** reach every Administrator's dashboard through a new
  notice store; the most important also open new fixed-text operational
  incident kinds, which the existing alert routes send by email and Slack (see
  [notifications](#notifications)).
- Views and commands share **one service layer** through a request-free
  `AdminCaller` and shared read models.
- **Schema change:** four new tables and three new functions; the incident-kind
  constraint and three operational and task-type functions extended; and six
  amended guards (plus any ADM-13 guard already installed), in two forward migrations under the
  [post-launch schema policy](../operations/spec.md#post-launch-schema-policy)
  (see [schema impact](#schema-impact)).

## Threat model

The command runs inside the web container. Anyone who can run
`docker compose exec web` (root or the host's Docker group) already holds the
web's SQL login, Valkey ACL and mounted credentials, and could open a Django
shell. This interface does **not** defend the deployment against a host
operator with that access and hides no data from that operator. That is the
Administrator's premise for this interface: the command line can be used only
on the deployment host, so anyone able to use it already controls the whole
system and gains little from it. It is therefore built to be as useful as
possible to the real Administrator, with notices and audit that inform rather
than limits that restrict.

What it does guarantee:

- **Integrity for well-meant automation.** A command reaches the database only
  through the same service functions, locks and SQL guards as the page, so an
  operator or assistant cannot skip validation by accident, and a direct SQL
  write is never the easier path.
- **Attribution.** Every change is attributed to the named Administrator who
  approved the session, and every state-changing command is in the audit trail,
  marked as coming from that automation session, next to the domain records it
  produced.
- **A recent sign-in behind each session.** The database refuses to create a
  session unless the approving browser session belongs to an Administrator and
  signed in with Google within the last five minutes. SQL proves that recent
  sign-in; that the Administrator actually clicked **Approve** rests on the
  page's CSRF-protected form, as for every other Admin action.
- **No new network exposure.** Nothing new listens on the public origin except
  the approval and access pages, which are ordinary authenticated Admin pages.
- **A useless database copy.** The database stores only a digest of each
  session secret, so a database dump or backup cannot be turned into a session.

### What a stolen session file allows

The session file is the only secret a session needs. It is usable only by a
process inside this deployment's web container (or a restored copy of it),
because only that process holds the web's SQL login on the backend network. A
copy taken elsewhere is useless by itself. In practice "stolen" therefore
means one of:

- an AI assistant or script on the host that was given the file and does more
  than intended, for example after a prompt injection;
- another host account that can read the file and also run Docker;
- a host backup, snapshot or disk image restored elsewhere together with the
  database.

Until it expires (at most 30 days) or is revoked, a full-scope session in such
hands can do, without the Administrator present, everything that Administrator
can do in the portal except the first-Admin setup wizard and campaign purge:

- confirm Production, which starts email to every Family, and withdraw before
  start;
- run Testing cleanup, which deletes Testing data that cannot be restored;
- pause, resume or resolve Family email delivery, and close the Family portal;
- send test email to chosen Families;
- change schedules, campaign content and configuration, which changes what
  Families receive and when;
- change user rules: grant Administrator to another address, add domain rules
  or Staff on a domain, or demote or disable the **other Administrators** who
  would receive the notices; grants outlive revocation of the session;
- change or remove the Slack settings that carry the notices;
- replace integration keys (ParishSoft, Google Workspace, mail, Slack) and the
  backup encryption key, which could cut off refreshes, mail, alerts or the
  ability to read future backups;
- write Family-level personal data into export files on the host;
- acknowledge notices and security events on the approving Administrator's
  own dashboard (each other Administrator still sees them until they
  acknowledge).

So the detective controls below can be weakened by the session they watch:
a session that first removes the other Administrators and Slack leaves only
the approving Administrator's own dashboard and email. The Administrator
accepted this (decision 13): someone holding the file on the host could make
the same changes through a Django shell, without any notice at all.

Host access already exposes the installed credentials themselves, so replacing
them adds little an intruder lacked (decision 15). One side effect is
accepted: the secret request guards' automation clause is keyed by principal
and sign-in instant, and the approving browser session shares that instant,
so for the session's life (up to 30 days) that browser session would also
pass those two SQL checks; Python's `require_fresh` still refuses it once its
own five-minute window has passed. It cannot run the setup
wizard, purge a campaign, act as anyone but its Administrator, extend its own
lifetime, approve a new session (approval needs a Google sign-in in the
browser), or print credentials. A read-only session can read status and
progress, counts and summaries, and nothing else.

### Mitigations

- **Administrators only, approved in the browser.** Only an Administrator can
  approve a session, and only from a browser session that signed in with
  Google within five minutes; the SQL guard on the session table enforces both.
- **Bounded lifetime.** At most 30 days, requested at pairing; the approver can
  shorten it, never lengthen it. The command line warns on standard error when
  a session has less than 72 hours left.
- **Narrow scope by choice.** A read-only session cannot change anything. The
  scope is requested at pairing and the approver can lower it, never raise it;
  the operator guide recommends read-only sessions for assistants that only
  watch, and the shortest lifetime that fits the task.
- **Revocation and listing.** The [Automation access](#revocation-and-listing)
  page lists the Administrator's own sessions and every live session of any
  Administrator, with label, scope, deadline, last use and host, liveness
  computed at read time, and any Administrator may revoke any of them;
  revocation takes effect at the next command, in Python and in SQL.
- **Ends on role loss, removal, recovery and restore.** Every command, and every
  guard that accepts automation, re-checks that the Administrator is still a
  live, enabled portal user with the Administrator role and that no offline
  Admin-access recovery has happened since approval; the restore procedure
  revokes every session with an operator command (see
  [session rules](#session-rules)).
- **Secret kept out of reach.** A 256-bit secret, made on the host, kept in an
  owner-only file outside every container, passed on standard input, never
  printed or logged, and stored in the database only as a digest.
- **Host binding.** Each session records an application-specific digest of the
  host's machine identity; use with a different digest is refused and revokes
  the session (see [session file](#session-file) for its limits).
- **Audit and notices.** Every state-changing command has its own audit event.
  Approval, **every** fresh-gated or irreversible action, every change a
  session makes to user rules or notification settings, and every refused use
  create a dashboard notice for every Administrator; the most important also
  go out by email and, when configured, Slack, as the
  [notifications](#notifications) table states. Slack is optional, so no
  notice depends on it.
- **Prompt by default.** Fresh-gated and irreversible commands show the preview
  and ask for the page's typed value; a run without a terminal fails at once
  unless it passes `--yes` explicitly.

### Accepted trade-off

The first design required a human Google sign-in, and for irreversible steps a
browser approval of the exact action, every time. The Administrator chose on
October 4, 2026 to drop that, because the command line exists for agents and
larger-scale automation that must run without a person approving each action.
The consequence, accepted knowingly, is that whoever holds a full-scope session
file on the host can take irreversible, Family-visible actions in the
Administrator's name for up to 30 days, and the controls above are mostly
**detective** (audit, listing, notices, revocation) rather than **preventive**.
A host operator could always force such actions through a Django shell, but
that was a deliberate, visible bypass; this interface makes it the supported
path, with attribution. Since such a holder already controls the host, limits
on the command line would hinder the Administrator more than an intruder, so
there are none (decision 12). The remedies are to give assistants read-only
sessions unless they must act, to choose short lifetimes, to keep a second
Administrator who receives the notices, and to revoke a session as soon as its
task is done.

## Interface choice

### Host command line

`pk-stewardship admin <area> <verb> [options]`, run in the web service with
`docker compose exec -T web`, is the only interface this specification
requires. It needs no new listener, reuses the operator's existing host access
and the in-container command pattern of `health`, `load-check`,
`source-form-check` and `engagement-backfill`, and keeps the
[architecture's public-interface rule](../architecture/spec.md#public-interfaces)
(no public REST or GraphQL API) intact. An AI assistant uses it over the same
SSH session it already uses for read-only diagnostics.

The host wrapper, `tools/stewardship-ops/pk-admin`, joins the operator scripts
of [#459](https://github.com/epiphany40223/parishkit/issues/459). It is a POSIX
shell script because the host has Docker but not the ParishKit Python package.
It supplies the Compose project and file arguments and the web configuration
path, makes the session secret at pairing, chooses and reads the session file,
sends the [preamble](#session-file), decides whether to forward standard
input, and, from PR 8, fetches [export files](#personal-data-on-the-command-line).
It holds no other behavior: every rule stays in the package. The
[operator guide](../../../guides/stewardship-admin-automation.md) also shows
the same steps by hand. The wrapper has its own tests in CI, run against a
fake `docker` on `PATH`, covering file creation and modes, session selection,
the preamble and input forwarding, and export fetching once PR 8 adds it.

### HTTP JSON API (rejected for now)

A token-authenticated `/admin/api/` would allow remote tools, but it widens the
attack surface of the public origin, needs CSRF-free bearer authentication,
rate limits and scoping that the browser session does not, and contradicts the
architecture's rule against a public API. If a remote need appears, a later
specification can expose the same service layer over HTTP.

### MCP and tool wrappers (deferred)

An MCP server or tool wrapper can follow later as a thin adapter that runs
`pk-admin` over SSH and passes its JSON through. It must not hold its own
credentials or add actions. It is tracked as its own issue.

## Automation sessions

An automation session is a durable record that lets the command line act as
one Administrator until it expires or ends. It is a delegation from a
Google-authenticated Admin session, not a new way to sign in; the Google-only
rule in
[identity and session security](../architecture/spec.md#identity-and-session-security)
stands. In LOCAL, where Google sign-in is replaced by the
[local test sign-in](../local-environment/spec.md#local-test-sign-in), the
approval page accepts that sign-in's step-up exactly as other Admin pages do.

### Session records

Two new tables hold the sessions (see [schema impact](#schema-impact) for why
a table is now required):

- **`stewardship_automation_session`**, one row per approved session:
  - `id` (UUID), `created_at` (the approval instant), `updated_at`, `version`,
    `actor_id` and `correlation_id`, as for other mutable records; on an
    ending update `actor_id` is the Administrator who revoked it, or null when
    the system ended it;
  - `principal_id`: the approving Administrator, as whom every command acts;
  - `approving_session_id`: the browser `PortalSession` that approved it (an
    opaque UUID, kept after that row is cleaned up);
  - `authenticated_at`: that browser session's Google sign-in instant, at most
    five minutes before approval;
  - `expires_at`: at most 30 days after approval;
  - `scope`: `read_only` or `full`;
  - `label`: 1 to 64 printable characters describing the session (for example
    `launch-ops assistant`); the operator guide says to put no personal data
    in it, and pages, email and Slack escape it as text;
  - `secret_digest`: the lowercase hexadecimal SHA-256 of the session secret,
    unique;
  - `host_digest`: the [host digest](#session-file) given at pairing;
  - `last_used_at`: the start of the latest admitted command;
  - `revoked_at` and `end_reason`, both set together, once.
- **`stewardship_automation_login`**, one row per command session:
  `portal_session_id` (primary key; an opaque UUID with no foreign key),
  `automation_session_id` (a foreign key to the session table) and
  `created_at`. Rows cannot be updated. `cleanup_admin_sessions`, run by the
  [maintenance task](#maintenance-task), deletes the link rows whose command
  session no longer exists after it deletes Admin session rows, and the guard
  admits a delete only in that case. Attribution
  after that rests on the [command events](#audit-attribution).

A session is **live** while `revoked_at` is null, `expires_at` is in the
future, its principal is an enabled portal user whose current rule grants
Administrator (the projection of `stewardship_export_authorized_v1(principal,
true)`), and no offline Admin-access recovery (`stewardship_admin_revocation`)
was recorded after its approval. Listings and guards compute liveness at read
time, so a session whose Administrator lost the role shows as ended at once;
the next command or the [maintenance task](#maintenance-task) then records the
reason on the row.
Session rows are never deleted; like audit events, they are kept for
attribution.

`end_reason` is one of `logout` (the command line's `logout`),
`revoked_by_owner`, `revoked_by_administrator` (another Administrator on Portal
users), `role_lost`, `user_removed`, `recovery`, `restore`,
`revoked_by_operator` (the operator's revoke-all command), `host_mismatch`,
`misused` or `pairing_abandoned`. Expiry needs no reason: a row past
`expires_at` is ended.

### Pairing

Pairing has two steps, so an agent gets the code at once and can relay it:

1. **Start.** The operator runs
   `pk-admin login start --name <name> --label <text> --expect-email <address> --scope read-only|full [--days N]`.
   `--name` names the
   session file and is 1 to 32 characters from `a-z`, `0-9` and `-`; the
   label is free text shown on the pages. `--expect-email` and `--scope` are
   required; `--days` is 1 to 30 and defaults to 30. The wrapper creates the
   session file (see [session file](#session-file)) with a fresh 256-bit
   secret and runs `pk-stewardship admin login start` with the same options
   and the preamble.
2. The command computes the secret's digest and stores a pairing record in
   the Valkey namespace the web ACL already grants, with a ten-minute expiry:
   the secret and host digests, name, label, expected email, requested scope
   and days, under `stewardship:auth:v1:automation-pairing:<user code>`
   (written with `SET NX`; a collision draws a new code), plus a key from the
   secret digest to the user code. The user code is eight unambiguous letters
   and digits. The secret itself never leaves the command's memory. It prints
   at once, flushed, one document with `final` false and `result` holding
   `user_code`, `approve_url` (`<origin>/admin/users/automation/approval/`,
   built from the route by name, never written out) and
   `expires_at`, and exits 0.
3. The Administrator opens the link in a browser signed in to the portal. The
   page requires the Administrator role and fresh Google authentication (the
   ordinary [step-up](../architecture/spec.md#identity-and-session-security)).
   A sign-in older than five minutes is never refused: the page renders its
   **Confirm with Google** step and keeps **Continue** and **Approve**
   unavailable until the sign-in is fresh, as the backup key page does. It
   then asks for the user code. Code entry has no attempt limit (applying
   decision 12's rationale): only an Administrator who just signed in with
   Google reaches the field, a wrong code identifies no pairing record, and
   because `--expect-email` is required, a correct guess can only approve a
   pairing that expects the guesser's own email. The code's ten-minute life
   and 40 bits of entropy suffice, and no Valkey failure can block approval.
   The page refuses when the signed-in email differs from the expected email,
   and that refusal shows no label, scope or other detail of the pairing.
4. The page shows the label, the expected email, the requested scope as plain
   text, the first 12 characters of the requesting host's digest, and the
   requested lifetime and scope, both pre-filled and lowerable but not
   raisable. Against a confused deputy it says to approve only a code the
   Administrator started in their own terminal, or that someone they trust
   started for them just now. It gives a plain warning that a full-scope
   session can run every Admin action the
   approver can, including Production confirmation, withdrawal and Testing
   cleanup, without asking again, until it expires or is revoked. A
   CSRF-protected **Approve** button submits it. The page follows the Admin
   confirmation guidance of
   [#523](https://github.com/epiphany40223/parishkit/issues/523) (G27): one
   plain sentence of explanation, the result shown in place, and a way back to
   where the Administrator came from.
5. Approval, in one transaction, re-checks the approver's current
   authorization and inserts the session row with the approving session's
   UUID and `authenticated_at`, records `automation_session_approved` and
   creates the approval [notice](#notifications). The SQL guard refuses the
   insert unless the approving session is live, belongs to the principal, is
   not a command session, signed in within five minutes, and the principal is
   an Administrator. The pairing record is left as it is: `wait` recognizes
   the approval by the new session row's secret digest.
6. **Wait.** `pk-admin login wait --name <name> [--timeout SECONDS]` (default
   and maximum ten minutes, bounded by the pairing record's expiry) runs
   `pk-stewardship admin login wait` with the preamble. It finds the pairing
   record through the secret digest and checks it every two seconds. On
   approval it consumes the record atomically (one Lua `EVAL` deletes both
   keys, and only while the digest still names this code), refuses and
   revokes the session (`misused`) if its principal's email differs from the
   record's expected address, records the use (`last_used_at`), and prints
   the session document (UUID, name, label, principal email, scope,
   deadline), never the secret. A later `wait` with the same secret, after a
   successful one, prints the document again. On timeout it logs
   what it waited for, the limit and the elapsed time, and exits 5 with
   `pairing_pending` while the record lives, so `wait` can run again; once the
   record has expired it revokes a session created with its digest and never
   collected (`pairing_abandoned`), exits 5 with `pairing_expired`, and the
   wrapper deletes the file.

The approving browser session is not changed, and nothing it does later
(sign-out, timeout, a new sign-in) ends the automation session.

### Session file

The session file lives on the host, never in a container:

- **Location.** `<root>/run/admin-automation/<name>.session`, where `<root>`
  is `PARISHKIT_ROOT` or `/opt/parishkit`, as for every other default path
  ([runtime storage](../operations/spec.md#runtime-storage)); `--session-dir`
  in the wrapper overrides the directory. The wrapper runs as the host
  operator account that runs Docker Compose (a member of the Docker group),
  not as the application user (10001). Provisioning leaves `<root>/run` owned
  by the application user with mode 0700, adds a search-only (`x`) ACL entry
  on it for each operator account, and creates
  `<root>/run/admin-automation` (or one directory per operator account) owned
  by that account with mode 0700; each session file is mode 0600 and owned by
  the same account. Deploys, image retargets and container restarts do not
  touch the directory, so sessions survive them.
- **Why not `credentials/`.** The backup mounts the whole `credentials` tree
  read-only into the backup container and archives it (`ARCHIVED_TREES` in
  `backup.py`), and the restore runbook changes that tree's owner to the
  application user, which would make the wrapper refuse the files. The
  `run/admin-automation` directory is in no archived tree, under no
  `run/persistent` store, and mounted into no container. Nothing cleans `run/`
  today; any future cleanup of it must skip this directory. A test checks the
  rendered Compose files and the backup's archived trees for it. A session is
  never restored from a backup; it is paired again.
- **Selecting a session.** `--session <name>`, else the `PK_ADMIN_SESSION`
  environment variable, else the only file in the directory. With several
  files and no choice, the wrapper exits 2 and lists the names.
- **Creation.** The wrapper creates the file with `umask 077` and exclusive
  create (it never overwrites), writing 32 random bytes from the operating
  system as unpadded base64url. It refuses a directory or file with wider
  modes or another owner.
- **Format.** One line, at most 128 bytes:
  `pk-admin-session/1 <secret>`, where `<secret>` is 43 base64url characters.
  Nothing else (no UUID, email or deadline) is stored, so the file reveals
  nothing but the secret.
- **Passing it in.** The file is mounted into no container: mounting it into
  the web service would expose every session to the long-running web process.
  Instead the wrapper sends a **preamble line** on standard input,
  `pk-admin-session/1 <secret> <host digest>`, and every command requires
  `--session-stdin`, which reads exactly that line first. The wrapper then
  forwards its own standard input only when the command line contains a `-`
  input, or from a terminal for a command that prompts (the wrapper lists
  them; none before PR 5); otherwise it closes input after the preamble, so an
  interactive run never waits on a terminal nobody is typing into and a
  prompt in a run without a terminal fails at once (see [command-line
  confirmation](#command-line-confirmation)). A secret given with
  `--secret-file` or `--secret-stdin` travels as one base64 line right after
  the preamble (see [secret replacement](#secret-replacement)).
- **Host digest.** HMAC-SHA256 keyed with the host's `/etc/machine-id` over the
  fixed text `parishkit-admin-automation/1`, in lowercase hexadecimal, as
  `machine-id(5)` recommends for application-specific identifiers; the raw
  machine identity never leaves the host. The wrapper computes it on each run;
  it is never stored in the file. Without a readable `/etc/machine-id` the
  wrapper exits 2.
- **Host binding.** The command compares the preamble's host digest with the
  session's `host_digest`. A mismatch refuses the command, revokes the session
  (`host_mismatch`) and creates a [notice](#notifications). This catches a file
  used with the stock wrapper on a different machine. It does not catch a
  disk image, snapshot or cloned virtual machine that keeps `/etc/machine-id`
  (the restore procedure covers a restored database by revoking every
  session), nor
  anyone with Docker access, who can send any digest. A host rebuild that
  changes the machine identity ends every session, which must then be paired
  again.
- **Ending.** `pk-admin logout` runs `pk-stewardship admin logout` and then
  deletes the file. A revoked or expired session's file is useless, and the
  wrapper deletes it when the command reports `session_ended`.

An operator may hold several sessions, each in its own file.

### Command sessions

Each invocation, after the preamble, admits itself in one short transaction:

1. It finds the session row by `secret_digest`. No row means exit 5 with
   `session_missing` and a process log entry for every one; the first unknown
   secret from a
   host digest in an hour also creates a dashboard notice and observes
   `automation_refused` (this groups the notices; it never delays or refuses
   a command).
2. It locks the row and checks that the session is [live](#session-records)
   and the host digest matches. A session that is no longer live because of
   role loss, removal or recovery is revoked with that reason; the command
   exits 5 with `session_ended`.
3. It creates a new Django session and `PortalSession` for the principal
   through a dedicated function (not `issue_admin`, which ends the request's
   own session; it shares only the row-creation core) with
   `authenticated_at` equal to the automation session's `authenticated_at`,
   `last_activity_at` = now, and `expires_at` the earlier of now plus four
   hours (the [watch limit](#watching-progress) plus a margin) and the
   automation session's deadline. Its Django session data carries the
   automation marker, the automation session UUID, the scope, the current
   recovery epoch and the current authority fingerprint. It inserts the
   `stewardship_automation_login` row and sets `last_used_at`.

The command session's key never leaves the process. Ordinary admission
(`authenticated_admin`) then runs on it as for a page, so every existing
session-bound check, and every SQL guard that requires a live Admin session
with recent activity, applies unchanged. A long `--watch` records activity on
its command session at most once a minute, as a heartbeat, so the 60-minute
idle checks hold while the process lives. At exit the command sets the command
session's `revoked_at` directly, without an audit event (the automation
session's events carry the attribution); a crashed process leaves a row that
idles out and is cleaned up as usual.

If the principal's roles or Ministries change while a command runs, ordinary
admission on the web would rotate the session. For a command session it
instead ends that command session and refuses the command with exit 5,
without rotating or calling `rotate_token`. The next command re-evaluates the
principal; the automation session itself ends only if the principal is no
longer an Administrator.

### Session rules

- **Lifetime.** A session lives until `expires_at` (at most 30 days after
  approval) or until it ends. Use does not extend it, nothing renews it, and
  there is no idle limit on the automation session itself. Every command
  whose session has less than 72 hours left writes a warning to standard
  error.
- **Role loss, removal, recovery and restore.** A session ends as soon as its
  principal is no longer an Administrator, is disabled or removed, or an
  offline Admin-access recovery is recorded. Every command checks this before
  acting, and every
  [guard that accepts automation](#sql-guards-that-accept-automation-sessions)
  checks it again in SQL, so no command succeeds after the change even before
  the row is marked revoked.
- **Restore.** A restored database holds the restored sessions' digests, and
  the session files survive on the host. The v1 restore is the
  [manual restore procedure](../../../plans/stewardship/v1-launch.md#manual-restore-for-v1-replaces-item-2)
  (the automated [restore](../operations/spec.md#restore) of OPS-06 is
  deferred), so PR 2 adds a step to it and to the backup runbook's real
  restore, after the database is restored and before `web` starts:
  `pk-stewardship revoke-automation-sessions --reason restore`. It is an
  offline operator command in the `admin-recovery` profile, on the existing
  `pk_stewardship_admin_recovery` login (which already revokes Admin sessions
  for `recover-admin`); PR 2 adds to that login's offline grants `SELECT` and
  column-level `UPDATE` (`revoked_at`, `end_reason`, `version`, `updated_at`,
  `actor_id`, `correlation_id`) on `stewardship_automation_session`, and
  `INSERT` on the notice table; offline logins have no column-level grants
  today, so PR 2 extends `offline_grants` and `admit_offline_database` to
  declare and verify them. Like `recover-admin`, it runs with every online
  service stopped (the offline startup lease). It revokes every unrevoked,
  unexpired session with `restore`, writing their audit events and notices
  with plain `INSERT`s, and prints only a count. The operator may also run it
  with `--reason
  revoked_by_operator` to end every session at once. When OPS-06 lands, its
  fenced initialization step does the same.
- **No rate limits.** Commands are not limited per session or per host
  (decision 12); only the `--watch` minimum interval and the page's own
  limits (for example a preview token's lifetime) apply. The approval page's
  user-code entry has no attempt limit either (see [pairing](#pairing)).
- `pk-stewardship admin whoami` prints the session document (UUID, label,
  principal email, current roles, scope, deadline, last use).
- `pk-stewardship admin logout` revokes the session (`logout`) and prints
  `{"ended": true, "end_reason": "logout"}`. If another ending committed
  between admission and logout, nothing changes and it prints
  `{"ended": false, "reason": "already_ended", "end_reason": ...}` with
  that ending's reason; both exit 0, and the wrapper deletes the file.
- `pk-stewardship admin sessions` lists the principal's sessions, live and
  ended in the last 30 days, without secrets or digests.

### Maintenance task

`cleanup_admin_sessions` runs only in tests today, so nothing deletes ended
Admin session rows in production. PR 2 adds an `automation_maintenance` task
type, executed by the general worker (`pk_stewardship_worker`) and produced
by a new scheduler producer at most once an hour (other producers, such as
`produce_collection`, run more often and only when they have work). The task
type is registered wherever task types are listed: the worker's compiled
registry (`runtime_background.py`), `jobs/queue_wait.py`, the task labels in
`jobs/views.py` and `stewardship_task_type_login_v1`. Each run:

- runs `cleanup_admin_sessions` in its bounded batches, which deletes ended
  Admin session rows (browser and command sessions) and their Django sessions,
  then deletes link rows whose command session no longer exists;
- marks sessions that are no longer live but not yet revoked (`role_lost`,
  `user_removed`, `recovery`), recording `automation_session_ended` and a
  notice for each;
- resolves the open automation incidents that have been quiet for an hour
  (see [notifications](#notifications)).

The worker's grants, through the database grant manifest, are what
`cleanup_admin_sessions` and the liveness check use, confirmed against the code
in PR 2: column-level `SELECT` on the session columns the cleanup reads (never
`session_id`, a credential the worker must not read), column-level `UPDATE`
(`revoked_at` with a version bump) and `DELETE` on `stewardship_portal_session`;
`EXECUTE` on `stewardship_admin_session_purge_v1`, which deletes the ended
rows' Django sessions for it; `INSERT` on `stewardship_audit_event` for the
`admin_timeout` and `automation_session_ended` events; `SELECT` on the tables
`stewardship_export_authorized_v1` (executable by everyone) and the recovery
check read; `SELECT` and `DELETE` on `stewardship_automation_login`;
`SELECT` and the same column-level `UPDATE` as above on
`stewardship_automation_session`; `INSERT` on the notice table; and what
resolving its incidents needs on `stewardship_ops_incident`. The session guard
admits the worker's ending update only with the three reasons above.

### Scopes

The approving Administrator confirms or lowers the requested scope, stored on
the row:

- **read-only:** status, progress, audited reads and report reads; no command
  that changes state, sends mail, queues work or creates or downloads an
  export;
- **full:** every command the Administrator's roles allow, including the
  fresh-gated ones.

A scope never adds a capability. Read-only is enforced in the seam, not only
by the command dispatcher: for an automation caller with read-only scope,
`authenticated_admin(activity=True)`, `admit_admin_action` and `require_fresh`
all refuse. The SQL guards that accept automation in place of a fresh sign-in
also require `full`.

### Revocation and listing

The source of truth is `stewardship_automation_session`.

- **Automation access** (`/admin/users/automation/`), a new entry in the
  Users and access menu group (the portal has no separate account menu),
  offered to Administrators only, lists that Administrator's sessions with
  label, scope, approval time, deadline, last use, a short form of the host
  digest and, for ended sessions, the end reason, and below them every live
  session of any Administrator. Any live one is revoked with a CSRF-protected
  in-place POST to `/admin/users/automation/sessions/<session>/`
  (`revoked_by_owner` for one's own, `revoked_by_administrator` otherwise).
  The list stays off Portal users, which the navigation work splits into
  several pages (NAV-15). Approving a new session from this page first asks
  for a fresh sign-in, as the approval page does.
- `pk-stewardship admin sessions` lists the caller's own sessions.

Revocation sets `revoked_at` and `end_reason`, records
`automation_session_ended` with the revoking Administrator as actor, and takes
effect at the next command or guard.

### Refusals in both directions

- **Web.** `authenticated_admin` is the enforcement point. Every Admin view
  reaches it, and so do the `AUTH_ROUTES` session views
  (`/admin/session/status`, `/admin/session/renew`) that authenticate
  themselves; `/admin/logout` checks the marker before `end_admin`. On the web
  path a session whose data carries the automation marker is refused, signed
  out and its automation session revoked (`misused`), with a security warning
  and a notice. A command session's key never leaves the command process, so
  such use means tampering inside the container.
- **Command line.** `AdminCaller.from_automation` accepts only a session
  secret; it never accepts a browser session key, so a copied browser cookie
  cannot drive it.
- **Approval.** Refused for a non-Administrator, from a command session (the
  SQL guard checks `stewardship_automation_login`), without a fresh sign-in,
  and for a mismatched expected email.

### Notifications

Notices are detective controls, in two layers.

**Dashboard notices** carry the detail. The existing security event record
(`stewardship_policy_security_event`) is tied to a configuration activation
and a role rule and cannot carry them, so the
[first migration](#schema-impact) adds:

- **`stewardship_automation_notice`**: one insert-only row per notice, with its
  kind (`approved`, `fresh_gated`, `irreversible`, `policy_change`,
  `refused`, `ended`), the automation session, the command
  type where there is one, the campaign where there is one, the correlation
  ID and the time;
- **`stewardship_automation_notice_ack`**: one row per Administrator and
  notice they have acknowledged.

The Admin dashboard shows every unacknowledged notice, with the session label
escaped as text, to every current Administrator until that Administrator
acknowledges it; acknowledging affects only one's own view.

**Email and Slack** use the existing operational alert path rather than a new
mail purpose. That path is bound in SQL to operational incidents: the outbox
guard, the operational cohort and recipient bindings, the prepare and
dispatch checks, and `stewardship_ops_content_v1`, which renders fixed text
from a closed list of incident kinds and has no place for a label. It sends
to the Administrators of the active configuration and, when configured, to
Slack. So four new incident kinds are added, each with fixed text naming no
session, label or Family, which tells Administrators to review the
automation notices on the dashboard:

- `automation_approved`: "An automation session was approved";
- `automation_irreversible`: "An automation session took an irreversible
  action";
- `automation_policy_change`: "An automation session changed user access,
  integration keys or notification settings";
- `automation_refused`: "An automation session was refused".

They open at CRITICAL so the routes send them at once. As for every incident,
one episode per kind is open at a time: a burst of events opens one episode,
later events in it count as occurrences with the routes' usual repeat notices,
and the [maintenance task](#maintenance-task) resolves an episode after an hour
without events, so the next event sends a new message. Resolving sends the
routes' `resolved` notice, as for every incident that was sent; it is kept,
because it tells Administrators the burst is over and that the dashboard holds
the detail. `stewardship_ops_content_v1` therefore gets resolved-phase text
for the four kinds: "No further automation events of this kind in the last
hour. Review the automation notices on the Admin dashboard if you have not
already." The command line
observes these incidents as the web login, so the incident guard admits the
web for these kinds. Delivery happens after commit and never blocks or undoes
the action.

A new mail purpose with its own content function, recipient binding and
dispatch checks would let messages carry the label and reach a just-removed
Administrator, but would duplicate about seven SQL functions of the
operational path; it is not proposed. Because the routes reach only current
Administrators, a session that removes the other Administrators first leaves
them unnotified; the Administrator accepted that (decision 13).

| Event | Dashboard notice | Email and Slack (operational routes) |
| --- | --- | --- |
| Session approved | Yes | `automation_approved` |
| Production confirmation, withdrawal or Testing cleanup through a session | Yes | `automation_irreversible` |
| Other fresh-gated action through a session (pause, resume, resolution, chosen-Family test, Family portal maintenance) | Yes | No |
| A session changes user rules, replaces an integration key or the backup key, or changes Slack or other notification settings | Yes | `automation_policy_change`, observed when the session submits the change, before the configuration installer applies it; dispatch re-checks Administrator status, so an Administrator the change removes is not reached |
| Refused use (an unknown secret, first per host digest per hour; host mismatch; web misuse) | Yes | `automation_refused` |
| Session ended by revocation, role loss, recovery or restore | Yes | No |

Every event also appears in the [system logs](../admin-portal/spec.md#logs).
Nothing carries the secret, digests or Family data.

### High-impact changes

A full-scope session may make the changes the portal treats as high impact,
with the same authorization, configuration request, security event and audit
as the portal (decision 13):

- granting Administrator to an address;
- creating a domain rule, or adding Staff to a domain rule (the existing
  [high-impact expansions](../admin-portal/spec.md#portal-user-management),
  which also raise their own security event and email);
- removing Administrator from, or disabling, another Administrator.

Each also creates an `automation_policy_change` notice, as does any key
replacement and any change to Slack or other notification settings. The portal
asks for no fresh sign-in for these changes (decision 19, #383), so a session
needs none either.

## Running a command

### Process admission and database login

`pk-stewardship admin` admits itself as `engagement-backfill` does: the
admitted web profile (`admit_online_service` returns the web role), the
lifecycle mounts, a non-offline `StartupLease`, and `admit_runtime_database`
on the **web's own restricted SQL login**. It refuses every other profile
and login, including the migration, installer and superuser logins.

It does not use the offline commands' `configure_operator_database`, whose
random `SECRET_KEY` cannot sign the command session's data or verify a
preview token from an earlier process. Instead it applies the web's
`django_signing` keyring through `SigningKeyring.django_settings()` (active key
and verify-only fallbacks) and the configuration authority store, Valkey and
database settings the web uses. Nothing else is assembled: no HTTP server,
download pool, worker, setup hold side effects or provider clients beyond what
a called service needs. Before any session use it compares its loaded
`django_signing` credential receipt with the receipt the running web published
(the proof `acknowledge-credential` checks) and refuses with exit 2 when they
differ, for example during a key rotation.

It holds at most one database connection and closes it between `--watch`
polls.

### Command shape

- Areas and verbs use their own `argparse` subparser tree in a new module,
  `parishkit.stewardship.admin_cli`, dispatched from `cli.py` like the other
  commands; the flat option whitelist of `cli.py` is not extended per verb.
- `--config` and `--session-stdin` are required on every command, including
  `login start` and `login wait`; the wrapper supplies both.
- Identifiers are canonical UUIDs or the stable keys the pages use (schedule
  slot names, content kinds), never row positions or display labels.
- Free-text inputs the page collects (reasons, notes) are options with the
  page's limits, or `-` to read them from standard input after the preamble.
- Changes to versioned records that have no preview token take
  `--expected-version`, the `version` field of the matching read document; a
  mismatch is refused with `stale_version` and nothing changes.

### Discovering commands

Every area and verb has `--help`. `pk-stewardship admin commands` prints a
machine-readable catalog: for each command its name, the scope it needs
(`none` for the pairing commands, which run before a session exists,
`read_only` for any session, `full` for a full-scope one), whether it changes
state, whether it is fresh-gated, whether it prompts, whether it takes
`--request-key` or `--expected-version`, whether it takes `--watch`, the
audit event it records, its options and positional arguments, its result
fields and the PR that added it. The catalog is generated
from the subparser tree and the read-model projections, and a test keeps it
complete. `commands` itself needs no database: it checks the preamble's form
and prints the catalog.

### Output documents

A command prints exactly one JSON document to standard output, unless it
runs with `--watch`, is `login start` (whose document has `final` false) or
streams an export:

```json
{
  "schema": "pk-admin/1",
  "command": "send progress",
  "correlation_id": "<uuid>",
  "ok": true,
  "final": true,
  "session": {"id": "<uuid>", "expires_at": "<instant>"},
  "result": {}
}
```

- `result` is the command's documented read model projection (see
  [read models](#read-models)). Instants are UTC ISO 8601 strings; counts are
  integers; enumerations use stored values, not translated labels; documents
  for versioned records include `version`.
- Additive changes keep `pk-admin/1`. Removing or renaming a field, or
  changing its meaning, needs `pk-admin/2` and a changelog entry in the
  operator guide.
- Output never contains credentials, session secrets or digests, Family
  access tokens, cipher material or exception text. Family-level personal data
  follows [personal data on the command line](#personal-data-on-the-command-line).
- Prompts, warnings and structured logs go to standard error, never standard
  output.

### Watching progress

`--watch SECONDS` (2 to 300) repeats a passive read and prints
newline-delimited JSON: one complete document per poll, `final` false until
the last. The last document has `final` true and its exit code is the
command's. Only the first read records the page's view event, as a page's
passive polls record none. The interval's maximum keeps every poll well
inside the command session's 60-minute idle limit. A watch stops when:

- the read reaches a terminal state (exit 0);
- `--timeout` passes (default and maximum three hours, matching the progress
  page's give-up limit; exit 7, `watch_timeout`, with the last state);
- the process receives SIGINT (exit 7, `watch_interrupted`, with the last
  state and no traceback); through the host wrapper, Ctrl-C ends only the
  client, because `docker exec` forwards no signal, and the watch in the
  container runs on until another stop
  ([#598](https://github.com/epiphany40223/parishkit/issues/598));
- the automation session ends or expires (exit 5);
- a database or Valkey outage interrupts a poll (exit 3, `unavailable`,
  with no last state; read again).

`--timeout` without `--watch` is a usage error (exit 2), never ignored.

Each poll re-checks that the automation session is live. A Family send that
outlasts an hour needs no renewal. The heartbeat (see
[command sessions](#command-sessions)) renews only a command session that is
still live, so it never revives one that ended or idled out.

### Exit codes and errors

| Exit | Meaning | `error.code` |
| --- | --- | --- |
| 0 | Done | none |
| 1 | Refused by the application; nothing changed | `denied`, `invalid`, `stale_version`, `not_available` |
| 2 | Usage, configuration or admission error before any command ran | `usage`, `configuration`, `credential_mismatch` |
| 3 | Temporarily unavailable before any change; retry | `unavailable`, `busy`, `internal` |
| 4 | Confirmation not given; nothing changed | `confirmation_required` |
| 5 | No usable automation session, or pairing not finished | `session_missing`, `session_ended`, `pairing_pending`, `pairing_expired` |
| 6 | Outcome unknown, or done in part; read status or repeat with the same key or token | `outcome_unknown`, `partial` |
| 7 | Watch timed out or was stopped | `watch_timeout`, `watch_interrupted` |

On failure `ok` is false and `error` holds `code`, the same user-facing
`message` the page would show (never exception text or paths), the request key
or receipt when one exists.
Field errors reuse the closed `web.contracts.ErrorCode` values with
server-owned field identifiers.

Exceptions map in this order, and the first match wins. Order matters:
`StartupBusy` subclasses `ConfigError`, and `FreshAuthenticationRequired` and
`UserFacingDenied` subclass `PermissionError`.

| Exception | Exit |
| --- | --- |
| `StartupBusy`, `LimiterUnavailable` (from a shared domain function), Valkey errors, `DatabaseError` from connection failure, too many connections, lock or statement timeout, or serialization failure, raised before the command's commit | 3 |
| `AuthorityChanging` (a configuration change activating) at any point, admission included; and in a read, any other `ConfigError` after admission (a stuck activation, a restore under review, YAML and database disagreeing), as `unavailable` | 3 |
| Usage errors, other `ConfigError` raised during admission, credential receipt mismatch | 2 |
| Confirmation declined, or end of input at the prompt | 4 |
| Automation session missing, ended, expired or revoked, a host mismatch, a command session ended by a role change, or pairing not finished, including `UserFacingDenied` for an ended session | 5 |
| `PermissionError` (including `FreshAuthenticationRequired`), `ValueError`, `StaleRecordError`, other `ConfigError` raised by a domain function | 1 |
| `admin_reads.NotAvailable` (an unknown task or campaign, or no current campaign), and `ObjectDoesNotExist` from a command that changes nothing, as `not_available`; a bare `LookupError` or `KeyError` is a bug (`internal`) | 1 |
| Any error after a durable commit (for example an export queued but its download failed), or an unexpected error in a state-changing command | 6 |
| An unexpected error in a read | 3, with `internal`; retry at most once, then report it |

A refusal says that nothing was done or sent only where exit 1, 3 or 4
guarantees it.

### Correlation and logging

Each invocation has its own correlation ID, printed in the output and bound to
every log record, audit event and task it causes, so the
[system logs](../admin-portal/spec.md#logs) can filter one command's effects.
Structured logs go to standard error at INFO and above. Every wait (pairing,
`--watch`, lock waits) has a stated limit, and a timeout logs what was waited
for, the limit and the elapsed time.

An error that ends as `internal` or `outcome_unknown` also logs one ERROR
failure line (#612): `startup_rejected` before admission finished and
`task_failed` after it, with the invocation's `correlation_id`, its
`failure_kind` and its
[`error_class`](../operations/spec.md#observability-and-health), never
exception text. The shared correlation ID joins the line to the document,
whose shape does not change; with debug logging on, the traceback follows.
Through the host wrapper, standard error reaches the operator's terminal,
not the web container's log, so the operator reports that line with the
document. It goes to the process log only; a durable copy is deferred
to #617.

## Authorization, confirmation and audit

### Same checks as the page

A command performs the same admission as its page, in the same transaction
order, on its command session: current principal and capability (for example
`CONFIGURE` for refresh and schedules, `BACKGROUND_WORK` for task retries,
`CAMPAIGN_REPORT` for campaign reports, `SYSTEM_LOGS` for logs), object scope,
current campaign and runtime mode, then the domain function and its SQL guard.
The automation scope is checked first and can only narrow the result.
Browser-specific defenses (CSRF, `SameSite` cookies, the HTML form field
whitelist) are replaced by the automation channel check; no web request can
select the command path. A state-changing command's admission records
activity (`activity=True`) as a page action does.

### Fresh-gated actions from the command line

On the page these actions need a Google sign-in within the last five minutes.
From PR 5 on, a **full-scope, live automation session** stands in for that
sign-in, with no browser step:

- the delivery controls: pause (including the emergency pause), resume and
  closed-campaign resolution;
- chosen-Family test sends;
- Family portal maintenance (closing and reopening the Family portal);
- the Production confirmation;
- pre-start withdrawal;
- integration key replacement, finish switching and the backup key change
  (see [secret replacement](#secret-replacement));
- the [System health](../admin-portal/spec.md#health-actions) actions
  (ADM-13): take a backup now, clear a halted mail sender, accept a large
  ParishSoft change once, and turn off debug logging. PR 5's migration
  amends any of their SQL guards already installed to accept
  `stewardship_automation_fresh_v1`, and a guard installed later includes it
  from the start (see
  [shared rules for health actions](../admin-portal/spec.md#shared-rules-for-health-actions)).

Testing cleanup is not fresh-gated on the page: it is irreversible and asks
for an acknowledgement, and its guard requires only a live Admin session whose
sign-in instant it records, which a command session provides. It prompts like
the fresh-gated actions. The first design's per-action browser approval for
cleanup, confirmation and withdrawal is removed.

In Python, `require_fresh` takes the caller. For a web caller it is unchanged.
Until PR 5 it refuses every automation caller outright. From PR 5, for an
automation caller it locks the automation session row (`FOR SHARE`), requires
it live and `full`, and returns the command session's `authenticated_at`,
which the domain function writes into its record as the page writes the fresh
instant. The call sites that accept automation are `campaign_family_test`,
both in `delivery_control_commands`, both in `withdrawal_commands`, the Family
portal maintenance switch, `confirmation_commands`, and the `require_fresh`
call in `confirmation_views` (in `_fresh_after_cleanup`).

The Production confirmation also requires, in Python, a sign-in after cleanup
finished: `confirmation_commands` refuses when the fresh instant precedes the
cleanup's `complete` event, and the confirmation view's
`_fresh_after_cleanup` shows step-up guidance on the same comparison. For an
automation caller both treat the post-cleanup requirement as met (the view's
logic moves to the service layer in PR 12), matching the
[SQL change](#sql-guards-that-accept-automation-sessions).

### SQL guards that accept automation sessions

The page's fresh sign-in is also enforced in SQL. Each of these guards admits
a row only when a live `stewardship_portal_session` for the actor has
`authenticated_at` equal to the row's recorded instant **and** within the last
five minutes:

| Guard (baseline file) | Protects |
| --- | --- |
| `stewardship_delivery_control_guard_v1` (`delivery_control.sql`) | Pause, resume and resolution commands |
| `stewardship_family_mail_test_guard_v1` (`family_mail_tests.sql`) | Chosen-Family test sends |
| `stewardship_production_confirmation_guard_v1` (`production_confirmation.sql`) | The Production confirmation; it also requires the sign-in to follow cleanup's completion |
| `stewardship_production_withdrawal_guard_v1` (`production_withdrawal.sql`) | Pre-start withdrawal |

Each changes so that the five-minute clause becomes "within the last five
minutes **or** `stewardship_automation_fresh_v1(login.id, actor)`", keeping
every other clause (the live, unrevoked, unexpired session, 60-minute activity,
equality with the recorded instant, the Administrator check). For the
Production confirmation, the post-cleanup clause is likewise satisfied by an
automation session instead of a sign-in after cleanup: the confirmation prompt
and the [notices](#notifications) replace it, as decided.

`stewardship_automation_fresh_v1(login uuid, actor uuid)`, a `SECURITY
INVOKER` function, returns true only when a `stewardship_automation_login` row
links that session to an automation session whose principal is `actor`, whose
scope is `full`, which is [live](#session-records) (unrevoked, unexpired,
principal an enabled Administrator, no later recovery), and whose
`authenticated_at` equals the command session's. It is new, created by the
first migration.

These guards' other callers are unaffected: a browser session has no link row,
so for it the predicate is exactly today's. The changes follow the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy):
a new forward migration whose frozen SQL file re-creates the four functions
with `CREATE OR REPLACE`, copied verbatim from the edited baseline files, and
ends with a `DO` block that raises unless each installed body contains the
automation clause. Tests run each changed guard against both its old and new
definition, proving the old refuses an automation command session and the new
admits it while still refusing a read-only, revoked, expired or
non-Administrator session.

Nothing else needs a guard change. Testing cleanup's guard
(`go_live.sql`) and the production cleanup checks (`production.sql`) compare
the recorded instant with a live session's `authenticated_at` without a
five-minute window, and the 60-minute activity checks throughout the schema
are met by the command session.

### Secret replacement

Integration key replacement and the backup encryption key change run from the
command line (decision 15) through the same service functions, validation,
sealing, confirmation and audit as the portal, and the same
[credential installer protocol](../../../guides/stewardship-credential-installers.md),
unchanged.

- **Reading a secret file.** `--secret-file <path>` first refuses a path that
  is not a regular file (`test -f` and not `test -p`), so opening a FIFO
  cannot block, then opens the file once (`exec 3<"$path"`), checks the open
  descriptor, not the path (`stat -L /dev/fd/3`: a regular file owned by the
  wrapper's account with no group or other permissions, 0600 or stricter),
  and reads only descriptor 3, so the file cannot be swapped between the
  check and the read. Any failed check exits 2. The value is never an
  argument. The wrapper's `stat`, `base64` and `/dev/fd` usage is GNU and
  Linux only, like the deployment host.
- **Reading standard input.** `--secret-stdin` from a terminal turns echo off
  (restored by a `trap` on every exit path) and reads until Ctrl-D, so a
  multi-line Google Workspace service-account JSON can be pasted. Piped
  `--secret-stdin` requires `--yes`, checked before anything is read, because
  the same input cannot also answer the prompt. The operator guide warns
  against `echo <key> |` and other forms that leave the value in shell
  history or the process list.
- **Passing it in.** The wrapper sends the value as one line after the
  preamble, `pk-admin-secret/1 <base64>`, by piping the source straight into
  `base64 -w0` (never `$(cat …)` or a shell variable), with the size limit
  enforced by `head -c` on the way in; the value passes only through pipes
  and shell builtins, never through an argument or the environment. It runs
  without `xtrace` and with `ulimit -c 0`; the command sets `RLIMIT_CORE` to 0
  before reading. Both the wrapper (on the raw bytes) and the command (after
  decoding) refuse a value over the page's size limit, and the command also
  refuses a value containing NUL or empty after normalization. The operator
  guide explains that Ctrl-D ends input at the start of a line (or twice
  mid-line).
- **Normalizing.** The command decodes the value and normalizes it exactly as
  the page's `InlineCredentialForm` does: UTF-8 decoding, and surrounding
  whitespace stripped for every target except `google_workspace`, whose JSON
  is kept as given. It then validates and seals it in process as the
  receiving view does and drops the plain value; it never writes it to
  output, logs, exceptions or the audit trail.
- **Commands.** `integration key replace --target parishsoft|google_workspace|slack`
  (`google_workspace` holds the mail key; Slack's token and channel go
  together, as on the page) runs the page's validation, prints the safe
  fingerprint and what will change, prompts, and submits the sealed request.
  `integration key status --watch` follows staged, testing, installing and
  acknowledged progress. `integration key finish-switching` is the page's
  **Finish switching to the new key** (`select_credential`) and prompts.
  `integration dismiss` dismisses a finished change. A failed or expired
  change leaves the old key installed, as on the page.
- **Backup key.** Three steps, as on the page: `integration backup-key
  challenge --public-key-file <path>` returns the proof-of-possession
  challenge; `integration backup-key preview --intent <token>
  --code-file <path>|--code-stdin` submits the proof; and
  `integration backup-key confirm --token …` prompts and makes the same
  configuration request and `backup_key_replaced` security event as the page.
  The paths are host paths the container cannot read: the wrapper reads the
  public key file and the proof code (file or terminal) exactly as it reads a
  secret, and sends each after the preamble on its own tagged base64 line,
  `pk-admin-public-key/1 <base64>` and `pk-admin-proof/1 <base64>`.
  Its configuration request has no SQL freshness check (only Python's
  `require_fresh`), so it needs no SQL change.
- **Setup wizard credentials** stay with the one-time first-Admin wizard,
  which is a permanent exemption (see [setup wizard](#setup-wizard)); its
  offline counterpart is `pk-stewardship bootstrap`. As defense in depth,
  `setup_credentials` refuses an automation caller outright.

Every call site that today requires a fresh browser sign-in for these flows
(`integration_credentials`, `integration_selection_views`,
`integration_views`, `backup_key`, and `admit_admin_action` for
`SECRET_REPLACEMENT`) uses the caller-aware `require_fresh`, so a full-scope
session stands in for the sign-in as for the other
[fresh-gated actions](#fresh-gated-actions-from-the-command-line).
`DESTRUCTIVE_CONFIRMATION` has no caller today; a later workflow that uses it
decides in its own specification whether automation may.

Two secret request guards check the sign-in instant recorded on the request,
not a session row, and gain an automation clause in the
[PR 5 migration](#schema-impact); every existing condition stays, including
the setup-install branch and the rule about empty required consumers:

- `stewardship_sealed_intake_admission_v1`, on insert of a
  `stewardship_secret_request` by the web login, refuses a recorded instant
  older than five minutes by the clock. It also admits an instant for which
  `stewardship_automation_fresh_principal_v1(requested_by, instant)` is true.
- `stewardship_secret_state_v2`, at the staged-to-testing step, refuses a
  recorded instant more than five minutes before the request's own
  `created_at`. That step runs as the target's installer login
  (`pk_stewardship_credential_<target>`), so it gains the same alternative
  through the same function.

`stewardship_automation_fresh_principal_v1(principal uuid, instant
timestamptz) RETURNS boolean` is true only when a live, full-scope automation
session of that principal has exactly that `authenticated_at`, and false
otherwise: its body is a single `EXISTS` over `public.`-qualified tables and
functions, it is not `STRICT`, and it never returns NULL. Because the
installer logins can read no automation table, and their admission
(`admit_installer_database` through `admit_grants`) refuses unlisted definer
routines and extra grants, it is a `SECURITY DEFINER` function owned by the
schema owner with a fixed `search_path` (`pg_catalog, public, pg_temp`).

- **Privileges.** The migration revokes it from `PUBLIC` (`REVOKE ALL ON
  FUNCTION … FROM PUBLIC`, as for `stewardship_family_login_v1`). `EXECUTE` is
  not granted by the migration: it comes from `provision_grants` through
  `runtime_functions(role, target)` in the `database-grants` step. PR 5 adds
  the function to `runtime_functions(ServiceRole.WEB)` (otherwise web
  admission, which checks the granted functions, would fail) and makes
  `runtime_functions` target-aware so the three credential installer targets
  (`parishsoft`, `google_workspace`, `slack`) each get it.
  `admit_installer_database` passes
  `functions=runtime_functions(ServiceRole.CREDENTIAL_INSTALLER, target)` to
  `admit_grants`. Admission tests cover the web and each installer login. The
  deploy order is the migration, then `database-grants`, then service start.
- **Calling it.** PostgreSQL checks `EXECUTE` when an expression is
  initialized, even in an untaken `CASE` branch, so each guard calls it in its
  own statement reached only on the web or installer path that needs it, and
  tests it as `NOT coalesce(stewardship_automation_fresh_principal_v1(…),
  false)`.
- **Revoked mid-flight.** If the session is revoked or expires between
  submission and the installer's test, the staged-to-testing step is refused.
  The installer logs a failure on each pass, retrying with its usual backoff
  of at most 60 seconds, until the staging lifetime (one hour) expires; that
  target's installer queue is blocked until then. The request then expires,
  the old key stays installed, and `integration key status` and the page show
  the change as expired.

The setup guards (`stewardship_setup_install_binding_v1`,
`stewardship_setup_secret_guard_v1`) do not change.

### Command-line confirmation

Prompting commands land in PR 5 or later, with the prompt itself. Every
fresh-gated command, every Testing cleanup and every command whose page
asks for a typed value or an irreversibility acknowledgement asks at the
prompt before it acts. It writes the preview summary (campaign, counts, what
cannot be undone) to standard error, then asks for the page's typed value
(`Production` for the Production confirmation) or otherwise `yes`, reading the
answer from standard input after the preamble. Any other answer, or end of
input, changes nothing and exits 4 with `confirmation_required`; because the
wrapper closes input when it has no terminal, a run without a terminal and
without `--yes` fails at once.

`--yes` answers every prompt in that invocation, and the command passes the
typed value and acknowledgement to the domain function exactly as the page
does after a person types them. A prompting command that reads a `-` input
requires `--yes`. The structured log records `confirmation` as `prompt` or
`yes` for each prompting command; the audit trail does not record it.

### Audit attribution

The SQL context check (`stewardship_safe_context_v1`, mirrored by
`audit/schemas.py`) accepts only a closed list of context keys and no free text,
and event types must match `^[a-z][a-z0-9_]{0,63}$`. Attribution therefore
uses actors, subjects, the durable session row and correlation IDs, with no
new context keys:

- **Actor.** `actor_kind` stays `portal_user` and the actor is the approving
  Administrator, so existing logs, reports and SQL checks see that
  Administrator. The session row records who approved it (`principal_id`,
  `approving_session_id`) and when.
- **Domain records** carry the command session's `PortalSession` UUID wherever
  the page records a session; until session cleanup,
  `stewardship_automation_login` maps it to the automation session.
- **Command events.** Each state-changing command records one event of type
  `admin_cmd_<area>_<verb>` (for example `admin_cmd_refresh_start`), with the
  Administrator as actor, the **automation session UUID** as subject, the
  invocation's correlation ID, and context `outcome` only. Because the session
  row is kept, this event identifies the channel, label and approver
  permanently.
- **Read commands** record exactly the view events the page records (for
  example `family_codes_viewed`, `delivery_viewed`, `system_logs_viewed`), and
  no others.
- **Session events.** `automation_session_approved` (subject: the new
  session), `automation_session_ended` (subject: the session; the actor is the
  revoking Administrator or none; the reason is on the row) and
  `automation_session_refused` (subject: the session when one was identified;
  written for host mismatch, web misuse and repeated unknown secrets).

Every new event type is registered in code: types written through
`record_action` get an `audit.schemas.Action` member, direct writes get
`DIRECT_AUDIT_TYPES` entries, and each type gets a
`log_descriptions.DESCRIPTIONS` sentence, which `test_log_descriptions.py`
enforces. Contexts use the `action` context schema with only `outcome`, one
of `succeeded`, `denied` or `failed`. Command names map to event types by
lowercasing and replacing hyphens and spaces with `_`, so
`go-live confirm-preview` becomes `admin_cmd_go_live_confirm_preview`. A test
checks that every command's type matches the event-type pattern within 64
characters.

The logs page shows "via automation session *label*" on every event that
carries an automation session UUID as subject or shares a correlation ID with
an `admin_cmd_*` event.

## Previews and confirmations

### Preview and confirm

Where the page shows a server-built preview and a confirm button, the command
line has a `preview` verb and a `confirm` verb. `preview` runs the same preview
function and prints its summary plus `preview.token`, the same signed binding
the page embeds. `confirm --token <token>` (or `--token -` for standard input)
calls the same confirm function, which re-checks authority, versions,
inventory and expiry exactly as for the page, after the
[confirmation prompt](#command-line-confirmation) where one applies. Tokens are
bound to the portal user, not the channel, so a preview made in the browser can
be confirmed from the command line and vice versa.

A token is not a secret but is single-intent and expires on the page's limit
(typically five minutes). An expired or stale token is refused with
`stale_version`, and the operator previews again.

### Typed confirmations and acknowledgements

The values the page makes a person type or tick are asked at the prompt, not
taken from options, so a script must either answer the prompt or pass `--yes`
deliberately:

- Testing cleanup and pre-start withdrawal (whose deleted Testing data cannot
  be restored): the irreversibility acknowledgement, answered `yes`;
- Production confirmation: the typed value `Production`;
- a reason where the page asks for one: `--reason` (or `-`), which is input,
  not confirmation, and `--yes` does not supply it;
- later guarded workflows (reopen, archive, return to Testing): their own
  typed values, defined when those workflows land.

### Idempotency and retries

- Keyed commands (refresh, test sends, configuration requests, exports) accept
  `--request-key <uuid4>`. Clients should pass their own key. Without one the
  command generates a key and writes it to standard error before it acts, so
  it survives a crash, and also returns it in the result. Repeating a command
  with the same key returns the original durable receipt, as a repeated page
  submission does.
- Token-based confirms are idempotent through the binding key.
- After exit 6 the operator reads status or repeats with the same key or
  token; a command never guesses that a failure means nothing happened.

## Shared service layer

### Caller seam

Today the command modules (`delivery_control_commands`, `go_live_commands`,
`confirmation_commands`, `withdrawal_commands`, `campaign_family_test` and
others) and several read helpers take a Django `request`, but use only its
session, its `PortalSession` and its CSRF state. The seam replaces that
parameter with an `AdminCaller` in a new module,
`parishkit.stewardship.accounts.admin_caller`. It is distinct from the
existing chrome module `accounts/admin_context.py`. Its fields:

- `session`: the Django session store, holding the recovery epoch, the
  authority fingerprint and, for automation, the marker;
- `portal_session`: the locked `PortalSession` row (the command session for
  automation), set by admission where views set `request.portal_session`
  today;
- `principal`: the admitted principal, set by admission;
- `channel`: `web` or `automation`, set only by its constructor;
- `automation_session_id` and `scope`: the automation session and its scope;
  `None` and `full` for `web`;
- `correlation_id`: the request's or invocation's correlation ID;
- `campaign_id`: the campaign the caller targets, where there is one.

`AdminCaller.from_request(request)` is the only web constructor. It asserts
the CSRF-processed POST under `/admin/` that `admit_admin_action` asserts today,
for state-changing calls. On authority rotation it keeps today's behavior,
including `rotate_token(request)`. `AdminCaller.from_automation(secret,
host_digest)` is the only command-line constructor; it performs the
[command session](#command-sessions) admission. It is called only from
`admin_cli`, which a test enforces. It never rotates and never touches CSRF.

`principal`, `authenticated_admin`, `require_fresh`,
`admit_admin_action` and the domain command functions take the caller, and
every existing check stays in them. Views keep form parsing and rendering
only; command modules keep every rule.

### Extractions by PR

Logic that lives in views moves to service modules in the PR that first needs
it, in its own commit, with existing view tests unchanged except for call
signatures:

- **PR 1:** `sessions.authenticated_admin`, `require_fresh`,
  `privileged_actions.admit_admin_action` and `_actor`,
  `admin_editing.principal`.
- **PR 3a:** the home summary's caller use (`admin_dashboard.observe`);
  `presence.active_families` (counts only, `presence.active_count`); send
  progress and history reads from `send_progress_views` and
  `send_history_views` (`jobs.send_reads`); background task reads from
  `jobs/views` (`jobs.task_reads`); the schedule read from `schedule_views`
  (`accounts.schedule_reads`).
- **PR 3b:** `go_live_inputs.collect_inputs` and the Production progress
  read (`confirmation_progress.progress`) take the caller, and the
  readiness page's recent cleanup requests move to
  `go_live_inputs.recent_cleanup_requests`.
- **PR 4:** the schedule preview and confirm from `schedule_views._preview`
  and its POST handling; configuration request status from
  `ministry_views.configuration_request`.
- **PR 5:** the automation branch of `require_fresh` at every call site listed
  under [fresh-gated actions](#fresh-gated-actions-from-the-command-line) and
  [secret replacement](#secret-replacement), including the post-cleanup check
  in `confirmation_commands`.
- **PR 6:** the refresh request from `refresh_views._request`; sample and
  chosen-Family tests (`campaign_mail`, `campaign_family_test`).
- **PR 7:** `delivery_control_commands` onto the caller; the maintenance
  switch from `family_maintenance_views`.
- **PR 8:** report and export reads and export actions from the report and
  export views; log reads and export from `log_views`.
- **PR 9:** `delivery_views.preparation_retry`,
  `export_views.retry_cleanup_command`, delivery detail and resolution, and
  refusal clearing from `delivery_views`.
- **PR 10:** campaign, content, Ministry, parish, branding and integration
  settings (including the backup folder probe) from `campaign_views`,
  `content_views`, `campaign_ministry_views`, `share_views`, `talent_views`,
  `parish_views`, `ministry_views` and `integration_views`.
- **PR 11:** user, rule, assignment, chairperson, event and follow-up actions
  from their views.
- **PR 12:** `go_live_commands` and `go_live_views.testing_families`, cleanup
  retry and cancel, the link preparation controls in `activation_views.links`,
  `confirmation_views._fresh_after_cleanup`, `confirmation_commands`, the
  Production preparation retry (`confirmation_progress.retry`) and
  `withdrawal_commands`.

### Read models

Each read comes from the function the page uses, returning a typed object. A
read model's `to_document()` is a **defined projection**, not a copy of what
the template shows. Its fields are listed in the operator guide and the
[command catalog](#discovering-commands) and contain no Family names, emails,
addresses, phone numbers, manual codes, access tokens or financial detail.
The page and the command read through that same function, moved out of the
view into a request-free module (`admin_dashboard.observe`,
`jobs.task_reads`, `jobs.send_reads`, `accounts.schedule_reads`) or, where
the read already lived outside the view, taking the caller instead of the
request (`go_live_inputs.collect_inputs`, `confirmation_progress.progress`),
so they cannot drift in what they read. Templates keep rendering from that
function's result, so the pages are unchanged; the document is the read
model's projection of the same result. Read models never take the
`request`.

### Rules for new Admin actions

From PR 3, where the route-parity test lands:

- every new Admin action or read lands with its service function, its command
  and tests through both paths, or with an exemption recorded in the
  [action inventory](#action-inventory); this includes new actions on an
  existing page, not only new URL names;
- a new fresh-gated action states whether automation may run it; if so, it
  uses `require_fresh` and its SQL guard uses
  `stewardship_automation_fresh_v1`; if not, its specification says how it
  refuses an automation caller;
- the route-parity test lists every `admin:` URL name and fails when one has
  neither a command nor an exemption in `admin_parity.LEDGER`, or when an
  entry names no route. Exemptions are **permanent** (web-only
  by nature, with the reason), **pending** (with the PR that removes them) or
  **deferred** (waiting on a decision, which is named).

## Personal data on the command line

Command output can end up in terminal scrollback and an assistant's context.
This limits accidents; it does not hide data from the host operator (see
[threat model](#threat-model)).

- Status and progress commands return counts, states and identifiers only.
- Reads whose page shows Family-level personal data (Family codes,
  directories, financial detail, information and Ministry follow-up items,
  delivery recipients, the Testing Families list, the names behind
  chosen-Family test DUIDs) are offered only as export commands. These need
  full scope.
- **Fetching an export.** The web container keeps nothing: its `/tmp` is a
  `tmpfs` that `docker compose cp` cannot read, and export storage is owned by
  the application user. `pk-admin export fetch <export UUID>` first runs
  `export status`, whose document gives the file name, size and SHA-256
  digest, then `export download --stream`, which writes the file's bytes, and
  nothing else, to standard output and its JSON document to standard error.
  The wrapper writes the stream with `umask 077` and exclusive create to a
  temporary file in `<root>/reports/admin-exports/` (mode 0700, owned by the
  wrapper's account), checks the size and digest, renames it to its final
  name without overwriting, and prints only the path, size and digest. A
  failed check deletes the temporary file. The wrapper refuses to stream to a
  terminal. Synchronous exports (the Testing Families list, `--names` for
  chosen-Family tests) produce an export record and are fetched the same way.
- The command records the same audit event as the page download.
  `pk-admin exports clean` deletes the fetched files; the operator guide asks
  that they be deleted once used, as for every report export.
- Chosen-Family tests take ParishSoft Family DUIDs. The preview prints the
  DUIDs with their eligibility, without names; `--names` produces an export
  with the names.

## Action inventory

This is the parity ledger: every `admin:` URL name at the time of writing,
and the separate actions some pages carry, grouped by area. "Command" names
the planned command and its PR; "Permanent", "Pending" and "Deferred" are
exemptions.

### Status and session routes

| URL names | Command or exemption |
| --- | --- |
| `index`, `background_counts` | `status` (PR 3a) |
| `presence` | `status`, its `presence.count` (PR 3a; see below) |
| `login`, `logout`, `session_status`, `session_renew` | Permanent: browser sign-in and session chrome; `login start`, `login wait`, `logout`, `whoami`, `sessions` and `commands` cover the automation side (PR 2) |
| `maintenance` | Permanent: the status page the access gate shows |
| `automation_access`, `automation_approval`, `automation_session` (revoke) and `automation_notices` (acknowledgement) (new) | Permanent: these pages are the human side of the interface (PR 2) |
| `response_dashboard` ([#517](https://github.com/epiphany40223/parishkit/issues/517)) | `report responses` with counts (PR 8) |
| `response_list`, `response_list_export` | Family-level rows, so export only: `export responses` (PR 8) |

The inventory's `status presence` is folded into `status` as its
`presence.count`, because a word cannot be both a command and an area in
the subparser tree (default, pending Administrator confirmation). `status`
also carries the Administrator's count of unacknowledged automation
notices, as the dashboard shows them.

`schedule show` reads the current campaign, or the campaign `--campaign`
names. As on the Mail schedules page, an unknown campaign, or no current
campaign, is `not_available`; a campaign that is not the current one, or
that background work holds while mail is sent, is `stale_version`. Its
`version` is the applied configuration's digest, the base a schedule change
(PR 4) is previewed against.

### Source refresh

| URL names | Command or exemption |
| --- | --- |
| `source_refresh` | `refresh start`, `refresh status` (PR 6) |
| `background_task_page`, `background_task_status` for the run | `task show --watch` (PR 3a) |

See [manual ParishSoft refresh](../admin-portal/spec.md#manual-parishsoft-refresh).

### Schedules and configuration

| URL names | Command or exemption |
| --- | --- |
| `schedule_settings` | `schedule show` (PR 3a); `schedule preview`, `schedule confirm` (PR 4) |
| `configuration_request` | `config request show --watch` (PR 4) |
| `campaign_settings`, `campaign_new`, `campaign_clone` | `campaign show`, `campaign preview`, `campaign confirm`, `campaign clone` (PR 10); until #145, creating or copying a campaign is refused ([navigation rule 10](../admin-portal/spec.md#navigation-rules)): commands that go through `confirm` and `_target`, or call `refuse_campaign_creation`, will get the same refusal as the pages; `privileged_actions.configuration_request` has no such check |
| `campaign_ministries`, `share_settings`, `talent_settings` | `campaign ministries`, `campaign shares`, `campaign talents` (PR 10) |
| `content_catalog`, `content_edit`, `content_revision`, `content_history`, `content_history_revision`, `content_plain_text` | `content list`, `content show`, `content preview`, `content confirm`, `content history` (PR 10) |
| `parish_settings`, `ministries` | `parish`, `ministries` (PR 10) |
| `hosted_files`, `hosted_file_delete`, `hosted_file_rename` | `files list`, `files delete`, `files rename` (PR 10) |
| `artwork_settings`, `artwork_remove` | `artwork show`, `artwork remove` (PR 10) |
| `branding_settings`: **Confirm new logos** | `branding show`, `branding confirm --expected-version …`, a configuration request for logos already uploaded (PR 10) |
| `hosted_file_upload`, `artwork_upload`, `branding_settings` uploads | Pending: file uploads; a later PR may accept `--file` |
| `artwork_preview`, `branding_preview`, `branding_asset` | Permanent: image previews and bytes for the browser |

Every configuration change still becomes a configuration request that the
configuration installer applies; the command reports the request outcome as
the page's request status does. See
[campaign configuration](../admin-portal/spec.md#campaign-configuration).

`schedule preview` and `schedule confirm` take `--campaign` as
`schedule show` does. The Mail schedules page asks for no fresh sign-in and
no typed value, so neither command is fresh-gated or prompts, and neither
waits for PR 5. `schedule preview` admits as the page's form post does,
recording activity, so it needs a full-scope session although it changes
nothing. It takes `--expected-version` (the `version` of `schedule show`,
the page's hidden base digest) and `--changes`, a JSON change document bound
to the page's own forms: a saved schedule named by `id` keeps what the
document leaves out, `delete` removes it, an entry without `id` adds one,
and saved schedules it does not name stay unchanged. The page's form errors
are `invalid` with `error.fields`, each with its field identifier, its
`ErrorCode` (`required` or `invalid`) and the page's message. The document's
format, the
`--expected-version` requirement and the full scope are defaults, pending
Administrator confirmation; the
[operator guide](../../../guides/stewardship-admin-automation.md#schedule-changes)
shows the format. `schedule confirm` records `admin_cmd_schedule_confirm`
in the request's own durable transaction, only when it creates the request;
a repeated confirmation returns the original request and records nothing.
Its configuration checks before intake are `unavailable` during a restore
review or an activating change, as for `schedule preview`. A database error
once the request row is written is `outcome_unknown`, never `unavailable`,
because it may have struck the commit; one before that rolled back and is
`unavailable`. An `outcome_unknown` document carries `error.request_id`,
the request's id, which is fixed before intake, so the operator can read it
with `config request show` or repeat the confirmation within the token's
fifteen minutes. A schedule change
is in no row of the [notifications](#notifications) table, so it creates
no automation notice. `config request show REQUEST_ID` reads only the
approving Administrator's own requests, as the page does; any other is
`not_available`, and, like the page, it records no view event.

### Production transition and withdrawal

| URL names | Command or exemption |
| --- | --- |
| `go_live` | `go-live readiness` (PR 3b); `go-live preview` with the DNS check, `go-live cleanup --token …` (PR 12) |
| `go_live_families` | `go-live families`, an export (PR 12) |
| `go_live_cleanup` | `go-live cleanup-status --watch`, `go-live cleanup-retry`, `go-live cleanup-cancel` (PR 12) |
| `go_live_links` | `go-live links`, `go-live links-status --watch`, `go-live links-retry`, `go-live links-cancel` (cancel or discard the preparation) (PR 12) |
| `production_confirmation` | `go-live confirm-preview`, `go-live confirm --token …` (PR 12) |
| `production_progress` | `go-live progress --watch` (PR 3b); the page's **Retry failed mail preparation** (PR 12) |
| `production_withdrawal` | `go-live withdraw-preview --reason …`, `go-live withdraw --token …` (PR 12) |

Cleanup, cleanup cancel, link preparation discard, confirmation and withdrawal
ask at the [prompt](#command-line-confirmation) unless `--yes` is given. See
[Production transition](../admin-portal/spec.md#production-transition).
These steps matter again only for the next campaign. The go-live redesign in
[#462](https://github.com/epiphany40223/parishkit/issues/462) changes them, and
PR 12 follows whichever flow is current.

`go-live readiness` and `go-live progress` read the current campaign, or the
campaign `--campaign` names, as `schedule show` does. Neither page records a
view event, so neither command does. No current campaign is
`not_available`. A refusal from the page's read is reported only after the
session is rechecked, so a session that ended during the read is exit 5.
Then an unknown campaign is `not_available`, and so is Production progress
for the current Production campaign with no confirmation receipt; any other
refusal is the page's own: readiness for a campaign that is not the current
Testing draft is `stale_version`, and Production progress for one that is
not the current Production campaign (a Testing draft included) is `denied`
(default, pending Administrator confirmation). Readiness shows the
problems as their stored codes and never the Admin report recipients.
A `go-live progress` watch stops once there is no initial mail to prepare,
once preparation is complete, or once its task has stopped: a failed task
changes nothing until the page's retry, which the document reports as
`retry_available` without its signed control. The **Retry failed mail
preparation** control was missing from this inventory; it is assigned to
PR 12 (default, pending Administrator confirmation).

### Family email sends and delivery controls

| URL names | Command or exemption |
| --- | --- |
| `family_email_progress`, `family_email_progress_status` | `send progress --watch` (PR 3a) |
| `family_email_sends` | `send history` (PR 3a) |
| `delivery_control` | `send pause-preview`, `send resume-preview`, `send resolve-preview`, `send confirm --token …` (PR 7) |
| `family_portal` | `portal maintenance show`, `portal maintenance set` (PR 7; the switch has no version, so the command sets the requested state as the page does) |

There is no separate "cancel" action today. Stopping a send is the delivery
pause, and closing work is the closed-campaign resolution. See
[live delivery pause](../admin-portal/spec.md#live-delivery-pause) and
[Family email progress](../admin-portal/spec.md#family-email-progress).

### Testing sends

| URL names | Command or exemption |
| --- | --- |
| `campaign_mail` | `test sample` (PR 6) |
| `campaign_mail_families` | `test families-preview --family DUID …`, `test families --token …`, `test status` (PR 6) |

### Reports and exports

| URL names | Command or exemption |
| --- | --- |
| `reports`, `report_campaigns` | `report list` (PR 8) |
| `participation` | `report participation` (PR 8) |
| `participation_chart`, `daily_digest_chart`, `daily_digest_download` | Permanent: PNG images; the data is in the matching report or digest read |
| `financial_report`, `talents_report`, `information_queue`, `information_item` | Aggregate reads as `report …`; Family-level rows only as exports (PR 8) |
| `ministry_reports`, `ministry_report_campaigns`, `ministry_report`, `ministry_joiners`, `ministry_leavers`, `ministry_packet` | `report ministry …`, counts; rows only as exports (PR 8) |
| `family_directory`, `postal_directory`, `family_codes` | Export only: `export directory`, `export postal`, `export family-codes` (PR 8) |
| `financial_export`, `talents_export`, `ministry_export`, `information_export`, `family_directory_export`, `postal_directory_export` | `export …` (PR 8) |
| `report_export_create`, `report_export`, `report_export_cancel`, `report_export_retry`, `report_export_regenerate`, `report_export_download`, `export_create`, `export_status`, `export_cancel`, `export_download`, `export_download_grant` | `export create`, `export status --watch`, `export cancel`, `export retry`, `export regenerate`, `export download --stream` (PR 8) |
| `report_exact_create`, `report_exact`, `report_exact_cancel`, `report_exact_retry`, `exact_export_create`, `exact_export_status`, `exact_export_cancel`, `exact_export_retry` | `export exact …` (PR 8) |
| `daily_digest_snapshot`, `weekly_digest_snapshot`, `weekly_digest_item`, `weekly_digest_manual` | `digest show`, `digest weekly-request` (PR 8) |
| `logs`, `logs_export` | `logs list`, `logs export` (PR 8) |

### Operations

| URL names | Command or exemption |
| --- | --- |
| `background`, `background_tasks`, `background_task` | `task list`, `task show` (PR 3a) |
| `retry_family_preparation`, `retry_daily_digest`, `retry_weekly_digest`, `retry_export_cleanup` | `task retry` (PR 9) |
| `deliveries`, `delivery`, `delivery_resolve` | `delivery list`, `delivery show`, `delivery resolve` (PR 9) |
| `delivery_refusals`, `delivery_refusal`, `delivery_refusal_clear` | `delivery refusals`, `delivery refusal-clear` (PR 9) |
| `system_health`, `system_health_status` (ADM-13) | `system health`, `system health --watch`, counts and states only |
| `system_backup_request` (ADM-13) | `system backup-now` (keyed), `system backup-status --watch` |
| `system_mail_check`, `system_mail_clear` (ADM-13) | `system mail-clear-preview` (starts the mailbox check and waits for it), `system mail-clear --token …` |
| `system_refresh_accept` (ADM-13) | `system refresh-accept-preview`, `system refresh-accept --token …` |
| `system_debug_off`, `system_debug_allow` (ADM-13) | `system debug-off`; `system debug-allow` (Testing only) |

The ADM-13 rows are pending exemptions until ADM-11 PR 3 (the read) and
PR 5 (the actions) land. The ADM-13 action routes are POSTs to the
[System health](../admin-portal/spec.md#health-actions) page, and
`system_health_status` is its passive status fragment.

### Users and follow-up

| URL names | Command or exemption |
| --- | --- |
| `users` | `users list` (PR 11) |
| `user_rules`, `rule_apply`, `rule_base`, `rule_request` | `rules show`, `rules apply`, `rules request show` (PR 11), including the [high-impact changes](#high-impact-changes) |
| `chair_confirmations`, `chair_reviews` | `chairs …` (PR 11) |
| `assignments` | `assignments …` (PR 11) |
| `security_event_acknowledge`, `critical_events_acknowledge` | `events list`, `events acknowledge` (PR 11) |
| `information_update`, `ministry_followup`, `ministry_followup_assign`, `ministry_followup_item`, `ministry_followup_update` | `followup …` (PR 11) |

### Integrations and credentials

| URL names | Command or exemption |
| --- | --- |
| `integrations`, `integration_settings`, `integration_status`, `credential_status` | `integration show`, `integration status`, `integration set` for non-secret settings, `integration backup-probe` for the backup Drive folder check (PR 10); `integration set --target parishsoft` takes the [refresh schedule](../background-processing/spec.md#refresh-schedule) with the page's validation, and `integration schedule-preview` prints the page's [seven-day preview](../admin-portal/spec.md#seven-day-preview) and [cost and freshness summary](../admin-portal/spec.md#cost-and-freshness-summary), for a saved or a proposed schedule ([#632](https://github.com/epiphany40223/parishkit/issues/632)) |
| `dismiss_credential_result` | `integration dismiss` (PR 10) |
| Key replacement through `integration_settings` | `integration key replace --target …`, `integration key status --watch` (PR 10; see [secret replacement](#secret-replacement)) |
| `select_credential` | `integration key finish-switching` (PR 10) |
| Backup encryption key change, through `integration_settings` with proof of possession | `integration backup-key challenge`, `integration backup-key preview`, `integration backup-key confirm` (PR 10) |

Secret values never pass through command output. The backup destination is an
`integration` target; backups themselves already run as `pk-stewardship
backup`, and backup status is part of `status`.

### Setup wizard

`setup`, `setup_cancel`, `setup_source`, `setup_source_progress`,
`setup_branding`, `setup_branding_asset`, `setup_credential`,
`setup_campaign`, `setup_shares`, `setup_preview`, `setup_confirmation`,
`setup_mail`, `setup_mail_status`, `setup_notification`,
`setup_notification_status`, `setup_schedules`, `setup_content`,
`setup_content_edit` and `setup_step` are permanent exemptions. The
first-Admin wizard is a one-time, browser-led flow that stages credentials and
uploads, with `pk-stewardship bootstrap` as its offline counterpart. See
[bootstrap and first-Admin wizard](../admin-portal/spec.md#bootstrap-and-first-admin-wizard).

### Excluded actions

- **Campaign purge** stays a guarded Admin web workflow with no console
  equivalent, as the
  [architecture](../architecture/spec.md#public-interfaces) requires.
- **Restore release, reopen, archive and return to Testing** have no pages
  yet. When they land, they follow the
  [rules for new Admin actions](#rules-for-new-admin-actions); whether their
  final confirmations may run from the command line is decided in each
  workflow's own specification.
- **ParishSoft publication** (ADM-09) is not built; the same rule applies.

## Existing operator commands

These stay as they are and are not wrapped by `pk-stewardship admin`. They act
as the host operator (`actor_kind` `operator`), not as a portal user:

- diagnostics: `health`, `load-check`, `source-form-check`, `healthcheck`,
  `installer-healthcheck`, `upgrade-check`;
- one-off data repair: `engagement-backfill`;
- lifecycle: `bootstrap`, `migrate`, `database-roles`, `database-grants`,
  `provision-runtime`, `retarget-image`, `acknowledge-credential`;
- recovery: `preview-admin-recovery`, `recover-admin` (see
  [offline Admin-access recovery](../operations/spec.md#offline-admin-access-recovery));
  a recovery also ends every automation session approved before it;
- automation: `revoke-automation-sessions` (see [session rules](#session-rules)),
  run in every restore and available to end every session at once;
- backup: `backup` (including the request mode that
  [System health](../admin-portal/spec.md#take-a-backup-now) adds),
  `backup-keygen`, `backup-open`, `backup-prove`;
- debug logging: `debug-off-clear`, which clears the System health
  [debug-off switch](../admin-portal/spec.md#turn-off-debug-logging)
  (ADM-13);
- smoke tests: `smoke`;
- LOCAL only: `local-sign-in`, `local-seed`, `fake-parishsoft`.

Operator commands are host maintenance that the portal cannot express.
`pk-stewardship admin` commands are portal actions and are attributed to a
portal user.

## Schema impact

The first design needed no schema change: its sessions were ordinary Admin
sessions bounded by the approving browser session's 12-hour lifetime. The
decisions of October 4 make durable records necessary:

- a session must outlive deploys, restarts and its approving browser session
  for up to 30 days, while `stewardship_portal_session` rows are capped at 12
  hours by their insert guard, idle out after 60 minutes, and are deleted by
  session cleanup;
- four SQL guards must tell an approved, full-scope, still-authorized
  automation session from a browser session, which Django's signed session
  data cannot show to SQL;
- the portal must list sessions with last use and end reasons, the audit
  trail must keep the label and approver after the session ends, and notices
  need a store that the existing security-event and incident records cannot
  provide.

Extending `stewardship_portal_session` instead (a kind column and a 30-day
cap) was rejected: it would relax the 12-hour insert guard for every session
and require every one of the many 60-minute activity checks in the schema to
special-case automation, a far wider change than new tables and six amended
guards (plus any installed ADM-13 guards). Valkey is not a durable credential store.

The changes, each a forward migration under the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy)
with one frozen SQL file taking the next unused repository-wide prefix at merge
time:

1. **Automation sessions and notices (PR 2).** A new `accounts` migration and
   frozen file (for example `0004_automation_sessions.sql`).
   - **New objects**, with plain `CREATE`, living only in the frozen file:
     `stewardship_automation_session`, `stewardship_automation_login`,
     `stewardship_automation_notice` and `stewardship_automation_notice_ack`,
     with their constraints, indexes and guard triggers;
     `stewardship_automation_fresh_v1`; `stewardship_automation_live_v1`,
     the one definition of [liveness](#session-records) that the guards,
     `stewardship_automation_fresh_v1` and Python's listings and checks all
     read; and `stewardship_admin_session_purge_v1(uuid[])`, a `SECURITY
     DEFINER` function, revoked from `PUBLIC` and granted to the worker
     through `runtime_functions`, that deletes the Django sessions of the
     named Admin session rows that have already ended, so the
     [maintenance task](#maintenance-task) never reads a session key.
   - **Altered baseline objects**, each given its final definition in the
     baseline file and re-created in the frozen file, copied verbatim:
     the `ops_incident_kind` constraint on `stewardship_ops_incident`
     (dropped and re-added with the four automation kinds;
     `operational_incidents.sql`); `stewardship_ops_incident_state_v1`, so the
     web login may observe those kinds (`operational_incidents.sql`);
     `stewardship_ops_content_v1`, with their fixed titles and instructions
     (`operational_render.sql`); and `stewardship_task_type_login_v1`, mapping
     `automation_maintenance` to `pk_stewardship_worker` (`functions.sql`).
     PR 2 first checks every other function that lists incident kinds or task
     types and adds any it finds to this list.
   - **Django model state.** `ops_incident_kind` is generated from
     `IncidentKind` in `jobs/operational_models.py` (Django state in
     `stewardship_jobs` migration 0002), and CI runs `makemigrations --check`.
     So PR 2 also adds a `stewardship_jobs` migration, depending on the
     `accounts` one, with only the state operations for the changed
     constraint (`SeparateDatabaseAndState`, no SQL), while the frozen file
     changes the database.
   - The `DO` block raises unless each new object exists and each altered
     function body and the constraint contain the new values.
   - The **session guard** enforces the [pairing](#pairing) rules on insert:
     web login only; a live approving browser session of the principal that
     signed in within five minutes and is not a command session; the
     principal an Administrator; `expires_at` after `statement_timestamp()`
     and at most 30 days after it; scope and digest formats. On update it
     admits only `last_used_at` forward to at most now (web), and `revoked_at`
     and `end_reason` once and together with a version advance: any reason
     from the web, `role_lost`, `user_removed` or `recovery` from the worker,
     and `restore` or `revoked_by_operator` from the admin-recovery login. It
     refuses deletes.
   - The **login guard** admits an insert only when the command session was
     created in the same transaction (`created_at` at or after
     `transaction_timestamp()`), after the automation session was created, is
     not the approving session, belongs to the principal, has the automation
     session's `authenticated_at`, and ends no later than it, and the
     automation session is live. It refuses updates, and admits a delete only
     when the command session row no longer exists.
   - The **notice guards** make notices insert-only and admit an
     acknowledgement only by a current Administrator for themselves.
   - **Grants**, through the database grant manifest and each login's grant
     tests: the web login gets `SELECT` and `INSERT` on the session table and
     column-level `UPDATE` on `last_used_at`, `revoked_at`, `end_reason`,
     `version`, `updated_at`, `actor_id` and `correlation_id`; `SELECT` and
     `INSERT` on the login table; `SELECT` and `INSERT` on the notice and
     acknowledgement tables; and the incident privileges its new kinds need.
     The worker and admin-recovery logins get the grants listed under the
     [maintenance task](#maintenance-task) and [session rules](#session-rules).
     Every table joins the schema inventory, as
     `stewardship_family_engagement` did.
   - **Code registries** in the same PR: the task type in the worker's
     compiled registry (`runtime_background.py`), its scheduler producer, and
     the four incident kinds with their CRITICAL level in `IncidentKind` and
     the operational incident registry, and in the kinds list of the
     [operational alerts guide](../../../guides/stewardship-operational-alerts.md).
2. **Fresh-gate acceptance (PR 5).** A second migration and frozen file (for
   example `0005_automation_fresh_guards.sql`) that creates
   `stewardship_automation_fresh_principal_v1` (plain `CREATE`,
   migration-owned, `SECURITY DEFINER` with a fixed `search_path`, revoked from
   `PUBLIC`; `EXECUTE` for the web and the three installer logins comes from
   `runtime_functions` in the `database-grants` step, and
   `admit_installer_database` allowlists it) and re-creates with `CREATE OR
   REPLACE`, copied verbatim from the edited baseline files, the [four
   session-bound guards](#sql-guards-that-accept-automation-sessions)
   (`delivery_control.sql`, `family_mail_tests.sql`,
   `production_confirmation.sql`, `production_withdrawal.sql`) and the two
   [secret request guards](#secret-replacement)
   (`stewardship_sealed_intake_admission_v1` and `stewardship_secret_state_v2`,
   in `functions.sql`), and ends with a `DO` block that raises unless the new
   function exists and each installed body contains the automation clause. The
   function-equality test checks each copy against the baseline, and the
   upgrade-parity test checks the catalog.

Audit adds event types only, which match the existing event-type constraint,
with contexts limited to `outcome`, which `stewardship_safe_context_v1`
already accepts. No applied migration changes, and no baseline table gains or
loses a column; the baseline changes are the one constraint and the nine
functions named above. Any further schema need found during implementation
amends this specification first.

## Testing requirements

Normal CI uses no real ParishSoft, Google, mail or Slack credentials. Tests
MUST prove:

- **pairing:** the full `login start` and `login wait` round trip; `start`
  prints its pending document at once; `wait` can be repeated while pending;
  expiry clean-up (`pairing_abandoned`); single use; a wrong code refused
  without identifying any record; approval needs the Administrator role and
  fresh authentication, refused for Staff and Ministry leaders, from a command
  session and for a mismatched expected email; the approver can lower but not
  raise the scope and lifetime; the SQL guard refuses a direct insert that
  breaks any pairing rule; the approving session unchanged; the secret never
  printed, logged or stored, only its digest;
- **wrapper and session file:** run against a fake `docker`: creation with
  mode 0600 in a 0700 directory and exclusive create; refusal of wider modes,
  another owner, malformed or oversize content and names outside
  `[a-z0-9-]{1,32}`; session selection by `--session`, `PK_ADMIN_SESSION` and
  the single-file default; the HMAC host digest and exit 2 without
  `/etc/machine-id`; the preamble read exactly once; standard input forwarded
  only for a terminal or a `-` input; export fetch with digest check, no
  overwrite and refusal to stream to a terminal;
- **lifecycle:** a session works across a container restart (new process, same
  file); it ends at `expires_at`; the 72-hour warning; revocation by owner, by
  another Administrator and by `logout` takes effect at the next command and
  records the revoking Administrator; loss of the Administrator role,
  disabling the user and an offline recovery each end it before any further
  command and in SQL, and listings show it ended at once;
  `revoke-automation-sessions` on the admin-recovery login revokes every live
  session (`restore`, `revoked_by_operator`) and nothing else; a host-digest
  mismatch refuses and revokes; a
  role or Ministry change that keeps Administrator ends only the running
  command session; the approving browser session's sign-out or timeout does
  not end it;
- **command sessions:** one per invocation, linked, with the automation
  session's `authenticated_at` and a deadline within four hours and the
  automation deadline; the login guard refuses a link to a session from
  another transaction, an older session, or the approving session; ended at
  exit without an audit event; a long watch keeps it active; cleanup removes
  it and then its link row, and the guard refuses any other delete;
- **refusals:** `authenticated_admin` on the web path, including the
  `AUTH_ROUTES` session views and `/admin/logout`, refuses a command session's
  key and revokes its automation session as `misused`; the command line
  refuses every profile and SQL login other than the admitted web, and a
  signing receipt that differs from the running web's; repeated unknown
  secrets from one host digest are each refused at once (decision 12) and
  create one notice per host digest per hour, while a valid session's
  command (a pause included) is never refused by them;
- **session directory:** no rendered Compose service mounts
  `run/admin-automation`, the backup's archived trees exclude it, and generic
  run cleanup skips it;
- **maintenance task:** it deletes ended Admin session rows and then orphaned
  link rows, records `role_lost`, `user_removed` and `recovery` endings with
  their notices, resolves quiet automation incidents, and the session guard
  refuses any other ending from the worker;
- **fresh gates:** before PR 5, `require_fresh` refuses every automation
  caller; each of the four changed session-bound guards, run against its old
  and its new definition, refuses an automation command session under the old
  and admits a full-scope one under the new, and under the new still refuses
  read-only, revoked, expired, non-Administrator and recovered sessions and
  behaves exactly as before for browser sessions; the frozen file's `DO` block
  fails against an unchanged guard; `require_fresh` admits a full-scope
  automation caller at each listed call site, including the post-cleanup check,
  which a browser session still must pass;
- **confirmation:** each prompting command changes nothing on a wrong answer
  or end of input (exit 4), acts on the correct typed value, and acts with
  `--yes`; a prompting command with a `-` input and no `--yes` is refused;
  the structured log records `prompt` or `yes`;
- **secret replacement:** the wrapper refuses a secret argument, a file with
  group or other permissions or another owner (checked on the open
  descriptor, so a file swapped after opening is not read), piped
  `--secret-stdin` without `--yes`, and an oversize value, and the command
  refuses an oversize decoded value; terminal input restores echo on every
  exit path and accepts multi-line JSON ending with Ctrl-D; the base64 line
  leaves the prompt input intact; normalization matches
  `InlineCredentialForm` per target; a FIFO or other non-regular path is
  refused before it is opened; a value with NUL or empty after normalization
  is refused; the public key and proof code reach the container on their own
  tagged lines; `setup_credentials` refuses an automation caller; the web and
  each installer login pass admission after `database-grants` with the new
  definer function from `runtime_functions` and fail it with any other
  definer routine; the function returns false, never NULL, for a missing,
  read-only, revoked or expired session; a session revoked between
  submission and the installer test leaves the request to expire with the old
  key installed, shown as expired; the value never appears in
  output, logs, exceptions or audit; key replacement, finish switching and
  the backup key change produce the same requests, security event and audit
  as the page; the two secret request guards admit a full-scope automation
  request under their new definitions and refuse it under their old ones,
  and still refuse a read-only, revoked or expired session;
- **notices:** each event in the [table](#notifications) creates its dashboard
  notice and, where stated, observes its incident kind, whose fixed content
  renders through `stewardship_ops_content_v1` in the opened, repeated and
  resolved phases; a burst opens one episode, and its resolution an hour later
  sends the resolved text; acknowledgement affects only the acknowledging
  Administrator; labels are escaped on the dashboard; no secret, digest, label
  or Family data appears in email or Slack;
- **seam:** `AdminCaller.from_automation` is referenced only from
  `admin_cli`; every module moved onto the seam keeps its existing view
  tests, changed only in call signatures; a read-only automation caller is
  refused by `authenticated_admin(activity=True)`, `admit_admin_action` and
  `require_fresh` directly, without the dispatcher;
- **parity:** for every command, the same capability denial, stale refusal
  and success as the page through the shared function, with the same domain
  audit events plus one `admin_cmd_*` event for state changes whose subject is
  the automation session; `--expected-version` refusals; the route-parity
  test and the command catalog are complete;
- **audit registration:** every new event type has its `Action` or
  `DIRECT_AUDIT_TYPES` entry and a description sentence;
- **previews:** a token confirms once; an expired, altered, other-user or
  stale-inventory token is refused with nothing changed; a repeated confirm
  returns the original record;
- **scopes:** read-only sessions are refused every state-changing, fresh-gated
  and export command, in Python and, for the six changed guards and any
  amended ADM-13 guard, in SQL;
- **output:** golden documents for each read model's `to_document()`; a
  field allowlist test proving no personal-data field appears; the page and
  the command read through the same function; the exception-to-exit mapping in its stated
  order; a generated request key is on standard error before the action;
- **exports:** the streamed bytes match the stored export's size and digest;
  audit parity with the page download;
- **migrations:** the frozen-file digest pins, the function-equality check and
  upgrade parity from the previous release tag.

PostgreSQL-backed tests use the existing disposable database fixtures. Before
the first Production use, a documented human run in the
[local environment](../local-environment/spec.md) exercises pairing, a
container restart with the session kept, a schedule change, a refresh, a
chosen-Family test, a pause and resume with and without `--yes`, an export
fetch, revocation from the portal, and the notices.

## Delivery plan

Each PR has its own independent review and full CI. The order puts what the
live campaign needs first: reading state, moving schedules, refreshing,
testing and controlling sends. The go-live steps matter again only for the
next campaign and come last. PR 1 to PR 3 go in order; later PRs depend on
PR 3 and may land in any order, except that PR 6, PR 7, PR 10 and PR 12
follow PR 5, which makes fresh-gated actions accept automation. PR 2 and PR 5
carry the schema changes, and PR 10 handles secrets; all three get
security-focused reviews, and none is deployed to Production without the
Administrator's approval of that deploy.

- **PR 0:** this specification, the ADM-11 package and its checklist.
- **PR 1, seam only:** `AdminCaller` with its web constructor, and the PR 1
  extractions; no behavior change.
- **PR 2, automation sessions:** the first migration; the automation
  constructor and command sessions, with `require_fresh` refusing automation;
  pairing, the host wrapper and its tests, and the session file; the approval
  page and the Automation access page with every live session; notices,
  their dashboard display and acknowledgement, and the four incident kinds; the
  maintenance task; `revoke-automation-sessions` and its steps in the restore
  procedure and backup runbook; `login start`, `login wait`, `logout`,
  `whoami`, `sessions` and `commands`; both refusals; lifecycle rules and
  audit events; and the operator guide.
- **PR 3, read-only status,** in two parts. **PR 3a:** read models and
  `to_document()`, `status`, `send progress`, `send history`,
  `schedule show`, `task list`, `task show`, `--watch`, and the route-parity
  test with its exemption list. **PR 3b:** `go-live readiness` and
  `go-live progress`, which matter again only for the next campaign. Later
  PRs depend on PR 3a.
- **PR 4, schedules:** `schedule preview`, `schedule confirm` and
  configuration request status.
- **PR 5, fresh-gate acceptance:** the second migration; the automation branch
  of `require_fresh` (including the secret replacement call sites) and the
  post-cleanup check; the confirmation prompt and `--yes`; the fresh-gated
  notices.
- **PR 6, refresh and Testing sends:** `refresh start`, `refresh status`,
  `test sample` and chosen-Family tests.
- **PR 7, delivery controls:** pause, resume, closed-campaign resolution and
  Family portal maintenance.
- **PR 8, reports and exports:** report reads, exports with streamed fetch
  (including the wrapper's `export fetch` and its tests), digests and logs.
- **PR 9, operations:** task retries, delivery detail and resolution, and
  refusal clearing.
- **PR 10, other configuration:** campaign, content, Ministries, parish,
  hosted files, artwork, branding confirmation, integration settings with the
  backup folder probe, and secret replacement: integration key replacement,
  finish switching and the backup key change, with the wrapper's secret
  input.
- **PR 11, users and follow-up:** users, rules (including the
  [high-impact changes](#high-impact-changes)), assignments, chairpersons, event acknowledgements and
  follow-up updates.
- **PR 12, go-live and withdrawal:** the verified readiness preview, cleanup
  with retry and cancel, link preparation with retry and cancel,
  confirmation, the Production preparation retry and withdrawal; after
  [#462](https://github.com/epiphany40223/parishkit/issues/462) if that has
  landed.

## Decisions

Decided by the Administrator on October 4, 2026, on #463: what was proposed,
what was decided, and why. Items 12 to 15 were decided later that day, on the
Administrator's reasoning that the command line runs only on the deployment
host, so anyone able to use it has already wholly compromised the system, and
items 16 to 19 last, closing every open question.

1. **Interface.** Proposed and decided: host command line only; HTTP API
   rejected; MCP deferred. Because a public API contradicts the architecture
   and widens the public surface.
2. **Identity.** Proposed and decided: sessions delegated by a named
   Administrator in the browser, instead of the issue's installer-provisioned
   credentials. Because the role model, Google sign-in at approval,
   revocation and attribution carry over with no new identity rules.
3. **Scopes.** Proposed and decided: read-only and full. Because finer scopes
   have no need yet.
4. **Who may pair.** Proposed: any portal user, limited by their own roles.
   Decided: Administrators only. Because the interface is for
   whole-deployment automation.
5. **Fresh-gated actions.** Proposed: a browser approval of the exact action
   for Testing cleanup, the Production confirmation and withdrawal, and a
   five-minute browser step-up for the others. Decided: no browser step-up and
   no per-action approval; a prompt that `--yes` skips. Because the command
   line is for agents and larger-scale automation that must run unattended.
6. **Emergency pause.** Proposed: keep its fresh sign-in. Decided, after a
   follow-up question: no step-up, as in 5. Because a pause should not wait for
   a person either.
7. **Secret replacement.** Proposed and decided: browser-only for now.
   Because no automation need exists yet. Reversed later on October 4, 2026
   by decision 15.
8. **Family-level personal data.** Proposed and decided: only in owner-only
   export files from a full-scope session. Because terminal output reaches
   scrollback and assistants' context.
9. **Session lifetime.** Proposed: bounded by the approving browser session's
   12-hour lifetime and ended by its sign-out. Decided: independent, up to 30
   days, revocable, ended on role loss or removal, the same for both scopes.
   Because automation must outlive one browser session; this reverses the
   earlier rejection of a durable session table (see
   [schema impact](#schema-impact)).
10. **Order.** Proposed and decided: live-campaign commands first, go-live and
    withdrawal last. Because go-live matters again only for the next
    campaign.
11. **Session file.** Proposed: the web container's `/tmp`. Decided: an
    owner-only file on the host, passed in. Because sessions must survive
    deploys and restarts; it is passed on standard input rather than mounted.
12. **Rate limits.** Proposed: per-session limits on state-changing and
    fresh-gated commands, and a limit on unknown-secret lookups. Decided: no
    rate limits. Because they would hinder the Administrator more than an
    intruder. Review then applied the same rationale to the approval page's
    user-code attempt limit and dropped it, since a correct guess can only
    approve a pairing that expects the guesser's own email.
13. **High-impact changes.** Proposed: browser-only from a session. Decided:
    allowed from a full-scope session with the portal's authorization and
    audit. Because a host intruder could make them anyway.
14. **Notices and audit.** Proposed and decided: kept. Because they inform
    without restricting.
15. **Secret replacement (reverses 7).** Proposed (decision 7):
    browser-only. Decided: allowed from the command
    line, read from a file or standard input, never an argument, never echoed
    or logged, with the portal's validation, confirmation and audit. Because
    host access already exposes the installed credentials.
16. **Approval-page scope and lifetime.** Proposed and decided: pre-filled from
    the request, lowerable, never raisable. Because the operator states what
    the task needs and the approver can only narrow it.
17. **Notification channels.** Proposed and decided: a dashboard notice for
    every event; email and Slack for approval, refused use, user, role and
    rule changes, key and notification changes, and the three irreversible
    actions; routine fresh-gated actions dashboard-only. Because Slack-only
    delivery would need a route split in operational dispatch, and routine
    actions would otherwise flood email.
18. **Host binding.** Proposed and decided: accepted, including that a host
    rebuild ends every session, with exit 2 when `/etc/machine-id` is
    missing. Because pairing again is cheap and the binding catches a copied
    file.
19. **Role grants and fresh sign-in (#383).** Proposed: options (a) keep the
    portal's low-friction role changes, (b) require a fresh sign-in for
    high-impact expansions, or (c) for any role addition. Decided: (a), and
    sessions follow the portal. Because role maintenance should stay low
    friction, with the security event and email as the detective control.

## Non-goals and rejected alternatives

- **Browser automation of the UI:** fragile against label changes (see #462)
  and unable to carry a Google sign-in.
- **Direct SQL writes:** bypass application validation and the audit trail.
- **A request-faking adapter** that builds a fake `HttpRequest` with CSRF
  marked as done, so commands call views unchanged: a back door shaped like
  the front door. `AdminCaller` makes the channel explicit instead.
- **A CLI that calls the web over loopback HTTP:** needs cookie and CSRF
  handling, per-view JSON rendering, and host and origin checks for an
  internal caller, for no gain in parity over the shared service layer.
- **An operator-identity command line** (`actor_kind` `operator`, no portal
  user): it would bypass the role model and attribute actions to nobody.
- **Installer-provisioned automation tokens:** the credential installer
  pattern suggested in the issue, still rejected. Such a token is provisioned
  by host access alone, approved by no person, never expires, is tied to no
  one's role and is rotated only by reinstalling. An automation session
  differs on each point: an Administrator approves it after a fresh Google
  sign-in; it expires within 30 days; it is listed and revocable in the
  portal and ends when that Administrator loses the role; it acts as, and is
  attributed to, exactly one Administrator; and only its digest is stored.
- **Per-action browser approval and step-up** (the first design): rejected by
  the Administrator on October 4, 2026, because it defeats unattended
  automation.
- **Extending `stewardship_portal_session` to 30 days:** see
  [schema impact](#schema-impact).
- **Reusing the security-event record for notices:** it is tied to
  configuration activations and role rules.
- **A new mail purpose for automation email:** it could carry the label and
  reach a just-removed Administrator, but would duplicate the operational
  path's outbox guard, cohort and recipient bindings, prepare and dispatch
  checks and content function; new fixed-text incident kinds reuse them.
- **Mounting the session directory into the web container:** exposes every
  session to the long-running web process, and changing mounts recreates the
  web container. Standard input passes one session to one command.
- **Writing exports inside the container:** its `/tmp` is a `tmpfs` that
  `docker compose cp` cannot read; the wrapper streams them to the host
  instead.
- **A separate one-shot command-line container** with its own mounts: it would
  duplicate the web's credential and lifecycle mounts for no gain over
  `docker compose exec`.
- **Storing the secret, or a reversible form of it, in the database:** a dump
  or backup would then yield working sessions; a SHA-256 digest of a 256-bit
  random secret is enough, with no need for a slow hash.
- **Free-text audit context:** would need a context-check change; the
  durable session row carries the label instead.
- **Speed:** this interface does not make sends or seeding faster.
