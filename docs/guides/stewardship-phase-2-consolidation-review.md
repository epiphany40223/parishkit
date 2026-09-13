# Phase 2 consolidation review

[Review ledger](stewardship-phase-2-reviews.md) ·
[Consolidation](stewardship-phase-2-simplification.md)

## Supplemental round 1

Pika session `20260913-065813-dab077` reviewed commit `8a2d8e0` against
`origin/main`. All 48 generated Claude shards and the Pika-owned Codex reviewer
completed, without degradation, failed agents, verdict mismatch or salvage.
Finalization retained 37 findings from 292 raw findings: 36 Claude-only and one
Codex-only. Three were High (the same Docker packaging fault); the remaining
34 were Medium. This was REQUEST_CHANGES, not approval.

Identifiers C1–C36 follow the finalized Claude array order; D1 is the Codex
finding. Decisions below follow the human-delegated phase delivery policy.

| Findings | Disposition | Evidence or correction |
| --- | --- | --- |
| C1, C2, D1 | Fixed, duplicate root cause | Both Docker context allowlists now include exactly the four baseline SQL assets. Synthetic no-bind build-context tests reproduce the omission; rebuilt operational bootstrap tests pass. |
| C3, C4, C9, C11, C14, C15, C16 | Fixed | Task, acceptance, phase and review records explicitly label old SHAs as pre-consolidation evidence on the preserved backup. Final-head evidence is tracked separately here and in the consolidation record. |
| C5 | Fixed | Current policy security-event UPDATE/DELETE append-only tests retained independently of retired historical migration tests. |
| C6 | Fixed | Source fallback test changes a CHECK-valid correlation ID and requires the append-only rejection; DELETE and preservation are also checked. |
| C7 | Fixed | Release coverage wiring uses an exact step contract, preventing shell bypasses from passing a substring assertion. |
| C8, C17 | Fixed, duplicate | Baseline coverage runs only in shard one; receipts and aggregation require that exact baseline assignment. |
| C10 | Fixed | Baseline and database subprocesses share one 20-minute deadline; remaining-budget and exhaustion regressions cover both paths. |
| C12 | Fixed | Failed independent collection prints bounded credential-free collector diagnostics before failing. |
| C13 | Fixed | Runtime CI explicitly runs the real Valkey ACL/Kombu tests with skip rejection; 13 local tests passed in 9.39 seconds. |
| C18 | Rejected, already handled | Initial and final setup loaders have different credential, configuration-scope and activation-fence admission contracts. Shared transport, load and staging primitives already exist; merging the orchestration is optional refactoring, not a demonstrated defect. |
| C19, C22 | Rejected, superseded scope | Historical downgrade support/tests contradict the explicit pre-production policy. The baseline remains fresh-install only; backwards manipulation of migration history is unsupported. |
| C20 | Fixed | Restored historical content requires the precise safe maintenance redirect and no-store response, not merely a non-200 status. |
| C21 | Fixed | Mail relay mutation tests require ownership/identity guard errors rather than any database error, excluding accidental foreign-key failures. |
| C23, C30, C34 | Fixed, duplicate root cause | Current Django field/key/constraint/index declarations are compared with installed SQL. Negative declaration-only field, check and index changes prove the regression detects drift. |
| C24 | Fixed | Schema inventory now includes full function/relation ACLs and owners, sequence parameters and live column order. The richer golden fixture was captured only after another successful reference/baseline equivalence comparison. |
| C25 | Fixed | Presence authorization test verifies applied configuration, a distinct subject and a positive authorized Admin response. |
| C26 | Fixed | Both shard execution and independent collection reject module-level skips; real pytest probes retain surviving tests to exclude empty-collection false positives. |
| C27 | Rejected, already handled | Operational role admission rejects TEMP authority, and runtime roles cannot create public schema objects. The proposed temporary-table shadowing requires authority deliberately absent from supported callers; privileged schema-owner mutation is outside this runtime boundary. |
| C28 | Fixed | The enqueue-observation assertion records calls and requires a nonempty all-true result; it no longer claims to test worker claiming. |
| C29 | Fixed | Configuration-installer excess-grant tests now include whole-table and session-ID column SELECT. |
| C31 | Fixed | The same campaign creation proposal must apply when the forbidden historical-content edit is removed. |
| C32 | Rejected, superseded scope | Detecting or admitting previously installed, edited development baselines is upgrade compatibility. Existing development databases are preserved and unsupported; fresh installations are the contract. |
| C33 | Fixed | Session-level database setup verifies the three seed sentinels before per-test fixtures can repair missing rows. |
| C35 | Fixed | Package metadata requires urllib3 2.7.0 or newer, matching the already-pinned runtime and closing the unpinned-install gap. |
| C36 | Fixed documentation; retry proposal rejected | Lease renewal intentionally fails closed on uncertainty and uses bounded task recovery. The pulse constant now documents why a speculative second renewal is not attempted. |

