# Stewardship report consumer increment

[Controlling sequence](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
· [Report tasks](../tasks/stewardship/reports.md)
· [Calculation contract](../specs/stewardship/reports/spec.md#participation-fact-materialization)

## Scope and boundary

Branch `pr/stewardship-report-selection` starts from PR #41's verified merge
`2eade2a53163d621ade3145ede092f7316055bda`. Its exact-head CI run `35141293392`
and protected merge-group run `35143121665` both passed all 24 jobs; DCO passed.

This prerequisite consumer increment integrates authorized current/stale
participation selection with the existing read-only campaign guard, shared
export document loading, and drift verification/compaction protection. These
are one demonstrable report-consumption boundary, not a new UI or an independent
calculation engine. Splitting it from queued exact-input requests keeps the
read-lifetime locking review separate from task ownership and retry policy.

Queued exact-input requests, pre-execution input pins, claim priority and
scheduled verification remain the following increment before BG-07. RPT-02's
remaining statistics/comparison services also remain prerequisites. Interactive
HTML/chart controls and the complete report authorization/accessibility matrix
remain Phase 5; periodic compaction remains OPS-07. No RPT-03 task is marked
complete by this partial consumer integration. Gate 3 remains closed.

## Consumer contract

- `reports/selection.py` authorizes current Admin/Staff policy inside a bounded
  `CampaignReadGuard`. Ministry leaders cannot read parish-wide participation
  or financial data. The caller supplies the real response-abort hook and keeps
  rendering inside the context; this service exposes no HTTP route yet.
- Expected campaign configuration, promoted source, maximum live submission
  sequence and selection instant come from one SQL statement. Campaign-local
  midnight and the selected campaign's immutable timezone define the through
  date. Testing responses cannot advance the expected live cutoff.
- Selection prefers an exact ready generation, then this campaign/scope's
  published pointer. Every fallback retains its own source-as-of, configuration,
  submission cutoff and date metadata, with `updating` status. No selection is
  `unavailable`, never fabricated zero values. A scope cannot borrow another
  scope's graph. A generation removed before lock acquisition is not silently
  replaced by an arbitrary latest row.
- `reports/documents.py` is shared with actual queued export rendering, so its
  complete immutable series and metadata are identical for equal inputs.
  Selecting a report does not allocate work or mutate its interactive pointer.
  Export allocation separately pins the immutable chosen generation.
- Generation reads hold shared transaction advisory locks in namespace
  `736231`, keyed by PostgreSQL's hash of the canonical generation UUID.
  Hash collisions only delay optional cleanup. The compactor skips a locked
  generation; direct SQL day/header deletion must obtain the exclusive lock.
  This replaces row locking, which PostgreSQL forbids in the campaign guard's
  `READ ONLY` transaction, without making that transaction writable.
- Current-scope verification checks the generation's durable source pin. SQL
  already forbids releasing it while the generation exists, so protected fact
  readers do not need a second source row lock to retain its membership data.
  Historical verification continues to use permanent provenance.

## Validation and review checkpoint

Implementation in progress; no completed review rounds or delivery claimed yet.
Initial focused validation: 56 PostgreSQL tests passed, including selection under
the actual web role, shared export document parity, both verification scopes in
read-only guards, and existing worker/export/retention behavior.

Fresh-install audit on disposable port `55440` independently compared verified
base and current schemas in `stewardship_selection_base_20260916a` and
`stewardship_selection_current_20260916a`. The base matched its committed catalog
fingerprint. Only `stewardship_fact_day_guard()` and
`stewardship_fact_set_guard()` changed; all relations, columns, constraints,
indexes, triggers and policies were identical. No upgrade migration, retained
database conversion or deletion is included.
