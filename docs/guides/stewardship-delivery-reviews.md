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

## Successful replacement review

The next full-diff attempt, session `20260914-190751-a1ee81`, reviewed head
`2b8eddbf586fc8f8f073f37221d221b20adf8fa6`, tree
`07d082b0bcbae92e578dae5aaf8bf70f439f93ea`, against the same merged base.
Its three Claude shards completed, but Codex failed on quota before the human's
usage reset. That attempt is not a completed round. Its finalize artifact is
`79ab385af20a811553692f67fd219da7a3de3769891cc71509c6519a4be1bed1`.

At the human's request, replacement session `20260914-214918-7f98cd` reran only
Pika's failed Codex source. A fresh exact-command permission preflight passed.
The base, head, tree, focus, full/shard diff bytes and Claude prompts were
verified equivalent; the only prompt difference was the session path. Existing
Claude outputs were copied byte-for-byte and revalidated, not rerun or rewritten.
`reused-claude-provenance.json` preserves that provenance. Claude output mtimes
in the replacement session are copy times, not fresh review durations.

Codex completed successfully in 776 seconds. Finalization reported no failed
agents, degradation, verdict mismatch or salvage requirement: 9 validated
Medium findings, 0 High/Critical. The raw reports contain 10 Medium (one
cross-source overlap) and 31 Low findings. Finalize artifact SHA-256:
`47488e266c0af475a59d12b50f5e1d7bcb846f81887c20c4e39e23f4b3406aec`.
Count this as the first successful dual-source review, not three separate rounds
for the original failures and replacement. Post-fix validation completes the
round; two further completed correction rounds remain required before a PR.

### Replacement Medium dispositions

All nine validated findings were verified and corrected under the standing
autonomous triage authority. No additional approval or scope expansion was
needed.

| Concern | Correction |
| --- | --- |
| Testing retry reopens frozen cleanup inventory (agreed) | Recheck mode/gate/epoch on failed retry and other transitions back to unsent work. Regression proves linked task/render changes roll back after go-live begins. |
| Missing independent SQL tests | Add raw ORM-bypass binding, render shape/template, hold, retry/completion clock, Production credential and checkpoint-event tests. The claimed missing go-live retention test already existed and remains. |
| Retry lock-order inversion | Both journals require the outer work-order lock before retry-root allocation. Tests reject plain atomic transactions and preserve successful linked retries. |
| Exhausted crash strands cleanup | Add attributed `recovery_fail`, bound to the latest fenced failed TaskRun; test both crash before domain start and crash after a checkpoint, then explicit retry. |
| Key rotation breaks resealed replay | Exclude encryption key identity as well as randomized ciphertext from semantic replay; verify original envelopes and retained-key dependencies survive rotation. |
| Admission cannot see proposed transition proof | Supply frozen `DeliveryCommand` with exact evidence/options and redacted metadata; pin the proposed submit task before admission and replay, including a child retry run. |
| Production submit/pause fence absent | Check current Production mode/campaign and restore/go-live/pause gates; bind hold/release to actual pause/resume state. Exercise real synthetic lifecycle controls, not fabricated pause flags. |
| Same cleanup batch counted twice | Unique per-request batch digest plus early replay-collision rejection; verify a new command cannot rerun its deletion callback or increase progress. |
| Deferred render pin ignores intermediate history | Validate each trigger's captured render selection; prove a later valid selection cannot rescue an invalid intermediate event. |

### Replacement raw Low dispositions

All 31 below-cutoff findings were inspected. These dispositions preserve the
original Low severity; they are not additional validated Medium findings.
Deferred owner checks must be implemented before their workflow is exposed.

