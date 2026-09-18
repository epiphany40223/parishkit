# Stewardship periodic authentication health

Continue [BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown)
from [PR #52 protected delivery](stewardship-health-observation.md#protected-delivery)
on `pr/stewardship-periodic-health`, based on verified main `08367cb7`.
Follow the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
and [observed-recovery contract](../specs/stewardship/architecture/spec.md#family-credential-security).

## Scope

Close the prior review's explicit quiet-traffic recovery gap as one bounded
web-lifecycle increment. Run actual limiter observations without requiring a
login request, with finite resource budgets and safe worker shutdown. Preserve
the existing SQL fences, aggregate predicates, notification owners and private
web credentials. Health HTTP endpoints remain read-only; the scheduler does not
gain web credentials. Current source/provider/due-work service observers remain
the next checkpoint before full BG-10 acceptance or ADM-05/Gate 3.

Keep measured test-efficiency work in a separate logical commit. Every relevant
application test, real lease/drain check and reference-scale case remains.
Trial twelve isolated PostgreSQL shards with deterministic balanced ownership;
complete same-tree receipts and combined coverage remain mandatory. Reuse setup
only for compatible cases with independent rejected-write transactions, and
remove unrelated configuration setup from focused limiter tests.

No historical upgrade compatibility, retained database deletion, provider writes,
deployment, release or production-readiness approval is implied. Complete three
successful dual-source review/fix rounds and exact-head CI/DCO before delivery.

## Measurements before implementation

The unchanged 31 recovery tests pass locally in 23.41 seconds with the full
authentication fixture and 19.82 seconds with a diagnostic narrow real-SQL/
real-Valkey fixture; both include about 10 seconds of schema bootstrap. Four
financial guard tests each repeat roughly one second of unrelated fixture work
after initial bootstrap. Profiling shows filesystem durability calls are not
the significant cost; do not remove them or weaken YAML parsing for test speed.

A replay of run `35313808093` estimates the longest test/baseline workload at
911.8 seconds under the old lexical eight-way partition, 846.8 seconds with
hashed ties and eight shards, and 577.6 seconds with hashed ties and twelve
shards. The twelve-way trial trades four additional isolated bootstraps for
shorter wall time. These are simulations, excluding runner queue/setup overhead,
not a claim of measured end-to-end improvement. Record actual CI results below.

## Implementation checkpoint

The admitted Gunicorn child starts one periodic observer after publishing its
credential receipts. Every thirty seconds it checks child/lifecycle admission
and forces the existing real limiter probe. Actual failures retain deduplicated
outage intent; failures to persist remain unavailable, never healthy. The child
exit hook stops and joins only its own thread, with a two-second upper wait.
An unresponsive probe cannot spawn replacement threads or delay exit forever.

SQL observation connections close after every pass and use two-second statement,
one-second lock and ten-second transaction limits. The web SQL ceiling and
auxiliary reserve account for the third observation thread across rollout
overlap; the default total is 99 connections. Existing Valkey credentials/key
namespaces remain unchanged, with one additional bounded pool slot.

Seven real PostgreSQL/Valkey checks pass in 12.23 seconds, including autonomous
recovery on a separate thread under exact web SQL grants and unavailable-store/
failed-persistence paths. The 299 focused process, topology, consumer and budget
checks pass in 2.18 seconds. Configured development/production Compose scenarios
now include a read-only SQL probe that requires observation timestamps to
advance without any authentication or health mutation from the test itself;
that evidence belongs to pending CI, not the local unit-test result.

The narrower limiter fixture and grouped financial rejection checks pass all
60 tests across both modules in 87.93 seconds. All four rejected mutations still
check SQLSTATE `23514` and absence of submission, receipt and source-pin effects.
The complete financial scenarios retain their separate setup/transaction paths.
Quality/partition checks pass 124 cases in 3.40 seconds before the additional
real-workflow matrix/combiner consistency check. Actual twelve-shard CI timing,
three review rounds and final delivery remain pending.

## Review ledger

### Round 1

Successful dual-source session `20260918-031716-30bfb5` reviews
`08367cb7..eab51ad`, with zero HIGH, two MEDIUM and eight LOW findings.
Accept the MEDIUM stale CI-contract assertions: derive shard counts from the
actual matrix in existing gate/release tests as well as the new consistency
test. CI independently exposes those assertions and the stale forty-connection
fixture; retain rejection of forty and accept the new actual ceiling of forty-four.

Accept the MEDIUM synchronized-observer concern with narrow handling: stagger
initial child phases by zero to thirty seconds and let only unattended probes
skip an already-owned health-observation advisory lock. A skipped sample does
not mutate proof or resolve an outage. Keep all other SQL/lock/persistence
failures fail-closed; do not blanket-ignore database timeouts. Real PostgreSQL
peer-contention coverage verifies unchanged proof and absence of false notices.

Accept six LOW refinements: classify expected limiter unavailability accurately,
use the observer event during exit, share identical failure handling, broaden the
sanitized startup message, bind the financial mutation explicitly, and stop the
threaded test after exactly one sample. Retain fail-fast reporting within the
shared financial test: every mutation still executes on a passing run with its
own rollback and labeled invariants, without adding a dependency or raising the
repository's pytest minimum merely for subtest reporting. The LOW trial/timing
concern is already part of acceptance; keep actual CI measurements mandatory and
remove the duplicate hard-coded shard count. The LOW one-connection-slack note
is not a current defect: the explicit admission guard rejects future expansion
unless configured capacity also increases; do not silently raise the SQL budget.

CI run `35318651294` also exposes a preexisting Firefox test race: response
arrival precedes JSON handling and DOM visibility updates. A separate signed-off
test commit waits for the actual DOM state with bounded browser expectations;
it does not add sleeps or weaken application checks. Include this correction
in round 2's independent delta review. Both configured Compose scenarios pass
the actual unattended-observation proof on this intermediate head.

Post-correction validation passes 176 focused unit/contract checks in 6.79 seconds
and 62 real PostgreSQL checks in 37.88 seconds. The first local browser attempt
cannot run because its virtual environment lacks Playwright; install the pinned
browser requirements and rerun, rather than treating that attempt as evidence.
Round 1's final browser check and subsequent independent rounds follow below.

The corrected Firefox polling regression passes in 7.79 seconds with the pinned
browser dependency and existing local engine. Eleven focused observer lifecycle/
diagnostic checks pass in 0.13 seconds; Ruff, formatting and changed Markdown
checks pass. This completes round 1. The initial stagger is included in the
bounded read-only Compose probe deadline, without changing production cadence.

### Round 2

Successful dual-source session `20260918-033101-c945ee` reviews
`eab51ad..9389eaf`, with zero HIGH, two MEDIUM and seven LOW raw findings.
Accept both MEDIUM test gaps: run actual peer contention with and without an
existing local/durable outage, preserving proof, incidents and notices until a
real post-contention probe; synchronize every browser poll on the separate
availability indicator's final success/error action before asserting unchanged
warning state. Codex's two LOW findings duplicate those accepted concerns.
The test seeds only the separate availability indicator's opposite state, never
the warning under test; no production hook or arbitrary delay is introduced.

Accept LOW documentation of skipped/throttled return semantics, explain the
ordinary probe calling contract, and pin the maximum initial jitter in its
existing cadence test. Retain the bounded random source: cryptographic strength
is unnecessary but harmless and no dependency is added. Keep the dependency
classification regression beside its producer integration; its location does
not weaken coverage. Do not move the established limiter exception solely for a
hypothetical missing required runtime dependency; current startup imports it
before this observer can run, and no observed import cycle is introduced.

CI run `35318651294` shard eight also exposes a missing SQL allowlist entry for
the new diagnostic event. Add that exact event to the fresh-install baseline.
Independent installations of immutable predecessor `08367cb7` and the corrected
baseline differ only in `stewardship_operational_log.operational_event_safe`:
its definition adds exactly one permitted literal. All catalog identities,
counts, other definitions and grants match; update only the verified constraint
fingerprint. Retain both audit databases without upgrading or deleting any
existing database. Existing event-registry and schema tests cover this contract.

Post-correction validation passes 50 focused PostgreSQL/schema/event checks in
21.09 seconds, six browser cases across all three engines in 9.02 seconds and
35 observer/diagnostic unit checks in 0.37 seconds. Django reports no model-state
drift. The intermediate attempted browser completion observer failed because
removing an already-absent attribute has no mutation; it is replaced by the
explicit opposite-state completion edge above, not counted as passing evidence.
Round 3 independently reviews this correction delta, including the CI fix.

### Round 3

Successful dual-source session `20260918-034102-1ca953` reviews
`9389eaf..8402b5a`, with zero HIGH, one MEDIUM and one LOW raw finding.
Accept the MEDIUM strengthening of the contention regression: compare the
complete operational episode before/after the skipped probe, require both
incident models to resolve after the accepted sample, and prove the no-outage
case creates neither incident nor notice. Clarify the LOW note that a skipped
SQL-lock probe still updates its process-local throttle; durable proof remains
unchanged. The nine real periodic-observer PostgreSQL tests pass in 12.92
seconds after these corrections; repository Ruff and formatting checks pass.

This completes three successful dual-source review/fix rounds. No accepted
MEDIUM-or-higher finding remains. The final test assertions and docstring are
round-3 corrections, not an unreviewed expansion of production behavior. Keep
the final exact-head CI/DCO and protected merge receipt in PR #53 and the next
increment's linked delivery record; no full BG-10 or Gate 3 completion is claimed.
