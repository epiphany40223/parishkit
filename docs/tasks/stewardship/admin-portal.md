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
- [x] ADM-08.04 — Build searchable Admin logs and timezone-aware exports.
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
- [x] ADM-11.03 — PR 2: add durable browser-approved automation sessions, notices and the maintenance task (migration), the host wrapper and session file, restore revocation, refusals and audit.
- [x] ADM-11.04 — PR 3: add read models, read-only status commands and the route-parity test.
- [x] ADM-11.05 — PR 4: add schedule preview and confirm and configuration request status.
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
ADM-11.03 lands in three stacked pull requests. 2a adds the forward
migration `stewardship_accounts.0004` with the frozen file
`0004_automation_sessions.sql` (the four automation tables and their
guards, the liveness, freshness and session purge functions, the worker's
cleanup guard on Admin sessions, the four automation incident kinds and the
`automation_maintenance` task type), the automation constructor and command
sessions, both refusals, the maintenance task and `revoke-automation-sessions`
with its restore steps, proven by
`tests/stewardship/test_automation_revocation.py` and
`tests/stewardship/database/test_automation_sessions_postgresql.py`,
`test_automation_logins_postgresql.py` and
`test_automation_maintenance_postgresql.py`, with the upgrade-parity and
schema baseline tests. 2b adds the session commands of
`pk-stewardship admin`, the host wrapper `tools/stewardship-ops/pk-admin`, the
approval page and the
[operator guide](../../guides/stewardship-admin-automation.md), proven by
`tests/stewardship/test_admin_cli.py`, `tests/stewardship/test_pk_admin.py`,
`tests/stewardship/database/test_admin_cli_postgresql.py` and
`test_automation_approval_postgresql.py`. 2c adds Automation access with
every live session, in-place revocation and the dashboard's automation
notices, proven by `tests/stewardship/database/test_automation_access_postgresql.py`
and the Chromium and WebKit tests in `tests/stewardship/browser/test_automation.py`.
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
and presence suites. 3b adds `go-live readiness` and `go-live progress`
(with `--watch`), reading through `go_live_inputs.collect_inputs` and
`confirmation_progress.progress`, which now take the caller, and
`go_live_inputs.recent_cleanup_requests`; proven by the go-live goldens and
allowlists in `tests/stewardship/test_admin_reads.py`,
`tests/stewardship/test_admin_cli.py` (catalog, usage and the fresh-process
case), `tests/stewardship/database/test_admin_go_live_cli_postgresql.py`
(a plain draft, a draft ready to go live, one whose cleanup started, and a
scheduled and an active Production confirmation) and the unchanged
go-live, confirmation, activation and withdrawal suites.
ADM-11.05 adds `schedule preview`, `schedule confirm` and
`config request show` (`parishkit.stewardship.admin_changes` and
`admin_reads.read_config_request`), with the page's preview moved into
`accounts.schedule_changes.build_preview`, its confirmation into
`admin_editing.confirm_intent` and the request status read into
`accounts.configuration_request_reads.receipt`; proven by
`tests/stewardship/test_admin_changes.py`,
`tests/stewardship/test_admin_cli.py` (catalog, usage, the registered
`admin_cmd_schedule_confirm` event and the fresh-process cases),
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_schedule_cli_postgresql.py` (the
round trip through the installer, tokens crossing between the page and the
command line, bad tokens, scopes, ended sessions and unknown outcomes) and
the unchanged schedule, clone, campaign, content, parish, Ministry and
configuration request suites.
ADM-11.06 lands in two pull requests. 5a adds the forward migration
`stewardship_accounts.0005` with the frozen file
`0013_automation_fresh_guards.sql`: the definer function
`stewardship_automation_fresh_principal_v1` (granted to web and the
parishsoft, google_workspace and slack credential installers through
`runtime_functions`, which `admit_installer_database` now passes to
`admit_grants`), and the four session-bound and two secret request guards
accepting a live full-scope automation session. `require_fresh` gains its
automation branch (the session locked `FOR SHARE`, live and full scope),
the post-cleanup checks accept an automation caller, and
`setup_credentials` and destructive confirmations refuse one. Proven by
`tests/stewardship/database/test_automation_fresh_guards_postgresql.py`
(each guard under its old and new definition, read-only, revoked and stale
browser sessions, the function's results, the frozen file's check against
an unchanged guard, the refusals and the post-cleanup check),
`tests/stewardship/test_schema_migration_files.py`, the regenerated schema
baseline and the unchanged grant, credential, Family test, delivery
control, confirmation and withdrawal suites. 5b adds the confirmation
prompt (`admin_cli.confirm`, `--yes` for prompting commands, exit 4
`confirmation_required`, the `confirmation` log field), the
`automation_fresh_gate` audit event and the `fresh_gated` and
`irreversible` notices from `require_fresh`; proven by
`tests/stewardship/test_admin_prompt.py` and
`tests/stewardship/database/test_automation_fresh_notices_postgresql.py`.
ADM-11.07 lands in three pull requests. 6a adds `refresh start` and
`refresh status` (`parishkit.stewardship.admin_refresh`), with the Source
refresh page's read and request moved into `read_refresh_page` and
`request_manual_refresh`; proven by `tests/stewardship/test_admin_refresh.py`
(golden documents, allowlists, one event per key, refusal mapping and the
unknown outcome), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_refresh_cli_postgresql.py` (keys
crossing between the page and the command line, joining a waiting refresh,
running and waiting status, scopes and ended sessions) and the unchanged
refresh view suite. 6b adds `test sample`, and 6c chosen-Family tests.
ADM-11.10 lands in two pull requests. 9a adds `task retry`
(`parishkit.stewardship.admin_operations`), with the retry pages' bodies,
the delivery views' admission and their command scope moved into
`jobs.task_retries`; proven by `tests/stewardship/test_admin_operations.py`,
`tests/stewardship/test_admin_cli.py` (catalog, the registered
`admin_cmd_task_retry` event and the fresh-process case),
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_task_retry_cli_postgresql.py` (Family
preparation, daily digest and export cleanup retries, keys crossing between
the page and the command line, stale runs, scopes, ended sessions and
unknown outcomes) and the unchanged delivery, command session, digest retry
and export retry suites. 9b adds the delivery and refusal commands.

## ADM-12: Admin navigation overhaul

Scope and dependencies: [ADM-12 work package](../../plans/stewardship/admin-portal.md#adm-12-admin-navigation-overhaul).
Each task maps to the row naming it in that package's table, not to a list item.
Specification: [Admin navigation](../../specs/stewardship/admin-portal/spec.md#admin-navigation).

- [x] ADM-12.00 — NAV-0: record the implementation plan's decisions in the specification and add this package and its checklist.
- [x] ADM-12.01 — NAV-1: gate every Admin and sign-in page on JavaScript (#565).
- [x] ADM-12.02 — NAV-2: rewrite the navigation registry into the seven menu groups with per-entry capability and reason checks.
- [x] ADM-12.03 — NAV-2: keep a stable menu shape, with unavailable entries greyed out and their reasons shown on hover, focus and tap.
- [x] ADM-12.04 — NAV-2: make menu groups collapsible, remembered per browser, and end the menu with Sign out.
- [x] ADM-12.05 — NAV-3: grey out multi-campaign controls with the #145 tip, refuse their actions on the server, and remove New campaign.
- [x] ADM-12.06 — NAV-4: give Campaign setup and Mail pages one name each, including Cancel go-live.
- [x] ADM-12.07 — NAV-5a: give Parish data, Users and access, System and Home pages, and the remaining setup wizard steps, one name each.
- [x] ADM-12.08 — NAV-5b: give report pages one name each.
- [x] ADM-12.09 — NAV-6: add the URL plumbing, per-group URL modules, legacy redirects and System URLs.
- [ ] ADM-12.10 — NAV-7: move Parish data and Users URLs and the change status URL.
- [x] ADM-12.11 — NAV-8: move Mail and Family portal URLs.
- [x] ADM-12.12 — NAV-9: move Campaign setup URLs, part A, and add the group root.
- [x] ADM-12.13 — NAV-10: move Campaign setup URLs, part B (go-live chain, test email, Campaign Ministries).
- [x] ADM-12.14 — NAV-11: move report URLs, including response lists and the reports root.
- [ ] ADM-12.15 — NAV-12: move export and digest URLs and fold the latest-data export into the shared export page.
- [ ] ADM-12.16 — NAV-13 (optional): add trailing slashes to sign-in, setup and maintenance URLs.
- [ ] ADM-12.17 — NAV-14: add the Emailed reports page.
- [ ] ADM-12.18 — NAV-15: split Portal users into Sign-in rules, Ministry assignments and Chairpersons (#535).
- [ ] ADM-12.19 — NAV-16: add the ways back (#521), including the test-email origin kept in the session.
- [ ] ADM-12.20 — NAV-17: add the reachability and no-UUID crawl test.
- [ ] ADM-12.21 — NAV-18: add Home's Next steps for each state and role and the per-role Today line.
- [x] ADM-12.22 — NAV-19: add the Find a Family header search (#561).

Evidence: In progress. ADM-12.00 merged in PR #568: the
specification states the JavaScript requirement once and links it from the
architecture, reports and hosted-files specifications; adds the Response list
row, the Find a Family header search, the non-page route, trailing-slash,
410 and query-string rules, the session-kept test-email origin, Home's
no-current-campaign explanation and Today line, and decisions 20 to 30; and
this package, its checklist and the acceptance-manifest owners are added,
checked by Markdown lint and `tests/stewardship/test_traceability.py`.
ADM-12.01 adds `admin-base.html`, which every Admin template extends, the
`admin-gate-v1.js` script and the `ui-v1.css` hiding rule; Family pages keep
the ungated base. It is proven by
`tests/stewardship/test_admin_javascript_gate.py` (template guard and
rendered gate) and `tests/stewardship/browser/test_admin_javascript_gate.py`
(panel only and nothing reachable with script off, the normal page with
script on, on Chromium and WebKit).
ADM-12.02 to ADM-12.04 (NAV-2) move the sidebar to Home and six groups:
`admin_navigation.py` gains the ordered `MENU` table, where each entry names
the capability its page checks and a reason check that reads only the
chrome's campaign and mode, and `admin_context.py` builds the menu from it,
folding in Delivery controls. Unavailable entries stay in place as links
without `href` (`role="link"`, `aria-disabled`, `tabindex="0"`,
`aria-describedby`) whose reason tip `admin-menu-v1.js` shows on hover, focus
and tap; groups are `<details>` disclosures, collapsed ones are remembered
per browser and the current page's group always opens; Sign out stays last.
It is proven by `tests/stewardship/test_admin_navigation.py` (one menu shape
per role in both modes and every campaign state, the spec's entry order, the
reasons, the rendered ARIA and groups), the chrome query count on a report
page in `tests/stewardship/database/test_admin_navigation_postgresql.py` (no
role above NAV-1) and `tests/stewardship/browser/test_admin_menu.py` (tip on
hover, focus and tap, Escape and tap-away dismissal, remembered collapse,
the current group forced open and the summary's expanded state, on Chromium
and WebKit). The menu's open counts are #585.
ADM-12.05 (NAV-3) applies navigation rule 10: Copy campaign on Campaign
settings and the "Choose a retained campaign" links on Participation and
Ministry requests become the shared `components/disabled-control.html`
(NAV-2's unavailable-entry pattern) with the tip "Disabled; will be removed
with the single-campaign change (#145)", defined once in
`campaigns/single_campaign.py`; New campaign and Campaign settings' create
branch are removed. The server refuses with 410: `campaign_clone` refuses
every request before reading it, the shared `confirm` refuses any change that
adds a campaign and `_target` refuses a new draft, and `admit_report_read`
refuses a campaign that is not current. The old New campaign address and the
two choosers redirect. It is proven by
`tests/stewardship/test_single_campaign.py` (exact tip text, rendered control,
the three templates, the service refusal),
`tests/stewardship/database/test_clone_views_postgresql.py` (page, seeded
preview and valid signed confirmation all refused, nothing recorded),
`tests/stewardship/database/test_campaign_views_postgresql.py` (New campaign
redirects and records nothing; a signed creation preview is refused while an
edit signed the same way applies),
`tests/stewardship/database/test_single_campaign_postgresql.py` and the
report suites (a non-current campaign refused with 410, after access is
checked with 403; choosers redirect) and
`tests/stewardship/browser/test_single_campaign.py` (the Copy campaign tip on
hover, focus and tap, on Chromium and WebKit).
ADM-12.22 (NAV-19) adds the header's Find a Family box for Administrators and
Staff: `admin_context.py` offers it only when the viewer's menu offers the
Family directory (no extra query), and the non-page `find_family` route runs
the directory's installed search by CSRF POST, rechecks directory and
timeline access inside the campaign read guard, and audits the search as a
directory view without its text; `find-family-v1.js` searches after a pause,
for 2 or more characters, cancelling older searches. Member names and the
envelope number are #664. It is proven by
`tests/stewardship/test_find_family.py` (who gets the box, its markup and the
results fragment), `tests/stewardship/database/test_directories_postgresql.py`
(results, POST-only refusals, no codes, `no-store`, audit without the text,
Staff allowed and a Ministry leader refused),
`tests/stewardship/database/test_identity_performance_postgresql.py` (the
Admin shell's query budget unchanged) and
`tests/stewardship/browser/test_find_family.py` (one request after a pause,
the keyboard path, refusals, WCAG checks at phone and desktop widths, on
Chromium and WebKit).
ADM-12.06 (NAV-4) gives every Campaign setup and Mail and Family portal page
its placement-table name in the registry, the browser title and the heading
(Dates and mail schedules, Pause and resume mail, Family email history, Cancel
go-live and the rest); Cancel go-live sits under Production activation, the
page that offers it, and its wording no longer says "withdraw" while its
audit events keep their kinds. "Return to" links name their target page,
links and messages that name a renamed page use the new name, and the
hand-written crumb links on Mail message and the refused-address pages are
removed. The setup wizard's Pages and emails, Share options and Dates and
mail schedules steps take the same names. URLs are unchanged. It is proven by
`tests/stewardship/test_admin_page_names.py` (registry labels match the
spec's table; title and heading match the label; every "Return to" names a
page; no template, script or user-facing message uses a retired name outside
a commented allowlist; Cancel go-live's trail and its blocking message's
links) and the updated page, menu and browser suites.
ADM-12.07 (NAV-5a) names Home ("Home"), the Parish data, Users and access
and System pages (Ministries, Review parish logos, Refresh from ParishSoft,
Change status, Background task and the sign-in rule, Ministry assignment and
Chairperson reviews) and every setup wizard step after its stepper label; the
data-entry and connection steps take their heading from the stepper entry
itself. Portal users keeps its name until NAV-15 splits it. Parish settings
loses its hand-written Administration link, and "Back to" links become
"Return to" links naming their page. It is proven by the extended
`tests/stewardship/test_admin_page_names.py` (the new groups, Home, Change
status and every setup step; more retired names) and the updated page,
setup and browser suites.
ADM-12.08 (NAV-5b) names the report pages after the placement table
(Participation, Financial stewardship, Additional information and
Information request, Latest-data export, Ministry requests with Members
joining and Members leaving, Ministry follow-up and Follow-up request, Send
a weekly report now, Weekly report and Weekly report item); templates
shared by a list and its item switch their heading by context, and the
empty-report page is named after the report opened. "Return to" links name
their page, including an export's report and Home. It is proven by
`tests/stewardship/test_admin_page_names.py` (the Responses and reports
group, the empty-report page, more retired names) and the updated report
suites.
ADM-12.09 (NAV-6) adds the URL plumbing: `web/admin_routes.py` with
`legacy()` (301 for GET and HEAD, 308 otherwise, query kept; for an old
address naming a campaign, a redirect only for the current campaign and a
never-cached 410 otherwise) and `current_campaign()`, and the
`admin_urls/` package with the System group's routes and the old-to-new
table. Integrations, key changes, Background work, its task pages and
retries, and System logs move under `/admin/system/`; the JSON reads that
scripts poll keep their addresses. Remembered change origins on old
addresses still lead back to their page, the setup-time background
allowance matches both forms, and the runbooks name the new addresses. It
is proven by `tests/stewardship/test_admin_url_scheme.py` (scheme rules,
every page reverses, each old address redirects keeping the query),
`tests/stewardship/test_family_routes_frozen.py` (Family and OAuth routes
unchanged), `tests/stewardship/database/test_admin_url_scheme_postgresql.py`
(campaign redirects and refusals, `current_campaign`, bookmarks through the
middleware) and the updated System suites.
ADM-12.10 (NAV-7, in part) moves the Parish data pages under
`/admin/parish/` (settings, logos, Ministries, hosted files with
`uploads/` and `deletion/` actions, and the ParishSoft refresh) and a
change's status page to `/admin/changes/<request>/`, each old address and
no-slash form redirecting. The ingress admits large uploads at both upload
addresses, and the two hard-coded redirects are reversed. The Users and
access URLs move with the Portal users split (NAV-15), so this task stays
open until then. It is proven by the expected-redirect table in
`tests/stewardship/test_admin_url_scheme.py`, the Caddyfile render tests and
golden files, and the updated Parish data and change suites.
ADM-12.11 (NAV-8) moves the Mail and Family portal pages under `/admin/mail/`
(Pause and resume mail at `controls/`, Family email progress and its status
fragment, Family email history, Outgoing mail and one message with its
`resolution/` action, Refused addresses and one address with its
`clearance/` action, Family portal availability and Families on the form
now), each old address and no-slash form redirecting. Pause and resume
mail's old address names a campaign, so it redirects only for the current
campaign and is gone (410) for any other. The header's presence count keeps
its polled `/admin/presence?format=count` read (decision 8) while page reads
of that address redirect, and the header's two polls take their URLs from
`data-` attributes rendered from the URL names instead of script literals.
No Family route moves. It is proven by
`tests/stewardship/test_admin_url_scheme.py` (the Mail rows, the presence
split), `tests/stewardship/database/test_admin_url_scheme_postgresql.py`
(Pause and resume mail's old address: current campaign redirected, another
refused with 410) and the updated mail, presence and browser suites.
ADM-12.12 (NAV-9) moves the first Campaign setup pages under
`/admin/campaign/` with no campaign in the address: Campaign settings, Copy
campaign (`copy/`, still refused), Pages and emails with each page or email,
its revisions and Content history, Campaign images with the upload, review
and `removal/` pages, Dates and mail schedules, Share options and Member
talents. Each old address names a campaign, so it redirects only for the
current campaign and is gone (410) for any other; each new page's no-slash
form redirects. The staged-image redirect is reversed instead of
hard-coded. The Campaign setup, Mail and Family portal and Parish data group
roots open the first entry the viewer may open now (else Home), from the
same menu the sidebar shows. The test email pages stay ahead of the old
editor route until part B moves them, and the content editor's plain-text
preview keeps its address because the setup wizard's editor shares it. It
is proven by `tests/stewardship/test_admin_url_scheme.py` (the Campaign
setup rows, the group roots, the test email routes),
`tests/stewardship/database/test_admin_url_scheme_postgresql.py` (old
addresses for the current campaign and another, each group root per role)
and the updated campaign, content, image, schedule, share and talent suites.

ADM-12.13 (NAV-10) moves the rest of Campaign setup under
`/admin/campaign/` with no campaign in the address: Preview and test email
`content/test/<revision>/` with Send to chosen Families `families/`, the
go-live chain (`go-live/`, `go-live/families/`, `go-live/cleanup/<request>/`,
its `links/` and `links/<preparation>/confirmation/`), Production activation
`production/` and Cancel go-live `production/cancellation/`. Campaign
Ministries moves under Ministries (`/admin/parish/ministries/campaign/`):
its trail and Return link run through Ministries, and Ministries links it
while the campaign is live; Campaign settings keeps its link. Each old
address names a campaign, so it redirects only for the current campaign
(301, or 308 that keeps a form's method and body) and is gone (410, never
cached) for any other, before any effect; the page a 308 reaches still checks
the CSRF token, the fresh sign-in and the reviewed token, so a re-posted form
never acts twice. The test email routes, their no-slash forms and their old
addresses come before the content editor's, which would otherwise take
`content/test/<revision>` as kind "test". Every moved page and its form
posts (Confirm Production and Cancel go-live included) refuse plainly when
there is no current campaign. It is proven
by `tests/stewardship/test_admin_url_scheme.py` (the part B rows, the test
email route order, the go-live chain),
`tests/stewardship/database/test_admin_url_scheme_postgresql.py` (old
addresses for the current campaign and another, an old readiness form that
starts nothing, the pages without a current campaign),
`tests/stewardship/database/test_confirmation_readiness_postgresql.py` and
`tests/stewardship/database/test_withdrawal_postgresql.py` (a confirmation or
cancellation re-posted through its old address keeps the CSRF, fresh sign-in
and reviewed-token checks and acts once) and the updated go-live, test
email, delivery control and live Ministries suites.

ADM-12.14 (NAV-11) moves every report page under `/admin/reports/` with no
campaign in the address: the Response dashboard `responses/` and its lists
`responses/<list>/` (CSV `csv/`), Participation `participation/`, Financial
stewardship `financial/`, Talents and limitations `talents/`, Additional
information `information/` and its requests, Ministry requests
`ministries/` with Members joining `joining/` and leaving `leaving/`,
Ministry follow-up `ministries/follow-up/` and its requests, the Family
directory `families/` and the Family timeline `families/<family>/` (it
also moved Family campaign codes to `family-codes/`; #873 later removed
that page and its addresses). Each report's form actions move with it as
nouns: exports post to its `exports/` collection, a follow-up is saved to
its request's `record/`, the follow-up packet to `ministries/packets/`, and
the header's Find a Family box posts to `families/search/`. Each old address
names a campaign, so it redirects only for the current campaign (301, or 308
that keeps a form's method and body) and is gone (410, never cached) for any
other, before any effect; the report a 308 reaches still checks the CSRF
token, so a re-posted form never acts by itself. The two campaign choosers
and the old Ministry reports root (`/admin/ministry-reports/`) are retired
to permanent redirects to Participation and Ministry requests, and
Ministry requests now shows the "no campaign" page that root showed when
there is nothing for the viewer to report. `/admin/reports/` keeps its
meaning: Participation, or Ministry requests for a viewer who may not open
Participation. The export, latest-data export and emailed report pages are
addressed by their own record and move with NAV-12, so sent digest emails
still open their reports. It is proven by
`tests/stewardship/test_admin_url_scheme.py` (the report rows, the reports
root, the directory route order, the sent digest link shapes),
`tests/stewardship/database/test_admin_url_scheme_postgresql.py` (old report
addresses and forms for the current campaign and another, the retired
addresses, the pages and forms without a current campaign, the Ministry
leader at the reports root) and the updated report, export, follow-up,
directory, timeline and single-campaign suites.

## ADM-13: System health page

Scope and dependencies: [ADM-13 work package](../../plans/stewardship/admin-portal.md#adm-13-system-health-page).
Specification: [System health](../../specs/stewardship/admin-portal/spec.md#system-health).

- [x] ADM-13.00 — PR 0: specify the System health page and its actions, record the Administrator's decisions, and add this package and its checklist.
- [x] ADM-13.01 — PR 1: add service status records and record every drop count with a refused refresh (migration).
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
`tests/stewardship/test_traceability.py`. ADM-13.01 adds the frozen forward
migration `0007_system_health_records.sql` (`stewardship_jobs.0007`, with
the state-only `stewardship_source.0003`): the service status records with
their per-login grants (including the installers' new write grant) and the
reporting in every online process, and the drop counts that the loader now
checks in full and the worker records with each refused attempt, checked by
`tests/stewardship/test_service_status.py`,
`tests/stewardship/test_source_loading.py`,
`tests/stewardship/database/test_system_health_records_postgresql.py`,
`tests/stewardship/database/test_automation_maintenance_postgresql.py`, the
migration-file and upgrade-parity tests and the regenerated schema baseline.
ADM-13.02 is in progress in parts. Part 2a adds the read model
`parishkit.stewardship.system_health` (`SystemHealth`), the Administrator-only
page at `/admin/system/health/` with its problems list and six panels, the
passive 10-second status fragment, `/admin/system/` opening it, the first
System menu entry, the `system_health_viewed` audit event and the debug
logging and critical-problems banners' links, with no schema or grant change, checked by
`tests/stewardship/test_system_health.py`,
`tests/stewardship/database/test_system_health_postgresql.py`,
`tests/stewardship/browser/test_system_health.py` and the navigation and
route-parity tests. Part 2b adds Home's System health problem lines for
viewers who may open the page (the page's own sentences, shared through
one template, read in the statement Home already runs for its ParishSoft
status, so Home's query count is unchanged) and the
`system health` command with `--watch` (stopping once nothing needs
attention), checked by the golden document and allowlist in
`tests/stewardship/test_admin_reads.py`, `tests/stewardship/test_admin_cli.py`
and `tests/stewardship/database/test_system_health_postgresql.py`. The
24-hour daily-limit count (part 2c) remains.
