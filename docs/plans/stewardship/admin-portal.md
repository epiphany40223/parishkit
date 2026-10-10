# Administration portal implementation plan

Task status: [Administration portal checklist](../../tasks/stewardship/admin-portal.md).

This plan implements the
[administration portal specification](../../specs/stewardship/admin-portal/spec.md).
All routes, partial endpoints, job status, exports, and destructive workflows
remain below `/admin/` and apply server-side authorization.

## Work packages

### ADM-01: Login, denial, and unconfigured-state routing

1. Build `/admin/login`, Google initiation/callback integration, logout, and the
   complete re-login-capable denial/error pages using ARC-04.
2. Gate every Admin route on configured state, current roles, restore state, and
   appropriate object scope.
3. Add neutral unconfigured/maintenance behavior for non-Admin users and all
   Family routes.
4. Add audit events for success, denial class, logout, timeout, and revocation
   without provider tokens or raw denied identities.
5. Test direct/stale/partial requests and progressive-enhancement endpoints, not
   only browser navigation.

### ADM-02: Bootstrap command and transactional setup wizard

1. Extend `pk-stewardship bootstrap` with empty-deployment checks, public origin,
   initial Admin, Google/Django and all Family-code/email-token keyring
   references, database readiness, proxy configuration, and restore intent.
   Use OPS-04's operator-only offline bootstrap profile and canonical mount/
   startup guards; do not expose a web provisioning or repair endpoint.
2. Implement temporary wizard staging for parish, login rules, integration
   credentials/tests, complete source load, mail/Slack, and first campaign.
3. Implement correlated TaskRun progress polling with worker-heartbeat checks,
   renewal limit, and hard watchdog defined by the Admin specification.
4. Make cancel/session/watchdog failure expire staging, prevent late worker
   writes, and remove staged credentials/files idempotently.
