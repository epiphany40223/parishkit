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
| `google_workspace` | `mail-dispatch` | The service account can act as the delegated mailbox (`--delegated-email`) and authenticate to Gmail SMTP | `--send-to ADDRESS` sends one fixed plain-text message |
| `slack` | `worker` (Slack-configured) | The bot token authenticates | `--channel-id ID --send` posts one fixed message |
| `google_oauth` | `web` | The OAuth client document has the expected shape; prints the redirect URI to register | none; the human signs in |

The result is one JSON line, `{"target": ..., "credential": "valid" |
"invalid" | "unavailable", "sent": true | false}` (plus `redirect_uri` for
the OAuth client), and exit `0`; any failure is one generic line on standard
error and exit `2`, with only a failure classification in the process log,
never the exception text. No token, key,
address or provider error text is printed.

## Design

### Reuse the installer's checks, in the consumer that holds the credential

The credential installers already validate each provider credential in an
endpoint-restricted session: exact-tenant organization validation for
ParishSoft, a token refresh and an SMTP `AUTH XOAUTH2` for Google Workspace,
and `auth.test` for Slack, and one classification turns each check's result
or exception into `valid`, `invalid` or `unavailable` (a rejected key or a
malformed credential file is invalid; an outage or an unexplained failure is
unavailable, never a rejection). The smoke command runs that same
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
- The real network path is not tested in CI: the runbook's smoke pass before
  the pre-launch gate is the human's.

## Checkpoint

Implementation and focused validation are complete; review/fix rounds, full
exact-head CI, DCO and protected delivery remain open. No deployment,
release, live-provider write or database deletion is authorized by this
increment; a real message is sent only when the human runs the command with
an address.
