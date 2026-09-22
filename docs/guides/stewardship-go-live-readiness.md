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

This increment stopped before Production activation/withdrawal, which later
pull requests (#60 and #61) delivered; the operator procedure is the launch runbooks'
[Production activation](stewardship-launch-runbooks.md#production-activation).
As written at the time: those later
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

ADM-05.01/.02 now pass implementation and local review acceptance; final-head
CI/DCO and protected PR delivery remain required. Tests use synthetic providers
and disposable databases. No real provider call, retained database deletion,
deployment, release or historical upgrade is authorized.

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
That checkpoint did not complete acceptance. The following checkpoint adds the
remaining Admin-digest impact, stale-input/concurrency and browser coverage;
independent reviews and final-head CI remain required before merge.

Admin impact now counts complete due date ranges with the same recovery-count
planner used by execution, separately reporting coalesced slots. Weekly impact
shares the report selector's item predicate and reads only nonprivate history:
Testing/manual intervals do not advance live automatic coverage, and actual
per-Admin provider acceptance suppresses only previously delivered items. No
message body, request text or source name is read for these counts. Exact due
boundaries change the preview binding even when recipient-message counts match.
The preview links directly to selected Family test-mail screens.

Four additional restricted-role digest cases pass, including a 123-day backlog,
empty weekly intervals and actual per-Admin acceptance. Three new cleanup cases
pass for expired/other-actor/revoked/changed-configuration/closed confirmations,
simultaneous idempotent confirmation on independent web connections, and
running-worker cancellation that retains the gate until a safe worker boundary.
Nine browser cases pass across Chromium, Firefox and WebKit, covering mobile and
desktop accessibility, native irreversible acknowledgement and no-JavaScript
controls. The shared weekly-selection/configuration pure set passes 83 cases.

CI on `7bc3f9a` found the new SQL asset missing from Docker's explicit build
allowlists. Correction `000679d` adds it to both contexts; all 41 focused build
tests pass. Full container validation is rerunning on the pushed correction.
No failed checkpoint is counted as PR approval.

## Review and correction evidence

Round 1 reviewed complete base `17f5f2fc` through `427308ee` (tree
`af7a23b42d9168c2189d23307f3bc1456df175ec`), Pika session
`20260918-202859-5beb9f`. Both Claude shards and Codex completed without
degradation. Raw severities: six Medium and 17 Low; five Medium findings passed
validation, with no High or Critical finding. The correction round will include
this disposition and surrounding owners, not only changed-line excerpts.

- Accept cancellation usability: a signed stop intent names the immutable cleanup
  request, not a rapidly changing checkpoint. Under the existing common lock,
  first cancellation uses the current request version; an exact replay retains
  the first durable cancellation's version. Retry remains version-bound. Added
  an actual-worker committed-batch regression and historical-gate reuse probes.
- Accept restore uncertainty: retain every hold state for a semantic Family slot
  rather than overwriting repeated-restore evidence. Unreviewed holds are visible
  as blocked Families, not sendable reminders; assumed delivery does not erase a
  different unresolved restore. Added actual web-reader coverage and resolution.
- Accept permission-test drift: the exact-runtime test now checks the web's
  guarded aggregate/request INSERT and exact request-control UPDATE columns,
  retaining worker-only checkpoints, claims and deletion denials. CI also found
  an older gate test that wrote `false` to an already-false gate; it now tests an
  actual held gate and rejects release without sealed cancellation intent.
- Reject the proposed historical-cancellation exploit for the currently exposed
  owner: a web acquisition must match a sealed request's exact new gate version;
  the credentials mutable guard advances that version on every update. A prior
  cancelled request therefore cannot reacquire its gate, and a newer nonterminal
  request independently blocks release. Cancellation atomically releases its
  gate. Actual-role regressions cover both attempted historical reuse paths.
  Production activation/withdrawal and any additional gate owner remain closed
  until ADM-05.03/.04, where their atomic release paths must be reviewed; this
  is not approval of a hypothetical future owner leaving an orphaned gate.
- Reject replacing current cleanup admission's exact re-read with the suggested
  cheap tuple as a correctness fix: that tuple does not cover every delivery,
  restore-hold and Testing-inventory change. The cleanup contract requires exact
  inventory and gate acquisition together. The no-enumeration/short-final-lock
  requirement belongs to later Production activation and remains explicit there.
  Current readers paginate and batch metadata; no provider call is under locks.
- The sixth raw Medium (confidence 30, below Pika's cutoff) speculated about
  superseded uncertain schedule revisions. Existing
  `stewardship_schedule_definition_v1` rejects replacement while the current
  revision has blocking work; the current reader does not bypass that owner.
  No historical-upgrade or fabricated inconsistent-source support is added.

CI on `427308ee` completed with the two permission-expectation failures described
above; its aggregate PostgreSQL job consequently failed. All other jobs passed.
Post-fix validation passes: all ten cleanup cases in 144.87 seconds, plus 51
Family-readiness, exact runtime permission, catch-up allocation and pure impact
cases in 25.83 seconds. Ruff, formatting, changed-Markdown lint and diff checks
pass. Round 1 is complete; final-head CI and two more completed rounds remain.

Round 2 reviewed `427308ee` through `ad245b69` (tree
`a3505733099e6e3890139506b5e619536217142f`) in session
`20260918-205022-66bc64`, with both sources complete and no degradation. Raw
severities were two Medium and three Low; both Medium findings were accepted,
with no High or Critical finding. The broad round-1 restore hold override was
too conservative for exact impact: only an unreviewed initial invitation blocks
an otherwise selected reminder, matching dispatch. Reminder-only holds remain
excluded individual slots, and responded/ineligible/closed groups retain their
skip outcomes. Retained restore evidence now streams in 200-row batches rather
than caching all histories for a Family page. Regression coverage exercises
201 repeated restores, mixed assumed/unreviewed evidence, reminder-only holds,
and seven planner disposition combinations. Round-1 gate/performance dispositions
remain recorded; future activation/withdrawal authority is not inferred.
All 33 focused PostgreSQL/planner cases pass in 17.38 seconds, including the
streaming-history regression; Ruff, formatting and changed-Markdown checks pass.
Round 2 is complete. A third completed round and final-head CI remain required.

Round 3 reviewed `ad245b69` through `4bcb3e9b` (tree
`8448e25b6e33383d22e8e40d43383033e4d45ab3`) in session
`20260918-205705-3e3070`. Both vendors completed without degradation; Codex
approved with no findings, while Claude reported two Medium and two Low issues.
No High or Critical finding was reported. Reject the claimed mixed-evidence
behavior mismatch: `family_schedule_planning.plan_family` can prepare a reminder,
but `family_mail_dispatch.disposition` independently rejects it whenever its
initial slot has an unreviewed hold. A selected occurrence is not provider
permission. Accept the companion request for stronger integration evidence:
new cases exercise the real readiness reader, real planner and actual restricted
provider-dispatch guard over identical mixed evidence, once with assumed initial
delivery and once with actual synthetic-provider accepted fulfillment. Both
prove zero sendable messages while held and one after the final hold is reviewed.
Only the final guard's metadata projection is synthetic; its persisted scope,
population, occurrence, hold and fulfillment queries are not mocked.

Post-round validation passes all 35 focused PostgreSQL/planner cases in 22.54
seconds, along with full Ruff/format checks. No production behavior needed a
round-3 change. All three required rounds are complete, every accepted Medium
finding has a passing correction/regression, and the final round has no High or
Critical finding. The earlier nine three-engine browser checks and independent
fresh-schema audit remain applicable. Exact-head CI/DCO still gates delivery.

### Final CI fixture correction

CI on consolidated head `1a9e5154` exposed a race in an existing report browser
test: the component server omitted `/admin/presence?format=count`, which Admin
chrome requests immediately. Waiting for that response reproduces the 404 on
all three engines, independently of CI timing. The fixture now returns the
synthetic count, and the regression waits for the response and rendered count
before checking console errors. Error diagnostics retain the failing URL;
unknown routes still fail, and the existing service-failure test still overrides
the endpoint explicitly. No application behavior, schema or permissions changed.
All 15 focused report/presence browser cases pass in 15.27 seconds across the
three engines, with Ruff and formatting passing. Exact corrected-head CI remains
required before delivery.

## Acceptance and delivery boundary

- ADM-05.01: complete current configuration/source/integration/test-mail checks,
  exact Family/Admin impact and cleanup inventory, expiring input-bound preview,
  explicit public-origin verification, current Admin authorization and bounded
  affected-Family listing.
- ADM-05.02: guarded durable request/gate/manifest/task admission, rehearsal
  invalidation, irreversible acknowledgement, passive progress, safe cancellation
  and failed-worker retry, with exact-role SQL and concurrent-request evidence.
- ADM-05.03/.04/.05: remain open for final activation, withdrawal, full boundary
  race coverage and the 5,000-Family short-final-lock/catch-up handoff measurement.
  The delivery-pause slice and integrated Phase 4/Gate 3 work also remain open.

Before protected delivery, consolidate the build correction into cleanup and the
review corrections into the final interaction/acceptance checkpoint, preserving
the resulting tree exactly. Record the final commit/tree and CI receipt in the
PR handoff; the reviewed endpoints above retain pre-consolidation provenance.
After PR #58 lands on refreshed `origin/main`, start the next coherent
activation/withdrawal increment there under the standing merge/continue authority.

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

## Protected delivery

PR #58 merged through protected auto-merge on September 19, 2026 at 01:32:41 UTC
as `47ec359982151ef1cc28f24f391c2f6d97faef8b`, verified on refreshed `origin/main`.
Corrected head `765ea213ea2f5399b8f73afa835042cfcaea9c13` passed all 24 jobs in
[CI run 35412153979](https://github.com/epiphany40223/parishkit/actions/runs/35412153979)
and DCO. All three completed dual-source review rounds and the narrow browser
fixture correction are recorded above. This supersedes prior pending-delivery
notes without claiming activation, withdrawal or Gate 3 acceptance.

The [activation increment](stewardship-production-activation.md) starts from
that verified merge on `pr/stewardship-production-activation`.
