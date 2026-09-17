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
