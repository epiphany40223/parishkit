# ParishKit Sync — Logging and Audit

What Sync records, how administrators search and export it, and how long it
is kept. Overview: [Sync spec](../spec.md).

## Log

- What happened: structured records with time, level, module, job, run, step,
  message and JSON fields.
- Sources: the web process, the scheduler and child `log` events (see
  [jobs](../jobs/spec.md#progress-protocol)).
- Stored in SQLite with an FTS5 index for full-text search. SQLite 3.46.1 in
  the base image supports FTS5, JSON, JSONB and WAL (verified 2026-10-25).

## Audit

- Who did what: sign-ins, setting changes, config saves and reverts,
  credential set, reveal and delete, Run now, Preview, Cancel, enable,
  disable, schedule changes, update actions.
- Append-only: the application never updates or deletes audit records except
  through retention.

## Search and export

- Filters: time range, level, module, job, run, step, actor (audit), plus
  full-text search.
- Export of the filtered result as JSONL or CSV; exports are audited.

## Retention

- Logs are deleted at an **age limit or a size cap, whichever comes first**.
  Defaults: 90 days and 1 GB; administrators can change both.
- The size cap applies to the **whole database file**, not just the log
  table: logs, their full-text index, audit records, config revisions,
  credentials and module state together. Retention trims the oldest logs
  (and their index entries) until the file is under the cap, so the
  database, and the pre-update snapshot of it (see
  [deployment](../deployment/spec.md#disk-budget)), stay within it.
- Everything else is small (config documents and credentials are
  kilobytes). If non-log content alone grows past a quarter of the cap, an
  alert says so instead of letting log retention shrink to nothing.
- Deletion runs in small batches with incremental vacuum, so the database
  file actually shrinks.
- **WAL counts too.** The cap covers the main file **plus** its write-ahead
  log. The portal sets SQLite's `journal_size_limit` and runs a
  `wal_checkpoint(TRUNCATE)` after each retention pass and whenever the WAL
  passes a threshold. Long reads cannot pin the WAL: log search pages and
  exports read in short transactions (one page or chunk at a time), so a
  checkpoint can always complete. If the combined size still nears the cap,
  retention runs early and an alert fires.
- Audit records are kept longer than logs (they are small).
- Logs can contain parishioner names and email addresses. Modules log counts
  and identifiers in routine records and names only where a person needs
  them (for example, the members added to a group).

## Console output

Only run summaries and errors go to stdout; the full detail lives in SQLite.
Podman sends stdout to journald, which bounds it (see
[deployment](../deployment/spec.md#disk-budget)).

## Low disk

The portal checks free space regularly. Below a threshold it raises a
dashboard alert and a notification.

## Shared code

Reuse the shared `parishkit.logging` structured-logging helpers where they
fit.
