# Stewardship pre-start Production withdrawal

Continue [ADM-05.04/.05](../tasks/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
after [PR #60's protected delivery](stewardship-production-confirmation.md#protected-delivery).
Branch `pr/stewardship-production-withdrawal` starts at verified `origin/main`
`6bc3238`. The [Production-transition specification](../specs/stewardship/admin-portal/spec.md#production-transition)
and [ADM-05 implementation package](../plans/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
control this increment; the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
controls validation, reviews and protected delivery.

## Increment boundary

Deliver the current-Admin pre-start withdrawal workflow: fresh Google
authentication, reason and irreversible-cleanup acknowledgement; exact future
live-work inventory; safe cancellation and blockers; atomic return to draft and
Testing with structural unlock, readiness invalidation and immutable audit.
Reject after start even when the boundary worker is delayed. Preserve all prior
Testing cleanup and activation history. Repeated go-live must use a new complete
readiness/test/cleanup/authentication cycle, not revive former evidence.

## Checkpoints

1. Inspect the lifecycle, reconciliation and credential/boundary owners; reuse
   their cancellation and uncertainty rules through a narrow durable command.
2. Implement current-session, version/time-bound preview and atomic withdrawal,
   with exact replay and no arbitrary web lifecycle/outbox mutation authority.
3. Expose native accessible forms and clear blocked/success states. Link only
   from eligible current scheduled campaigns; never imply cleanup is restored.
4. Test exact roles, CSRF/stray input, stale readiness, failed/unknown delivery,
   rollback, competing confirmations, withdrawal/start races and repeated go-live.
   Audit only a fresh schema baseline and preserve all retained databases.
5. Complete three dual-source review/fix rounds, focused validation and final-head
   CI/DCO, then protected delivery. Delivery pause and Gate 3 remain later work.

No task is completed by this initial checkpoint. No real-provider writes,
deployment, release or retained database deletion are part of this increment.

## Implementation checkpoint

The native current-Admin workflow binds its reason, irreversible-cleanup consent,
campaign/runtime versions and exact work inventory into a five-minute signed
preview. Fresh Google authentication and independent SQL checks gate the final
immutable receipt. One transaction cancels safely unsent scheduled work through
the shared reconciliation owner, changes Production/scheduled to Testing/draft,
unlocks structural edits and invalidates readiness. Unknown acceptance (including
an idempotent uncertain retry), unsupported work and start-time arrival block it.
Operational notifications are excluded. Exact committed replay still requires
current Admin authority, but not an unexpired preview.

Withdrawal retains activation, cleanup and delivery history. New test-mail
previews bind the campaign readiness revision; subsequent go-live requires a
successful test requested after the latest withdrawal, in both web readiness and
SQL cleanup intake. The real repeated-cycle test runs new mail testing, cleanup,
link preparation and fresh final confirmation, retaining distinct receipts and
token generations. It does not fabricate provider-validation receipts or weaken
source freshness policy.

## Fresh-install schema audit

Independent fresh catalogs compare verified merged `6bc3238` with this baseline.
Only the withdrawal table/count-only view, their 18 columns, 25 constraints,
five indexes, four private functions and two triggers are added. Existing
schedule reconciliation delegates unchanged cancellation mechanics to a private
shared helper; go-live intake additionally rejects pre-withdrawal test evidence.
No preexisting objects are removed; row policies and all other definitions and
ACLs remain unchanged. The final view correction scopes boundary tasks by their
actual campaign domain identity, not by boundary occurrence identity.

Retained audit artifacts are `/tmp/parishkit-withdrawal-before-v.json` and
`/tmp/parishkit-withdrawal-after-x.json`. The predecessor fingerprint matched
before comparison. The updated inventory contains 189 relations, 2,169 columns,
3,098 constraints, 925 indexes, 533 functions, 518 triggers and 28 policies.
Only after inspecting that exact delta was the strict fixture updated. Django's
initial state describes the same fresh schema; no upgrade migration is added.

The original disposable PostgreSQL container filled its 1 GiB tmpfs while
retaining audit databases. That infrastructure-failed run is not passing
evidence. A new PostgreSQL 18.6 test container with 2 GiB tmpfs was created;
no existing container, volume or retained database was deleted or reset.

## Validation in progress

- Focused withdrawal/repeated-go-live, schema/model agreement, existing schedule
  reconciliation and grants: 86 passed in 107.48 seconds.
- Actual Production/withdrawal templates, mobile and desktop WCAG checks and
  no-JavaScript controls across Chromium, Firefox and WebKit: nine passed in
  16.23 seconds.
- `ruff check .` and `makemigrations --check --dry-run` passed.
- The uncertain-delivery scenario passed through submitting, unknown,
  idempotent-retry blocking and verified nonacceptance before safe cancellation.
  Its initial synthetic envelope omitted required Production generation binding;
  correcting that fixture retained the real storage guards.
- Competing withdrawal/start, expired-replay/current-authority HTTP checks and
  the final strict catalog fingerprint: three passed in 41.02 seconds. The start
  test observes an actual restricted connection waiting for the work lock, then
  rejects at the opening instant even before the boundary worker runs; the
  maintained scheduler/worker subsequently starts the campaign and the same
  withdrawal remains rejected. Its first attempt lacked a provisioned concurrent
  test login; fixing fixture lifetime did not change runtime admission.
- Three dual-source review/fix rounds and exact-head CI remain required before
  delivery. Formal task completion is not claimed yet.

## Review round 1 corrections

Full-PR review `6bc3238` → `609baf7`, Pika session
`20260919-020238-094f54`, completed both Claude and Codex without degradation.
Claude reported one Medium and five Lows; Codex approved with no findings.
There were no High/Critical findings. The validated Medium is accepted:
retained cancelled occurrences previously suppressed a later activation using
the same schedule revision.

The correction adds the [Production execution cycle](../specs/stewardship/data/spec.md#schedule-revisions-and-fulfillment)
to occurrence identity, independently of semantic fulfillment and deliverability
recovery. Only an evidenced withdrawal advances it. Allocation, readiness,
catch-up, ordinary Family/digest planning and SQL recovery keys use that cycle;
old work cannot gain dispatch permission. A persistent digest producer also
resets its cursor for the new cycle. The repeated-go-live test now retains
cancelled Family, daily and weekly occurrences before completing a new actual
test/cleanup/preparation/confirmation cycle and planning replacement work.

Low dispositions:

- Fixed the missing progress entry-link coverage: actual eligible HTTP context,
  withdrawn rejection and scheduled/active browser components including WCAG.
- Removed the redundant private inventory wrapper; the already privileged
  guard reads the count-only view directly.
- Retained correlated occurrence/outbox events as cancellation outcome evidence.
  The receipt binds the pre-inventory; duplicating those durable effects into
  a second aggregate journal would add redundant state, not missing provenance.
- Retained the short passive work-order lock for a coherent current runtime and
  receipt projection, consistent with adjacent configuration/progress owners.
  No long-running provider or worker action occurs in the page transaction.
- Additional enabled Staff/Minister and second-Admin permutations are a Low
  coverage follow-up for the integrated Gate 3 authorization matrix. The shared
  current-Admin capability guard, signed actor binding, independent SQL Admin/
  session check, arbitrary-actor negative and revoked-authority replay tests
  remain mandatory and are not relaxed.

Initial CI's build failure was the omitted withdrawal SQL Docker allowlist entry.
Both build contexts now include it. Eleven PostgreSQL shards and all three
browser engines passed on the initial head; the remaining shard caught that
same packaging omission. These results do not substitute for corrected-head CI.

Correction catalogs `/tmp/parishkit-withdrawal-cycle-before.json` (verified
`609baf7`) and `/tmp/parishkit-withdrawal-cycle-after-z.json` add two columns and
four constraints, extend one unique constraint/index, change eight named guard/
effect functions and remove only the redundant inventory wrapper. Relations,
triggers, policies and other objects/ACLs are unchanged. The reviewed strict
fixture has 189 relations, 2,171 columns, 3,102 constraints, 925 indexes,
532 functions, 518 triggers and 28 policies. Worker boundary grants include the
new column because their shared transition effect preserves it; SQL requires
exact withdrawal evidence for any advance. No retained database is upgraded.

Post-correction validation: 86 browser/build/grant/identity tests passed in
17.14 seconds; 34 digest-planning/recovery/schema checks passed in 61.32 seconds
alongside a repeated-cycle fixture failure (superseded content version lookup).
The fixture now selects current content and installs valid digest templates.
The complete retained-work/new-cycle regression passed in 28.24 seconds.
Fresh schema/model equivalence, lint, formatting and migration-state checks
passed. Round 1 is complete; subsequent correction rounds remain required.
