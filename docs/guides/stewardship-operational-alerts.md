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
