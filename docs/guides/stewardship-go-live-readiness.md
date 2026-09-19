# Stewardship go-live readiness and cleanup

Continue [ADM-05](../tasks/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
from [PR #57's protected delivery](stewardship-due-work-health.md#protected-delivery)
on `pr/stewardship-go-live-readiness`, based on verified main `17f5f2fc`.
Follow the [controlling Phase 4 sequence](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
and [Production-transition contract](../specs/stewardship/admin-portal/spec.md#production-transition).

## Coherent outcome

Give an authenticated Admin a current, exact readiness/impact preview and a
guarded path into the existing bounded Testing-cleanup worker, with durable
status, safe retry and cancellation. Reuse configuration, source, test-delivery,
coalescing, cleanup inventory and request owners; a signed preview alone is
never authorization or proof of current readiness. Passive status must not
renew an abandoned login. Staff and Ministry leaders have no access.

Keep the irreversible cleanup acknowledgement explicit, invalidate rehearsal
access with gate acquisition, and preserve completed cleanup on cancellation.
No endpoint may nominate arbitrary deletion targets, inventory counts, actor
identity or privileged callbacks. No GET sends a message or starts cleanup.

This increment stops before Production activation/withdrawal. Those later
ADM-05 commands must recheck current readiness under short final locks and
exercise the required direct-activation load/catch-up handoff. Keep their SQL
and application guards closed until that owner is complete. This is a
reviewability split, not removal of ADM-05.03/.04/.05 or Gate 3 acceptance.

## Checkpoints

1. Current readiness, exact impact/inventory and stale-input binding, including
   explicit blockers and existing remediation links.
2. Admin preview/acknowledgement, real cleanup request/gate integration, progress,
   retry/cancel and response-time authorization checks.
3. Actual-role, stale input, boundary, interrupted cleanup and browser acceptance
   tests; independent fresh-schema audit if the schema changes.
4. Three completed dual-source review/fix rounds, final-head CI/DCO and protected
   merge before the next fresh-main activation/withdrawal increment.

Implementation is in progress; no ADM-05 checkbox is complete yet. Tests use
synthetic providers and disposable databases. No real provider call, retained
database deletion, deployment, release or historical upgrade is authorized.

## Implementation checkpoints

The read-only Family impact collector uses the execution coalescing planner,
streams complete groups in 200-Family pages, and separately counts immediately
due messages, coalesced slots, skipped slots and blocked Families. Its digest
binds eligibility, live responses, source generations, current schedule revisions
and Production coverage/attempts. It never allocates delivery work or reads
Family codes. Testing fulfillment does not count as live coverage.

Full-source readiness follows the current snapshot's permanent full-load anchor,
verifies tenant and selected campaign/fund window, and uses the configured
source-staleness threshold (default 30 minutes) from the full load's start.
A newer delta does not extend that deadline. Missing or stale evidence requires
a new full refresh before cleanup can begin. This is distinct from routine
nightly full-refresh scheduling.

Focused validation so far: 20 pure impact cases (including a 5,000-Family stream),
three PostgreSQL Family-reader cases (including page boundaries and actual web
role), and four PostgreSQL source-readiness cases (full/delta anchors, exact
expiry, changed window and unavailable/future evidence) pass.

The next checkpoint adds the passive Admin readiness/inventory screen and
bounded affected-Family listing. Explicit URL/DNS verification uses a fixed
five-second resolver helper outside database transactions; GET never invokes
it. Resolution checks do not replace external TLS/deployment smoke tests. An
accepted configuration- and credential-bound Family test remains separate from
provider installation acknowledgements. Missing evidence remains a blocker.

Additional focused validation passes: six configuration-reference cases, seven
origin-adapter cases, two restricted-role cleanup-inventory cases and seven
Admin HTTP cases. The latter cover access denial, passive activity, exact close,
response-time revocation, explicit DNS verification, closed input fields, and
rejection of a signed intent when current readiness is incomplete. Cleanup
mutation/progress endpoints, their SQL authority, full happy-path/race/browser
acceptance and peer review remain in progress; no destructive control is exposed
by these checkpoints.

[Draft PR #58](https://github.com/epiphany40223/parishkit/pull/58) runs full CI
alongside implementation. Its first checkpoint `9ff513d` passed all 25 reported
CI/DCO checks ([run 35406113971](https://github.com/epiphany40223/parishkit/actions/runs/35406113971)).
These are internal checkpoints, not completed workflow, review-gate or PR
acceptance. No review round has been counted for this PR yet.

The guarded cleanup checkpoint now exposes explicit irreversible acknowledgement,
durable progress, cancellation and failed-task retry. Every command rechecks the
current Admin and Testing draft; passive status does not renew login activity.
The web role can insert only guarded intent/inventory and update control columns,
not delete target data or manufacture worker checkpoints. Deferred database
guards require a sealed manifest for the new request and its gate/epoch changes.
Worker cancellation retains the original Admin intent across a safe batch boundary.

Focused validation passes: 30 PostgreSQL cases across cleanup admission/control,
readiness HTTP and the strict fresh-schema suite in 109.24 seconds; 34 pure
configuration, impact and origin cases in 0.19 seconds. Cleanup integration starts
with the real setup/final source load, installed consumer acknowledgements and
accepted synthetic-provider test mail, then creates disposable rehearsal detail.
It covers idempotent admission, unjournaled gate/direct-delete denial, queued and
completed cancellation, actual HTTP acknowledgement/CSRF/passive status, and
exhausted-worker retry through completion. Completed deletion is not restored.
No provider receives a real message. Full branch lint and format checks pass.

The preceding pushed checkpoint `2a48a0f` also passed all 25 CI/DCO checks
([run 35407390223](https://github.com/epiphany40223/parishkit/actions/runs/35407390223)).
Remaining acceptance includes complete Admin-digest impact, stale-input/race and
browser coverage, and the three peer-review rounds; this is not a ready-to-merge
claim or completion of ADM-05.

## Fresh-install schema audit

Independently installed immutable `2a48a0f` and the cleanup-authority candidate
into separate new databases `stewardship_mail_health_before_20260918j` and
`stewardship_mail_health_after_20260918j`; both are retained. The predecessor
matched its checked-in fingerprint before comparison. No table, column, index,
policy, existing constraint or trigger changed or disappeared.

The precise delta adds three private trigger functions (current Admin scope,
manifest commitment, and gate/epoch binding), five triggers including three
deferred constraints, and modifies three existing functions. The runtime-command
guard admits only web cancel/retry commands alongside the existing worker path;
target and manifest verification become trigger-only security-definer functions
so web needs neither private helper execution nor private payload reads. Catalog
inspection verifies pinned search paths and schema-owner-only execution ACLs.
Counts are 183 relations, 2,107 columns, 2,994 constraints, 901 indexes,
519 functions, 495 triggers and 28 policies. Only the three affected fingerprint
categories were updated after this comparison; strict baseline tests pass.
