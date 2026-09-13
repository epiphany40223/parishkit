# Phase 3A review and correction evidence

[Scope and acceptance evidence](stewardship-phase-3a.md) ·
[Controlling review workflow](../plans/stewardship/overall.md)

## Round 1

The full branch review covered base
`7b2b1dd4478ae4014b167d6c0c203127c9a0ceb9` through
`dbe1bf0e531941105eb83dd709c2f51ca5219e24` (clean tree
`5f3b144ed765db5cb83b5522d4185cb0c81c35ac`). Pika session
`20260913-143543-e3f91b` completed all four automatically selected Claude shards
and its one Codex reviewer. No reviewer failure, degraded coverage, verdict
mismatch or salvage remained. Verdict: **REQUEST_CHANGES**; 12 validated
findings, including two overlapping High findings about email delimiters.

The local-review-triage verification retained eight corrections, consolidated
two duplicates and rejected two false positives. Remediation follows the
standing autonomous authorization; no product choice was changed.

| # | Source | Severity | Disposition |
| --- | --- | --- | --- |
| 1 | Agreed | High | Accept: use the same comma/semicolon email representation on validation and revisit. |
| 2 | Claude | High | Duplicate of 1. |
| 3 | Claude | Medium | Accept: preserve accepted Thank You across local expiry, including in-flight acceptance. |
| 4 | Claude | Medium | Accept: do not serialize or replay disabled additional-information text. |
| 5 | Claude | Medium | Duplicate of 4. |
| 6 | Claude | Medium | Accept: initial SQL proposals cannot mint reviewer decisions or terminal execution outcomes. |
| 7 | Claude | Medium | Accept: cancel actionable proposals for moved, removed, inactive or deceased Members. |
| 8 | Claude | Medium | Already handled: the deferred pin-side guard prevents unpinning an active form; add an exact-web-role regression. |
| 9 | Claude | Medium | False positive: common final validation requires valid email syntax, including prefilled values; optional invalid source addresses may be corrected or cleared, not silently accepted. |
| 10 | Codex | Medium | Accept: competing tab/source edits require an explicit value choice before another review/Submit. |
| 11 | Codex | Medium | Accept: release unused intermediate comparison pins, preserving reviewed/validated inputs and every still-referenced source. |
| 12 | Codex | Medium | Accept: bind worker reconciliation to both live source/task fences and protected current source; bind web supersession to a new final response. |

Focused corrections pass 30 browser tests across Chromium, Firefox and WebKit.
Seventy focused database tests passed before the new moved-Member case exposed
two pre-existing worker metadata/locking omissions in new-Family link issuance.
These narrow grants now allow that real source path without exposing token
contents. All 11 revisit/reconciliation tests then passed. The final focused
HTTP/SQL-authority/schema-audit batch passed 25 tests in 36 seconds, including
real additional-information disable/revisit/Submit and forged SQL review-state
denial. Full-suite validation and later review rounds remain required.

The initial broad database run was diagnostic, not passing gate evidence:
2,012 passed and 148 failed. Most failures shared an overwritten setup metadata
grant; response grants now extend the existing set instead. The other failures
were the guard inventory's missing response-specific contracts, now checked
against their actual enabled SQL triggers and immutable-field protection.

The finalized JSON was recovered after its stdout artifact pointer overwrote
the same output path. The recovered original 37,128 bytes exactly match the
finalizer's emitted SHA-256
`619f7cbf9104df4ca2553bed020424dfc738c632f659549b4bd1fdfdf9e46907`.
No findings were reconstructed from memory or invented. Subsequent rounds keep
artifacts and redirect CLI stdout to a distinct file.

The correction schema audit compared a fresh installation with the preserved,
verified exact-main reference. It adds two response metadata helpers, bringing
the function inventory to 297. No other catalog counts or old table/column,
constraint, index, policy or trigger definitions changed. Only the already
documented source-pin and token-activation functions differ from merged main;
new response guards were tightened. No retained development database was
upgraded or deleted.

## Round 2

The correction review covered `dbe1bf0e531941105eb83dd709c2f51ca5219e24`
through `844d01a439372effd1c52cf641b1d72a423fcbbd`, clean tree
`00ed8a18de43e1f45b6f087fd28b76fba387c140`. Session
`20260913-151606-ed11f1` used one Claude and one Codex reviewer, with no
sharding, failed agents, degradation, mismatch or salvage. Verdict:
**REQUEST_CHANGES**; five validated findings from 15 raw findings. The finalized
artifact SHA-256 is
`a799cbf3c57bfb56bd0db7337b645ae0e7e5fc63d2d6250c13a106a6b907d31d`.

| # | Source | Severity | Disposition |
| --- | --- | --- | --- |
| 1 | Claude | High | Accept: explicitly qualify the SQL helper's snapshot argument; otherwise a same-named column shadows it. |
| 2 | Claude | Medium | Duplicate/coverage of 1: probe an obsolete snapshot under an actually live source owner, for both pin insertion and proposal update. |
| 3 | Claude | Medium | Accept: definite rejected submissions clear the ambiguous in-flight flag before later session expiry. |
| 4 | Claude | Medium | Accept: keyboard/mouse conflict choices remain visible and reversible until explicit Review. |
| 5 | Codex | Medium | Accept: authenticate every inserted proposal's comparison provenance against the protected source or same pending intent, including default unreviewed proposals. |

The source-field SQL helper independently reconstructs the four supported
Member fields from the exact validation snapshot and active household. Default
review state cannot bypass comparison validation, and a terminal old proposal
does not donate its obsolete baseline to a new request. Regression tests probe
forged availability/values and real live-worker non-current snapshot attempts.
Field-by-field SQL/Python parity covers ordinary, multiple, null and missing
emails and missing middle names. The focused authority/field/schema-audit batch
passes 24 tests in 33 seconds; the earlier integrated correction batch passes
59 tests in 70 seconds. Browser corrections pass 39 tests across all three
engines, including arrow-key exploration and expiry after definite rejection.

Nine additional real-connection concurrency tests pass: either ordering of
relevant/unrelated promotion and Submit; simultaneous duplicate Submit; both
baseline/promotion orderings; and compaction racing Submit/cancellation of a
real expiring form pin. Tests observe actual blocked database locks and have
bounded failure paths. They were prototyped against a separate idle disposable
PostgreSQL instance, then added to the maintained suite and rerun there.

The repeated fresh-install audit adds one closed field-source helper and
changes only the previously documented response functions. The function
inventory is now 298; all other catalog counts are unchanged. No upgrade or
deletion of retained databases occurred. Full validation and the third review
remain required; this round's High finding means the review loop cannot exit
at this point.
