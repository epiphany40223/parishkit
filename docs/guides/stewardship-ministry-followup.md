# Ministry follow-up staff workflow

This Phase 5 increment begins at verified main `c08fd51d` after PR #71's
[protected delivery](stewardship-ministry-exports.md#protected-delivery).
It implements the scoped Ministry follow-up queue, detail, assignment, contact
attempts, notes, outcomes and history for
[ADM-08.03](../plans/stewardship/admin-portal.md#adm-08-manual-refresh-follow-up-queues-and-logs)
and the Ministry-request portion of
[DAT-07](../plans/stewardship/data.md#dat-07-follow-up-content-templates-jobs-and-audit).
The [Admin follow-up workflows](../specs/stewardship/admin-portal/spec.md#follow-up-workflows)
and [follow-up records](../specs/stewardship/data/spec.md#follow-up-records)
specifications control behavior.

## Why this precedes the packet

The [multi-Ministry follow-up packet](../specs/stewardship/reports/spec.md#multi-ministry-follow-up-packet)
prefills recorded email/phone contact dates and `Other` outcome notes. Before
this increment a `MinistryRequest` retained only state and outcome, with no
assignee, note or contact-attempt storage. Delivering RPT-07 first would either
ship permanent blanks or invent placeholder storage inside a report. This is
dependency ordering within M5, following the earlier
[additional-information precedent](stewardship-additional-followup.md); it
waives no RPT-07 requirement. RPT-07 remains unchecked until its packet reads
this real workflow.

## Scope and acceptance

Admin/Staff see every live Ministry request. Ministry leaders see and edit only
requests for their currently assigned Ministries. Every queue page, detail,
history read and mutation reloads current policy; an old session, a known
request UUID or a previously visible row is not a grant. The interface offers
queue filters, assignee/status/outcome editing, contact-attempt entry, notes,
bulk assignment and a link to the Member's authorized report detail. It exposes
no financial or unrelated Family data. Identifying search stays in CSRF POST
bodies, not URLs or logs. No source write occurs.

Edits append immutable history and advance the request version in one
authorized transaction. Replays cannot create a second revision. A stale Staff
form cannot overwrite a newer edit, a Family replacement or a source
resolution. Bulk assignment binds the exact selected request/version set and
changes all of it or none of it. Purge work gates close mutations; admitted
reads retain their guards through response completion.

## Design

### One immutable revision per authorized edit

`MinistryWorkflowRevision` mirrors `AdditionalInformationRevision`: request,
expected version, replay key, the complete resulting assignee/state/outcome,
bounded notes, and at most one contact attempt (channel, time and notes). The
database stamps creation time and owns actor attribution. The request row keeps
only the current projection: `state`, `outcome`, `resolved_at` and a new
`assignee_id`. Notes and contact attempts are private workflow history and never
enter audit context, logs or URLs.

Three SQL guards pair the pieces, so application checks are not the only
defense: the revision guard validates current actor authority, Ministry scope,
assignee authority, the work gate and the row version; the existing request
guard accepts a Staff projection change only with its exactly matching
revision; and a deferred effect trigger requires the advanced projection plus
its narrow audit event.

Contact channels are `email`, `phone`, `in_person` and `other`. The packet
consumes the first two; the others let Staff record real attempts without
mislabelling them. A contact time cannot be in the future.

### Staff-owned transitions

Staff edits move only among `new`, `assigned`, `in_progress`, `resolved` and
`closed_no_response`. `cancelled` and `superseded` remain Family-owned, and
roster-evidence resolution remains the source worker's: a Staff resolution
never sets `resolution_source`. `assigned` requires an assignee and `new` has
none. `closed_no_response` pairs with `no_response`; `joined` applies only to
`join` and `leave_confirmed` only to `leave`; `other` requires notes. Closed
outcomes stay immutable, as the existing guard already requires; the
specifications define no reopening.

An assignee must currently hold Ministry follow-up authority for that Ministry:
an active Admin/Staff user, or a leader currently assigned to it. Authority is
rechecked on every revision, so an edit cannot silently retain an assignee who
was disabled or lost the Ministry; the editor reassigns or clears it.

### Surviving Family resubmission

A Family resubmission derives a new `MinistryRequest` and supersedes its
predecessor, already preserving actionable state for the same intent. Staff
work must not reset on that path, and notes must not be copied or rewritten.
The successor therefore inherits the predecessor's `assignee_id` under the same
guard that inherits its state, and history is read across the same-intent
supersession chain: predecessors linked by `superseded_by` with the same
action. A changed intent starts a fresh workflow and old history stays with the
superseded request. The old request is no longer actionable and its version
advanced, so stale Staff forms against it fail rather than orphaning work.
A later Staff edit chains a new revision bound to the successor.

This is simpler than a guarded current-revision pointer inherited across
request generations: it adds one projected column instead of a second mutable
provenance pointer, and the chain is bounded by a Family's resubmission count.

## Validation plan

- Pure closed form grammar; state/outcome/action pairing; bounded notes and
  contact times.
- Grouped real-role PostgreSQL edits, reads, replay and stale writers;
  cross-Ministry denial; changed assignment, disabled actor and unauthorized
  assignee; the current work gate.
- Same-intent Family supersession retains assignee and history and invalidates
  old forms; changed intent, withdrawal, source catch-up and hidden Ministries.
- No raw SQL state change, forged attribution, orphan revision, worker write or
  unpaired projection/audit.
- Bulk assignment of an exact UUID/version set with no silent partial update.
- The shared native browser fixture on all three engines.
- An independent fresh-schema comparison before changing the fingerprint.

Use the fresh-install baseline only, with no retained-database deletion or
upgrade compatibility work. Use focused local tests and draft fast CI, three
completed dual-source review/fix rounds, then full exact-head protected
CI/merge. No production provider calls, deployment or release are authorized
by this slice.

## Checkpoint

Design recorded; implementation in progress. ADM-08.03, RPT-07 and M5/Gate 3
remain open.
