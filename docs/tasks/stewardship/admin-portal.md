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

Since [#1547](https://github.com/epiphany40223/parishkit/issues/1547)
(2026-10-17) no Admin action checks how long ago the Admin signed in: the
step-up ("Confirm with Google"), `require_fresh`, the setup sign-in hour and
the SQL guards' sign-in age clauses (migration 0114) are gone. Evidence below
that mentions fresh sign-ins, step-ups or `require_fresh` records the
behavior when that work landed; open tasks no longer include them.

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
- [ ] ADM-04.07 — Shared schedule preview (#1129), in three slices: 7a specify the component and its adoption (docs only); 7b add the component and draw the refresh schedule's seven-day list with it; 7c draw the mail schedule review's send times with it.

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

ADM-04.07 is post-launch work for #1129. Slice 7b draws the refresh schedule's seven-day list with the shared `occurrence_preview` component and `components/occurrences.html`, render-identical except the `data-day`, `data-today` and `data-other-zone-time` hooks (`test_refresh_preview_render.py`); slice 7c remains. Slice 7a is a docs-only change that adds the [schedule previews](../../specs/stewardship/ui-conventions/spec.md#schedule-previews) convention (with its traceability entry), the adoption paragraphs in the admin-portal spec's seven-day preview and mail schedule review, the command line's from-now send times in the admin-automation spec, and this item's delivery plan.

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
- [ ] ADM-10.04 — Build typed purge confirmation (no sign-in age check, #1547).
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
- [x] ADM-11.06 — PR 5: let fresh-gated SQL guards and checks accept full-scope automation sessions (migration) and add the confirmation prompt.
- [x] ADM-11.07 — PR 6: add refresh and Testing send commands.
- [x] ADM-11.08 — PR 7: add delivery control (7b) and Family portal maintenance (7a) commands.
- [ ] ADM-11.09 — PR 8: add report, export, digest and log commands.
- [x] ADM-11.10 — PR 9: add task retry, delivery and refusal commands.
- [ ] ADM-11.11 — PR 10: add the remaining configuration commands, including secret replacement.
- [ ] ADM-11.12 — PR 11: add acknowledgement (`events list`, `events acknowledge`) and follow-up (`followup …`) commands. The users commands (`users list`, `users preview-upgrade`, `users preview`, `users confirm`, `users import-leaders`, including high-impact changes) move to the #952 slices; rule and assignment commands are retired by #952 and #922.
- [x] ADM-11.13 — PR 12: add go-live and withdrawal commands (#1809, PR #1816: the Go live page's actions, `admin_go_live`, `test_admin_go_live_actions_cli_postgresql.py`).

Since #1547 an automation session no longer stands in for a recent sign-in,
because nothing needs one; privileged commands still need a live full-scope
session and still record `automation_fresh_gate` and the `fresh_gated` and
`irreversible` notices (names kept). Every session-bound SQL guard still
requires a command session's live full-scope automation session
(`stewardship_automation_fresh_v1`), and the secret request guards refuse a
request whose named automation session ended
(`stewardship_automation_request_live_v1`, frozen migration 0125,
[#1763](https://github.com/epiphany40223/parishkit/issues/1763), which
replaced 0114's `stewardship_automation_instant_ended_v1`, so a browser's
own requests no longer depend on the automation sessions it approved);
`stewardship_automation_fresh_principal_v1`, unused after 0114, was
dropped by frozen migration 0123
([#1550](https://github.com/epiphany40223/parishkit/issues/1550)), which also
made an Admin session's sign-in instant immutable.

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
refresh view suite. 6b adds
`test sample-preview` and `test sample` (`parishkit.stewardship.admin_tests`),
with the sample test page's list moved into `campaign_mail.recent_tests`;
proven by `tests/stewardship/test_admin_tests.py` (golden documents and
allowlists with no address or message body, and the conditional prompt),
`tests/stewardship/test_admin_cli.py`, `tests/stewardship/test_pk_admin.py`
(the wrapper's `PROMPTING` list against the catalog),
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_test_sample_cli_postgresql.py` (tokens
crossing between the page and the command line, one send per key, a pending
test, the unknown-outcome prompt refused and answered, one activity write,
scopes and ended sessions) and the unchanged campaign mail suite. 6c adds
`test families-preview`, `test families` and `test status`; proven by
`tests/stewardship/test_admin_tests.py` (golden documents and allowlists
with no Family name, Family id or address, and the always-on prompt),
`tests/stewardship/test_admin_cli.py` (the fresh-gated, prompting catalog
entry and the page's acknowledgement), `tests/stewardship/test_pk_admin.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_test_families_cli_postgresql.py`
(refused prompts change nothing, one ticket per Family with one fresh-gate
event and notice, a repeat from either channel, the page's review sent from
the command line, an ineligible Family, scopes and ended sessions). The
preview's `--names` export (#817, migration 0037) is proven by
`tests/stewardship/test_family_test_names.py` (the CSV and its access),
`tests/stewardship/test_admin_tests.py` (the document carries only the
export's status) and the same database suite (names only in the fetched
file, one trail, a repeat, a bound key, regeneration and the SQL capture
guards).
ADM-11.10 lands in three pull requests. 9a adds `task retry`
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
and export retry suites. 9b adds `delivery list`, `delivery show`,
`delivery resolve`, `delivery refusals` and `delivery refusal-show`, with
the pages' reads moved into `jobs.delivery_reads`; proven by
`tests/stewardship/test_admin_operations.py` (golden documents and
allowlists with no recipient, DUID or note text),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_delivery_cli_postgresql.py` and the
unchanged delivery view, recovery, command session and resolution suites.
`resend` and `delivery refusal-clear` follow in 9c, at PR 5b's prompt.
ADM-11.09 lands in five parts. 8a adds `logs list` and `logs export`, with
the System logs page's reads moved into `audit.log_reads`, the first
command that streams a file (its document on standard error) and the
wrapper's refusal to write one to a terminal; proven by
`tests/stewardship/test_admin_reports.py` (golden documents and allowlists
with no actor email or Family DUID, the page's filters, the stream),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/test_pk_admin.py`,
`tests/stewardship/database/test_admin_logs_cli_postgresql.py` (the page's
rows, events, counts and download bytes) and the unchanged log view suites.
8b adds the export lifecycle (`export create`, `export status`,
`export cancel`, `export retry`, `export regenerate` and
`export download --stream`) and the wrapper's `export fetch` and
`exports clean`, with the download's grant consumption and guarded read
moved into `export_views.prepare_download`; proven by
`tests/stewardship/test_admin_exports.py` (golden documents and
allowlists, the stream), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/test_pk_admin.py` (the owner-only fetch, its digest
check, no overwrite, no copy, the interrupted fetch and `exports clean`),
`tests/stewardship/database/test_admin_export_cli_postgresql.py` (the
page's bytes in csv, png and pdf, the page's events and contexts, repeats
by key and the refusals) and the unchanged export view suites. 8c adds the
aggregate report reads (`report list`, `report participation` with the
`fact_set_id` that `export create --fact-set` takes, `report responses`,
`report financial`, `report talents`, `report information` and
`report ministry`), counts and summaries only, through the pages' own
reads under their campaign read guard; proven by
`tests/stewardship/test_admin_report_reads.py` (golden documents and the
personal-data pattern), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_report_cli_postgresql.py` (each
read against its page, the pages' events and context members, a
read-only session, an ended session, no current campaign and the pages'
filter refusals) and the unchanged report view suites. 8d adds the digests
(`digest daily`, `digest weekly` and `digest weekly-request`), read from
the stored snapshots through the pages' own functions, with the manual
request through the page's `request_manual_report`; proven by
`tests/stewardship/test_admin_digests.py` (golden documents, the
personal-data pattern and the acknowledgement),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_digest_cli_postgresql.py` (each
report against its page, the pages' events, read-only sessions, a failed
read, the database's refusal of unresolved prior work, keyed repeats) and
the unchanged digest view suites. A follow-up adds `digest list`, the
Emailed reports page's lists through its own `emailed_views.listing`, with
no schema change; proven by `tests/stewardship/test_admin_digests.py`
(golden documents, a reader who is not an Administrator),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_digest_list_cli_postgresql.py` (the
page's reports, read-only and ended sessions, no event, `--requested`, no
current campaign) and the unchanged Emailed reports page suite. Another
follow-up adds `report source-form`, System health's Families the form
cannot open (#774) by DUID, through the page's own admission and
`blocked_families`, with no schema change; proven by
`tests/stewardship/test_admin_source_form.py` (the golden document without
the Family name, the catalog entry),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_source_form_cli_postgresql.py` (the
page's findings by DUID, read-only and ended sessions, the page's event, a
Staff reader denied, a check that cannot run, no current campaign, a
missing configuration row) and the unchanged source form page suite. 8e
adds the Family-level exports that
create an export record without the Family keys (`export financial`,
`export information`, `export ministry`, `export ministry-packet`),
through the export forms' own services; proven by
`tests/stewardship/test_admin_family_exports.py` (the filter grammar
without search, the parser, the catalog),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_exports.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_family_export_cli_postgresql.py`
(the page's record and `export_requested` event, the worker's file
streamed byte for byte as the page's download, keyed repeats, a bound
key, refused filters, a read-only session). 8f adds `export directory` and
`export postal`, loading only the code MAC keyring, only for a new request
and only when it matches the running web's; proven by
`tests/stewardship/test_admin_family_exports.py` (the keyring loader's
receipt check), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_family_export_cli_postgresql.py`
(one key load per new request and none for a repeat or a refusal, the codes
only in the file, the stream equal to the page's download, a keyring
mismatch with nothing made, a read-only session refused, a busy key lock as
exit 3), and the real admission's keyring wiring in
`tests/stewardship/test_admin_cli.py`. 8h adds the exact daily exports
(`export exact create`, `export exact status`, `export exact cancel`,
`export exact retry`) through the page's own exact-export services; proven
by `tests/stewardship/test_admin_exact_exports.py` (the documents, when a
watch stops, the catalog), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_exact_export_cli_postgresql.py`
(create, repeat and the page's form with the same key, a bound key, retry
by key, the real calculation and render, a read-only watch to the end, the
handed-off file, cancel before handoff and after publication, unknown
requests). 8g adds the Family timeline export, a new export kind with its
frozen migration 0063 (`stewardship_timeline_export_snapshot`, the
request's `timeline_snapshot_id`, the guards' timeline branches), the page's
export form and `export family-timeline`; proven by
`tests/stewardship/test_timeline_exports.py` (the file's contents),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_schema_migration_files.py`,
`tests/stewardship/database/test_timeline_exports_postgresql.py` (the
page and the command making the same record and event, the worker's file
with the code, the stream equal to the page's download, Staff denied, the
capture's SQL refusals), the schema baseline and upgrade parity suites and
the timeline browser test. The in-memory downloads (Limitations, response
lists, when PR #752 lands) follow. Another follow-up (#463) adds `report
roster`, Roster changes to enter counted, in
`parishkit.stewardship.admin_roster`, through the page's own admission and
`roster_rows` under the shared report read, with no schema change; proven
by `tests/stewardship/test_admin_report_reads.py` (the golden documents
without Member, Family or resolver, the catalog, usage errors),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_roster_cli_postgresql.py` (the
page's list counted with its Show and Ministry choices, the page's event,
an out-of-range Ministry `invalid` with nothing recorded, read-only and
Staff readers, a Ministry leader denied, an ended session, no current
campaign). A further follow-up (#463) adds `report census`, the Census
changes list counted, in `parishkit.stewardship.admin_census`, through the
page's own admission, `CensusQuery` and `census_changes` under the shared
report read, offered by `report list`, with no schema change; proven by
`tests/stewardship/test_admin_report_reads.py` (the golden documents
without Family, Member, value or resolver, the catalog, usage errors),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_report_cli_postgresql.py` (`report
list`) and `tests/stewardship/database/test_admin_census_cli_postgresql.py`
(the page's list counted with its filters, the page's event, refused
filters `invalid` with nothing recorded, read-only and Staff readers, a
Ministry leader denied, an ended session, no current campaign). Another
follow-up (#463) adds `census resolve`, a Census changes row's Record
outcome, in `parishkit.stewardship.admin_census`, through the page's own
admission and `resolve_census_change` in the page's command scope,
recording `admin_cmd_census_resolve` once per request key, with no schema
change; Entered in ParishSoft prompts, as the page's dialog asks; proven
by `tests/stewardship/test_admin_census.py` (the golden document without
note, Family, Member or value, the service's actions, the racing write
only for a unique violation, usage errors including `--note -` without
`--yes`), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_pk_admin.py` (the wrapper's prompting list),
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_census_resolve_cli_postgresql.py`
(entered, ignored and reopened with both events, Entered unanswered
changing nothing, a repeat recording nothing, a rebound key and a refused
outcome `invalid`, Reopen without a note, a stale version, an unknown or
another campaign's change and a repeat after the campaign changed
`not_available`, a read-only session denied, Staff entering and ignoring
but denied Reopen, a Ministry leader denied, an ended session, no current
campaign). A #528 follow-up adds `census review`, an Automatic row's Review
(ADM-09), through the same command scope (`admin_census._census_write`,
which `census resolve` now shares) and the page's `review_census_change`,
recording `admin_cmd_census_review` once per request key, with no schema
change and no prompt; `export census --format jsonl` gains `reviews`;
proven by `tests/stewardship/test_admin_census.py` (the golden document,
the decisions, the Administrator's role checked first, usage errors),
`tests/stewardship/test_admin_census_export.py` (`reviews` per state and
reader), `tests/stewardship/test_admin_cli.py` and
`tests/stewardship/database/test_admin_census_review_cli_postgresql.py`
(Approve, Ignore and Back to To review with both events, a repeat and a
rebound key, an unchanged decision `invalid`, the open-state matrix with
every queued review and a Conflict's Approve refused, By-hand `invalid`, a
stale version, an unknown or another campaign's change `not_available`, a
read-only session, Staff and a Ministry leader denied, an ended session, no
current campaign). #1434 adds `census edit` (Edit value, Clear edit) and
`census resolve-conflict` (Use the Family's value anyway) through the same
command scope and the page's `edit_census_change` and
`resolve_census_conflict`, recording `admin_cmd_census_edit` and
`admin_cmd_census_resolve_conflict` once per request key, with no schema
change and no prompt; `export census --format jsonl` gains `editable` and
`resolvable`; proven by `tests/stewardship/test_admin_census.py`,
`tests/stewardship/test_admin_census_export.py`,
`tests/stewardship/test_admin_cli.py` and
`tests/stewardship/database/test_admin_census_edit_cli_postgresql.py`
(edit, clear, a repeat and a rebound key, unchanged edits recording
nothing, a refused value with its reason, By-hand, queued, stale, unknown
and Staff refusals; a resolution with both events, its repeat and rebound
key, its undo by `census review`, and its refusals).
A follow-up (#682) lets `delivery resolve` retry Family emails too, loading
the web's general and public token keyrings only for that, checked against
the web's published receipts; proven by `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_operations.py` and
`tests/stewardship/database/test_admin_delivery_cli_postgresql.py` (a double
invoke with one key prepares once; credentials unchanged). 9c adds
`delivery resend` and `delivery refusal-clear`, which ask at PR 5b's
prompt; proven by `tests/stewardship/test_admin_operations.py`,
`tests/stewardship/test_admin_cli.py`, `tests/stewardship/test_pk_admin.py`
(the wrapper's `PROMPTING` list against the catalog),
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_delivery_ack_cli_postgresql.py` (a
refused prompt changes nothing, a typed `yes`, a double invoke, the page's
form with the same key, stale versions, unchanged credentials and the
read-only refusal). A follow-up (#1211) adds `delivery bounce-report`, the
Mail message page's Report a bounced address (#806 slice 4), through the
page's admission and `report_recipient_bounce` in the page's command scope,
asking for the page's tick at the prompt, recording
`admin_cmd_delivery_bounce_report` once per request key, and `kind` in the
refusal reads, with no schema change; proven by
`tests/stewardship/test_admin_operations.py` (the golden document without
address or note), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_pk_admin.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_bounce_report_cli_postgresql.py` (a
refused prompt changes nothing, a typed `yes` with an address as a bounce
message spells it, a double invoke, the page's form with the same key, a
rebound key `invalid`, an undelivered email, an address not sent to and one
already set aside `stale_version`, an unknown email, the read-only refusal,
`kind` in the reads and unchanged credentials).
A further follow-up (#463) adds `export census`, the Census changes
download, in `parishkit.stewardship.admin_census_export`, through the page's
own admission, `CensusQuery`, `census_changes` and file writer
(`census_change_views.export_body`, extracted from the page's view) under
the shared report read, with a command-line `jsonl` format that carries
each change's id, version and actions, and no schema change; proven by
`tests/stewardship/test_admin_census_export.py` (the JSON Lines writer, its
keys against the page's headings, the formats and choices),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_census_export_cli_postgresql.py`
(the page's CSV byte for byte, XLSX, PDF and JSON Lines, the page's event
with its count, refused choices `invalid` with nothing recorded, a
read-only session refused, Staff, a Ministry leader denied, an ended
session, no current campaign).
A further follow-up (#463) adds `export limitations`, the Limitations
download and the last part of PR 8h, in
`parishkit.stewardship.admin_limitations_export`, through the page's own
admission, `LimitationsQuery` (its Show choice, without the search),
`limitations_report`, choice check and file writer
(`limitation_views.export_choice` and `limitation_views.export_body`,
extracted from the page's view) under the shared report read, recording
the page's `talents_report_exported` with its count and #752's audit
fields, and no schema change; proven by
`tests/stewardship/test_admin_limitations_export.py` (the options against
the page's choices, the choice check, the file name),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_exports.py`,
`tests/stewardship/test_pk_admin.py` (the wrapper's streaming list),
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_limitations_export_cli_postgresql.py`
(the page's CSV byte for byte, XLSX sheet for sheet, PDF, the Show choice,
the page's event with its count and choices, a refused choice `invalid`
with nothing recorded, a read-only session refused, Staff, a Ministry
leader denied, an ended session, no current campaign).
A further follow-up (#463) adds `export roster`, the Roster changes to
enter download (PR 8g), in `parishkit.stewardship.admin_roster_export`,
through the page's own admission, `roster_rows` (its Show and Ministry
choices), choice check and file writer
(`ministry_roster_views.export_choice` and
`ministry_roster_views.export_body`, extracted from the page's view) under
the shared report read, recording the page's `roster_changes_exported`
with its count, and no schema change; proven by
`tests/stewardship/test_admin_roster_export.py` (the options against the
page's choices, the choice check, the file name),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_exports.py`,
`tests/stewardship/test_pk_admin.py` (the wrapper's streaming list),
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_roster_export_cli_postgresql.py`
(the page's CSV byte for byte, XLSX, PDF, the Show and Ministry choices,
the page's event with its count, refused choices `invalid` with nothing
recorded, a read-only session refused, Staff, a Ministry leader denied, an
ended session, no current campaign).
ADM-11.08 lands in two parts. 7a adds `portal maintenance show` and
`portal maintenance set` (fresh-gated, always prompts) in
`parishkit.stewardship.admin_portal`, through the page's own
`family_maintenance` functions, with no schema change; proven by
`tests/stewardship/test_admin_cli.py` (catalog flags and the event),
`tests/stewardship/test_pk_admin.py` (the `PROMPTING` list),
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_portal_cli_postgresql.py` (a refused
prompt changes nothing; `yes` closes with the page's event, the command
event, the fresh-gate event and notice; Families then see the maintenance
page; the page and the command line reopen each other's change; a no-op
`stale_version`; bad notes `invalid` before the prompt; read-only and ended
sessions). 7b (pause, resume and closed-campaign resolution) follows; its
first part adds `send controls`, the Pause and resume mail page's read, in
`parishkit.stewardship.admin_delivery_control`, through the page's status
(moved into `delivery_control_commands.controls`, which the page now calls),
with no schema change; proven by
`tests/stewardship/test_admin_delivery_control.py` (the golden document and
its member allowlist, the catalog entry, the ledger, usage errors),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_delivery_control_cli_postgresql.py`
(a Testing draft's read with no control available, a scheduled Production
campaign's read equal to the page's status before and after a pause that
holds a queued email, its recovery counts against the page's SQL reads, a
paused, closed campaign's resolution choices with held receipts and weekly
reports, no event, no current campaign `not_available`, a Staff reader
denied, an ended session) and the unchanged
`test_delivery_control_postgresql.py`. Its second part (#463) adds
`send pause-preview`, `send resume-preview`, `send resolve-preview` and
`send confirm` (fresh-gated, always prompts) in the same module, through
the page's own previews and `confirm` (which gains an optional `record`
hook for the command event), with no schema change and no new send path;
proven by `tests/stewardship/test_admin_send_controls.py` (the preview
documents without fingerprints, the prompt's lines, the catalog entries,
the parser's types against the page's, usage errors),
`tests/stewardship/test_admin_cli.py`, `tests/stewardship/test_pk_admin.py`
(the `PROMPTING` list), `tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_send_controls_cli_postgresql.py`
(previews equal to the page's for a pause, a resume before the start and a
closed campaign's cancellation; refused prompts change nothing; the
control, the command event, the fresh-gate event and notice; an exact
repeat recording nothing; a page token confirmed by the command and a
command token by the page; a resume refused by the page's preview while a
delivery is unknown and, for a token that skipped it, by the SQL guard,
with nothing recorded; bad reasons, an altered or expired token, a
read-only session, a Testing draft, no current campaign, Staff denied
before the prompt and an ended session).
ADM-11.11 lands in parts. The first adds `files list`, the Hosted files
page's library, in `parishkit.stewardship.admin_hosted_files`, through the
page's own capability, order and row builder, with no schema change;
proven by `tests/stewardship/test_admin_cli.py` (catalog flags, no event),
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_files_cli_postgresql.py` (the
page's rows sorted by placeholder name for a read-only session, an image's
tag and size, a use, a missing stored copy, no event, a Staff reader
denied, an ended session, no media root `unavailable`) and the unchanged
hosted files page suite. The second adds `files delete` and `files
rename`, the page's row actions, through its own `delete_many` and
`rename` (each gains a `record` callback for the command's event), with
no schema change; proven by `tests/stewardship/test_admin_cli.py` (catalog
flags, described events), `tests/stewardship/test_pk_admin.py` (the
`PROMPTING` list), `tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_files_change_cli_postgresql.py`
(deletion at the prompt and with `--yes`, several files, an already
deleted ID, a file in use refused before the prompt with nothing deleted,
renaming with the link unchanged, the current name recording nothing, an
invalid or taken name, a missing file, read-only and Staff sessions
refused, an ended session) and the unchanged hosted files page suite.
The third adds `content list` and `content history`, the Pages and emails
and Content history pages' reads, in `parishkit.stewardship.admin_content`,
through the catalog's campaign check and row builder and the history
page's read (moved into `content_history.retained_content`), with no
schema change; proven by `tests/stewardship/test_admin_content.py` (golden
documents, catalog flags, no event), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_content_cli_postgresql.py` (the
page's rows for a read-only session, a held campaign `stale_version`, no
current campaign, a retained revision's sample, an unknown revision, no
event, a Staff reader denied, an ended session) and the unchanged content
page suites. The fourth adds `parish show`, `ministries list`, `branding
show` and `campaign shares`, the Parish settings, Ministries, Parish logos
and Share options pages' reads, in `parishkit.stewardship.admin_settings`,
through the pages' own profile, catalog, logo and campaign-target reads
(the logo page's current-logo query moved into
`branding_slots.current_assets`, which the page now calls), with no schema
change; proven by
`tests/stewardship/test_admin_settings.py` (golden documents, catalog
flags, the ledger, usage errors), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_settings_cli_postgresql.py` (each
read for a read-only session with no event, no Ministry catalog
`unavailable`, logos in slot order with a missing asset left out, a
draft's Share options editable and a live campaign's not, no financial
module, no or an unknown campaign, a Staff reader denied, an ended
session). The fifth adds `parish preview` and `parish confirm`, the Parish
settings page's Review and Apply, in `parishkit.stewardship.admin_parish`,
through the page's own form, review (moved into
`parish_views.review_change`, which the page now calls) and
`admin_editing.confirm_intent`, with no schema change; proven by
`tests/stewardship/test_admin_parish.py` (the change document's shape,
catalog flags, the described event), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_parish_cli_postgresql.py` (a
preview and confirm recording the request and event, a telephone stored
as E.164, the timezone note, removing the giving link, a repeated confirm,
a stale version, no change, a field error, Staff denied, an ended session,
another page's token) and the unchanged parish settings page suites.
Another part adds `artwork show`, the Campaign images page's
read, in `parishkit.stewardship.admin_artwork`, through the page's own
campaign check, upload availability check and current-image query (moved
into `artwork_views.current_images`, which the page now calls), with no
schema change; proven by `tests/stewardship/test_admin_artwork.py` (the
golden document, unavailable slots, the catalog entry, the ledger, usage
errors), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_artwork_cli_postgresql.py` (every
slot in order for a read-only session with the images the page applied
and no event, no current campaign, a Staff reader denied, an ended
session) and the unchanged Campaign images page suite. Another part adds
`artwork remove-preview` and `artwork remove`, Campaign images' Remove
this image and its Apply, in `parishkit.stewardship.admin_artwork`,
through the page's own removal review (moved into
`artwork_views.review_removal`, which the page's review now calls) and
`admin_editing.confirm_intent`, with no schema change; proven by
`tests/stewardship/test_admin_artwork.py` (the catalog entries, the
described event, the parser's slots, the ledger, usage errors),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_artwork_remove_cli_postgresql.py`
(a preview and removal recording the request and event with the slot
emptied once installed, a repeated removal, a command token confirmed on
the page and a page token by the command, an unavailable slot's image
removed, a stale version, an empty slot, an altered, another slot's or
another page's token, another Administrator's token denied, a token
reviewed before the settings changed, no current campaign,
a read-only session and Staff denied, an ended session) and the unchanged
Campaign images page suite. Another part (#1865) adds `branding preview`
and `branding confirm`, Parish logos' upload with its review and the review
dialog's Apply, in `parishkit.stewardship.admin_branding`, through the
page's own form, staging (moved into `branding_views.stage_upload`, which
the page now calls), review and `admin_editing.confirm_intent`, with staged
uploads owned by the automation session on the command line
(`branding_staging.sign_in`) and no schema change; proven by
`tests/stewardship/test_admin_branding.py` (the printed sizes, the bound
form, the reader's limit, the catalog entries, the described events, the
ledger, usage errors), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_branding_cli_postgresql.py` (a
preview and confirm staging under the automation session, recording both
events and the request, installed and read back by `branding show`, a
repeat; one size replaced alone; a stale version, a non-image, a size
outside its rules, a size with nothing to keep and empty input staging
nothing; logos staged on the page or by another automation session not
available; an altered, another bundle's or another page's token, another
Administrator's denied, a token reviewed before the settings changed; a
read-only session and Staff denied, an ended session) and the unchanged
Parish logos page suites. Another part adds `workgroup show`,
`workgroup preview` and `workgroup confirm`, the No-reminders WorkGroup
page's read, Review and Apply, in `parishkit.stewardship.admin_workgroup`,
through the page's own campaign check, form, review (moved into
`reminder_workgroup_views.review_change`, which the page's Review now
calls) and `admin_editing.confirm_intent`, with no schema change; proven
by `tests/stewardship/test_admin_workgroup.py` (the Now states, the
catalog entries, the described event, the documents' names, the ledger,
usage errors), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_workgroup_cli_postgresql.py` (each
Now state for a read-only session with no event, a preview and confirm
recording the request and event with the name applied once installed,
clearing it, a repeated confirm, a command token confirmed on the page and
a page token by the command, a stale version, no change, a name too long,
an altered, another page's or another Administrator's token, a token
reviewed before the settings changed, no current campaign, a read-only
session and Staff denied, an ended session) and the unchanged
No-reminders WorkGroup page suite. Another part adds `campaign show`,
`campaign preview` and `campaign confirm`, Campaign settings' read, Review
and Apply, in `parishkit.stewardship.admin_campaign`, through the page's
own state, form, Review (moved into `campaign_views.review_settings`) and
Apply routing (moved into `campaign_views.confirm_route`), both of which
the page now calls, and `admin_editing.confirm_intent`, with no schema
change; proven by `tests/stewardship/test_admin_campaign.py` (the change
document's parser, the catalog entries, the described event, the
documents' names, the ledger, usage errors),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_campaign_cli_postgresql.py` (a
draft's read with no event, a rename previewed and confirmed with the
request and event and applied once installed, a repeated confirm, a
command token confirmed on the page and a page token by the command, a
stale version, no change, a field error, an unknown field and a wrong
type, an altered, another page's or another Administrator's token, a token
reviewed before the settings changed, a live campaign's open fields and a
locked field refused, its leader roles, its end date with
`campaign_end_date_requested` and its intent, its Ministries with
`campaign_ministries_requested`, no current campaign, a read-only session
and Staff denied, an ended session) and the unchanged Campaign settings,
live end date and live Ministries page suites. Another part (#1899) adds
`campaign create-preview` and `campaign create`, Create the campaign's
Review and Apply for the deployment's first campaign, in
`parishkit.stewardship.admin_campaign_create`, through the page's own
admission, form, Review, salt and Apply recheck and
`admin_editing.confirm_intent` with `first_campaign`, and defers
`campaign_clone` on #145, with no schema change; proven by
`tests/stewardship/test_admin_campaign_create.py` (the catalog entries,
the described event, the documents' names, the ledger, the refusal's
code, usage errors), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_campaign_create_cli_postgresql.py`
(the first campaign reviewed, created with the event and applied with its
default content, a repeat once it exists, a second review refused, the
form's starting values for omitted fields, a command token confirmed on
the page and a page token by the command, a stale version, field errors,
an unknown field, a malformed document, an altered, Campaign settings' or
another Administrator's token, a token reviewed before the settings
changed, a campaign added since the review, a read-only session and Staff
denied, an ended session) and the unchanged Create the campaign page
suite. Another part adds `campaign
shares-preview` and `campaign shares-confirm`, Share options' Review and
Apply, in `parishkit.stewardship.admin_shares`, through the page's own
target and editability checks, formset, review (moved into
`option_editing.review_options`, which the page's Review now calls) and
`admin_editing.confirm_intent`, with no schema change; proven by
`tests/stewardship/test_admin_shares.py` (the change document's parser,
the Review post it becomes bound to the page's formset, the catalog
entries, the described event, the documents' names, the ledger, usage
errors), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_settings.py`,
`tests/stewardship/database/test_admin_shares_cli_postgresql.py` (a
relabel, move, addition and deletion previewed and confirmed with the
request and event and applied once installed, a repeated confirm, the
document on standard input with `--campaign`, a command token confirmed on
the page and a page token by the command, a stale version, no change, a
saved option left out, an unknown id, a label the page refuses, an unknown
member, a census-only draft, an unknown campaign, a locked draft, a draft
background work holds, a live campaign and its stale token, a token for
another or an unknown campaign, an altered, another page's or another Administrator's token,
a token reviewed before the options changed, no current campaign, a
read-only session, an ended session and Staff denied before reading input)
and the unchanged Share options page suite.

ADM-11.12 lands in parts. The first adds `events list` and `events
acknowledge`, the Admin home page's security events and critical-problem
banner and their Acknowledge buttons, in
`parishkit.stewardship.admin_events`, through the page's own
`open_events`, `alert_counts`, `summary` and `sign` and the views'
transactions, moved into request-free `acknowledge_now` functions in
`security_event_views` and `critical_event_views`, which the views now
call, with no schema change; proven by
`tests/stewardship/test_admin_events.py` (golden documents, catalog flags,
the ledger, usage errors), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_events_cli_postgresql.py` (the list
for a read-only session with no event, a security event acknowledged with
the page's event and the command's, a repeat recording nothing, an
unknown event, the banner's groups and token acknowledging exactly the
listed rows while a later one stays, an altered token, a read-only
session, an ended session and Staff refused) and the unchanged
`tests/stewardship/database/test_security_events_postgresql.py` and
`tests/stewardship/database/test_critical_events_postgresql.py`.

ADM-11.12's PR 11a adds `followup list`, `followup show`, `followup
update` and `followup bulk`, the Ministry requests follow-up queue, in
`parishkit.stewardship.admin_followup`, through the page's own admission,
selection, history and services (`update_request`, `apply_bulk_outcome`)
in its command scope, with no schema change; proven by
`tests/stewardship/test_admin_followup.py` (golden documents and the
personal-data allowlist, catalog flags, the parser's choices against the
page's, the ledger, the page's refusals as field errors, usage errors),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_role_first.py`,
`tests/stewardship/test_pk_admin.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_followup_cli_postgresql.py` (the
queue and a request with the page's events and filters for a read-only
session, a Save with its events once for each revision its key binds, a
key shared with the page, notes kept or replaced, contact notes from
standard input, stale and closed requests checked before the field rules,
options without effect refused as usage, lengths and the page's refusals
as field errors, the bulk outcome's prompt, a mixed or stale choice saving
nothing, a choice too long refused before the prompt, a repeat, Staff
reading and changing, a leader's out-of-scope request answered as an
unknown one, another campaign's request, a read-only session refused, an
ended session and no current campaign). The roster tick, the queue's export and Family
information follow-up remain.

ADM-11.12's PR 11b adds `followup roster-entered` and `followup
roster-bulk` (in `parishkit.stewardship.admin_roster_tick`), the Entered in
ParishSoft tick and Roster changes to enter's bulk mark, and `followup
information-show` and `followup information-update` (in
`parishkit.stewardship.admin_information`), an Additional information
item's page and Save, through the pages' own admissions, reads and
services (`set_roster_entered`, `mark_many_entered`, `update_information`)
in the page's command scope, with no schema change; proven by
`tests/stewardship/test_admin_roster_information.py` (golden documents and
the personal-data allowlist, catalog flags, the ledger, the service's
refusals as field errors, limits checked before admission, usage errors),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_role_first.py`,
`tests/stewardship/test_pk_admin.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_roster_information_cli_postgresql.py`
(a tick set, repeated, rebound, already set, stale and cleared with its
events once per key, a page tick making the command's sequence stale, the
bulk mark's prompt, all-or-none refusals and repeat, Staff ticking, a
leader denied before the bulk prompt, another campaign's request, the item
read with the page's event and no Family text, a Save with kept values,
the clearing confirmation, a stale version, a key shared with the page, a
leader denied, a read-only session refused and an ended session). The
queue's export remains.

ADM-11.12's PR 11c adds `followup export` (in
`parishkit.stewardship.admin_followup_export`), the follow-up queue's
Export these requests, streamed as `export roster` is, through the
queue's own admission, choice check (`_choices`) and file read and writer
(`ministry_followup_exports.export_body` and `export_file_name`, extracted
from the page's view) under the shared report read, recording the page's
`ministry_followup_exported` with its count and Ministries, with no schema
change; proven by `tests/stewardship/test_admin_followup_export.py` (the
options against the page's choices, the choice check, the file name),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_followup.py` (the ledger),
`tests/stewardship/test_pk_admin.py` (the wrapper's streaming list),
`tests/stewardship/test_export_default_format.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_followup_export_cli_postgresql.py`
(the page's CSV byte for byte, XLSX, PDF, the filters, the page's event
with its count and Ministries, a PDF above the limit `invalid` at
`format` and recorded as failed, refused choices `invalid` with nothing
recorded, Staff, a role the selection refuses, a read-only session
refused, an ended session, no current campaign). This completes PR 11.

Another part adds `content show`, the content editor's read
of one page's or email's text, in `parishkit.stewardship.admin_content`,
through the editor's own lookup (moved into
`content_views.editor_content`, which the page now calls), with no schema
change; proven by `tests/stewardship/test_admin_content.py` (golden
document, catalog flags, the ledger, usage errors),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py` and
`tests/stewardship/database/test_admin_content_show_cli_postgresql.py` (a
saved page and email for a read-only session with no event, a new email
and the default text, the confirmation email, a re-save notice, an unknown
slot or revision, no current campaign, a held campaign `stale_version`, a
Staff reader denied, an ended session) and the unchanged content page
suites.

Another part adds `content preview` and `content confirm`, the email
tables' Delete and the editors' plain-text preview, in
`parishkit.stewardship.admin_content`, through the page's own removal
build (moved into `content_delete_views.review_deletion` and `chosen_ids`,
which the Delete now calls), its conversion (moved into
`content_views.plain_text`, which the plain-text preview now calls) and
`admin_editing.confirm_intent`, with no schema change; proven by
`tests/stewardship/test_admin_content_changes.py` (catalog flags, the
described event, usage errors, the choice check, the conversion),
`tests/stewardship/test_admin_content.py` (the ledger),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_content_changes_cli_postgresql.py`
(a preview and confirm of two unused emails recording the request and
event with the sent email kept once installed, a repeat, a sent email,
the confirmation email, an unknown or repeated id, a stale version, a
held campaign, an altered or another page's token, another
Administrator's token denied, a token built before the settings changed,
no current campaign, the plain-text preview equal to the page's answer
with no event and no renewed session, a read-only session and Staff
denied, an ended session) and the unchanged content page suites.

Another part adds `integration list`, `integration show` and `integration
status`, the Integrations pages' reads, in
`parishkit.stewardship.admin_integrations`, through the pages' own helpers
(the list's rows, the key change line and the backup status moved into
`integration_views.integration_list`, `key_summary` and `backup_state`,
which the pages now call), with no schema change; proven by
`tests/stewardship/test_admin_integrations.py` (golden documents and their
member allowlist, catalog flags, the ledger, usage errors),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_admin_route_parity.py`,
`tests/stewardship/database/test_admin_integrations_cli_postgresql.py` (the
list and ParishSoft for a read-only session with no event, Slack not set
up and a watch that stops at once, a staged key change equal to the page's
line, the backup folder, its copy status and only this Administrator's
check, Staff denied, an ended session) and the unchanged integration and
backup page suites.

Another part adds `integration preview` and `integration confirm`, an
integration settings page's non-secret Save and its Remove, in
`parishkit.stewardship.admin_integration_changes`, through the page's own
form, reviews (moved into `integration_views.review_settings` and
`review_removal`, which the page's Save and Remove now call), salt and
`admin_editing.confirm_intent` with its fresh gate (whose repeat now
records no second fresh-gate notice), with no schema change; proven by
`tests/stewardship/test_admin_integration_changes.py` (the change
document, the form post, the prompt, catalog flags, the described event,
the ledger, usage errors), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/test_pk_admin.py` (the wrapper's prompting list),
`tests/stewardship/database/test_admin_integration_changes_cli_postgresql.py`
(mail settings reviewed, prompted, applied and repeated with the command
event and fresh-gate trail, a Slack change and removal with the policy
notice, the off-site folder added and removed, a command token confirmed
on the page and a page token by the command, the page's refusals, Slack and
mail not set up, foreign tokens, a read-only session and Staff denied, an
ended session) and the unchanged integration page suites.

`integration preview parishsoft --refresh-schedule` (#632 slice 5b) posts
a stored-shape refresh schedule as the page editor's own rows, so the
page's `ScheduleEditor` checks it and derives the stored settings, and
`integration show parishsoft` prints the schedule as the page shows it,
with no schema change; proven by the schedule tests in
`tests/stewardship/test_admin_integration_changes.py` (the document's
shape, the rows read back, the switch and mode, the page's problems at
each row, the review) and
`tests/stewardship/database/test_admin_integration_changes_cli_postgresql.py`
(a schedule reviewed and applied, the same patch as the page's review of
the same rows, an unchanged schedule kept exactly, the refusals).

`integration schedule-preview [--refresh-schedule DOCUMENT|-]` (#632 slice 5a)
is the ParishSoft page's live schedule check: the applied or a proposed
schedule, read by the page's `ScheduleEditor` and previewed by the page's
`_schedule_context`, with the page's problems reported rather than refused;
any session, Administrators only, the role first, no event, no schema
change. Proven by the schedule preview tests in
`tests/stewardship/test_admin_integrations.py` (the page's preview words,
problems at each row, an unreadable row, the refusals after the recheck,
the catalog and ledger) and
`tests/stewardship/database/test_admin_integrations_cli_postgresql.py`
(the applied schedule as `integration show` prints it, a proposed one from
standard input, problems at exit 0, refusals without echo, Staff denied,
an ended session).

Another part adds `integration backup-probe` (the Off-site backups page's
Test access) and `integration dismiss` (a key change line's Dismiss) in
`parishkit.stewardship.admin_integration_actions`, through the page's own
check (moved into `integration_views.queue_probe`, which the page's Test
access now calls) and `integration_credentials.dismiss` (which now returns
whether it dismissed and takes a callback for the command's event), with
no schema change; proven by
`tests/stewardship/test_admin_integration_actions.py` (catalog flags, the
described events, the ledger, usage errors),
`tests/stewardship/test_admin_cli.py`,
`tests/stewardship/database/test_admin_integration_actions_cli_postgresql.py`
(a check of the saved folder and of a given link queued with the command
event and no Google contact, no folder, a bad link, Workspace not set up,
the local environment, a changed Workspace mailbox; a finished line
dismissed with both events, a pending or other request dismissing nothing,
a required integration not set up, a read-only session and Staff denied,
an ended session) and the unchanged integration and backup page suites.

Another part adds `integration key status` (the Key replacement status
page) and `integration key finish-switching-preview` and
`integration key finish-switching` (Finish switching to the new key) in
`parishkit.stewardship.admin_integration_keys`, through the pages' own
`secret_request_status`, `_selectable`, `_selection`, `_changes` (which now
also names each setting's field) and salt, with no schema change; proven by
`tests/stewardship/test_admin_integration_keys.py` (catalog flags, the
described event, the ledger, usage errors, no fingerprint in any result
field, a watch pinned to its first key change), `tests/stewardship/test_admin_cli.py`,
`tests/stewardship/database/test_admin_integration_keys_cli_postgresql.py`
(a failed switch shown, reviewed, prompted and finished with its request,
events and notices, a repeat recording nothing new, a page-signed token
confirmed by the command from standard input, altered, other pages',
another Administrator's, expired and earlier key changes' tokens, nothing
to switch, a key change still running, another Administrator's, another
integration's or an unknown key change, a key no longer current, a changed
loaded organization on review and on confirm, read-only, Staff and ended
sessions, and no key or fingerprint in any output outside the page's own
signed token, which is decoded and checked apart) and the unchanged
integration key page suites.

Another part adds `integration key replace` (a settings page's Save with a
new key) in `parishkit.stewardship.admin_integration_keys` and the host
wrapper's `--secret-file` and `--secret-stdin`, through the page's own
forms and its request-free `integration_views.sign_key_intent`,
`key_intent` and `stage_key` (moved out of `_save`, which now calls them)
and the unchanged `save_credential` path (now with an `attach` callback, an
unrecorded first freshness check in its own transaction, and the caller's
authority runtime passed to `admit_admin_action`), with no schema change;
proven by `tests/stewardship/test_admin_integration_keys.py` (catalog
flags, the described event, the ledger, usage errors, the key line's
parser, size and encoding refusals, the prompt's words, the role first),
`tests/stewardship/test_admin_role_first.py`,
`tests/stewardship/test_pk_admin.py` (the tagged line after the preamble
and never an argument or file, the limit equal to the page's, a
group-readable, linked, FIFO, missing or oversized file refused, a piped
key needing `--yes`, misused options refused, echo off at a terminal and
back for the answer),
`tests/stewardship/database/test_admin_integration_key_replace_cli_postgresql.py`
(a key staged, installed by the isolated installer, acknowledged and
switched with its events and notices, a declined prompt staging nothing,
settings saved with the key, refused settings and keys at their fields, a
malformed key line, a pending key change and a stale version, read-only,
Staff and ended sessions, Slack set up by its first token, a refused Slack
format before the prompt, and no key in any output, log or audit row) and
the unchanged integration key page suites.

Another part adds the Backup encryption key's steps,
`integration backup-key challenge`, `preview` and `confirm`, in
`parishkit.stewardship.admin_backup_key`, through the page's own
`check_new_key`, `start_proof`, `finish_proof`, a request-free
`backup_key.review_change` (moved out of the page's `_review`, which now
calls it) and `admin_editing.confirm_intent` with the page's salt, and
generalizes the host wrapper's secret input to a per-command table
(`--public-key-file`, `--code-file` and `--code-stdin` beside
`--secret-file` and `--secret-stdin`), with no schema change; proven by
`tests/stewardship/test_admin_backup_key.py` (catalog flags, the described
event, no option taking a value, the field limit equal to the page's, the
tagged lines' parser, size and encoding refusals, usage errors, the role
first), `tests/stewardship/test_admin_role_first.py`,
`tests/stewardship/test_pk_admin.py` (each value on its own tagged line
and never an argument, an oversized code file refused, a piped code with
no `--yes`, misused options refused, a code typed at a terminal not
echoed), `tests/stewardship/database/test_admin_backup_key_cli_postgresql.py`
(challenge, a real proof, a wrong code, review, a declined prompt,
confirmation with its trail, the installer applying it with
`backup_key_replaced`, a repeat; a private key, a non-key, the key in use
and an over-long paste refused without echo; an altered, expired or
out-of-date intent and an altered token; read-only, ended and Staff
sessions; no private key, code or pasted text in any output, log or audit
row) and the unchanged backup key page suites.

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
- [x] ADM-12.10 — NAV-7: move Parish data and Users URLs and the change status URL.
- [x] ADM-12.11 — NAV-8: move Mail and Family portal URLs.
- [x] ADM-12.12 — NAV-9: move Campaign setup URLs, part A, and add the group root.
- [x] ADM-12.13 — NAV-10: move Campaign setup URLs, part B (go-live chain, test email, Campaign Ministries).
- [x] ADM-12.14 — NAV-11: move report URLs, including response lists and the reports root.
- [x] ADM-12.15 — NAV-12: move export and digest URLs and fold the latest-data export into the shared export page.
- [ ] ~~ADM-12.16 — NAV-13~~ — Dropped, not done: sign-in, setup and maintenance keep their addresses without a trailing slash (#522).
- [x] ADM-12.17 — NAV-14: add the Emailed reports page.
- [ ] ~~ADM-12.18 — NAV-15~~ — Dropped, not done: the Users revamp (#952) replaces the planned Portal users split (#535).
- [x] ADM-12.19 — NAV-16: add the ways back (#521), including the test-email origin kept in the session.
- [x] ADM-12.20 — NAV-17: add the reachability and no-UUID crawl test.
- [x] ADM-12.21 — NAV-18: add Home's Next steps for each state and role and the per-role Today line.
- [x] ADM-12.22 — NAV-19: add the Find a Family header search (#561).

Evidence: Done (ADM-12.16 and ADM-12.18 dropped). ADM-12.00 merged in PR #568: the
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
envelope number followed in #664 (frozen migration
`0022_directory_member_search.sql`, proven by
`tests/stewardship/database/test_directories_postgresql.py` and
`tests/stewardship/database/test_directory_member_search_migration_postgresql.py`).
It is proven by
`tests/stewardship/test_find_family.py` (who gets the box, its markup and the
results fragment), `tests/stewardship/database/test_directories_postgresql.py`
(results, POST-only refusals, no codes, `no-store`, audit without the text,
Staff allowed and a Ministry leader refused),
`tests/stewardship/database/test_identity_performance_postgresql.py` (the
Admin shell's query budget unchanged) and
`tests/stewardship/browser/test_find_family.py` (one request after a pause,
the keyboard path, refusals, WCAG checks at phone and desktop widths, on
Chromium and WebKit).
ADM-12.20 (NAV-17) adds
`tests/stewardship/database/test_admin_reachability_postgresql.py`. As
Administrator and as Staff, on a new Testing draft and on a campaign with a
complete ParishSoft capture, it follows every GET link from Home and checks
[navigation rules](../../specs/stewardship/ui-conventions/spec.md#navigation-rules)
1 (every page without route arguments that opens is reached) and 3 (every
reached page but Home has a linked parent crumb or a "Return to" link), and
that no `href`, `action`, `src` or `data-*-url` names the current campaign.
Held emails, linked from Home only after a restore, is proven by its own
suite; a `PENDING` set for pages awaiting #521 starts empty and may only
shrink. The same checks also run with no current campaign, as a Ministry
leader (who reaches Ministry requests and Ministry follow-up), and on a live
campaign in Production mode with every module and a configured Ministry, so
Financial stewardship, Pause and resume mail, Limitations and the Ministry
pages are crawled too. With no current campaign `/admin/reports/` now opens
Participation, whose greyed page says why, instead of a page of its own that
no link reached.
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
itself. Portal users keeps its name until the Users revamp (#952). Parish settings
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
access URLs moved with the Users revamp (#952) in PR #994, under
`/admin/users/`, which completes this task. It is proven by the
expected-redirect table in
`tests/stewardship/test_admin_url_scheme.py`, the Caddyfile render tests and
golden files, and the updated Parish data and change suites.
ADM-12.11 (NAV-8) moves the Mail and Family portal pages under `/admin/mail/`
(Pause and resume mail at `controls/`, Family email progress and its status
fragment, Family email history, Sent mail and one message with its
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
`responses/<list>/` (CSV `csv/`; since removed with no redirect, #933),
Participation `participation/`, Financial
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

ADM-12.15 (NAV-12) gives every export one page, `/admin/reports/exports/<request>/`
(decision 8): a latest-data participation export now shares it, keeping its
own heading and trail, and each request id is handed to the view that owns
the record. The export's actions are nouns under it (`cancellation/`,
`retries/`, `download/`, `regeneration/`). The reports sent by email move
under `/admin/reports/emailed/`: daily `daily/<snapshot>/` (with its chart and
download images), weekly `weekly/<snapshot>/` and its items, and Send a weekly
report now `weekly/new/`. New report emails link the new addresses. No old
address is kept (#864): the old export, latest-data export, manual weekly
report and daily and weekly report addresses are gone (404), so a link in a
report email sent before NAV-12 no longer opens. The unused JSON export API
and the old postal routes were left for #758, which then removed both: no
page, script or command line used the JSON API, and the two postal addresses
get no redirect (#864; they are Admin-only). From the NAV-11 review: a moved
report opened with no current campaign says so plainly instead of "This
campaign is no longer the current campaign", and Ministry follow-up shows the
"no campaign" page as Ministry requests does. It is proven by
`tests/stewardship/test_admin_url_scheme.py` (the export and emailed report
rows, the new digest link shapes, the old digest addresses' 404, the shared
export page, the route order),
`tests/stewardship/database/test_admin_url_scheme_postgresql.py` (the old
export and digest addresses' 404 through the real middleware) and the
updated export, latest-data export, daily and weekly report suites.

ADM-12.17 (NAV-14) adds Emailed reports at `/admin/reports/emailed/`, a
Responses and reports menu entry for Administrators and Staff that is never
greyed out: the current campaign's daily reports (and, for Administrators,
its weekly reports, those sent on request marked so), newest first, each
linking its page. Send a weekly report now leaves the menu for a button on
the page, and after queuing it returns there with `?requested=<request>`,
which links the report it produced or, until it is ready, its background
task. The emailed report pages and Send a weekly report now sit under it in
the trail. It is proven by `tests/stewardship/test_admin_navigation.py` (each
role's menu), `tests/stewardship/test_admin_page_names.py`,
`tests/stewardship/test_admin_url_scheme.py` and
`tests/stewardship/database/test_emailed_reports_postgresql.py` (the list for
each role, the request notice, the manual form returning to the page).

Follow-up #864 drops every old Admin address that NAV-6 to NAV-11 kept as a
redirect (the Administrator's decision of 2026-10-08, reaffirmed 2026-10-09):
`admin_urls/legacy.py` and its campaign-UUID 410 handling are removed, so
each old address, the retired campaign choosers and the old Ministry reports
root included, answers 404. Each page's form without its trailing slash
still redirects to the page (`admin_urls/slashless.py`), the header's
presence count keeps polling `/admin/presence?format=count`, and the old
hosted-file upload address leaves the ingress's large-body route. The
checklist text above records what each NAV pull request delivered at the
time. It is proven by `tests/stewardship/test_admin_url_scheme.py` (old
addresses resolve to nothing, every slashless row) and
`tests/stewardship/database/test_admin_url_scheme_postgresql.py` (old
addresses are 404 through the real middleware, the presence count still
polls).

ADM-12.19 (NAV-16) adds the ways back for #521: Preview and test email
returns to the page it was opened from, kept in the signed-in session
(PR #1230); the breadcrumb is the way back on deep pages (PR #1250); help
text and status messages link the pages they name (PRs #1265 and #1277);
and hand-written "Return to" links that repeat the trail are dropped
(PRs #1344 and #1348). It is proven by
`tests/stewardship/database/test_admin_reachability_postgresql.py` and the
updated page suites.
ADM-12.21 (NAV-18) adds Home's Next steps for each campaign state and role
and the per-role Today line (`accounts/home_steps.py`, PR #1246). It is
proven by `tests/stewardship/database/test_home_next_steps_postgresql.py`
and `tests/stewardship/browser/test_home_next_steps.py`.
ADM-12.16 (NAV-13) is dropped (#522): moving sign-in, setup and
maintenance to the trailing-slash rule changes exact paths in the access
gate's allowlists and about 300 test lines for no visible gain, and
under #864 it would turn their current addresses into 404s or extra
redirects.

## ADM-13: System health page

Scope and dependencies: [ADM-13 work package](../../plans/stewardship/admin-portal.md#adm-13-system-health-page).
Specification: [System health](../../specs/stewardship/admin-portal/spec.md#system-health).

- [x] ADM-13.00 — PR 0: specify the System health page and its actions, record the Administrator's decisions, and add this package and its checklist.
- [x] ADM-13.01 — PR 1: add service status records and record every drop count with a refused refresh (migration).
- [x] ADM-13.02 — PR 2: add the read-only System health page, with its read command or its pending exemption.
- [x] ADM-13.03 — PR 3: add Take a backup now through a durable request and request mode (migration), with its command or its pending exemption.
- [x] ADM-13.04 — PR 4: add Clear the halt with halt identities, the mailbox check and the clear signal (migration), with its commands or their pending exemption. It lands as ADM-13.16 (PR 4a) and ADM-13.17 (PR 4b).
- [x] ADM-13.05 — PR 5: add Accept this change once, bound to every recorded count, with example Families (migration), with its commands or their pending exemption.
- [x] ADM-13.06 — PR 6: add the debug-off switch, its in-process override, its host clear command and Allow debug logging again (Testing only, refused in Production) (migration), with its command or its pending exemption.
- [x] ADM-13.07 — specify the System health dashboard (#1214): the overall status line, the tiles, the problem registry, opening and closing tiles, Home and the critical-problems banner, and the delivery slices.
- [x] ADM-13.08 — dashboard slice 1: the problem registry, its severities and tiles, and the new kinds. (The registry's tiles and severities for the existing kinds, the split of a grouped process problem and the document's `severity` and `tiles` came with ADM-13.09 and ADM-13.10, and the shared banner words with ADM-13.15; the new shared kinds, read in the statement Home already runs, the unknown WorkGroup's error while Reminders are scheduled, the backup key rule and Home's key fallback came last.)
- [x] ADM-13.09 — dashboard slice 2a: the overall status line, its Not current state, the status indicator icons and the contrast test.
- [x] ADM-13.10 — dashboard slice 2b: the tile frame, the tile grid and its bands, its mirror targets, the tiles' Not current state, and the moved, always-drawn controls.
- [x] ADM-13.11 — dashboard slice 3: the Mail tile: its Working summaries in a fixed order, one line for both mail senders while they agree, and its explanations in toggletips and "About this page". (A halt's kind and Clear the halt remain with ADM-13.04.)
- [x] ADM-13.12 — dashboard slice 4: the ParishSoft refresh tile: its Working summaries in a fixed order, the refused counts inside the refusal's problem item with Accept this change once right under them, and a panel that repeats none of its problems.
- [x] ADM-13.13 — dashboard slice 5: the Backups and Debug logging tiles: their Working summaries in a fixed order, the Backups page findings as Warning items of the tile's problems, and panels that repeat none of their tiles' problems.
- [x] ADM-13.14 — dashboard slice 6: the page-load and services tiles: their summaries and states chosen in one place, each page-load finding as its tile's problem item with its link drawn only while relevant, a Version panel that repeats none of its problems, and Accept this change once's step-up notice only while the sign-in is stale.
- [x] ADM-13.15 — dashboard slice 7: Home and the critical-problems banner.
- [x] ADM-13.16 — PR 4a: the halt identities in the marker, the halt record, the clear signal and the mailbox check request with their guards and grants (frozen migration 0115), the consumers recording halts and reading the signal, the mailbox check in mail-dispatch, `mail_halt_cleared`, and `system mail-clear-preview` and `system mail-clear`.
- [x] ADM-13.17 — PR 4b: the Clear the halt button in the Mail tile, with its preview, the in-place mailbox check and the critical-problems banner's acknowledgement of the halt's `mail_provider_failed` entries.

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
ADM-13.02 landed in three parts. Part 2a (#670) adds the read model
`parishkit.stewardship.system_health` (`SystemHealth`), the Administrator-only
page at `/admin/system/health/` with its problems list and six panels, the
passive 10-second status fragment, `/admin/system/` opening it, the first
System menu entry, the `system_health_viewed` audit event and the debug
logging and critical-problems banners' links, with no schema or grant change, checked by
`tests/stewardship/test_system_health.py`,
`tests/stewardship/database/test_system_health_postgresql.py`,
`tests/stewardship/browser/test_system_health.py` and the navigation and
route-parity tests. Part 2b adds Home's System health problem lines for
viewers who may open the page (#679; the page's own sentences, shared through
one template, read in the statement Home already runs for its ParishSoft
status, so Home's query count is unchanged) and the
`system health` command with `--watch` (stopping once nothing needs
attention), checked by the golden document and allowlist in
`tests/stewardship/test_admin_reads.py`, `tests/stewardship/test_admin_cli.py`
and `tests/stewardship/database/test_system_health_postgresql.py`. Part 2c
(#684, #1082) shows the 24-hour count, through the definer function of frozen migration
0012, and the limit Family email stops at in the "Why sends are waiting"
panel and as `daily_sent` and `daily_limit` in the command's document,
with no schema or grant change, checked by
`tests/stewardship/test_system_health.py`,
`tests/stewardship/test_admin_reads.py`,
`tests/stewardship/database/test_system_health_postgresql.py` and
`tests/stewardship/browser/test_system_health.py`. When sending resumes
after the daily limit follows, through the definer function of frozen
migration 0076 (web EXECUTE only), shown while the count is at the limit
and as `daily_resume_at` in the command's document, checked by the same
tests and `tests/stewardship/database/test_mail_daily_limit_postgresql.py`
(#1171). ADM-13.02 is complete.
ADM-13.03 landed in two parts. Part 3a (#1039) adds the frozen forward
migration `0062_backup_request.sql` (the request record and its guard),
the web's and backup login's request grants and the web's read of the
newest backup's size and version, request mode (`backup --request`) with
the backup lock, the backups panel's size, version and request state, and
the backup runbook's request-mode cron entry, restore step and drill
warning, checked by `tests/stewardship/test_system_health.py`,
`tests/stewardship/test_runtime_grants.py` and
`tests/stewardship/database/test_backup_request_postgresql.py`. The Take a
backup now button follows as part 3b (#1040), which adds the button on System
health: a preview (the last backup and its size, off-site copies,
retention, and the wait during a bulk send), confirmation answered in place
with a repeated confirmation returning the first request, the fresh
sign-in step-up, the `backup_requested` audit event naming the request,
the greyed button with its reason while a request is live (kept current
by the page's poll), the steps posted to the System health page itself so
they are answered in place, refusals and the step-up answered inside the
section, and the commands owed by ADM-11 PR 5,
checked by `tests/stewardship/database/test_backup_now_postgresql.py` and
`tests/stewardship/browser/test_system_health.py`. ADM-13.03 is complete.
ADM-13.05 landed in three parts. Part 5a adds the frozen forward migration
`0080_drop_acceptance.sql` (`stewardship_source.0008`): the acceptance
record and its guard, the shared SQL lifetime check and the worker's read
grant; the full refresh honoring a live acceptance within the reviewed
counts (`parishkit.stewardship.source.drop_acceptance`), recording it in its
manifest, logging it and promoting only while it is still live; and the
trend of each count that failed starting again after it, checked by `tests/stewardship/test_source_drop_acceptance.py`,
`tests/stewardship/test_source_trend.py`,
`tests/stewardship/database/test_source_drop_acceptance_postgresql.py`, the
runtime-grant and users swap tests and the regenerated schema baseline.
Part 5b adds the web's insert and read grants on acceptances (a
`database-grants` change; the table was already in the users swap's
closure tables), Accept this change once on System health (drawn in the
ParishSoft refresh panel right under a refused change, in an element the
status updates keep, and acknowledging that refusal's critical problem
when confirmed: a preview of every count,
the "I checked this in ParishSoft" tick box, the fresh sign-in step-up, a
signed binding answered as "This changed since you looked" when the
refusal changed, the `source_drop_accepted` audit event naming the
acceptance, the full refresh it asks for, and the greyed button saying who
accepted it, when and until when, or that the acceptance lapsed or that an
empty load can never be accepted, kept current by the page's poll, all
answered in place), `pk-admin system refresh-accept-preview` and `system
refresh-accept`, and the launch and deployment runbook updates (the
operator override as the emergency path), checked by
`tests/stewardship/database/test_accept_once_postgresql.py`,
`tests/stewardship/test_system_health.py` and
`tests/stewardship/browser/test_system_health.py`. Part 5c adds the
frozen forward migration `0085_drop_examples.sql` (`stewardship_source.0009`):
the example Families table and its guard (the worker only, for a count
that failed whose drop count was recorded no earlier than the inserting
transaction began, never for an empty load; nobody changes one), and the one SQL function naming the refusal whose examples
may be shown, with the worker's insert, read and delete grants and the
web's read grant; the worker recording up to five example Families (DUID
and directory name, from the current data) per failing Family, Member or
contact count with each refusal (`parishkit.stewardship.source.drop_examples`),
best effort; the hourly maintenance deleting every example that may no
longer be shown (superseded, promoted over, accepted, or seven days old);
the preview listing them under each failing count with an "i" toggletip;
`system refresh-accept-preview`'s `examples` (DUIDs only); and the
operations spec, launch, backup and deployment runbook updates, checked by
`tests/stewardship/test_source_drop_examples.py`,
`tests/stewardship/database/test_source_drop_examples_postgresql.py`,
`tests/stewardship/database/test_accept_once_postgresql.py`,
`tests/stewardship/test_system_health.py`,
`tests/stewardship/browser/test_system_health.py`, the runtime-grant and
users swap tests and the regenerated schema baseline.
ADM-13.06 is in progress in parts. Part 6a adds the frozen forward migration
`0078_debug_off_switch.sql` (`stewardship_jobs.0028`): the switch record and
its guard, the one-boolean read every runtime login may run, the
admin-recovery-only clear and the `debug_logging_switched_off` event; the
in-process override (`parishkit.stewardship.debug_switch`) read on every new
database connection and before each service status record, with the
setting taken out of the process environment so helpers inherit none; and
the offline `pk-stewardship debug-off-clear` command, checked by
`tests/stewardship/test_debug_switch.py`,
`tests/stewardship/database/test_debug_off_switch_postgresql.py`, the
runtime-function tests, the users swap tests and the regenerated schema
baseline. Part 6b adds the web's insert and read grants on the switch (a
`database-grants` change, registered in the users swap's closure tables),
Turn off debug logging on System health (drawn while a service has debug
logging in effect: a preview, the fresh sign-in step-up, the
`debug_logging_turned_off` audit event naming the switch, the greyed
button with who set it and when, kept current by the page's poll, answered
in place), `pk-admin system debug-off`, the operator clear's
`debug_logging_allowed` event (actor kind `operator`, in the clear's
transaction), the panel's who-and-when line and the deployment and launch
runbook updates, checked by
`tests/stewardship/database/test_debug_off_now_postgresql.py`,
`tests/stewardship/database/test_debug_off_switch_postgresql.py`,
`tests/stewardship/test_system_health.py` and
`tests/stewardship/browser/test_system_health.py`. Part 6c (#1207) adds the
frozen forward migration `0088_debug_allow.sql` (`stewardship_jobs.0031`),
which replaces the switch's guard so the web login may clear it only in
Testing, for a freshly signed-in Administrator, once; the web's column
UPDATE grant on the clear; Allow debug logging again in the Turn off debug
logging region (a preview, the fresh sign-in step-up, `debug_logging_allowed`
naming the switch, greyed with its reason in Production or while no switch
is set, answered in place) and `pk-admin system debug-allow`; and the
banner's end condition, kept as the web process's own state with the spec
changed to match. It is checked by
`tests/stewardship/database/test_debug_allow_postgresql.py`,
`tests/stewardship/database/test_debug_off_switch_postgresql.py`,
`tests/stewardship/test_system_health.py`, the runtime-grant and users swap
tests and the regenerated schema baseline. ADM-13.06 is complete.
Follow-up #1229 adds the frozen forward migration
`0098_debug_off_clear_subject.sql` (`stewardship_jobs.0032`), which replaces
the operator's count-only clear with `stewardship_debug_off_clear_v2()`,
returning the cleared switch's id, so the operator's `debug_logging_allowed`
names the switch too, checked by
`tests/stewardship/database/test_debug_off_switch_postgresql.py`.
ADM-13.16 (PR 4a, the server side of Clear the halt) adds the frozen
forward migration `0115_mail_halt_clear.sql` (`stewardship_jobs.0033`):
the halt record (mail dispatch writes it), the clear signal and the
mailbox check request (the web writes them, for an Administrator with a
live session and no recent sign-in, #1547), each with its invoker guard,
and their grants through `runtime_grants`. The consumers name each halt in
the stop marker, record it and lift it from their idle loop
(`jobs/mail_halt.py`, `DeliveryCircuit.lift`), and the main process runs
the mailbox check through the provider-check helper; the shared service
functions (`mail_halt_clear.py`) back `pk-admin system mail-clear-preview`
and `system mail-clear` and record `mail_halt_cleared`. It is checked by
`tests/stewardship/database/test_mail_halt_clear_postgresql.py`,
`tests/stewardship/test_mail_halt.py`, `tests/stewardship/test_mail_consumers.py`,
`tests/stewardship/test_admin_cli.py`, the users swap test and the
regenerated schema baseline.
ADM-13.17 (PR 4b) adds Clear the halt to the Mail tile
(`system-health-clear-halt*.html`, `system_health_views._clear_step`): the
section only while a running mail sender reports Halted, the preview from
`mail_halt_clear.preview_clear`, Check again, and Confirm through
`clear_halt`, which now also acknowledges the halt's critical-problems
banner entries; the mailbox check and Confirm follow the polled status in
place. ADM-13.04 is complete. It is checked by
`tests/stewardship/database/test_mail_halt_clear_page_postgresql.py` and
`tests/stewardship/browser/test_system_health_clear_halt.py`.
