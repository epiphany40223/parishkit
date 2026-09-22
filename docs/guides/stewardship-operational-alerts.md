# Stewardship operational alerts

This increment owns the operational-notification portion of
[BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown),
following the [Phase 4 order](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications).
Its source contracts are [critical errors](../specs/stewardship/background-processing/spec.md#critical-errors-and-notification)
and [operational routing](../specs/stewardship/background-processing/spec.md#mode-routing).
The branch starts at verified PR #49 merge `70797cb2`.

## Scope and boundaries

Deliver a coherent path from bounded incident observations through durable
deduplication, escalation/recovery and per-Admin email notification. Reuse the
existing auth incident producer, task/outbox journals,
exact-role service boundaries and isolated provider transports. Do not create
a parallel authentication detector or a caller-controlled operational exemption.

Operational notifications bypass campaign Testing rerouting, not authorization
or durable delivery evidence. Current exact Admin recipients must be rechecked;
Staff and Ministry leaders do not become operational recipients by implication.
Fixed content identifies deployment mode and excludes Family/Member information,
credentials, free-text submissions and access links. Email delivery failure must
not recursively allocate email-failure notifications. Unknown external outcomes
must not become automatic resends.

Audit existing stop-event, bounded-drain, lease and recovery behavior before
adding shutdown mechanisms or marking BG-10.04/.05 complete. Later publication,
backup, restore and purge producers remain with their owning phases; defining
a closed incident kind does not enable those workflows. No historical upgrade
compatibility or retained-development-database changes belong here.

At the email-dispatch checkpoint, the increment is split at its independently
testable incident-to-email boundary to keep review cost bounded. Optional Slack
has a closed transport and current-channel reader here, but no runtime submission
owner. The next increment must complete Slack outcome ownership, remaining
current-phase health producers and notification/shutdown acceptance before
advancing to ADM-05 or Gate 3. This is a delivery boundary, not removal of any
BG-10 acceptance criterion; all BG-10 task checkboxes remain open.

Gate 3 has not passed. Use fake/disposable transports only; no real provider
delivery, deployment or release is authorized by this increment.

## Checkpoints

1. Define the closed incident taxonomy and typed, fixed-content alert compiler.
2. Implement durable incident/occurrence ownership, suppression/escalation and
   recovery with strict SQL admission and fresh-install schema evidence.
3. Bind current Admin recipients, scheduled preparation and
   independently authorized delivery with explicit outcome/retry handling.
4. Integrate existing critical-log/auth producers and email delivery fault tests;
   retain the explicitly deferred Slack/health/shutdown handoff above.
5. Finish at least three dual-source review/fix rounds and exact-head CI, then
   deliver through the protected auto-merge cycle.

## Execution evidence

The first checkpoint provides a closed incident taxonomy, validated non-personal
facts and fixed subject/HTML/plaintext alternatives. Mode and recovery are
explicit, counts use USA grouping, and timestamps are labelled UTC. UTC timezone
objects returned by database adapters normalize without changing presentation.
At this checkpoint the compiler was not yet connected to a dispatch owner;
the subsequent email-dispatch checkpoint below supplies that boundary.

All 42 focused compiler checks pass in 0.06 seconds; Ruff check/format pass.
No database schema or grants have changed at this checkpoint. Durable storage,
recipient selection, routing, transports and producer/shutdown integration are
not yet complete. No BG-10 task or gate is marked complete by these unit tests.

The pure transition checkpoint adds bounded repeat suppression, immediate
severity escalation, sustained-warning escalation and one recovery notice for
previously notified incidents. Resolved history is never reopened; a recurrence
starts a new episode. Counters saturate without stopping notification work.
Owning producers must resolve healthy conditions: elapsed time alone does not
prove a warning continued through an unobserved interval. The durable owner must
supply database time, serialize the episode and persist the transition together
with its notification occurrence; these functions grant no delivery authority.

The compiler and transition checks together pass 74 tests in 0.26 seconds without
database startup. Policy defaults are 900 seconds for repeat suppression and
sustained-warning escalation, bounded to 60–86,400 seconds; configuration wiring
remains in progress. Database ownership and provider integration remain open.

## Durable episode checkpoint

The fresh-install baseline now includes operational episodes and immutable notice
occurrences. One active episode per closed incident kind is serialized across
processes. SQL computes counts, severity, observation time and resolution, and
creates each notice in the same transaction. Notice failure rolls back the
episode change. Recovery never reopens history; the next failure starts a new
episode. The general worker may submit bounded observations but cannot edit
derived state, rewrite pinned policy, insert notices or delete history. Other
runtime identities receive no incident mutation grant.

The application entry point has no caller-clock parameter. Controlled fixture
insertion can represent an earlier finite observation for timing tests; ordinary
observation/recovery uses the database statement clock. The producer still owns
exactly-once consumption of its input; replaying a source event must not inflate
occurrence counts. Producer integration remains unfinished.

An independent fresh installation of predecessor `70797cb2` matched its retained
fingerprint. Comparison with the new baseline found only the two owned tables:
28 columns, 42 constraints, seven indexes, five functions and five triggers were
added. No existing object changed or disappeared, and row policies are unchanged.
Model-contract validation found literal-cast and expression-grouping differences
in five new constraints; these were corrected to the model-compiled definitions
before the final fingerprint was accepted. Retained development databases were
not upgraded, reset or deleted.

Validation passes 68 PostgreSQL checks in 20.18 seconds, including incident
behavior, the actual worker/denied-role grants, concurrent absent-row creation,
atomic rollback, full fresh catalog/model equivalence and immutable/version
guards. The configuration/compiler/policy suite passes 189 checks in 0.35 seconds.
No delivery consumer or provider authority is enabled by this checkpoint.

## Operational policy configuration

These technical settings live in deployment YAML, not campaign templates:

```yaml
deployment:
  operational_alerts:
    suppression_seconds: 900
    escalation_seconds: 900
    source_stale_seconds: 1800
```

Each window is bounded to 60–86,400 seconds. Services read the policy from
their rendered configuration, which provisioning writes from the deployment
YAML, so set it before provisioning: `retarget-image` treats a later change
as a changed deployment input and refuses it, and before the schema freeze
such a change is taken by reinstalling. Environment/explicit deployment
overrides use `PARISHKIT_STEWARDSHIP_OPERATIONAL_SUPPRESSION_SECONDS`,
`PARISHKIT_STEWARDSHIP_OPERATIONAL_ESCALATION_SECONDS` and
`PARISHKIT_STEWARDSHIP_OPERATIONAL_SOURCE_STALE_SECONDS`, with the existing
explicit-over-environment-over-YAML precedence. Values are read at process
startup. The producer pins that policy when opening an episode; changing
deployment configuration affects new episodes, not recorded decisions or an
already-active episode. Web/operator startup now wires the validated policy for
the incident producers. The source freshness threshold instead applies at each
sample; changing it cannot resolve an existing incident without a new successful
source observation. See [source health](stewardship-source-health.md) for initial
grace and full-refresh recovery evidence. These settings never authorize recipients
or external I/O.

## Private transport checkpoint

Operational mail and Slack now have separate closed private-pipe schemas. Both
accept only typed incident facts and rebuild fixed content inside their isolated
helper. They have no body/template/attachment/access-link input. Each email has
exactly one recipient; Slack disables markup and previews. Existing SMTP
acceptance/uncertainty and bounded Slack POST behavior are reused without
widening the Family, digest or fictional readiness payload types. Provider
uncertainty remains unknown, not an automatic retry decision.

The new transports plus existing Slack/weekly regressions pass 129 checks in
1.55 seconds, including synthetic real-helper invocation with invalid credentials
that cannot contact providers. Durable recipient selection, submission admission,
outcome reconciliation and producer/runtime integration are still unfinished.

Draft PR #50 runs full CI alongside implementation. Its first checkpoint exposed
the new SQL file missing from both explicit Docker build-context allowlists;
both were corrected without broadening the context. All 41 focused build-contract
checks pass in 0.21 seconds. That failed CI run is not acceptance evidence; the
corrected head must pass CI before this draft can be delivered.

## Critical producer intake checkpoint

The maintained general worker now consumes bounded pages of immutable CRITICAL
logs, including events created directly by SQL safeguards. Exact log receipts
commit with incident observations and notices under the live Task fence. There
is no timestamp watermark: late commits with older timestamps remain eligible.
Failure rolls back the whole page without losing its independently durable
source logs. Scheduler production is independent of campaign/configuration
holds and grants no provider authority.

Authentication incidents use their existing distributed detector and window
deduplication. Critical observations and limiter recovery atomically create the
same durable notice intents; isolated warning windows do not fabricate sustained
abuse. The web role receives only bounded incident-observation authority, not
notice writes or provider credentials. Deployment policy is wired at startup.
The obsolete unreleased `notification_pending` placeholder is removed in favor
of actual notice records. No retained database was altered or deleted.

Independent catalog comparison against `70797cb2` found the three owned tables
and their guards, plus only the intended removal of that placeholder column and
its NOT NULL constraint. Existing objects and row policies otherwise match.
All 60 focused PostgreSQL checks pass in 34.67 seconds with one shared fresh
database setup. Runtime, configuration and transport regressions pass 429 checks
in 1.64 seconds; Ruff check/format pass. Actual recipient fanout, submission and
outcome recovery are still unfinished, and no BG-10 checkbox is complete.

Current recipient/content selection also passes three restricted-role PostgreSQL
checks in 11.19 seconds, without a campaign or live providers. These verify exact
Admin grants, Staff/Ministry/domain exclusion, Testing and restore exemptions,
revocation, optional independent Slack routing and file/SQL coherence. The mail
role can read the non-personal incident/notice facts, not mutate them. These
readers are not send permits: durable fanout and submission admission remain open.

## Durable recipient preparation checkpoint

Each notice now receives one immutable exact-Admin cohort and bounded pages of
individual outbox intents. Cohort capture, each child Task/render and its recipient
receipt are guarded by the real preparation claim and commit atomically. Restart
reuses retained recipient identities. Initial setup without routing leaves notices
pending rather than consuming a retry budget. Preparation does not call providers
or claim that an email was delivered.

SQL independently compares fixed operational content, exact recipients, captured
configuration and mode. An injected body or missing recipient receipt rolls back
the page. Contract checks compare every closed kind and recovery content with the
application compiler using one shared database setup. Dispatch will replace the
captured-mode rendering with current-mode content under current Admin authority;
the outbox's allocation-mode identity itself remains historical and immutable.

All 40 focused PostgreSQL checks pass in 43.77 seconds, including existing Family
mail preparation and complete fresh catalog/model agreement. Runtime and grant
regressions pass 247 checks in 1.04 seconds. The independent fresh baseline audit
contains five new operational tables and owned helpers; the only existing
function change admits the new exact preparation owner. The prior auth-placeholder
removal remains the only removed column/constraint; row policies are unchanged.
Model equivalence caught and corrected a literal-cast difference in the new mode
constraint before accepting the fingerprint. No development database was changed.

Provider submission, Slack outcome ownership, remaining health producers and
shutdown acceptance are still open. The draft is not ready for final reviews or
merge, and BG-10/Gate 3 remain incomplete.

## Operational email dispatch checkpoint

The shared outbox Task now selects independently admitted campaign and operational
owners from stored identity. Operational MAIL rechecks the complete cohort,
current exact Admin, coherent configuration and installed Workspace fingerprint;
it commits an attempt before using the closed private helper. Current-mode fixed
content is enforced independently in SQL. Provider outcomes and abandoned-attempt
uncertainty use the existing numbered outbox journal; no unknown result permits
automatic resend. In-flight observations remain valid after mode/grant changes.

Definitive failures and exhausted preparation create fixed ERROR logs from their
immutable evidence, not recursive CRITICAL email events. This uses guarded SQL
triggers rather than granting MAIL arbitrary log insertion. A focused systemic-
failure test caught that permission distinction before this checkpoint committed.
The triggers return before delivery lookups for unrelated progress/heartbeat work.

The operational plus existing Family dispatch suite passes 23 PostgreSQL checks
in 56.67 seconds. The expanded operational/schema suite passes 24 in 28.66 seconds;
the final logging-trigger optimization and fresh fingerprint pass three targeted
checks in 12.62 seconds. Runtime/private-transport regressions pass 277 checks in
1.35 seconds. The independent fresh audit adds only operational helpers/triggers
and the reviewed owner/result dispatch branches; no existing table or row policy
changed at this checkpoint. Slack outcome ownership, other producer integration,
shutdown acceptance, three dual-source review rounds and final CI remain open.

## Focused test timing checkpoint

CI run `35298891939` measured 105.47 seconds for the final-setup installer
contention case and 51.48 seconds for cancellation between provider pages.
These tests use synthetic provider exchanges but were waiting for production
request, safety and retry budgets. Their shared setup helper now supplies a
short actual request timeout, one-second additional safety margin and retry
delay. The unchanged transport still reserves its full five-second forced drain;
real database time, advisory locks, lease deadlines and admission checks remain.
Production defaults and transport contract tests are unaffected.

All 16 focused final-setup tests pass in 135.07 seconds with one database
bootstrap. The two cases above take 18.90 and 9.23 seconds locally. These local
and CI measurements are not a controlled machine-to-machine benchmark; the
next full CI run must establish the resulting shard improvement.

CI run `35300838349` subsequently passed all checks on `c9fd22f`. Its corresponding
two test calls took 19.12 and 8.33 seconds, and shard 6 test time fell from 716.11
to 553.91 seconds. The slowest PostgreSQL job still took 18m06s, so this is a
measured targeted improvement, not completion of the overall CI-speed work.

## Review round 1 correction record

Dual-source review `20260917-225013-916ecb` reviewed `70797cb2..c9fd22f`, with
three Claude shards and one Pika-owned Codex reviewer. Both sources completed
without degradation. Raw severities were two HIGH, seven MEDIUM and 16 LOW;
Pika produced eight validated entries by merging two distinct issues at one
location. Corrections treat those as distinct findings rather than losing either.
Corrections pass all 60 focused PostgreSQL checks in 44.20 seconds and 228
runtime/circuit regressions in 0.99 seconds. Ruff and Markdown lint pass. A fresh
independent catalog comparison against the previous checkpoint changes only the
incident state, operational dispatch and delivery-error function bodies; every
other catalog object is identical. The checked-in fingerprint and full model
contract pass. This completes round 1; round 2 reviews the correction delta.

- Accepted HIGH: operational provider trip held new work forever. The operational
  consumer now opts into a five-minute cooldown; campaign circuit behavior and
  in-flight drainage are unchanged. Tests cover systemic and repeated temporary
  outages followed by recovery.
- Accepted MEDIUM within the merged entry: terminal incomplete fanout stranded
  children. Exact unsent children now cancel with `preparation_failed`, with SQL
  independently proving the latest parent's terminal state and incomplete cohort.
  Parent exhaustion and child cancellation create fixed ERROR records, not alerts.
- Accepted HIGH: enqueue/claim during authority mismatch exhausted preparation.
  Production now waits for coherence; hint/claim repeat that check, and a later
  capture race records a held retry excluded from the failure budget.
- Accepted MEDIUM: page capture needlessly reselected routing. Existing cohorts
  now retain their captured recipients without reading new routing; compiled
  admission still verifies coherence before executing a page.
- Accepted MEDIUM: absent email settings could exhaust a live intent. Channel
  absence holds claims; loss after claim uses the existing held-retry journal.
  Tests remove and restore the actual configured channel without spending attempts.
- Accepted MEDIUM: web could write worker-owned incident kinds. SQL now restricts
  that identity to the four authentication kinds on both INSERT and UPDATE.
- Partly accepted MEDIUM: intake needs routable Admins. The metadata predicate
  now checks exact Admin existence and capture races become holds. Domain-wide
  Admins and email records missing mandatory sender/reply-to are already rejected
  by policy/configuration validation; no invalid-configuration fallback is added.
- Rejected MEDIUM (false positive): unknown log events poisoning the collector.
  `schema/tables.sql` already has `operational_event_safe`, whose closed values
  match `Event`; an attempted unknown SQL insert fails before collection. No
  obsolete-schema/enum-rename compatibility is required in this fresh baseline.
- Deferred MEDIUM to the explicitly scoped next health-producer increment:
  healthy-window resolution for `admin_abuse`, `family_abuse` and
  `limiter_state_lost`, plus repeated observations for sustained limiter outage.
  Only `limiter_unavailable` currently supplies an observed recovery signal.
  Elapsed silence must not manufacture recovery. Complete these before BG-10
  or Gate 3, alongside the deferred Slack/shutdown acceptance.
- Fixed LOW: pin only the collection test's scheduler-minute bucket so a minute
  rollover cannot spuriously fail idempotence. Real database/incident clocks stay
  unchanged. Other LOW suggestions remain non-blocking; provider uncertainty
  already creates the shared durable WARNING, invoker-only helper ACLs follow
  existing ownership conventions, and full producer-volume tuning belongs to
  the later performance acceptance. No HIGH/MEDIUM issue is silently downgraded.

## Review round 2 correction record

Dual-source review `20260917-231657-031dfd` reviewed `c9fd22f..8c62ad2` with
one Claude and one Pika-owned Codex reviewer. Both completed without degradation.
Raw severities were zero HIGH, two MEDIUM and six LOW; both MEDIUM entries
validated. The dispositions below preserve the bounded provider policy rather
than replacing admission with an effectful half-open reservation.

- Fixed MEDIUM: authority mismatch escaped fanout hint admission as `ConfigError`
  and aborted the scheduler's entire mixed page. Hint/claim now return a held
  admission result, while effect-time races still become journaled held retries.
  A real-role mixed scheduler test proves the collector's hint is published and
  the held preparation consumes no attempt.
- Rejected MEDIUM (false positive): the claim that a cooldown admits every queued
  alert simultaneously and exhausts them after ten minutes does not match the
  maintained solo, concurrency-one consumer. Each subsequent send rechecks the
  shared circuit; denied hints consume neither Task nor provider attempts. Actual
  provider attempts remain bounded, and definitive systemic nonacceptance remains
  a durable terminal failure with an ERROR record, as designed. New PostgreSQL
  tests cover both systemic and repeated temporary outages, seven denied hints
  with zero attempts, and successful later delivery after a simulated hour.
  Only the process cooldown clock and external SMTP are replaced; SQL clocks,
  live role admission, Task execution and durable outcomes are real.
- Fixed LOW: combine repeated cohort and parent-state reads into one metadata
  query, retaining the separate mandatory lock-order proof. The real-role test
  asserts the resulting two-query budget.
- Fixed LOW: cover channel removal after an existing MAIL claim as well as before
  claim. The maintained execution journals a held retry without consuming a
  preparation or provider attempt; restoring the channel admits waiting work.
- Clarified LOW: Gate 3 has not passed. Slack runtime ownership, remaining health
  producers and shutdown acceptance still have their explicit next-increment
  owner. Invalid/missing routing remains a hold; elapsed silence is not recovery.

Post-fix validation passes all 21 focused PostgreSQL tests in 32.10 seconds,
228 runtime/circuit checks in 1.10 seconds, Ruff and Markdown lint. No SQL catalog
object changed in this round. Full CI run `35302605738` passed all 24 checks plus
DCO on the prior head `8c62ad2`; the correction head requires its own CI. Round 2
is complete; round 3 reviews its correction delta with surrounding context.

## Review round 3 and delivery handoff

Dual-source review `20260917-233552-6256a7` reviewed `8c62ad2..547d2a9` with
one Claude and one Pika-owned Codex reviewer. Both completed without degradation;
raw severities were zero HIGH, zero MEDIUM and two LOW. Finalization approved
with no validated Medium-or-higher findings. Codex's read-only review could not
run pytest because its sandbox had no writable temporary directory; the parent
ran the focused tests recorded above, and full CI remains independently required.

The LOW request for denied-claim coverage is already handled by the neighboring
seven-denied-claims regression; the mixed-scheduler test independently proves the
collector hint survives. Keep both contracts without repeating fixture setup.
The low-confidence raw-SQL table-name suggestion is non-blocking: this repository
uses fixed SQL identifiers for the fresh baseline, with real-role/schema tests
checking the matching model/table/grant contract. No accepted finding remains.

All three review/fix rounds are complete. The final history is consolidated into
eight adjacent logical groups, preserving the final tree exactly; signed-off
history and exact-head CI/DCO must pass before protected merge. The PR records
the final tested SHA, CI and merge receipt; its successor records the verified
main merge. Full BG-10 remains incomplete for the explicitly named Slack,
health-producer and shutdown scope above. No live provider, deployment, release
or Gate 3 approval is implied.

## Protected delivery

[PR #50](https://github.com/epiphany40223/parishkit/pull/50) auto-merged as
`18959490893f60f25648bf580a56b210fdeec999`, verified on refreshed `origin/main`.
Final head `c12d5dc5232db21834b1a5b8c317366a2e2d55d1` passed all 24 CI jobs in
run `35304228883` and DCO. No merge queue or protection bypass was used. The
eight logical signed-off commits retain the exact pre-squash tree. This receipt
supersedes earlier pending-review/CI/merge notes, not the remaining BG-10 scope.

PostgreSQL shard 1 remained the slowest at 18m34s, followed by the 45-second
coverage aggregation. The baseline runs only on shard 1; schema bootstrap is
once per shard. Continue measured fixture/cost improvements without removing
application behavior or permission coverage. The
[remaining operational health increment](stewardship-operational-health.md)
starts from this verified merge; Gate 3 has not passed.
