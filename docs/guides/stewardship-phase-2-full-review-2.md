# Phase 2 full review: round 2

[Acceptance index](stewardship-phase-2-acceptance.md) ·
[Review ledger](stewardship-phase-2-reviews.md) ·
[Round 1](stewardship-phase-2-full-review-1.md)

## Reviewed input and delivery

Both vendors reviewed `58e7a8c8e99d1ff82af8e6a796cd5fac1b6d566a` against
`48be3666f0c89cc15586cb67465cd1ba0504203c`. Pika session
`20260913-002359-25ff68` finalized with COMMENT: 23 validated Medium findings,
22 Claude-only and one Codex-only; no High/Critical findings. There were no
failed agents, verdict mismatches, or degraded vendor results. All 39 generated
Claude shards delivered; pika owned the single detached Codex reviewer.

The local runner lost twelve early Claude process handles. Read-only process
checks confirmed those processes had ended without output; the same manifest
entries were restarted in bounded batches. This was tool-capacity recovery,
not additional reviewers or permission bypass. Every generated output and
completion marker was verified before finalization. The final artifact SHA-256
is `6a2b8a97cac16022b5a544be050d4f92079918ad38716dc3c3e0c0372e75fcbe`.

## Dispositions

All findings are retained. Corrections are implemented; final integrated
validation remains open. Numbering follows triage order: Claude, then Codex.

| ID | Retained correction | Current evidence |
| --- | --- | --- |
| 1 | Limit the two-hour watchdog to loading, not later mail/Slack checks | Migration 0093 and aged-load regressions added |
| 2 | Prove chair-view grants rather than non-updatable-view errors | Exact privilege/SQLSTATE assertions added |
| 3 | Do not accept NOT NULL errors as completion authorization proof | Exact SQLSTATE assertions added |
| 4 | Do not accept foreign-key errors as disposal ownership proof | Exact SQLSTATE assertions added |
| 5 | Separate queue publication from consumer QoS keys | Real Valkey/Kombu suite passes |
| 6 | Cover held initial-setup scheduler production | Pure held/happy-path tests pass |
| 7 | Avoid truncating a 64-bit transaction ID to `xid` | Migration 0094 uses the established epoch-safe comparison |
| 8 | Replace contradictory BG-01/BG-05 checkpoint prose with current status | Task summaries rewritten; history linked |
| 9 | Validate Slack/Workspace candidate shape before sealing | Local intake checks and realistic synthetic fixtures added |
| 10 | Settle cancellation during setup mail credential waits | Fresh queued/live checks and cancellation regression added |
| 11 | Complete Gmail's SASL error challenge | Closed 334/empty-response/535 tests pass |
| 12 | Bound abandoned initial-load retries | Five-attempt recovery/backoff tests pass |
| 13 | Audit HTML task reports only after rendering and final authorization | Response-boundary checks and regressions added |
| 14 | Explicitly mutate preseeded bootstrap sentinel attribution | Fixture no longer relies on `get_or_create` defaults |
| 15 | Recreate initial consumers after cancellation removes provider files | Runbook/UI guidance and Compose abort scenario added |
| 16 | Bind fact-pin release to its actual parent | Explicit parent checks and wrong-parent regression added |
| 17 | Classify original-setup cancellation before credential publication/receive | Fresh owning admission and cancellation regressions added |
| 18 | Use bounded transient retry/backoff for final setup | Five-attempt policy; source contention remains retryable |
| 19 | Avoid the global work lock for count-only presence polling | Named roster retains lock; count regression asserts no advisory lock |
| 20 | Separate local admission errors from provider DTO errors | Typed local boundary; pure and real-transport regressions added |
| 21 | Explain recovery from permanent initial-load failure | Progress page directs cancellation, correction and new login |
| 22 | Recheck all initial credential receipts/ACKs at atomic completion | Migration 0095, narrow metadata reads and lost-ACK regressions added |
| 23 | Require a clean integrated PostgreSQL coverage run | Passed: 2,091 PostgreSQL cases; later round-3 changes require their own full rerun |

Gmail's empty error continuation follows the
[official XOAUTH2 protocol](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol).
Tests use synthetic credentials and fake provider responses, not live services.

## Validation in progress

The round-1 full PostgreSQL coverage run finished: 2,066 passed and one failed
in 3,000.46 seconds, with 90% combined coverage. The dashboard used 67 queries
against its unchanged 64-query budget. Removing an unnecessary final work lock
and redundant read-only savepoints fixes that regression; both reference-load
performance cases pass in the focused rerun. Source changes after round-2
finalization still require another full run.

The first round-2 baseline diagnostic reported 4,183 passes, three fixture
failures, 2,576 explicit profile skips and two existing warnings. Those three
fixtures lacked the newly required attempt/effect metadata; their corrected
44-case rerun passes. The next complete baseline passes 4,210 tests with 2,581
explicit profile skips and two existing warnings in 33.15 seconds. The fresh
PostgreSQL run remains required.
Selected provider/process checks pass 104 cases; setup retry/classification
checks pass 46; broker/ACL checks pass 35, including actual Valkey and Kombu.

The changed PostgreSQL regressions pass 220 cases in 516.31 seconds. A later
47-case run passes in 341.07 seconds, including immutable preview-schema retries,
cadence policy provenance and final credential-state serialization. The added
content/projection/recovery tests pass all 17 cases in 12.23 seconds. All 447
browser checks pass in 445.56 seconds. Ruff, formatting, Markdown and model-drift
checks pass. These focused results do not replace the final full coverage run.

The first all-profile Docker rerun passed six existing scenarios; both new abort
cases completed rollback and recreation but failed the test's final, incorrect
web-only health probe. It now uses the same installer-style liveness check as
the actual worker/mail topology. The rebuilt-image rerun is still in progress.
These are chronological diagnostic results, not the latest phase status. The
subsequent complete round-2 coverage run passed 2,091 PostgreSQL cases in
3,149.09 seconds, with 93.09% line and 81.44% branch coverage; its baseline
passed 4,210 cases. The [third review](stewardship-phase-2-full-review-3.md)
has now completed with both vendors and no High/Critical findings. Its new
corrections require final integrated validation. No PR or phase-exit approval
is implied by this record.

## Acceptance gap found during corrections

The BG-05 audit found that the nightly scheduler supported a default time but
the Admin editor and closed configuration schema could not change it. The
ParishSoft integration editor now exposes parish-local `HH:MM`, default `02:00`.
New `source-cadence-v8` configuration and explicit ordinary, recovery and
credential-selection request schemas preserve historical parser behavior and
all content, campaign and authorization provenance guards. Credential validation
still binds organization alone, not scheduling metadata. Migration 0096 extends
every exact normalized projection and refuses downgrade with retained cadence
history. Pure schema/form checks, actual Admin-to-scheduler PostgreSQL behavior,
credential-selection replay across upgrades, retained content/Ministry
projections, and additive offline recovery pass. Full acceptance remains open.
