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
was disabled or lost the Ministry; the editor reassigns or clears it. The
request page names such an assignee and says the request cannot stay assigned
to them, rather than letting the choice fall back to nobody unexplained.

`new` and `assigned` mean only whether a request has an assignee, so both the
single edit and bulk assignment derive them from the submitted assignee.
Choosing or clearing an assignee is one intent and needs one control. Every
stored revision still carries the complete resulting state.

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

### A sibling read model, not a wider report

The queue needs names, workflow, the latest notes and the recorded email and
phone contact dates in one coherent statement. It is a sibling of the Ministry
report query rather than an extension of it. That query's rows flow verbatim
into immutable [export captures](stewardship-ministry-exports.md), so adding
private notes there would risk leaking them into exports. The follow-up query
projects names and workflow only. Email, phone and address stay behind the
Ministry report's publish-flag rules, and each page links to that authorized
detail instead of repeating the privacy decision.

SQL intersects the caller's current role scope with the campaign's Ministries
before reading any request, so an out-of-scope or unknown request UUID is simply
absent and produces the same denial. Latest intent is selected before state
filtering, and notes and contact dates are read across the same-intent chain
only for the returned page.

The one value the Ministry report does gain is the assignee. The
[reports specification](../specs/stewardship/reports/spec.md#ministry-change-summary)
lists assignee with status and outcome on the joiner and leaver lists, and the
report and its exports previously printed a fixed placeholder because nothing
recorded one. The email is resolved inside that single statement, so an export
captures who held the work as of its data. Captures taken earlier have no such
key and render as unassigned.

### Interface decisions

Assignee choices are computed for exactly one Ministry, because authority is
per Ministry. Bulk assignment is therefore offered only when the queue is
filtered to one Ministry, and a crafted selection spanning another is rejected.
It binds each selected request to the version the page displayed.

Native date and time controls carry no zone, so a contact attempt is entered
and labelled as UTC; displayed instants use the browser zone when scripts are
available. A closed request shows its history without a form. A gated campaign
disables every control but remains readable.

## Fresh-install schema audit

Fresh databases were installed separately from verified main `1faa4a88` and
from the candidate on the disposable PostgreSQL 18.6 cluster, and their complete
catalogs were compared object by object. The predecessor exactly matched its
committed strict fingerprint. No preexisting object disappeared, and exactly one
changed: the Ministry request guard, which now inherits the assignee and admits
a paired Staff projection. Additions are one revision table, 15 columns, 20
constraints, five indexes, five functions and two triggers; row policies are
unchanged.

The audit's model-parity check found a defect before it could land. The two new
CHECKs using `IN` had been pasted as Django's raw SQL. PostgreSQL deparses that
to a whole-array cast, while the model compile yields the per-element form the
contract compares. They are now written in the deparsed form, as every such
constraint in the baseline already is. A second fresh install differed from the
first only in those two renderings, and a third, after the report gained its
assignee, only in that one function body. Only after inspecting each exact delta
was the fingerprint updated.

The candidate has 209 relations, 2,349 columns, 3,256 constraints, 966 indexes,
564 functions, 533 triggers and 28 policies. The strict fingerprint and full
Django model/schema parity checks pass, and the revision guard is registered
with the immutable-record check as one that also validates INSERT.

No historical upgrade migration, retained-database deletion or compatibility
path was introduced. The three audit databases remain on the disposable cluster.

## Focused validation

All runs below are local, on the disposable PostgreSQL 18.6 and Valkey services,
and are focused selections rather than a complete acceptance pass.

- 32 database-free cases of the closed change grammar, 0.07 seconds.
- Six PostgreSQL cases under the real web and worker roles, about 37 seconds
  with the grammar table: scope, replay, stale writers, raw-SQL forgery, orphan
  and forged revisions, the work gate and a revoked actor; same-intent
  inheritance, chain history and dead old forms; all-or-nothing bulk
  assignment; the scoped read model and its filters; and the native HTTP flow
  through a real leader session with CSRF, denial, 409, seven malformed forms
  and proof that notes never reach an audit context.
- 33 cases covering the strict fingerprint, model parity, the immutable-record
  registry and the existing Ministry report and export suites against the
  changed guard and report function, 68 seconds.
- 31 existing Ministry response and authority cases against the changed request
  guard, 87 seconds.
- 24 browser cases on Chromium, Firefox and WebKit, 33 seconds: ten follow-up
  states at 320 and 1280 pixels with no horizontal overflow and no WCAG 2.2 AA
  violation, escaping of hostile names and notes, and script-disabled native
  POST for filters, the edit with a contact attempt and bulk assignment, with
  no identifying value in a URL. The existing Ministry report scenarios pass
  with its new follow-up link and assignee.
- The complete fast selection, Ruff lint and format, Markdown lint and
  `makemigrations --check` are clean.

Draft CI substitutes this increment's grammar module for the information
renderer module, keeping the ten-module bound. The renderer module stays in the
complete baseline run.

## Checkpoint

Implementation, focused validation and
[three review/fix rounds](stewardship-ministry-followup-reviews.md#round-3) are
complete, with no unresolved accepted Medium-or-higher finding. Full exact-head
CI, DCO and protected delivery remain open. ADM-08.03 is
implemented; RPT-07 stays unchecked until its packet reads this workflow, and
M5 and Gate 3 remain open. No deployment, release, live-provider write or
database deletion is authorized by this increment.