| # | Concern | Disposition |
| --- | --- | --- |
| 1 | Checkpoint/transition command namespace collision fails late | Existing unique event command rolls back all database work. Defer early error classification to BG-03's compiled deletion owner. |
| 2 | Null active configuration fails after callbacks | Existing non-null request constraint rolls back all effects; ADM-05 readiness must reject incomplete setup before intent. |
| 3 | Unknown/wrong-root cleanup claim lookup | SQL rejects mismatched roots; global work-order serialization prevents the hypothesized lock cycle. BG-03 supplies owned run IDs and typed recovery errors. |
| 4 | Binding/version errors precede admission | Internal storage only, with no runtime write grants or user-callable port. ADM-05/BG-03 must authorize callers before identifying private requests. |
| 5 | Guide shows obsolete fingerprint | Corrected counts and linked the current ledger evidence. |
| 6 | Redundant Production event checks for symmetry | Keep the binding trigger as authority: action matches the checked request; previous state matches prior immutable history. Additional checks are unnecessary for correctness. |
| 7 | Action tuple assembly complexity | Preserve deterministic catalog ordering and the existing enum-derived vocabulary; no behavioral defect. |
| 8 | Key-rotation replay | Fixed with the corresponding Medium finding. |
| 9 | Replay requires original expected version | Explicitly document exact command replay, including expected version; changed intent remains a conflict. |
| 10 | Release of unheld message reaches SQL error | SQL rejects atomically. Defer presentation/error translation to BG-06's resume workflow. |
| 11 | Namespace validation occurs after admission | Callbacks perform no external work; rejected input rolls back. BG-06 must construct typed owner inputs before calling storage. |
| 12 | Repeated delivery-purpose vocabulary | Existing pure matrix and schema/model equivalence tests cover current values. Defer optional enum refactoring to the first BG-06 consumer. |
| 13 | Extra ciphertext-bearing ORM reads | Bounded internal transaction, no decryption or logging. Defer query trimming until BG-06 dispatch profiling establishes its workload. |
| 14 | Broad history-update exception assertion | Existing immutable guard is the intended defense; dedicated new intermediate-render tests independently prove the specific history-binding boundary. |
| 15 | Broad late-batch failure exception assertion | Existing tests assert rollback, with independent claim/category tests asserting specific guard messages. No accepted behavioral gap remains. |
| 16 | One-second cleanup lease tests | Preserve genuine database-clock/fencing tests; no deadline relaxation. Investigate full-suite timing failures separately before final acceptance. |
| 17 | Production token liveness | SQL currently proves identity binding; BG-06 owns current generation, token liveness and eligibility checks immediately before dispatch, now explicitly documented. |
| 18 | Future SQL writers could omit work-order lock | Current writers serialize and no runtime generic port exists. BG-03/BG-06 compiled entry points must join the same work-order lock before any gate/inventory write. |
| 19 | Redundant terminal-count scan | Retain the explicit nonterminal rejection for readability; no scale concern established for the once-per-campaign journal prerequisite. |
| 20 | Raw insert could record stale gate version | Current owner captures the locked credential version and immutable request retains it. ADM-05's compiled port must derive, not accept, that value. |
| 21 | Busy key lock and retired key share error | Both fail closed without provider work. BG-06 must classify transient key contention at its compiled boundary. |
| 22 | Task/domain completion ordering | BG-03/BG-06 must reconcile domain outcome before marking tasks terminal; generic TaskRun success never proves delivery or cleanup success. |
| 23 | SQL lower versus Python casefold | SQL supplies minimum shape checks; the typed renderer is intentionally stricter. BG-06 must use the canonical recipient validator. |
| 24 | Shared UUID helper has delivery-specific name | Internal error naming only; defer shared-helper extraction until another public owner needs it. |
| 25 | Redundant whitespace mailbox check | Retain inexpensive explicit defense in depth. |
| 26 | Operational mail can carry campaign attribution | Intentional: deduplication scope is parish, while operational errors may identify their related campaign. Testing cleanup selects routing, not attribution. |
| 27 | Whitespace reconciliation note | Nonempty verified digest and owning admission still required; BG-06's evidence resolver must require a meaningful human note rather than infer proof from note shape. |
| 28 | Grant probe checks writes, not every SELECT | Current increment promises no generic mutation port; private read exposure remains covered by existing role contracts. BG-06 must test its exact decrypt/read surface when granted. |
| 29 | Short outbox lease/retry test windows | Retain genuine timing boundaries; investigate failures without weakening leases or silently accepting flaky full-suite results. |
| 30 | Broad immutability exception checks | Existing all-model immutability suite and new specific binding tests independently cover the boundaries; assertion-message tightening is optional. |
| 31 | Function-local test imports | Retain the established fixture-import pattern; production fixtures are shared with boundary tests and broad import rearrangement is unnecessary. |

### Correction validation

The first correction run found two test-contract mismatches, not passing
acceptance: an older retry test omitted the newly required work-order lock, and
the synthetic Production fixture had not cleaned its rehearsal credentials.
Both fixtures now use the actual owning workflows. The subsequent journal/SQL
suite passed 106 tests in 74.68 seconds. The pure transition/input suite passed
212 tests in 0.31 seconds; Django reported no model-state changes.
The final focused run, including strict catalog/model checks and independent
SQL guard tests, passed 39 tests in 29.00 seconds. Ruff lint and formatting pass.
This completes the first review/fix round's applicable local validation; full
same-tree quality remains a separate PR acceptance requirement.

The independent merged-base audit passed in 11.32 seconds. All preexisting
catalog objects remain unchanged. The correction adds one checkpoint uniqueness
constraint and its index: 2,350 constraints and 719 indexes overall. Function
fingerprint:
`51fe2913061eaa7e7a74e3a4fef22e604df2601f31e5e7d65ba387bb2a2b02bc`.
The strict fixture was updated only after that comparison.

The earlier second full coverage run is also not acceptance: its four shards
reported 644 passed; 649 passed/6 failed; 636 passed/14 failed; and 665 passed.
The failures include authentication, timestamp and setup paths; the cause is
not yet established. A new 300-sample read-only host/PostgreSQL probe showed no
backward clock steps, with observed offsets from -4.505 to +7.291 milliseconds.
That probe does not retroactively explain or excuse earlier failures. Final
same-tree coverage and protected CI remain required.

