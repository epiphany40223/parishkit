# Daily Administrator digests

## Scope and prerequisites

This coherent Phase 4 increment starts on `pr/stewardship-daily-digests` from
PR #46's verified merge `849cc71f`. It implements
[BG-07.02](../tasks/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests)
and its daily-specific BG-07.05 tests under the
[controlling plan](../plans/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests).
The authoritative behavior is the
[daily digest contract](../specs/stewardship/background-processing/spec.md#daily-campaign-digest),
[report parity](../specs/stewardship/reports/spec.md#daily-email-report-parity),
and [immutable input model](../specs/stewardship/data/spec.md#campaign-daily-report-facts).
Weekly digests, full post-close resolution, the complete Phase 5 report UI and
Gate 3 are not completed by this increment.

## Internal acceptance checkpoints

1. Produce accessible daily/recovery content and a compiled inline chart from
   the same immutable participation document and statistics library as reports.
   Add reusable in-memory inline-image MIME support without enabling arbitrary
   file attachments or image markup in Family receipt templates.
2. Freeze coherent report observations and exact fact-build inputs under durable
   digest ownership. Integrate source/fact pins, exact-generation recovery and
   compaction protection without consuming interactive debounce revisions.
3. Materialize bounded ordinary/recovery work through existing schedule and
   immutable replacement lineage. Never release a truncated missed-day group.
   Route separate messages to current exact-address Admins, enforcing Testing,
   current authority, pause, restore and post-close gates.
4. Reuse fenced provider-attempt ownership, truthful uncertainty and explicit
   recovery. Aggregate completion cannot claim success while any required
   recipient result remains unresolved. Preserve pinned report-link parity and
   integrate Testing cleanup and Admin observability.
5. Verify pure calculations/MIME, real-role PostgreSQL admission and races,
   fake-provider commit order, responsive/accessibility behavior, current
   fresh-install schema and container isolation. Run full coverage and three
   successful dual-source review/fix rounds, exact-head CI/DCO and the normal
   protected merge queue before advancing.

## Execution evidence

Implementation is beginning. The checkpoints above are targets, not completion
claims; BG-07.02 remains unchecked. No provider credentials, real delivery,
deployment, release or retained database deletion is authorized.

### Rendering checkpoint

The detached daily/recovery renderer reuses participation charts and current
statistics, rejects mixed observation cutoffs, and includes an accessible daily
table and protected snapshot-link identity. Shared MIME supports bounded
in-memory raster images without filesystem attachments. Admin digest templates
accept only public campaign substitutions; individual routing preserves the
intended Admin while sending Testing mail only to the configured test address.

Validation: 212 focused report/MIME/statistics/coverage checks and 236
content/routing/editor checks passed. The credential-free baseline passed
6,283 tests, with 4,288 database/browser/runtime cases intentionally skipped
and two existing Redis-client deprecation warnings, in 58.67 seconds. Ruff
check and format validation passed. Durable ownership, the linked report route,
worker scheduling/delivery, integration validation and review gates are still
in progress; these results do not claim a functioning scheduled digest yet.

### Private transport checkpoint

The one-Admin chart adapter now shares the established SMTP and private-pipe
transport while retaining a distinct closed payload. It permits only the
compiled inline PNG, validates HTML and image bounds, and preserves unknown
acceptance without automatic resend. Family input restrictions and the smaller
Family/readiness transport limits remain unchanged.

Validation: 252 focused digest/Family/transport/report checks passed, including
the actual isolated helper with deliberately invalid synthetic credentials and
no Django configuration. The full credential-free baseline passed 6,345 tests
with 4,288 profile skips and the same two deprecation warnings in 58.58 seconds.
This adapter has no scheduled execution authority yet; database ownership and
runtime integration remain the next checkpoint.
