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

No review round has run yet.

## Round 1

Claude only (Codex produced no structured output). Five raw findings, none
validated. Two low-severity notes were taken: the lock comment now records
the other half of the deadlock argument (the rest of the promotion must never
lock or update a `FamilySession`), and the guide records the Family-request
latency while the locks are held, including for a large first import and a
same-generation replay. Driving the real `authenticated_family` path in the
race test and sharing the lock-waiter query with `lock_observer` were not
taken. A further round follows.
