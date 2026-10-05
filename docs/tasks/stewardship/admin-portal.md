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

- [x] ADM-05.01 — Build go-live readiness and exact impact preview.
- [x] ADM-05.02 — Build transition requests, rehearsal invalidation, and cleanup controls.
- [x] ADM-05.03 — Build atomic Production confirmation with asynchronous catch-up progress.
- [x] ADM-05.04 — Build guarded pre-start withdrawal.
- [x] ADM-05.05 — Test cleanup, readiness, and campaign-boundary races.

Evidence: ADM-05.01/.02 pass implementation and three completed dual-source
review/fix rounds on `pr/stewardship-go-live-readiness`, based on verified PR #57
merge `17f5f2fc`. The [acceptance map](../../guides/stewardship-go-live-readiness.md#acceptance-and-delivery-boundary)
and review ledger record exact-role, concurrency, cancellation, restore-hold,
browser and fresh-schema evidence. Final-head CI/DCO and protected PR #58 delivery
remain required. Activation, withdrawal, full handoff/load acceptance and Gate 3
remain later checkpoints; the whole ADM-05 package is not complete.

PR #58 has now merged as `47ec3599` after exact-head CI/DCO passed; see its
[protected receipt](../../guides/stewardship-go-live-readiness.md#protected-delivery).
ADM-05.03/.04/.05 continue on the fresh-main
[activation/withdrawal branch](../../guides/stewardship-production-activation.md),
including the missing runtime owner for inactive token preparation.
PR #59's [delivery boundary](../../guides/stewardship-production-activation.md#delivery-boundary)
is the complete inactive-link preparation/progress/retry/disposal workflow.
Final readiness, activation, withdrawal, full load/handoff and boundary-race
acceptance follow on fresh main after that PR's protected delivery. The above
tasks remain unchecked; this review-size split does not reduce their acceptance.

PR #59 merged as `880507fc` after final-head CI/DCO and three dual-source rounds;
see its [protected receipt](../../guides/stewardship-production-activation.md#protected-delivery).
Final confirmation and catch-up acceptance now continue from that verified main
tip on `pr/stewardship-production-confirmation`; withdrawal follows separately.

PR #60 now completes ADM-05.03 implementation and three dual-source review/fix
rounds; its [delivery boundary and acceptance ledger](../../guides/stewardship-production-confirmation.md#delivery-boundary)
record exact-role, replay/race/rollback, bounded-load, outcome-comparison and
browser evidence. Corrected-head CI/DCO and protected merge remain required.
ADM-05.04 and withdrawal/repeated-go-live portions of ADM-05.05 remain open.

PR #60 merged as `6bc3238` after all final-head CI/DCO checks passed; see its
[protected receipt](../../guides/stewardship-production-confirmation.md#protected-delivery).
ADM-05.04/.05 now continue on the fresh-main
[pre-start withdrawal branch](../../guides/stewardship-production-withdrawal.md).

PR #61 completes ADM-05.04/.05 implementation and three completed dual-source
review/fix rounds. Its [acceptance ledger](../../guides/stewardship-production-withdrawal.md#review-round-3-and-acceptance)
records withdrawal/start races, rollback, current authority, cleanup denial,
new go-live evidence, cancelled/failed mail across execution cycles, SQL guards,
browser checks and the retained 5,000-Family performance bounds. All accepted
Medium findings are fixed; the final round has no High/Critical findings.
Exact-head CI/DCO and protected delivery remain required before the fresh-main
ADM-06 delivery-pause increment. Integrated Gate 3 remains after Phase 5.

## ADM-06: Restore release, delivery pause, reopen, and archive

Scope and dependencies: [ADM-06 work package](../../plans/stewardship/admin-portal.md#adm-06-restore-release-delivery-pause-reopen-and-archive).

- [ ] ADM-06.01 — Build state-aware restore inventory, fresh-token preparation, and release.
- [x] ADM-06.02 — Build delivery pause, resume, and post-close resolution.
- [ ] ADM-06.03 — Build staged token preparation and guarded campaign reopen.
- [ ] ADM-06.04 — Build archive, unarchive, Return, and obligation resolution.
- [ ] ADM-06.05 — Test lifecycle races and durable post-close coverage.

Evidence: The [delivery-pause acceptance](../../guides/stewardship-delivery-pause.md#acceptance-and-delivery-boundary)
completes ADM-06.02 implementation, focused actual-role/browser/schema validation
and three dual-source review/fix rounds. Final-head CI/DCO and protected PR #62
delivery remain required. Restore/reopen/archive/Return and their broader race/
unmaterialized-obligation matrix keep .01/.03/.04/.05 open for later owners.

## ADM-07: User rules and Ministry assignments

Scope and dependencies: [ADM-07 work package](../../plans/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments).

- [x] ADM-07.01 — Build login-rule tables with serialized autosave and conflict recovery.
- [x] ADM-07.02 — Implement low-friction role edits and high-impact notifications.
- [x] ADM-07.03 — Build chair suggestions, grant provenance, and inherited-role review.
- [x] ADM-07.04 — Build manual assignment and runtime-suspension review.
- [x] ADM-07.05 — Test rapid autosave, uncertain outcomes, precedence, and policy races.

Evidence: In progress. The
[portal users review increment](../../guides/stewardship-portal-users.md) adds
the read-only Administrator tables of .01 and the provenance and suspension
display of .03 and .04, with address-over-domain precedence from .05. It changes
no rule. The [login rule edit increment](../../guides/stewardship-user-rule-edits.md)
adds the low-friction role edits, rule creation and removal of .02 as reviewed
configuration requests with the last-Administrator guard, provenance-preserving
grants and closed refusals. The
[security event acknowledgement increment](../../guides/stewardship-policy-security-events.md)
adds the dashboard prominence and audited acknowledgement of .02's high-impact
expansions, which the activation trigger already recorded, and the
[security event email increment](../../guides/stewardship-security-event-mail.md)
sends each such expansion to every Administrator who existed before it
through the durable outbox, completing .02's notification, and is delivered
under its
[protected receipt](../../guides/stewardship-security-event-mail.md#protected-delivery).
The [Chairperson suggestions increment](../../guides/stewardship-chair-suggestions.md)
adds the read-only suggestion review of .03, each current Chairperson with the
Member, Ministry, publication flag, current rule and assignments and any
ambiguity, and is delivered under its
[protected receipt](../../guides/stewardship-chair-suggestions.md#protected-delivery).
The [Chairperson confirmation increment](../../guides/stewardship-chair-confirmation.md)
adds the confirmed suggestion request of .03, the sole creator of seeded
rules, grants and assignments, with the selected Member retained as evidence,
and is delivered under its
[protected receipt](../../guides/stewardship-chair-confirmation.md#protected-delivery).
The [Chairperson seed review increment](../../guides/stewardship-chair-review.md)
adds the suspended-assignment review, the restore and removal decisions and
the Keep role independently action of .03 and .04, each an ordinary policy
request, and is delivered under its
[protected receipt](../../guides/stewardship-chair-review.md#protected-delivery).
The [manual assignment editor increment](../../guides/stewardship-assignment-editor.md)
adds the YAML-backed manual assignments of .04 with Ministry names beside
each assignment, and is delivered under its
[protected receipt](../../guides/stewardship-assignment-editor.md#protected-delivery).
The [login-rule autosave queue increment](../../guides/stewardship-rule-autosave.md)
adds the autosave of role checkbox changes of .01 through keyed configuration
requests with the queue, applied-digest adoption, pause and conflict
resolution the specification requires, and is delivered under its
[protected receipt](../../guides/stewardship-rule-autosave.md#protected-delivery).
The [race and exact-once tests increment](../../guides/stewardship-autosave-races.md)
adds the cases of .05 the delivered suites did not yet hold and cites where
the rest live, so .01 to .05 are checked; it is delivered under its
[protected receipt](../../guides/stewardship-autosave-races.md#protected-delivery),
which completes ADM-07.

## ADM-08: Manual refresh, follow-up queues, and logs

Scope and dependencies: [ADM-08 work package](../../plans/stewardship/admin-portal.md#adm-08-manual-refresh-follow-up-queues-and-logs).

- [x] ADM-08.01 — Build coalesced manual refresh controls.
- [ ] ADM-08.02 — Build additional-information and manual-census queues.
- [x] ADM-08.03 — Build scoped Ministry follow-up controls.
- [ ] ADM-08.04 — Build searchable Admin logs and timezone-aware exports.
- [ ] ADM-08.05 — Test workflow history, scope, and concurrency.

Partial evidence: the [Staff follow-up increment](../../guides/stewardship-additional-followup.md)
implements the additional-information portion of .02/.05, with authorization,
history, concurrency and accessible native forms. Three dual-source rounds
pass; protected delivery and the other
ADM-08 workflows remain open; these mixed-scope tasks are not complete.

The [Ministry follow-up increment](../../guides/stewardship-ministry-followup.md)
implements .03 and the Ministry portion of .05: a row-scoped queue with
assignee/status/outcome filters, contact-attempt entry, notes, bulk assignment,
immutable history, optimistic concurrency and links to the Member's authorized
report detail. It was sequenced before RPT-07 so packets read real contact
dates and notes. Three
[review/fix rounds](../../guides/stewardship-ministry-followup-reviews.md#round-3)
are complete; full exact-head CI/DCO and protected delivery remain open. .01, .04 and the remaining scope of .02/.05 are not
complete.

The [system logs increment](../../guides/stewardship-admin-logs.md) adds the
read-only Administrator screen of .04: both sources, five accessible levels with
DEBUG excluded by default, and closed level, source, type, actor, correlation,
campaign and date filters over a stable cursor. .04 stays unchecked until its
text and JSONL export, full-text search, and entity and Ministry filtering land
with RPT-09.

The [manual refresh increment](../../guides/stewardship-manual-refresh.md)
implements .01 under the v1 launch scope: a confirmed, keyed, coalesced
manual full refresh that leads to the run's progress page, with the home
page linking to it; it is delivered under its
[protected receipt](../../guides/stewardship-manual-refresh.md#protected-delivery),
so .01 is checked.

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

## ADM-11: Admin automation interface

Scope and dependencies: [ADM-11 work package](../../plans/stewardship/admin-portal.md#adm-11-admin-automation-interface).
Specification: [Admin automation interface](../../specs/stewardship/admin-automation/spec.md).

- [x] ADM-11.01 — PR 0: specify the Admin automation interface and record the Administrator's decisions.
- [x] ADM-11.02 — PR 1: add the AdminCaller seam with no behavior change.
- [ ] ADM-11.03 — PR 2: add durable browser-approved automation sessions, notices and the maintenance task (migration), the host wrapper and session file, restore revocation, refusals and audit.
- [ ] ADM-11.04 — PR 3: add read models, read-only status commands and the route-parity test.
- [ ] ADM-11.05 — PR 4: add schedule preview and confirm and configuration request status.
- [ ] ADM-11.06 — PR 5: let fresh-gated SQL guards and checks accept full-scope automation sessions (migration) and add the confirmation prompt.
- [ ] ADM-11.07 — PR 6: add refresh and Testing send commands.
- [ ] ADM-11.08 — PR 7: add delivery control and Family portal maintenance commands.
- [ ] ADM-11.09 — PR 8: add report, export, digest and log commands.
- [ ] ADM-11.10 — PR 9: add task retry, delivery and refusal commands.
- [ ] ADM-11.11 — PR 10: add the remaining configuration commands, including secret replacement.
- [ ] ADM-11.12 — PR 11: add user, rule (including high-impact changes), assignment, acknowledgement and follow-up commands.
- [ ] ADM-11.13 — PR 12: add go-live and withdrawal commands.

Evidence: In progress. ADM-11.01 merged in PR #511, which added the
Admin automation interface specification and the Administrator's
decisions. ADM-11.02 adds
`parishkit.stewardship.accounts.admin_caller` and moves
`authenticated_admin`, `require_fresh`, `admit_admin_action`, `_actor` and
`principal` onto the caller with no behavior change, proven by
`tests/stewardship/test_admin_caller.py`,
`tests/stewardship/database/test_admin_caller_postgresql.py` and the
unchanged session, privileged-intake and Admin view suites.
ADM-11.04 lands in two pull requests. 3a adds the read models
(`parishkit.stewardship.admin_reads`), the read-only commands `status`,
`task list`, `task show`, `send progress`, `send history` and
`schedule show`, `--watch` with its heartbeat and timeout, and the
route-parity ledger `parishkit.stewardship.admin_parity`, with the reads
moved out of their views into `admin_dashboard.observe`, `jobs.task_reads`,
`jobs.send_reads`, `accounts.schedule_reads` and `presence.active_count`;
proven by `tests/stewardship/test_admin_reads.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/database/test_admin_status_cli_postgresql.py` and the
unchanged home, Background work, Family email progress and sends, schedule
and presence suites. 3b adds `go-live readiness` and `go-live progress`.

## ADM-12: Admin navigation overhaul

Scope and dependencies: [ADM-12 work package](../../plans/stewardship/admin-portal.md#adm-12-admin-navigation-overhaul).
Each task maps to the row naming it in that package's table, not to a list item.
Specification: [Admin navigation](../../specs/stewardship/admin-portal/spec.md#admin-navigation).

- [x] ADM-12.00 — NAV-0: record the implementation plan's decisions in the specification and add this package and its checklist.
- [ ] ADM-12.01 — NAV-1: gate every Admin and sign-in page on JavaScript (#565).
- [ ] ADM-12.02 — NAV-2: rewrite the navigation registry into the seven menu groups with per-entry capability and reason checks.
- [ ] ADM-12.03 — NAV-2: keep a stable menu shape, with unavailable entries greyed out and their reasons shown on hover, focus and tap.
- [ ] ADM-12.04 — NAV-2: make menu groups collapsible, remembered per browser, and end the menu with Sign out.
- [ ] ADM-12.05 — NAV-3: grey out multi-campaign controls with the #145 tip, refuse their actions on the server, and remove New campaign.
- [ ] ADM-12.06 — NAV-4: give Campaign setup and Mail pages one name each, including Cancel go-live.
- [ ] ADM-12.07 — NAV-5a: give Parish data, Users and access, System and Home pages one name each.
- [ ] ADM-12.08 — NAV-5b: give report pages one name each.
- [ ] ADM-12.09 — NAV-6: add the URL plumbing, per-group URL modules, legacy redirects and System URLs.
- [ ] ADM-12.10 — NAV-7: move Parish data and Users URLs and the change status URL.
- [ ] ADM-12.11 — NAV-8: move Mail and Family portal URLs.
- [ ] ADM-12.12 — NAV-9: move Campaign setup URLs, part A, and add the group root.
- [ ] ADM-12.13 — NAV-10: move Campaign setup URLs, part B (go-live chain, test email, Campaign Ministries).
- [ ] ADM-12.14 — NAV-11: move report URLs, including response lists and the reports root.
- [ ] ADM-12.15 — NAV-12: move export and digest URLs and fold the latest-data export into the shared export page.
- [ ] ADM-12.16 — NAV-13 (optional): add trailing slashes to sign-in, setup and maintenance URLs.
- [ ] ADM-12.17 — NAV-14: add the Emailed reports page.
- [ ] ADM-12.18 — NAV-15: split Portal users into Sign-in rules, Ministry assignments and Chairpersons (#535).
- [ ] ADM-12.19 — NAV-16: add the ways back (#521), including the test-email origin kept in the session.
- [ ] ADM-12.20 — NAV-17: add the reachability and no-UUID crawl test.
- [ ] ADM-12.21 — NAV-18: add Home's Next steps for each state and role and the per-role Today line.
- [ ] ADM-12.22 — NAV-19: add the Find a Family header search (#561).

Evidence: In progress. ADM-12.00 is this docs-only change: the
specification states the JavaScript requirement once and links it from the
architecture, reports and hosted-files specifications; adds the Response list
row, the Find a Family header search, the non-page route, trailing-slash,
410 and query-string rules, the session-kept test-email origin, Home's
no-current-campaign explanation and Today line, and decisions 20 to 30; and
this package, its checklist and the acceptance-manifest owners are added,
checked by Markdown lint and `tests/stewardship/test_traceability.py`.

## ADM-13: System health page

Scope and dependencies: [ADM-13 work package](../../plans/stewardship/admin-portal.md#adm-13-system-health-page).
Specification: [System health](../../specs/stewardship/admin-portal/spec.md#system-health).

- [x] ADM-13.00 — PR 0: specify the System health page and its actions, record the Administrator's decisions, and add this package and its checklist.
- [ ] ADM-13.01 — PR 1: add service status records and record every drop count with a refused refresh (migration).
- [ ] ADM-13.02 — PR 2: add the read-only System health page, with its read command or its pending exemption.
- [ ] ADM-13.03 — PR 3: add Take a backup now through a durable request and request mode (migration), with its command or its pending exemption.
- [ ] ADM-13.04 — PR 4: add Clear the halt with halt identities, the mailbox check and the clear signal (migration), with its commands or their pending exemption.
- [ ] ADM-13.05 — PR 5: add Accept this change once, bound to every recorded count, with example Families (migration), with its commands or their pending exemption.
- [ ] ADM-13.06 — PR 6: add the debug-off switch, its in-process override, its host clear command and Allow debug logging again (Testing only, refused in Production) (migration), with its command or its pending exemption.

Evidence: In progress. ADM-13.00 is this docs-only change: the Admin portal
specification's System health section (panels, actions, service status
records, replaced runbook steps, what stays on the host and the
Administrator's decisions), links from the operations and automation
specifications, the corrected SYSTEMIC outcome wording in the Family mail
dispatch guide and launch runbooks, this package, its checklist and the
acceptance-manifest owners, checked by Markdown lint and
`tests/stewardship/test_traceability.py`.
