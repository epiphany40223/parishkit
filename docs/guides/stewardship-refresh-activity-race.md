# Stewardship refresh activity race

This guide records a correction found by the
[pre-launch gate](../plans/stewardship/v1-launch.md#v1-process-changes)
review: a Family's portal activity could abort a source refresh. It is part
of the [v1 launch scope](../plans/stewardship/v1-launch.md) and needs no
schema change.

## The defect

`reconcile_families()` read every `FamilyCampaign` row of the campaign
without a row lock, advanced `version` in memory, and wrote the rows back
with `bulk_update()`. Every refresh carries a new source generation, so
every row is written. Family activity (a portal load with activity, or a
keepalive) runs `authenticated_family()` outside the work order and bumps
the same row with `version = version + 1`.

Under READ COMMITTED, an activity bump that committed between the refresh's
read and its write left the row one version ahead of the refresh's copy.
The refresh then wrote the version the row already had, the
`stewardship_family_campaign_mutable_v1` guard rejected it with SQLSTATE
23514 ("Every update must advance the record version"), and the whole source
promotion rolled back. During the live campaign any Family activity in that
window aborted a manual refresh or a scheduled delta, and launch-day traffic
would make that repeat.

## The correction

The refresh now reads the campaign's rows with
`select_for_update(no_key=True)`, ordered by primary key, right after it
locks the Campaign and its population state. An activity bump on a row the
refresh holds waits for the refresh to commit and then advances the
committed version; a bump that commits first is read by the refresh, which
advances from it. Versions stay monotonic, and the eligibility history keeps
recording the refresh's own version. The code-assignment batch reads its
rows from the same locked set, so it is covered too.

What advances the version is unchanged, and activity still bumps it.
Writing `version + 1` in SQL from the refresh would also satisfy the guard,
but the refresh would still decide from a stale copy of each row; the lock
keeps its read and write coherent. Dropping the activity bump would need a
review of every version consumer, which this correction does not need.

## Why it cannot deadlock

- The lock mode is the one the refresh's `UPDATE` statements already took:
  none of them changes a unique key, so PostgreSQL takes `FOR NO KEY UPDATE`.
  The fix moves the same row locks earlier in the same transaction; it adds
  no new lock and still conflicts with neither the foreign-key share locks
  of session, token and code inserts nor plain readers.
- The refresh still locks in its existing order: work order, runtime
  configuration, Campaign, population state, then Family rows in primary-key
  order.
- Family activity locks its `FamilySession` row and then one `FamilyCampaign`
  row, and takes no further lock after it: the activity update changes none
  of the columns that fire the population-dirty, eligibility-history or
  activation-impact triggers. The refresh never locks a `FamilySession` row,
  so no cycle can form.
- Every other `FamilyCampaign` writer (response submission, recipient
  suppression and resolution) holds the work order, which the refresh also
  holds for its whole transaction, so those writers never interleave with
  it.

Locking every row costs no more than the old write did, since a refresh with
a new generation already updated every row. What changes is when the locks
are taken: at the start of Family reconciliation rather than at its final
write, and they are held until the promotion commits. A Family page load or
keepalive that arrives in that window waits, holding its own session row,
with no lock timeout; for an ordinary quarter-hour refresh the window is
short, and for a large first import it includes code allocation for every
newly eligible Family. A replay of the same source generation, which writes
no Family row, now also locks them all for its duration.

## Focused validation

- PostgreSQL, new regression: a second backend commits the activity bump
  just after the refresh reads its rows. Without the fix the refresh fails
  with the 23514 guard error; with it the activity is observed as a blocked
  lock waiter, the refresh commits, the activity then advances the version
  once more and the eligibility history keeps the refresh's version.
- PostgreSQL, related suites: Family identity, identity review and
  performance, second-review races, Family authentication, presence, code
  lookup, active tokens, source Families, effects, refreshing, refresh
  views, deltas, production, superseding and the schema baseline pass.

## Checkpoint

Implementation and focused validation are complete. The three
[review rounds](stewardship-refresh-activity-race-reviews.md), full
exact-head CI, DCO and protected delivery remain open. No deployment,
release, live-provider write or database deletion is authorized by this
increment.
