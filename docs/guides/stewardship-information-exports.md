# Complete additional-information exports

This coherent Phase 5 increment starts at verified main `6a636680`, after
[PR #66's protected delivery](stewardship-additional-followup.md#protected-delivery).
It owns [RPT-04.04](../tasks/stewardship/reports.md#rpt-04-additional-information-workflow-report)
under the [report specification](../specs/stewardship/reports/spec.md#additional-information)
and [shared export contract](../specs/stewardship/background-processing/spec.md#exports-and-graph-rendering).

## Scope

Admin/Staff export every filtered item, with complete text and optional Staff
workflow history, as CSV/XLSX/PDF. A shared query feeds the queue and immutable
export snapshot. Native confirmation shows scope, estimate, format, timezone
and privacy warning; identifying filters remain CSRF POST state.

Reuse the existing asynchronous export execution, requester controls, guarded
downloads, cleanup and regeneration. Retries and regeneration retain the captured
data; later Family/Staff edits or source promotion cannot replace it. Existing
participation generation/pin protection stays intact. No Ministry authorization,
generic query language, new delivery framework or Phase 6 workflow is added.

## Acceptance and status

Implementation is in progress. Task completion requires full-result and history
parity, safe complete-text formatting, actual-role allocation/worker/download
tests, retained regeneration, denial/revocation/purge protection, and native
three-engine coverage. Schema/model checks include the all-model immutable-guard
inventory and independently inspected fresh-schema delta. No retained database
is deleted and no historical upgrade compatibility is introduced.

Use shared test fixtures and focused local checks, bounded draft CI, three
completed dual-source review/fix rounds, then full corrected-head CI/DCO and
protected delivery. Full RPT-04 acceptance and Gate 3 remain open until their
owning criteria are met; no deployment or release is authorized.

## Initial validation

- Final focused PostgreSQL validation passes all eight cases in 39 seconds:
  complete capture, native worker/download/regeneration, source refresh,
  authorization/gates, strict fresh-schema fingerprint, declaration parity and
  immutable guards. The combined pure/build/grant/CI selection passes 159 cases
  in four seconds. Ruff and formatting pass for all 1,364 Python files.
- Real web/worker/download-role integration passes for native CSV/XLSX/PDF
  allocation, replay, publication, streaming and expired regeneration after a
  later Family submission (18 seconds, one shared fixture).
- The 51-response history case proves complete capture versus 50-row HTML
  pagination. Staff authorization changes, purge preparation and missing-source
  parity pass with it (three cases, 29 seconds).
- Twelve Chromium/Firefox/WebKit native, mobile, keyboard, accessibility and
  no-script cases pass in 20 seconds. Five focused rendering cases pass in less
  than one second; the prior combined run passed the other 65 participation and
  follow-up checks before correcting one new test's expected character count.

## Fresh-install schema audit

Independent fresh predecessor/candidate databases were compared on the
disposable PostgreSQL cluster; the predecessor exactly matches its strict
fingerprint. Additions are one self-contained capture table, 11 columns,
17 constraints, six indexes, three functions and two triggers. The only removed
constraint is the old unconditional non-null fact reference: the strengthened
report/input check now requires exactly one report-specific retained input.
Only that existing column, the report/input constraint and three export guards
change. No unrelated object or policy changes. The candidate has 206 relations,
2,311 columns, 3,201 constraints, 949 indexes, 550 functions, 527 triggers and
28 policies. Update the strict fixture only after this inspected comparison.
