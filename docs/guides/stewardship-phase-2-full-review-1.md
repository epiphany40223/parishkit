# Phase 2 full review: round 1

[Phase acceptance](stewardship-phase-2-acceptance.md) ·
[Review ledger](stewardship-phase-2-reviews.md)

Reviewed commit: `0b9677f4825d2dac51a1639626a79f6a5a595bab`.
Base: `48be3666f0c89cc15586cb67465cd1ba0504203c`.
Pika session: `20260912-231556-6bf86a`.

Both models completed without degradation, failed agents, verdict mismatch or
salvage. Pika assigned 39 Claude shards and one Codex reviewer. All Claude
processes exited successfully with validated output and completion markers.
The round produced 387 raw findings: Claude supplied 43 Medium and 342 Low;
Codex supplied two Medium. The pipeline retained 36 Medium findings, with no
High or Critical findings. The 351 below-cutoff findings are not accepted
remediation items. This is the first full-phase round, not a relabeling of an
earlier component review. Its retained findings have been corrected and targeted regressions verified.
Full integrated coverage and the next two independent rounds remain required.

The numbers below follow the triage ordering: Claude findings first, then Codex.
The source artifact retains each complete finding. “Fixed” means the correction has targeted regression evidence; it does not
close the full integrated coverage or remaining review gates.

| ID | Subject | Current disposition and evidence |
| --- | --- | --- |
| 1 | Stale task evidence | Fixed: ADM-02/03/04 have one current summary each, with historical detail linked to the ledger. Overall/milestone/acceptance status distinguishes implemented paths from incomplete validation and review gates. |
| 2 | Setup-exchange mutation matrix | Fixed: actual target and schema owner are tested before and after reply; targeted PostgreSQL passes. |
| 3 | Real mismatched setup base | Fixed: the actual frozen attempt is bound to another retained prepared candidate as its wrong base; PostgreSQL proves SQLSTATE 23514 rather than a dangling foreign key. |
| 4 | Dashboard audit success before rendering | Fixed: success is recorded after rendering and final authorization; real revoke/render PostgreSQL regression passes. |
| 5 | Passive presence audit amplification | Fixed: count polling creates no roster-disclosure event; named roster views remain audited; PostgreSQL passes. |
| 6 | Consumer Valkey RPUSH | Fixed: actual expired-message restore reproduced NOPERM, then passes with consumer-only command authority; 23 ACL/real-container cases pass. |
| 7 | SQL denial tests accept integrity errors | Fixed: named private-column reads and SQLSTATE 42501 assertions prove denial rather than accidental SQL/shape errors; PostgreSQL passes. |
| 8 | Deterministic body failures retried as outages | Fixed: a closed nontransient helper result preserves body-contract failure through client/task classification; 98 transport/classifier cases pass. |
| 9 | Purge fact conflated with work hold | Fixed: the absent Phase 2 purge fact is explicitly separate from the work hold; BG-11/ADM-10 retain the real future query. Campaign editor regression passes. |
| 10 | Unbounded setup-cleanup recovery | Fixed: five-attempt bounded recovery/backoff preserves actual drainage; all ten focused cases pass. |
| 11 | Empty delta skips global group changes | Fixed: every delta fetches/compares global Family groups, even without household indications; 15 pure cases and both owning PostgreSQL empty-delta paths pass. |
| 12 | Runtime guide says background services inert | Fixed: distinguish preparation from the services started in step 7. |
| 13 | Cancel races campaign-mail start | Fixed: cancellation is reread and settled under the same claim/work locks; the actual scheduler-race PostgreSQL case passes. |
| 14 | Expired clone seed appears malformed | Fixed: an expired clone identity returns a stale/reload conflict, not malformed input; PostgreSQL passes. |
| 15 | Local provider-check errors reject candidate | Fixed: local failures become unavailable, never invalid; fatal ownership/drain signals escape; 71 provider cases and owning PostgreSQL cases pass. |
| 16 | Setup mail deadline boundary | Fixed: savepoint settlement retries a deadline-raced definitive result once as unknown without bypassing SQL guards; focused helper and real PostgreSQL crossing tests pass. |
| 17 | Setup Slack deadline boundary | Fixed: the same guarded settlement helper is exercised by an actual Slack deadline-crossing PostgreSQL case. |
| 18 | Definitive Google token refusal is unavailable | Fixed: closed 400/401 token refusals classify invalid; unknown/outage responses stay unavailable; installed Google parsing covered in 71 provider cases. |
| 19 | Source-pin release lacks parent binding | Fixed: release requires exact parent kind/UUID and admission receives the actual pin; wrong-parent, idempotence and compaction/race PostgreSQL cases pass. |
| 20 | Setup source completion ignores update count | Fixed: a missed attempt update rolls back Task completion and source release; post-update fault injection verifies all three durable states and the execution marker. |
| 21 | Abort journal can retain impossible owner/reason | Rejected, already handled: immutable intent admission in migration 0064 binds owner, request actor and base; abort insertion independently checks exact expiry reason. Recovery cannot encounter the proposed mismatch from an admitted journal. |
| 22 | Superseded incomplete fact generation | Rejected, false positive: newer inputs do not make an older exact-input build unrecoverable. The data spec protects building/recoverable failed generations; reports/recovery.py explicitly resumes both with fenced ownership. The proposed dead-builder/newer-ready shortcut could delete recoverable work. No irrevocable-disposal transition is represented by these states. |
| 23 | Setup relay starves ordinary credential rollback | Fixed: file reconciliation runs first each bounded pass; six relay/staging-failure variants prove it still runs; 28 process cases pass. |
| 24 | Broad PermissionError classification | Fixed: named source scope/credential changes classify expected failures; arbitrary filesystem/PermissionError failures remain unknown. The real changing-scope test now settles its actual typed exception. Pure and PostgreSQL suites pass. |
| 25 | Bad finalization selection starves scheduler | Fixed: incoherent selected YAML is ineligible for the tick and does not abort scheduling; a real prepared receipt/actual scheduler regression passes. |
| 26 | Finalization original-session deadline | Fixed: original idle/absolute ownership remains unchanged, its recovery limit is documented, and final progress displays exact deadlines; original-session PostgreSQL checks pass. |
| 27 | Campaign-mail deadline boundary | Fixed: campaign-mail crossing settles unknown and terminates the Task immediately; real mail-dispatch PostgreSQL case passes. |
| 28 | Restart requires undisclosed new login | Rejected, already handled: setup.html already explains re-login both before cancellation and on the expired-attempt screen, with a Sign out action. |
| 29 | Unrelated configuration holds branding cleanup | Fixed: one invoker-rights predicate scopes pending pins to selected assets and is shared by SQL guards/paged selection. GIN lookup avoids unrelated request history. Actual scheduler/worker and direct-SQL regressions pass. |
| 30 | Setup expiry diverges from Admin policy | Rejected, false positive: domain rules cannot grant Administrator; authenticated Google identities are normalized before persistence. Setup is restricted to exact bootstrap Admin rules and SQL independently enforces that original identity. |
| 31 | Changed connection limits need upgrade guidance | Fixed: runtime guide identifies the old-limit mismatch, forbids first-deployment repair/adoption, and describes the owned offline upgrade checks required when OPS-05 supplies that workflow. |
| 32 | Out-of-period pledge amounts block loading | Fixed: identity/tenant/date still establish scope; money and household validation applies only to selected periods. Unknown dates remain blocking because exclusion cannot be proven. Giving/process combined 60 cases pass. |
| 33 | Other task's source lease prevents failure settlement | Fixed: a task must release its own source lease, not another task's; 42 pure final-task/assembly cases cover none/self/other ownership, with successful compiled PostgreSQL execution. |
| 34 | Duplicate source-lease settlement finding | Rejected as duplicate of 33. |
| 35 | Final setup permits no initial schedule | Fixed: final readiness requires an initial invitation even after an accepted sample; empty/digest-only negative PostgreSQL cases pass. Draft editing remains permissive. |
| 36 | Missing mandatory page content | Rejected, false positive: the owner's original requirements allow every Admin-authored block to be empty or removed. Page structure is application-owned; absence of authored prose does not remove required fields/pages. Email-template references remain validated separately. |

