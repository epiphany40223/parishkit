# Member household requests review ledger

[Increment scope and validation](stewardship-member-requests.md) ·
[Standing delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)

Review follows `local-review` and `local-review-triage` under standing autonomous
triage, correction and protected merge authority. Pika owns the exact Claude
and Codex roster. Local probe/review artifacts remain outside Git. This ledger
authorizes no deployment, release, provider write or retained-database upgrade.
Gate 2 remains open; the final section records FAM-04's local acceptance and
the outstanding final-head CI/protected-merge boundary.

## Round 1: Complete terminal and proposed-Member increment

- Session: `20260913-220029-5546f5`.
- Base: `eeb346342fe7c78bd4295801f6800dc5f6ec44fb`.
- Reviewed head: `70640426adc351b29ae0073fcdbd890afe94c65d`.
- Reviewed tree: `5b663946f27649e7bc535b876f8ab3820a4e7b2e`, clean.
- Permission probe: `K78RUN`, successful exact validator/move grants, CLI exit,
  fixture byte comparison, parent validation and no permission denials.
  An earlier probe failed because Claude appended a prose period as a command
  argument. The authorized retry used explicit command delimiters without
  widening grants. That failed probe was not a review round.
- Both vendors completed. Claude reported 15 raw findings and Codex one.
  Finalization validated one High and five Medium findings and filtered ten
  Low findings. No failed/degraded reviewer, mismatch or salvage occurred.
- Final artifact SHA-256:
  `b0ea2dffcc8c2defd0b6e8b1ae03eb67bc2521c58248de69123558c8c0237ee5`.

### Findings and dispositions

| Finding | Source/severity | Disposition |
| --- | --- | --- |
| Terminal source status silently cancels independent death-date work | Claude High, Codex Medium | Accepted as one defect; retain same-Family date comparison after deceased/inactive status and block missing/transferred Members without foreign-source reads. |
| Missing date/status catch-up coverage | Claude Medium | Accepted; test four source transitions with/without matching date, Python/SQL parity and a later surviving-household response. |
| Ordinary edits silently adopt a concurrent terminal request | Claude Medium | Accepted; require an explicit structural choice when another response changes status while ordinary fields were edited. |
| Terminal toggles erase unresolved ordinary field conflicts | Claude Medium | Accepted; retain hidden conflicts through toggles and Review/Back navigation, requiring choices if ordinary editing resumes. |
| Untouched revisits reopen externally resolved semantic work | Claude Medium | Accepted; exclude completed semantic proposals from terminal presentation without cancelling independent date work. |

Corrections include exact-role PostgreSQL and three-engine browser regressions.
All 32 focused database cases and 27 focused browser cases pass, alongside
4,998 default tests and Ruff. The date-survival fixture retains another active
Member: removing the sole active Member correctly disables Family login under
the existing shared eligibility policy, which has not changed.

The independent fresh-install audit verifies the exact starting main against
its own golden inventory before comparing the candidate. Only the same five
function definitions differ from main; 303 functions have round-1 fingerprint
`8cacf087b3f1ab6bdd07bfd47d59486350051910846bfe30b780e05e30cefd6f`.
All non-function fingerprints are
unchanged. No retained database was deleted or upgraded.

Broader post-correction validation passes 193 combined response/request/schema
database cases in 165 seconds and 174 Family browser cases in 212 seconds.
Full tracked Markdown and model-state drift checks pass. Rounds 2/3 remain
required before local-review completion, PR creation and final-head CI.

## Round 2: Request continuity and hidden-conflict corrections

- Session: `20260913-222708-b9166c`.
- Base: `70640426adc351b29ae0073fcdbd890afe94c65d`.
- Reviewed head: `78a99db5854eda7f8bee7df4b223269dac7bd7b6`.
- Reviewed tree: `026fc02b4ded8d56ec889d1008ed2bb364636e4b`, clean.
- Permission probe: `T9vhYZ`, exact successful validator/move grants, fixture
  comparison, parent validation and no permission denials.
- Both vendors completed. Claude reported six raw findings and Codex one;
  finalization validated three Medium findings and filtered four Low findings.
  No High/Critical, failure, degradation, mismatch or salvage occurred.
