# Stewardship operational health observations and recovery

Continue [BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown)
from [PR #51 protected delivery](stewardship-operational-health.md#protected-delivery)
on `pr/stewardship-health-observation`, based on verified main `fb945fb9`.
Follow the [controlling Phase 4 plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
and [critical-notification contract](../specs/stewardship/background-processing/spec.md#critical-errors-and-notification).

## Scope and checkpoints

Deliver these as small, independently reviewed checkpoints. The first PR owns
authentication recovery and the separate measured test-efficiency commit;
source/service/provider observers and shutdown integration remain subsequent
checkpoints. Do not expand this first PR into another full-phase diff.

1. Add observed healthy-window recovery for the retained authentication
   incidents and bounded repeat observations of continuing limiter outages.
   Successful real probes, store continuity and current aggregate observations
   must support recovery; missing cache entries or elapsed silence cannot.
2. Complete current-phase source freshness/failure, due-work service health and
   provider-health observation through the existing incident/notice owners.
   Respect current source scope, intentional holds, SQL time, service isolation,
   finite probe budgets and notification-failure recursion boundaries.
3. Preserve exact Task/provider ownership during graceful and forced shutdown.
   Integrate real maintained-execution and restart/recovery evidence with the
   accepted email/Slack transport evidence, without treating intent as delivery.
4. Keep the requested measured test-efficiency improvements in a separate logical
   commit: shared setup for compatible rejected-write probes, shortened synthetic
   request budgets and current shard-duration estimates. Preserve real isolation,
   guards, lease/drain clocks, application scale checks and complete CI coverage.
5. Complete at least three independent dual-source review/fix rounds, focused
   local validation and exact-head full CI/DCO before protected delivery. Update
   each task only when its complete current-phase acceptance passes.

Backup RPO, publication ambiguity and purge-specific producers stay with their
later owning packages. No historical upgrade compatibility, retained database
deletion, live provider writes, deployment, release or Gate 3 approval is implied.

## Test-efficiency measurements

The predecessor run already uses one fresh schema bootstrap per isolated shard
and memory-backed disposable PostgreSQL storage. Do not claim either as a new
optimization. Preserve production storage and the separate Compose persistence
checks unchanged.

A local diagnostic runs all five setup-mail SQL-rebinding probes with one
shared setup and an independent rollback transaction for each attempted write.
The original five-fixture execution takes 26.36 seconds, including a 9.94-second
schema bootstrap; the grouped diagnostic takes 12.91 seconds, including a
9.85-second bootstrap. All five rejected mutations remain. The diagnostic uses
an external process-local plugin, not a change to the reviewed predecessor or
acceptance evidence for a committed implementation.

A second diagnostic applies the existing short synthetic-request budget to
setup-source disposal. The test body takes 8.29 seconds, retaining the real SQL
deadline and full five-second provider drain. The prior CI body takes 51.76
seconds. Actual Task/source lease-expiry tests and 5,000-Family application
scenarios remain relevant and must not be deleted as tool tests.

The old shard hints overestimate several now 8–15-second setup tests as
55–100 seconds and reserve only 90 seconds for a baseline observed near
228 seconds. Any rebalancing remains scheduling only: every collected test
must still run exactly once, and combined same-tree coverage remains mandatory.

Implementation checkpoints and their test/review evidence follow as completed.

### Shared-setup implementation checkpoint

The repository test now executes all five SQL-rebinding rejections against one
prepared mail exchange, with a separate rollback transaction for each mutation.
Every rejection also checks SQLSTATE `23514` and verifies the complete exchange
row remains unchanged. The disposal test uses the existing short fake-provider
budget; the actual database deadline and provider drain remain intact.

Focused execution of both PostgreSQL modules passes all 12 collected tests in
51.62 seconds, with one 10.06-second schema bootstrap. The grouped rejection
test body takes 3.91 seconds; the disposal body takes 8.83 seconds. This is
focused local evidence, not a claim that complete CI already passed.

Shard estimates now use the predecessor CI measurements, distinguish the
unloaded disposal parameter from its real-lease sibling and reserve 240 seconds
for the first shard's separate baseline. Unknown tests retain default weight;
the selector still requires exhaustive, unique, deterministic ownership.
All 53 quality/partition tests pass in 2.75 seconds; targeted Ruff, formatting,
Markdown and whitespace checks also pass.

## Authentication recovery checkpoint

The real INFO/canary probe now also samples both aggregate windows atomically
without inserting attempts, pruning counters or extending their TTLs. Durable
timestamps implement the [observed-recovery contract](../specs/stewardship/architecture/spec.md#family-credential-security).
The health and authentication locks serialize retained samples against new
failures. Stale samples do not declare availability, and the incident owner's
recovery cutoff separately rejects observations newer than healthy proof.

Continuing outages reobserve the operational episode at most once per minute;
the existing operational owner independently suppresses repeated notifications.
The existing authentication row's fixed-size last-failure fence advances for
every failure, so suppressed repeats cannot escape recovery ordering. There is
no per-request historical row, count increment or notification allocation.
No new provider authority, recipient access or notification content is added.
Recovery requires actual successful requests/probes; quiet traffic is not proof.
This checkpoint supplies startup and request-driven observations only. The
following BG-10 service-health checkpoint must supply a bounded periodic forced
probe so quiet parishes can recover without waiting for login traffic. Until
that producer is implemented, uninterrupted idle-time recovery is not delivered
and BG-10.03 remains incomplete. Do not grant scheduler access to web limiter
credentials merely to shortcut the existing service-isolation contract.

### Fresh-install schema audit

An independent installation of immutable predecessor `fb945fb9` matches its
committed fingerprint. A second fresh installation adds exactly four nullable
observation/window timestamps and three ordering constraints to the existing
limiter-health table. No preexisting object changes or disappears. Counts are
182 relations, 2,098 columns, 2,979 constraints, 898 indexes, 510 functions,
487 triggers and 28 policies; grants and provider ownership are unchanged.
Both audit databases are retained. SQL and initial Django model state change
together; no retained development database is upgraded or deleted.

The focused recovery, limiter, operational-incident, pure-window and complete
fresh-schema/model checks pass 70 tests in 32.69 seconds. Django's migration
autodetector reports no drift. Full CI and independent review remain required.
The final focused recovery module passes 14 tests in 15.33 seconds, including
direct SQL rejection of invalid proof windows and a failure committed during
the real probe. Repository Ruff/format checks and changed Markdown checks pass.

## Review ledger

### Incomplete first attempt

Session `20260918-020249-8e7a11` reviews `fb945fb9..1a4c6a7`. Claude completes;
Codex exits before reviewing with `Selected model is at capacity`. Finalization
reports `codex-reviewer: result artifact missing — codex produced no structured
output`. This is degraded evidence, not a completed dual-source round or an
approval. A fresh full-diff dual review is required.

Claude reports zero HIGH, two MEDIUM and six LOW findings. Both MEDIUM findings
are accepted: add counter-only threshold regressions without a masking failure
receipt, and classify successful incoherent canary reads as counter loss rather
than falsely declaring store unavailability. All six LOW findings are also
accepted: fence repeated outage observations, limit steady-state recovery work
and old resolved inputs, assert discarded-sample state/callback behavior,
document the tri-state contract, cross-reference/test detector predicates, and
cover older failures plus missing previous samples in the pure window policy.
Post-correction limiter/recovery/window validation passes 55 tests in 30.68
seconds. The partial review remains uncounted despite those corrections.

The first CI run `35313245105` identifies a real deployed-ACL omission: the new
read-only recovery script needs `ZCOUNT`, absent from the compiled web Valkey
allowlist. Add that single command without expanding any key namespace or
other service's authority, plus a real pinned-Valkey test running the exact
script as the web identity. Include this correction in the fresh full review.
The focused real-container run passes the script and all nine cross-service
denial probes in 0.97 seconds, sharing one ephemeral Valkey fixture. Only that
fixture's own disposable container is removed; retained service data is untouched.

### Round 1

Successful dual-source session `20260918-021210-dedccd` reviews
`fb945fb9..bb87019`, with no degradation. Raw totals: zero HIGH, two MEDIUM and
two LOW. Codex's MEDIUM availability race is accepted: move timestamp-fenced
probe recovery inside its authentication-lock transaction, remove the later
unfenced callback, and give INFO-throttled counter recovery the same SQL-start
fence. Add before/after-commit and counter-path regressions.

Claude's MEDIUM periodic-probe concern is assigned explicitly to the following
BG-10 service-health checkpoint, which already owns autonomous service
observations. This PR makes no quiet-traffic recovery or completed BG-10 claim;
the requirement is not discarded or bypassed. The LOW helper stale-timestamp
concern is fixed defensively. The low-confidence LOW lock-contention concern
is not accepted as a new outage-policy defect: the predecessor already acquired
the same authentication lock for every accepted probe's availability callback,
and unavailable durable security evidence intentionally remains fail-closed.
No external I/O runs under that lock; filters and absent-episode checks limit
the added recovery work. Do not silently weaken audit persistence on timeout.
Post-fix recovery, limiter, existing authentication-race and pure-contract
validation passes 128 tests in 38.70 seconds. Ruff, formatting, changed Markdown
and whitespace checks pass. This completes round 1; two further independent
dual-source rounds remain mandatory.

### Round 2

Successful dual-source session `20260918-022328-e46669` reviews
`bb87019..7c2e302`, without degradation. Raw totals: zero HIGH, four MEDIUM and
four LOW. The two reviewers independently report the auth/operational
split-resolution race; these are one accepted defect, not two distinct fixes.
Resolve neither record unless both timestamp fences pass under the retained
locks, and retry any still-open operational recovery. Return the actual recovery
outcome so refused availability keeps the counter-path retry flag set.

The third distinct MEDIUM concerns suppressed repeat failures: always advance
the existing fixed-size authentication fence, while independently throttling
operational observations by their own timestamp. Do not allocate per-request
audit/history/notice rows. The LOW missing-interleaving tests and retry-flag
report are covered by those fixes. Both LOW helper/comment issues are fixed by
checking stale observed time at the outer boundary before all mutation and
describing the lock-taking/re-entry correctly. An accepted probe now returns an
explicit loss/availability record; None still means discarded proof, and the
public check_health method retains its boolean loss result.
Post-fix validation passes 148 focused recovery, limiter, authentication-race,
operational-intake and pure-contract tests in 48.29 seconds. Ruff, formatting,
changed Markdown and whitespace checks pass. This completes round 2.

### Intermediate full CI

Commit `bb87019` passes all 24 CI jobs plus DCO in run `35313808093`, including
all eight operational Compose scenarios, three browsers and eight PostgreSQL
shards. Same-tree coverage is 93.89% lines and 84.86% branches. This precedes
the later availability-fence corrections and is not final-head acceptance.

Measured PostgreSQL shard jobs range from 10 minutes 25 seconds to 17 minutes
3 seconds. The first shard's database portion covers 347 cases; the other
shards cover 485–493 each. The rebalance has not demonstrated a meaningful
end-to-end wall-time improvement over the predecessor. Keep the measured
fixture savings, but do not claim that scheduling alone solved CI latency;
follow-up work must address aggregate fixture/test cost and residual imbalance.

### Round 3

Successful dual-source session `20260918-023517-aaff1f` reviews
`7c2e302..6342cbd`, without degradation. Raw totals: zero HIGH, one MEDIUM and
four LOW. Accept the MEDIUM local retry-state race: a later thread may report a
failure after durable success but before the caller settles its local flag.
Use a short-lock generation comparison for both probe and counter recovery;
hold no local state lock during network or database I/O. Add real-thread
interleaving regressions followed by successful INFO-throttled counter recovery.

Accept the LOW callback-contract documentation and refused-probe flag coverage
notes. Retain the extra outer observed-time guard as defense against inconsistent
or seeded future proof, documenting why it supplements the normal bookkeeping
order. Accept the LOW per-failure write-cost observation as an explicit tradeoff:
exact failure ordering costs one update of the existing fence row per failure,
including WAL/dead-tuple work; bounded row/notice count is not bounded write
volume. Do not coalesce away a failure that could invalidate outstanding proof.
The existing finite lock budget and fail-closed persistence policy still apply.
Post-fix recovery, limiter and callback-contract tests pass 69 cases in 36.80
seconds, including both real-thread interleavings and follow-up recovery. This
completes round 3 with no validated HIGH/CRITICAL and no unresolved accepted
MEDIUM findings in this increment. The deferred periodic producer remains an
explicit subsequent package requirement, not completed work.

The delivery handoff records the consolidated exact SHA/tree and protected
checks. Consolidate corrective commits without altering their final tree,
retain the reviewed predecessors locally, and require fresh final-head CI/DCO.
Do not treat intermediate CI or this review-loop completion as merge evidence.

## Protected delivery

[PR #52](https://github.com/epiphany40223/parishkit/pull/52) merged at
2026-09-18 07:03:29 UTC as `08367cb78a5459b9077d9baeca7778b79a0a4ea6`,
verified on freshly fetched `origin/main`. Final head `8cd54cc7` contains three
signed-off logical commits; its tree `175bbda9` is identical to the post-round-3
correction tree at `bff7528`. All 24 jobs in exact-head CI run `35316183897`
and DCO pass. Same-tree coverage is 93.89% lines and 84.85% branches.
PostgreSQL jobs range from 9 minutes 48 seconds to 16 minutes 24 seconds.

Standing human authority permits this protected merge and continuation without
a merge-queue rerun. The [periodic-health increment](stewardship-periodic-health.md)
closes the explicitly deferred quiet-traffic gap next; the broader BG-10 package
and Gate 3 remain incomplete. No deployment or release is authorized.
