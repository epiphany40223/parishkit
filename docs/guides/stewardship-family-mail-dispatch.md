# Stewardship isolated Family mail dispatch

## Scope and delivery boundary

Branch `pr/stewardship-family-mail-dispatch` starts at verified PR #38 merge
`72787c28d9108fee0c48e570e09b167d13e9f860`. It continues
[BG-06](../plans/stewardship/background-processing.md#bg-06-family-invitations-and-reminders)
after [personalized preparation](stewardship-family-mail-preparation.md).

This increment connects a prepared invitation/reminder to the isolated MAIL
consumer, including current-source rendering, private-token resolution, final
admission, provider submission, recipient-specific outcomes, bounded retries,
pause/resume and crash reconciliation. It does not permit live-provider smoke
tests or Production activation before the controlling gate.

The following coherent increment owns the Admin delivery-resolution and
verified-refusal-clear workflows: external-evidence resolution, explicit
duplicate-risk resend, failed-message retry and durable portal notification.
Those actions remain unavailable, never inferred from an uncertain SMTP
acknowledgement. The full BG-06 checklist stays open until that integration and
its acceptance tests are complete. BG-10 retains operational escalation;
ADM-06 retains the broader campaign-control UI. Gate 3 remains closed.

## Implementation checkpoints

- SMTP uses one Family envelope, shared MIME construction and the configured
  Workspace identity. MAIL/RCPT and DATA are separated so a definitive refusal
  is distinguishable from lost acceptance. A stable Message-ID is correlation
  only; this provider has no assumed idempotent-send or status-query contract.
- A finite private helper receives credentials and resolved content through
  anonymous pipes. It emits only a closed result and recipient positions,
  never addresses, provider prose, tokens or keys. Missing/malformed/late
  acknowledgements remain uncertain.
- Per-address outcomes are recorded in the existing immutable outbox event's
  bounded evidence, bound to its numbered attempt and exact rendering. A
  permanently refused address can be suppressed independently of accepted,
  retryable or uncertain DATA outcomes. Transient refusals do not suppress an
  address, and partial acceptance does not resend to the accepted recipients.
- Current source, recipient selection, content and credentials are rechecked at
  the locked submission boundary. The mail process has no general-code key;
  it opens the prepared code/reference and decrypts only the corresponding
  retained token. Generation changes remain with restore/reopen ownership.
- Observed outcomes settle independently of subsequent lifecycle/configuration
  changes. Terminal outbox transitions scrub message substitutions; uncertainty
  retains them and blocks automatic resend. Task abandonment plus elapsed
  provider deadline is uncertainty, not proof of non-acceptance.

Implementation and acceptance coverage are in progress. No review round or
final validation is claimed yet. All checks use synthetic providers and owned
disposable databases; existing development databases are untouched.

## Fresh-install schema evidence

Independent empty PostgreSQL 18.6 databases installed the exact PR #38 baseline
and the current schema. The former matched its committed catalog fingerprint.
The latter adds four dispatch/result functions and six triggers, changes the
occurrence guard, refusal guard, refusal-effect function and scheduling work
view, and removes nothing. Columns, constraints, indexes and policies are
unchanged. The updated baseline records 401 functions and 409 triggers; it is
not an upgrade path or a promise of development-database compatibility.

The refusal-effect trigger uses a fixed search path and owner privileges only
to derive deliverability from validated immutable refusal evidence. It has no
callable runtime/public entry point. MAIL has no general Family UPDATE grant;
the real-role permission tests check both boundaries.
