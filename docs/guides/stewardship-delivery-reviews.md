# Delivery journal review ledger

[Scope and validation](stewardship-delivery-journal.md) ·
[Controlling delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)

## Incomplete first attempt

Pika session `20260914-181907-5d9331` reviewed base
`6c8cd512e5121095961ffbeb3f80f4dfd844043c` through head
`3c7134d4355815fd2c9c4bdcf8a40035e326d1d4`, tree
`8708b0c0baf0c3f127782d5915b9af5cac14c00f`. The permission probe passed.
All three generated Claude shards completed. Pika's Codex reviewer did not:
`codex-reviewer: stalled (no new output within the idle window) — treated as timed out`.
This is degraded review evidence, not approval or a completed dual-source round.
Finalization retained 11 Medium findings (including duplicates), with no High or
Critical; the raw reports also contain 24 Low findings. Finalize artifact SHA-256:
`53fe931a6a6d76123ebc974346285a60ad0f87e8c0a1b6013a934332c46dc54b`.

### Medium dispositions

Routine specification-consistent corrections follow the standing autonomous
triage authority. Implemented fixes remain subject to post-fix validation and a
successful replacement review.

| Concern | Disposition |
| --- | --- |
| Missing delivery/cleanup retry coverage | Add real PostgreSQL retry, deadline, evidence and claim-recovery tests. |
| Running cleanup stranded after TaskRun recovery | Add an explicit newer-claim recovery edge that preserves checkpoints. |
| Randomized ciphertext breaks semantic replay (two findings) | Bind stable credential metadata; resealed replays retain the first envelope. Keep documented original-initiator attribution. |
| Write-grant test masked by absent SELECT | Inspect table and column privileges for all eight journal tables, with real grants for online roles and every credential-installer target. |
| Missing campaign audit attribution | Persist the campaign UUID in both journals' audit events; assert it in PostgreSQL tests. |
| Key-metadata-only updates evade dependency checks | Validate metadata changes under the shared inventory lock; test mismatched keys and two-connection exclusion. |
| Missing go-live/epoch recheck | Guard Testing creation, preparation and submission; verify Production credential binding and reject invalidated rehearsal authority. |
| Unfenced provider outcomes (two findings) | Require evidence and the current claim for direct submitting outcomes. Lost claims may conservatively record uncertainty; reconciliation requires attributed evidence and fresh owner admission. |
| Go-live accepts nonterminal mail or invented totals | Check actual campaign `testing_override` terminal counts atomically before accepting the request; rejection rolls back the gate. |

### Raw Low dispositions

The following accounts for all 24 raw Low findings, including issues duplicated
by validated Medium findings. Severity remains the reviewers' original severity.

| Raw concern | Disposition |
| --- | --- |
| Failure reasons allowed on unrelated actions (two findings) | Require a safe nonempty reason only for failure/delay; clear it on subsequent nonfailure actions. |
| Checkpoint cumulative-count query cost | Defer optimization to BG-03's real inventory/batch workload; retain bounded batches and exact per-category checks. |
| Long generated constraint identifier | Retain current baseline convention and PostgreSQL's verified catalog name. No upgrade operations or compatibility promise are introduced. |
| Preliminary evidence reads as final | Explicitly label earlier results as checkpoints; record final acceptance separately. |
| Additional cleanup recovery/category/cancellation tests | Add new-claim recovery, cumulative-category bounds and abandoned-task cancellation rejection. |
| Cancel after an uncertain idempotent retry | Reject cancellation until the uncertain outcome is resolved. |
| Key metadata rebind bypass | Address with the corresponding Medium correction. |
| Submit actor can differ from worker | Enforce actor/worker equality in the SQL claim guard. |
| Secret-history assertion cannot detect ciphertext | Inspect serialized event/audit values for the actual envelope and plaintext. |
| Unrelated-field negative test hits another constraint | Mutate a valid but unrelated schedule field and match the specific guard error. |
| Missing unknown/direct-outcome evidence tests | Parameterize missing-evidence checks for every provider-outcome action. |
| Retirement test does not prove a race fence | Add real independent transactions holding exclusive/shared inventory locks. |
| Misleading shared pause/deadline/claim error | Give pause, not-yet-due and claim failures distinct errors. |
| Cleanup deletion port not implemented | Explicitly assign terminal Testing graph deletion to BG-03; no generic deletion bypass is added. |
| Mixed SQL layout | Defer cosmetic normalization; independently audit exact catalog definitions. |
| Cross-module test helpers | Retain the repository's existing fixture-import pattern for this slice; shared factory extraction is not required for correctness. |
| Reprepare uncertain idempotent payload | Reject re-preparation until resolution so a reused provider key keeps its original payload. |
| Retained database wording implies compatibility | Clarify that retained databases are untouched, not upgraded. |
| Original creation actor binding | Document logical initiator versus transient worker identity; preserve attribution. |
| Redundant replay comparisons | Retain explicit attribution/render checks as readable contract assertions alongside fingerprints. |
| Duplicated Production action vocabulary | Derive the model vocabulary from the typed action enum plus journal-only events. |
| Cleanup counts annotation disagrees with frozen value | Annotate the public value as a read-only Mapping. |
| Testing-prefixed production class names | Retain descriptive domain names with explicit pytest collection exclusions. |

## Validation investigation

The first four-shard full coverage run is not acceptance evidence: shard 3
passed 640 database tests; the others reported 18/17/12 failures and 8/9/0
errors respectively. A wall-clock discontinuity was observed during the run,
but its causal relationship to failures is not established. No deadlines,
session limits, leases or tests were weakened. A separate rerun of 112 tests
covering secret requests, identity reviews, integration views and presence
passed in 52.26 seconds. The first corrected journal suite passed 56 tests in
37.82 seconds. Complete same-tree coverage remains required.

After the remaining corrections, 77 journal PostgreSQL tests passed in 46.44
seconds. The repeated additive catalog audit passed: all preexisting objects
remain unchanged. Relative to the first journal checkpoint, only the new
Production action constraint and four new journal function bodies changed;
object counts are unchanged. The corrected function fingerprint is
`8ed9ca1aa0094a1a1479c6c54cc321eb7d037b16833115675f858bd46734da04`.
An initial combined invocation ran the external audit before the repository's
fresh-seed fixture and consequently failed the later fixture after audit-test
flush; audit and repository schema tests must run as separate invocations.
