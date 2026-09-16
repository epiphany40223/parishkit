# Activation catch-up review ledger

Scope and validation belong to the [increment guide](stewardship-activation-catchup.md).
Routine technical triage follows the standing delegated delivery policy; this
ledger preserves raw severity, including rejected findings. No review result
alone releases Gate 3 or authorizes provider delivery.

## Round 1

Full branch `db8aee09` → `3ddfbc28a52319a23a846f973f405d805f11fe0e`, Pika session
`20260915-222515-412387`. Both Claude shards and Pika's Codex reviewer completed;
no failed agents, verdict mismatches, timeout or stall. Finalized result:
**REQUEST_CHANGES**, 2 High and 11 Medium validated findings, plus 14 raw Low
findings below the display cutoff. The accepted corrections and post-fix
validation below are complete; round one counts toward the three-round minimum.

### Validated findings

| ID | Source / raw severity | Disposition and evidence |
| --- | --- | --- |
| C1 | Claude / Medium | Fixed: remove the unused late-binding owner and no-op fixture calls. Activation allocation remains the sole binding owner; tests assert that committed source/task bindings cannot be rewritten. |
| C2 | Claude / Medium | Fixed: scope predecessor fulfillment queries by definition, Production mode and Admin target. |
| C3 | Claude / Medium | Fixed: two actual worker connections contend on the observed PostgreSQL lock, reload the cursor, and commit distinct groups; a second case includes Family replacement lineage. Only the harness observer uses owner visibility. |
| C4 | Claude / Medium | Fixed: add a replacement-leading index for recursive successor lookups, retaining the demand-leading writer index. |
| C5 | Claude / Medium | Rejected, already handled: `plan_family()` invokes `require_work_order()`, which requires both an open transaction and the existing advisory lock. Added an autocommit rejection test proving no occurrence is written. |
| C6 | Claude / Medium | Duplicate of C4: the model and fresh-install state describe the same missing index; corrected together. |
| C7 | Claude / Medium | Rejected as a request to relax an intentional hold: unresolved initial/delivery work must not release preparation. BG-06 owns terminal/deliverability recovery and must integrate before ADM-05 activation is enabled. The retained ordered cursor identifies the unresolved next Family; no private text is copied to errors. Quarantining pending mail as complete would change the controlling contract. |
| C8 | Claude / Medium | Rejected, already handled: `stewardship_campaign_runtime_v1` requires matching immutable lifecycle/control evidence before campaign updates can fire credential effects. An actual web-role direct-close test proves the attempted mutation fails and the campaign remains active. Function EXECUTE revocation is not the claimed sole defense. |
| C9 | Claude / Medium | Documented intentional behavior: restore/purge/go-live revocation forbids further progress/failure writes. The execution is recovered through ordinary lease abandonment after admission returns; the existing restore-before-claim and restore-after-claim tests retain demand/cursor. Do not bypass maintenance to manufacture a retry receipt. |
| C10 | Claude / Medium | Rejected, unreachable under admitted owners: source reconciliation preserves FamilyCampaign identities and marks inactivation; it does not delete visited cohort rows. Purge closes admission before deletion. Existing source-inactivation and live-source-recheck tests preserve the cohort. Strict equality therefore detects corruption rather than accepting missing evidence. |
| X1 | Codex / High | Fixed: database receipts validate each exact bounded date page, its sequential cursor, retained outcomes/fulfillment, exhausted traversal and final selection. Actual-role tests reject fabricated page, cover, digest and final receipts; omitted fulfillment rolls back the real coverage batch. |
| X2 | Codex / High | Fixed: occurrence writes require immutable claim-event evidence matching the live run, worker and fence, canonical target/cohort/slot/due/key, applicable skip reasons and same-target valid coalescing. Actual-role tests reject arbitrary targets/dates/keys, unfenced or superseded claim evidence and false ineligibility. |
| X3 | Codex / Medium | Fixed for valid replacement: append Family selection lineage without rewriting original coalesced reminders. Removal of only the initial while reminders remain is already prohibited by configuration; removal of the complete set retains history without recreating mail. Regression tests cover both admitted cases. |

### Raw Low findings

All 14 are retained here; none is silently promoted to acceptance or counted as
a missing review source.

1. Digest set allocation: fixed, hoist the union and use a set of slot keys.
2. Duplicate exclusion query forms: deferred readability-only consolidation;
   both bounded date checks and cover queries enforce the same fulfillment/hold
   rules. No incorrect result was identified.
3. Runtime task-type import: fixed, import from its allocation owner.
4. Cover cursor's retained date: documented as the exhausted page-chain proof;
   outstanding outcomes, not this date, select the next coverage batch.
5. Grant composition and unused scheduler lineage read: fixed, merge worker
   grants and do not grant that table to the scheduler.
6. Duplicate binding and no-op attribution: fixed with C1; allocation keeps the
   originating demand attribution instead of rewriting identical values.
7. Missing prepared source diagnostic: fixed with a typed invariant failure.
8. Date-reader purpose: documented as Production Admin-digest-only; it does not
   interpret Family or Testing records as report dates. Canonical slot guards
   reject malformed new preparation dates.
9. Combined invalid-argument diagnostic: deferred wording-only refinement; the
   bounded reader rejects all invalid inputs before querying, with no private
   values included.
10. Lifecycle emitter docstring: fixed to describe atomic task allocation.
11. Guard naming/shared immutable function: deferred cosmetic rename. Strict
    schema and enabled immutable-trigger inventory tests explicitly cover the
    shared guard; a rename would not improve enforcement.
12. Corrupted-cycle fixture: deferred deliberate guard-disabling test. The
    recursive UNION terminates repeated IDs; valid writes permit only forward
    pending-successor edges. Existing multiple-page lineage tests cover normal
    keyset traversal. No runtime cycle-creation path was identified.
13. Data specification wording: clarified as a requirement on later digest and
    post-close consumers, not a claim that BG-07 is implemented.
14. Fresh-install model state ordering: deferred cosmetic reordering; Django's
    complete state resolves references and model-to-database tests validate it.

### Validation checkpoint

Before correction, the same-tree full quality run at `3ddfbc2` passed all 2,923
PostgreSQL tests across four disposable clusters, with 94.10% line and 85.52%
branch coverage. This supersedes the earlier inventory-test failure, not the
acceptance requirement for subsequent corrections. The first corrected 77-test
regression set passed in 59.46s. The wider corrected set passed 134 PostgreSQL
tests in 67.60s, including strict schema fingerprints, model/state parity,
actual-role negative cases and observed contention with lineage insertion.
The baseline passed 5,674 tests in 54.89s; lint, formatting and changed Markdown
checks passed. The fresh-schema audit verified only the three new validation
functions, named guard changes, argument-label change and one successor index
before updating the strict baseline. Rebuilt-image configured development and
production Compose checks passed (2 tests in 109.11s), as did full tracked
Markdown validation. No accepted Medium-or-higher round-one finding remains
unresolved. Independent correction review follows before PR delivery.
