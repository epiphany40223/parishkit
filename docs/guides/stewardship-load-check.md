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
  form performs (a lower bound for the page, which also records a baseline),
  for a sample of Families the form would admit (half the largest households,
  half spread across the rest), serially and with bounded concurrency. Each
  read rechecks that the Testing portal is open and the Family still
  admitted; a Family no longer admitted is skipped, not failed.
- **Report first pages:** the statistics aggregate (2 s), and the financial
  (skipped when that section is off) and information first pages (3 s),
  unfiltered, 20 serial runs each, admitted exactly as the pages admit them.
  The specification states a p95 only for ordinary pages; the check applies
  p95 to report first pages too.
- **Invitation run (information only):** when the current Testing
  credentials have an invitation run, how long planning, preparation and
  dispatch took and how many messages ended in each state; otherwise
  `not_run`. It never affects the verdict, so staff need not email every
  Family to produce load evidence.
- **Background:** queued and running tasks at the start and end, showing the
  timings were taken while the workers ran.

## Safety

It runs only in the web container of a deployment in Testing mode, under the
same admission as the health command, and only while the Testing Family
portal is open. Every read after admission runs in a read-only transaction
with statement and lock timeouts, so it cannot write, lock rows or send
mail; it builds no Valkey client, so it cannot touch the sign-in limits; its
concurrency is capped at the web service's threads and the web database
login's spare connections (it refuses when there are none); it stops a phase
after five failed reads and the whole run after 15 minutes; it refuses to
publish a verdict if the parish data was refreshed during the run; and its
output is limited to fixed keys, numbers, booleans and timestamps. Normal CI
covers it with fake-backed tests plus a small PostgreSQL test that runs its
reads under the real web login.

## Reviews

This increment follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds, with a correction check after any round that validates a
finding, each recorded here by which sources answered.

### Round 1

Claude and Codex both answered. Twenty-one raw findings; six validated and
corrected, with every Low below the cutoff taken:

- Medium (both sources): p95 truncated the nearest rank, so with small or
  uneven samples the slowest read could be ignored and a run could pass over
  target. It now uses the true nearest rank, with tests at two, four, seven
  and twenty samples.
- Medium (Codex): scope and background reads ran outside any bounded
  transaction; they now run read-only with statement and lock limits.
- Medium (Codex): the form timing never checked that the Family form would
  actually admit anyone. The check now refuses unless the Testing portal is
  open, and rechecks the scope and each sampled Family inside every read.
- Medium (Codex): concurrency was capped only by the web service's threads,
  not by the web login's spare connections; it is now capped by both and
  refuses when there are none.
- Medium (Codex): a source refresh during the run could mix populations; the
  check now refuses to publish a verdict if the promoted source changed.
- Lows: the reported concurrency is the number of threads that ran; the
  PostgreSQL test runs two overlapping readers; a mid-run error is reported
  apart from a refusal; phases stop after five failures and the run after 15
  minutes; the state vocabularies are tied to their sources; the invitation
  run is limited to the current Testing credentials; and the documents say
  how Families are sampled, that report pages are timed serially against a
  p95, and that the form timing is a lower bound for the page.

A correction check follows.
