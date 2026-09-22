# Stewardship v1 launch scope

Human decision, September 21, 2026. This plan amends the
[overall implementation plan](overall.md) for the first live campaign. It
records which planned work is **cut from v1**, which is **reduced**, which
remains **launch-critical**, and the schedule to the go-live date. Everything
cut or reduced here stays in the specifications, plans and task checklists so
it can be added back later; this document is the index of that deferred work.

The [normative specifications](../../specs/stewardship/spec.md) are unchanged.
They still describe the complete system. This plan changes only what must be
delivered before the first live campaign and in what order.

## Go-live date

- **Saturday, October 3, 2026**: initial Family invitation emails are sent to
  all parishioner Families. The live campaign runs for 30 days, closing on or
  about November 2, 2026.
- Production activation must complete by **Thursday, October 1** so that
  Friday, October 2 remains a buffer day.

Where this plan conflicts with the overall plan's phase order or gate
sequence, this plan governs until the v1 launch. After the launch, work
resumes under the overall plan, starting with the
[post-launch priority](#post-launch-priority-parishsoft-write-back).

## Launch-critical remaining work

Implement these, in this order, before anything else. The ordinary PR
delivery cycle applies, with the [v1 process changes](#v1-process-changes).

1. Finish in-flight ADM-07 (PR #89, ADM-07.05 race tests).
2. **ADM-08.01**: coalesced manual ParishSoft refresh controls, so staff can
   pull source changes during the campaign.
3. **Production deployment**: complete the OPS-01.02 image and service commands
   needed by the v1 services; complete ARC-06.03, .04, .05 and .07 as far as the
   production deployment needs them (key rotation, ARC-06.06, is cut); write the
   deployment, first-install and upgrade runbook.
4. **Reduced backup** (see [reduced item 6](#reduced-for-v1)), including one
   tested manual restore into a disposable environment.
5. **Human-run smoke tools** (the OPS-09.04 smoke portion): ParishSoft read,
   Google login, mail-provider send to a test address, and optional Slack, with
   redacted output. They read credentials at runtime and stay out of CI.
6. **Operational runbooks** for launch (the OPS-08.05 portion): deploy, backup,
   manual restore, mail-provider outage, ParishSoft outage, delivery pause and
   resume, and what to do with `delivery_unknown` messages.
7. **Pre-launch gate** (see [v1 process changes](#v1-process-changes)).
8. Fix bugs found during staff validation. **Validation bug fixes take priority
   over items 2–6 as soon as validation starts.**

Phase 5 remainders that are **not** launch-blocking and may land after the
launch: ADM-08.02 (manual-census queue portion), ADM-08.04 (log text/JSONL
export, full-text search, entity and Ministry filters), ADM-08.05, RPT-09
(log export and web/chart/digest parity tests), and closing the bookkeeping on
partially delivered packages (RPT-01, RPT-02, RPT-03, BG-04, BG-06, BG-08,
DAT-04, DAT-05, DAT-07, DOM-03). If time remains before the code freeze,
prefer RPT-09.03/.04 parity tests, since the daily digests go to staff from
day one.

## Cut from v1

These are not built for the launch. Anything already partly built stays
disabled and fails closed, as the overall plan's implementation principles
require. Their task IDs stay unchecked in the checklists.

| # | Deferred scope | Task IDs | Interim v1 measure | Needed by |
| --- | --- | --- | --- | --- |
| 1 | Exceptional campaign purge | DAT-09.02, ADM-10.01–.06, BG-11.01–.05, OPS-07.04, the purge portion of DAT-09.04 | None; purge is exceptional. No purge entry point is exposed. | No fixed date |
| 2 | Automated restore release, closed-campaign reopen, archive/unarchive and Return to Testing | OPS-06.01–.06, ADM-06.01, .03, .04, .05, BG-02.03 (restore/reopen token preparation), BG-07.04 | Manual restore runbook (below). Archive and Return to Testing are needed only before the *next* campaign. | Before the 2027 campaign is prepared |
| 3 | Retention and compaction jobs | OPS-07.01–.05, DAT-09.03 | Disk growth over one campaign is small. Operators watch disk use; expired export files may be cleaned by hand if needed. | Before the 2027 campaign |
| 4 | Release-pipeline extras and key rotation | OPS-09.06, the SBOM, provenance and multi-architecture portions of OPS-09.03, ARC-06.06 | Single-architecture image built from a tagged commit. Keys are generated at install and not rotated during v1. | Before the 2027 campaign |

### Manual restore for v1 (replaces item 2)

Until OPS-06 exists, a restore is an operator procedure, not an application
workflow. The runbook must say to:

1. Stop the scheduler, general worker and mail-dispatch services **before**
   restoring, and keep them stopped, so no scheduled or retried Family mail
   is sent from restored state.
2. Restore the database, configuration and credentials from the latest
   verified backup.
3. Start only the web service with Family access closed, and have an
   Administrator review the delivery and outbox state against the mail
   provider's own logs. Any message that may already have been sent must not
   be resent automatically.
4. Only then restart background services, with the campaign's delivery paused
   if there is any doubt, and resume delivery deliberately.

This is a known v1 limitation to approve at the pre-launch gate: a restore
during the live campaign needs careful manual work and may require re-sending
some Family links by hand.

## Reduced for v1

| # | Reduced scope | Task IDs | v1 delivers | Deferred remainder |
| --- | --- | --- | --- | --- |
| 5 | ParishSoft write-back (publication) | DAT-09.01, .04 (publication portion), ADM-09.01–.05, BG-09.01–.05, RPT-08.01–.03, .05 | Nothing before the launch; it is the first work after the launch. | See [post-launch priority](#post-launch-priority-parishsoft-write-back). |
| 6 | Backup | OPS-05.01–.05 | Encrypted nightly `pg_dump` of the database, plus configuration and credentials, copied off-host; a documented, human-held decryption key; a failure alert through the existing operational alerts; one restore tested into a disposable environment. A backup is taken immediately before Production activation and before every post-launch upgrade. | Consistent manifests, isolated backup-worker routing, purge-triggered backup, revalidation, operator escrow workflow and RPO alerts. |
| 7 | Phase 7 hardening | ARC-08.01–.05, FAM-08.01–.05, OPS-08.01, .02, .04, .06, DOM-05.01, .03, .05, OPS-09.05 | One load check at the parish's real Family count, run against the validation deployment; mobile and desktop browser checks of the Family form and main staff pages; logs plus the existing operational Slack alerts; the acceptance scenarios for paths v1 actually ships. | Scale fixtures and latency budgets, the full device/browser matrix, the metrics endpoint and its credential rotation, failure-injection runbook exercises, the complete acceptance matrix. |
| 8 | Delivery process | Overall plan's [automated phase delivery cycle](overall.md#automated-phase-delivery-cycle) and gates | See [v1 process changes](#v1-process-changes). | The full cycle and Gates 3–5 resume after the launch, as described below. |

## V1 process changes

These apply to all work before the launch.

- **Review rounds**: at least **two** completed review-and-fix rounds per PR,
  instead of three. The exit criteria are unchanged: no validated High or
  Critical finding in the final round and no unresolved accepted Medium or
  higher finding. PRs touching Family authentication or credentials, mail
  dispatch, backup, or the database schema still get three rounds.
- **Claude-only reviews**: the Codex-outage exemption in the
  [delivery cycle](overall.md#automated-phase-delivery-cycle) is extended
  through October 30, 2026. A completed Claude-only review counts as a
  completed round, recorded as single-source with the observed Codex failure.
- **Delivery records**: record each PR's reviewed SHA, rounds with raw
  severities and dispositions, and CI result in its own guide only. Do not
  add a new narrative paragraph to the
  [top-level task plan](../../tasks/stewardship/overall.md) for each PR; keep
  one short current-status line there instead.
- **One pre-launch gate** replaces Gate 3 and the v1-relevant parts of Gates 4
  and 5 for the launch. It is an integrated independent review of the paths
  v1 ships (Claude-only rounds count under the exemption below), reusing prior PR reviews under the Gate 2 evidence-reuse procedure,
  with explicit attention to:
  - Family code and link authentication, sessions and campaign boundaries;
  - mail recipient privacy, Testing routing, sealed credentials and
    unknown-provider outcomes;
  - report and export authorization by role, Ministry and column, including
    Family codes and financial data;
  - Production activation and delivery pause and resume; and
  - backup, manual restore and the deployment runbook.

  Gate exit still requires no unresolved validated Critical, High or Medium
  finding, plus the human's explicit product, security and operations
  approval of the launch and its known limitations.
- **Real external use before the gate**: the human authorizes a Testing-mode
  validation deployment with real read-only ParishSoft data, real Google login
  and real mail delivery routed by Testing mode to staff addresses. This
  replaces the overall plan's "fake or disposable environments until Gate 3"
  restriction for that deployment only. Production activation still waits for
  the pre-launch gate.
- **After the launch**: the full delivery cycle resumes. Gate 3 is considered
  satisfied for the v1 scope by the pre-launch gate; the publication portion
  of Gate 4 must pass before the first real ParishSoft write, and Gate 4's
  restore and purge portions and Gate 5 apply when that deferred work lands.

## Production-readiness activation and schema freeze

Going live ends the [pre-production development policy](../../specs/stewardship/operations/spec.md#pre-production-development-policy)
for the live deployment. The post-launch write-back work adds tables, so the
live database must be upgraded in place instead of reinstalled.

- **Until the schema freeze**, the fresh-install baseline policy continues.
  Avoid schema changes during staff validation. If one is unavoidable, the
  human decides whether to reinstall the validation deployment or add a
  forward migration. Never delete that deployment's database without the
  human's explicit authorization.
- **Schema freeze: at the pre-launch gate, no later than September 30, 2026.**
  The then-current baseline becomes the declared production baseline and must
  not be rewritten afterward, as the
  [schema baseline guide](../../guides/stewardship-schema.md) already requires.
- **After the freeze**, every schema change is a reviewed forward Django
  migration, tested by installing the frozen baseline, loading representative
  data and migrating. Each production upgrade takes a verified backup first,
  then pulls the new image, runs migration checks and migrations, and
  restarts services, as in the operations spec's
  [production upgrades](../../specs/stewardship/operations/spec.md#production-upgrades-deferred)
  section. Downgrade is by database restore only; automated upgrade readiness
  checks (OPS-04.03) and upgrade-path tests (OPS-04.05) remain deferred.

## Schedule

| Date | Implementation | Human |
| --- | --- | --- |
| Mon 9/21–Tue 9/22 | PR #89 lands; this plan lands; ADM-08.01 | Provision the production host, DNS and TLS; create the Google OAuth client, ParishSoft API key, mail-provider account and optional Slack webhook |
| Wed 9/23–Thu 9/24 | Production image and service commands, deployment runbook, smoke tools, reduced backup | Install on the production host in Testing mode; run smoke tests; run the setup wizard with real ParishSoft data |
| Fri 9/25–Tue 9/29 | Validation bug fixes first; pre-launch gate reviews start Sat 9/26; remaining launch runbooks; load check | Staff validate the Family form, content, templates, schedules, reports and Testing-routed mail; finalize campaign content |
| Tue 9/29 | **Code freeze**: only launch-blocking fixes after this point | Report any launch blockers |
| Wed 9/30 | Pre-launch gate exits; **schema freeze**; verified backup | Approve the launch and known limitations; final smoke tests |
| Thu 10/1 | Support activation | **Production activation**: readiness, Testing cleanup, activation; confirm catch-up and the initial-mail schedule |
| Fri 10/2 | Buffer | Sanity checks |
| **Sat 10/3** | Monitor | **Initial Family emails sent** |

If the pre-launch gate or staff validation finds a launch blocker that cannot
be fixed by September 30, the human decides between moving the go-live date
and cutting the affected feature. The schedule above assumes roughly the
delivery rate of the preceding week.

## Post-launch priority: ParishSoft write-back

Start the write-back work (reduced item 5) as soon as the launch is stable,
targeting about October 5. Publication happens after the campaign closes, so
it must be complete, reviewed and smoke-tested before about November 2.

1. **RPT-08.01** pending-census change report first. It is small, and it
   lets staff apply changes by hand if write-back slips.
2. **DAT-09.01** publication plans and outcomes (a forward migration; see the
   [schema freeze](#production-readiness-activation-and-schema-freeze)).
3. **ADM-09.01–.05** review, edit, subset, preflight and progress UI.
4. **BG-09.01–.05** fenced ParishSoft writes with read-after-write
   verification, partial retry and final refresh.
5. **RPT-08.02, .03, .05** publication and manual-resolution integration.
6. The publication portion of Gate 4, then a human-run ParishSoft write smoke
   test, before any real write.

The ParishSoft API can write only Family and Member contact fields; Ministry
rosters are read-only. Ministry changes are therefore always applied by hand
from the Ministry follow-up workflow, with or without write-back.

## Adding deferred work back

After the write-back, resume the overall plan with cut items 2, 3 and 4 and
the remainders of reduced items 6 and 7 before preparing the 2027 campaign,
then item 1. Each returns through the ordinary delivery cycle and its
original gate. Remove its row from this document when it is complete.
