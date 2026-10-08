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
not currently require branches to be up to date. Pushes to `main` currently run
only the light `validate` job (a temporary pre-launch measure tracked in
[#158](https://github.com/epiphany40223/parishkit/issues/158)); the full suite
runs on ready PRs and, by default, on a manual `workflow_dispatch`, which a
release requires for its tagged tree (see [release evidence](#release-evidence)).
Until #158 is resolved nothing runs
the full suite on the landed result automatically, so concurrent independent
PRs can introduce an integration regression after their individual checks
passed. Keep stewardship
increments serial and inspect base drift before merging; refresh and revalidate
when intervening changes affect the increment. Do not represent auto-merge as
restoring the removed queue guarantee.

## Path-based job skipping

A full run costs about 280 runner minutes, most of it in the PostgreSQL shards
(see [#158](https://github.com/epiphany40223/parishkit/issues/158) and
[#626](https://github.com/epiphany40223/parishkit/issues/626)). A ready PR, or
a dispatch that explicitly asks for affected jobs, therefore skips each heavy
job group that none of its changed paths can affect. The `validate` job's
"Classify changed paths" step (`parishkit.stewardship.quality_paths`) maps
every changed path to the groups it needs and exports one `true`/`false`
output per group:

| Group | Jobs |
| --- | --- |
| `postgresql` | database shards (and the combined coverage gate) |
| `browser` | browser engines |
| `compose` | compose-core |
| `operational` | operational scenarios |

| Changed path | Groups that run |
| --- | --- |
| Documentation (see below) | none, `validate` only |
| Templates under `src/` | `postgresql`, `browser`, `compose` |
| Static assets under `src/` | `browser`, `compose` |
| Python the browser tests can reach (see below) | all |
| Other application Python and `tests/stewardship/*.py` | all but `browser` |
| Browser tests (`tests/stewardship/browser/`) | `browser`, `compose`, `operational` |
| Database tests (`tests/stewardship/database/`) | `postgresql`, `compose`, `operational` |
| `tools/`, `scripts/`, `tests/test_*.py` | `compose`, `operational` |
| CI, deployment and test infrastructure (see below) | all |
| Anything else | all |

Documentation means `docs/`, `AGENTS.md`, `CLAUDE.md`, `LICENSE`,
`.pymarkdown.json` and `scripts/*/README.md`. CI, deployment and test
infrastructure means `.github/`, `deploy/` (including the Dockerfile),
`tools/ci-pip-install.sh`, settings, schema, migrations, the `quality*.py` CI
tooling and the shared conftests. "Anything else" includes requirements,
`pyproject.toml`, `README.md` (an image input) and any new top-level path.

A mixed change runs the union. Any path the development `tests` service
bind-mounts also runs `compose`: compose-core runs the complete suite inside
the image over those mounts (all of `tests/`, selected scripts and tools, the
stewardship specs, plans and development docs, and the workflows) and checks
that the host and image collect the same tests. The classifier reads the
mounts from `deploy/stewardship/compose.development.yaml`; if it cannot,
compose-core runs. Files a database test reads from the checkout (the
`DATABASE_INPUTS` patterns: everything under `tools/stewardship-ops/` and the
[mail send report](stewardship-mail-send-report.md) guide) also run
`postgresql`.

"Reach" is the static import closure of the browser tests, the conftests
pytest loads for them, and Django's implicit entry points: settings, template
tag libraries and each app's `apps`, `models` and `admin` modules. Dotted
`parishkit.…` strings (settings entries, `include()` targets) count as
imports. The closure is computed from the checkout, so it cannot drift. It
covers most application modules, because the browser tests import forms,
views and helpers directly. A parse failure or a deleted module runs the
engines.

An empty or failed diff, a git query past its one-minute limit (logged), a
pull request checkout that is not GitHub's two-parent test merge, `main`
pushes and every default dispatch run all groups. A false positive only costs
runner time; a false negative could merge an untested change, so new rules
must err toward running. `tests/stewardship/test_quality_paths.py` pins the
rules, the set of tracked top-level entries (a new one fails until its rules
are decided), and the database and browser tests and shared helpers that
read files outside `src/` and `tests/`.

Several non-database tests read documentation, workflows and deployment files,
and shard one normally runs that complete non-database suite. When the
`postgresql` group is skipped, `validate` runs it instead ("Complete
non-database suite"). That step cannot use `--require-no-skips`: on the host,
the browser, container and other environment-gated tests skip by design,
which is why mounted documentation keeps compose-core rather than relying on
`validate` alone.

### Dispatched runs

`gh workflow run ci.yml --ref BRANCH` runs every job: the `jobs` input
defaults to `all`, which is what release and merge evidence needs. Add
`-f jobs=affected` to apply the path rules to the branch's changes since its
merge base with `main`. Because the delivery cycle keeps PRs in draft and
merges on a dispatched run, this is where path selection saves most of its
runner time. If fetching `main` fails, the classifier finds no merge base and
runs everything.

A dispatched run is named `CI (jobs: all)` or `CI (jobs: affected)`. An
`affected` dispatch of a commit that is already on `main` when the run starts
has an empty diff and runs everything, but a run made before the commit
reached `main` may have skipped groups. `release.yml` and `release.sh`
therefore accept only a run named exactly `CI (jobs: all)` as release
evidence, and `release.sh` dispatches with `-f jobs=all`.

### Database test selection

When an `affected` dispatch runs the `postgresql` group, it also narrows the
group to the tests that can observe the change
([#858](https://github.com/epiphany40223/parishkit/issues/858)). Almost all
database tests exercise application behavior, so a schema-only rule would
skip too little, and every test imports enough of the package that a static
import closure selects almost nothing out. The selection uses measured
coverage instead (`parishkit.stewardship.quality_select`):

- Every run that measures coverage records per-test contexts
  (`--cov-context=test`). The PostgreSQL gate turns them into a map from each
  `src/` file to the database tests that execute it and keeps it as the
  `stewardship-test-map` artifact.
- The `validate` job's "Select database tests" step takes the map from the
  newest successful `CI (jobs: all)` run of `main` or a `train/*` branch,
  fetches that run's commit and diffs it against the dispatched head. The
  diff starts at the map's commit, not the merge base, so changes `main` made
  after the map are selected too.
- The run keeps the tests whose recorded files changed, every test in a
  changed database test file or in any database test file that imports one
  (directly or through helper modules anywhere under `tests/stewardship`,
  from a static parse of the imports),
  and every test the map does not know (new tests). The shard jobs partition
  only that set and print how many tests the selection ran and skipped, in
  the log and the job summary.
- Tests marked `sql_rules` only assert that the database refuses something:
  a trigger, guard or grant. The marker documents which tests are pure
  database-rule checks; it rarely skips anything. Such a test still runs
  whenever the map or a changed file selects it, and the marker only keeps
  out a `sql_rules` test the map does not know that sits outside every
  changed file, unless the change touches the grants registry or
  provisioning (`src/*grants*.py`, `src/*provisioning*.py`). Schema and
  migration changes already run the whole group.

The whole group runs, unselected, whenever the selection cannot be trusted:
no usable map (none found, an expired artifact, an unfetchable commit, a
different schema or partition count), an empty or failed diff, any path the
path rules always run everything for (schema, migrations, settings, CI
tooling and workflows, the top-level conftests), the shared database
builders and conftests, templates and other non-Python inputs coverage
cannot see, and any Python file the map has no record of (including test
helpers outside `src/`), any test module whose importers cannot be traced,
and any mapped Python file whose change coverage cannot credit to every test
that depends on it. Coverage records code that runs once per process only
under the first test that triggers it, or under no test at all during
import, so an AST comparison of the map's version with the checkout's runs
the whole group for a change outside function bodies (module and class
statements, decorators, signatures and defaults), to a function decorated
with `cache`, `lru_cache` or `cached_property`, to a `ready()` method, or to
a function that module or class code of the same file calls at import,
directly, through the file's other functions or as a decorator factory (for
example `admin_cli.py`'s `COMMANDS = COMMANDS + _read_specs() + ...`).
Every git and gh call has a one-minute limit and the whole selection a
six-minute budget, inside the step's ten minutes; a kill or a spent budget
is logged with its limit and elapsed time and runs the whole group, as does
a failure of the step itself (its output is then empty).

Known limits: a selection trusts that a test which executed none of a
changed function's lines cannot observe the change. Code an ordinary
function runs only once per process, for example a function another file's
import-time code calls, a session-scoped fixture or a hand-written memo, is
credited only to the first test that ran it (or to none); code run in a subprocess (such as the upgrade-parity test's) carries no
test context at all. Full runs still execute every test, so such a miss
surfaces at the next train or release run, never in release evidence.

Coverage is evaluated only for full runs and release evidence, so an
`affected` dispatch runs its shards without coverage and the gate skips the
combined report. Selected or unmeasured partition evidence says so in its
receipt, and `quality_ci combine` refuses it, so it can never become full-run
evidence. A map that cannot be built is logged and skipped, never failing a
full run; later `affected` runs then fall back to the whole group. `tests/stewardship/test_quality_select.py` covers every selection
rule and fallback.

### Release evidence

A release needs a successful `CI (jobs: all)` dispatch on a commit whose tree
is identical to the tagged commit's, or differs only in docs-safe paths
([#662](https://github.com/epiphany40223/parishkit/issues/662)). That commit
need not be an ancestor of the tag: after a nightly train head passes one full
run and its PRs merge into `main` in the same order, `main` has the same tree
under a new SHA, and the release reuses the train's run instead of paying for
the whole suite again. A documentation change merged after a fully tested
commit is reused the same way.

[`release_evidence.py`](../../tools/stewardship-ops/release_evidence.py) holds
the rule and its closed allowlist: Markdown under `docs/` (except the
send-report guide, whose SQL `send-report.sh` runs) and the root `AGENTS.md`
and `CLAUDE.md`. `README.md` is not docs-safe, because the image and the
package metadata include it, and neither is anything under `.github/`,
`tools/`, `src/`, `tests/`, `deploy/` or `requirements/`. Only the newest full
run on each commit counts, so a later failed or cancelled run withdraws an
earlier success, and the newest run whose commit qualifies decides: it must
have passed (a pending run is waited for). Whether a run's commit can be read
is decided by the remote (the commit is fetched by SHA into an empty
repository), never by a local checkout, so `release.sh` and `release.yml`'s
fresh clone choose the same run. A newer full run whose commit cannot be
fetched is refused rather than skipped, since it might be a newer failure.
`release.yml` applies the rule on every tag push, independently of
`release.sh`, and names the evidence run and commit in the GitHub release
notes. `release.sh` reuses a qualifying run and dispatches a new full run only
when none passed or is still running.

Documentation is still a test input. When the evidence tree differs from the
tagged one, `release.yml` runs Markdown lint over every tracked Markdown file
and the documentation-reading tests (`release_evidence.py docs-tests` lists
them: `test_traceability.py`, `test_build.py`, `test_ops_scripts.py` and
`test_local_script.py`, no database) on the tagged
tree before building, and the release notes name those checks. `release.sh`
runs the same checks on `main`'s head in a temporary worktree before it tags,
so a failure refuses before the tag is pushed. For that it needs
`STEWARDSHIP_PYTHON` to be a development environment that can import
`django`, `pymarkdown` (from `pymarkdownlnt`), `pytest` and `pytest_django`,
such as a virtual environment with `requirements.txt` installed as CI installs
it; it checks this before watching the CI run and refuses without them.

For a nightly train:

- Include the version-bump PR in the train. A bump merged after the train's
  full run changes `pyproject.toml`, which is not docs-safe, so the release
  would need another full run.
- Keep the train branch until the release run has passed. `release.yml`
  fetches the evidence commit by SHA, and a deleted branch's head may no
  longer be served.

### Protected gates

The protected gates (`stewardship-postgresql`, `stewardship-browser`,
`stewardship-compose`) pass when each of their jobs succeeded or was skipped
on purpose: successful preflight, a ready (non-draft) `pull_request` or an
`affected` dispatch, that job's group classified `false`, and the job
`skipped`. `stewardship-compose` judges compose-core and the operational
scenarios separately, so a template change can run one and skip the other.
Drafts, failed or skipped preflight, missing classification, default
dispatches, failures and cancellations still fail. The PostgreSQL gate
combines coverage, and keeps the database test map, only when the shards ran
and measured every test, never on an `affected` dispatch.
`tests/stewardship/test_quality_paths.py` executes each gate's complete truth
table.

The branch ruleset's required status checks must name only jobs that report on
every ready PR. A skipped job reports success to required checks, so required
matrix jobs still pass on an intentional skip, but renaming a matrix entry
leaves its old required context unreported and blocks every PR until the
ruleset is updated. Update the ruleset in the same change that renames a
required job or matrix value.

## PostgreSQL partition packing

After launch, several PRs' CI runs overlap, so runner slots and runner-minutes
limit throughput more than one run's wall time
([#625](https://github.com/epiphany40223/parishkit/issues/625)). The account
runs at most 20 jobs at once. In the 2026-10-04/05 baseline the 14 PostgreSQL
partition jobs took a median 13.2 minutes each (maximum 18.9), about 65% of a
full run's runner-minutes, and a full run needed more slots than a second run
could find, so overlapping runs queued serially.

Each `stewardship-postgresql-shard` job therefore runs `PARTITIONS_PER_JOB` (in
`parishkit.stewardship.quality_ci`) partitions at once with `quality_ci job`.
Every partition is still an ordinary `quality_ci shard` child with its own
receipt, `--require-no-skips`,
[deadline](stewardship-database-tests.md#parallel-ci-and-live-progress),
`partition-N` output directory and pytest temporary root. Each slot has its own
PostgreSQL/Valkey service pair on its own ports (SQL roles are cluster-wide, so
partitions never share a cluster); Valkey skips port 56380, which the
unavailable-service tests keep closed. Child output is relayed live with a
`[partition N]` prefix, so a job killed at its time limit still shows each
partition's last `CI_PROGRESS` record. A job fails if any of its partitions
fails, after all of them finish; if the job is interrupted, or a partition
cannot start, the partitions already running are stopped. The gate merges every
job's artifact into one directory and combines coverage exactly as before; the
path-selection skips above are unchanged.

The packing is three per job, the smallest that frees at least eight slots:
five jobs instead of fourteen frees nine per full run, while two per job would
free only seven. A standard hosted runner for a public repository has 4 vCPUs
and 16 GB. The partitions spend much of their time waiting on PostgreSQL and on
real lease, drain and deadline waits, so three rarely need more than three
cores at once, leaving one for the PostgreSQL backends. Three 2 GiB tmpfs
clusters and three pytest processes stay well inside 16 GB; four would
oversubscribe the CPUs whenever the partitions are all busy. The first job
takes the short remainder (two partitions): partition one also runs the
CPU-heavy non-database baseline, so it is often the longest. The partition
deadline and job limit were raised to absorb sharing the runner, not to permit
a slower suite.

The expected cost is roughly 100 runner-minutes for the PostgreSQL group
instead of about 185, with a lone run's PostgreSQL wall time a few minutes
longer. Measure before claiming either: first with the introducing PR's own
dispatched `CI (jobs: all)` run (the five jobs' times and runner-minutes, and
partition one's margin to its deadline), then by comparing a dispatched `CI
(jobs: all)` run on an otherwise idle account, and the runner-minutes of a week
of ordinary PR runs, against the #625 baseline. To retune, change
`PARTITIONS_PER_JOB`; `tests/stewardship/test_quality_ci.py` then names the job
matrix and the `postgres-N`/`valkey-N` service pairs `ci.yml` must declare.

## Transient infrastructure failures

Hosted runners occasionally fail a dependency install with "no matching
distribution" for a pin that exists on PyPI, or with "Connection broken:
IncompleteRead". Every CI pip install therefore goes through
`tools/ci-pip-install.sh`, which adds pip's own `--retries 5 --timeout 60` and
retries the whole install up to three times, 20 seconds apart. A genuine
resolution conflict fails every attempt and still fails the job. Changing the
wrapper runs every job group, like changing `ci.yml`.

Browser assertions that follow a navigation, a disclosure or a script render
use the auto-waiting `visible()` helper in `tests/stewardship/browser/waits.py`
(Playwright's `expect(...).to_be_visible()`), not an immediate
`assert locator.is_visible()`, which raced on slower engines. A check that an
element is hidden right after a click, fill, clock advance or script update
uses the matching `hidden()` helper (`expect(...).to_be_hidden()`), because
the script that hides it can lag the same way. Initial-state checks right
after a navigation, and negative checks that prove an element never shows,
stay immediate.

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

### Round two

Session `20260917-202005-b4ad42` reviewed `bca0d68` against `32d9006`, including
the log-copy reduction and every first-round correction. Both vendors completed
without degradation; Codex reported no findings. Claude reported eight raw
findings: one Medium and seven Low, with no High or Critical.

The one validated Medium concern was missing per-fault attribution when a
grouped attempt raises an unexpected exception. Add an exception note naming
that input and re-raise the original exception unchanged, preserving its type
and traceback. Expected SQL rejection and named unexpected-commit failures keep
their existing paths. No dependency or runtime behavior changed. The third
review round and final exact-head CI remain required.

### Round three and protected delivery

Session `20260917-202912-9484a0` reviewed `c7513a0` against `bca0d68`. Both
vendors completed without degradation, with no validated findings and no High
or Critical issues. Claude reported two raw Low items, one outside the changed
file set; Codex reported no findings. The corrected four grouped authority tests
passed in 17.77 seconds. All three review/fix rounds are complete and every
accepted Medium-or-higher issue is resolved.

[PR #49](https://github.com/epiphany40223/parishkit/pull/49) auto-merged at
`70797cb2531062be298c94647253c960138a073d` on September 18, 2026 at 00:49:59 UTC,
verified on refreshed `origin/main`. Exact reviewed head `c7513a0` passed all
24 jobs in run `35291396014` and DCO, without a merge-queue run. Combined
coverage is unchanged at 30,443/32,387 lines (94.00%) and 8,554/10,040 branches
(85.20%). No complete local acceptance suite was duplicated.

In that run, shard-three teardown took one second rather than the earlier
236 seconds. Its full log shrank from 9,931,809 to 391,522 bytes, retaining
normal error details. The slowest PostgreSQL job still took 19 minutes and
26 seconds: this fixes specific overhead, not the overall database-test
bottleneck. Further profiling and compatible grouping remain appropriate; no
coverage or test gate was waived. No deployment or release occurred.
