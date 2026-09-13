# Phase 2 full review: round 3

[Acceptance index](stewardship-phase-2-acceptance.md) ·
[Review ledger](stewardship-phase-2-reviews.md) ·
[Round 2](stewardship-phase-2-full-review-2.md)

## Reviewed input and delivery

This is the historical third review, before migration/CI consolidation. Its
implementation commits remain on the preserved backup branch. Later work is not
documentation-only: the [consolidation record](stewardship-phase-2-simplification.md)
tracks additional review and full acceptance required for the replacement
baseline. Results below do not certify that later tree.

Both vendors reviewed `03527493496b9c45bc57fefb45f65cc7dca0e48c` against
`48be3666f0c89cc15586cb67465cd1ba0504203c`. Pika session
`20260913-014009-7631d0` finalized with COMMENT: 39 validated Medium findings,
38 Claude-only and one Codex-only; zero High/Critical findings. All 40 Claude
shards and the single pika-owned Codex reviewer completed successfully. Every
output and completion marker was verified; there were no failed agents, verdict
mismatches, degraded results or salvaged findings. The final artifact SHA-256 is
`5a6601d378f214e53eae3cd4c84d836d3f9b1c39b4b8ce53f96cfe00ecbe200c`.

This completes the third independent review/fix round. All retained corrections
are implemented and both focused regressions and final integrated validation
pass. Routine
triage follows the user's delegated review/fix workflow; no merge is authorized.

## Dispositions

Numbering follows triage order: Claude, then Codex. Duplicates remain visible.
There are 30 corrections, five auto-skips and four evidence-backed pushbacks.

| ID | Disposition | Evidence or remaining correction |
| --- | --- | --- |
| 1 | Corrected | Deployment role list includes mail-dispatch. |
| 2 | Corrected; focused tests pass | Notification denial tests require SQLSTATE 42501. |
| 3 | Corrected; focused tests pass | Tick forgeries use canonical slot keys and exact guard errors. |
| 4 | Corrected; focused tests pass | Intake and installer share every manual-provenance schema, including v8; direct insertion regression added. |
| 5 | Corrected; tests pass | Distinct fake web/background ACK owners prove CLI routing. |
| 6 | Corrected; focused tests pass | Migration 0098 preserves cancelled outcome when queued mail is scrubbed. |
| 7 | Corrected; focused tests pass | Real sealed-intake regression checks matching and stale locked configuration digests. |
| 8 | Corrected; tests pass | Initial, final and ordinary reads share bounded failure actions and retry delays. |
| 9 | Corrected; focused tests pass | Fact-retention race observes actual PostgreSQL lock blocking. |
| 10 | Corrected; focused tests pass | Negative setup-branding routes execute under the actual web login. |
| 11 | Corrected; focused tests pass | Source-compaction race observes actual PostgreSQL lock blocking. |
| 12 | Auto-skipped: duplicate | Same installer provenance defect as 4. |
| 13 | Corrected; focused tests pass | Source migration 0024 shares the frozen alias normalizer; actual EET, US/Eastern and Indianapolis ticks pass. |
| 14 | Corrected; focused tests pass | Initial/final failure settlement binds original task correlation; final rejection regression added. |
| 15 | Corrected; tests pass | Malformed or wrong-tenant change feeds are incomplete source collections, not full-load fallback signals. |
| 16 | Corrected; focused tests pass | Migration 0097 adds generated equality evidence; scheduler payload SELECT returns 42501 while cleanup and real mail hint admission still work. |
| 17 | Corrected; focused tests pass | Migration 0097 forces public handoff RLS; restricted target/owner-policy tests pass. |
| 18 | Corrected; focused tests pass | Direct real-SQL ancestry tests cover exact/rebound and retired content/Ministry additions. |
| 19 | Corrected; focused tests pass | Exchange rebinding must hit invariant 23514 for both permitted writers, never foreign-key 23503. |
| 20 | Corrected; focused tests pass | Lifecycle cleanup/claim race observes actual backend blocking. |
| 21 | Corrected; focused tests pass | Real mail execution replaces or removes the key between provider preflight calls. |
| 22 | Corrected; focused tests pass | Campaign Ministry catalog reuses the shared activity predicate. |
| 23 | Corrected; focused tests pass | Service, disabled UI field and migration 0098 freeze only the source-bound timezone; cancellation still scrubs it. |
| 24 | Corrected; focused tests pass | Migration 0097 limits Slack to its own step's equality metadata and removes all draft-value SELECT authority. |
| 25 | Corrected; tests pass | Independent scheduler producers cannot starve later owners; lost scheduler ownership remains fatal. |
| 26 | Corrected; focused tests pass | Held finalization emits redacted, correlated diagnostics. |
| 27 | Auto-skipped: already handled | Disposal suites already cover expiry, 501 rows, resumed batches, shared payloads and finalization. |
| 28 | Pushed back | Keep explicit-profile 30/10-second staging performance bounds; no reproduced reference-load violation justifies weakening acceptance. |
| 29 | Corrected; focused tests pass | Populated downgrade test targets the exact frozen phase guard without reverting dependent schemas. |
| 30 | Pushed back | Unverifiable deployment state is not cancellation authority; actual selection mismatch already cancels, while original-login expiry and isolated producers bound holds. |
| 31 | Corrected | Confirmation docstring explicitly limits replay to the original signed-preview/live-session window. |
| 32 | Corrected; focused tests pass | Python and reports migration 0008 scope civil-date ordering to one configuration; source/submission watermarks stay monotone. Real timezone/end-date installation tests pass. |
| 33 | Corrected; focused tests pass | Campaign mail requires the exact admitted TaskRun, not merely a shared root. |
| 34 | Pushed back | Exact retained ancestry is necessary for removed/re-added identities; no measured query regression supports replacing it with incomplete predecessor or unrelated-branch checks. |
| 35 | Corrected clarification; focused tests pass | Inconsistent tenants/windows do not prove supersession. Preserve the hold and test denied/malformed scope; remove misleading new-tenant wording. |
| 36 | Auto-skipped: already handled | Finalization intentionally keeps original session deadlines; the page shows them. Automatic renewal here would change the approved session policy. |
| 37 | Auto-skipped: false-positive | Mandatory SQL advisory-lock checks serialize every attempt mutation with cancellation, including direct ORM writes. |
| 38 | Pushed back; tests added | Closing dates and training flags change a stint payload, not its identity. Conflicting duplicate stints fail closed; pure regressions pass. |
| 39 | Auto-skipped: duplicate | Independent Codex finding confirms the same v8 installer provenance gap as 4. |

