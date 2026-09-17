# Daily Administrator digests

## Scope and prerequisites

This coherent Phase 4 increment starts on `pr/stewardship-daily-digests` from
PR #46's verified merge `849cc71f`. It implements
[BG-07.02](../tasks/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests)
and its daily-specific BG-07.05 tests under the
[controlling plan](../plans/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests).
The authoritative behavior is the
[daily digest contract](../specs/stewardship/background-processing/spec.md#daily-campaign-digest),
[report parity](../specs/stewardship/reports/spec.md#daily-email-report-parity),
and [immutable input model](../specs/stewardship/data/spec.md#campaign-daily-report-facts).
Weekly digests, full post-close resolution, the complete Phase 5 report UI and
Gate 3 are not completed by this increment.

## Internal acceptance checkpoints

1. Produce accessible daily/recovery content and a compiled inline chart from
   the same immutable participation document and statistics library as reports.
   Add reusable in-memory inline-image MIME support without enabling arbitrary
   file attachments or image markup in Family receipt templates.
2. Freeze coherent report observations and exact fact-build inputs under durable
   digest ownership. Integrate source/fact pins, exact-generation recovery and
   compaction protection without consuming interactive debounce revisions.
3. Materialize bounded ordinary/recovery work through existing schedule and
   immutable replacement lineage. Never release a truncated missed-day group.
   Route separate messages to current exact-address Admins, enforcing Testing,
   current authority, pause, restore and post-close gates.
4. Reuse fenced provider-attempt ownership, truthful uncertainty and explicit
   recovery. Aggregate completion cannot claim success while any required
   recipient result remains unresolved. Preserve pinned report-link parity and
   integrate Testing cleanup and Admin observability.
5. Verify pure calculations/MIME, real-role PostgreSQL admission and races,
   fake-provider commit order, responsive/accessibility behavior, current
   fresh-install schema and container isolation. Run full coverage and three
   successful dual-source review/fix rounds, exact-head CI/DCO and the normal
   protected merge queue before advancing.

## Execution evidence

Implementation is in progress. The checkpoints above are targets, not completion
claims; BG-07.02 remains unchecked. No provider credentials, real delivery,
deployment, release or retained database deletion is authorized.

### Rendering checkpoint

The detached daily/recovery renderer reuses participation charts and current
statistics, rejects mixed observation cutoffs, and includes an accessible daily
table and protected snapshot-link identity. Shared MIME supports bounded
in-memory raster images without filesystem attachments. Admin digest templates
accept only public campaign substitutions; individual routing preserves the
intended Admin while sending Testing mail only to the configured test address.

Validation: 212 focused report/MIME/statistics/coverage checks and 236
content/routing/editor checks passed. The credential-free baseline passed
6,283 tests, with 4,288 database/browser/runtime cases intentionally skipped
and two existing Redis-client deprecation warnings, in 58.67 seconds. Ruff
check and format validation passed. Durable ownership, the linked report route,
worker scheduling/delivery, integration validation and review gates are still
in progress; these results do not claim a functioning scheduled digest yet.

### Private transport checkpoint

The one-Admin chart adapter now shares the established SMTP and private-pipe
transport while retaining a distinct closed payload. It permits only the
compiled inline PNG, validates HTML and image bounds, and preserves unknown
acceptance without automatic resend. Family input restrictions and the smaller
Family/readiness transport limits remain unchanged.

Validation: 252 focused digest/Family/transport/report checks passed, including
the actual isolated helper with deliberately invalid synthetic credentials and
no Django configuration. The full credential-free baseline passed 6,345 tests
with 4,288 profile skips and the same two deprecation warnings in 58.58 seconds.
This adapter has no scheduled execution authority yet; database ownership and
runtime integration remain the next checkpoint.

### Durable input and complete-coverage checkpoint

Daily preparation now has an opaque task root and bounded, fenced discovery and
coverage pages. Its first executed page freezes the recovery cutoff, so a delayed
queued hint includes intervening missed days without allowing subsequent pages
to chase an endlessly growing range. SQL independently rejects premature date
exhaustion, incomplete coverage, stale claims and phase jumps. Ordinary recovery
retains earlier aggregates through immutable occurrence and replacement edges;
Testing dates cannot enter the default Production coverage reader.

The worker captures one coherent statistics observation and its source pin in
the same transaction, then uses the shared exact fact builder without consuming
an interactive debounce window. Compaction respects the retained input even
before a fact pin exists. Compilation retains the exact chart bytes, content,
Admin selection and fact pin atomically before exposing the fanout phase. A
failed older task does not permanently prevent new daily obligations.

Validation: 29 PostgreSQL schema/ownership/capture/build checks and 47 existing
catch-up, exact-export, fact-recovery and recurring-schedule regressions passed.
Five additional recovery tests passed for delayed first execution, raw shortcut
denial, exact SQL fencing, nested aggregate lineage and new work after failure.
The credential-free baseline passed 6,345 tests with 4,305 profile skips and the
same two deprecation warnings in 59.47 seconds. Django's model-state check
reported no changes. This remains an internal checkpoint: production scheduler
registration, recipient fanout/dispatch, aggregate delivery resolution, the
authorized report route, Testing cleanup and the final review gates are not yet
complete. BG-07.02 remains unchecked; no scheduled digest delivery is claimed.

#### Fresh-install schema audit

Independent empty PostgreSQL installations compared the committed `849cc71f`
baseline with this checkpoint. No preexisting relation, column, constraint,
index, trigger or row policy changed or disappeared. Four new ownership tables
add 60 columns, 83 constraints, 24 indexes, 12 functions and 11 triggers.
Five existing function bodies change only to admit the fenced daily owner,
protect its exact inputs, and permit terminal non-interactive generation
recovery. Their actual installed definitions were compared before updating the
strict catalog fingerprint. Model declarations match the installed schema.
No retained development database was upgraded, reset or deleted.

### Testing cleanup checkpoint

The closed cleanup inventory now includes Testing digest recipients, compiled
content, private observations and their fact/source pins. Dependency-aware
batches remove child records first and delete an observation with its source
pin as an indivisible two-record unit. Shared live report facts are not Testing
data and remain available to their other owners. Direct deletion still fails;
only the journaled cleanup checkpoint can remove its exact inventoried records.
Opaque preparation history remains so stale workers can cancel safely after
the private data and selected occurrence have gone. Activation independently
rejects retained Testing observations.

Validation: 13 real PostgreSQL digest/batch checks, 37 existing inventory,
request, worker and bounded-scan regressions, and 30 schema/planning/recovery
checks passed. The credential-free baseline passed 6,345 tests with 4,311 profile
skips and the same two deprecation warnings in 60.05 seconds. Django reports no
model state changes; Ruff check and format validation passed. This remains an
internal checkpoint, not scheduled delivery completion.

An independent fresh-install comparison against `a7002ed` found only the expanded
cleanup-category check, one new private immutability function, six changed
function bodies and four added/three changed cleanup triggers. All relations,
columns, indexes, row policies, owners and grants remain unchanged. The actual
installed function deltas were inspected before updating the catalog fixture;
existing development databases were preserved.

### Preparation and recipient-intent checkpoint

The provider-free handler now advances the entire finite preparation under a
maintained task lease. Shared-generation waits retain the original observation;
fact unavailability retries within the task budget. Individual Admin outbox,
render, task and recipient records commit as one unit in bounded pages. A
deferred SQL check rejects messages without their recipient owner. SQL also
rejects detached sender, recipient, Testing route and required report content.
Retries preserve previous recipient bindings and rendered messages.

Unrelated configuration edits no longer strand existing work: preparation
follows its unchanged campaign dates/timezone and schedule revision, while
capture uses the current configuration once and subsequently keeps its original
facts. Public campaign substitutions are shared with Family mail without
changing the latter's private placeholders or credential behavior.

Validation: 13 focused worker/fanout checks, 10 worker/configuration/retry-order
checks, 31 existing Family preparation/guard regressions and 17 schema checks
passed. The credential-free baseline passed 6,345 tests with 4,326 profile skips
and two existing warnings in 60.83 seconds. Ruff and Django model-state checks
passed. Independent fresh schemas against `46063a3` show only two added
functions, five changed function bodies and one new deferred constraint/trigger;
all other catalog objects and grants remain unchanged.

These handlers are not registered for runtime execution yet. Multi-Admin
schedule reconciliation, accepted-delivery coverage, provider dispatch,
authorized report links, Admin retry UI and final validation/reviews remain
required before BG-07.02 can be marked complete or this PR can be delivered.
