# Stewardship final Production confirmation

Continue [ADM-05.03](../tasks/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
from [PR #59's protected delivery](stewardship-production-activation.md#protected-delivery)
on `pr/stewardship-production-confirmation`, based on main `880507fc`.
The [Production-transition specification](../specs/stewardship/admin-portal/spec.md#production-transition)
and [implementation package](../plans/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
control behavior and acceptance. The
[automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
controls reviews, exact-head checks and protected delivery.

## Increment boundary

Deliver current post-cleanup readiness and exact impact, freshly authenticated
typed confirmation, atomic scheduled/direct-active activation, and visible
catch-up status/retry. Include the corresponding boundary, SQL-role, replay,
load and browser acceptance. Pre-start withdrawal follows in a separate coherent
increment; ADM-05.04 and the complete ADM-05.05 remain open. No activation owner
is exposed by the initial internal checkpoint below.

## Checkpoints

1. Establish a constant-size guard for every enumerated mail-impact dependency,
   excluding session-activity noise. Independently audit the fresh schema.
2. Collect post-cleanup current readiness; retain only intentionally deleted
   Testing proof through its immutable aggregate. Bind the exact preview to
   relevant versions and its next time-based invalidation boundary.
3. Implement signed CSRF/current-Admin/fresh-Google typed confirmation. Atomically
   commit lifecycle state, selected generation, activated request, gate release
   and the existing bounded catch-up handoff; no Family scan in final locks.
4. Expose active campaign separately from initial-mail preparation hold, with
   durable status and safe current-Admin retry.
5. Validate SQL roles, stale inputs, concurrent replay, expiry/start/close races,
   5,000-Family final-transaction time and responsive submissions during catch-up.
   Exercise mobile, keyboard, no-JavaScript and all three browser engines.
6. Complete three dual-source review/fix rounds, exact-head CI/DCO and protected
   delivery before beginning withdrawal from refreshed main.

## Impact revision checkpoint

A private SQL statement trigger maintains a global mail-impact revision in the
same transaction as relevant Family eligibility, schedule/coverage/restore,
weekly selection/acceptance and outbox-state changes. One bulk statement ticks
once rather than once per Family. Activity-only changes do not tick. The global
scope conservatively invalidates previews for historical mail changes too;
it avoids omitting shared acceptance/restore dependencies. Runtime web access
is read-only, not authority to reset the clock. Final confirmation must still
validate current nonenumerated readiness and time boundaries separately.

The first three PostgreSQL cases pass in 11.79 seconds: bulk change versus
activity noise, transactional rollback, and actual restricted-role read-only
access. Initial model-state drift validation and Ruff pass.

Independent retained databases `stewardship_mail_health_before_20260918q` and
`stewardship_mail_health_after_20260918q` compare immutable `880507fc` with the
candidate. The predecessor matches its recorded fingerprint. The only additions
are one table, two columns, six constraints, one index, one private function and
13 statement triggers. No existing object changes or disappears; policies,
existing ownership and grants remain unchanged. Totals are 186 relations,
2,130 columns, 3,036 constraints, 913 indexes, 527 functions, 514 triggers and
28 policies. This is a current fresh-install baseline, not an upgrade or deletion
of any retained database.

All 17 strict fresh-schema/model-contract cases pass in 19.07 seconds. Final
preview, confirmation and catch-up UI remain in progress; no ADM task is marked
complete by this internal checkpoint.

## Readiness and time-boundary checkpoint

Post-cleanup collection now independently rechecks current configuration,
source/full-load freshness, integration acknowledgements, source-bound ready
links and their succeeded task, current Admin authority and pending work. Only
the deleted Testing proof is represented by the immutable sealed cleanup
aggregate. The cheap readiness digest includes the impact revision; it does not
enumerate Family rows or schedule occurrences.

The separate preview enumerates exact Family/digest impact, checks the revision
again afterward, and expires at the earliest source, five-minute, start/close
or newly due schedule boundary. The canonical civil-time planner is reused,
including long paginated history and DST folds. Twelve pure cases pass in
0.06 seconds. A real setup/cleanup/restricted-worker/restricted-web scenario
passes in 24.14 seconds, demonstrating post-cleanup readiness, no Family or
occurrence enumeration in its short recheck, unchanged mode, and invalidation
after changed mail eligibility. No final-confirmation command is exposed yet.
