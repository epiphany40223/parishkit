# Existing Member census review ledger

[Increment scope and validation](stewardship-member-census.md) ·
[Standing delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)

Review follows `local-review` and `local-review-triage` under the human's
standing autonomous correction and protected merge authority. The exact
dual-model roster is owned by Pika; no extra reviewers are launched. Probe and
review artifacts contain synthetic/local evidence and remain outside Git.
No deployment, release, real source write or retained-database upgrade is
authorized by this ledger. Gate 2 and the full FAM-04 package remain open.

## Round 1: Complete existing-Member increment

- Session: `20260913-193130-ebd7a0`.
- Base: `5c478e8a304b5de66e9f96d9096937e498d2f836`.
- Reviewed head: `e27e9d2aa87fb6b533861cf977a9c0dd754d9f85`.
- Reviewed tree: `4db9c49aa35935927ce9e8a8c87f0f145070829d`, clean.
- Permission probe: `04QhgB`, exact validator/move grants; successful CLI,
  `PERMISSION_PREFLIGHT_OK`, no denials, exact bytes and parent validation.
- Exactly one Claude and one Codex reviewer completed. Claude reported 20 raw
  findings; Codex reported three. Finalization validated seven Medium findings
  and filtered 16 Low findings. No High/Critical, failed/degraded reviewer,
  verdict mismatch or salvage requirement occurred.
- Final artifact SHA-256:
  `1240cf2198fd0f04ef2a33c939b68fb2cfe7abd9da367bc632ee2ddd164df310`.

### Findings and dispositions

| Finding | Source | Disposition |
| --- | --- | --- |
| Unicode separator validation differs from SQL | Claude and Codex, separately validated | Accepted as one defect; align Python/browser checks and add pure/HTTP/browser regression cases. |
| Empty same-as-home has no visible validation message | Claude | Accepted; add an accessible inline constraint explanation with a distinct ID from server errors. |
| Joined email normalization exceeds the field bound | Claude | Accepted; recheck normalized length in Python/browser instead of failing at insertion. |
| Known-null birth date silently preselects Unknown | Claude | Accepted; only an explicit prior Family answer may prefill Unknown. |
| Unknown wording suggests privacy rather than clearing a recorded date | Claude | Accepted; control and review explain the Admin-reviewed removal request, and the normative spec clarifies the existing workflow. No automatic source write is added. |
| Oversized retained source cannot round-trip or fails uncontrolled | Codex | Accepted; apply compatible projection bounds and the static unavailable-form path, preserving source rather than truncating it. |

Related consistency corrections retain bounded unsupported enum choices after
outer-whitespace normalization in Python and SQL. An unrepresentable pending
field preserves its last verified comparison/pin during refresh without
blocking unrelated source updates; ownership/adapter faults are not swallowed.

The frozen reviewed head passed all 230 combined response/household/Member/
schema database tests in 206 seconds. Its focused 396-test coverage run reached
96% combined statement/branch coverage across the four measured response
modules; that is not a substitute for the complete-project CI coverage gate.
All accepted Medium findings are corrected: 4,946 default tests, 74 Member/
schema database tests and 141 combined Family browser cases pass. Ruff and
changed Markdown checks pass. The independent fresh-
install audit after the enum correction verifies 303 functions, fingerprint
`8b819de66e4db2dd4f7cdabd07c1118a82e67acf4ddbe1dc8458424f7e778d54`,
and no non-function changes. The exact-main reference remains preserved.

## Round 2: Source-unavailable and validation corrections

- Session: `20260913-195703-84ba12`.
- Base: `e27e9d2aa87fb6b533861cf977a9c0dd754d9f85`.
- Reviewed head: `63be1821c525ce030362eb8402b9c25ec87b2b34`.
- Reviewed tree: `2e61c0479801e2c54113b9f36cebcb7c6c83cec3`, clean.
- Permission probe: `LtXhTT`, exact validator/move grants, successful CLI,
  exact fixture bytes, parent validation and no permission denials.
- Both vendors completed: eight raw Claude findings and two raw Codex findings,
  with two validated Medium findings and eight filtered Low findings.
  No High/Critical, failure, degradation, mismatch or salvage was reported.
- Final artifact SHA-256:
  `cf3affc8a37d0df172112c4985c83fb39ba9f49f298647cdea150a24c5759198`.

Both Claude findings are accepted under standing autonomous triage authority:

