# Stewardship Production activation and withdrawal

Continue [ADM-05.03/.04/.05](../tasks/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
from [PR #58's protected delivery](stewardship-go-live-readiness.md#protected-delivery)
on `pr/stewardship-production-activation`, based on verified main `47ec3599`.
The [Production-transition specification](../specs/stewardship/admin-portal/spec.md#production-transition)
owns behavior; the [Phase 4 implementation plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
owns dependencies, review gates and the synthetic/disposable-only restriction.

## Intended coherent outcome

An Admin can finish a cleaned-up campaign's go-live process with fresh Google
authentication and explicit typed confirmation. Prepare inactive Family links
as bounded background work before the short final transaction; never enumerate
Families or construct their messages while holding final activation locks.
Preserve stable manual Family codes. Select the prepared generation atomically
with the campaign state, global mode, cleanup request and gate release.

Before the start instant, the result is scheduled. Inside the half-open campaign
interval it is active, with one durable catch-up demand/task and visible mail
preparation progress. At or after close, confirmation fails without a partial
mode change. Only scheduled pre-start campaigns may be withdrawn through the
reasoned, freshly authenticated workflow. Completed Testing deletions remain
irreversible; a later go-live attempt needs fresh evidence and cleanup.

## Implementation checkpoints

1. Bind resumable general-worker token preparation to completed cleanup, current
   source/population/configuration, credential epoch and public-key inventory.
   Provide current-Admin progress, retry/cancel, stale-input rejection and safe
   inactive-generation disposal. Reuse the maintained task runtime and existing
   token-generation primitives; do not expand private-key mounts or turn the
   deletion-only cleanup worker into a Production writer.
2. Build post-cleanup readiness and exact impact preview, using the immutable
   pre-cleanup aggregate only for intentionally deleted test evidence. Establish
   complete version/as-of guards for short final confirmation, not just a tuple
   that omits delivery, coverage, hold or newly due work changes.
3. Implement final confirmation and atomic scheduled/direct-active activation,
   current/fresh authorization, current readiness, boundary checks and existing
   catch-up integration. Keep submission availability separate from mail holds.
4. Implement guarded pre-start withdrawal and future-work cancellation, blocking
   provider-submitting/uncertain effects and losing safely to the start boundary.
5. Exercise actual restricted SQL roles, interrupted preparation, stale inputs,
   role/session revocation, concurrent confirmations and boundary/withdrawal
   races. Measure the final transaction at 5,000 Families and verify catch-up
   recovery plus responsive submissions. Include no-JavaScript and three-engine
   mobile/keyboard/accessibility acceptance.
6. Complete three dual-source review/fix rounds, final-head CI/DCO and protected
   delivery before the delivery-pause slice. Audit any fresh-schema change
   independently against the immutable predecessor. Gate 3 remains separate.

## Current status

Investigation and implementation are in progress; none of ADM-05.03/.04/.05 is
complete. Existing token-generation storage has no runtime preparation caller,
so that dependency is included before opening activation authority. No real
provider calls, retained-database deletion, deployment, release or historical
upgrade compatibility is authorized by this work.

## Internal preparation checkpoint

Immutable preparation intent now binds completed cleanup, source snapshot and
generation, campaign configuration, credential epoch, key inventory and exact
eligibility coverage. Its opaque task root is committed with the request;
unbound task insertion and forged input bindings fail in PostgreSQL. Internal
batch effects require a live exact claim and use the existing public-key sealing
and ready-manifest owners. A ready generation does not select a campaign pointer,
change mode, send mail or change stable manual codes.

The 25 pure input cases and two initial PostgreSQL integration cases pass; the
latter run actual cleanup, request replay, stale-input/claim denial and inactive
token creation. Runtime-role grants, maintained handlers, cancellation/disposal,
Admin UI and final activation remain in progress. These initial tests explicitly
use the internal owning admission seam, not a completed web/worker integration.

The schema checkpoint was independently installed from immutable `47ec3599` and
the current candidate into separate retained databases
`stewardship_mail_health_before_20260918k` and
`stewardship_mail_health_after_20260918k`. The predecessor matched its recorded
fingerprint. No existing object changed or disappeared: the delta adds two
tables, 21 columns, 36 constraints, 11 indexes, three private functions and three
triggers, with all policies unchanged. Totals are 185 relations, 2,128 columns,
3,030 constraints, 912 indexes, 522 functions, 498 triggers and 28 policies.
Fingerprint changes are limited to those inspected additive categories; further
SQL-owner changes require a new comparison before acceptance.

Combined focused validation passes 44 input, PostgreSQL preparation and complete
schema-contract cases in 22.83 seconds. Full Ruff/format, migration-state drift,
changed-Markdown and diff checks pass. This is an internal checkpoint, not a
completed ADM-05 task or a reviewed release candidate.

## Maintained preparation and disposal checkpoint

The general worker now seals inactive links in bounded batches through the
maintained dispatcher, with repeated claim/input checks and atomic manifest/task
completion. A separate immutable cancellation intent owns bounded disposal.
Cancellation before preparation, after preparation and after credential-epoch
change never selects live links or changes stable codes. The scheduler reads
completion evidence without receiving token execution or private-value access.
Recovery accepts only a current ready manifest or proven stale disposal.

Restricted-role tests exercise both worker and scheduler logins. The scheduler's
deployment-metadata id-only UPDATE grant permits row locking; immutable identity
and mandatory-version guards reject actual edits. SQL independently rejects a
task success without its completed domain proof. CI's immutable-model inventory
now registers the two new shared INSERT/UPDATE/DELETE guards, verifying their
enabled row-level rejection contract instead of assuming generated guard names.

An independent comparison installed immutable `8c9d290` and this checkpoint in
new retained databases `stewardship_mail_health_before_20260918l` and
`stewardship_mail_health_after_20260918l`. The predecessor fingerprint matched.
Only functions/triggers changed: three new functions and three new triggers;
the existing boundary guard delegates exact inactive-generation effects, and
the current-input predicate becomes a read-only SECURITY INVOKER function usable
with existing caller metadata grants. It gains no elevated execution authority.
All table, column, constraint, index, policy and ownership fingerprints remain
unchanged. Totals are 525 functions and 501 triggers; other totals remain above.
