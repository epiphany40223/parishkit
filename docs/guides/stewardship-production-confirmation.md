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

## Atomic confirmation checkpoint

An immutable confirmation receipt binds the exact preview, current Admin session,
post-cleanup Google authentication, expected versions and selected generation.
Its private SQL owner atomically changes lifecycle and mode, releases the gate,
and creates the existing bounded catch-up demand/task only for direct activation.
Web access gains receipt intake, not general lifecycle or mode mutation. Final
confirmation performs no Family, occurrence or outgoing-message enumeration.

Two real restricted-role cases cover scheduled and direct-active confirmation,
rejection of authentication predating cleanup, and identical replay. Both pass
in 39.79 seconds. The browser workflow and negative/race acceptance still follow;
these internal checks do not complete ADM-05.03.

Independent fresh databases with suffixes `r` and `s` compare immutable `99eb462`
with this candidate. The final inventory adds one table, 20 columns, 36
constraints, seven indexes, two private functions and two triggers. Only the
existing Production-request state guard and go-live gate guard change; their
narrow activation exceptions require the exact confirmation receipt. Existing
owners, ACLs and policies are preserved. The second audit corrects only the
target-state literal cast to exactly match the Django model declaration, without
normalizing or weakening the contract test. All 18 strict schema/model and
immutable-guard cases pass in 18.52 seconds. Totals are 187 relations, 2,150
columns, 3,072 constraints, 920 indexes, 529 functions, 516 triggers and 28
policies. No retained database is upgraded, downgraded or deleted.

## Confirmation and progress forms

The preparation page now links to passive final-readiness status. Only an
explicit CSRF-protected verification enumerates impact and checks public DNS;
fresh post-cleanup Google authentication and typed `Production` admit the exact
signed confirmation. Native forms work without JavaScript. Status separates
campaign lifecycle from durable mail-preparation completion, exposes real worker
timestamps/counts without an invented denominator, and offers exact safe retry.
It never calls task success proof of completed coverage or delivered email.

Three real-database scenarios pass in 56.06 seconds, covering bounded readiness,
both HTTP activation outcomes, CSRF/stray-field rejection, passive idle behavior,
fresh authentication, identical confirmation/retry replay, competing retry and
revocation. A late post-effect failure rolls back mode, generation, receipt and
gate together and permits retry. The stale-scope/expiry/start/close/source-age,
changed eligibility and cancelled-preparation scenario passes in 25.06 seconds.
Nine browser cases pass in 10.98 seconds across Chromium, Firefox and WebKit,
320/1,280-pixel widths, native keyboard submission, no-JavaScript controls and
WCAG scans. The component test receives its synthetic POST locally rather than
depending on browser-specific native error-page navigation. Reference-load,
additional SQL/race acceptance and peer reviews remain open.
