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
