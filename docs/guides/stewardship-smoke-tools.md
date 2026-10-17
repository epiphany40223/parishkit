# Stewardship smoke tools

This guide records the human-run smoke tools of the
[v1 launch scope](../plans/stewardship/v1-launch.md#launch-critical-remaining-work)
(item 5, the smoke portion of
[OPS-09.04](../tasks/stewardship/operations.md#ops-09-continuous-integration-coverage-and-release-gates)):
one console command that checks each installed provider credential against
the real provider from inside the deployed container that holds it, with
redacted output, and can send one fixed message to an address or channel the
operator names. It stays out of normal CI, as the operations specification's
[CI and local validation](../specs/stewardship/operations/spec.md#ci-and-local-validation)
section requires, and follows the
[pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## Scope

`pk-stewardship smoke --config SERVICE_CONFIG --target TARGET` runs inside a
deployed consumer and reads only the credential that consumer already mounts:

| Target | Container | What it proves | Optional send |
| --- | --- | --- | --- |
| `parishsoft` | `worker` | The API key sees exactly the configured organization (`--organization-id`) | none (read-only) |
| `parishsoft_contact` | `worker` | Whether `GET members/{id}` carries all fifteen Member contact PUT fields, for one test Member (`--member-id`, repeated as `--confirm-member-id`); see [Member contact round trip](#member-contact-round-trip) | `--send` writes one marker to that Member's `pager` or `fax`, reads the whole record back and restores the original |
| `google_workspace` | `mail-dispatch` | The service account can act as the delegated mailbox (`--delegated-email`) and authenticate to Gmail SMTP | `--send-to ADDRESS` sends one fixed plain-text message |
| `slack` | `worker` (Slack-configured) | The bot token authenticates | `--channel-id ID --send` posts one fixed message |
| `google_oauth` | `web` | The OAuth client document has the expected shape; prints the redirect URI to register | none; the human signs in |
| `backup_drive` | `backup-worker` (`docker compose run`) | The Workspace key, acting as `--delegated-email` with the Drive scope, can write to and trash in the folder at `--folder-link` | `--send` uploads the newest complete local backup set, tagged as this deployment's like a real copy (it also reads the database, as the backup login) |

The result is one JSON line, `{"target": ..., "credential": "valid" |
"invalid" | "mismatch" | "unavailable", "sent": true | false}` (`mismatch`
only for ParishSoft: the key works but sees a different organization than
`--organization-id`; plus `redirect_uri` for
the OAuth client), and exit `0`; `backup_drive` instead prints
`{"target": "backup_drive", "accepted": true, "copied_set": ...}` or
`"accepted": false` with a `reason` category and its plain-language
`message`; any failure is one generic line on standard
error and exit `2`, with only a failure classification in the process log,
never the exception text. No token, key,
address or provider error text is printed.

In the [local environment](stewardship-local-environment.md) the command
runs the same checks against what is installed there
([#1399](https://github.com/epiphany40223/parishkit/issues/1399)):
ParishSoft against the fake service, the mailbox against the mail catcher,
the sentinel OAuth client, and a test Slack token. A `--send-to` is refused
there, because the send path takes only a Google service account, which the
local environment still refuses. The local environment's
[safety guarantees](../specs/stewardship/local-environment/spec.md#safety-guarantees)
say what each check can reach.

## Design

### Reuse the installer's checks, in the consumer that holds the credential

The credential installers already validate each provider credential in an
endpoint-restricted session: exact-tenant organization validation for
ParishSoft, a token refresh and an SMTP `AUTH XOAUTH2` for Google Workspace,
and `auth.test` for Slack, and one classification turns each check's result
or exception into `valid`, `invalid`, `mismatch` or `unavailable` (a
rejected key or a malformed credential file is invalid; a ParishSoft key for
another organization is a mismatch, refused for good like an invalid key,
[#1492](https://github.com/epiphany40223/parishkit/issues/1492); an outage or
an unexplained failure is unavailable, never a rejection). The smoke command runs that same
classification on the mounted credential file, so a passing smoke check
means what a passing installation means, and no second validation code path
can drift. It runs
where the credential is: `docker compose ... exec -T worker` for ParishSoft
and Slack, `mail-dispatch` for the mailbox, `web` for the OAuth client. No
service gains a mount, and the command refuses to run under any other
profile.

### The one thing the installer never does

With an address or channel the operator names, the command sends one fixed
message, the subject `ParishKit Stewardship smoke test` and a body naming the
time, through the same authenticated SMTP session or Slack's
`chat.postMessage`. The message names nothing about the deployment. A send
happens only after the credential check passed and only when asked. The
mailbox's send-time token refresh goes through the installer's restricted
session; the Slack post keeps the same posture, ignoring proxy, CA bundle and
netrc settings from the environment, following no redirect and reading a
bounded answer, so the token never meets a transport the installer would
refuse.

### Google login is the human's

The OAuth client document is validated for shape only; whether its redirect
URI is registered with Google is proved by the human signing in at the
public origin. The command prints the URI the deployment expects,
`<public origin>/admin/oauth/callback`, so the console entry can be compared.

## Member contact round trip

Census publication (#528) writes a Member with ParishSoft's v2 Member
contact PUT, which replaces all fifteen `MemberContactUpdateRequestModel`
fields at once. The publish worker must therefore read all fifteen right
before each PUT, and which ParishSoft read returns them is not yet
confirmed: the publication gateway's read refuses with `read_unconfirmed`,
and the `parishsoft_publication` switch stays off
([ParishSoft publication](stewardship-member-requests.md#parishsoft-publication)).
The `parishsoft_contact` target settles that against one test Member,
chosen by a human, in the `worker` container:

```sh
# Read-only probe: which PUT fields GET members/{id} carries, and how.
docker compose ... exec -T worker pk-stewardship smoke \
  --config /opt/parishkit/config/services/worker.yaml \
  --target parishsoft_contact --organization-id 12345 \
  --member-id 67890 --confirm-member-id 67890

# Round trip: add --send (and optionally --field pager|fax).
```

### Before the run

- **Choose a fully populated test Member,** kept for testing and not a
  parishioner, with a birth date, a gender, at least two email addresses
  and the home, mobile and work phones filled in. A blank encoding field
  is listed in `unsettled`: the run says nothing about how ParishSoft
  stores it.
- **Note that Member's current values in ParishSoft first,** at least the
  chosen field and the fields listed below. The tool never prints a value,
  so a restore that fails can only be finished by hand from your note.
- **Run it when no refresh or publication is running.** It takes no source
  lease, and its restore writes all fifteen fields back as first read, so
  an edit made to that Member during the run (up to a few minutes) is
  reverted.
- With `exec -T`, Ctrl-C at the host may not reach the process in the
  container. Let a run finish.

### What it does

- **Guards.** It refuses before any call unless `--member-id` is given and
  `--confirm-member-id` repeats it exactly. The tenant check (the
  `parishsoft` target's) runs before the Member is read. Every read must
  answer with that Member's own `memberDUID`, and every URL is built from
  that one id, so no other Member is read or written.
- **Probe (always).** It reads `GET members/{id}` and reports, for each of
  the fifteen PUT fields:
  - whether it is present, and under which spelling (its own, or the
    Member record's `birthdate`, `sex` or `mobilePhone`); two spellings
    with different values make a field ambiguous, with each spelling's
    type and shape shown so a human can choose;
  - its JSON type and, for the dates, phones and gender, its character
    shape (every digit as `9`, every letter as `a`);
  - for the email field, the number of addresses and the separators
    between them, as shapes;
  - `encoder_matches_read`: whether the publication encoder, given the
    value as read, produces exactly the same text (or `unreadable`); a
    false one means the encoder or the read mapping must change before
    publication can verify a write;
  - `sendable`: whether the value as read satisfies the recorded PUT
    schema (type, length, a strict `YYYY-MM-DD` date).

  It also lists `unsendable`, `unsettled` (blank encoding fields),
  `markers_present` (fields already holding a marker) and the record's
  top-level key names.
- **Round trip (`--send`).** It is refused unless the probe found all
  fifteen fields, every one `sendable` as read, and no marker. If
  ParishSoft returns birth or death dates as date-times
  (`1970-01-02T00:00:00`), the date field is not `sendable` and `--send`
  refuses that Member with `refused_unsendable`. That probe output is
  itself the evidence: the follow-up change must add a read-to-PUT
  conversion before any round trip, or publication, can write such a
  Member. Otherwise it sends the
  payload exactly as read with the chosen field set to a fixed marker in
  Family display form (`(555) 555-0199` for `pager`, the default;
  `(555) 555-0198` for `fax`), so a provider that normalizes phones fails
  confirmation. It reads back and compares **every top-level key** of the
  record, not just the fifteen, apart from the audit stamps that change on
  every write (`dateModified`, `modifiedDate`, `lastModifiedDate`,
  `modifiedBy`, `lastModifiedBy`; reported as `volatile_changed`). It
  prints that marker result to standard error, then puts the original
  back, whatever happened: after a failed marker PUT (including a request
  helper that would not stop, `drain_failed`) it first waits 15 seconds,
  since a timed-out PUT may land late. The restore is retried after 5, 15
  and 30 seconds until a read matches the first one. Ctrl-C is ignored
  from just before the marker PUT until the restore ends; every call in
  between is bounded by its timeout.
- **Output.** One JSON line with no values, names or ids: the
  `parishkit_version`, the probe report, `write` and `restore_write`
  (`accepted`, a gateway failure word, `drain_failed`, or a `refused_…`
  reason),
  `marker_read_back`, `also_changed` and `restore_mismatch` (record key
  names only), `restore_attempts`, `restored` and `confirmed`. `confirmed`
  is true only when the marker PUT was accepted, the marker came back
  exactly, no other key changed and the restore read matches the first
  read. A run interrupted after a write prints its partial result, with
  `aborted`, before the generic error line.
- **If `restored` is false,** put every field named in `restore_mismatch`
  (or, with no read, the chosen field) back by hand from your note, then
  probe again: `markers_present` must be empty. A timestamp-like key in
  `restore_mismatch` or `also_changed` (an audit stamp ParishSoft rewrites
  on every write, not listed above) is not something to fix by hand: it
  means the volatile list must grow, in a reviewed change, and the run
  repeated.

### Recording the result

A confirmed run is evidence, not a setting. The operator pastes the JSON
line (it carries no values or ids) as a comment on #528. A reviewed change
then replaces the gateway's `read_unconfirmed` refusal with this read and
the spelling map the result shows, citing that comment and its
`parishkit_version`, with a synthetic fixture of the same shape. It also
corrects the encoder for any field whose `encoder_matches_read` is false,
and treats `unsettled` fields as still unknown. There is deliberately no
configuration flag that accepts the read: a flag could switch on a read
nobody reviewed. Turning `parishsoft_publication` on is a further, separate
Administrator decision.

## Focused validation

- Database-free, with a fake session and SMTP: for every target, the real
  failure types (a 401 or 403 ParishSoft rejection, a provider error, a
  malformed credential, a connection error, a timeout, an unexplained
  failure) classify exactly as the installer's helper classifies them, and a
  check that did not pass never sends; the mailbox send happens only after a
  valid check and only with an address, with the fixed subject and body and
  the operator's address, and a refused send-time authentication sends
  nothing; the send-time token exchange reaches only Google's token URL; the
  Slack post happens only with a channel and `--send`, under a session that
  ignores the environment, and Slack refusing it, a non-JSON answer or one
  past the bound is a refusal; a failure after a valid check reaches the
  console as the one generic line; the OAuth
  document check prints the redirect URI; a consumer without the credential,
  an unknown target and a non-consumer profile (scheduler, backup worker,
  credential installer) are refused before any check runs; the console
  never echoes an option value, a token or a provider error; the parser
  admits only the smoke options.
- The Member contact round trip (#528), on an in-memory fake gateway only:
  the recorded PUT schema is pinned to the capability fixture; the probe
  reports spellings, types, Unicode-masked shapes, email separators,
  encoder and schema checks and unsettled fields, and no value or id; a
  complete read round-trips the marker and restores the original; an
  incomplete or unsendable read, another Member's answer, a missing
  Member, any left-behind marker and an unlisted field (including
  `nickName`) write nothing; a change to a key outside the request model
  or a normalized marker fails confirmation and is named; a failed marker
  PUT waits before the restore read; a failed restore is retried with
  backoff; a helper that will not drain, a non-gateway error or Ctrl-C
  after a write still restores (Ctrl-C is ignored across the marker, its
  report and the restore) and an abort prints the partial result; the command refuses without the Member id
  given twice, outside the worker, and before the Member when the tenant
  check fails; a killed request is recorded with no task; and, in a fresh
  process with no Django settings, the command configures Django before
  importing the gateway. Static fences pin the callers of the contact PUT,
  the raw probe read, the task-free opener and the smoke module, and see
  every import form.
- The real network path is not tested in CI: the runbook's smoke pass before
  the pre-launch gate is the human's.

## Checkpoint

Implementation, focused validation and the
[review rounds](stewardship-smoke-tools-reviews.md) are complete; full
exact-head CI, DCO and protected delivery remain open. No deployment,
release, live-provider write or database deletion is authorized by this
increment; a real message is sent only when the human runs the command with
an address.

## Protected delivery

PR #95 delivered candidate `f963c461`, three logical commits plus the PR #94
receipt and the CI smoke-set rotation, whose tree `df3ce3df` is identical to
the retained commit-by-commit review history on
`pr/stewardship-smoke-tools-reviewed` (`d595226f`) and to the landed tree. The
three [review/fix rounds](stewardship-smoke-tools-reviews.md) were
single-source under the exemption, with every accepted finding fixed and the
third validating nothing. The pull request was marked ready before the
candidate was pushed. Exact-head ready-candidate CI `35723878626` and DCO
passed all 25 checks, from 11:53:39 to 12:19:55 UTC on September 22, 2026
(26 minutes 16 seconds). Its first attempt failed one scenario, the complete
operational Compose setup, whose finalization task was still in
`retry_wait` when the probe's deadline passed after the configuration
installer reported one transient unavailable configuration; nothing in this
increment touches that installer or the setup path, the same scenario passed
on `main` an hour earlier, and a rerun of the failed jobs on the same
candidate passed. `origin/main` had no intervening commits since the
candidate's base `ad43c0d9`. Protected auto-merge landed as `dd15605f` at
12:20:05 UTC and was verified on freshly fetched `origin/main`, whose second
parent's tree is the candidate's, before the next increment started. This
used the standing delivery authority, without deployment or release; no real
provider was contacted. The failed first attempt and the cancelled run are
not counted as acceptance.

The smoke tools increment is delivered. The launch scope continues with the
[launch runbooks](stewardship-launch-runbooks.md).
