# Ministry response review ledger

[Increment scope and validation](stewardship-ministry-responses.md) ·
[Standing delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)

Reviews use `local-review` and `local-review-triage` with standing autonomous
triage/correction authority. Pika owns the Claude/Codex roster. Local review
artifacts remain outside Git. All three local rounds are complete. Gate 2,
final-head CI and the protected merge are not yet complete. No provider write, deployment, release or retained-database
upgrade is authorized by this ledger.

## Round 1: Complete Ministry response increment

- Session: `20260914-003123-2bd71e`.
- Base: `047f0f458c160fd4f79edc6a57b85f1f32826c1f`.
- Reviewed head: `912b93e8ad3a3e3edca3f4bc0c71fe4c25a0c530`.
- Reviewed tree: `9d7fd5ff9046bc259ce8107c6046fb360d844f74`, clean.
- Permission probe: `wbifkW`; exact validator/move grants, successful CLI,
  expected result, no denials, byte comparison and parent validation.
- Pika partitioned the complete 42-file diff into two Claude shards; both and
  the single Pika-launched Codex reviewer completed successfully.
- Raw severity counts: six Medium and eighteen Low; no High/Critical.
  Finalization validated six Medium findings (five Claude, one Codex) and
  filtered eighteen below-cutoff Low findings. No failed/degraded reviewer,
  verdict mismatch or salvage occurred.
- Final artifact SHA-256:
  `41dabc622a5212ea3a71677bcb6981384125a2c3cfe101f292098482ed07702d`.

### Findings and dispositions

| Finding | Source/severity | Disposition |
| --- | --- | --- |
| Missing positive leave-resolution coverage | Claude Medium | Accepted; join/leave controls run under both schema-owner and exact worker roles, asserting outcome and retained source pin. |
| Resolved proposed Member omission cancels unseen Ministry intent | Claude Medium | Accepted; preserve requests for UUIDs no longer presented after manual census resolution. Mirror the scope proof in Python, web SQL and deferred completeness guards. |
| Missing direct-SQL inactive/unselected/proposed-leave rejection | Claude Medium | Accepted; extend the normalized-answer fault matrix while retaining exact web-role execution. |
| Ministry-only responses sever earlier census history | Claude Medium | Accepted; use the latest complete census response within the admitted Family/campaign/mode/epoch/version, including SQL predecessor checks, explicit Unknown and household convenience presentation. |
| Resolved proposed Member cancellation, second report | Claude Medium | Auto-skipped as duplicate of the second finding; covered by the same correction and regression. |
| Hidden withdrawal edits lose stale-form acknowledgement | Codex Medium | Accepted; require acknowledgement in either direction of an unavailable edited choice, without revealing its old label. |

The module-toggle regression uses actual journaled Testing configuration edits,
not forged campaign projections. It verifies census decision retention,
proposed UUID/Ministry intent, supersession, explicit Unknown, and no resurrection
after a subsequent complete census response withdraws an edit. The resolved
proposed-Member fixture models the later Staff association owner with the same
guarded privileged transition as existing manual-outcome tests; it grants no new
web or worker rights. Existing explicit proposed-Member removal still cancels
visible Ministry work.

Post-correction validation passes 38 focused PostgreSQL cases in 53 seconds,
18 browser cases across three engines in 27 seconds, 5,037 default tests and
Ruff. Infrastructure/browser/database cases are intentionally separate opt-in
profiles, not asserted covered by the default run. A fresh independent audit
verifies the exact main baseline and changes only functions relative to the
round-1 candidate: two added helpers and three revised definitions. The function
inventory is 312 with SHA-256
`db30a8856b102952b0a3a7ddf24a07087439e83a841c3edb4a552840ae2edafc`;
all non-function fingerprints are unchanged. Retained databases are untouched.
Broader regression validation and rounds 2/3 remain required.

The round-1 corrected tree subsequently passed 336 combined response/census/
authority/schema PostgreSQL cases in 321 seconds and 192 Family browser cases
in 233 seconds. Full tracked Markdown and model-state drift checks also pass.

## Round 2: Census continuity and informed Ministry withdrawal