## Validation and completed acceptance

Focused post-review pure tests pass: 48 credential-runtime/cadence cases,
92 change-feed/retry cases, 129 runtime/corpus/diagnostic cases, and a later
188-case runtime/retry/change-feed run including lost scheduler ownership.
These overlap and must not be summed as distinct coverage. Changed PostgreSQL
regressions pass: the 96-case setup/cadence/exchange/notification run, 54-case
fact/storage/race run and 27-case setup-mail run all pass. The preceding
95 passing cases also cover finalization correlation, branding, intake and
installer provenance. These overlapping focused runs are not a substitute for
the final full coverage run.

Before these corrections, the complete baseline passed 4,210 tests with 2,587
explicit-profile skips and two existing warnings. The associated full
PostgreSQL coverage run passed 2,091 tests in 3,149.09 seconds with 93.09% line
and 81.44% branch coverage. That earlier run did not validate the subsequent
schema/code changes. A later baseline passes 4,297 tests with 2,608 explicit-profile
skips and two existing warnings in 36.15 seconds. Container/isolation/provisioning
checks passed 59 cases, including the separately enabled lifecycle smoke test.
The eight-case operational Compose run passed seven cases but timed out in the
development initial-completion case. An isolated rerun passed, which does not
establish full acceptance. Fixture-only diagnostics now record exception type
and filename/function/line metadata without exception text, locals or source
lines, to diagnose recurrence during the required full rerun.

The first round-3 image run exposed a metadata-only scheduler query that selected
the entire setup mail row after its payload grants were correctly narrowed.
All four complete/abort cases failed at mail admission; the other four passed.
The query now selects only its eight binding/status fields, and a real scheduler
hint regression proves both successful admission and denied payload reads.
The corrected 27-case mail regression passes. All eight operational cases now
pass together in 724.96 seconds on the corrected image; failures were not waived
or timeout budgets increased. The final image also passes all 59 container,
isolation, provisioning and lifecycle-smoke cases in 108.97 seconds. Its image
ID is `sha256:21f79ac17d9c54adb1c5909b8421ca304a378a2d3736d82550557460ae88fd0d`.
The final combined coverage run passes 4,297 baseline cases (2,610 intentional
profile skips, two existing warnings) and all 2,114 PostgreSQL cases, with no
database-profile skips. The PostgreSQL portion took 3,106.18 seconds. Scoped
coverage is 94.16% lines and 85.04% branches. The browser suite passes all 447
cases in 438.68 seconds. These final results validate corrected implementation
`ea2d5cb974bccbe2fc5ec85fadded17a07a8bbeb`; subsequent handoff edits are docs only.
See the [acceptance index](stewardship-phase-2-acceptance.md) for the final
demonstration mapping and remaining CI/human merge obligations.
