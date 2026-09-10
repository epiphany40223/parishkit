# Stewardship campaign configuration foundation

This internal foundation implements the draft-configuration portion of
[DAT-02](../plans/stewardship/data.md#dat-02-campaign-lifecycle-and-schedule-schema)
and pure decisions in
[DOM-02](../plans/stewardship/campaign-domain.md#dom-02-campaign-interval-and-lifecycle-policy).
The [normative lifecycle](../specs/stewardship/spec.md#campaign-lifecycle) remains
controlling. No operational editor, Family portal, scheduler or Production
transition is enabled by these primitives.

## Delivered boundary

`campaign-foundation-v3` adds immutable CampaignConfiguration/ScheduleRevision
projections and requires explicit Administrator policy. Historical v1/v2
validators and patch parsers retain their semantics. New ordinary requests use
`campaign-foundation-patch-v3`; additive offline recovery after campaign adoption
uses `operator-recovery-patch-v2`, preserving campaign data and revocation effects.

Campaign projections retain modules, explicit Ministry/fund mappings, whole-year
financial/comparison periods, ordered share options with stable IDs, content
version references, additional-information settings and resolved boundaries.
Content/template existence and source eligibility are future readiness checks,
not implied by valid UUIDs. Drafts may omit financial periods or schedules;
supplied values must be valid and module-applicable. Overlap requires explicit
confirmation. UI defaulting, including comparison periods, belongs to ADM-04.

Preparation writes history only. Activation atomically creates/updates the sole
Testing draft, selects the global pointer and records parish-owned audit evidence.
The first draft timezone copies the Parish value; later draft edits can change
it independently. Parish timezone edits never rebucket existing campaign data.
The current applied configuration selects configured schedule revisions; removed
revisions remain historical. Retired schedule UUIDs cannot change owner or type.
Every activation advances the campaign projection pointer/version, even for an
unrelated configuration edit; its audit event is then `campaign_reprojected`,
not `campaign_configured`. Callers must treat that version as projection freshness,
not as a count of user-visible campaign edits. SQL insertion guards independently
resolve stored UTC boundaries; gap/fold parity tests cover non-hour transitions
and a whole skipped day. Timezone-rule disagreement fails closed.

The runtime deliberately forbids Production, lifecycle mutations, campaign
deletion and replacement. Future migrations must replace these restrictions
alongside the required durable workflow evidence. The global advisory key
`(736220, 1)` is reserved for creation, Return to Testing and purge admission.
The serialized installer is currently the only admitted runtime writer.

`campaigns.lifecycle` supplies transition metadata and date/state predicates,
including scheduler lag, restore gates, independent delivery pause, historical
unarchive restrictions, purge edges and structural locks. Its target is a policy
decision, not proof of authorization, readiness, token preparation or quiescence.
Owning services must obtain those facts under their transaction locks.
Registry modes deliberately allow Return to Testing when already in Testing,
and reopen preserves or asserts Production under its separate readiness guard.
Live delivery pause/catch-up flags are Production-only; they do not suppress
explicit readiness/test-recipient sends. Overdue boundaries must record start
then close in one owning transaction, even when both dates have passed. These
pure decisions never admit Family access outside the effective interval.

Temporary restore/mode admission holds leave a staged configuration request
retryable; invalid draft intent still produces a terminal rejection receipt.

## Timezone-rule upgrade safety

Python and PostgreSQL independently resolve local times. Their rules must agree
for every retained campaign and schedule, including historical configurations.
An affected timezone-data update can invalidate verification of the entire
immutable lineage, blocking ordinary activation and offline policy recovery,
even if only an older campaign is affected. Failing closed is intentional.

Before upgrading either image or timezone data, validate the retained lineage
in an isolated restored copy using the proposed image pair. Keep the known-good
pair if any boundary differs. Recovery requires matching rules or an explicitly
reviewed forward migration that preserves historical resolution semantics;
there is no automatic boundary rewrite or in-place database downgrade. Follow
the [operations upgrade/restore workflow](../specs/stewardship/operations/spec.md)
for any required restoration. Do not disable guards, edit historical UTC values,
or replay old mail to get past a mismatch. Regression tests simulate rule drift
and verify that restoring matching rules revalidates unchanged stored history.

## Remaining integration

This reviewable batch groups schemas, migrations, installer/recovery integration,
policy and tests. It does not complete Phase 1A or any formal review gate.

- DAT-02.01/.02: live structural-lock/runtime fields, the pinned read guard and
  deployment-wide bounded download admission remain open.
- DAT-02.03: boundary occurrences, lifecycle/mode history and activation catch-up
  demands remain open; BG-02/ADM-05/ADM-06 own operational effects.
- DAT-02.04: logical runtime schedules, occurrence/fulfillment state, replacement
  markers, restore holds and post-close resolutions remain open. Configuration
  revisions are not delivery evidence.
- DAT-02.05 and DOM-02.03 through .05: pure decisions and draft installer races
  are tested; live transition, purge and download races require those records
  and later worker/web integration.

Continue the remaining storage/read-guard tasks as the next coherent batch,
before campaign-detail/report/download consumers. Never use supplied booleans
as substitutes for durable readiness and token/work evidence.

## Validation and migrations

Tests cover strict schemas, chronological mail, retained parser semantics,
state/mode/date matrices, real installer activation, raw-write denial, concurrent
draft requests, rollback/recovery, timezone independence and offline recovery.
Historical migration fixtures use historical models. Empty reversal/reapplication
is supported; populated v3 history refuses downgrade before removing guards.
No previously merged migration is edited. Final validation and review dispositions
belong to the [milestone evidence](../tasks/stewardship/milestones.md#campaign-configuration-and-lifecycle-policy-batch).
