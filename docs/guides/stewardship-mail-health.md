# Stewardship mail-provider health

Continue [BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown)
from [PR #55's protected delivery](stewardship-source-health.md#protected-delivery)
on `pr/stewardship-mail-health`, based on verified main `7b5c43b6`.
Follow the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
and [critical notification contract](../specs/stewardship/background-processing/spec.md#critical-errors-and-notification).

## Coherent outcome

Connect actual campaign-mail provider outcomes to durable critical notification
and verified recovery. Systemic failures alert immediately; three consecutive
observed unavailability results for the same provider configuration also alert.
Persist critical intent atomically with the owning outbox result so interruption
or delayed collection cannot lose a short-lived outage. Existing process-local
delivery circuits remain admission controls, not durable notification evidence.

Recovery requires an actual healthy SMTP observation against the current
provider configuration, begun after newer failed observations. Recipient refusal
can still establish healthy transport; unknown or unobserved outcomes cannot.
Consume pending critical receipts before resolving, preserving one recovery
notice and immutable history. Operational-email failure must not recursively
generate email-failure notifications. No new provider probe or external write
is authorized; tests use synthetic transports and real PostgreSQL ownership.

Due-work service monitoring and the final current-phase BG-10 acceptance remain
the following increment. This independently testable mail-outcome boundary keeps
review scope separate from cross-process scheduler/worker health observation.
No gate, deployment, release or production-readiness approval is implied.

## Evidence

Implementation and three dual-source review/fix rounds are complete. Final-head
CI and protected delivery remain pending. Do not mark BG-10 complete until all
remaining current-phase owners pass acceptance.

The implementation reuses the immutable outbox evidence protocol. Bounded queries
select three canonical observed results, then independently validate their full
typed contents and digests. Provider identity includes Workspace credentials and
settings plus public sender/reply settings; unrelated YAML edits cannot reset
the failure streak. SQL derives the immutable result's indexed identity from its
rendering; callers cannot choose the key. Recovery uses that same identity
function and a dedicated healthy-result index. The isolated MAIL role receives
no operational-log INSERT
or historical reads. An insert-only SQL trigger on already-guarded outbox results
commits safe critical intent with the exact submitted attempt. Operational sends
cannot originate an alert, but their typed observations participate in continuity.

The existing general-worker collector resolves provider incidents separately
from source sampling, inside an isolated savepoint. Actual operational SMTP
outcomes may prove recovery or veto older success, without originating another
mail alert. Metadata-only scheduling continues while source work is held. No
new table, scheduler, network probe or process-local health authority is added.
The scheduler reads only incident kind/resolution metadata and stops mail-only
sampling once no unresolved provider incident remains.

Independent empty databases install immutable predecessor `7b5c43b6` and the
new baseline. Complete inventories differ only in the operational event allowlist
and matching SQL receipt-classification function: exactly one diagnostic literal
and one branch are added. Round 1 adds exactly one revoked-public trigger function,
its trigger, and an ordered partial SMTP-outcome index. Independent catalogs prove
all other object definitions and permissions unchanged; model drift is absent.
Retained databases are not upgraded or deleted. The scheduler's compiled metadata
reads are described above; MAIL's original restricted grants are preserved.

The first seven PostgreSQL integration tests pass in 54.07 seconds; two additional
configuration/rollback cases pass in the subsequent targeted run. That run also
found an overly narrow test-helper assumption about the number of notices,
corrected by selecting the actual mail-provider notice. An earlier retry could
not build its disposable test schema because the retained audit container's
1 GiB memory filesystem was full. A separate fresh test container preserves all
old databases; this is infrastructure capacity, not a passing test or application
failure. All 119 focused transport/observability checks pass in 0.75 seconds.
The corrected non-recursion test and adjacent real-role, provider-result,
operational-dispatch and fresh-schema checks pass all 60 cases in 53.80 seconds
with one database bootstrap. Ruff, formatting, Markdown and diff checks pass.
The following sections record the subsequent three review/fix rounds; final-head
CI remains required.

## Review round 1

Session `20260918-102219-5715c3` independently reviewed the full diff from
`7b5c43b6` to `4823a63` (tree `b13bd80d`). Both vendors completed without
degradation: seven raw Medium findings and five Low, no High/Critical. Pika
merged two distinct continuity/evidence concerns into one displayed finding;
both were separately investigated.

- Fixed the two no-op scheduling findings with unresolved-incident metadata,
  including actual scheduler-role regression coverage before/after recovery.
- Fixed continuity across operational sends: they break the failure streak
  without originating another alert. Added actual dispatch coverage.
- Removed both broad MAIL log INSERT and historical timestamp SELECT exposure.
  The guarded result trigger emits fixed content without exposing a callable
  definer. Actual-role tests reject direct log insertion, reading and invocation.
- Added the ordered partial provider-result index to avoid a full journal sort
  for each latest-result lookup. Returned/validated records remain bounded.
- Historical poisoning is not reachable through supported writes: the existing
  `stewardship_family_dispatch_result_v1` guard rejects malformed typed evidence
  before it becomes history. A negative regression confirms this. Future protocol
  upgrade compatibility remains outside the pre-production policy. The SQL
  sampler nevertheless treats unverifiable prior evidence as a streak break.
- Recovery-sampler defects now use a task-failure diagnostic rather than falsely
  labeling them as provider failures. Pending-receipt and fourth-failure coverage
  is added. Repeated failures intentionally retain occurrence counts; notification
  suppression prevents storms, and newer failure evidence must veto old success.
- Retained the one-second retry wait: it respects the existing minimum real
  database retry interval, without bypassing delivery guards or altering live
  ownership/provider clocks. Fixtures already share their expensive setup.

The fresh correction catalog against the original independent PR-head install
adds exactly one index, function and trigger; every original object is identical.
The strict fingerprint records 899 indexes, 511 functions and 488 triggers;
tables, columns, constraints and policies are unchanged from that checkpoint.
An initial correction test run exposed two harness errors (interrupting before
submission, and expecting SQL to accept malformed evidence), not passing checks.
Their corrected regression run remains the validation authority. Fourteen mail
health cases pass in the follow-up run; its remaining operational-success helper
needed the explicit policy argument. That corrected case, exact model/schema
comparison and adjacent background grants pass all 30 targeted checks in 29.92
seconds. Ruff, formatting, Markdown and model drift checks pass. The initial
head's full CI run `35355809651` passed, but correction-head CI is still required.

## Review round 2

Session `20260918-103918-fa9d4c` reviewed corrections from `4823a63` to
`96475e1` (tree `960a4a4a`) with surrounding health code. Both vendors completed
without degradation: one raw Medium and seven Low, no High/Critical.

- Fixed the remaining Medium scan cost, also noted as Low by Claude: the
  original ordered index could still scan unrelated provider history. The
  immutable event now retains one SQL-derived provider-identity digest, with
  identity-leading ordered outcome and healthy-result indexes. One shared SQL
  identity function serves both insertion and recovery, eliminating divergent
  provider matching. A 20,000-row sparse-provider query-plan fixture exercises
  the actual ORM query and installed index definitions in a transaction-local
  replica; fewer than 100 outcome rows may be visited. It never disables or
  seeds invalid data into the authoritative delivery tables.
- Fixed the permission-test ambiguity by requiring SQLSTATE `42501`, rather
  than accepting an intrinsically uncallable trigger-function error. Added
  mixed operational-unavailable continuity alongside healthy SMTP coverage.
- Added a trigger `WHEN` predicate to skip irrelevant definer invocations.
  Retained frozen reason literals in SQL/model state; round-trip model/schema
  comparison and actual typed-result tests protect that deliberate duplication.
- The existing two systemic-outcome cases provide the rollback scenario's
  positive insertion controls; the interruption case then proves atomic absence.
- Sampler exceptions already retain an open provider incident and a correlated
  task diagnostic. A low-confidence suggestion to generate further critical
  monitoring alerts is deferred to broader due-work monitoring, avoiding a new
  recursive alert producer in this mail-outcome slice.

CI `35357583511` found the new SQL file absent from Docker's explicit build
allowlists (Compose bootstraps and the existing package check failed). Both
allowlists are corrected; 41 packaging tests and both real scratch-context Docker
checks pass (0.24 seconds and 2.17 seconds). No required check is waived.

All 18 mail-health/model checks pass in 119.52 seconds, including actual grants,
rollback, operational continuity, pending receipts, recovery and reference-volume
queries. The preceding 20 collector/operational-dispatch regressions passed in
28.26 seconds. No full local suite is repeated.

The independent next fresh catalog adds one non-null outcome identity column,
two identity functions, one derivation trigger and a healthy-result index; only
the PR's outcome index/result function/result trigger are revised. Every other
catalog definition/owner/ACL is identical to the first corrected catalog.
Counts are 182 relations, 2,099 columns, 2,980 constraints, 900 indexes, 513
functions, 489 triggers and 28 policies. Model drift is absent; no retained
development database is upgraded or deleted. At this checkpoint round three and
final-head CI remained required; the next section records that round.

## Review round 3 and handoff

Session `20260918-105328-3ec629` reviewed corrections from `96475e1` to
`600bea2` (tree `ef725b00`) with the full affected result/recovery context. Both
vendors completed without degradation: one raw Medium and four Low, no
High/Critical.

- Fixed the Medium same-provider unobserved-history scan: the outcome index
  now includes only the three observed health prefixes. SQL, model declaration
  and frozen fresh-install model state agree. The reference-volume regression
  covers 20,000 foreign-provider rows, same-provider unobserved rows and
  same-provider unhealthy rows, with both the real ORM lookup and the query
  extracted from the installed trigger. It counts visits including plan loops,
  not elapsed time or a forced planner choice.
- Fixed the Low identity-derivation and healthy-query coverage gaps: stored
  outcome keys equal the shared SQL function; non-outcome keys are empty;
  healthy recovery still visits fewer than 100 outcome rows in the volume case.
- Retained the trigger's internal eligibility check as deliberate defense in
  depth, in addition to its performance-oriented `WHEN` predicate. Rewrapped
  the noted guide paragraph.

All four final query/model cases pass in 26.17 seconds; model drift is absent.
The final independent catalog comparison changes only
`outbox_provider_history`, with unchanged object counts and all other complete
definitions/owners/ACLs identical. No accepted Medium-or-higher finding remains.
Per the controlling cycle, these fixes and passing regressions complete round
three without requiring an empty fourth review. Final-head CI remains required.

Implementation fixups will be consolidated into one signed feature commit,
separate from the predecessor delivery/scope receipt. The PR handoff retains
the final checkpoint/tree mapping and exact-head CI/DCO receipts; verified merge
delivery is committed by the next fresh-main increment, avoiding receipt-only CI.
