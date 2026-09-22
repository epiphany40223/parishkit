# Stewardship Production link preparation and activation handoff

Continue [ADM-05.03/.04/.05](../tasks/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
from [PR #58's protected delivery](stewardship-go-live-readiness.md#protected-delivery)
on `pr/stewardship-production-activation`, based on verified main `47ec3599`.
The [Production-transition specification](../specs/stewardship/admin-portal/spec.md#production-transition)
owns behavior; the [Phase 4 implementation plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
owns dependencies, review gates and the synthetic/disposable-only restriction.

## Delivery boundary

PR #59 delivers checkpoint 1 below as an independently usable Admin workflow:
prepare inactive links after cleanup, inspect bounded progress, retry failures,
cancel/dispose staging, and reject stale inputs without activating the campaign.
It includes the applicable SQL-role, source-change, crash/retry, HTTP and browser
acceptance. This is a vertical workflow, not a helper-only PR. Its scope was
split at this boundary to keep reviews smaller before introducing final
readiness guards, activation and withdrawal owners.

After three completed dual-source rounds and exact-head CI/DCO, merge through
the protected workflow, verify main, and begin the remaining checkpoints on a
fresh branch. ADM-05.03/.04/.05 remain unchecked until their full original
acceptance passes. This split does not waive final-transaction load measurement,
catch-up handoff, boundary races or integrated Gate 3 review.

## Following activation and withdrawal outcome

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
   delivery for each coherent increment before the delivery-pause slice. Audit
   any fresh-schema change independently against the immutable predecessor.
   Gate 3 remains separate.

## Current status

Historical: this section describes the work as it stood before final
confirmation and withdrawal were delivered by PRs #60 and #61. The
operator's activation procedure is the launch runbooks'
[Production activation](stewardship-launch-runbooks.md#production-activation).

At the time: investigation and implementation were in progress; none of
ADM-05.03/.04/.05 was complete. The previously missing runtime preparation caller and Admin controls
are implemented; final confirmation and withdrawal remain closed. No real
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

## Admin preparation checkpoint

The cleanup page links to a paginated, current-Admin preparation workflow.
GET/HEAD do not create tasks, renew idle sessions or disclose credentials. Signed,
CSRF-protected controls bind the original actor, transition and inputs, then
recheck current authority under work ordering. Failed preparation/disposal retries
create immutable retry children; cancellation queues bounded worker disposal.
A new revision cannot strand prior inactive ciphertext: unfinished staging must
be disposed first. Neither this page nor a ready generation activates Production.

One real initial-setup/cleanup fixture exercises the full HTTP-to-worker path,
including missing CSRF, unknown fields, stale signed input, idempotent request,
both retry paths, disposal and session revocation. Two additional cases deny
Staff/Ministry access before looking up a preparation. These three cases pass
in 28.43 seconds. Nine browser cases pass in 15.81 seconds across all three
engines, mobile/desktop, accessibility and native no-JavaScript forms. A real-clock
disposal regression exposed a preexisting earlier-SELECT timestamp; disposal now
uses the UPDATE's domain-clock expression, matching its SQL ownership guard.

The next independent schema comparison uses immutable `2ce181f` and fresh
databases `stewardship_mail_health_before_20260918m` and
`stewardship_mail_health_after_20260918m`. Only the preparation-intake and task-pin
function bodies change: prior staging must be disposed before replacement, and
runtime retry allocation requires current Admin authority/current preparation
inputs. All object counts and every other fingerprint category are unchanged.

A source-backed multi-batch regression additionally commits one partial batch,
fails its execution, resumes through a retry child without changing prior sealed
links, then promotes changed source and requires disposal before a fresh revision.
It passes in 12.94 seconds. All 17 fresh-schema fingerprint/model-contract cases
pass against the independently audited candidate. Ruff/format and changed
Markdown checks pass. Peer review and exact-head CI are still required.

## Review ledger

### Round 1

Session `20260918-222342-7f2d8b` reviewed the complete change from merged
`47ec359982151ef1cc28f24f391c2f6d97faef8b` to
`98a296ea7230ea62236bdc75e665649a3b067f7a` (tree
`bf716bba111a0ea06999841ce4bc012092343c6b`). Exact Claude permission preflight,
both source completions and finalization passed without degradation or failed
agents. Raw severity: four Medium, ten Low, no High/Critical. Four validated
Medium findings represented three distinct issues:

- Claude concurrent-control errors: fixed by mapping changed cancellation
  intent and retry conflicts to stale responses; independent-tab HTTP controls
  now return 409 while exact replay remains idempotent.
- Claude/Codex competing retries: one duplicate finding, both resolved. A retry
  must exclude other preparations' nonterminal tasks and undisposed staging,
  in both current-Admin admission and the SQL retry guard.
- Codex exact SQL fencing: fixed by carrying transaction-scoped run/fence/worker
  evidence into each preparation/disposal effect. The setting grants no
  privilege: SQL compares it to the current durable owner and live lease.
  Direct restricted SQL rejects absent, previous-run, wrong-fence and wrong-worker
  evidence, including disposal after a replacement claim. Savepoint rollback
  restores claim context without masking the original SQL error.

All 29 focused preparation, real HTTP/retry/race and complete schema-contract
tests pass in 77.18 seconds. No accepted Medium-or-higher finding remains from
this round. Low findings were below the configured validation cutoff, not
represented as a finding-free review. The reviewed head's full CI run
`35415468088` also passed; corrections require their own exact-head CI.

The correction audit independently installed immutable `98a296e` and the candidate
in `stewardship_mail_health_before_20260918n` and
`stewardship_mail_health_after_20260918n`. One read-only invoker availability
predicate is added; only intake, task-pin and worker-effect function bodies
change. The predecessor matched its fingerprint. All other object categories
are unchanged; the new function count is 526. No retained database was altered.

### Round 2

Session `20260918-223751-e0dc97` reviewed the correction from `98a296e` to
`6d4ccd32c31a87db57629a086d721fc38f441c7d` (tree
`134eb13d6bef78d06a941542459df96f066a3891`), with surrounding ownership/grant
context. Exact permission preflight and both source completions/finalization
passed with no degradation. Raw severity: two Medium, six Low, no High/Critical.
The two validated Medium findings concern the same direct-SQL exclusion race.

Accepted and fixed: a runtime retry must already hold work ordering before its
root is locked; the deferred trigger rejects an un-ordered retry rather than
acquiring that lock late and inverting lifecycle order. Availability is explicitly
VOLATILE so its read does not depend on a caller's statement snapshot. Real
restricted connections reproduce the old retry defect: a retry committed while
competing intake was uncommitted. The intake-versus-intake test already rejected
the loser on PostgreSQL 18.6; it remains regression coverage for post-wait
visibility. Both races and the normal HTTP retry/disposal cases pass after the
fix: four cases in 66.07 seconds. No accepted Medium-or-higher issue remains.

Independent databases `stewardship_mail_health_before_20260918o` and
`stewardship_mail_health_after_20260918o` compare immutable `6d4ccd3` with this
correction. The predecessor fingerprint matches. Only the availability and
task-pin functions change; all object counts, owners, ACLs and other fingerprint
categories remain unchanged. The strict fresh-schema contract is revalidated
before committing this checkpoint.

### Round 3

Session `20260918-224913-575bda` reviewed the correction from `6d4ccd3` to
`d60bc2989c4829ff731465da9046e18c08ea8de9` (tree
`94b4f0e1aa21f472d1819efae44f56ad2f1b67ac`). Exact permission preflight and
both source completions/finalization passed without degradation or failed
agents. Raw severity: two Medium, six Low, no High/Critical. Both validated
Medium findings received corrections:

- Claude fixed-snapshot intake/retry: enforce READ COMMITTED before admission;
  advisory serialization cannot refresh a REPEATABLE READ snapshot. Real
  restricted intake must reject the unsupported isolation before waiting, and
  an otherwise ordered retry must also fail without creating a child.
- Codex post-wait current scope: make the read-only scope predicate explicitly
  VOLATILE as well. The new two-connection epoch-invalidation regression already
  rejected stale intake on PostgreSQL 18.6 before this correction; that specific
  failure was not reproduced. Keep the explicit fresh-read contract and the
  regression rather than depending on the enclosing trigger's snapshot behavior.

Independent retained databases `stewardship_mail_health_before_20260918p` and
`stewardship_mail_health_after_20260918p` compare immutable `d60bc29` with this
correction. The predecessor fingerprint matches. Only current-scope volatility
and the intake/task-pin function bodies change; all object counts, owners, ACLs
and other fingerprint categories are unchanged. No existing database was altered.

All seven real-connection concurrency and HTTP workflow cases pass in 108.96
seconds after correction, including ordered fixed-snapshot retry rejection.
All 17 fresh-schema/model-contract cases pass in 18.84 seconds. Ruff, formatting,
changed Markdown and diff checks pass. All three dual-source
review/fix rounds are complete with no unresolved accepted Medium-or-higher
findings; exact-head CI/DCO and protected delivery are still required.

## Protected delivery

PR [#59](https://github.com/epiphany40223/parishkit/pull/59) merged through normal
protected auto-merge on September 19, 2026 at 03:19:55 UTC as
`880507fc573f3018d4bbf1e3073e59b83ab5849b`. Final reviewed-and-corrected head
`004b7791121e9e9e06e61fb60348fd5cf04f36c4` passed full CI run `35417533423`
and DCO. The three-round ledger above remains the review evidence; no accepted
Medium-or-higher finding remains. No protection was bypassed or deployment made.

The merge was fetched and verified on `origin/main` before creating
`pr/stewardship-production-confirmation` from that exact tip. Continue final
readiness, confirmation and catch-up acceptance there; pre-start withdrawal
follows as its own coherent increment. ADM-05.03/.04/.05 remain open until their
respective full acceptance passes, and integrated Gate 3 remains later work.
