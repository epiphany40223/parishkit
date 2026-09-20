# Multi-Ministry follow-up packet

This Phase 5 increment begins at verified main `bd5522f6` after PR #72's
[protected delivery](stewardship-ministry-followup.md#protected-delivery). It
implements [RPT-07](../plans/stewardship/reports.md#rpt-07-multi-ministry-follow-up-packet),
the printable packet that Staff and Ministry leaders work from. The
[packet specification](../specs/stewardship/reports/spec.md#multi-ministry-follow-up-packet)
controls behavior. It reads the recorded contact dates, notes and outcomes of
the Ministry follow-up workflow, which is why that increment landed first.

## Scope and acceptance

A requester ticks the Ministries to include, or none for every Ministry they
are authorized for, and chooses whether to include resolved and withdrawn
requests. One control has one meaning, so the native form cannot reach an
ambiguous state without scripts. The packet
has one section per Ministry with its name, active chair names and stewardship
campaign, year and period, then one row for each latest effective join or leave
request: Member name and DUID, authorized email and phone values, the recorded
email-contact and phone-contact dates, and the outcome under the specified
mapping. Recorded values are prefilled; every other cell stays blank for
completion by hand. XLSX has one sheet per Ministry, PDF starts each Ministry on
a new page, and CSV is one file with a blank row and repeated headings between
Ministries.

Ministry leaders receive only their assigned Ministries, and email and phone
follow the Ministry report's ParishSoft publish-flag rule, rendering
`Not published` rather than a source value. That rule applies identically to
CSV, XLSX and PDF because all three render one captured document.

## Design

### One more closed action on the existing export lifecycle

A packet is a `ministry` export whose captured action is `packet`. It reuses the
requester-owned [export lifecycle](stewardship-ministry-exports.md): immutable
capture, the background task, current-scope rechecks at status and download,
private detached rendering and audit. No second task engine or snapshot table
was added, and the scope recorded for later rechecks has the same shape.

The capture trigger validates the packet's closed parameters. Its only choices
are the Ministries and the history option; every other report filter must hold
its neutral value, so one capture has exactly one meaning. The selection is JSON
`null` for every authorized Ministry, or one to 200 distinct ascending DUIDs.

### Its own projection

The packet is neither a wider Ministry report nor a wider follow-up queue. The
report withholds leave-request contacts and never carries workflow notes; the
queue never carries contact payloads. A packet needs publish-scoped contacts for
both actions together with contact dates, so it has its own SQL projection.
Workflow notes stay private there too: they are projected only for the outcome
`other`, whose specified label is `Other` with its notes or reference.

Latest intent is selected before state filtering. By default a packet lists
unresolved latest requests; the history option adds resolved, closed and
withdrawn ones. Because selection happens first, the option can never resurrect
a superseded request. Contact dates and the `other` notes are read across the
same-intent supersession chain, so they survive a Family resubmission.

### Scope is intersected, and an explicit selection is exact

SQL intersects the selection with the requester's current scope and the
campaign's Ministries; caller input is never authority. An explicit selection
must then resolve to exactly the Ministries asked for. Silently dropping one the
requester cannot see would capture a packet that misstates its own request, so
SQL refuses it. The service checks the same things first, so a leader choosing
another Ministry is denied and a stale or crafted selection outside the campaign
is a bad request rather than a database error reported as an outage.

A selected Ministry with no matching request still gets its section. A missing
page would read as "nothing to do" for the wrong reason.

### Header and row decisions

Chair names come from current roster evidence: a current ASCII `Chairperson`
role held by an active Member, the same rule as chair suggestions. Listing a
name needs no contact or login, and none is shown. They are computed in one
pass over the snapshot roster for every Ministry at once, because the capture
runs while holding the shared work lock and a per-Ministry rescan would stall
Staff follow-up during a parish-wide packet.

The header carries the campaign name, its stewardship year and its period
dates. The year uses the application's single campaign-year rule, the one behind
Admin previews, page blocks and share labels: the Admin-configured year label,
otherwise the start year. An autumn campaign that funds the following year is
labelled by that configuration, so the packet never derives a year of its own.
SQL captures the raw label and the application applies the rule, keeping one
copy of it.

The specified row contents do not say whether a request is to join or leave, and
with history a blank outcome cannot tell an unresolved request from a withdrawn
one. Two columns, Request and Status, were added so a printed row is
unambiguous. Contact dates are calendar dates in the requester's chosen zone.

### Rendering

The sectioned renderer reuses the field/value line wrapper and the page writer
extracted from the existing complete-text renderer, so both wrap, escape and
paginate identically; that renderer's behavior is unchanged. Every spreadsheet
cell is a literal string and CSV cells are formula-safe. Worksheet titles are
made valid and distinct within the 31-character limit despite duplicate, long or
forbidden Ministry names, including a reserved title and an apostrophe exposed
by truncation. The spreadsheet library silently cuts a cell at 32,767
characters, so rendering refuses such a value rather than publish an incomplete
packet; CSV and PDF keep it. PDF overflow adds pages and never truncates or
merges two Ministries. An empty packet is still a valid file carrying its provenance.

## Fresh-install schema audit

Fresh databases were installed separately from verified main `bd5522f6` and from
the candidate on the disposable PostgreSQL 18.6 cluster and their complete
catalogs compared object by object. The predecessor exactly matched its
committed strict fingerprint. Nothing was removed. Exactly one existing object
changed, the Ministry export capture trigger function, and one function was
added, the packet projection. Tables, columns, constraints, indexes, triggers
and row policies are unchanged. Only after inspecting that exact delta was the
fingerprint updated. The candidate has 565 functions; every other count is
unchanged. No upgrade path was added and no retained database was deleted.

## Focused validation

All runs are local, on the disposable PostgreSQL 18.6 and Valkey services, and
are focused selections rather than a complete acceptance pass.

- 17 database-free cases, 1.2 seconds: canonical selection, every outcome
  mapping with unresolved blank, contact dates in the display zone, withheld
  contacts, a proposed Member, refusal of incomplete or naive captures, CSV
  separators and repeated headings with formula safety, worksheet naming and
  literal cells, an empty packet, and PDF page starts with overflow.
- Two PostgreSQL cases under the real web and worker roles, 25 seconds: scope
  intersection, the history option, chair names, chain-derived contact dates,
  notes only for `other`, a leader's withheld contacts, denial of another
  Ministry, the audit context, twelve forged captures refused at the statement
  with a positive control and message matching, worker rendering of the retained
  capture, and the native request form with CSRF, replay, denial and ten
  malformed selections.
- Nine existing Ministry report and export PostgreSQL cases against the changed
  capture trigger and worker dispatch, 54 seconds, and the 17-case schema
  contract, 27 seconds.
- 15 browser cases on Chromium, Firefox and WebKit, 27 seconds, including the
  accessibility scan of the summary page with its packet section at 320 and
  1280 pixels and the script-disabled native packet request.
- The 62 existing complete-text renderer and export cases pass unchanged after
  the extraction. Ruff, Markdown lint, `makemigrations --check` and the fast
  selection are clean.

Draft CI substitutes this increment's packet module for the follow-up grammar
module, keeping the ten-module bound; that module stays in the complete baseline.

## Checkpoint

Implementation, focused validation and
[three dual-source review/fix rounds](stewardship-ministry-packet-reviews.md#round-3)
are complete, with no unresolved accepted Medium-or-higher finding. Full
exact-head CI, DCO and protected delivery remain open. M5 and Gate 3 remain
open. No deployment, release, live-provider write or database deletion is
authorized by this increment.