## Additional validation findings

The exact host/container collection check exposed a random UUID embedded in a
parameterized test name. An explicit stable case ID preserves random test data
while making collection portable. The complete parity/lifecycle suite then
passes all 30 cases in 71.10 seconds, using this disposable Docker Desktop
fixture's native PostgreSQL volume option.

The full PostgreSQL coverage run started before this correction pass finished
with 2,025 passed and 12 failed in 2,970.22 seconds. Its combined report shows
91% coverage, but the failures mean it is not a passing acceptance gate.
Corrections address recreated-table fixture grants, flushing deferred events
before restoring aged-fixture triggers, explicit private-payload access checks,
and the additional global-group page required by empty deltas. The changed-module runs finished with 319 passed/one new fixture failure and
103 passed/one test-import failure. Both newly introduced test errors were
corrected; the final focused follow-up passes all 22 cases in 17.23 seconds,
including source failure/transport classification. No test assertion or
production ownership guard was disabled to make these corrections pass.

The baseline passes 4,161 cases with 2,558 explicit opt-in profile skips and two
existing warnings. The combined focused pure correction suite passes 320 cases.
Ruff, formatting, Markdown and migration-drift checks pass. A fresh combined
baseline/PostgreSQL coverage run is in progress on the corrected source; browser
and operational Compose regressions also repeat before phase exit.

Disposition total: 30 corrected findings and six evidence-backed rejections
(21, 22, 28, 30, 34 and 36). No retained Medium finding is intentionally deferred.
Rounds 2 and 3 remain mandatory before PR readiness.
