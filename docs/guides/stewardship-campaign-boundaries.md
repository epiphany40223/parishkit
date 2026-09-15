# Campaign boundary processing

[Coordinating tasks](../tasks/stewardship/overall.md#phase-4-production-scheduling-and-delivery) ·
[BG-02 plan](../plans/stewardship/background-processing.md#bg-02-campaign-boundary-occurrences) ·
[Boundary contract](../specs/stewardship/background-processing/spec.md#campaign-lifecycle-boundaries)

## Scope

Branch `pr/stewardship-campaign-boundaries` starts from verified PR #31 merge
`f4e000c5b3f7024e47c7f6d3dbdd30c6cd4976e1` on refreshed `origin/main`, after
[complete protected delivery](stewardship-delivery-reviews.md#protected-delivery).
Deliver BG-02's Phase 4 start/close behavior as one coherent scheduler/worker
increment: durable occurrence production, compiled admission and execution,
ordered overdue recovery, end-date replacement and their real database/runtime
tests. Individual records or helpers are internal checkpoints, not PRs.

BG-02.03 retains shared restore/reopen token preparation in Phase 6. BG-03/04
retain actual Production cleanup and schedule fulfillment; BG-06 retains mail
dispatch and ADM-05 retains activation. Do not expose those unfinished owners
or weaken existing held-work checks to make this slice runnable. Full affected
mail reconciliation remains BG-04; boundary integration must preserve its
existing conservative admission until that owner is ready. Gate 3 remains
after complete Phase 4/5 integration.

## Internal checkpoints

1. Connect the existing ordered lifecycle journal to bounded durable occurrence
   production and the compiled scheduler/worker registry. Use stable identities,
   authoritative database time, current campaign/mode/gate admission and the
   existing TaskRun lease/recovery substrate. Broker hints remain non-authoritative.
2. Complete transactional start/close execution, exact proof and audit metadata.
   Close-first delivery applies any overdue start in the same transaction, with
   no externally visible active gap and no out-of-interval Family access.
3. Reconcile end-date changes with safe queued-work replacement and current
   worker exclusion. Closing first leaves the change to the guarded reopen
   owner. Preserve complete schedule reconciliation and provider-uncertainty
   blockers; do not introduce a partial date-only escape path.
4. Prove duplicate/restart/DST/exact-boundary behavior, missing/stale/foreign
   claims, crash-before/after domain commit, delayed hints, held gates and real
   races. Verify restricted SQL roles, metadata-only scheduler behavior and
   runtime registry wiring without provider credentials or live external writes.
5. Audit any fresh-install SQL/model deltas without retained-database deletion.
   Run repository and integration validation; complete three dual-source
   review/fix rounds and final-head CI before protected delivery.

## Evidence

Planning and implementation have started; no BG-02 task is newly complete.
Existing boundary/storage race tests are prior substrate evidence, not proof
of the new compiled scheduler and worker integration.

The first internal checkpoint implements bounded occurrence/root production and
the compiled metadata/execution/recovery handler. Seventeen PostgreSQL tests
passed in 22.88 seconds, covering exact dates, repeated scans/restart, held
configuration, close-first execution, late-denial rollback, stale/unbound task
views, and recovery before versus after the domain commit. Ruff passes for the
new modules and tests. No runtime registry or additional SQL authority is
enabled yet; restricted-role integration, replacement, audit/lag evidence,
broader races and the complete three-round review/CI cycle remain required.

The restricted-role checkpoint passes 38 PostgreSQL tests in 30.97 seconds.
Actual scheduler/worker logins produce and execute ordered boundaries, including
a populated Family login whose session is revoked and link destroyed at close.
Worker SQL cannot read token ciphertext or session keys; populated premature
lifecycle/scrub attempts fail for both installed and custom worker role names.
Empty-table updates are explicitly distinguished from populated row authority.

An independent fresh database loaded from exact merged PR #31 was compared with
the working fresh-install schema. Its original fingerprint matched before the
comparison. Only three boundary proof functions, four boundary guards and the
narrow `stewardship_setup_completion_write_v1` exception differ; all relations,
columns, constraints, indexes and policies are unchanged. The audited fixture
now contains 338 functions and 337 triggers. No retained database was deleted or
upgraded. This is an internal checkpoint, not completed BG-02 acceptance.

## Runtime and timing checkpoint

The scheduler now registers metadata-only boundary admission and scans the
boundary producer independently of the other producers. The general worker
registers the compiled executor; mail dispatch does not. Every ordinary pass
retains current YAML/SQL authority checks. Runtime assembly, producer isolation,
grant contracts and context-validation tests pass (199 tests in 1.12 seconds).

Terminal boundary changes append a closed `boundary` audit context containing
the occurrence identity, kind, intended/actual UTC Unix microseconds, lag in
microseconds, previous/new state and the event's correlation ID. Skipped and
successful outcomes both retain this context. No parishioner values are allowed
in this schema. Scheduler lag exceeding the existing process-health threshold
of 90 seconds records one durable WARNING per occurrence; its task context
contains the root ID and lag seconds in `count`. The insert-only warning survives
duplicate scans/restarts without granting log-payload reads. BG-10 still owns
notification delivery and escalation.

End-edit admission now checks both execution-bound task IDs and immutable root
allocation keys, rejecting running/abandoned close work even before its worker
binds the occurrence. Queued work remains replaceable. This does not loosen
affected-mail reconciliation or permit edits after the original closing instant.

The complete focused boundary set passes 49 PostgreSQL tests in 38.65 seconds:
production/execution, exact grants, populated credential cleanup, typed timing
audit, lag threshold/deduplication, pre-binding end-edit exclusion, concurrent
workers, and spring/fall DST intervals. Fifty-four lifecycle, work-admission,
exceptional-end and strict schema/model regressions pass in 32.67 seconds.
Ruff lint/format, Markdown lint of this guide and `makemigrations --check
--dry-run` pass. The independent PR #31 catalog comparison was repeated: beyond
the earlier worker-proof changes, only boundary audit, safe-context validation,
end-edit admission, and the two closed audit/operational constraints changed.
No table, column, index or policy was added or changed.

The full credential-free baseline passes: 5,539 tests, 3,477 opt-in/profile
skips, and two pre-existing client-library deprecation warnings in 55.55 seconds.
This is not a claim that skipped database, browser or Compose suites passed.

An additional setup-heavy regression invocation was interrupted after 15 passed
tests in 525.91 seconds, while waiting in a deliberate threading wait. It is
not complete validation or a demonstrated deadlock. Remaining setup regressions
must run in bounded diagnostic groups before final acceptance.

## Approved execution-revision contract

The human approved support for A → B → A with immutable replacement history and
a fresh execution revision for the reused date. The normative data/background
specifications now define monotonically increasing per-campaign/kind execution
revisions, with distinct occurrence IDs and task roots. This is an expressly
approved change to the previously reviewed date-only identity, not permission
to rewrite terminal history or add pre-production upgrade compatibility.

The implementation now atomically retires an obsolete close occurrence and
allocates its successor during the end-date configuration transaction. Both
the producer and executor select the latest execution revision; stale hints
acknowledge cancellation without reviving history. SQL independently enforces
sequential revisions, immutable identity, no competing pending predecessor and
current-revision worker proof. A close that wins before publication of an
earlier reviewed end edit leaves the campaign closed and requires reopening.

The expanded boundary/exceptional-date/regression set passed 77 PostgreSQL tests
in 59.34 seconds; the final six end-date tests passed in 14.51 seconds. After
the independent schema audit, the strict fresh-schema/model and end-date set
passed 23 tests in 25.21 seconds. The latest credential-free baseline passed
5,539 tests with 3,480 opt-in/profile skips and two pre-existing warnings in
56.14 seconds. The schema audit confirmed only the approved execution-revision
column, revision constraints/identity index and associated guard changes beyond
the already recorded boundary deltas. No retained database was upgraded/deleted.

Complete remaining runtime/regression validation and three dual-source review
rounds before PR delivery. No new BG-02 checkbox is complete and this branch
has no PR yet.

## Review round 1

Full branch review `20260915-112014-41e06d` examined base
`f4e000c5b3f7024e47c7f6d3dbdd30c6cd4976e1` through
`d8ae7dcea79f0ccaa2bd9b66e84b03eebe4147fe`, tree
`04f889b09821d478df12dcccf7b3a8801633d5d0`. Both reviewers completed
successfully; Claude covered all 39 manifest files. Finalization retained its
artifacts and reported REQUEST_CHANGES, without degradation or verdict mismatch:
15 raw findings (two High, four Medium, nine Low), six above the cutoff.

The restored lean-ctx configuration and expressly approved `claude` command
entry remain outside the repository. The exact validator/move permission probe
passed before either reviewer launched; no broad permission bypass was used.

| Source/severity | Disposition and regression evidence |
| --- | --- |
| Claude High: configuration-installer grants | Fixed. Grant successor boundary INSERT and write-only audit-context INSERT, without private reads. Actual exact config-installer login now performs end edits and A → B → A. Full reopen activation remains ADM-06/Phase 6. |
| Claude Medium: end-edit role and SQL coverage | Fixed with the preceding grant correction and independent SQL rejection of running/abandoned unbound close roots while the Python preflight is deliberately permissive. |
| Claude Medium: custom worker runtime transition | Fixed. Privilege-based runtime journal guard rejects Return to Testing for both installed and custom worker names, including a populated archived, otherwise-admissible campaign. |
| Codex High: post-claim authority mismatch | Fixed. Invoke bound handler effect admission under installer/work/task locks before the first domain effect; retain installation serialization through the ordered transaction. A newly selected, unactivated YAML version blocks an already claimed boundary. |
| Codex Medium: unrestricted boundary metadata | Fixed. Exact allowed boundary deltas, empty transition reasons and absent unused fields are enforced for installed/custom worker names. Raw writes with valid live claims cannot alter pending reasons or inject unrelated transition fields. |
| Codex Medium: population effect claim budget | Fixed with bounded renewal, not a frozen clock. After lock waits and fresh admission, each due boundary renews its still-live claim to the existing 300-second substrate maximum. All SQL wall-clock expiry checks remain. A delayed-effect test exceeds its original short claim and verifies the renewed budget; overdue start-first and close-first hints both work. |

The nine raw Low findings remain visible, with these dispositions:

| Raw Claude Low finding | Disposition |
| --- | --- |
| Purge-only states absent from boundary context | Deferred to BG-11/Phase 6 integration. Current production/execution admission excludes purge states and unreleased gates; this PR does not implement purge-time terminalization. |
| Other-campaign work gate yields SQL rejection rather than Python hold | Deferred to BG-11 integration of historical-campaign purge alongside a current campaign. Existing global SQL exclusion remains intact; it is not bypassed here. |
| Repeated row-level close proof | Retained deliberately for independent SQL fencing; the accepted claim-budget correction and population measurement address execution latency without caller-set proof flags. |
| Installer freshness constant also controls boundary lag | Low coupling cleanup deferred to BG-10's shared operational alert policy. Current threshold remains 90 seconds and includes pending worker backlog. |
| New trigger names/header style | Low schema-comment/naming cleanup deferred; guards are functional and included in the independent catalog audit. |
| Redundant nonnegative revision constraint/default | Retained for Django model/schema parity; the stronger positive constraint and sequential-allocation guard enforce the actual contract. |
| Operator replacement actor kind | Deferred to Phase 6 operator exceptional-work integration; normal end edits remain explicitly portal-user authored. No operator exceptional workflow is enabled here. |
| Extra recovery/held branch coverage | Low coverage extension retained for subsequent boundary/gate validation; existing before/after-commit recovery and restore-hold regressions pass. No missing branch is represented as tested. |
| Imported test helpers/fixtures | Retained established suite convention; refactoring is optional and not required for boundary behavior. |

At the reviewed head, all eight operational Compose cases passed in 793.05
seconds; seven setup-finalization/disposal cases passed in 526.24 seconds;
14 source-worker/setup-disposal cases passed in 79.03 seconds; and the complete
60-case boundary set passed in 47.96 seconds. These head-specific results do not
certify later corrections. The correction set has passed 38 focused PostgreSQL
tests in 40.69 seconds and 54 strict-schema/remaining-boundary regressions in
28.81 seconds. The independent reference-schema comparison passes with only
the intended new boundary/runtime journal guards and proof changes; other
catalog families are unchanged from the preceding audited baseline.

Post-fix validation also passes 14 restricted source-worker/setup-disposal
regressions in 74.97 seconds, 5,540 credential-free baseline tests in 54.68
seconds (3,495 opt-in/profile skips, two pre-existing warnings), Ruff and Markdown
lint. A separate disposable 5,000-Family benchmark exercised real token creation,
activation and exact-role ordered closing; the closing transaction and task
acknowledgment took 0.742 seconds, and all 5,000 bearer rows were destroyed.
Fixture creation plus benchmark took 23.70 seconds. The benchmark contains only
synthetic data and remains outside the repository, so normal CI does not repeat
large credential fixtures unnecessarily.

Round 1 is complete with all six accepted findings fixed. PR delivery still
requires at least three completed dual-source rounds and final-head CI.

## Review round 2

Correction review `20260915-114610-784267` examined
`d8ae7dcea79f0ccaa2bd9b66e84b03eebe4147fe` through
`f92f62cb4820194918ef5ae77298d7e73680f599`, tree
`4f43e876a87afaf33d902ddfbfd3562028e29b52`. The exact permission probe passed;
both reviewers completed, and Claude covered all ten correction manifest files.
Finalization retained artifacts without degradation or mismatch. REQUEST_CHANGES
contained one agreed High and one Claude Medium, from nine raw findings (two
corroborating High, one Medium, six Low).

| Finding | Disposition |
| --- | --- |
| Agreed High: skip timestamp came from a prior statement | Fixed. `not_applicable` completion now evaluates the database domain-clock function inside its UPDATE, matching the SQL guard. Four real-statement-clock cases cover both hint orders and exact/custom worker roles through bound YAML/SQL authority. Only fixture setup/activation move into the past; execution uses the unmodified production clock. |
| Claude Medium: overly broad rejection assertions | Fixed. Regressions match specific SQLSTATE/messages and include successful positive controls under otherwise-identical conditions. Additional terminal skip tests cover reason, completion time and unrelated transition identifiers. |
| Claude Low: skipped transition identifier could corrupt audit | Rejected as already handled. `campaign_boundary_result` requires a skipped row's transition to be NULL. Both role variants now assert that exact CHECK rejection, followed by a successful valid skip. |
| Claude Low: renewal measurement and short initial lease | Improved. Measure remaining time against database wall time, use a three-second initial lease and a 3.1-second delayed effect, retaining actual expiry checks. This does not claim a new blocked-installer race test. |
| Claude Low: bound-authority and post-mismatch task coverage | Improved. The real-clock ordered cases use `bind_authority`; mismatch regression explicitly retains a running task for recovery. |
| Claude Low: installer context hardening | Deferred as optional defense in depth to ADM-06/Phase 6. The trusted installer remains configuration-authoring authority; this PR adds no general private reads or exceptional reopen activation. |
| Claude Low: misleading SQL trigger headers | Fixed the per-table header comments, including the prior round's scrub headers. No trigger name or firing order changed. |
| Claude Low: duplicate initial eligibility queries | Simplified. Initial bound admission performs eligibility once; subsequent ordered effects recheck eligibility under the retained transaction/installation lock. |

At the reviewed head, all eight rebuilt-image Compose cases passed in 738.96
seconds. Post-fix strict-schema/remaining-boundary regressions passed 62 cases
in 34.84 seconds; the four real-clock cases passed in 11.99 seconds. Baseline
validation passed 5,540 tests in 55.62 seconds, with 3,505 opt-in/profile skips
and two pre-existing warnings. No schema fingerprint changed: this correction
changes the Python timestamp expression and SQL comments, not database objects.

The final expanded worker regression set passes all 40 cases in 43.03 seconds;
Ruff and Markdown lint pass. Round 2 is complete with both accepted findings
fixed. Round 3 and final-head CI remain required; no PR has been created yet.

## Review round 3

Correction review `20260915-120455-e2db55` examined
`f92f62cb4820194918ef5ae77298d7e73680f599` through
`5478bbd66361a8649029c26d06135b0673cd9dec`, tree
`0e87a226297f5299d2598245de7e31de3d282dc4`. The exact permission probe and both
reviewers completed successfully. Claude covered all six correction files;
Codex returned a schema-valid APPROVED result with zero findings. Its read-only
review environment could not create PostgreSQL test files, so its approval is
code-review evidence, not an independent database-test pass. Parent validation
is recorded separately. Finalization retained artifacts without failed agents,
degradation or mismatch, and returned COMMENT.

The six raw Claude findings were one Medium and five Low; there were no High
or Critical findings. Four Low findings fell below cutoff and one cited an
unchanged file outside the correction diff. The accepted Medium strengthens the
terminal-reason negative test to use `boundary_replaced`: unlike arbitrary
text, this passes the generic reason vocabulary and must be denied by the
worker-specific proof. Both role variants retain the successful valid-skip
positive control and assert the exact worker guard message/SQLSTATE.

Low dispositions: clarified the compiled initial-admission dependency in a
comment and paired each expected test error with its SQLSTATE. The existing
schedule-factory monkeypatch pattern remains a test-helper refactoring deferred
to OPS-08 acceptance cleanup. The pre-existing `_now` docstring finding was
outside the correction diff and is deferred to DOM lifecycle documentation
cleanup; this PR's timestamp-writing rule is documented at the changed call
site. The finite-lease regression retains its real wall-clock safety behavior;
possible CI-load timing refinement remains OPS-08 test-harness work, not a waiver
of expiry enforcement.

The exact reviewed-head baseline passed 5,540 tests in 52.16 seconds, with
3,505 opt-in/profile skips and two pre-existing warnings. Final post-fix worker
validation passes all 40 cases in 43.99 seconds; Ruff/format and Markdown lint
pass. All three rounds satisfy the delegated exit criteria with no unresolved
accepted Medium-or-higher findings. BG-02.01/.02/.04/.05 are locally complete;
BG-02.03 remains partial for its Phase 6 restore/reopen token owner. PR and
protected delivery still require final-head CI; Gate 3 remains after Phase 4/5.

## PR delivery validation

[PR #32](https://github.com/epiphany40223/parishkit/pull/32) consolidates this
increment into three signed-off logical commits. The original review/correction
history is preserved on `jsq/pr/stewardship-boundary-review-evidence-20260915`.
The initial consolidated head `b3894dae` had exactly the same complete tree
`88a100378fc10bc53fece19fa491c6e21cd912d4` as original correction head `34d67e79`.

Initial PR CI run `34993776862` passed browser/Compose validation and all but
one database shard. Shard 5's existing
`test_all_concrete_mutable_records_have_enabled_guard` requires canonical quoted
identifiers in each immutable-column comparison. The revision guard used
unquoted `execution_revision`; PostgreSQL resolves it identically, but the
test's structural assertion failed. The correction quotes that lowercase
identifier consistently with adjacent fields; it changes neither scope nor
execution semantics. The independent catalog audit passed in 8.98 seconds,
with only that expected function-text difference, and 47 storage/end-date
regressions passed in 17.27 seconds. The strict fingerprint was updated only
after this audit; all 17 strict schema/model checks then passed in 16.15 seconds.
This mechanical CI correction does not require an additional
independent review round under the controlling material-change rule.

Protected delivery remains pending a fully passing run on the corrected head,
followed by all merge-group checks and verification on refreshed `origin/main`.
