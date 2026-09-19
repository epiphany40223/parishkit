# Delivery-pause review ledger

[Implementation and evidence](stewardship-delivery-pause.md) ·
[Automated review cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)

## Round 1

Pika session `20260919-051307-70c78f` reviewed the full PR diff from merged
`4be1ce09` to `abfc52ca`. Both Claude shards and the independent Codex reviewer
completed; finalization had no failed agents, degradation or verdict mismatch.
The exact-path permission preflight passed. The result requested changes:
eight validated findings from 25 raw (four High, five Medium, sixteen Low before
the confidence/severity cutoff; seventeen were filtered out). No salvage was
required. Corrections and focused validation below complete this first round;
two more rounds remain required before delivery.

Routine technical triage follows the human's standing delegated authority:

| Finding | Severity/source | Disposition |
| --- | --- | --- |
| Pause confirmation starves during live inventory churn | High / Claude | Accept. Pause previews are informational; only resume requires an exact backlog in the controlling live-pause specification. Retain exact current counts in the committed command while still fencing campaign/runtime/authentication changes. |
| Receipt/weekly release and later-pause regression gaps | Medium / Claude | Accept. Exercise actual restricted MAIL dispatch, another held type, and invalidation of a released-but-unsent message after a new pause. |
| Snapshot-wide rather than recipient-specific cancellation coverage | Medium / Claude | Duplicate of Codex's frozen-version/subset finding below; fixed together, not discarded as harmless. |
| One failed initial blocks campaign-wide resume | Medium / Claude | Accept. Defer that Family to the unchanged ordinary initial-invitation recovery checks without selecting/coalescing its work or blocking unrelated Families. |
| Mixed accepted/cancelled cohort incorrectly counts a full skip | Medium / Claude | Push back on the proposed whole-slot delivery fulfillment. The specification requires an explicit skip separate from provider success. Accepted child outboxes remain unchanged; only remaining held children are cancelled. Add explicit mixed-cohort assertions and retain actual recipient coverage. |
| In-flight retry returns without an atomic pause hold | High / Codex | Accept. Attach the current hold when a message returns to an unsent state, and continue counting an idempotent-uncertain retry as unresolved. |
| Semantic slot exclusion ignores exact input coverage | High / Codex | Accept. Only current covered versions exclude a slot; later live inputs invalidate the old coverage proof. CI independently found the same regression. |
| Cancellation fabricates current versions and full-snapshot subsets | High / Codex | Accept. Freeze selected versions at report capture, independently validate them in SQL, and derive cancellation coverage from each recipient's actual subset. |

Checkpoint CI `35434036270` passed browser, runtime and ordinary validation,
but PostgreSQL partition 2 failed the existing later-correction resolution test.
That failure is part of the accepted exact-coverage fix, not an unrelated test
failure or a reason to weaken its assertion. Two
further review/fix rounds, exact-head CI/DCO and protected merge remain required.

Correction validation: eight actual-role regression scenarios passed in 117.07
seconds, including the CI regression, both released non-daily message types,
re-pausing, in-flight retries, later inputs and exact recipient subsets. The
extended failed-initial case passed in 27.59 seconds and verifies that an actual
MAIL claim still cannot send its deferred reminder. Nineteen schema/model/guard
and mixed accepted/cancelled-cohort checks passed in 38.56 seconds. Thirty-four
focused unit tests passed in 0.18 seconds; model-state, Ruff, formatting,
Markdown and whitespace checks passed.

The independently installed `after-l` catalog adds only the frozen item-version
column/NOT NULL constraint and opaque current-coverage view. Seven function
bodies, four view definitions and the existing immediate-hold trigger change.
Indexes, row policies and existing owners/ACLs are unchanged. No object was
removed and no retained database was upgraded or deleted.

## Round 2

Pika session `20260919-054046-5a895b` reviewed the correction delta
`abfc52ca` to `bdfb2a6`, with the prior dispositions and surrounding contracts.
Both sources completed without degradation, failed agents, mismatch or salvage.
The exact permission preflight passed. Four findings were validated from eight
raw: one High, three Medium and four filtered Low. Corrections below address
both accepted defects; the Codex finding duplicates the first Claude finding.

| Finding | Severity/source | Disposition |
| --- | --- | --- |
| Differing weekly recipient subsets violate one-resolution-per-occurrence | High / Claude, Medium / Codex | Accept as one defect. Aggregate the exact ordered union of explicitly cancelled children; never include accepted siblings. Retain individual per-message decisions and independently validate the union. Exercise real differing recipient subsets, with and without an accepted sibling. |
| Later completed reporting still revives earlier skipped slots | Medium / Claude | Accept. A later completed automatic snapshot with current inputs can discharge the newer obligation without rewriting earlier immutable coverage. Manual and incomplete reports cannot supply that proof. |
| Idempotent uncertain retry cannot resume during an active pause | Medium / Claude | Out of supported provider scope. The current Workspace SMTP adapter never supplies contractual idempotency; its Python outcome map and all actual MAIL SQL writers reject `retry_idempotent`. The generic journal's synthetic test-owner branch deliberately remains fail-closed. Add an actual MAIL rejection assertion; a future provider adapter must own reconciliation of this state before enabling it. Do not make uncertain delivery cancellable or expand MAIL permissions. |

The independent `after-m` catalog adds one private coverage-union function,
changes only the settlement and post-close guard functions, and changes the
opaque current-proof view. All columns, constraints, indexes, row policies,
triggers and existing ownership/ACLs remain unchanged. No retained database is
upgraded or deleted. Twenty-one focused PostgreSQL tests passed in 78.34 seconds,
including genuinely differing recipient subsets with/without accepted siblings,
later delivered coverage restoring the prior skip, strict schema/model parity,
and the generic later-version regression. The earlier run passed five existing
close/retry scenarios, including rejection of invented MAIL idempotency; its two
new fixture failures were corrected by adding a third real report generation.

Checkpoint CI `35435352606` passed 22 jobs; partition 3 and its aggregate failed
only because an old raw-retry test expected the pre-lock error message. The new
immediate-hold trigger obtains the work lock first, so the unchanged owner guard
rejects the absent live claim instead. Update that precise expectation and
assert unchanged state/version; no authorization check is relaxed. The focused
CI regression passed in 30.48 seconds; repository Ruff/formatting, changed-doc
Markdown and whitespace checks passed. Round 2 is complete. Round 3 and
corrected-head CI/DCO remain required.
