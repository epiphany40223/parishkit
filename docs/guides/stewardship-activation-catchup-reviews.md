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

## Round 2

Correction delta `3ddfbc28a52319a23a846f973f405d805f11fe0e` →
`74a25903eb92d0c9ae35ad6c3c84cb99fe823c13`, Pika session
`20260915-230544-6c8a1b`. Claude and Codex both completed successfully. Finalized
result: **COMMENT**, six Medium findings, no High/Critical, and ten raw Low
findings below cutoff. There were no failed agents, mismatches or degradations.

### Round-two validated findings

| ID | Source / raw severity | Disposition and evidence |
| --- | --- | --- |
| C1 | Claude / Medium | Fixed: resolve the immutable claim event once per digest batch, retaining per-effect ownership checks. The real digest test observes exactly one resolution for each date/cover batch. |
| C2 | Claude / Medium | Fixed: recheck the exact TaskClaim before each Family lineage insert. SQL independently checks the event against the live lease; the additional Python check keeps all effect loops consistent. |
| C3 | Claude / Medium | Fixed: any semantic coverage or independent unreviewed/assumed-delivered restore hold excludes the initial slot consistently in SQL and Python. A real worker can prepare remaining reminders without consuming the initial's restore hold. |
| C4 | Claude / Medium | Fixed: aggregate date selection excludes covered/restore-held originals and synthetic recovery slots. A newest-held-date regression completes the aggregate at the latest eligible date and retains the held original. |
| X1 | Codex / Medium | Fixed: the Family receipt guard requires forwarding every cancelled coverage predecessor when a current selection exists. Suppressing the Python forwarding call now rejects and rolls back the receipt/new selection; original fulfillment remains intact. |
| X2 | Codex / Medium | Fixed: pending applicable Family selection count must be zero when current eligibility, response or close state requires a skip. Actual-worker forged receipts fail for inactive, email-ineligible, undeliverable and closed cases. |

A related self-audit corrected Family pending-count/unresolved checks and digest
uncertainty checks to use the same exclusion set. Independent restore holds are
not preparation failures and remain unchanged after preparation completes. The
shared read-only SQL predicate consolidates these repeated checks without adding
writer authority. Completion and coverage are still not provider permission.

### Round-two raw Low findings

1. More than 100 Family predecessors: rejected as the claimed accumulated-chain
   failure. The query selects only predecessors without a successor; successfully
   forwarded earlier links leave that set. Repeated replacements do not accumulate
   100 unforwarded ancestors. The bound detects corrupt/unhandled inventory;
   changing its exception to retryable would not make that inventory progress.
2. Claim-event uniqueness: already enforced by event-to-current-run binding,
   sequential event versions and increment-on-claim fences. Arbitrary duplicate
   claim events cannot be inserted under the existing guards. Do not choose an
   arbitrary latest row to hide broken history.
3. Multiple unfinished activation demands: already prevented by draft-only
   Testing-to-Production activation and unique demand per immutable activation.
   No admitted transition returns a campaign to draft for another activation.
   A redundant partial index is not needed to fix a reachable selection ambiguity.
4. Broad forged-receipt assertion: fixed. Digest cases now require the specific
   inventory-proof error; completion requires its own completion-proof error.
5. Prepared-source diagnostic distinctions: deferred wording/test refinement.
   Missing or unbound source is the same closed invariant failure at this
   boundary, and allocation rollback is tested. No private source details should
   be exposed and no fallback allocation is allowed.
6. Specification wrapping: fixed.
7. Real retry delay: retained the short database wait because task lease/backoff
   time deliberately uses wall time, not the campaign clock. Added an explicit
   successful-reclaim assertion so future timing changes fail diagnostically;
   direct mutation of immutable retry evidence is not a suitable substitute.
8. Redundant demand refresh: removed.
9. Post-correction full-suite evidence: the round-one ledger explicitly labels
   the full suite's pre-correction SHA and corrected subsets. Final-tree full
   coverage remains required before delivery; no subset is presented as that run.
10. Intermediate cover progress inflation: retained as a Low limitation of
    count-only preparation telemetry. A fabricated positive intermediate receipt
    cannot settle outcomes or release preparation; the final receipt independently
    proves the full inventory. Binding counts to each effect would require new
    batch attribution, not timestamp guesses. No denominator/percentage or delivery
    success is inferred from these counters.

### Round-two validation checkpoint

The first added-test run exposed invalid eligibility fixture combinations and
missing maintained execution in the restore harness, not accepted runtime
behavior. After correcting the harness, all 42 focused PostgreSQL tests passed
in 47.59s. The fresh-install audit against `74a2590` verified exactly one added
read-only helper and the three intended guard changes; all other inventories
are unchanged. The strict baseline was updated only after that comparison.
The broader corrected suite passed 142 PostgreSQL tests in 71.73s, including
the strict schema baseline. The baseline passed 5,674 tests in 54.57s, with
environment-gated suites explicitly skipped. Lint, formatting and all tracked
Markdown passed. Rebuilt-image configured development and production Compose
checks passed (2 tests in 106.04s). All six accepted Medium findings are fixed;
round two is complete. Full corrected coverage and round three follow.