- Final artifact SHA-256:
  `0b86b1811b94a9d7660bfacb7398a4fd712f7e1dcc6c3e5b575329a0ab6b6dee`.

All three Claude findings are accepted under standing triage authority:

1. A repeated source refresh rewrites unchanged missing/transferred date blocks.
   Keep the original comparison/version and pin when scope remains unavailable;
   test repeated promotions without churn.
2. Preserved terminal/date rows become invisible after an intervening response.
   Select the latest record per terminal/date key across the admitted Family,
   campaign, mode and rehearsal namespace up to the effective response version.
   Include completed/cancelled records in latest selection so older work cannot
   resurrect. Mirror predecessor/continuation authority in SQL; retain exact-
   response ownership for ordinary fields and proposed Members. Test return to
   active membership, same/different intent, decision retention, withdrawal and
   exclusion of another Family's request after a Member transfer.
3. Re-selecting completed deceased status with an unseen blank date silently
   withdraws independent hidden date work. Preserve that date unless the Family
   actually supplied a replacement; test multiple untouched responses followed
   by a new blank deceased request.

The first 35 focused database cases pass. The independent fresh-install audit
still changes only the same five functions, with function fingerprint
`f9fd28a9ee23ddceb0baba249819a8e87cb0a1351d89747ad86af34fb4e04c1f`;
all non-function fingerprints remain unchanged.
Full browser validation passed 621 cases in 660 seconds on the unchanged
round-1 browser code. Broader corrected validation passes 197 combined database
cases (including the additional cross-Family regression) in 200 seconds and
4,998 default tests. Ruff and changed Markdown checks pass. Round 3 and
final-head CI remain required.

## Round 3: Historical namespace and SQL continuation authority

- Session: `20260913-224317-a69afc`.
- Base: `78a99db5854eda7f8bee7df4b223269dac7bd7b6`.
- Reviewed head: `442c57735cb58bf29316f30df04bbd8f4eab4714`.
- Reviewed tree: `8f9ba79f18e952f4c233514bc117dbda567a524b`, clean.
- Permission probe: `Bs7FsM`, successful exact validator/move grants, fixture
  comparison, parent validation and no permission denials.
- Both vendors completed. Claude reported four raw findings and Codex one;
  finalization validated two Medium findings and filtered three Low findings.
  No High/Critical, failure, degradation, mismatch or salvage occurred.
- Final artifact SHA-256:
  `b25cdfe1c894e406b5a1759dfb3150dec65edc4189c7d6a1ff33a22e2832cae9`.

Both findings are accepted:

1. Codex: the web replacement guard did not require the latest matching request
   within the in-flight response's prior namespace/version. Add the same exact
   latest-record selection used by Python and SQL INSERT before allowing a
   transition, even if earlier faulty derivation left a stale actionable row.
2. Claude: normal application tests could hide defects in SQL's independent
   namespace checks. Add direct forged INSERT/UPDATE cases that bypass Python
   selection: foreign Family/mode, old rehearsal epoch, older non-terminal
   ordinary fields, stale predecessors and a successor outside the open
   response. Add actual new-epoch Python isolation and future-version exclusion.

Fault injection is confined to disposable PostgreSQL fixtures. Only their schema
owner constructs malformed historical provenance; the assertions execute with
the exact restricted web role. Existing development databases, application
mutation authority and production protections are unchanged. The independent
fresh-install audit verifies the corrected function fingerprint recorded in the
increment guide, with all non-function fingerprints unchanged.

Post-correction validation passes all 121 combined authority/request/revisit/
rehearsal-cleanup/schema database cases in 122 seconds and all 4,998 default
tests. The 621-case full browser result applies to the unchanged browser code.
Ruff lint/format, changed Markdown and independent fresh-schema verification
pass. The broader 197-case response suite passed before this final SQL-only
restriction and its focused regression rerun.

Three completed dual-model review/fix rounds, no High/Critical finding in the
final round, no unresolved accepted Medium findings and passing post-correction
validation satisfy the documented local-review exit rule. The tested final-round
correction does not itself require a fourth round. Final-head CI and protected
merge remain required; neither is claimed by these local results.
