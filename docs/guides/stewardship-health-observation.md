# Stewardship operational health observations and recovery

Continue [BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown)
from [PR #51 protected delivery](stewardship-operational-health.md#protected-delivery)
on `pr/stewardship-health-observation`, based on verified main `fb945fb9`.
Follow the [controlling Phase 4 plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
and [critical-notification contract](../specs/stewardship/background-processing/spec.md#critical-errors-and-notification).

## Scope and checkpoints

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
