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

## Review handoffs

- Review Gate 1 covers ADM-01.
- Review Gate 2 covers ADM-02 through ADM-04 and user-facing campaign setup.
- Review Gate 3 covers ADM-05, the delivery-pause subset of ADM-06, ADM-07, and
  ADM-08, with focused authorization and privacy review.
- Review Gate 4 is mandatory before merging the remaining restore-release/
  reopen/archive subset of ADM-06, ADM-09, or ADM-10 destructive/external-write
  workflows.

## Completion criteria

- Every Admin route has positive and negative role/object-scope tests.
- Long-running or external work returns durable status instead of blocking a web
  request.
- Lifecycle, publication, restore, and purge mutations are transactional,
  reauthenticated where specified, confirmed, and replayable from audit.
