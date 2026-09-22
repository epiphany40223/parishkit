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

## Round 2

Claude only (Codex produced no output), the correction check of round 1.
Seven raw findings, one validated. The medium-severity finding: with
separate cookies, the Family CSRF secret rotates only on a Family sign-in,
which revokes every earlier Family session in the browser and replaces
`pk_family`. A stale Family token with a live session therefore means the
tab's session was replaced, possibly by a different Family, and the
`csrf_failed` recovery silently rebound the old tab to the newer session:
its keepalive renewed the new session and its submit then failed, after a
misleading "try again". The correction removes the recovery: the CSRF
failure view is again a plain 403 for every route, `ui-v1.js` and
`family-v1.js` are unchanged from before this correction, and a Family 403
still ends the tab's session. The namespace separation alone fixes the
gate finding. A PostgreSQL test now shows that after a second Family
sign-in in the same client the first tab's old token is refused and cannot
renew the new session. One low-severity note was taken: the Family logout
in the session-end test asserts its 302. The other low-severity notes
concerned the removed recovery and are moot. A further round follows.

## Round 3

Claude only (Codex produced no structured output). Two raw findings, none
validated; the review rounds are closed. The low-severity notes (the
malformed-cookie test does not use a well-formed 64-character value or show
a refused POST, and the attribute test does not check `max-age`) were not
carried forward.
