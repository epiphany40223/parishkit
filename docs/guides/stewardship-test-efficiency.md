# Stewardship test efficiency

## Execution policy

Use focused tests while implementing or correcting a finding. Commit and push
coherent checkpoints early; let draft-PR CI run the complete acceptance suite
while peer reviews proceed. Do not routinely repeat the same full suite locally
and in CI. Required exact-head, coverage and protected merge-queue checks remain
mandatory. A superseded PR run is cancelled; main and merge-queue runs retain
independent concurrency groups. This follows
[GitHub's workflow concurrency contract](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency).

## Measured bottlenecks

The successful PR #47 merge-group run `35243208902` measured PostgreSQL partition
execution at 789–1,175 seconds, Firefox at 712 seconds, WebKit at 310 seconds,
Chromium at 257 seconds, and the credential-free baseline at 132 seconds.
Dependencies took about 25 seconds per PostgreSQL runner; database initialization
took 11–26 seconds. Test execution, not package installation, dominates.

A focused nine-case weekly manual-report profile on September 17, 2026 took
52.47 seconds with profiling overhead: one fresh database/migration setup took
23.38 seconds, SQL execution 15.64 seconds across the run, and nine teardown
flushes 3.74 seconds. These nested times overlap and must not be added. The
profile is diagnostic, not a CI performance comparison or acceptance receipt.

Firefox's per-test startup repeatedly took about 2.5 seconds in CI. Browser
process creation was deliberately repeated for all engines after a known WebKit
hang when reusing a process across the 64th scenario. That specific workaround
does not establish a need to restart Chromium and Firefox for every scenario.

## First bounded optimizations

- Reuse Chromium and Firefox processes for an engine's run, while every scenario
  retains a fresh context for cookies, storage, routes and clocks. Leaked
  contexts fail the fixture and are closed before another scenario. Mixed-engine
  local runs release the cache before WebKit to avoid nested synchronous driver
  loops. WebKit retains its fresh driver and process for every case.
- Put only each disposable PostgreSQL CI service's data directory on bounded
  2 GiB tmpfs. Keep normal SQL transactions, fsync, constraints and role checks;
  do not relax database semantics or change application/deployment storage.
  Container persistence checks remain in the separate Compose suite using its
  real durable storage. Each partition already owns a fresh isolated service.
- Cancel superseded PR heads, retaining every current-head test and coverage
  check. No selectors, assertions or required check names are removed.

Measure the resulting GitHub jobs before claiming a speedup. Do not substitute a
longer timeout for a performance fix, share mutable database rows/roles between
concurrent cases, or waive coverage to reduce elapsed time.

## Relevance and repeated-bootstrap audit

Retain a test when it protects ParishKit behavior, a required integration
assumption, or a documented dependency regression. Do not add tests merely to
prove a dependency implements its documented primitives. Review expensive
end-to-end parameter matrices for cases that can exercise the same ParishKit
decision through a cheaper layer; preserve representative real-service paths.

The initial sample covers storage/session guards, cryptographic envelopes,
fresh-schema contracts, browser fixtures, and the slow report/setup-disposal
cases. The storage guards and cryptographic tests exercise ParishKit's own
constraints, key-purpose separation and context binding, not PostgreSQL or AES
correctness. Fresh-schema comparisons protect our separately maintained SQL
baseline against our model declarations; they are not upgrade tests. Real
worker-disposal waits verify lease ownership and safe cancellation. None of
these should be deleted merely because a dependency is involved.

Further candidates are repeated creation of identical configuration/source
fixtures, unnecessary database access in validation-only tests, and repeated
application bootstrap within parameter matrices. Shared infrastructure must
preserve fresh mutable state, exact-role admission, genuine commit boundaries,
and concurrent-race tests. The whole-suite relevance audit remains in progress;
no wholesale removal or bootstrap caching is claimed by this first optimization.

### Follow-up measurements and grouping

The PR #48 run `35284928521` completed all Chromium and Firefox scenarios:
their test steps took 99 and 232 seconds, respectively, versus 257 and 712 in
the earlier run. These are observed CI comparisons, not controlled benchmarks;
the suite also gained five scenarios. WebKit keeps its documented workaround.

The setup wizard's five public pages now run as one authenticated journey,
retaining every page's save/revisit/passive-session assertion and adding a final
check that all five saved drafts coexist. This eliminates four repeated
configuration bootstraps, OAuth logins and database flushes. Separate pure-form
tests still cover individual input cases. On the same disposable local database,
the focused before/after commands took 14.46 and 12.15 seconds including fresh
schema setup. Five test nodes became one journey; no page case was removed.

Code-generation validation now controls the sampler to verify our alphabet,
length and rehearsal-prefix wiring, instead of assuming 1,000 random samples
never collide. The real-database allocation test still forces a collision and
verifies our retry and reservation behavior. No material timing gain is claimed
for this relevance correction.

CI had three complete baseline executions: standalone lint/validation, coverage
shard one, and image parity. Remove only the first duplicate. The mandatory
PostgreSQL gate still requires the full host baseline's successful execution and
coverage receipt; Compose still runs the complete image baseline and compares
its collection against the host. The lint/drift job retains those separate
responsibilities. Release validation uses the same disposable tmpfs service as
PR coverage, as required by the existing service-consistency test. Neither
application persistence tests nor deployment storage use this optimization.