- Session: `20260914-005601-f10ad0`.
- Base: `912b93e8ad3a3e3edca3f4bc0c71fe4c25a0c530`.
- Reviewed head: `bd7e8b6dc132952f64e6114a5dca3b403e83a5e0`.
- Reviewed tree: `ae98e91b2df73e52032322de161dd95a726ac92f`, clean.
- Permission probe: `91xu0y`; exact grants, successful CLI/result, no denials,
  fixture byte comparison and parent validation.
- Both vendors completed; Claude reported eleven raw findings and Codex two.
  Raw severity counts were one Medium and twelve Low, with no High/Critical.
  Finalization validated the Claude Medium and filtered the twelve Low
  findings. No failed/degraded reviewer, mismatch or salvage occurred.
- Final artifact SHA-256:
  `b5ada060f73300414a79b0eac5e3bb250635ec0ef633c66b7bc6128603f111c2`.

Accepted the missing independent SQL scope coverage. Three exact-web-role
faults now attempt cancellation after a proposed Member's census resolution,
cancellation during a Ministry-only response, and omission of a required
withdrawal after a Ministry-only gap. Fault injection replaces only Python
Ministry derivation; real statement/deferred guards must reject the transaction.
The same form then completes through genuine derivation, and a further revisit
checks continued preservation or explicit cancellation. No application code or
schema changes are needed. All 66 combined Ministry, Member authority and
schema cases pass in 100 seconds, including the three new faults. Ruff and
changed Markdown checks pass. The 5,037 default-test and 192 Family-browser
results still apply to unchanged application code. Round 3 and final-head CI
remain required.

## Round 3: Independent SQL fault-test verification

- Session: `20260914-010601-7abdfe`.
- Base: `bd7e8b6dc132952f64e6114a5dca3b403e83a5e0`.
- Reviewed head: `615b83960a063876ced3b8af66051f52e4d28551`.
- Reviewed tree: `93c4be7e9d867dc41124369f818dd2b4cad66519`, clean.
- Permission probe: `rFvJM4`; exact grants, successful CLI/result, no denials,
  fixture byte comparison and parent validation.
- Both vendors completed. Claude reported five raw Low findings; Codex returned
  a valid `APPROVED` result with no findings. Codex run metadata confirms exit
  zero, no stall and no timeout. Finalization approved with zero validated
  findings, filtering the five below-cutoff Low findings. There were no
  failed/degraded reviewers, mismatches or salvage tasks.
- Final artifact SHA-256:
  `e6bbac831704d3124a6ce6c37cd4b192d12cb4b12e4e40017bd93e383f310d6e`.

No round-3 remediation is required. The reviewer traced the three faults into
the real SQL guards; independent database execution was unavailable in its
read-only sandbox, so runtime evidence is the orchestrator's 66-case passing
run, not an asserted reviewer rerun. Final-head lint/format/Markdown checks
also pass. All accepted Medium-or-higher findings across three rounds are
resolved; no round reported a High/Critical finding.

### Pre-PR history consolidation

The final reviewed application/test tree is `615b839`; subsequent closure edits
are documentation only. Before consolidating fixups, preserve the full original
history on local branch `pr/stewardship-ministry-responses-reviewed`. Verify
the consolidated final tree is byte-for-byte identical to that preserved tree.
Use separate logical commits for the previous-increment handoff, the complete
Ministry implementation/tests, and current review/task evidence. Final-head CI
and the protected merge still precede the financial increment.

### CI storage-contract registry correction

PR #27's first final-head run, `34808777278`, passed seven PostgreSQL partitions
but failed partition 4's generic mutable-model inventory test. The Ministry
table correctly uses its domain-specific INSERT/UPDATE/DELETE guard, but that
test had not registered the exact custom trigger/function pair alongside the
other response guards. Add that pair without changing any assertion, application
code, SQL function, privilege, model or schema fingerprint. The test now checks
the actual enabled row trigger, version increment and complete immutable-field
comparison. All 77 selected storage/schema/Ministry-authority cases pass in
47 seconds, alongside Ruff and Markdown checks.

This is a test-inventory synchronization, not a material application/authority
correction requiring another independent-review round under the standing cycle.
The three completed rounds remain valid; final CI verification for the updated
head is recorded on [PR #27](https://github.com/epiphany40223/parishkit/pull/27)
before merge. The preceding history/tree mapping describes the pre-CI
consolidation; this subsequent signed-off test/evidence commit is separate.
