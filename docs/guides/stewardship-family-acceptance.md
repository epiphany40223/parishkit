# Integrated Family acceptance and Gate 2

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[Family checklist](../tasks/stewardship/parishioner-portal.md) ·
[Controlling gate](../plans/stewardship/overall.md#review-gate-2-family-data-privacy-and-ux) ·
[Normative Family specification](../specs/stewardship/parishioner-portal/spec.md)

## Boundary

Branch `pr/stewardship-family-acceptance` starts from PR #29's verified
main merge `e406ecfa5af136aa06b67e5461bc1389360b2a8b`, after final-head and
complete protected merge-group CI passed. The financial delivery evidence is
in its [ledger](stewardship-financial-responses.md#protected-delivery).

Complete the remaining Phase 3 Family acceptance work as one demonstrable
increment. Audit and close FAM-01/02/06/07 against actual implementation and
tests, exercise representative FAM-08, and finish the M3 demonstration. Preserve
the later named owners for full release-browser/manual accessibility coverage,
actual receipt delivery, reporting, publication and destructive workflows.
Do not mark a mixed-phase package complete from partial evidence.

The integrated Gate 2 baseline is the Gate 1 release,
`48be3666f0c89cc15586cb67465cd1ba0504203c`. The human-approved
[evidence-reuse procedure and integration map](stewardship-gate-2-reviews.md)
cover all current Phase 2/3 work since that baseline, including already merged
PRs; the current branch diff alone is insufficient. The pre-production policy still
applies: fresh-install schema and current behavior, without reintroducing
historical upgrade/downgrade work or deleting retained development databases.

## Internal checkpoints

1. Complete the specified entry/final-Submit/Testing confirmation wording and
   section-level review editing, preserving tab-only answers, focus and final
   acknowledgement semantics. Recheck every Family access boundary and record
   the existing and new acceptance-test mapping.
2. Exercise every enabled module combination, repeat/source merge, no-change,
   terminal/proposed Members, additional-text supersession, transactional
   rollback and explicit stale-response review through the real owners.
3. Complete representative narrow-mobile/desktop keyboard, accessibility,
   expiry/error, no-persistent-draft and large-form measurements. Inspect the
   rendered full flow; keep remaining Gate 5 manual/browser obligations explicit.
4. Reuse the existing complete-receipt sharded coverage runner for local gate
   validation with isolated test services. Make test-only service ports
   configurable as needed; do not share a PostgreSQL cluster between concurrent
   shards or weaken collection, coverage, timeout or no-skip checks.
5. Run the complete gate validation and at least three successful dual-model
   review/fix rounds, following the approved Gate 2 integration scopes. Correct all
   accepted Medium+ findings and record endpoints, artifacts and demonstrations.
   Require final-head and complete protected merge-group CI before delivery.

These are internal checkpoints, not routine human approval stops. Standing
merge/continue authority applies after the controlling gate criteria pass.
Phase 4 remains paused until Gate 2 is released; deployment, release, real
provider writes and Gate 5 human approval retain their separate boundaries.

## Implementation and acceptance mapping

The review screen now has section-specific Edit controls with keyboard focus
restoration and unchanged tab-only answers. Controls are disabled while Submit
is pending. Testing entry and final Submit have distinct specified labels,
independent unchecked acknowledgements, and an explicit disposable/return-for-live
Thank You. The live action names the parish; that definition participates in
`family-inputs-v8` for every enabled-module combination, so renaming the parish
requires a new review instead of silently changing the reviewed recipient.

The following test owners exercise actual application code. Browser boundary
fixtures are synthetic; PostgreSQL tests use real source/configuration/session/
submission owners and restricted-role or independent-SQL assertions where named.
They make no real provider writes.

| Acceptance | Executable owners under `tests/stewardship/` |
| --- | --- |
| FAM-01 availability, code/link exchange, epoch/restore, rate limits | `database/test_family_auth_postgresql.py`, `test_active_family_tokens_postgresql.py`, `test_family_identity_postgresql.py`; `test_response_http_postgresql.py` exercises each private form endpoint after interval/eligibility/rehearsal/configuration loss |
| FAM-01 audit/presence and answer-free access | `database/test_presence_postgresql.py`, `test_response_baselines_postgresql.py`, `test_response_http_postgresql.py`; `browser/test_components.py` |
| FAM-02 navigation, validation, draft loss, focus, expiry | `browser/test_family_response.py`, `test_family_acceptance.py`, `test_family_census.py`, `test_member_census.py`, `test_components.py` |
| FAM-06 complete review, final consent, no-change, receipt intent and logout | `database/test_response_http_postgresql.py`, `test_response_submission_postgresql.py`, `test_financial_responses_postgresql.py`; `browser/test_family_acceptance.py`, `test_family_response.py` |
| FAM-06 atomicity, independent authority, duplicate/stale and source races | `database/test_response_atomicity_postgresql.py`, `test_response_authority_postgresql.py`, `test_response_concurrency_postgresql.py`, `test_response_validation_postgresql.py`, `test_financial_responses_postgresql.py` |
| FAM-07 caught-up/conflicting/withdrawn/repeated answers and provenance | `database/test_response_revisit_postgresql.py`, `test_ministry_responses_postgresql.py`, `test_financial_responses_postgresql.py`; existing Member-request suites and the seven Family browser suites |
| Seven nonempty module combinations | Census-only HTTP acceptance, Ministry-only/census+Ministry in `database/test_ministry_responses_postgresql.py`, all four financial combinations in `test_financial_responses_postgresql.py`, plus corresponding browser editors/review |
| Representative FAM-08 privacy/accessibility, network and expiry races | Three browser engines, 320-/1,280-pixel layouts, WCAG 2.2 AA automated scan, keyboard review shortcuts, dirty reload/cancel, answer-free keepalive, no persistent draft, in-flight expiry, uncertain outcome and explicit resubmission tests |

Receipt intent is durable, but delivery remains BG-07/Phase 4. Full current/
previous branded-browser coverage, manual screen-reader validation, worker-delay
mail UX and deployment-scale concurrent load remain their named later gates.
The acceptance mapping does not claim those obligations complete.

## Initial measurements

The large-form browser fixture uses 20 existing Members, the supported maximum
of 100 proposed Members, and the architecture's 500-Ministry reference corpus.
Five hundred is not a production Ministry cap. Its form JSON is 398,700 bytes;
the actual final response JSON is 43,997 bytes, below the enforced 256-KiB limit.
Across Chromium, Firefox and WebKit, initial rendering measured 0.23–1.57 seconds
and review rendering 0.12–0.22 seconds. Join choices remain on-demand/searchable,
the proposed-Member limit disables Add, and final Submit preserves all Members.
These are synthetic local timings, not production p95 measurements.

The integrated mobile/desktop review was visually inspected after automated
keyboard and accessibility checks. Text and controls wrap without horizontal
overflow. The new test-only Valkey port setting allows the existing complete-
receipt coverage runner to use independent local service pairs; it does not
change deployment credentials/hosts or weaken validation.

## Status

Implementation `64ecb4e` passed the complete credential-free baseline (5,297
tests), all 2,490 PostgreSQL cases across four isolated local shards, and all
714 browser cases. Receipt-checked coverage is 94.02% lines and 85.62% branches;
the parallel local database gate took approximately 16 minutes. All 12 isolation,
17 runtime/provisioning, 30 Compose and eight operational cases passed. Ruff,
formatting, tracked Markdown, schema drift and whitespace checks passed.

The first Compose attempt failed its in-container traceability check because
no plan packages were visible, despite the host check passing. An isolated check
with the actual Compose mounts and the complete unchanged-tree rerun passed.
The failed attempt is retained; the underlying transient cause is not confirmed.

Integrated review/correction and Gate 2 closure remain in progress. These
results do not certify later corrections; no remaining Family task or Gate 2
item is claimed complete yet.
