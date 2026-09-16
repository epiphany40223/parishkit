# Stewardship report consumer increment

[Controlling sequence](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
· [Report tasks](../tasks/stewardship/reports.md)
· [Calculation contract](../specs/stewardship/reports/spec.md#participation-fact-materialization)

## Scope and boundary

Branch `pr/stewardship-report-selection` starts from PR #41's verified merge
`2eade2a53163d621ade3145ede092f7316055bda`. Its exact-head CI run `35141293392`
and protected merge-group run `35143121665` both passed all 24 jobs; DCO passed.

This prerequisite consumer increment integrates authorized current/stale
participation selection with the existing read-only campaign guard, shared
export document loading, and drift verification/compaction protection. These
are one demonstrable report-consumption boundary, not a new UI or an independent
calculation engine. Splitting it from queued exact-input requests keeps the
read-lifetime locking review separate from task ownership and retry policy.

Queued exact-input requests, pre-execution input pins, claim priority and
scheduled verification remain the following increment before BG-07. RPT-02's
remaining statistics/comparison services also remain prerequisites. Interactive
HTML/chart controls and the complete report authorization/accessibility matrix
remain Phase 5; periodic compaction remains OPS-07. No RPT-03 task is marked
complete by this partial consumer integration. Gate 3 remains closed.

## Consumer contract

- `reports/selection.py` authorizes current Admin/Staff policy inside a bounded
  `CampaignReadGuard`. Ministry leaders cannot read parish-wide participation
  or financial data. The caller supplies the real response-abort hook and keeps
  rendering inside the context; this service exposes no HTTP route yet.
- Expected campaign configuration, promoted source, maximum live submission
  sequence and selection instant come from one SQL statement. Campaign-local
  midnight and the selected campaign's immutable timezone define the through
  date. Testing responses cannot advance the expected live cutoff.
- Selection prefers an exact ready generation, then this campaign/scope's
  published pointer. Every fallback retains its own source-as-of, configuration,
  submission cutoff and date metadata, with `updating` status. No selection is
  `unavailable`, never fabricated zero values. A scope cannot borrow another
  scope's graph. A generation removed before lock acquisition is not silently
  replaced by an arbitrary latest row.
- `reports/documents.py` is shared with actual queued export rendering, so its
  complete immutable series and calculation metadata are identical for equal
  inputs. Request-time context (Parish branding, requested-at and browser zone)
  may differ; retained exports keep their original request context.
  Selecting a report does not allocate work or mutate its interactive pointer.
  Export allocation separately pins the immutable chosen generation.
- Generation reads hold shared transaction advisory locks in namespace
  `736231`, keyed by PostgreSQL's hash of the canonical generation UUID.
  Hash collisions can delay optional cleanup or briefly make an unrelated
  generation busy/retryable. The compactor skips a locked
  generation; direct SQL day/header deletion must obtain the exclusive lock.
  Readers also use a nonblocking acquisition: a generation already held by
  cleanup raises `FactBusy` (a `FactUnavailable` subclass), not an unexpected
  database timeout. Cleanup acquires its advisory lock only after source/row/
  disposability skip checks. Every skipped candidate rolls back its own
  savepoint to release acquired row locks before the batch continues; successful
  deletions still commit atomically with the whole batch.
  This replaces row locking, which PostgreSQL forbids in the campaign guard's
  `READ ONLY` transaction, without making that transaction writable.
- Current-scope verification checks the generation's durable source pin. SQL
  already forbids releasing it while the generation exists, so protected fact
  readers do not need a second source row lock to retain its membership data.
  Historical verification continues to use permanent provenance.

## Validation and review checkpoint

Implementation, local validation and three dual-source review/fix rounds are
complete. Protected PR/merge-group delivery is not yet claimed.
Initial focused validation: 56 PostgreSQL tests passed, including selection under
the actual web role, shared export document parity, both verification scopes in
read-only guards, and existing worker/export/retention behavior.

At implementation commit `78ae496`, the full baseline passed 6,039 tests
(4,127 profile skips, two existing client deprecation warnings); all eight
database shards passed all 3,283 tests, with 94.00% scoped line coverage and
85.19% branch coverage. Shard durations were approximately 10–12 minutes.
The expanded pre-review focused checks also passed (34 role/race/worker tests
and 17 selection checks). Ruff, formatting, Markdown and model drift passed.

## Review round 1

Pika session `20260916-162214-abdefc`, full diff `2eade2a5..78ae496`, completed
both reviewers without degradation. Raw findings: two Medium, seven Low;
no High/Critical. All dispositions below are complete. Post-fix focused
validation passed 43 PostgreSQL tests (36.84s), plus Ruff, formatting, Markdown
and model drift checks. No SQL body changed after the independent audit.

- Codex Medium, busy reader timeout: fixed with a nonblocking shared advisory
  acquisition returning `FactUnavailable`. The actual read-only race now proves
  failure before cleanup is allowed to commit, then unavailability afterward.
- Claude Medium, producer/selector input drift: added actual-database parity
  tests for both scopes before/after a live submission. Both services use the
  existing shared campaign SQL clock (the fixture can freeze that same clock).
  Separate lock/SQL owners remain intact.
- Claude Low, unnecessary cleanup lock retention: move advisory acquisition
  after all source/row/disposability skips.
- Claude Low, magic namespace: share `FACT_READ_NAMESPACE` between both Python
  paths, retain the reviewed matching SQL guards and cross-path race tests.
- Claude Low, Parish-name parity: clarify request-context versus calculation
  metadata. Missing system configuration is already denied by campaign
  admission; it is not a successful report context.
- Claude Low, successful fallback/dedup tests: add exact-busy/pointer-success
  and identical-exact/pointer single-attempt coverage.
- Claude Low, web-role branch coverage: execute exact, unavailable and Staff
  selection under actual restricted web credentials, in addition to stale data.
- Claude Low, date-dependent fixture branch: stage one deterministic zero-
  response series unconditionally, including a legitimate empty date range.
- Claude Low, missing current-source pin: add the negative predicate regression
  without bypassing SQL or deleting protected inputs. The function-local guard
  import is consistent with other existing tests; no unrelated import churn.

## Review round 2

Pika session `20260916-163942-a4b288`, correction diff `78ae496..f1930f6`,
completed both reviewers without degradation. Raw findings: seven Low, no
Medium/High/Critical. All dispositions below are complete; post-fix validation
passed 45 PostgreSQL tests (48.87s), Ruff, formatting and Markdown.

- Claude and Codex Low, collision availability: corrected the shared-key
  documentation; collisions may temporarily defer reads as well as cleanup.
- Claude Low, error classification: add `FactBusy` for typed retryable
  contention, preserving existing `FactUnavailable` fallback handling.
- Claude Low, SQL namespace drift test: already covered by direct-SQL day and
  header deletion races. They require a `protected` rejection while the Python
  reader owns the key; a mismatched SQL namespace instead reaches a different
  deferred constraint and fails these tests. A textual function-definition
  assertion would add less behavioral coverage than these existing checks.
- Claude Low, real selection contention: added the complete actual web-role
  selection path while another SQL connection owns the exclusive generation
  key, proving savepoint recovery and labeled pointer fallback.
- Claude Low, parity fixture clock: explicitly freeze the shared campaign clock
  within the test rather than relying only on its outer fixture.
- Codex Low, skipped candidate row locks: give each candidate a savepoint and
  roll it back on every skip. A new race leaves the batch open after a reader-
  conflict skip and proves source/generation rows are immediately lockable.

The Codex reviewer could not run PostgreSQL tests in its read-only sandbox
without a writable temporary directory. This did not degrade its completed
structured review; parent-run PostgreSQL results provide execution evidence.

## Review round 3

Pika session `20260916-164859-e82165`, correction diff `f1930f6..799d0f5`,
completed both reviewers without degradation or verdict mismatch. Codex
approved with zero findings; Claude reported two Low test-strength suggestions,
with no Medium/High/Critical. Both suggestions are implemented:

- After releasing actual exclusive contention, the same web-role selection
  must return the exact generation as current, proving the lock caused fallback.
- A two-candidate compaction batch tests both orders of a protected skip and a
  successful deletion. The skipped generation's row is lockable before batch
  completion, the deletion remains invisible until batch commit, and the
  successful deletion/evidence survive the other candidate's rollback.

Final post-correction validation passed 47 PostgreSQL tests (51.59s), Ruff,
formatting, Markdown and whitespace checks. No accepted Medium-or-higher
finding remains. The baseline at `799d0f5` passed 6,039 tests (4,134 profile
skips, two existing warnings, 60.96s). Third-round corrections change only
regression tests and evidence, not implementation or SQL.

Fresh-install audit on disposable port `55440` independently compared verified
base and current schemas in `stewardship_selection_base_20260916a` and
`stewardship_selection_current_20260916a`. The base matched its committed catalog
fingerprint. Only `stewardship_fact_day_guard()` and
`stewardship_fact_set_guard()` changed; all relations, columns, constraints,
indexes, triggers and policies were identical. No upgrade migration, retained
database conversion or deletion is included.