The C35 minimum also includes the subsequent upstream streaming correction
documented in [urllib3's advisory](https://github.com/urllib3/urllib3/security/advisories/GHSA-mf9v-mfxr-j63j).
No provider credentials, upgrades, database deletion or deployment were involved.

## Validation and next gate

- Model/schema checks and negative drift probes: five passed in 8.30 seconds.
- Schema and campaign-clone regression group: 15 passed in 21.01 seconds.
- CI evidence/build regression group before the last two build assertions:
  78 passed in 2.81 seconds.
- Rebuilt operational Compose/provisioning group: 12 passed in 751.32 seconds.
- Real Valkey ACL/Kombu group: 13 passed in 9.39 seconds.

Full final-head CI and another independent review remain required. The prior
three complete Phase 2 reviews still count, but this round's raw High findings
require a subsequent completed round satisfying the exit criterion. Do not merge
on the strength of the historical acceptance record.

## Supplemental attempt 2: incomplete dual-source review

Pika session `20260913-082238-edd5f7` reviewed `4aee4cd` against `48be3666`.
All 48 prescribed Claude shards delivered; sixteen network-failed entries were
retried with the identical prompts and exact artifact permissions, after a new
successful permission preflight. Pika retained 22 Medium findings from 268 raw
findings, with no High/Critical findings. There was no verdict mismatch or
salvage queue. The Pika-owned Codex process did not complete:

> codex-reviewer: stalled (no new output within the idle window) — treated as timed out

This is a Claude-only result, **not** an approval or a completed review round.
Its findings still receive evidence-based triage. R1–R22 below follow the
finalized Claude array order; original artifacts remain outside the repository.

| Findings | Disposition | Evidence or correction |
| --- | --- | --- |
| R1 | Rejected, speculative maintenance risk | The installed trigger explicitly invokes `stewardship_secret_state_v2`; the unused v1 trigger function cannot be called as an ordinary function or attached by restricted runtime roles. The catalog fingerprint covers both function definitions and trigger targets, so the suggested future miswiring fails current-schema verification. Renaming/removing inert catalog objects is optional cleanup, not a demonstrated credential-lifecycle bypass. No historical upgrade support is promised. |
| R2 | Corrected | Presence removal first proves a successful heartbeat and visible session, then independently establishes visibility before revoking eligibility. Both rejection paths are asserted. |
| R3, R4 | Corrected | Full-review rounds 1 and 2 now explicitly label their SHAs, numbered migrations, upgrade statements and pending-work text as historical backup-branch evidence. |
| R5 | Already corrected | The top-level task record now distinguishes pre-consolidation evidence, passing CI and the still-open supplemental review gate. |
| R6, R11, R12 | Corrected; duplicate root cause | The required Compose-matrix contract invokes real pytest collection and compares its exact node set with the runner matrix. New parameters or a second test cannot silently disappear from PR CI. |
| R7 | Corrected | Forged canonical-window text now carries its own valid SHA-256, isolating current/canonical scope refusal from the separate digest check. |
| R8 | Corrected shared scope; separate admission retained | Staging, ordinary selection and setup preparation use the same public `authentication_scope` helper and invalid-tenant tests. Initial setup intentionally requires `awaiting_ack` plus original setup readiness, while ordinary selection requires the latest `applied` receipt and no pending replacement; these different admission owners remain separate. |
| R9 | Corrected | The evidence digest additionally covers deployment files, script/tool Python, root installer/build inputs, and documentation/acceptance inputs consumed by baseline tests. Mutation probes cover each previously omitted category. |
| R10 | Corrected | Model checks now require declared field/ForeignKey index coverage and compare server-parsed declared database defaults. Negative field-index/default drift probes accompany the existing field/constraint/named-index probes. Explicit NULL defaults use a left-joined catalog query because PostgreSQL may omit their `pg_attrdef` row. |
| R13 | Corrected | Missing-checkbox confirmation responses retain validation errors but issue a fresh hidden token for the displayed preview. A real CSRF-protected invalid-then-explicit-submit regression verifies recovery. |
| R14 | Corrected | Logo-pinning tests first require a failed receipt, restored base YAML and exactly one abort journal before asserting cleanup remains pinned. |
| R15 | Corrected | Fresh-install no-race and current startup-refusal requirements now precede the deferred production-upgrade section. |
| R16 | Corrected | Branding caches its verified presentation projection on the current request; it runs before Admin chrome so both render the same projection. Authorization never consumes this display cache. |
| R17 | Corrected | A dedicated read-only in-flight observer permits graceful drain while retaining SQL ownership, admission and renewal-failure checks. Both setup and campaign sample-mail pulses use it; new external work still uses stop-sensitive admission. |
| R18 | Rejected, already handled | `schema_inventory` fingerprints `pg_get_functiondef` for every stewardship function, including each function's `SET search_path`. The fresh-schema golden assertion therefore already detects loss of the three named pins without restoring obsolete downgrade tests. |
| R19 | Clarified; lifetime extension rejected | Explicit consent remains bounded by the signed preview's 15-minute lifetime. The retry docstring now states that boundary; an expired replay leaves the original journal intact and accessible through passive status, without creating another send. |
| R20 | Corrected under delegated Admin policy | Any currently authorized, freshly authenticated Admin may select the latest acknowledged receipt using their own exact signed preview. Another Admin's signed intent remains unusable; Integration settings exposes the recovery link without exposing private credential values. |
| R21 | Corrected | Saved Ministry/fund IDs missing from the current catalog remain labeled unavailable selections. No names are borrowed from another tenant, and unrelated structural edits preserve the selections. |
| R22 | Corrected | Missing runtime/campaign state at source-refresh admission is explicitly classified as `SourceScopeChanged`, retaining held/replan handling instead of an unknown read failure. |

Focused validation and a new completed correction-scoped dual-source review are
required before these corrections close the gate. The human-approved delivery
cycle now permits that focused scope; the preceding **completed** review baseline
is `8a2d8e0`, not this degraded attempt. Preserve exact tree identity across the
squashed PR and any review-only snapshot.

## Supplemental round 2: completed correction review

Pika session `20260913-114439-253ad9` completed both prescribed vendors without
failed agents, degradation, verdict mismatch or salvage. It retained four
Medium findings from twelve raw findings; eight Low findings were below the
configured floor. There were no High/Critical findings. The final artifact's
SHA-256 is `1c5928304f0689290156097ca1484ded04451b67d532c463622c096fc3efc92a`.

The review covered the actual 72-file correction delta since the last completed
review, `8a2d8e0dab31ae379333b81b679bf6c8f24f1b94`. Because the implementation
history had been regrouped, review-only snapshot `7dbefa8f8f732e22d25e5fde73a080716e364ee2`
retained that parent. Its tree `cae67d333f994544abcfe6aa295f62b432c6e906` was
verified identical to PR head `0bd831a4a1813b080a573896d6c7b48bb6a39bce`.
The snapshot branch is not implementation history and must not be merged.

| Findings | Disposition | Correction and regression evidence |
| --- | --- | --- |
| S-C1 | Corrected | Index signatures now include ordered operator-class names, collations and `indoption` flags, with opclass, descending-order and collation drift probes. |
| S-C2 | Corrected | Campaign sample delivery performs stop-sensitive admission immediately before its submitting marker. A real restricted-role regression stops during credential reading and proves no provider call, no submitted marker, failed Task disposition and scheduler cancellation. The existing post-marker graceful-drain regression still passes. |
| S-D1 | Corrected | Expected DDL is built independently from current Django models, without inheriting installed defaults or generated expressions. Full column/default/generated-expression comparison is bidirectional; negative probes cover default removal and a changed generated expression. |
| S-D2 | Corrected | Full expected and installed constraint/index multisets are compared bidirectionally, including implicit field keys, foreign keys and indexes. Explicit SQL-only logging checks are narrowly listed; their definitions and all constraint triggers remain protected by the independent golden inventory. Removal probes cover unique, foreign-key, field-index and named constraint/index declarations. |

The strengthened independent-Admin credential-selection test additionally
disables the original Admin, rejects their signed intent under the replacement
Admin, and executes the replacement Admin's own intent through real installer
activation. This confirms recovery reaches applied configuration, not merely a
successful HTTP response.

The three original complete Phase 2 rounds and both complete supplemental
rounds count. The degraded attempt does not. Under the controlling delivery
cycle, these corrections and their passing tests complete this round; no
finding-free rerun is required. Final-head CI and normal protected merge remain
mandatory, with their final SHA and run recorded in PR #22's handoff.

Post-correction local validation: all 57 schema, campaign-mail and credential
selection PostgreSQL cases pass in 105.53 seconds. The unchanged-model check
covers every managed stewardship model; all fifteen negative drift probes pass.
The credential-free baseline passes 4,357 tests in 39.87 seconds, with 2,591
explicit opt-in profile skips and two existing warnings. Ruff, formatting,
Markdown and `makemigrations --check --dry-run` pass. Before these final narrow
corrections, the broader 159-case PostgreSQL group and all 447 browser tests
passed; CI on `0bd831a` also passed every check. That earlier green head is not
substituted for final-head CI.