5. Freeze setup, run target-specific secret installers, apply one complete YAML
   version/materialization, and commit the configured marker only after source,
   Family codes, Testing mode, digests, and consumer fingerprints agree.
   Use the [initial-setup abort protocol](../../specs/stewardship/data/spec.md#parish-and-integrations)
   for selected-but-unapplied cancellation. Commit database activation and the
   configured marker atomically; test cancellation/activation races and crash
   recovery on both sides of manifest restoration without rewinding applied history.
6. Add browser and worker-race tests for happy path, every abort boundary,
   timeout, and restored deployment skip.

### ADM-03: Navigation, dashboard, indicators, and configuration

1. Build role-filtered navigation and home dashboard using DOM-04 components.
2. Add Testing, restore, delivery-pause, go-live-cleanup, and critical-health
   persistent banners with authorized links.
3. Implement presence/background indicators and detail drawers backed by
   non-idle-renewing polling and authorized data.
4. Build parish, branding, timezone, contact, mode, integration, sender, Slack,
   and secret replace/test pages with YAML change-request and sealed target-
   installer progress, version diff, optimistic digest concurrency, and audit.
   Include the [Ministry activity editor](../../specs/stewardship/admin-portal/spec.md#ministry-activity-management),
   with persistent tenant/DUID overrides, impact preview and applied-status UI.
   Its activation must atomically reevaluate seeded assignment overlays without
   changing manual assignments or the campaign's structural selection.
5. Make Parish-timezone edits explicitly prospective: they affect general
   presentation and future drafts but never mutate an existing campaign.
6. Add logo variant preview and safe branding-version handling.
7. Test all role variants, browser sizes, applying/applied/error states, stale
   saves, config-installer crash recovery, secret expiry/failure rollback,
   campaign-timezone isolation, and browser-local timestamp rendering.
   Test Ministry inactivation/reactivation, rename and catalog reappearance,
   import persistence, active-campaign edits, seeded-access effects, manual-role
   preservation and activity-change/source-promotion races.

### ADM-04: Campaign editor, content, schedules, and previews

1. Implement new/clone campaign workflow with the single-current-campaign and
   Testing guards through applied YAML versions and normalized snapshots.
2. Build campaign-timezone, module-dependent dates, financial periods/funds,
   Ministries, share options, additional-information toggle, mail/digest
   schedules, and structural lock UI/server validation; initialize timezone
   from Parish and keep it editable only in `draft`.
3. Build content/template WYSIWYG and plain-text controls with named-slot maps,
   placeholder validation, immutable versions, and empty optional slots.
4. Implement page/email previews using safe sample or explicitly selected
   Family, including readiness-test sends that never satisfy live schedules.
5. Implement atomic schedule edit/removal previews and conflict handling for
   in-flight/unknown work, including the combined reconciliation editor required
   when an end-date shortening would strand future Family mail.
6. Add form/request tests for hidden stray values, pending/mismatched config,
   locking, cloning exclusions, preview privacy, and schedule races.

### ADM-05: Production transition and pre-start withdrawal

1. Build readiness checks and exact impact preview for configuration, source,
   integrations, templates, Admin recipients, Family populations, due-work
   coalescing, and terminal Testing outbox.
2. Implement ProductionTransitionRequest creation, irreversible acknowledgement,
   campaign go-live gate, progress/retry/cancel UI, and BG-03 batched cleanup.
   Invalidate rehearsal credentials/sessions at gate acquisition and include
   their sensitive detail in cleanup readiness and final activation checks.
3. Implement fresh-auth typed final confirmation and the short atomic
   draft-to-scheduled/direct-active transition with commit-time boundary check.
   Direct activation inserts only the durable catch-up demand/task and version
   guards; BG-04 materializes overdue work asynchronously. Show active campaign
   separately from scheduled-mail preparation progress/hold and safe retry.
4. Implement guarded scheduled-to-draft withdrawal, reason, cancellation
   preview, unknown-delivery blockers, Testing return, readiness invalidation,
   and structural unlock.
5. Test cleanup interruption/cancel, changing readiness, start/close races,
   direct catch-up, no partial live state, and repeated go-live attempts.
   Measure final confirmation/lock hold time at the 5,000-Family reference load
   with many overdue schedule revisions; prove no per-Family occurrence writes
   occur there and Family submissions remain responsive during catch-up.

### ADM-06: Restore release, delivery pause, reopen, and archive

1. Build restore-state inventory and maintenance-only controls, delivery-
   uncertainty holds, assumed-delivered/resend resolutions, and atomic state-
   aware release confirmation, preserving Production for a sole current
   scheduled, active, closed, or archived campaign; archived-current preserves
   its pointer for later unarchive/Return, and closed/archived releases keep
   Family access and live Family mail disabled.
   Prepare fresh tokens for scheduled/active release through BG-02, display
   old-link invalidation/manual-code fallback, and atomically activate the
   current restore-epoch generation after manifest rechecks. Do not send
   replacement mail or resolve holds implicitly.
2. Implement delivery pause/resume with fresh authentication, impact counts,
   pre-provider recheck, held message visibility, coalescing, and post-close
   receipt/digest resolution.
3. Implement closed-campaign end-date extension/readiness/reopen directly to
   active, background token-generation preparation/progress/retry/cancel,
   short pointer activation with pinned-input rechecks, future-only schedules,
   and no replay of skipped work.
4. Implement archive eligibility, unarchive-to-closed guards, and the dedicated
   post-archive Return to Testing workflow that clears the current pointer and
   presents the purge-before-successor decision window. Inventory all receipt/
   digest obligations, including unmaterialized future slots, and provide
   explicit reasoned skip resolutions with coverage preview and safe work
   cancellation. Recheck the shared inventory transactionally at archive/Return.
5. Test restore/pause/reopen/archive races, held messages, inconsistent state,
   successor denial, and audit/reauthentication; include final-day/weekly mail
   not yet due, failed/unknown delivery, stale coverage, and durable skips across
   scheduler retries, schedule revisions, and unarchive/reopen.

### ADM-07: User rules and Ministry assignments

1. Build sorted domain/address role tables with YAML-backed autosave,
   Applying/Applied/error status, optimistic active-digest checks, explicit
   deny, disabled domain Admin, and `gmail.com` validation.
   Implement the canonical per-page logical-intent queue, one nonterminal
   request at a time, applied-version handoff, and distinct queued/applied
   indicators. Use idempotent request/status reconciliation for uncertain
   responses and inline conflict review without automatic stale-payload rebase.
   Cover page-exit warnings and never replay an unsent queue after reload.
2. Preserve the selected no-reauth/no-confirmation policy for every role change
   while enforcing CSRF, current-Admin authorization, last-Admin protection,
   and complete before/after audit; an exact-address Administrator grant,
   creation of any domain rule, or addition of Staff to an existing domain rule
   additionally creates a persistent security event and notifies all preexisting
   Administrators.
3. Build chairperson suggestion review, inherited-role preview, bulk selection,
   confirmed YAML rule/assignment requests, and suspended/source-return review.
   Display rule/grant provenance and provide the explicit Keep role independently
   action; preserve origins on unrelated autosaves and remove all grant origins
   on explicit role removal without implicitly creating Ministry scope.
4. Build YAML-backed manual Ministry assignments while source synchronization
   changes only the fail-closed runtime suspension overlay.
5. Test hosted-domain behavior, precedence, concurrent digest changes,
   activation-time revocation, Administrator-grant notification failure/retry/
   acknowledgement, and Ministry row-scope updates.
   Test rapid edits across rows/tables and repeated toggles of an in-flight
   checkbox, slow installers, lost acceptance/activation responses, failed
   requests, other tabs/Admins, removed targets, session expiry/revocation,
   page teardown, and exact-once request/audit/notification behavior.

After launch, #922 replaced Ministry assignments and Chairperson suggestions
with Ministry leaders from ParishSoft roles, and the Users revamp (#952)
replaces the rule tables, hosted-domain rules and autosave above with one
table of users, each with one role, saved through Review and Apply. The
current behavior is the specification's
[Portal user management](../../specs/stewardship/admin-portal/spec.md#portal-user-management); the
slices of #952 are tracked on that issue.

### ADM-08: Manual refresh, follow-up queues, and logs

1. Add idempotent manual full-refresh trigger/status with request coalescing and
   detailed authorized task phases.
2. Build additional-information and manual-census queues with filters, durable
   notes, follow-up state/history, correction dispositions, and optimistic
   editing.
3. Build Ministry follow-up queues with assignment/outcome/contact attempts and
   leader row scope.
4. Build combined Admin-only operational/audit log UI with level/source/action/
   actor/entity/time filters, browser-local display, redacted detail, and
   asynchronous text/JSONL export.
5. Test correction history, unauthorized rows/columns, concurrent edits,
   default DEBUG exclusion, and export timezone selection.

### ADM-09: Census review and ParishSoft publication UI

1. Build searchable/filterable/paginated proposal review with current/submitted/
   proposed values, writability, conflicts, bulk decisions, edit, and reversible
   ignored/unreviewed/approved states.
2. Separate review decisions from publication plan creation and execution.
3. Build latest-source preflight, conflict resolution, selected-subset publish,
   progress, partial failure/retry, read-after-write results, and final refresh.
4. Preserve immutable Family-submitted value display when an Admin edits the
   publish proposal.
5. Test Staff view-only behavior, Admin-only API publication, stale plans,
   subset sessions, and every outcome.

### ADM-10: Exceptional campaign purge web workflow

1. Build `/admin/operations/purge/` eligibility selection and durable request
   resume/cancel/status UI; require completed Return to Testing, a null current
   pointer, and the canonical
   [purge eligibility guard](../../specs/stewardship/data/spec.md#job-outbox-audit-and-purge-records).
   Explain the recurring window without treating archived successors as blockers.
2. Acquire the campaign work gate, show/cancel/drain conflicting work, reconcile
   external uncertainty, and record quiescence.
3. Build dry inventory and **Create purge backup** asynchronous action with
   verified encrypted off-host reference, independent evidence expirations from
   the purge specification, and refresh of only the expired artifact.
   Add structured operator recovery-attestation entry bound to the selected
   backup, escrow/key manifests, and request. Display its separate expiry and
   dependent invalidation without uploading secrets or arbitrary attachments.
   Add asynchronous **Revalidate selected backup**, preserving the immutable
   backup and independent recovery expiry under the canonical purge workflow.
4. Require fresh authentication, exact campaign name and generated phrase, then
   queue the idempotent purge worker after atomic prerequisite recheck.
5. Expose safe pre-delete rollback, resumable deletion, cleanup retry, terminal
   tombstone, and CRITICAL failure behavior without offering forbidden rollback.
   Show the existing-reader/download drain phase and timeout recovery before
   the first deletion checkpoint; do not invent an additional request state.
6. Add exhaustive browser/PostgreSQL race tests for every gate/state/pointer/
   successor/expiry/interruption path.
   Cover off-host recovery checks exceeding backup-evidence lifetime followed
   by same-backup revalidation, independently expired recovery evidence, and
   expiry at claim or after reader drain before the first deletion batch.

### ADM-11: Admin automation interface

Implements the [Admin automation interface
specification](../../specs/stewardship/admin-automation/spec.md) for
[#463](https://github.com/epiphany40223/parishkit/issues/463). It is
post-launch work outside the phase and gate sequence. Each item is one pull
request with independent review and full CI; normal CI stays credential-free.
PR 2 and PR 5 (items 3 and 6) change the schema through forward migrations
under the [post-launch schema
policy](../../specs/stewardship/operations/spec.md#post-launch-schema-policy),
as the specification's [schema
impact](../../specs/stewardship/admin-automation/spec.md#schema-impact) states;
any other schema need amends the specification first. The specification's
[delivery plan](../../specs/stewardship/admin-automation/spec.md#delivery-plan)
owns each PR's scope and dependencies, its [extractions by
PR](../../specs/stewardship/admin-automation/spec.md#extractions-by-pr) the
view-to-service moves, and its [testing
requirements](../../specs/stewardship/admin-automation/spec.md#testing-requirements)
the tests; items here only name them.

1. **PR 0, specification.** The specification, this package and its
   checklist; the Administrator's decisions recorded on #463. Tests: Markdown
   lint and the traceability test.
2. **PR 1, seam only.** `AdminCaller` and its web constructor; no behavior
   change.
3. **PR 2, automation sessions.** The migration for the session and notice
   tables, the automation incident kinds and the maintenance task type;
   pairing, the host wrapper and session file, approval and access pages,
   notices, the maintenance task, `revoke-automation-sessions` and its restore
   step, session commands, refusals, lifecycle and audit.
   Security-focused review.
4. **PR 3, read-only status.** Read models, status commands including
   `schedule show`, `--watch` and the route-parity test, in two parts: 3a
   with the live-campaign reads and the route-parity test, 3b with the
   go-live reads.
5. **PR 4, schedules.** Schedule preview and confirm, and configuration
   request status.
6. **PR 5, fresh-gate acceptance.** The guard migration letting six SQL
   guards (four session-bound fresh-gate guards and two secret request
   guards), plus any ADM-13 System health guard already installed, accept a full-scope automation session, caller-aware freshness
   checks, the confirmation prompt and `--yes`. Security-focused review.
7. **PR 6, refresh and Testing sends.** Refresh, sample and chosen-Family
   tests.
8. **PR 7, delivery controls.** Pause, resume, closed-campaign resolution and
   Family portal maintenance.
9. **PR 8, reports and exports.** Report reads, exports, digests and logs.
10. **PR 9, operations.** Task retries, deliveries and refusals.
11. **PR 10, other configuration.** Campaign, content, Ministries, parish,
    hosted files, artwork, integration settings, and secret replacement
    (integration keys, finish switching and the backup key) with the
    wrapper's secret input. Follows PR 5. Security-focused review.
12. **PR 11, users and follow-up.** Users, rules (including the high-impact
    changes), assignments, chairpersons, acknowledgements and follow-up.
13. **PR 12, go-live and withdrawal.** The go-live sequence and withdrawal.

### ADM-12: Admin navigation overhaul

Moves the Admin portal to the
[Admin navigation](../../specs/stewardship/admin-portal/spec.md#admin-navigation)
structure and the
[JavaScript requirement](../../specs/stewardship/admin-portal/spec.md#javascript-requirement),
through #520 (one name per page), #521 (reachability and ways back), #525
(URL scheme), #561 (Find a Family) and #565 (JavaScript gate). It is
post-launch work outside the phase and gate sequence, with no schema change.
Each row is one pull request with independent review and full CI. The
[implementation plan on #522](https://github.com/epiphany40223/parishkit/issues/522)
owns each PR's files, tests, dependencies and risks; this table only orders
them. The URL PRs (NAV-6 to NAV-12) also update their rows in the spec's
placement table, the other specs that name those URLs, and the guides.

| Tasks | PR | Scope | Lane |
| --- | --- | --- | --- |
| ADM-12.00 | NAV-0 | Spec fixes for the plan's decisions; this package and its checklist | Any time |
| ADM-12.01 | NAV-1 | JavaScript gate on every Admin and sign-in page (#565) | Serial |
| ADM-12.02–.04 | NAV-2 | Registry rewrite: seven groups, stable menu shape with greyed entries and reasons, collapsible groups and Sign out | Serial |
| ADM-12.05 | NAV-3 | Grey out multi-campaign controls, server refusals, remove New campaign (rule 10) | A |
| ADM-12.06 | NAV-4 | Names for Campaign setup and Mail, including Cancel go-live | A |
| ADM-12.07 | NAV-5a | Names for Parish data, Users and access, System and Home, and the setup wizard steps NAV-4 left (Review, Test email, Test Slack, Finish setup and the connection steps) | B |
| ADM-12.08 | NAV-5b | Names for Responses and reports | C |
| ADM-12.09 | NAV-6 | URL plumbing, per-group URL modules, System URLs | Serial |
| ADM-12.10 | NAV-7 | Parish data and Users URLs; change status URL | B |
| ADM-12.11 | NAV-8 | Mail and Family portal URLs | D |
| ADM-12.12 | NAV-9 | Campaign setup URLs, part A | A |
| ADM-12.13 | NAV-10 | Campaign setup URLs, part B (go-live chain, test email, Campaign Ministries) | A |
| ADM-12.14 | NAV-11 | Report URLs, including response lists | C |
| ADM-12.15 | NAV-12 | Export and digest URLs | C |
| ADM-12.16 | NAV-13 | Optional: trailing slash on sign-in, setup and maintenance | Last |
| ADM-12.17 | NAV-14 | Emailed reports page | C |
| ADM-12.18 | (dropped) | Not built: the Users revamp (#952) replaces the planned Portal users split | — |
| ADM-12.19 | NAV-16 | Ways back (#521): test-email origin, Return links, linked messages | Serial |
| ADM-12.20 | NAV-17 | Reachability and no-UUID crawl | Serial |
| ADM-12.21 | NAV-18 | Home Next steps and Today line | Serial |
| ADM-12.22 | NAV-19 | Find a Family header search (#561) | C |

NAV-1 and NAV-2 come first (NAV-1 merges after #559), and NAV-6 merges
before any lane starts its URL work. Lane A runs NAV-3, NAV-4, NAV-9 and
NAV-10 (NAV-9 needs NAV-4 and NAV-6); lane B runs NAV-5a and NAV-7;
lane C runs NAV-5b, NAV-11, NAV-12, NAV-14 and then NAV-19 (which also needs
NAV-1), after the in-flight report work in #553, #567, #559 and #477 PR 5
merges; lane D runs NAV-8. NAV-16 (after NAV-10 and NAV-11) to NAV-18 come at
the end, and NAV-13 is last and optional.

### ADM-13: System health page

Implements the
[System health](../../specs/stewardship/admin-portal/spec.md#system-health)
page for [#530](https://github.com/epiphany40223/parishkit/issues/530): a
System menu entry at `/admin/system/health/` with plain-language panels and
four Administrator-only, fresh-gated, audited actions, each with a matching
command through the same service function. It is post-launch work outside
the phase and gate sequence. Each item is one pull request with independent
review and full CI. Normal CI stays credential-free. PR 1 and PR 3 to PR 6
(and PR 2 if it adds the #382 index) each change the schema through their
own forward migration under the
[post-launch schema policy](../../specs/stewardship/operations/spec.md#post-launch-schema-policy),
and each frozen SQL file ends with a check that raises unless it was
installed. The specification owns the behavior; items here only name each
PR's scope.

Dependencies: the System menu group and its URLs come from ADM-12 (#522,
NAV-2 and NAV-6), and PR 2 follows them. The commands need ADM-11's read
models (ADM-11 PR 3) and fresh-gate acceptance (ADM-11 PR 5). Until those
land, each ADM-13 PR records its command as a pending exemption in the
[action inventory](../../specs/stewardship/admin-automation/spec.md#action-inventory).
ADM-13's SQL guards are written without the automation clause; ADM-11 PR 5's
migration amends those already installed, and a guard installed after it
includes the clause from the start. The Administrator's
[decisions](../../specs/stewardship/admin-portal/spec.md#system-health-decisions)
of 2026-10-04 are recorded in the specification.

1. **PR 0, specification.** The System health section, the links from the
   operations and automation specifications, the corrected SYSTEMIC outcome
   wording in the Family mail dispatch guide and launch runbooks, this
   package and its checklist. Tests: Markdown lint and the traceability
   test.
2. **PR 1, status records and drop counts.** Service status records
   (migration and per-login grants, including the installers' new write
   grant), and the loader change that checks and records every drop count
   with the refused run. Security-focused review.
3. **PR 2, read-only page.** The read model and page with all six panels,
   the problems list, live updates, Home's problem lines and the debug
   banner's link, and the read command or its pending exemption. If #382
   has not added its item L9 daily-count index, PR 2 adds it in a forward
   migration and then gets a security-focused review. It lands in parts
   (defaults posted on #530): **2a** the read model, the page, its
   fragment, the problems list and the six panels, the menu entry and the
   debug and critical-problems banners' links, with no schema or grant
   change; **2b** Home's problem lines and the `system health` command;
   **2c** the 24-hour daily-limit count through
   a definer function with the L9 index (migration, security-focused
   review). The backups panel's size and version columns come with PR 3's
   grant change, and the halt kind with PR 4.
4. **PR 3, take a backup now.** The backup request (migration), the backup
   login's request grants, request mode with the backup lock and the hold
   during a bulk send, the host cron entry, and the backup runbook's schedule,
   checking, restore and restore drill updates. Security-focused review.
5. **PR 4, clear a halted mail sender.** Halt identities, the clear signal
   and the mailbox check request with their guards (migration), the check
   in `mail-dispatch`, the consumers reading the signal, and the dispatch
   guide and mail-provider outage runbook updates. Security-focused review.
6. **PR 5, accept a large ParishSoft change once.** The one-time acceptance
   and its guard, and the example Families table with its deletion
   (migration), the drop check honoring the acceptance, the manifest
   record and the launch runbook update. Security-focused review.
7. **PR 6, turn off debug logging.** The debug-off switch and its guard
   (migration), the in-process override, children's environment, the
   `debug-off-clear` operator command, **Allow debug logging again**
   (`system_debug_allow`, Testing only, refused in Production by its SQL
   guard), the banner's end condition, and the Production activation and
   deployment runbook updates. Security-focused review.

PR 3 to PR 6 each add their command or its pending exemption.

PR 2c (#684, frozen migration 0012) adds the 24-hour count's definer
function and #382's L9 index. Showing the count in the "Why sends are
waiting" panel needs the page from PR 2a (#670), which is not on 2c's
base: whichever of #670 and #684 lands second adds the wiring
(`daily_sends()` in `system_health.read_health` and its sentence), as
tracked on #530.

## Review handoffs

- Review Gate 1 covers ADM-01.
- Review Gate 2 covers ADM-02 through ADM-04 and user-facing campaign setup.
- Review Gate 3 covers ADM-05, the delivery-pause subset of ADM-06, ADM-07, and
  ADM-08, with focused authorization and privacy review.
- Review Gate 4 is mandatory before merging the remaining restore-release/
  reopen/archive subset of ADM-06, ADM-09, or ADM-10 destructive/external-write
  workflows.
- ADM-11 is outside the review gates: each of its pull requests gets an
  independent review, and PR 2, PR 5 and PR 10 (items 3, 6 and 11)
  security-focused ones.
- ADM-12 is outside the review gates: each of its pull requests gets an
  independent review.
- ADM-13 is outside the review gates: each of its pull requests gets an
  independent review, and PR 1 and PR 3 to PR 6 (items 2 and 4 to 7), plus
  PR 2 if it adds a migration, security-focused ones.

## Completion criteria

- Every Admin route has positive and negative role/object-scope tests.
- Long-running or external work returns durable status instead of blocking a web
  request.
- Lifecycle, publication, restore, and purge mutations are transactional,
  reauthenticated where specified, confirmed, and replayable from audit.
