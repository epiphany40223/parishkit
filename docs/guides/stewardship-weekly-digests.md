# Weekly Administrator information digests

## Scope and prerequisites

This coherent Phase 4 increment starts on `pr/stewardship-weekly-digests` from
PR #47's verified merge `e548809c`. It owns
[BG-07.03](../tasks/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests)
and the weekly-specific portion of BG-07.05, following the
[controlling work package](../plans/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests).
The authoritative behavior comes from the
[weekly digest contract](../specs/stewardship/background-processing/spec.md#weekly-additional-information-digest),
[follow-up record model](../specs/stewardship/data/spec.md#follow-up-records),
and [additional-information report](../specs/stewardship/reports/spec.md#additional-information).

The complete Phase 5 follow-up workflow and Phase 6 post-close obligation
inventory/explicit resolution remain with their existing owners. This increment
must preserve the current fail-closed archive behavior and does not release
Gate 3, enable production use, deploy, release, or send real provider mail.

## Internal acceptance checkpoints

1. Compile safe, bounded new-information and correction sections, using only
   live actionable items at generation. Include Family identity, submission
   time and protected detail links; do not repeat withdrawn text. Add a closed
   no-attachment mail payload without weakening the daily chart or Family mail
   contracts.
2. Retain coherent generation inputs and exact item/disposition coverage under
   fenced ownership. Retry, partial acceptance, schedule replacement and
   recipient changes must not fabricate success, lose corrections, or resend
   historical work as new. Record successful empty intervals explicitly.
3. Integrate the existing weekly schedules, separately addressed immutable
   Admin cohort, current-role rechecks, Testing override/cleanup, delivery
   pause/restore gates, truthful provider uncertainty and explicit recovery.
4. Provide protected report/detail links and the required explicitly labeled,
   audited manual report occurrence. Keep this scoped read surface distinct
   from the later full Staff follow-up queue and editing workflow.
5. Test changed/unchanged/cleared/replaced text, correction history, empty
   intervals, partial recipients, crashes, concurrent submission versus
   generation, actual-role SQL boundaries and responsive accessibility. Finish
   at least three successful dual-source review/fix rounds, full validation,
   exact-head CI/DCO and protected delivery before advancing.

## Validation resource discipline

Use focused tests through implementation/review corrections and complete final
coverage against stable corrected source. Locally, use at most two concurrent
disposable PostgreSQL slots, fresh per-partition storage, and stop finished
owned synthetic services; retained development and schema-audit databases must
not be stopped or reset for capacity. Preserve failed evidence separately from
successful same-source coverage receipts. Normal CI continues to require every
partition and both coverage floors; do not trade correctness for elapsed time.

## Execution evidence

Implementation is in progress. The checkpoints are acceptance targets, not
completion claims. BG-07.03 and the complete BG-07.05 matrix remain unchecked.

### Rendering and isolated transport checkpoint

The detached compiler preserves every selected item, explicit campaign-local
timestamps, Family names/DUIDs, protected report/detail links, and distinct
superseded/withdrawn corrections without former text. It visibly shortens only
email excerpts; a 5,000-Family reference test verifies that no selected identity
is dropped. Empty input cannot create an email. Manual reports are visibly
labeled, and all private row/content types omit their fields from `repr`.

The weekly helper accepts a distinct closed no-attachment payload and one Admin
recipient. It shares maintained SMTP acceptance/uncertainty and private-pipe
ownership without weakening the daily chart or Family payloads. Testing mail
keeps the intended Admin visible while routing only to its configured test
address. The stateless helper imports neither ORM models nor report compilers.
No scheduler or durable capture authority is registered by this checkpoint.

Validation: 264 initial report/routing/transport checks and 362 expanded shared
Family/readiness/weekly transport checks passed. The full credential-free
baseline passed 6,467 tests with 4,438 expected profile skips and two existing
client-library deprecation warnings in 59.48 seconds. Ruff check/format pass.
Durable inputs, fulfillment, schedule integration, protected routes, review
rounds and final full validation remain outstanding.

### Coherent input selection checkpoint

The internal capture service reads live item dispositions, current source names
and the live submission watermark in one PostgreSQL statement. Rehearsal answers
are excluded; source inactivation or removal does not remove a submitted request.
A missing current source Family is identified by its retained DUID with the
generic label `Family`. Correction inputs omit historical text in SQL, before
decoding. The detached selection uses submission sequences to distinguish new
items even when two submissions have the same timestamp.

Successful interval advancement is separate from accepted-message history.
Partial acceptance does not advance the interval, and only previously reported
items can produce corrections. Resolved corrections do not repeat after empty
intervals. These are internal calculation inputs, not browser-provided authority;
the durable owner must still bind history to its campaign/mode/epoch, retain the
observation, and prove per-recipient coverage before delivery is enabled.

The general worker gains only the required item/submission read columns, not
follow-up mutations or full submitted answers. Actual-role tests deny raw text
to the scheduler and mail-dispatch worker. A concurrent real submission between
query execution and result consumption proves that capture does not mix the old
watermark with a new disposition.

Validation: 160 focused selection/compiler/transport checks passed in 0.94
seconds; 18 new PostgreSQL checks passed in 33.26 seconds; the expanded 66-test
PostgreSQL capture/worker/dispatch grant run passed in 45.59 seconds. The complete
credential-free baseline passed 6,535 tests with 4,456 expected profile skips and
the same two client-library warnings in 64.42 seconds. No schema, scheduled
execution, persistent weekly history or send authority is added by this checkpoint.

### Durable capture and schedule-coverage checkpoint

The fenced preparation owner now discovers weekly slots in bounded pages and
coalesces overdue dates into the latest occurrence without losing original slot
coverage. Current failed/unfinished preparations hold newer intervals; completing
preparation is not evidence that its Admin cohort has resolved. Configuration
replacement lineage remains append-only. The producer and handler are not yet
registered in the assembled scheduler/worker runtime.

Capture atomically retains its self-contained source/item observation, selected
information/correction identities, successful-interval boundary, generation-time
Admin cohort and exact task fence. SQL independently recalculates the selection
and rejects invented names/text, omitted requests, stale fences, premature phase
jumps and a snapshot committed without its fanout transition. Replayed capture
uses the retained copy. An empty snapshot still leaves its occurrence pending:
the later completion owner must establish successful empty fulfillment.

The recipient table reserves immutable per-Admin content and accepted-coverage
bindings, but recipient insertion, outbox allocation, delivery, Testing cleanup,
completion, manual reports, protected routes and runtime registration remain
unfinished. No weekly message can be sent by this checkpoint. The existing
general worker gains report-input ownership, not provider authority.

#### Fresh-install schema audit

Independent empty PostgreSQL installations compared pinned commit `5cf0a26`
with this checkpoint. Three tables add 52 columns, 68 constraints, 18 indexes,
14 functions and five triggers. No existing relation, column, constraint, index,
trigger, policy, owner or ACL changed or disappeared. The two existing function
body changes admit only the fenced weekly schedule/replacement owner. Their
installed definitions were compared directly before updating the strict catalog
fingerprint. A second fresh installation verified the new phase constraint's
literal-cast representation against Django's model compiler; this changed no
preexisting object or constraint semantics. Both Docker build allowlists include
the two new SQL assets explicitly. No retained database was upgraded or reset.

Validation: 71 PostgreSQL capture/storage checks passed in 53.75 seconds; 16
expanded weekly capture/unfinished-interval checks passed in 32.56 seconds;
61 fresh-schema and daily/weekly regression checks passed in 78.01 seconds.
The focused compiler/transport/grant run passed 190 tests; the build/selection/
grant correction run passed 139. The corrected complete baseline passed 6,536
tests with 4,472 expected profile skips and the same two warnings in 62.06
seconds. Ruff check/format and Django model-state drift checks pass. These are
checkpoint results, not the increment's final coverage or review acceptance.

### Weekly journal capacity checkpoint

Weekly envelopes now retain their compiler's separately bounded 8 MiB HTML/text
bodies through the actual delivery journal. Python rejects that larger render
type for any other purpose, and PostgreSQL independently selects the limit from
the owning message. Daily, Family and operational journal limits remain 1 MiB.
Private render values are excluded from diagnostic representations.

Validation: 183 focused renderer, envelope and transport tests passed in 1.17
seconds, including a 5,000-Family report in Testing and Production. All 75
PostgreSQL journal/boundary tests passed in 61.68 seconds, including direct SQL
attempts to exceed or misapply the limit. Independent fresh installations of
`efa058b` and this checkpoint differed only in the expected render-shape function
body; its owner/ACL and every other catalog object were unchanged. No existing
database was upgraded or reset. Weekly fanout and provider authority remain
unfinished and disabled.

### Per-Admin allocation and replacement coverage checkpoint

The worker now loads bounded recipient pages, compiles private content outside
database transactions, and rechecks the complete page under its live claim
before committing. Each recipient's selected item/disposition IDs, accepted
coverage references, compiled bytes and outbox intent commit together. An
interrupted or omitted binding rolls back the whole page. Completed pages are
immutable and skipped on resume.

Only actual accepted messages for the same Admin and campaign/mode/epoch cover
prior items. Complete coverage allocates no new message. Partial coverage removes
only the already accepted item/disposition pairs, preserving new requests and
new corrections. SQL independently recalculates the proof instead of trusting
the Python reader. Empty snapshots allocate neither recipients nor messages;
successful interval fulfillment still requires the separate completion owner.

Schedule replacement now counts and fences weekly child messages and Task roots.
It cancels only proven-unsent children, preserves accepted history, and blocks on
submitting or uncertain outcomes. Private projections expose counts/versions to
the existing configuration workflow, not request text or Admin mail bodies.
Provider dispatch, completion, cleanup, protected/manual UI and runtime assembly
remain unfinished; no weekly provider authority is enabled by this checkpoint.

Validation: the initial 29 capture/allocation PostgreSQL checks passed in 75.35
seconds. The expanded 63 weekly coverage/allocation and existing daily/schedule
regressions passed in 129.69 seconds. The complete credential-free baseline passed
6,547 tests with 4,498 expected profile skips and the same two client-library
warnings in 62.21 seconds. Independent fresh installations of `2095bab` and this
checkpoint identify two new private views, four functions and two triggers;
only the five intended allocation/reconciliation function bodies and the common
schedule-work view changed. Existing owners/ACLs and table/model structure remain
unchanged. No retained database was upgraded or reset. Final coverage and review
acceptance remain outstanding.

The final 60-test PostgreSQL coverage/schema/runtime-grant run passed in 46.77
seconds, including a forged Python coverage result rejected independently by
SQL. Ruff check/format, Markdown checks and Django model-state drift checks pass.

### Interval completion and maintained preparation checkpoint

Weekly completion now derives the outcome from the entire immutable generation
cohort. The final accepted child commits the normal running/succeeded occurrence
history and delivered fulfillment together. Partial acceptance retains reported
item history without advancing the successful interval. A coherent empty capture
records explicit empty fulfillment without allocating or claiming provider mail.
Later corrections remain eligible after intervening empty weeks.

Late acceptance of a previously uncertain attempt commits truthfully even after
the provider task fails. A distinct metadata-only finalizer obtains a fresh claim
and records completion exactly once; it does not rewrite the failed provider
task. Daily and weekly finalizers share the recovery mechanics but retain closed,
separate task/model/SQL identities. The common fulfillment and occurrence guards
branch before weekly metadata reads, preserving unrelated mail-role permissions.

The provider-free weekly handler maintains its lease through bounded discovery,
capture, detached rendering and atomic page retention. It reloads a changed
coverage page without duplicating mail, exposes truthful coarse progress, and
retains bounded recovery/failure behavior. The scheduler may admit queued metadata
but cannot execute private compilation. These handlers are tested internally;
assembled runtime registration, weekly provider dispatch/reconciliation, Testing
cleanup, protected/manual views and required reviews remain open.

Validation: 24 initial completion, late-reconciliation and allocation regressions
passed in 82.47 seconds; the expanded 50-test existing Family/daily mail regression
passed in 138.55 seconds. The current worker/completion/fresh-schema suite passed
26 tests in 42.77 seconds, and 37 focused task/recovery cases passed in 0.21
seconds. Independent fresh installations against `e2f9bfb` add only one private
completion view, three functions and three constraint triggers; only the two
intended shared guard function bodies changed. Installed definitions and unchanged
owners/ACLs were compared before accepting the strict schema fingerprint. No
retained database was upgraded or reset. Final increment acceptance remains open.

The final current-source 44-test Family/daily/weekly regression passed in 94.22
seconds. After explicitly registering the new SQL-only completion view in the
closed permission-catalog test, 66 focused task/grant checks passed and the
complete baseline passed 6,578 tests with 4,507 expected profile skips and the
same two warnings in 61.13 seconds. Ruff check/format, Markdown checks and Django
model-state drift checks pass.

### Testing retention cleanup checkpoint

The independent Python/SQL Testing inventories include weekly snapshots and
per-Admin recipient records. Bounded deletion waits for recipient dependents,
uses the existing private checkpoint effect, and preserves opaque preparation
history. Raw deletion remains forbidden even to the schema owner outside this
workflow. Production reports never enter the Testing inventory; activation
independently rejects remaining Testing snapshots. Web receives only the extra
campaign metadata needed to select its weekly inventory, not private mail bytes.

The 58-test weekly/daily cleanup, bounded batch and storage suite passed in 62.42
seconds. A denied all-inventory Web read in the first new test was corrected to
exercise only authorized weekly metadata; no unrelated permissions were added.
Independent fresh installs against `6de2bc0` change only the cleanup category
constraint, five intended function bodies and weekly immutable-trigger arguments,
plus two cleanup-protection triggers. The weekly immutable function also loses
unnecessary PUBLIC execution permission. Existing owners, all other ACLs, columns,
indexes, policies and relation metadata remain unchanged. No retained database
was upgraded, reset or deleted. Final increment acceptance remains outstanding.

The final 46-test fresh-schema/inventory/capture run passed in 54.04 seconds;
135 focused cleanup, Production-state and permission tests passed in 0.35 seconds.
Ruff check/format, Markdown checks and model-state drift checks pass.

### Protected report and detail checkpoint

Email links now resolve to current-Admin-only report and item pages. The pages
use the existing campaign response-lifetime barrier, recheck authority before
opening private content, and retain started/completed-or-failed access evidence
without logging text or recipients. Anonymous, Staff-only and Ministry-only
accounts cannot read these Admin mail reports. The later Staff follow-up queue
and its editing/export workflow remain Phase 5 work.

Overview pages contain at most 50 selected items; detail links must select an
item actually included in that snapshot, not merely one present in its broader
source observation. Captured names/text remain stable while current request
disposition is read explicitly. Superseded historical text is visibly labeled;
correction records never retrieve or repeat the former text. Full captured text
is escaped, overview excerpts are bounded, and browser-local timestamp rendering
uses the shared responsive interface. Reads never send email or alter follow-up.

Web now has column-scoped retained report input reads for this protected surface;
it still cannot read the generation-time recipient cohort or compiled per-Admin
mail bytes. Scheduler and MAIL retain their previous narrower permissions.

Validation: 19 bounded-selection/parameter checks passed in 0.38 seconds. The
initial 16 report/recovery database checks passed in 62.42 seconds after fixing
the new anonymous-principal denial. The expanded 38-case weekly/daily protected
view, response-guard and dispatch run passed in 102.60 seconds. All nine real
Chromium/Firefox/WebKit phone/desktop accessibility and no-JavaScript scenarios
passed in 12.55 seconds. The complete credential-free baseline passed 6,597 tests
with 4,566 expected profile skips and the same two client-library warnings in
66.56 seconds. No database schema changed in this checkpoint.

Manual report requests, runtime assembly, final validation and required review
rounds remain open. BG-07.03 and BG-07.05 are still incomplete.

### Provider dispatch and Admin recovery checkpoint

Weekly per-Admin messages now use the maintained MAIL worker and isolated weekly
transport. Dispatch rechecks the current Admin, scope, pause and restore holds;
SQL independently binds the current envelope and retained recipient subset.
Neither the scheduler nor MAIL may read raw additional-information observations
or complete submitted answers. Weekly mail never decrypts Family credentials or
propagates cancellation to another Admin's message. Abandoned submissions become
uncertain and cannot be automatically resent.

The existing Admin delivery journal now accepts weekly decisions. Explicit
external acceptance preserves failed provider history for metadata finalization;
resend requires duplicate-risk acknowledgement. Retry preparation stores only a
fixed, non-sendable seed. The restricted MAIL worker replaces that seed with the
original retained subset before opening another attempt. Web cannot supply a
report body or invoke the private seed directly. Preparation/finalization retry
controls use CSRF-protected opaque commands, current Admin authority and exact
latest-run identity without changing the report interval.

Validation: 31 maintained-worker and existing dispatch regressions passed in
89.38 seconds; 46 weekly/daily/Family recovery checks passed in 143.53 seconds;
14 weekly/daily retry-control checks passed in 36.86 seconds. The 62-test
dispatch/schema/grant/view run passed in 78.74 seconds. The credential-free
baseline passed 6,578 tests with 4,544 expected profile skips and the same two
client-library warnings in 67.67 seconds. Independent fresh installations against
`ad24a4e` add four weekly dispatch functions and change only five intended shared
dispatch/recovery functions; installed definitions, owners and ACLs were checked.
Tables, constraints, triggers, indexes, policies and existing relation metadata
are unchanged. No retained database was upgraded or reset.

Runtime registration remains disabled until Testing cleanup and protected/manual
report workflows are ready. Required review rounds and final acceptance remain
open; this checkpoint does not complete BG-07.03 or BG-07.05.

The final 16-case dispatch run passed in 48.11 seconds, including a paused
Production report resuming after campaign close with its original private
subset. Ruff check/format, Markdown checks and model-state drift checks pass.

### Manual reporting and runtime integration checkpoint

The Admin navigation now offers a CSRF-protected, configuration-bound manual
information report confirmation. It queues an actor-bound immutable command;
SQL independently checks current authority and gates and derives the occurrence
and worker task. Duplicate confirmation returns the original task. Web cannot
choose recipients, supply report bodies, or insert preparations directly.

Manual selection follows the
[manual-report contract](../specs/stewardship/background-processing/spec.md#weekly-additional-information-digest):
all current actionable live requests plus corrections, visibly labeled manual,
with independent audit history. Manual delivery neither advances the scheduled
watermark nor suppresses the next scheduled report. Both use the same isolated
transport, current-recipient recheck, reconciliation and cleanup protections.
Private Testing snapshots remain cleanup-owned; opaque manual command history
is retained and a replay cannot reopen it.

The runtime scheduler and worker register weekly preparation and metadata
finalization alongside the existing daily owners. Scheduler execution remains
metadata-only; provider work remains in MAIL. Initial validation: all 202 runtime
integration tests passed in 1.14 seconds; six manual PostgreSQL scenarios passed
in 25.30 seconds; 15 Chromium/Firefox/WebKit report/form accessibility scenarios
passed in 20.82 seconds. Further security, cleanup and regression checks are in
progress; required review rounds and final acceptance remain open.

The subsequent manual/security/storage run passed all 48 tests in 32.30 seconds,
including SQL rejection of non-Admin commands, stale form refusal, retained
opaque history after Testing cleanup, and regular delivery after a manual report.
Ruff check/format and model-state drift checks pass.

Independent fresh installs against `888f878` add only the manual-request table,
its seven columns, eleven constraints, five indexes, two triggers and three
functions; four existing weekly functions change for manual ownership/history
and coverage. Installed definitions and execution privileges were checked;
existing owners/ACLs, policies and all unrelated objects are unchanged. The
strict fresh-install fingerprint records this bounded change, with no upgrade
path or retained-database mutation.

The complete credential-free baseline passed 6,613 tests with 4,581 expected
profile skips and the same two client-library warnings in 66.67 seconds. The
weekly PostgreSQL suite plus schema and runtime grants passed all 176 checks in
310.53 seconds. Required dual-model review rounds remain pending.

### Review round one

Session `20260917-184316-8f5b5c` reviewed the complete weekly increment through
`85ba754` against merged `e548809c`. Five Claude shards and Codex completed
without degradation. Codex reported no findings; Claude reported 42 raw
findings, of which seven met the Medium-or-higher cutoff (two High, five Medium).
The remaining 35 were Low and are not outstanding Medium-or-higher acceptance
findings. Finalized disposition of the seven:

| Finding | Disposition |
| --- | --- |
| High: fractional timestamp encoding breaks exact SQL capture | Fixed. Retained UTC timestamps now use the SQL JSON fractional precision. A real six-submission test covering one through six fractional digits failed before the change and passes afterward. |
| High: imported NBSP/CR labels fail strict HTML validation | Fixed. Normalize display whitespace without changing retained source/configuration values. All three regression cases failed before the change and pass afterward. |
| Medium: duplicated daily/weekly planner | Documented intentional isolation while weekly ownership stabilizes, including the obligation to check both planners when modifying shared budgeting/lineage behavior. Avoid expanding this correction into a reviewed daily-owner refactor. |
| Medium: missing pagination coverage | Already covered by `test_weekly_presentation.py`: three pages, first item identities, row counts, page-scoped status reads and out-of-range denial. Existing HTTP tests cover authorization/barriers and browser tests cover navigation. Do not recreate 50+ persisted submissions merely to repeat the pure slicing test. |
| Medium: missing SQL large-render coverage | False positive. `test_weekly_render_bounds_postgresql.py` already inserts a body above 1 MiB, checks the 8 MiB boundary and rejects oversized daily/operational renders through the real guard. |
| Medium: full observation retained per weekly snapshot | Intentional self-contained selection evidence, including why other observed items were omitted. Terminal item text is excluded in SQL. The annual campaign snapshot cost is accepted for this increment; no demonstrated capacity failure justifies changing that audit contract during stabilization. |
| Medium: all-revoked cohort advances empty boundary | Intentional immutable-cohort contract: recipient withdrawal is audited, not delivery, and newly added Admins do not reopen historical cohorts. The existing all-revoked test proves empty fulfillment; an explicit manual report can repeat current actionable items. |

The focused correction run passed 138 pure compiler/selection/presentation
tests in 0.71 seconds and the real fractional-instant capture regression in
13.45 seconds. No SQL schema or privileges changed. Further review rounds remain
required; no final acceptance or Gate 3 release is claimed.

### Early CI and test-efficiency adjustment

Draft PR #48 opened at `85ba754` so full CI runs alongside reviews instead of
after duplicate local full-suite validation. Following the user's explicit
direction, the broader local shared regression run was deliberately interrupted
after 97 passing cases; Firefox was stopped and WebKit was not started. These
are not complete acceptance receipts. Chromium had already completed all 274
cases in 150.83 seconds.

The separately committed test-efficiency change at `21ab665` follows the
[measurement and relevance audit](stewardship-test-efficiency.md). It retains
all cases and assertions while sharing Chromium/Firefox process startup,
preserving isolated contexts and the WebKit-specific restart workaround, using
bounded disposable CI database tmpfs, and cancelling superseded PR jobs.
Focused fixture/CI tests passed 150 cases in 14.10 seconds; all 15 weekly browser
cases passed in 19.33 seconds. Full speedup and acceptance evidence come from
GitHub CI, not a second full local run. This change is included in the next
focused review, not claimed as reviewed by the earlier round-one snapshot.

### Review round two

Session `20260917-190311-d05504` reviewed `cff8aff` against `85ba754`, focusing
on the first corrections and efficiency changes. Both vendors completed with
no degraded/failed review. Codex reported no findings; Claude reported eleven
raw findings, of which five met the cutoff (two High, three Medium). Six Low
findings do not constitute outstanding Medium-or-higher acceptance issues.

| Finding | Disposition |
| --- | --- |
| High: CI/release test service mismatch | Already fixed in `c7b79c7` following CI's exact-service assertion. Both use the same bounded disposable tmpfs; real deployment persistence is unchanged. |
| High: daily compiler also rejects NBSP labels | Pre-existing, but reproduced and corrected in a separate logical commit. The daily chart already rejects CR/control characters, so that part of the claim does not apply. The pure NBSP regression fails with the old renderer and passes with display-only normalization. Cards/table values are generated numeric/date strings, not imported names. |
| Medium: older browser guides contradict reuse | Fixed with explicit superseding links to the new per-engine policy, retaining historical evidence and the WebKit workaround. |
| Medium: timestamp format lacks cheap exact coverage | Added seven pure cases covering zero through six fractional digits, asserting both capture/item strings and round-trip identity. The whole-second path already had integration coverage; the real fractional capture test remains. |
| Medium: browser close failure skips other cleanup | Fixed. Attempt every owned browser and driver cleanup, clear ownership even when stopping fails, then report all failures. Two focused injected-failure regressions protect this resource-lifetime behavior. |

Focused correction validation passed 237 digest, selection, resource-pool,
coverage-runner and CI-gate checks in 6.96 seconds. The daily NBSP regression
was independently reproduced against the old renderer. No SQL schema changed.
The next round also includes `c7b79c7`'s repeated-bootstrap/baseline reductions;
those were outside round two's fixed review snapshot. Final acceptance and Gate 3
remain open.

### Review round three

Session `20260917-191748-6f7edf` reviewed `cf8c614` against `cff8aff`, including
all second-round corrections and the follow-up efficiency work. Both vendors
completed without degradation. Codex reported no findings; Claude reported eight
raw findings, one High and seven below the Medium cutoff.

The High finding identifies a pre-existing companion daily-renderer defect:
quotes in text nodes were escaped differently from the strict HTML serializer's
canonical output. Apostrophe and double-quote regressions both reproduced the
failure. Daily text-node escaping now matches the weekly compiler, while URL
attributes retain quote escaping and markup remains escaped. The existing
malicious-branding test now checks the real compiled-body boundary, not merely
the intermediate renderer output. All 89 daily/weekly compiler checks pass in
1.34 seconds. A further review is required because this round found a High issue.

Final review/CI and merge receipts will also be retained on
[PR #48](https://github.com/epiphany40223/parishkit/pull/48). Record the verified
delivery in the coordinating task ledger before beginning the next increment.

### Review round four and handoff

Session `20260917-192814-303e0d` reviewed `8dcdd84` against `cf8c614`, narrowly
covering the quote correction. Both vendors completed without degradation.
Codex reported no findings. Claude reported six raw findings: four Low and two
Medium, of which one was outside the diff and excluded by finalization. There
were no High findings. The one validated Medium concern was test parity at the
daily compiled-body validation boundary.

Disposition: production already checks that boundary at retention and again
at fanout. Keep those owners rather than add another repeated image/HTML
validation in production. The shared daily compiler test helper now validates
every rendered result through that actual boundary, so future canonicalization
regressions fail the inexpensive compiler suite. All 89 daily/weekly compiler
checks pass in 1.36 seconds. No SQL schema, privilege or deployment changed.

Four review/fix rounds are complete, with no High issue in the final round and
all accepted Medium-or-higher findings resolved. BG-07.03 is complete for its
weekly scope; BG-07.04/.05, later Staff workflows and Gate 3 remain open.
Exact-head CI/DCO and protected merge-queue checks are still required. Final
receipts belong on PR #48 and the next coordinating delivery checkpoint, avoiding
a documentation-only push merely to repeat the full acceptance suite.

### Protected delivery

The human merged [PR #48](https://github.com/epiphany40223/parishkit/pull/48)
on September 18, 2026 at 00:04:18 UTC as
`8998542bfdfc3020052dc96dae24aceaf176e2cf`, verified on refreshed `origin/main`.
Exact-head run `35287511489` passed all 24 CI jobs and DCO at `380eced`;
combined coverage was 94.00% lines and 85.20% branches. Four successful
dual-source review/fix rounds and their dispositions are recorded above.

The human disabled the merge queue and enabled auto-merge before manually
merging. The now-obsolete queue run `35289294784` was cancelled, not counted as
acceptance evidence. This supersedes the pending queue requirement above;
exact-head checks and review requirements are unchanged. Main-branch CI remains
enabled. BG-07.04/.05 and Gate 3 remain open; no deployment, release or real
provider delivery was authorized.
