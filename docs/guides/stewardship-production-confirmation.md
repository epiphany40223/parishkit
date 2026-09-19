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

## Reference load and competing confirmations

Three additional acceptance scenarios pass in 82.30 seconds, sharing setup
within each complete scenario rather than repeatedly bootstrapping every rejected
field. Direct restricted SQL rejects missing lock order, wrong actor/session,
old authentication, altered request/campaign/runtime/impact versions, mismatched
preparation/generation, expiry, malformed counts and wrong target state. An
observed real lock wait rechecks a committed eligibility change. A competing
in-flight confirmation receives a retryable conflict; after the winner commits,
the identical browser intent resolves to exactly one receipt.

The reference test loads 5,000 real normalized Families through the provider-fake
pipeline and stages 20 overdue schedule definitions through ordinary setup.
Its exact preview predicts 5,000 messages and 95,000 coalesced semantic slots.
Final confirmation takes 0.1605 seconds / 136 SQL statements, with no Family or
occurrence enumeration and no materialized occurrence/outbox rows. A maintained
restricted worker then commits bounded catch-up groups. Between groups, actual
Family login, baseline and submission succeed on an independent restricted web
connection while catch-up remains unfinished; baseline plus Submit takes 0.2479
seconds. No full 100,000-slot drain is needed to prove this admission property.
The test retains separate durable progress and does not manufacture completion.

The first complete candidate is ready for the required three-round peer-review
cycle. ADM-05.03 remains unchecked until that cycle and final validation pass;
withdrawal and its repeated-go-live/start-race acceptance remain ADM-05.04/.05.

## Peer-review ledger

### Round 1

Pika session `20260919-002028-1559ad` reviews immutable `880507fc..0cba0b7`.
Two manifest Claude shards and Pika's Codex reviewer complete without degradation,
failed agents, mismatch or salvage. Raw findings: one High, three Medium and
12 Low. Four findings clear the configured cutoff. This is not a finding-free
review; the dispositions below preserve the original severities.

- Claude Medium, nested-SQL measurement: fixed. The reference test now samples
  PostgreSQL transaction-local relation counters inside the actual final owner,
  including its private trigger effects; it also matches client SQL table names
  independent of quoting or `FROM`/`JOIN` position. All three watched relations
  report zero tuples read, inserted, updated or deleted. Instrumented confirmation
  takes 0.1635 seconds / 138 statements, and Family baseline plus Submit takes
  0.2603 seconds with incomplete maintained catch-up.
- Claude Medium, global impact contention: fixed for live traffic. No tick or
  singleton write occurs in Production, when new final confirmation is impossible.
  Returning to Testing changes the independently bound runtime version; revisions
  are never reset. Relevant campaign writers and lifecycle transitions already
  share the work order. The reference test proves both real live submission and
  catch-up effects leave the impact revision unchanged.
- Claude Medium, expired committed replay: fixed. Signature integrity, current
  authority, exact intent and current campaign still gate replay; the five-minute
  limit applies only when creating a new confirmation. Both HTTP outcomes test
  an expired completed replay; an expired uncommitted intent remains rejected.
- Codex High, direct SQL versus browser authority: rejected as an additional
  trust-boundary requirement, not represented as fixed. The assertion correctly
  observes that SQL does not verify Django signatures, typed browser input or
  external DNS. The existing architecture places these obligations in trusted
  compiled web owners, not in an independent cryptographic database attestation
  service. The internal database login identifies that service, not an end user;
  ordinary browser endpoints never expose arbitrary command-table inserts.
  See architecture's Identity and session security and operations' internal
  PostgreSQL network, alongside the existing `go_live_commands.start_cleanup`,
  `stewardship_go_live_admin_v1` and `sessions.issue_admin` boundaries. This PR
  retains browser signature/CSRF/current-role/origin/readiness checks and adds
  independently enforced SQL session/scope/version/generation invariants. Those
  are tested separately; they are not claimed to resist a fully compromised web
  SQL credential. Code comments now state this division explicitly. Replacing
  the service-trust model with a new attestation process is not silently added
  as a prerequisite of this increment; correction review must revisit this
  disposition against the controlling architecture.

