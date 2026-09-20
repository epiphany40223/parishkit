# System logs screen

This Phase 5 increment begins at verified main `1070fd28`. It is the first slice
of ADM-08.04 in
[ADM-08](../plans/stewardship/admin-portal.md#adm-08-manual-refresh-follow-up-queues-and-logs):
a read-only, Administrator-only screen combining operational diagnostics and the
audit trail. The [logs specification](../specs/stewardship/admin-portal/spec.md#logs)
controls behavior.

It is independent of the open financial stewardship detail and portal users
increments and branches from main, not from either.

## Scope and acceptance

An Administrator opens **System logs** from the Admin navigation and sees both
sources together, newest first, fifty entries a page: time, source, severity,
type, actor, the correlation, campaign and subject identifiers, and the recorded
detail. The five levels are distinguished by a word and a symbol, never by
color alone. DEBUG is excluded until chosen.

Filters cover level, source, type, actor, task or request correlation, campaign
and a date range. The specification's text and JSONL export, its full-text
search, entity and Ministry filtering including the retained export result
scope, and an export time-zone choice are **not** in this increment. They follow
with [RPT-09](../plans/stewardship/reports.md#rpt-09-logs-and-daily-email-parity)
on the shared export pipeline, so ADM-08.04 stays unchecked.

The specification's before/after detail for audit events is met only as far as
the stored evidence goes: the recorded-detail column shows the reviewed
`before_version`, `after_version`, `before_state` and `after_state` fields when
an event carries them. No event stores before and after *values*, by design, so
there is nothing richer to show.

## Design

### A closed grammar, without hiding real types

Every filter is an exact value from a closed shape: a canonical identifier, a
canonical UTC day, a level tick or a source choice. Identifiers travel only in
CSRF POST bodies; a query string is refused, so none reaches a URL or a log.

The type filter is an exact identifier, the same shape the audit table's own
check constraint enforces, not a fixed list. Many audit types are written
directly by their owners and by SQL triggers, such as `admin_login` and
`family_submission`, and are in neither reviewed application vocabulary; a fixed
list would have made them unsearchable. The known types are offered as
suggestions only.

A submitted form carries a marker, so a form with no level ticked means no
operational entries rather than falling back to the first visit's default.

Audit records have no severity level. The level choices apply to operational
entries, and the source choice decides whether audit records are listed; the page
says so.

### A keyset cursor, because the log grows while it is read

Paging is a cursor on the last entry shown, ordered by time and then identifier,
exactly as each source's query orders it. An offset would skip or repeat entries
as new ones arrive, and would grow more expensive with depth. Each source reads
at most one page plus a sentinel row, and the first page of their merge is the
true next page of the union.

### Only reviewed detail is shown

Stored context was already reduced to reviewed closed fields when it was
written. The page still renders only fields that some reviewed context schema
names, with a number, a Boolean, a list of numbers or text of at most 128
characters, and renders them as text, never as markup. A key that merely looks
like an identifier is not enough: a future flat text field, or a context written
directly by an SQL trigger, would otherwise be rendered verbatim.

An actor is shown by address on screen, for the actors on that page only, and
never enters the audit event or a log. Its identifier is shown beneath the
address, because the Actor filter takes that identifier. One that is not a
current portal user, such as a Family or a former user, is shown as that.

Dates are whole UTC days between 2020 and 2999. The last representable day
cannot be advanced to its exclusive upper bound, so it is refused as a filter
rather than allowed to overflow.

### Authority, audit and cost

Only an Administrator reads the logs, checked through `Capability.SYSTEM_LOGS`
by both the page and its navigation entry. The log only grows, so an ordinary
snapshot is coherent and the shared work lock is not taken. The page is captured
in one transaction, rendered outside it, and current access is rechecked before
the view is audited. Each view records one audit event carrying an outcome and
a count, never a filter or an identifier, and the response is never cached.
Because every view is audited, it appears in the list the next time; that is
expected.

The page is unavailable during a restore review, like the delivery pages beside
it.

Ordering by time across both tables is not served by their composite indexes,
and the audit table gains a row for each view of this page. Pages are bounded
and parish logs are small; an index on time then identifier, with the cursor
written as a row comparison so it can be one range scan, is a candidate for the
export increment if measurement shows a need.

Two known limits are accepted here. The two sources are read by separate
statements, so an entry committed between them, or written by a transaction
older than the cursor, can be missed by a reader already past that instant; the
newest page shows it. With scripts enabled the shared local-time enhancement
shows minutes, so entries within one minute read alike there, while the
unscripted page shows the full UTC instant.

An error page gives filter guidance only when a filter was the problem: a denied
reader or an outage submitted nothing that could be corrected.

## Schema

No schema or grant change. The audit action is an application vocabulary entry
with no SQL constraint, and the restricted web role already reads all three
tables, which the PostgreSQL cases prove by running under that role.

## Focused validation

All runs are local, on the disposable PostgreSQL 18.6 and Valkey services, and
are focused selections rather than a complete acceptance pass.

- Four database-free cases, under one second: DEBUG excluded until chosen and an
  empty choice meaning none; the closed grammar with 26 refusals, including the
  last representable day, and a repeated parameter, accepting a directly written
  type; the detail whitelist dropping unreviewed flat text fields, overlong
  values, nested values and oddly keyed context; and the merge ordered across
  sources with a cursor that round-trips through the grammar.
- Nine PostgreSQL cases, every request under the real web role, 19 seconds. Both
  sources on the default page, eight filtered views including a directly written
  type, a refused query string and unauthenticated POST, six refused filters
  with guidance and no echoed value, and nine audit rows naming nothing. Older
  entries reached by a stable cursor while the log grows between requests. A
  cursor walked across both tables through thirty entries sharing one instant,
  each exactly once in one total order, which also proves PostgreSQL and Python
  order identifiers alike. Staff and Ministry leaders denied with no filter
  guidance, no navigation entry and no audit row. Access lost between rendering
  and the recheck disclosing and auditing nothing. A restore review, whether
  present before the read or beginning during the request, making the page
  unavailable and auditing nothing. Five hundred more entries leaving one
  bounded read of each log table and no per-row actor lookup.
- The 16-case navigation suite pins the entry to Administrators.
- Nine browser cases on Chromium, Firefox and WebKit, 14 seconds: accessibility
  scans of six states at 320 and 1280 pixels, severity words beside their
  symbols, detail rendered as text, kept filters, and script-disabled filtering
  and paging that carry the applied filters and leave the URL clean.
- Ruff lint and format and Markdown lint are clean.

Draft CI's ten-module fast selection is unchanged by this increment for now: two
open increments edit the same lines, so the substitution is made when the
delivery order is known.

## Checkpoint

Implementation and focused validation are complete. One
[review attempt](stewardship-admin-logs-reviews.md) was single-source because
the Codex reviewer failed, and is not counted; its findings were fixed. Three
dual-source review/fix rounds, full exact-head CI, DCO and protected delivery remain open. M5 and
Gate 3 remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.
