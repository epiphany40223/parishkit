# Administration portal tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/admin-portal.md) ·
[Normative specification](../../specs/stewardship/admin-portal/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

Phase 2 results at `ea2d5cb` below are pre-consolidation evidence, retained on
the named backup branch. Follow the
[consolidation record](../../guides/stewardship-phase-2-simplification.md) for
current baseline/review/CI acceptance; old counts do not certify the current tree.

## ADM-01: Login, denial, and unconfigured-state routing

Scope and dependencies: [ADM-01 work package](../../plans/stewardship/admin-portal.md#adm-01-login-denial-and-unconfigured-state-routing).

- [x] ADM-01.01 — Build Google login, callback, logout, and denial pages.
- [x] ADM-01.02 — Gate all Admin routes on current authorization and state.
- [x] ADM-01.03 — Handle unconfigured and maintenance states.
- [x] ADM-01.04 — Audit login, logout, timeout, and revocation.
- [x] ADM-01.05 — Test direct routes and partial endpoints.

Evidence: Phase 1B implements Google-only initiation/callback, CSRF logout,
retryable denial, current-role checks and durable login/session audit. Real
PostgreSQL and synthetic signed Google tests cover normal and denied claims,
revocation, cookie isolation, expiry and recovery boundaries. Setup/maintenance
routing and direct HTML/POST admission pass ten additional PostgreSQL cases.
The actual wizard and its durable configured marker stay with ADM-02; absent
marker providers fail closed. See [Phase 1B evidence](../../guides/stewardship-phase-1b.md).

## ADM-02: Bootstrap command and transactional setup wizard

Scope and dependencies: [ADM-02 work package](../../plans/stewardship/admin-portal.md#adm-02-bootstrap-command-and-transactional-setup-wizard).

- [x] ADM-02.01 — Implement bootstrap configuration and every required keyring.
- [x] ADM-02.02 — Implement isolated temporary setup staging.
- [x] ADM-02.03 — Implement heartbeat-aware setup progress and watchdog.
- [x] ADM-02.04 — Expire aborted staging and reject late worker completion.
- [x] ADM-02.05 — Finalize secrets, YAML, source, and campaign setup coherently.
- [x] ADM-02.06 — Test wizard aborts, concurrency, and installer recovery.

Evidence: Implemented and accepted locally at `ea2d5cb`, including all three
full-phase review/fix rounds. Bootstrap/keyring preparation, original-login
temporary wizard
staging, isolated credential intake, logo/content/schedule previews, source
progress, explicit email/optional Slack checks, consumer installation/ACKs and
final confirmation are connected. Atomic completion publishes fresh source,
Family codes, prepared YAML/database configuration, the first Testing draft and
the configured marker together. Cancellation/expiry scrubs temporary artifacts;
the owner-approved journaled rollback applies only before activation, never to
an applied configuration. Real restricted PostgreSQL, three-engine browser and
both complete initial-setup Compose profiles exercise these paths. The
[integrated acceptance index](../../guides/stewardship-phase-2-acceptance.md)
tracks the current validation and remaining gate work; the
[review ledger](../../guides/stewardship-phase-2-reviews.md) preserves historical
checkpoints. PR CI and human merge approval remain required.

## ADM-03: Navigation, dashboard, indicators, and configuration

Scope and dependencies: [ADM-03 work package](../../plans/stewardship/admin-portal.md#adm-03-navigation-dashboard-indicators-and-configuration).

- [x] ADM-03.01 — Build role-filtered navigation and dashboard.
- [x] ADM-03.02 — Build persistent operational-state banners.
- [x] ADM-03.03 — Build presence and background-task indicators.
- [x] ADM-03.04 — Build durable configuration, Ministry activity, and credential editors.
- [x] ADM-03.05 — Keep Parish-timezone edits prospective.
- [x] ADM-03.06 — Build branding previews and variant handling.
- [x] ADM-03.07 — Test configuration UI, Ministry activity, concurrency, and timezone isolation.

Evidence: Implemented with passing integrated acceptance and review closure. Navigation,
Testing/operational banners, passive presence/task indicators, task history,
Parish/Ministry/integration editors and sealed credential replacement use current
role checks, exact previews and durable installer receipts. Parish-timezone
edits leave existing campaign timezones unchanged. Logo variants, immutable
branding and bounded cleanup preserve retained references. Explicit readiness
delivery and wizard finalization are implemented under ADM-02/ADM-04, not missing
ADM-03 dependencies. Real PostgreSQL grants/concurrency cases and three-engine
browser cases are indexed in
[Phase 2 acceptance](../../guides/stewardship-phase-2-acceptance.md); current
[review corrections](../../guides/stewardship-phase-2-reviews.md) include
audit timing, passive polling, provider classification and scoped media cleanup.

## ADM-04: Campaign editor, content, schedules, and previews

Scope and dependencies: [ADM-04 work package](../../plans/stewardship/admin-portal.md#adm-04-campaign-editor-content-schedules-and-previews).

- [x] ADM-04.01 — Build guarded campaign creation and cloning.
- [x] ADM-04.02 — Build structural campaign configuration forms.
- [x] ADM-04.03 — Build named content and email-template editors.
- [x] ADM-04.04 — Build page previews and readiness-test emails.
- [x] ADM-04.05 — Build atomic schedule reconciliation previews.
- [x] ADM-04.06 — Test campaign editing, previews, and schedule races.

Evidence: Implemented with passing integrated acceptance and review closure. Guarded
draft creation, archived cloning, structural forms, current-source Ministry/fund
selection, named rich-text/page/email editing and atomic schedule reconciliation
use exact previews and immutable Applied receipts. Retained page previews keep
the selected campaign's Parish/branding configuration. Applied-template test
mail uses fictional content, Testing routing, the compiled isolated consumer
and non-retrying uncertain-delivery recovery. PostgreSQL race/grant tests and
three-engine browser tests cover these boundaries; see
[Phase 2 acceptance](../../guides/stewardship-phase-2-acceptance.md) and the
[review ledger](../../guides/stewardship-phase-2-reviews.md) for evidence.
Production transitions and live campaign delivery remain with their later
phase owners, not this preparation package.

## ADM-05: Production transition and pre-start withdrawal

Scope and dependencies: [ADM-05 work package](../../plans/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal).

- [ ] ADM-05.01 — Build go-live readiness and exact impact preview.
- [ ] ADM-05.02 — Build transition requests, rehearsal invalidation, and cleanup controls.
- [ ] ADM-05.03 — Build atomic Production confirmation with asynchronous catch-up progress.
- [ ] ADM-05.04 — Build guarded pre-start withdrawal.
- [ ] ADM-05.05 — Test cleanup, readiness, and campaign-boundary races.

Evidence: In progress on `pr/stewardship-go-live-readiness` from verified PR #57
merge `17f5f2fc`. The [increment guide](../../guides/stewardship-go-live-readiness.md)
owns current readiness/impact and guarded cleanup controls. Activation,
withdrawal, full handoff/load acceptance and Gate 3 remain later checkpoints;
no ADM-05 task is complete yet.

## ADM-06: Restore release, delivery pause, reopen, and archive

Scope and dependencies: [ADM-06 work package](../../plans/stewardship/admin-portal.md#adm-06-restore-release-delivery-pause-reopen-and-archive).

- [ ] ADM-06.01 — Build state-aware restore inventory, fresh-token preparation, and release.
- [ ] ADM-06.02 — Build delivery pause, resume, and post-close resolution.
- [ ] ADM-06.03 — Build staged token preparation and guarded campaign reopen.
- [ ] ADM-06.04 — Build archive, unarchive, Return, and obligation resolution.
- [ ] ADM-06.05 — Test lifecycle races and durable post-close coverage.

Evidence: Not started.

## ADM-07: User rules and Ministry assignments

Scope and dependencies: [ADM-07 work package](../../plans/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments).

- [ ] ADM-07.01 — Build login-rule tables with serialized autosave and conflict recovery.
- [ ] ADM-07.02 — Implement low-friction role edits and high-impact notifications.
- [ ] ADM-07.03 — Build chair suggestions, grant provenance, and inherited-role review.
- [ ] ADM-07.04 — Build manual assignment and runtime-suspension review.
- [ ] ADM-07.05 — Test rapid autosave, uncertain outcomes, precedence, and policy races.

Evidence: Not started.

## ADM-08: Manual refresh, follow-up queues, and logs

Scope and dependencies: [ADM-08 work package](../../plans/stewardship/admin-portal.md#adm-08-manual-refresh-follow-up-queues-and-logs).

- [ ] ADM-08.01 — Build coalesced manual refresh controls.
- [ ] ADM-08.02 — Build additional-information and manual-census queues.
- [ ] ADM-08.03 — Build scoped Ministry follow-up controls.
- [ ] ADM-08.04 — Build searchable Admin logs and timezone-aware exports.
- [ ] ADM-08.05 — Test workflow history, scope, and concurrency.

Evidence: Not started.

## ADM-09: Census review and ParishSoft publication UI

Scope and dependencies: [ADM-09 work package](../../plans/stewardship/admin-portal.md#adm-09-census-review-and-parishsoft-publication-ui).

- [ ] ADM-09.01 — Build bulk proposal review, edit, accept, and ignore.
- [ ] ADM-09.02 — Separate review decisions from publication execution.
- [ ] ADM-09.03 — Build source preflight, conflict resolution, and progress.
- [ ] ADM-09.04 — Preserve original submitted values beside Admin edits.
- [ ] ADM-09.05 — Test publication authorization, subsets, and stale plans.

Evidence: Not started.

## ADM-10: Exceptional campaign purge web workflow

Scope and dependencies: [ADM-10 work package](../../plans/stewardship/admin-portal.md#adm-10-exceptional-campaign-purge-web-workflow).

- [ ] ADM-10.01 — Build purge eligibility and durable request UI.
- [ ] ADM-10.02 — Build campaign quiescence and conflicting-work resolution.
- [ ] ADM-10.03 — Build inventory, backup creation/revalidation, and recovery-evidence controls.
- [ ] ADM-10.04 — Build fresh-authentication and typed purge confirmation.
- [ ] ADM-10.05 — Build reader-drain status, pre-delete recovery, deletion retry, and terminal UI.
- [ ] ADM-10.06 — Test every purge state, race, and confirmation boundary.

Evidence: Not started.