Low dispositions: fixed the exact typed-value browser pattern, unavailable
historical progress links, named/commented schedule bound, lifetime test naming
and missing docstring, scoped settings override, and outdated SQL header. The
duplicate expired-replay observation is covered above. Retain the specified
timing acceptance rather than removing its bound. The SQL/Python catch-up
protocol continues to have actual restricted-worker handoff, binding and retry
tests; consolidating those internal allocation implementations is deferred.
The two all-trigger mutation-matrix suggestions are one deferred Low test
expansion: strict catalog fingerprints pin every current trigger, while actual
Testing eligibility/rollback and Production submission/catch-up behavior are
covered. Broad internal-helper renaming is deferred. A schema seed is not added:
the first actual relevant write establishes revision evidence, and a missing
clock deliberately fails closed rather than inventing evidence.

All 26 affected PostgreSQL/schema cases pass in 138.59 seconds; 21 browser/time
cases pass in 14.34 seconds. The independent `t` before/after audit matches
`0cba0b7` and changes only `stewardship_activation_impact_tick_v1()`; counts,
owners, ACLs and all other catalog objects remain identical. Both extra HTTP
settings-link/replay cases pass in 41.47 seconds after moving the active settings
visit outside the passive-page idle measurement. Candidate `0cba0b7` passes full
CI run `35421010733`; correction-head CI and rounds 2 and 3 remain required.

### Round 2

Pika session `20260919-004343-af8de4` reviews `0cba0b7..ee8da05`. Both sources
complete without degradation, failed agents, mismatch or salvage. Raw findings
are three Medium and seven Low, with no High/Critical. Two Medium findings are
validated; Claude's matching outcome-report finding is filtered because its
cited file was outside the correction diff, not because the defect is absent.
It is included in the correction below. Claude independently accepts the
recorded web-service trust-boundary rationale; neither source renews that High.

- Both sources, outcome comparison: fixed with immutable count-only observations
  in completed Family/digest checkpoints. The progress page compares the bound
  preview with prepared message candidates and coalesced semantic slots, marks
  partial results, and does not call preparation rendering or delivery. Current
  eligibility/recipients are rechecked by existing owners. Configuration restarts
  select their own completed groups; completed demands retain their completion
  configuration even after later edits. Forwarded semantic coverage is counted
  once. Scheduled activation has zero immediately-due work and no catch-up;
  later boundary/scheduler work remains ordinary mail, not this activation's
  immediately-due comparison.
- Codex Medium, index-only instrumentation: fixed by disabling index-only scans
  locally in the measured transaction. A deliberately nested 5,000-Family scan
  proves the harness rejects enumeration. The measured actual confirmation
  passes the constant-read/no-write bounds. Deferred constraints run before the
  final sample. See PostgreSQL's [statistics counter semantics](https://www.postgresql.org/docs/18/monitoring-stats.html).
  Query/time measurements include the explicitly disclosed instrumentation.
- Low fixes: remove the redundant age-free signature parse; allow committed
  exact replay after an origin edit while retaining origin checks for new
  intent; prove the expiry clock patch actually expires the token. Deferred
  trigger measurement is included in the instrumentation correction above.
- Low deferrals/rejections: retain brief locked authority checks for expired
  Admin replay rather than introducing another preflight race; browser typing
  is deliberately stricter about surrounding whitespace than the tolerant
  server. The low-confidence hypothetical writer without work order is not a
  demonstrated current defect; current lifecycle/submission/preparation owners
  serialize their effects. No permission or admission guard is weakened.

The first 20 affected confirmation/load/catch-up cases pass in 107.69 seconds;
the strengthened 19 HTTP/catch-up cases pass in 62.53 seconds. Nineteen strict
schema/confirmation-guard cases pass in 49.59 seconds, and nine three-engine
browser cases pass in 12.44 seconds. Ruff and model-state drift checks pass.
Independent fresh databases with suffix `u` verify immutable `ee8da05` and add
only the checkpoint count column and its non-null constraint, changing only
`stewardship_checkpoint_guard_v1()`. Owners, ACLs and policies are unchanged.
Totals: 187 relations, 2,151 columns, 3,073 constraints, 920 indexes, 529 functions,
516 triggers and 28 policies. No retained database is changed or deleted.
Full CI run `35422067466` passes for `ee8da05`; this correction needs its own
final-head CI and the third dual-source review before delivery.
