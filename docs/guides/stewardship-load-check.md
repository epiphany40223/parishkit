# Stewardship load check

The [v1 launch scope](../plans/stewardship/v1-launch.md#reduced-for-v1)
reduces the performance work to one load check at the parish's real Family
count, run against the validation deployment before the pre-launch gate.
`pk-stewardship load-check` is that check; the
[deployment runbook](stewardship-deployment-runbook.md#staff-validation-checklist)
says how to run it. Scale fixtures and enforced latency budgets
(ARC-08.01 and ARC-08.05) remain deferred.

## What it measures

The targets are the
[architecture specification's](../specs/stewardship/architecture/spec.md)
reference figures: a p95 server time under 2 seconds for ordinary pages and
under 3 seconds for a filtered report's first page, at a reference size of
5,000 Families.

- **Population:** the current campaign's Family rows (the set the scheduler
  walks), portal-eligible Families, Members and deliverable email counts,
  beside the reference size and the web service's process, thread and
  connection budget.
- **Family form inputs (2 s):** the per-Family source read that opening the
  form performs, for a sample of portal-eligible Families, largest
  households first, serially and with bounded concurrency.
- **Report first pages:** the statistics aggregate (2 s), and the financial
  (skipped when that section is off) and information first pages (3 s), 20
  runs each, admitted exactly as the pages admit them.
- **Invitation run (information only):** when a full Testing invitation run
  exists, how long planning, preparation and dispatch took and how many
  messages ended in each state; otherwise `not_run`. It never affects the
  verdict, so staff need not email every Family to produce load evidence.
- **Background:** queued and running tasks at the start and end, showing the
  timings were taken while the workers ran.

## Safety

It runs only in the web container of a deployment in Testing mode, under the
same admission as the health command. Every read runs in a read-only
transaction with statement and lock timeouts, so it cannot write, lock rows
or send mail; it builds no Valkey client, so it cannot touch the sign-in
limits; its concurrency is capped at the web service's threads; and its
output is limited to fixed keys, numbers, booleans and timestamps. Normal CI
covers it with fake-backed tests plus a small PostgreSQL test that runs its
reads under the real web login.

## Reviews

This increment follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds, with a correction check after any round that validates a
finding, each recorded here by which sources answered.