## Passing complete coverage checkpoint

At head `f43585fac5656e4c0e9f54ce1d5005dc437cd46e`, tree
`443cd08aaacc5ddef9532572670f16c83340d461`, the complete four-shard rerun passed.
The baseline passed 5,512 tests in 106.27 seconds. PostgreSQL shards passed
652, 663, 657 and 672 tests respectively (2,644 total); the independent combine
accounted for the exact complete collection and passed with 94.05% line and
85.55% branch coverage. Shard wall times were 957.67, 946.50, 1,061.41 and
1,054.83 seconds; shard one's baseline is additional. These are local four-way
results, not the eight-runner GitHub CI results or proof about a later head.

No earlier authentication failures recurred. The original cause remains
unestablished; no production deadline, lease or session limit was relaxed.
A 120-second stack dump during a long setup-disposal case showed its explicit
Event wait for the real task/source/HTTP drain deadline. That case subsequently
passed after 142.934 seconds including preparation, so this delay was not a
deadlock. A separate dense 10,000-read clock probe observed no backward step.

## Second correction review

Session `20260915-072837-a10997` reviewed
`2b8eddbf586fc8f8f073f37221d221b20adf8fa6` through
`f43585fac5656e4c0e9f54ce1d5005dc437cd46e`, preserving surrounding journal/spec
context. Both sources completed; the finalized 362.44-second round reported
two validated Medium findings (Claude), no High/Critical, and 13 raw Low.
There were no failed agents, degradation or verdict mismatch. Finalize artifact:
`21486ed0ebcc1a06c18b95a90293cbb23c5d0743358f31d0cd9f433924b032d3`.

The successful permission probe was `pika-review-permissions.DmITDS`. An earlier
unused probe was discarded after the parent validator encountered a lean-ctx
allowlist block. The needed parent tooling commands were explicitly allowed
under the human's standing authority; reviewer processes retained the skill's
narrow validator/move grants. No denied command was used as review evidence.

### Second-round dispositions

| Severity | Concern | Disposition |
| --- | --- | --- |
| Medium | Null TaskRun recovery actor strands the domain mirror | Reject unattributed `production_cleanup` recovery failure at the TaskRun SQL boundary; regression leaves the task abandoned and permits a subsequent attributed recovery. |
| Medium | Production admission lacks proposed transition proof | Add frozen `ProductionCommand`, lock the proposed owning-root claim before the request/admission, and preserve the fourth argument for replays and nested cancellation/release checks. |
| Low | Work lock may be acquired after retry-root allocation | Require work order inside linked TaskRun retry allocation itself, before its first root lock. Both relevant task types have regression coverage. |
| Low | Replay proposal may describe discarded ciphertext metadata | Document that proposal describes caller input; dispatch loads the original retained envelope/render. |
| Low | Preparation/hold callbacks remain three-argument | Explicitly document immutable-input closure responsibility and BG-06's compiled input-binding owner; no user-callable callback port exists. |
| Low | Root-claim test alone cannot prove child locking | The existing child-claim parameter independently proves the corrected path. Added Production child-lock ordering assertions too. |
| Low | Additional recovery/pause-gate branch cases | Add delayed-run/new-child recovery and stale prior-run rejection. Restore/non-current lifecycle integration remains BG-06/ADM-05's complete admission matrix. |
| Low | Recovery calling convention insufficiently documented | Document latest failed run, post-expiry fence, recovery actor and derived worker identity. |
| Low | Batch digest could describe only counts/query | Specify that it fingerprints exact batch membership, distinguishing equal-sized batches. |
| Low | Proposed run may lock another root | Filter both journals' proposed locks by owning root and reject missing/foreign IDs before admission. |
| Low | Conditional callback splat readability | Use a straightforward tuple with an optional fourth argument. |
| Low | Repeated render fingerprint computation | Bounded immutable input; defer optional caching to BG-06 rendering integration rather than add a second digest representation here. |

Pika finalized with automatic session cleanup, retaining Claude's report but
removing the Codex raw artifact. Its three below-cutoff Low descriptions are
therefore unavailable for detailed disposition; the finalized severity/source
counts are preserved above. No Codex Medium-or-higher finding was reported.
Subsequent finalizations retain artifacts explicitly to avoid this evidence gap.

The initial post-fix regression passed 140 tests in 83.35 seconds. The independent
merged-base catalog comparison passed in 10.45 seconds: no preexisting object
changed or disappeared. One new function and TaskRun trigger enforce recovery
attribution, bringing totals to 335 functions and 333 triggers. Function hash:
`96f728f76c45b2d067aca396445b6db783c6adf3717b0d5d5e2c3a96dd606c1e`.
The strict fixture was updated after examining these additions. The final
focused journal, shared TaskRun and strict schema suite passed 194 tests in
114.49 seconds; lint, formatting, Markdown and model-state checks pass. This
completes round two. A third completed review/fix round and final-head protected
CI remain required; the complete coverage checkpoint above is not relabeled as
measurement of these later corrections.
