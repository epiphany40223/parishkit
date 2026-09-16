# Stewardship personalized Family mail preparation

## Scope and dependency

Branch `pr/stewardship-family-mail-preparation` starts at verified PR #37 merge
`0a4313e59b7e04eb7a758ef417adfbea50d3870c` on `origin/main`.
It continues [BG-06](../plans/stewardship/background-processing.md#bg-06-family-invitations-and-reminders)
after [recipient/recovery preparation](stewardship-family-deliverability.md).

The intended coherent outcome is source-backed personalized Family messages
prepared in the durable outbox, with current mode/epoch credential binding,
redacted retained renderings and sealed substitutions. Recipient evaluation,
versioned content, lifecycle/response checks, idempotent journal ownership and
restricted-role tests are internal checkpoints of this increment, not separate
helper-only PRs.

Follow the controlling [mail contract](../specs/stewardship/background-processing/spec.md#family-invitations-and-reminders)
and [credential boundary](../specs/stewardship/architecture/spec.md#family-credential-security).
Preparation must not decrypt a retained link token in web/general workers,
expose credentials in retained renderings, reuse stale rehearsal credentials,
or grant provider submission merely because an outbox row exists.

Provider submission/reconciliation, partial-recipient outcome handling, the
Admin verified-clear workflow and final pause/resume dispatch integration remain
the following BG-06 delivery increment. This split keeps a complete prepared
message outcome independently testable without combining renderer/credential
review with new external-side-effect ownership. Full BG-06 checkboxes and
Production activation remain open until those owners are integrated.

## Delivery boundary

The merged recipient/recovery and generic outbox owners connect to
the existing content renderer and cryptographic service boundaries. No provider
calls, deployment, release or production-readiness activation is authorized by
this work. All validation uses synthetic/disposable fixtures; the fresh-install
baseline policy remains in force.

## Preparation ownership

The scheduler initializes a missing active Testing epoch under its real session
and campaign-work locks. It then allocates an immutable, opaque preparation
ticket for the selected occurrence and that exact mode/epoch. Tickets retain
only operational UUIDs; they cannot retain credentials or prevent Testing-detail
cleanup. An old ticket drains as cancelled when its epoch or occurrence is gone.

The general worker re-evaluates the complete Family schedule group under its
live TaskRun claim. It provisions a missing rehearsal credential through the
existing collision-reservation service, reads only this Family's current source
records, applies unresolved Family-scoped refusals, and selects the applied
content and sender/Reply-To. It seals the manual code and an opaque primary-token
record reference using the public key. It never reads primary-token ciphertext
or loads a token-private key.

The outbox, redacted rendering, initial history, dispatch task and occurrence
receipt commit together. Preparation completion does not imply provider
acceptance. A lost acknowledgement reuses the committed receipt without
re-rendering; a failure before commit leaves no partial message. PostgreSQL
independently checks current recipients, Testing routing, content/settings
identities, credential generation, task fencing and final occurrence binding.
Workers receive guarded INSERT access, not outbox UPDATE or DELETE access.

Preparation tickets are operational task metadata, not another semantic
fulfillment ledger. An occurrence's stable outbox identity is reused for retries;
the existing schedule fulfillment slot continues to cover initial-invitation
recovery across occurrence revisions. The following dispatch increment must
resolve outcomes and enforce that shared success boundary, including partial
recipient refusals and unknown acceptance.

Temporary source-generation or recipient-projection disagreement holds task
admission/recovery without consuming attempts. Permanent preparation failures
retain their failed TaskRun after five attempts. `retry_preparation` reloads
current Admin policy, mode/epoch and lifecycle scope, then allocates a canonical,
idempotent linked retry on the same root; the worker rechecks current source
before executing it. A repeated command returns that allocated run's current
status even after completion, after rechecking the original actor and current
Admin authorization; it grants no new execution authority. The background-job
Admin UI remains its later presentation
owner, as with report-job retries. It is not an automatic infinite retry loop.

Each Family body alternative must contain both the code and secure-link
placeholders. Initial/reminder editing, YAML application and worker rendering
share this validation. Credential placeholders are not accepted in subjects;
public substitutions cannot synthesize additional private markers. Submission
receipts have a separate non-credential contract and cannot use this renderer.
Testing retains its mandatory subject prefix; subjects longer than 247 rendered
characters are shortened with an ellipsis to keep the final subject at most
254 characters. Production subjects are unchanged.

### Trusted computation and SQL scope

The compiled Python worker is the trusted content renderer and cryptographic
MAC owner. SQL independently enforces task/epoch ownership, selected
configuration/template identities, source recipients and routing; it does not
reimplement HTML sanitization, template rendering or MAC computation. The
database has neither the general decryption key nor the MAC key. A compromised
worker that can already decrypt manual codes or generate new rehearsal secrets
could also leak them through arbitrary logs; SQL content signatures computed by
that same worker would not establish a separate trust boundary.

Reservation writes require a live preparation claim, a credential created since
that claim, and a key actually used for that credential. The compiled allocator
retains the bounded, one-reservation-per-new-credential contract. SQL does not
prove a reservation digest corresponds to encrypted plaintext or claim to
prevent resource exhaustion by arbitrary malicious code running with the
worker's database and keyring privileges. Adding privileged SQL crypto or a
separate trusted renderer is outside this increment's established architecture.

## Review round one

Pika session `20260916-064653-254441` reviewed base `0a4313e` through
`11573c6`, with one successful Claude source and one successful Codex source.
It returned 25 raw findings, eight validated findings after filtering and
cross-source agreement. Every raw finding is dispositioned below; `C` and `X`
refer to the respective raw Claude and Codex order, not filtered severity.

| Raw findings | Disposition |
| --- | --- |
| C1, X4: terminal preparation retry | Fixed: source holds and authorized linked retry; exhaustion/replay/recovery tests. |
| C2: NULL occurrence task domain | Fixed: NULL-safe binding, with direct SQL regression. |
| C3: NULL/missing reconciliation owner | Fixed: explicit false fallback, with corrupted-evidence view tests. |
| C4: recovery/guard/recipient coverage | Expanded real-role recovery, pause, refusal/resolution, immutable-ticket and SQL guard tests. |
| C5: stale deliverability burns retries | Fixed: source agreement is an admission hold; a new real refusal produces a durable skip before credentials. |
| C6: broad integrity-error collision retry | Fixed: retry only SQLSTATE `23505`, not authorization/check errors. |
| C7, X2: arbitrary reservation digest/volume | Narrowed to the current claim and actual key; push back independent SQL MAC/compromised-worker resource guarantees for the trust boundary above. |
| C8: malformed sealed reference | Fixed: uniform cryptographic error, including non-string token IDs. |
| C9: scope row locks in admission | Push back: read-only means no durable effects; shared work-order/row locks deliberately serialize this coherent ownership check. |
| C10: generic Reply-To default | Push back: the generic outbox supports non-Family callers; the compiled Family owner explicitly supplies configured Reply-To and SQL verifies it. |
| C11: cross-owner private helpers | Retain: local imports follow existing owner dependencies; no correctness failure or new import-time cycle. |
| C12: redundant occurrence index | Removed from model and fresh baseline; the composite unique index covers the prefix. |
| C13: steady-state epoch lock | Added an unlocked deferral-only precheck; initialization still rechecks under locks. |
| C14: empty head-name banner | Fixed: fall back to household name or Family. |
| C15, X6: Testing subject expansion | Fixed: bounded Testing-only subject presentation with boundary tests. |
| C16: overly broad privilege-test error | Fixed: require permission-denied evidence. |
| C17: stale guide checkpoints | Updated increment boundary; final-head evidence remains explicitly pending until earned. |
| C18: docstring wrapping | Reflowed. |
| X1: independent SQL body/redaction verification | Push back: trusted rendering is an application boundary, not independently provable SQL cryptography; see explicit scope above. |
| X3: missing access placeholders | Fixed: require code and link in both body alternatives before credential loading. |
| X5: synthesized marker injection | Fixed: temporary disjoint slots and post-render marker rejection. |
| X7: PUBLIC helper execution | Revoked direct trigger-function execution and tested it; retain invoker-rights boolean helpers, which confer no additional reads or mutation authority. |

## Review round two

Pika session `20260916-071824-333ff3` reviewed correction delta `11573c6` through
`e764650`, with both sources successful: nine raw findings, three validated
after severity filtering (one High and two Medium). All findings were accepted
or covered by their corresponding accepted correction, including Low items.

| Raw findings | Correction |
| --- | --- |
| C1 High: authoring/preparation contract mismatch | Shared validation in normal/setup editors, YAML application and worker; generated plaintext is validated after extraction, with actionable editor feedback. |
| C2 Medium: source-absent Family admission | Check coherent population metadata, then let the planner skip inactive/ineligible retained identities without reading a missing source row. |
| C3 Medium, C7 Low, X1 Low: shortened private subject slots | Credentials belong in both bodies, never subjects; shared authoring/application/rendering validation rejects both private subject placeholders, including cut-point cases. |
| C4 Low: completed retry-command replay | Return the existing run after current Admin/original-actor checks, without requiring new-execution admission. |
| C5 Low: redundant source reads | Source projection checks occur at hint/claim, recovery and actual preparation, not every metadata action or same-transaction callback. |
| C6 Low: overly broad authorization assertion | Match the explicit Administrator requirement. |
| X2 Low: reservation predicate coverage | Independently test a pre-claim credential with otherwise matching attribution and a freshly created credential with an unknown key. |

At the reviewed `e764650` head, all eight isolated database shards passed:
3,072 PostgreSQL tests, 94.10% line coverage and 85.38% branch coverage for the
Stewardship scope. That head also passed 5,799 baseline tests and 41
Compose/isolation checks (one separately opt-in smoke test skipped). Subsequent
round-two corrections require their own validation and third review below;
this evidence is not relabeled as final-head coverage.

Round-two corrections passed 62 focused PostgreSQL checks spanning preparation,
recovery, SQL guards and both content-editor entry paths. After strengthening
the reservation attribution fixture, all 18 SQL guard checks passed again.
The targeted pure validation suite passed 105 tests; the broader baseline
passed 5,817 tests before the final receipt-denial regression was added.
Ruff, formatting, migration-state drift and edited Markdown checks passed.
Final committed-head coverage and the third review remain pending.

## Review round three

Pika session `20260916-074238-a1d5e5` reviewed correction delta `e764650`
through `a1e6056` (tree `38b1f38f1bba296728f7812c4956da94d5a5253b`). Both
sources completed successfully. Codex found no issues; Claude returned seven
raw findings, one Medium and six Low, with no High/Critical. The filtered result
was approval, but every raw finding was inspected independently:

| Raw finding | Disposition |
| --- | --- |
| C1 Medium: runtime setup invitation lacks access placeholders | Accepted despite being outside the correction diff: update the actual Compose setup fixture and rerun the disposable full-setup path. |
| C2 Low: missing Production population dereference | Already handled: `_planning_scope` raises `PermissionError` for missing credential state before the dereference, under the same campaign-work lock. |
| C3 Low: repeated target/source checks | Retain: metadata admission must admit an absent/ineligible source Family to the planner, while the private projection loader requires a present source row. Canonical target checks are small fail-closed boundary checks. |
| C4 Low: unrelated editors show Family-only error guidance | Fixed: separate general content errors from a typed `family_access` error, emitted only for initial/reminder templates. |
| C5 Low: negative editor tests lack exact error/control | Fixed: validate the unmodified payload first and assert the Family-specific error code for both slots. |
| C6 Low: YAML tests cover only one slot/credential | Fixed: cross both slots, both private placeholders and all three content fields, with a valid-document control. |
| C7 Low: shared marker aliases/check duplication | Retain existing renderer names; Ruff enforces the import layout. Source-value validation is distinct from template validation and still rejects source-generated marker injection. |

The full `a1e6056` database run additionally found one campaign-mail fixture
with the same missing placeholders and one obsolete setup-preview assertion
that expected no fictional link. Both fixtures now reflect the access contract;
no runtime validation was relaxed. That failed full run is not counted as
passing evidence. Post-correction checks and final protected CI remain required.

## Internal validation checkpoints

- After round-one corrections, the pure baseline passed: 5,799 tests, with
  integration-only suites explicitly skipped.
- Restricted PostgreSQL preparation tests passed for Testing and Production,
  exact-role allocation/execution, source-backed recipients, new rehearsal
  creation, lost acknowledgement, mid-transaction failure, response races,
  invalidated epochs, forged recipients/settings and denied private-token reads.
- The expanded preparation/recovery/guard, storage, rehearsal and schema-baseline
  suite passed all 96 tests after round-one corrections.
- The first full eight-shard run found a schema-audit mapping missing the new
  immutable ticket guard; seven shards passed. The mapping and its exact enabled
  trigger assertion are corrected and pass in the expanded focused run.
- All three required dual-source rounds have completed. Final post-correction
  validation is pending; no failed run is counted as completed validation.

All PostgreSQL checks use owned disposable test instances. Baseline schema
comparisons create new empty databases and never upgrade, adopt or delete an
existing development database. These checkpoints do not claim BG-06 completion,
Gate 3 release, or Production-readiness approval.

## Final local validation and handoff

Implementation and review corrections were consolidated into `d6b0bda`, whose
tree `8d5a7a57332f83e13c9dd8afa3a60fb6c090ade3` is identical to pre-squash
`6a88915`. The original reviewed history is retained locally, and the round
endpoints above are not relabeled as reviews of an unrelated squash history.
The later handoff commit changes documentation only.

At that implementation tree:

- All 3,075 PostgreSQL tests passed across eight isolated shards; the longest
  shard took 10:47 locally. Combined Stewardship coverage passed at 94.09% lines
  and 85.38% branches, with every collected database test accounted for.
- The baseline passed 5,827 tests, with 3,883 explicitly environment-gated skips
  and two existing Python/Valkey deprecation warnings. The focused pure
  content-contract matrix passed 114 checks.
- All 45 targeted PostgreSQL campaign-mail, setup-preview and content-editor
  checks passed after correcting the full-run fixtures.
- The rebuilt image passed 41 Compose/isolation checks. The separately opt-in
  development smoke scenario remains assigned to protected CI.
- Full original-browser initial setup passed in fresh development and
  production-layout runtime projects, using synthetic providers and real
  restricted service identities, in 2:11 and 2:33 respectively. An initial
  concurrent fixed-subnet collision was retried sequentially; no runtime
  isolation was weakened and no retained development services were changed.
- Ruff, formatting, tracked Markdown, migration-state drift and whitespace
  checks passed.

This supersedes the pending local-validation checkpoints above. All three
review/fix rounds have passing correction evidence, with no unresolved accepted
Medium-or-higher issue and no High/Critical finding in the final round. Require
all 24 exact-head CI jobs plus DCO and the protected merge-group checks before
delivery; then verify fresh `origin/main` and continue the dispatch increment.
Full BG-06 completion, Production activation and Gate 3 remain open.

## Protected delivery

PR #38 merged on September 16, 2026 at 13:03:20 UTC as
`72787c28d9108fee0c48e570e09b167d13e9f860`. All 24 exact-head CI jobs
and DCO passed at `214b9fd3dfba524d9a0eb308534624b48817f0bd`
(run `35095792454`); all 24 protected merge-group jobs passed in run
`35097721514`. The merge was verified on freshly fetched `origin/main`
before creating `pr/stewardship-family-mail-dispatch`.

The first PR CI run exposed a browser-test synchronization race: the new-Member
helper captured its baseline before the Family form finished rendering. The
independent test-only correction waits for the Add button before capturing
existing Members, then verifies exactly one new editor. All 45 affected browser
checks passed across three engines, and the original Firefox case passed three
additional independent runs. Supplemental dual-source review
`20260916-082612-162cad` covered `158a2ef` through `214b9fd`, with both sources
successful and no Medium-or-higher findings. Codex reported no findings; Claude's
single Low comment-clarity suggestion was considered and the comment retained:
the client renders existing Member editors synchronously before exposing the
Add button, which establishes the baseline invariant. The reviewer's inability
to bind a local socket did not replace the implementing agent's passing browser
validation. No failed CI attempt is counted as passing evidence.

The final exact-head PostgreSQL aggregate accounts for all 3,075 tests with
94.10% line and 85.39% branch coverage. This supersedes the pending protected
delivery requirement above, without completing BG-06 or releasing Gate 3.
