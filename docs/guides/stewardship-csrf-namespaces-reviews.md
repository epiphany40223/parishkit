# Stewardship CSRF namespaces reviews

This ledger records the independent review/fix rounds of the
[CSRF namespaces correction](stewardship-csrf-namespaces.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, because it changes authentication and CSRF handling, with a
correction check after any round that validates a finding. The Codex
reviewer has been out of quota since September 20, 2026; under the human's
exemption, extended through October 30, 2026, a completed Claude-only pass
counts as a round, and each round records which sources answered.

## Round 1

Claude only (Codex produced no output). Five raw findings, one validated.
The medium-severity finding: only the Family form's own requests recovered
from `csrf_failed`. The background keepalive in `ui-v1.js` retried later
with the same stale token forever, for example after a second Family
sign-in in the same browser. The correction moves the recovery into one
shared `ui-v1.js` helper that installs the fresh token in every page token
field; keepalive and presence retry once with it without ending the
session, and the Family form uses the same helper. A browser test covers a
rejected keepalive and presence beat. The four low-severity notes were
taken: only a missing or stale token or cookie gets the recoverable body,
so Origin and Referer failures stay a plain 403; the middleware honors
`CSRF_COOKIE_DOMAIN` and refuses to load with `CSRF_USE_SESSIONS` or a
custom CSRF cookie name or path; the namespace path rule lives once in
`web/namespaces.py`, shared by sessions, CSRF, the failure view and the
access gate; and tests assert each namespace's `Set-Cookie` attributes. A
correction check follows.
