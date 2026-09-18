# Stewardship operational alerts

This increment owns the operational-notification portion of
[BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown),
following the [Phase 4 order](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications).
Its source contracts are [critical errors](../specs/stewardship/background-processing/spec.md#critical-errors-and-notification)
and [operational routing](../specs/stewardship/background-processing/spec.md#mode-routing).
The branch starts at verified PR #49 merge `70797cb2`.

## Scope and boundaries

Deliver a coherent path from bounded incident observations through durable
deduplication, escalation/recovery and per-Admin email plus optional Slack
notification. Reuse the existing auth incident producer, task/outbox journals,
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

Gate 3 remains closed. Use fake/disposable transports only; no real provider
delivery, deployment or release is authorized by this increment.

## Checkpoints

1. Define the closed incident taxonomy and typed, fixed-content alert compiler.
2. Implement durable incident/occurrence ownership, suppression/escalation and
   recovery with strict SQL admission and fresh-install schema evidence.
3. Bind current Admin recipients and optional Slack, scheduled preparation and
   independently authorized delivery with explicit outcome/retry handling.
4. Integrate existing producers and complete notification/shutdown fault tests.
5. Finish at least three dual-source review/fix rounds and exact-head CI, then
   deliver through the protected auto-merge cycle.

## Execution evidence

The first checkpoint provides a closed incident taxonomy, validated non-personal
facts and fixed subject/HTML/plaintext alternatives. Mode and recovery are
explicit, counts use USA grouping, and timestamps are labelled UTC. UTC timezone
objects returned by database adapters normalize without changing presentation.
This compiler is not a dispatch authorization boundary and currently has no
production consumer.

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
```

Each window is bounded to 60–86,400 seconds. Environment/explicit deployment
overrides use `PARISHKIT_STEWARDSHIP_OPERATIONAL_SUPPRESSION_SECONDS` and
`PARISHKIT_STEWARDSHIP_OPERATIONAL_ESCALATION_SECONDS`, with the existing
explicit-over-environment-over-YAML precedence. Values are read at process
startup. The producer pins that policy when opening an episode; changing
deployment configuration affects new episodes, not recorded decisions or an
already-active episode. Startup/producer wiring remains part of the next
checkpoint. These settings never authorize routing, recipients or external I/O.

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