1. Skipping an unusable source field leaves a proposal apparently actionable
   against stale data. Supersede the round-1 skip policy with explicit conflict,
   unavailable current value and a coherent new source pin. Repeated unusable
   refreshes do not churn blocked proposals. Usable corrections resolve through
   the normal merge and release only obsolete intermediate pins.
2. A generic unavailable form gives staff no actionable source diagnostic.
   Record a closed, value-free WARNING with Family/Member DUIDs and field name.
   Preserve static Family errors; never retain source text or exception dumps.
   Enforce the context independently in SQL and test exact web/worker roles.

A related self-check found that JSON-looking opaque phone input could be
mistaken for a serialized comparison tuple and throw in browser validation.
Use a typed parser result directly and tag opaque comparisons, with malformed
and valid-JSON collision regression cases across all three browser engines.

The frozen reviewed head also passed all 239 combined response/census/schema
database cases in 235 seconds. Post-correction validation passes 4,959 default
tests, 147 combined Family browser cases and 98 focused Member/audit/schema
database cases under the restricted roles. The first combined database run
caught the missing operational-event allowlist entry (258 passed, five failed);
the focused rerun verifies that baseline correction. The broader database rerun
passes all 263 cases in 214 seconds. Ruff, Markdown and model-state drift
checks pass.

## Round 3: Conflict recovery and diagnostic privacy

- Session: `20260913-202327-48f899`.
- Base: `63be1821c525ce030362eb8402b9c25ec87b2b34`.
- Reviewed head: `247b2752eeef83c7e41a1134f2db5de8229e2d69`.
- Reviewed tree: `39b4b6fac8e2b49b3faac5284c79933a35caba96`, clean.
- Permission probe: `ypd32N`, exact validator/move grants, successful CLI,
  exact fixture bytes, parent validation and no denials.
- Both vendors completed: eight raw Claude findings and one raw Codex finding.
  One Medium finding was validated; eight Low findings were filtered.
  No High/Critical, failure, degradation, mismatch or salvage was reported.
- Final artifact SHA-256:
  `f53e728f43257aeeeb8a6c435d83a99d75c11a16b42a807b40f56d9085d2b619`.

The Claude finding is accepted: unusable-to-absent source recovery could keep
the proposal blocked because both comparison payloads carry unavailable/null.
Re-evaluate a previously unavailable conflict after a usable read even when
the new source field is absent. Normal merge then restores pending intent and
releases the obsolete unusable-source pin. Exact-worker regression covers both
corrected-value resolution and absent-value recovery.

Post-correction validation passes all 110 Member/revisit/audit/schema database
cases in 75 seconds and all 4,959 default tests. The 147 combined browser cases
passed on the unchanged browser tree. Ruff lint/format, full tracked Markdown,
model-state drift and the independently audited fresh-install baseline pass.

The three completed rounds, no High/Critical in the final round and no
unresolved accepted Medium findings satisfy the documented local-review exit
rule. No fourth round was required solely because the final round included
this tested correction. The subsequent CI-only correction was independently
reviewed as recorded below.

## CI-only correction and closure

The first CI run caught a source-refresh test fixture using the UTC calendar
date instead of the admitted parish day for giving data. The test-only fix
uses the admitted refresh input's `as_of` and adds winter/summer regressions;
27 affected database tests pass. It changes no production refresh behavior.

- Additional session: `20260913-204853-d905f3`.
- Base: `c0702e810d6605251cb77c86474987efe21e36dd`.
- Reviewed head: `edb5a3eff5cd211e84f5916bed84407d9add01e2`.
- Reviewed tree: `e1e38f0b22bf5e251276c879e8054548054e727c`, clean.
- Permission probe: `2PCMpi`, exact successful validator/move grants, fixture
  comparison, parent validation and no denials.
- Both reviewers completed. Claude reported three Low findings, Codex none;
  finalization approved with no validated Medium-or-higher findings and no
  failure, degradation, mismatch or salvage.
- Final artifact SHA-256:
  `e4a0891a1253d671d4a8a823e236696971b81a0fa81fe72fb2ba287f843a78d3`.

Feature/review fixups were consolidated into one logical signed-off commit
with an independently verified identical corrected tree; the documentation
and unrelated fixture correction remain separate logical commits. Complete
final-head and merge-group CI passed, and PR #25 landed on `origin/main`.
Exact run links, merge SHA and final test counts are in the
[increment evidence](stewardship-member-census.md#evidence).
