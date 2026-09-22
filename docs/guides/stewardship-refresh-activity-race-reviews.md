# Stewardship refresh activity race reviews

This ledger records the independent review/fix rounds of the
[refresh activity race correction](stewardship-refresh-activity-race.md),
under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, because it changes Family authentication and source
reconciliation locking, with a correction check after any round that
validates a finding. The Codex reviewer has been out of quota since
September 20, 2026; under the human's exemption, extended through October
30, 2026, a completed Claude-only pass counts as a round, and each round
records which sources answered.

## Round 1

Claude only (Codex produced no structured output). Five raw findings, none
validated. Two low-severity notes were taken: the lock comment now records
the other half of the deadlock argument (the rest of the promotion must never
lock or update a `FamilySession`), and the guide records the Family-request
latency while the locks are held, including for a large first import and a
same-generation replay. Driving the real `authenticated_family` path in the
race test and sharing the lock-waiter query with `lock_observer` were not
taken. A further round follows.

## Round 2

Claude only (Codex produced no structured output). Four raw findings, none
validated. Two low-severity notes were taken: the ledger's stale opening
sentence is removed, and the guide now says a true first import makes no
one wait, since no Family can hold a session before its row is committed,
while a refresh that makes many existing Families eligible holds the locks
through their code allocation. A test of the code-allocation path's double
write and a lock timeout on Family requests were not taken: the locks were
already held through the rest of the promotion by the old final write, and
the guide records the latency. A further round follows.

## Round 3

Claude only (Codex produced no structured output). Three raw findings, none
validated; the review rounds are closed. The low-severity notes (the guide's
lock order omits two earlier shared locks that do not affect the argument,
the race test accepts any blocker rather than the refresh's own backend, and
its outcome list is positional) were not carried forward.
