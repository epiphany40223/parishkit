# Stewardship pre-launch gate

This evidence map records the one pre-launch gate the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes) puts in
place of Review Gate 3 and the v1-relevant parts of Gates 4 and 5: an
integrated independent review of the paths v1 ships, reusing the recorded
reviews of merged pull requests under the
[Gate 2 evidence-reuse procedure](../plans/stewardship/overall.md#review-gate-2-family-data-privacy-and-ux)
and adding fresh integration reviews of the five areas the launch scope names.
Claude-only rounds count under the exemption extended through October 30,
2026, recorded as single-source. Gate exit requires no unresolved validated
Critical, High or Medium finding and then the human's explicit product,
security and operations approval of the launch and its known limitations;
nothing here infers that approval.

## Baseline and scope

The gate reviews `main` at `4b36435d` (PR #97's merge): every launch-critical
item except the gate itself has landed, including the release image, the
deployment, backup and launch runbooks, the smoke tools and the paused resend
correction. Rows are reused only for their v1-shipping paths; cut paths inside
the same pull requests (restore release and reopen, archive and Return to
Testing, purge, retention and compaction, publication, key rotation) are not
reused as approval and are checked here only for failing closed. Validation
bug fixes that land after this baseline receive a renewed focused review of
the affected scope before the gate exits.

## Reused component evidence

### Phase 0-1 foundations (Gate 1)

| Merged increment | Review evidence reused | PL scopes |
| --- | --- | --- |
| PR #8 Phase 0 skeleton (ARC-01/02, OPS-01, OPS-09 baseline, DOM-05 clock) | [Eight M0 rounds](../tasks/stewardship/milestones.md#phase-0-skeleton) | PL-I5 |
| PR #9 DAT-01.01/.04 storage conventions | [Three rounds](../tasks/stewardship/milestones.md#phase-1-secure-foundation) | PL-I5 |
| PR #10 configuration preparation | [Three dual-model rounds](../tasks/stewardship/milestones.md#configuration-preparation-increment), [boundary](stewardship-configuration-preparation.md) | PL-I5 |
| PR #11 configuration request intake | [Three rounds](../tasks/stewardship/milestones.md#configuration-request-intake-increment), [boundary](stewardship-configuration-requests.md) | PL-I5 |
| PR #12 configuration activation | [Three rounds](../tasks/stewardship/milestones.md#configuration-activation-increment), [boundary](stewardship-configuration-activation.md) | PL-I5 |
| PR #13 secret-request storage | [Three rounds](../tasks/stewardship/milestones.md#secret-request-storage-increment), [boundary](stewardship-secret-requests.md) | PL-I2, PL-I5 |
| PR #15 audit ownership | [Three dual-model rounds](../tasks/stewardship/milestones.md#audit-ownership-increment), [boundary](stewardship-audit-ownership.md) | PL-I3, PL-I5 |
| PR #16 TaskRun storage | [Three dual-model rounds](../tasks/stewardship/milestones.md#taskrun-storage-increment), [boundary](stewardship-taskrun-storage.md) | PL-I4, PL-I5 |
| PR #17 authorization and offline-recovery batch | [Three full-branch rounds](../tasks/stewardship/milestones.md#authorization-and-recovery-batch), [boundary](stewardship-authorization-foundation.md) | PL-I1, PL-I3, PL-I5 |
| PR #18 campaign configuration and lifecycle policy | [Three rounds](../tasks/stewardship/milestones.md#campaign-configuration-and-lifecycle-policy-batch), [boundary](stewardship-campaign-foundation.md) | PL-I1, PL-I4 |
| PR #19 Phase 1A completion (DAT-02, DOM-02) | [Four full-branch dual-model rounds](stewardship-phase-1a-completion.md) | PL-I1, PL-I4 |
| PR #20 Phase 1B identity/web (ARC-03/04/05/06/07, DAT-04) | [Three rounds](stewardship-phase-1b-reviews.md), [checkpoints](stewardship-phase-1b.md) | PL-I1, PL-I2 |
| PR #21 Phase 1C runtime (OPS-02/03/04, OPS-08 baseline) and Gate 1 | [Three rounds, rounds 2-3 cumulative over Phase 1](stewardship-phase-1c-reviews.md), [Gate 1 record](../tasks/stewardship/milestones.md#gate-1-foundation-and-security) | PL-I1, PL-I5 |

### Phase 2-3 source, setup and Family response (Gate 2)

| Merged increment | Review evidence reused | PL scopes |
| --- | --- | --- |
| PR #22 setup/source and fresh-install baseline | [Three full Phase 2 rounds](stewardship-phase-2-reviews.md), [two supplemental consolidation rounds](stewardship-phase-2-consolidation-review.md), [replacement-baseline delivery](stewardship-phase-2-simplification.md) | PL-I1, PL-I2, PL-I5 |
| PR #23 baseline/atomic response/revisit | [Three Phase 3A rounds and delivery](stewardship-phase-3a-reviews.md) | PL-I1 |
| PR #24 Family census | [Three rounds](stewardship-family-census-reviews.md) | PL-I1 |
| PR #25 Member census | [Three rounds plus CI correction review](stewardship-member-census-reviews.md) | PL-I1 |
| PR #26 terminal/proposed Members | [Three rounds and delivery closure](stewardship-member-requests-reviews.md) | PL-I1 |
| PR #27 Ministry responses | [Three rounds and protected delivery](stewardship-ministry-responses-reviews.md) | PL-I1 |
| PR #29 financial responses | [Three rounds, schema audit and protected delivery](stewardship-financial-responses.md) | PL-I1, PL-I3 |
| PR #30 Gate 2 integrated Family acceptance | [Five dual-source integration rounds and protected delivery](stewardship-gate-2-reviews.md), [acceptance](stewardship-family-acceptance.md) | PL-I1 |

### Phase 4 production scheduling, delivery and health

| Merged increment | Review evidence reused | PL scopes |
| --- | --- | --- |
| PR #31 durable delivery journal/outbox | [Three rounds and protected delivery](stewardship-delivery-reviews.md), [scope](stewardship-delivery-journal.md) | PL-I2, PL-I4 |
| PR #32 campaign lifecycle boundaries | [Three dual-source rounds and delivery](stewardship-campaign-boundaries.md) | PL-I1, PL-I4 |
| PR #33 Production-transition cleanup | [Three dual-source rounds and protected delivery](stewardship-production-cleanup.md) | PL-I4 |
| PR #34 schedule planning/replacement | [Three rounds and protected delivery](stewardship-schedule-planning.md) | PL-I4 |
| PR #35 activation catch-up preparation | [Three dual-source rounds](stewardship-activation-catchup-reviews.md), [protected delivery](stewardship-activation-catchup.md) | PL-I4 |
| PR #36 export foundation | [Three dual-source rounds and protected delivery](stewardship-export-foundation.md) | PL-I3 |
| PR #37 Family deliverability/suppressions | [Three dual-source rounds and protected delivery](stewardship-family-deliverability.md) | PL-I2 |
| PR #38 Family mail preparation | [Three dual-source rounds and protected delivery](stewardship-family-mail-preparation.md) | PL-I2 |
| PR #39 Family mail provider dispatch | [Three rounds and protected delivery](stewardship-family-mail-dispatch.md) | PL-I2 |
| PR #40 Admin delivery resolution | [Three dual-source rounds](stewardship-family-mail-resolution.md); delivery recorded only in the [task plan](../tasks/stewardship/overall.md#phase-4-production-scheduling-and-delivery) | PL-I2, PL-I4 |
| PR #41 ordinary report facts | [Three dual-source rounds and delivery](stewardship-report-facts.md) | PL-I3 |
| PR #42 report selection | [Three dual-source rounds and delivery](stewardship-report-selection.md) | PL-I3 |
| PR #43 exact report facts/exports | [Three dual-source rounds and protected delivery](stewardship-exact-exports.md) | PL-I3 |
| PR #44 scheduled fact verification | [Three dual-source rounds](stewardship-fact-verification.md); delivery recorded in the [task plan](../tasks/stewardship/overall.md#phase-4-production-scheduling-and-delivery) and [successor](stewardship-campaign-statistics.md) | PL-I3 |
| PR #45 campaign statistics | [Three dual-source rounds and protected delivery](stewardship-campaign-statistics.md) | PL-I3 |
| PR #46 submission receipts | [Three dual-source rounds and protected delivery](stewardship-submission-receipts.md) | PL-I2 |
| PR #47 daily Admin digests | [Five dual-source rounds and protected delivery](stewardship-daily-digests.md) | PL-I2, PL-I3 |
| PR #48 weekly additional-information digests | [Four dual-source rounds and protected delivery](stewardship-weekly-digests.md) | PL-I2, PL-I3 |
| PR #50 operational alerts | [Three dual-source rounds and protected delivery](stewardship-operational-alerts.md) | PL-I2, PL-I5 |
| PR #51 operational health/Slack | [Three dual-source rounds plus CI correction review, protected delivery](stewardship-operational-health.md) | PL-I2, PL-I5 |
| PR #52 health observation | [Three dual-source rounds and protected delivery](stewardship-health-observation.md) | PL-I5 |
| PR #53 periodic health | [Three dual-source rounds and protected delivery](stewardship-periodic-health.md) | PL-I5 |
| PR #55 source health | [Three dual-source rounds and protected delivery](stewardship-source-health.md) | PL-I5 |
| PR #56 mail health | [Three dual-source rounds and protected delivery](stewardship-mail-health.md) | PL-I2, PL-I5 |
| PR #57 due-work health | [Three rounds and protected delivery](stewardship-due-work-health.md) | PL-I4, PL-I5 |
| PR #58 go-live readiness and cleanup admission | [Three dual-source rounds and protected delivery](stewardship-go-live-readiness.md) | PL-I4 |
| PR #59 inactive-link preparation | [Three dual-source rounds and protected delivery](stewardship-production-activation.md) | PL-I1, PL-I4 |
| PR #60 Production confirmation/activation | [Three dual-source rounds and protected delivery](stewardship-production-confirmation.md) | PL-I4 |
| PR #61 pre-start withdrawal | [Three dual-source rounds and protected delivery](stewardship-production-withdrawal.md) | PL-I4 |
| PR #62 live delivery pause | [Three dual-source rounds](stewardship-delivery-pause-reviews.md), [protected delivery](stewardship-delivery-pause.md) | PL-I2, PL-I4 |

### Phase 5 reports and staff workflows

| Merged increment | Review evidence reused | PL scopes |
| --- | --- | --- |
| PR #63 report workspace | [Three rounds](stewardship-report-workspace-reviews.md), [protected delivery](stewardship-report-workspace.md) | PL-I3 |
| PR #65 exact export UI | [Three rounds](stewardship-exact-export-ui-reviews.md), [protected delivery](stewardship-exact-export-ui.md) | PL-I3 |
| PR #66 additional-information follow-up | [Three dual-source rounds](stewardship-additional-followup-reviews.md), [protected delivery](stewardship-additional-followup.md) | PL-I3 |
| PR #67 information exports | [Three dual-source rounds](stewardship-information-export-reviews.md), [protected delivery](stewardship-information-exports.md) | PL-I3 |
| PR #68 Family directories and code lookup | [Three dual-source rounds](stewardship-family-directory-reviews.md), [protected delivery](stewardship-family-directories.md) | PL-I3 |
| PR #69 directory exports | [Three dual-source rounds](stewardship-directory-export-reviews.md), [protected delivery](stewardship-directory-exports.md) | PL-I3 |
| PR #70 scoped Ministry reports | [Three dual-source rounds](stewardship-ministry-report-reviews.md), [protected delivery](stewardship-ministry-reports.md) | PL-I3 |
| PR #71 Ministry exports | [Three dual-source rounds plus candidate-CI correction review](stewardship-ministry-export-reviews.md), [protected delivery](stewardship-ministry-exports.md) | PL-I3 |
| PR #72 Ministry follow-up | [Three rounds](stewardship-ministry-followup-reviews.md), [protected delivery](stewardship-ministry-followup.md) | PL-I3 |
| PR #75 multi-Ministry packets | [Four dual-source rounds (three plus post-correction)](stewardship-ministry-packet-reviews.md), [protected delivery](stewardship-ministry-packets.md) | PL-I3 |
| PR #76 financial stewardship detail | [Four rounds: round 1 dual-source, rounds 2-3 and a correction check single-source](stewardship-financial-report-reviews.md), [protected delivery](stewardship-financial-report.md) | PL-I3 |
| PR #77 portal users | [Three rounds: round 1 single-source, rounds 2-3 dual-source](stewardship-portal-users-reviews.md), [protected delivery](stewardship-portal-users.md) | PL-I1, PL-I3 |
| PR #78 system logs | [Three single-source rounds](stewardship-admin-logs-reviews.md), [protected delivery](stewardship-admin-logs.md) | PL-I3 |
| PR #79 financial exports | [Three rounds: round 1 dual-source, rounds 2-3 single-source](stewardship-financial-export-reviews.md), [protected delivery](stewardship-financial-exports.md) | PL-I3 |
| PR #80 login rule edits | [Eleven rounds (three plus eight correction checks): four dual-source, seven single-source](stewardship-user-rule-edit-reviews.md), [protected delivery](stewardship-user-rule-edits.md) | PL-I1, PL-I3 |
| PR #81 security event acknowledgement | [Five single-source rounds (three plus two correction checks)](stewardship-policy-security-event-reviews.md), [protected delivery](stewardship-policy-security-events.md) | PL-I1, PL-I2 |
| PR #83 security event mail | [Four single-source rounds (three plus a correction check)](stewardship-security-event-mail-reviews.md), [protected delivery](stewardship-security-event-mail.md) | PL-I2 |
| PR #84 Chairperson suggestions | [Four single-source rounds (three plus a correction check)](stewardship-chair-suggestions-reviews.md), [protected delivery](stewardship-chair-suggestions.md) | PL-I3 |
| PR #85 Chairperson confirmation | [Four single-source rounds (three plus a correction check)](stewardship-chair-confirmation-reviews.md), [protected delivery](stewardship-chair-confirmation.md) | PL-I3 |
| PR #86 Chairperson seed review | [Three single-source rounds](stewardship-chair-review-reviews.md), [protected delivery](stewardship-chair-review.md) | PL-I3 |
| PR #87 manual assignment editor | [Four single-source rounds (fourth a correction check)](stewardship-assignment-editor-reviews.md), [protected delivery](stewardship-assignment-editor.md) | PL-I3 |
| PR #88 login-rule autosave | [Seven single-source rounds](stewardship-rule-autosave-reviews.md), [protected delivery](stewardship-rule-autosave.md) | PL-I1, PL-I3 |
| PR #89 autosave races/exact-once tests | [Six single-source rounds](stewardship-autosave-races-reviews.md), [protected delivery](stewardship-autosave-races.md) | PL-I1, PL-I3 |

### V1 launch items and gate corrections (PR #91-#107)

| Merged increment | Review evidence reused | PL scopes |
| --- | --- | --- |
| PR #91 manual ParishSoft refresh (ADM-08.01) | [Seven single-source rounds](stewardship-manual-refresh-reviews.md), [protected delivery](stewardship-manual-refresh.md) | PL-I5 |
| PR #92 release image and retargeting | [Five single-source rounds (three full, two correction checks)](stewardship-release-image-reviews.md), [protected delivery](stewardship-release-image.md) | PL-I5 |
| PR #93 deployment runbook | [Three single-source rounds (two full, one correction check) and protected delivery](stewardship-deployment-runbook-reviews.md), [runbook](stewardship-deployment-runbook.md) | PL-I5 |
| PR #94 v1 backup | [Five single-source rounds (three full, two correction checks)](stewardship-backup-reviews.md), [protected delivery](stewardship-backup.md), [runbook](stewardship-backup-runbook.md) | PL-I5 |
| PR #95 human-run smoke tools | [Three single-source rounds](stewardship-smoke-tools-reviews.md), [protected delivery](stewardship-smoke-tools.md) | PL-I2, PL-I5 |
| PR #96 launch runbooks | [Five single-source rounds and protected delivery](stewardship-launch-runbooks-reviews.md), [runbooks](stewardship-launch-runbooks.md) | PL-I2, PL-I4, PL-I5 |
| PR #97 paused resend correction | [Three single-source rounds](stewardship-paused-resend-reviews.md), [protected delivery](stewardship-paused-resend.md) | PL-I2, PL-I4 |
| PR #98 restore correction (gate round 1) | [Four single-source rounds](stewardship-restore-correction-reviews.md), [protected delivery](stewardship-restore-correction.md) | PL-I1, PL-I5 |
| PR #99 activation procedure (gate round 1) | [Five single-source rounds and protected delivery](stewardship-activation-runbook-reviews.md) | PL-I4 |
| PR #100 unsent resolution (gate round 1) | [Six single-source rounds](stewardship-unsent-resolution-reviews.md), [protected delivery](stewardship-unsent-resolution.md) | PL-I2, PL-I4 |
| PR #101 Family denials (gate round 1) | [Three single-source rounds](stewardship-family-denials-reviews.md), [protected delivery](stewardship-family-denials.md) | PL-I1 |
| PR #102 runbook corrections (gate round 2) | [Four single-source rounds and protected delivery](stewardship-runbook-corrections-reviews.md) | PL-I2, PL-I4, PL-I5 |
| PR #103 refresh activity race (gate round 2) | [Three single-source rounds](stewardship-refresh-activity-race-reviews.md), [protected delivery](stewardship-refresh-activity-race.md) | PL-I1 |
| PR #104 CSRF namespaces (gate round 2) | [Three single-source rounds](stewardship-csrf-namespaces-reviews.md), [protected delivery](stewardship-csrf-namespaces.md) | PL-I1, PL-I3 |
| PR #105 gate round 3 corrections | [Three single-source rounds and protected delivery](stewardship-gate-round3-fixes-reviews.md) | PL-I2, PL-I4, PL-I5 |
| PR #106 operator diagnostics (gate round 4) | [Four single-source rounds and protected delivery](stewardship-operator-diagnostics-reviews.md) | PL-I2, PL-I5 |
| PR #107 upgrade recovery and paused report resend (gate round 5) | [Six dual-source rounds and protected delivery](stewardship-gate-round5-fixes-reviews.md) | PL-I2, PL-I5 |

Not reused as shipping-code evidence:

- CI/test tooling only: PR #14 CI merge group, #28 browser CI, #49 test
  bootstrap, #54 CI bootstrap reuse, #64 CI feedback, #73 weekly skip test
  and #74 shard watchdog.
- Documentation/scope only or not stewardship: PR #2-#7, #82 (README), #90 (v1
  launch scope).

Gaps in the reused records:

- PR #40 and PR #44: the owning guides record the rounds but have no
  "Protected delivery" section; merge evidence is only in the task plan
  (#44 also in the successor guide's opening line).
- PR #9, #11, #12, #13, #18: round counts are stated in the milestone
  evidence and task plan, but source (dual-model vs single) is not stated
  uniformly; the rows say only "three rounds".
- PR #8 is the Phase 0 scaffold; its eight M0 rounds predate the fresh-install
  baseline consolidation in PR #22, like the other Phase 1 rows. Gate 2
  treated pre-consolidation tests as not current-schema proof; the same
  caveat applies to rows PR #8-#21.

Each linked ledger retains its review endpoints, corrections and protected
delivery record. A failed or degraded attempt is never counted; an open
checkpoint is not substituted for the later successful delivery.

## Fresh integration coverage

| ID / controlling requirement | Required current integration check |
| --- | --- |
| PL-I1: Family code and link authentication, sessions and campaign boundaries | Code or link → acknowledgement → session → form, submit, keepalive and logout across Testing and Production modes, campaign state changes, epoch changes and expiry; every boundary fails closed before private bytes or writes. Inspect the current authentication, session and campaign-boundary owners, not only recent diffs. |
| PL-I2: mail recipient privacy, Testing routing, sealed credentials and unknown-provider outcomes | Recipient resolution and Testing rerouting for every mail kind (Family, receipts, digests, operational, security); sealed credential handling in mail-dispatch and the installers; `delivery_unknown` entry, reconciliation and authorized resend; pause holds. Inspect the outbox, dispatch, delivery-resolution and credential owners. |
| PL-I3: report and export authorization by role, Ministry and column | Every report, export and download path admits exactly the roles, Ministries and columns the specification names, including Family codes and financial data; the download role and exact-export UI boundaries; audit of exports. Inspect the report workspace, export and grant owners. |
| PL-I4: Production activation and delivery pause and resume | Readiness preview → Testing cleanup → activation → catch-up and initial schedule; withdrawal; pause, resume and held-message resolution under concurrent scheduling; no live message before activation. Inspect the go-live, activation, catch-up, delivery control and scheduler owners. |
| PL-I5: backup, manual restore and the deployment runbook | Provisioning → first install → upgrade (retarget, backup admission, migration, grants) → rollback; the backup profile's mounts, identity and sealed outputs; the restore drill; runbook accuracy against the commands. Inspect the provisioning, retarget, backup and operator-command owners and the three runbooks. |

Each round runs one independent reviewer per scope against a clean checkout
of the baseline, with that scope's reading list of current owners (modules,
SQL, tests, specification sections and guides) and its negative cases; the
review inspects the integrated owners, not a diff. Each finding must quote the
reviewed file exactly; findings whose quote does not match are discarded, and
every Critical, High or Medium finding is verified against the code before it
is accepted. Accepted findings are corrected through ordinary pull requests,
and the next round rechecks the corrections and every scope's negative cases.
Gate exit needs completed coverage for every row, not a count of reviewer
invocations.

## Validation

The full PostgreSQL suite passed on each round's baseline, each run on a
dedicated disposable server: 4064 tests on `4b36435d` in 1 hour 29
minutes, 4093 on `95ddef08` in 1 hour 33 minutes, and 4105 on `3d5cbe6d`
in 1 hour 37 minutes. Each correction pull request passed its own focused
suites and full exact-head CI, including its PostgreSQL shards, before it
merged; the corrections after `3d5cbe6d` (PR #105 to #107) changed no
database path.

The schema freeze audit installed `2c16c49e` fresh and compared its catalog
with the committed fresh-install baseline
(`tests/stewardship/database/schema-baseline.json`): every category
matched exactly, so no schema change landed after the baseline was
recorded, and PR #106 and #107 changed no schema.

| Category | Count | Digest prefix |
| --- | --- | --- |
| Relations | 217 | `d1af8677` |
| Columns | 2417 | `74f8b12b` |
| Constraints | 3342 | `dd448165` |
| Indexes | 992 | `e0ebd55b` |
| Functions | 586 | `77fabd05` |
| Triggers | 546 | `6d4e884f` |
| Policies | 28 | `1c9c3b2d` |

This baseline is the frozen production schema; a change after the gate
needs the human's decision between a reinstall of the validation deployment
and a forward migration.

## Known limitations for the human's approval

- Restore is a manual procedure (no OPS-06 automated restore, reopen, archive
  or Return to Testing); a restore during the live campaign may require
  re-sending some Family links by hand.
- The backup seals to a public key with anonymous encryption; origin is proved
  by the recorded manifest digest kept off the host, not by a host-held
  signing key. The off-host copy is the operator's cron job. The private key
  is not rotated during v1.
- The upgrade admission is a recorded backup within 24 hours standing in for
  verified restore evidence; automated upgrade readiness checks and
  upgrade-path tests are deferred.
- The application image is single-architecture (`linux/amd64`) without SBOM,
  provenance or vulnerability scanning; release-pipeline extras are deferred.
- Provider smoke checks against real providers are human-run; normal CI stays
  fake-backed.
- Retention and compaction jobs, exceptional purge and ParishSoft write-back
  are not in v1; write-back is the first post-launch work.
- The validation deployment used real read-only ParishSoft data, real Google
  login and Testing-routed real mail before the gate, as the launch scope
  authorizes.
- A restore returns the deployment to the backup's moment with no
  restore-review workflow: Family access stays open during the
  Administrator's review, work after the backup is lost, and mail the
  provider accepted after the backup can be sent again, as the backup
  runbook's [restore limitations](stewardship-backup-runbook.md#restore-limitations-in-v1)
  state. Backups by hand after each large send keep the window small.
- Production activation is time-bound: a full refresh counts for 30 minutes
  from its start, and any later refresh (the quarter-hour delta or the
  nightly full refresh) makes prepared Family links stale, so the
  [activation procedure](stewardship-launch-runbooks.md#production-activation)
  must be followed in one sitting, with a full refresh timed on the
  validation deployment beforehand.
- Round 3's one uncorrected Medium (PL-I2): if a campaign is paused while
  an invitation or reminder whose preparation failed is waiting for
  **Retry unsent**, and the campaign then closes while still paused, no
  action can resolve that message, so the pause on the closed campaign is
  never cleared. Clearing it matters only for reopening or archiving a
  closed campaign, neither of which v1 ships; held receipts and digests on
  the closed campaign still resolve through the closed resolution. Avoid it by resolving failed preparations before pausing
  near the close, or by resuming before the close. The human decides
  whether to accept this for v1 or require a correction before exit.
- The Family code path does not add the progressive delay in elevated mode
  that the architecture specification describes; the Administrator path
  does. During a distributed guessing burst the per-IP limit is halved and
  an abuse incident is raised, and the code space (about 23^8 codes)
  and the per-IP and per-pair windows still bound guessing, but a wrong
  code gets no added delay.

## Rounds

### Round 1

Five independent Claude reviewers, one per scope, read a clean checkout of
`4b36435d` on September 22, 2026 (Codex was unavailable under the exemption;
the round is single-source). Every finding's quote matched the reviewed file.
Thirteen findings were raised: two High, eight Medium and three Low. All ten
at Medium or above were verified against the code and accepted; the Lows
were taken with them. PL-I3 found nothing: its reviewer traced every report,
export and download entry point, the Python and SQL policy agreement on
role, ownership and Ministry scope, and eight negative cases, reading sixty
files.

| Scope | Finding | Correction |
| --- | --- | --- |
| PL-I1 | Medium: every Family sign-in failure rendered one "Sign-in is unavailable" page, contradicting the specification's code and link results | [Family denials](stewardship-family-denials.md), PR #101 |
| PL-I1 | Medium: restore told the operator to start web with Family access closed, which v1 cannot do | [Restore correction](stewardship-restore-correction.md), PR #98 |
| PL-I2 | Medium: an unknown delivery whose resend is no longer admitted had no truthful resolution, so a pause could never resume | [Unsent resolution](stewardship-unsent-resolution.md), PR #100 |
| PL-I4 | Medium: the activation procedure pointed to development guides that said activation was closed and omitted required steps; Low: withdrawal refused while paused, undocumented; Low: report preparation blocks every closed resolution | [Activation procedure](stewardship-launch-runbooks.md#production-activation), PR #99 |
| PL-I5 | High: the backup dumped without owners or privileges, so a restore could not start | PR #98 |
| PL-I5 | High: the restore promised no duplicate mail and closed Family access | PR #98, with the [restore limitations](stewardship-backup-runbook.md#restore-limitations-in-v1) listed below for approval |
| PL-I5 | Medium: only missing files were restored and the database had no emptying command; Medium: media was not backed up; Low: an authority store outside the archived trees was silently omitted | PR #98 |

Each correction went through its own review rounds, recorded in its ledger,
and its own protected delivery. Their reviews found and fixed further
defects of the same kind: the restore correction's first round alone found
that the provisioning record was missing from the set, that media would be
restored where nothing mounts it, that `pg_restore --clean` leaves a later
release's objects behind, and, when its exact commands were run, that piping
`pg_restore` into `psql` commits an emptied schema with status 0 when
`pg_restore` fails.

### Round 2

Five independent Claude reviewers read a clean checkout of `95ddef08` (the
merge of PR #100) on September 22, 2026, each told what round 1 found in its
scope and which pull request corrected it (single-source under the
exemption). Every quote matched. Twelve findings were raised: two High,
five Medium and five Low. PL-I3 again found nothing (56 files, nine negative
cases). The regression on this baseline passed: 4093 PostgreSQL tests in 1
hour 33 minutes.

| Scope | Finding | Correction |
| --- | --- | --- |
| PL-I1 | High: a refresh read Family rows unlocked and wrote a stale version, so any concurrent Family page load or keepalive rolled back the whole refresh | [Refresh activity race](stewardship-refresh-activity-race.md), PR #103 |
| PL-I1 | Medium: one site-wide CSRF cookie was rotated by both Family and Admin sign-in, so a staff sign-in in the same browser ended a Family's tab and lost its unsaved answers | [CSRF namespaces](stewardship-csrf-namespaces.md), PR #104 |
| PL-I5 | High: no upgrade, rollback or restore step refreshed the static files, so a release changing a script would have shipped with the previous release's scripts | [Runbook corrections](stewardship-runbook-corrections-reviews.md), PR #102 |
| PL-I5 | Medium: an application-only rollback after a grant change left the older services refusing to start; Medium: a grant-narrowing release could not be deployed; Low: rollback across a new deployment field; Low: a restore left plaintext copies | PR #102 |
| PL-I4 | Medium: a failed report preparation never finishes by itself, but resume and closed resolution said to wait; Medium: the fresh sign-in and sender-test clocks for resume were not ordered | PR #102 |
| PL-I2 | Low: on a paused campaign a failed report to a current Administrator is folded into the next combined report, not offered for retry | PR #102 |

One PL-I1 Low remains open: the Family code path does not add the
progressive delay in elevated mode that the architecture specification
describes (the Administrator path does); round 3 rechecks it.

### Round 3

Five independent Claude reviewers read a clean checkout of `3d5cbe6d` (the
merge of PR #104) on September 22, 2026, each told what earlier rounds
found and corrected in its scope (single-source under the exemption). Every
quote matched. Seven findings were raised: three Medium and four Low. PL-I3
again found nothing.

| Scope | Finding | Correction |
| --- | --- | --- |
| PL-I5 | Medium: the rendered service configurations dropped the deployment's `operational_alerts`, so every service ran the default alert windows | [Gate round 3 corrections](stewardship-gate-round3-fixes-reviews.md), PR #105 |
| PL-I2 | Medium: the mail-provider outage runbook said sending resumes by itself, but the campaign-mail circuit stops sending until `mail-dispatch` restarts | PR #105 |
| PL-I4 | Low: resume is hidden while an activation catch-up is incomplete, undocumented | PR #105 |
| PL-I5 | Low: the backup refusal log records only a category; Low: a single nightly backup would raise the 24-hour overdue alert on any late night | PR #105 |
| PL-I2 | Medium: an invitation or reminder whose preparation failed, held by a pause on a campaign that then closes, can never be resolved, so the pause is never cleared | Not corrected; listed under [known limitations](#known-limitations-for-the-humans-approval) for the human's decision |
| PL-I1 | Low (open since round 2): no progressive delay on the Family code path in elevated mode | Not corrected; listed under known limitations |

### Round 4

A correction check of PL-I2 and PL-I5 on `2c16c49e` (the merge of PR #105)
by two independent Claude reviewers, told what round 3 found and how PR
#105 corrected it (single-source). Every quote matched. Three findings were
raised, all against round 3's own corrections:

| Scope | Finding | Correction |
| --- | --- | --- |
| PL-I5 | Medium: the backup runbook promised `pg_dump`'s message in the process log, and Medium: the upgrade runbook promised a sentence naming the missing backup, but the production log formatter keeps only reviewed events and drops both | [Operator diagnostics](stewardship-operator-diagnostics-reviews.md), PR #106 |
| PL-I2 | Low: the outage recovery resumed before settling unknown deliveries, which resume refuses | PR #106 |

The operational-alerts correction checked clean through every consumer of
the rendered documents, and the outage procedure checked clean against the
campaign-mail circuit, the dispatch completion and the deliveries page.

### Round 5

A correction check of PL-I2 and PL-I5 on `d23d20d5` (the merge of PR #106)
by two independent Claude reviewers (Codex was tried and was still out of
credits). Every quote matched. Two findings were raised:

| Scope | Finding | Correction |
| --- | --- | --- |
| PL-I5 | Medium: once an upgrade has retargeted, the backup profile runs the new image, which refuses the unmigrated schema, so a step 1 backup that aged past 24 hours left the operator able neither to back up nor to migrate | [Gate round 5 corrections](stewardship-gate-round5-fixes-reviews.md), PR #107 |
| PL-I2 | Low: a report resent on a paused active campaign is folded into the next combined report on resume, not sent as the runbook said | PR #107 |

The diagnostics correction checked clean against every refusal path.

### Round 6

A correction check of PL-I2 and PL-I5 on `d4206390` (the merge of PR #107)
by two Claude reviewers and, with Codex back in credit, two Codex reviewers
on the same prompts. Codex approved both scopes, and the PL-I5 Claude
reviewer found nothing: the upgrade recovery, the rollback and the restore
step agree with `retarget-image`, the backup admission and the schema
check. The PL-I2 Claude reviewer raised two Lows, both taken in this pull
request: the launch runbooks and the Admin portal specification said a
message resent on a paused active campaign is sent on resume, but the
resume's recovery plan decides it like any held message, so a later
reminder can replace it and a submission can cancel it. No Critical, High
or Medium finding remains unresolved.

## Exit

Open, awaiting the human. Every Critical, High and Medium finding that the
six rounds validated is corrected, except round 3's PL-I2 Medium listed
under known limitations for the human's decision. The gate exits only when
the human gives explicit product, security and operations approval of the
launch and of the known limitations above; nothing here infers it. Before
that approval, the human also:

- reinstalls the validation deployment from the current release, since the
  backup release, PR #97 and PR #100 changed the fresh-install schema and
  PR #105 added the rendered `operational_alerts` policy;
- runs the provider smoke checks and the restore drills (the full drill on
  the validation deployment, and the replacement-host steps on a
  disposable host), as the
  [backup runbook](stewardship-backup-runbook.md#restore-drill) describes;
- times a full ParishSoft refresh on the validation deployment for the
  [activation procedure](stewardship-launch-runbooks.md#production-activation);
- decides whether round 3's closed-while-paused Medium is accepted for v1.

## Reviews of this record

This evidence map and its accompanying correction follow the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds, with a correction check after any round that validates a
finding, each recorded by which sources answered.
