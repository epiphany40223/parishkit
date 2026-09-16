# Family mail Admin resolution increment

Branch `pr/stewardship-family-mail-resolution` starts at the verified PR #39
merge, `9e5b6e99d1fbfd1c33e386a46525630e9dd73f0a`, on refreshed `origin/main`.
This is the next coherent increment under the
[controlling plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
and [BG-06 tasks](../tasks/stewardship/background-processing.md#bg-06-family-invitations-and-reminders).

## Scope and acceptance

Implement the [delivery-resolution contract](../specs/stewardship/background-processing/spec.md#family-invitations-and-reminders)
and its [Admin indicator](../specs/stewardship/admin-portal/spec.md#background-indicators):

- Admin-only searchable, paginated delivery metadata and private evidence notes;
  no message bodies, credential substitutions, or raw provider responses.
- Persistent unresolved-delivery warning and deduplicated WARNING audit.
- External-evidence acceptance, explicit duplicate-risk acknowledged resend,
  and retries of failed deliveries or failed unsent preparation tasks.
- Verified refusal clearance with immutable evidence, current source identity,
  atomic deliverability recalculation and the existing recovery-edge policy.
- Current authorization, CSRF, replay/version fences, safe lifecycle admission,
  immutable prior attempts and atomic outbox/occurrence/fulfillment effects.

SMTP has no contractual status-query or idempotent-send capability. The UI must
state this limitation; absence of a lookup result never establishes failure.
An external receipt may resolve old uncertainty after close, but cannot authorize
a new out-of-interval send. A cleared address cannot duplicate an initial
message already accepted for another head in the same Family.

## Internal checkpoints

1. Restricted metadata and verified-refusal clearance, with database tests.
2. Evidence-backed resolution and fresh-credential retry services, with ownership
   and race tests. These are internal checkpoints, not separate PR boundaries.
3. Responsive Admin UI and durable warning integration, including browser tests.
4. Integrated validation, three dual-source review/fix rounds, exact-head CI,
   protected queue, verified main ancestry, then the next dependency-ready work.

Implementation is present and integrated validation is in progress; no review
round is claimed complete. Fresh-install baseline changes only; no upgrade compatibility
or retained development-database deletion. BG-10 retains operational email/Slack
escalation, ADM-06 retains broader campaign controls, and Gate 3 remains closed.

## Implementation checkpoint

The Admin navigation now links to bounded Family-mail metadata, immutable attempt
history and private evidence notes. Every command requires a current Admin,
CSRF, an exact idempotency key and the observed delivery/source version.
Successful retries allocate a linked Task, rerender current source/settings and
seal current credential references without Web access to the private token key
or provider transport. Preparation, retry and dispatch share public rendering.
Prior attempts remain immutable; acceptance atomically fulfills the semantic
slot, and a duplicate-risk acknowledged resend preserves its unknown history.

`delivery_unknown` has a durable, deduplicated WARNING and persistent Admin
indicator, independent of Task status. Forms explain SMTP's lack of status-query
and idempotent-send guarantees. Refusal clearance requires verified evidence and
updates only current Family deliverability; other unresolved refusals continue
to suppress the address. A cleared address does not directly send mail. Failed
local-preparation Task pages expose replay-safe retry only for the latest run.

Campaign end and Production pause deny new retries but permit evidence-backed
confirmation of past delivery. Ordinary source, mode, epoch, restore, gate and
semantic-fulfillment checks remain in force. Error pages provide fixed recovery
links without echoing submitted private evidence.

## Fresh-install schema audit

Two newly created disposable databases independently installed the exact PR #39
baseline and this branch's current SQL. The reference fingerprints matched the
committed baseline before comparison. The audited delta adds one command table,
13 columns, 14 constraints, three indexes, five functions and four triggers;
no existing relation, column, index, trigger or policy was removed or changed.
Existing changes are limited to the operational-event and refusal-resolution
checks plus occurrence recovery, verified-refusal admission and deliverability
effect functions. The 28 policies are unchanged. Catalog fingerprints are
updated only after this comparison, not copied from a failing test.

The resulting function catalog has 406 entries, hash
`4db09e7ec9d48a51a6a0b582c0f8aaee73b2f5182bd73aa807e5cbadf209784f`.
The command trigger consumes and scrubs ephemeral preparation before persistence;
the Web role receives neither outbox UPDATE nor render/private-token SELECT.
No upgrade path or retained database was changed.
