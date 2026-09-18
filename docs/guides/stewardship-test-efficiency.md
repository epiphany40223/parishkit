# Stewardship test efficiency

## Execution policy

Use focused tests while implementing or correcting a finding. Commit and push
coherent checkpoints early; let draft-PR CI run the complete acceptance suite
while peer reviews proceed. Do not routinely repeat the same full suite locally
and in CI. Required exact-head and coverage checks remain mandatory. Following
the September 17 human decision, use protected auto-merge without a merge queue
or its duplicate CI run. A superseded PR run is cancelled; main runs retain
independent concurrency groups. The review gates are unchanged. This follows
[GitHub's workflow concurrency contract](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency).

This removes a second execution of the same suite, not an identical validation
context: the queue formerly retested against its then-current merge base. PR
checks use [GitHub's test merge](https://github.com/actions/checkout#checkout-pull-request-head-commit-instead-of-merge-commit)
when checking out a `pull_request` event, but do
not automatically rerun for every later change to `main`. The repository does
not currently require branches to be up to date. Post-merge main CI is the final
landed-result check; concurrent independent PRs can therefore introduce an
integration regression after their individual checks passed. Keep stewardship
increments serial and inspect base drift before merging; refresh and revalidate
when intervening changes affect the increment. Do not represent auto-merge as
restoring the removed queue guarantee.

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

### Compatible database fixture reuse

The next bounded maintenance increment reuses expensive setup only where the
application scenarios require the same starting state:

- All 26 financial guard cases call ParishKit's immutable SQL scalar/JSON
  function without inserting application rows. They now use rollback isolation
  rather than a committed-test flush. Expected SQL errors remain inside their
  own savepoints. A focused marker-only comparison took 15.07 seconds with
  flushes versus 9.61 seconds with rollback, including fresh schema creation;
  this is a local diagnostic, not a full-suite speedup claim.
- The 15 member field reconstruction comparisons now read one immutable source
  snapshot in a single test. Every field still compares SQL and Python source
  values and availability, with the field name included on failure.
- Four ministry authority fixture groups retain all 14 forged aggregate cases.
  Eleven cases share identical campaign prerequisites; the other three retain
  independent setup. Every rejected write still runs with the real web role in
  a separate genuine transaction, checking that no submission or ministry
  request survived. A final valid submission in each group proves failed
  attempts did not poison the baseline or session.

No shared mutable fixture crosses independent tests, no SQL authorization or
deferred-constraint semantics are weakened, and no input cases are dropped.
This maintenance does not complete additional feature tasks or release Gate 3.

After rebasing onto PR #48's verified merge, the three affected PostgreSQL
modules passed all 81 collected tests in 65.04 seconds. Grouped case counts are
separate from collected test nodes; all original fault/field inputs are retained.

### Avoid large rejected-statement dumps

In PR #48's final run `35287511489`, shard three spent 235.08 seconds between
the runner's PostgreSQL `docker logs` command and container removal. Its log
contained individual rejected SQL statements of 8,389,913 and 1,049,469 bytes.
Shard four's corresponding interval was about 0.09 seconds with no comparable
large statement. These timestamps identify log copying, not database shutdown,
as the observed teardown bottleneck; they are not a promise of a fixed speedup.

Set only `log_min_error_statement=panic` through `POSTGRES_INITDB_ARGS` in the
disposable PR and release validation services. PostgreSQL's
[logging contract](https://www.postgresql.org/docs/18/runtime-config-logging.html#GUC-LOG-MIN-ERROR-STATEMENT)
separates this from error-message filtering: error messages, details, hints and
contexts remain at their defaults. Do not change `log_min_messages`, error
verbosity, transaction durability or deployment configuration. Pytest retains
its assertion tracebacks and client-side errors. The existing exact-service
parity test also checks this bounded setting. A one-time pinned-image probe
verified initdb accepts the setting and error messages still reach server logs;
do not add recurring tests of PostgreSQL's own logging implementation.

## Maintenance review ledger

### Round one

Session `20260917-201132-a6800c` reviewed `32d9006` against merged PR #48's
`8998542`. Both vendors completed without degradation; Codex reported no
findings. Claude reported eleven raw findings: five Medium and six Low, with
no High or Critical. All five validated Medium concerns were dispositioned:

| Concern | Disposition |
| --- | --- |
| Intro specification still requires queue CI | Updated its authoritative CI description and linked this execution policy. |
| Fault closure depends on rebinding the setup variable | Separate explicit setup selector and per-case fault argument; bind each validator using `partial`. |
| Failed rejection does not name its fault | Explicit named failure if SQL accepts the forged aggregate. Keep fail-fast after an unexpectedly committed write: continuing from a consumed baseline would manufacture misleading later results. Every successful run still executes all cases. |
| Fixture selection depends on tuple ordering | Parametrize the prerequisite selector alongside each fault tuple; selecting setup no longer depends on the first input. |
| Queue removal obscures merged-result tradeoff | Document current non-strict protection and possible concurrent-base drift. Correct the review's stronger claim: PR checkout already tests a merge result, but not every later base revision. Main CI remains. |

The four grouped authority tests pass in 18.73 seconds after these corrections,
retaining all fourteen rejected inputs and four positive controls. Ruff and
changed-document lint pass. The log-copy reduction was committed after this
round's fixed snapshot and belongs to round two's review scope. The remaining
review rounds and exact-head CI are still required before merge.
