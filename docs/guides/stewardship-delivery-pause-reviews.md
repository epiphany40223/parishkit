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
failure or a reason to weaken its assertion. Remaining round validation, two
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
