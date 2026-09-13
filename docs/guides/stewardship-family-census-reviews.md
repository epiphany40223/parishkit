# Family census review ledger

[Increment and evidence](stewardship-family-census.md) ·
[Standing development cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)

The smaller Phase 3B household increment starts at merged `4051b4a` (PR #23).
Each round uses Pika's generated Claude roster and its own detached Codex
reviewer. No duplicate reviewers are launched. Correction rounds review the
actual delta since the preceding completed round, with the original scope and
this disposition ledger as context. No deployment, release or provider writes
are authorized by this ledger.

## Round 1

- Session: `20260913-173944-31c0c1`.
- Full branch: `4051b4a..2f7a989`.
- Reviewed tree: `eb3d6cb42a2f6c33c05da5bc0767048b09a65146`, clean.
- One Claude reviewer and one Codex reviewer completed successfully.
- Four validated findings from 17 raw findings: four Medium, no High/Critical.
- No failed agents, degradation, verdict mismatch or salvage.
- Finalized artifact SHA-256:
  `0bb4699c544bf48f3939e2035ef99c632bdc8007d9312a3afc50ca22ae47e3a5`.

The disposable native permission probe passed before launching either reviewer:
CLI exit zero, exact `PERMISSION_PREFLIGHT_OK`, `is_error: false`, no permission
denials, draft gone, output byte-identical to the validated fixture. Claude
received only exact per-process validator and draft-to-output move permissions.
Local lean-ctx initially lacked `mktemp`, `pika`, `claude` and `cmp`; these
task-relevant commands were added under the user's standing authorization,
without disabling shell security or broadening Claude's Bash grants.

### Dispositions

All four validated findings were verified and accepted through
`local-review-triage` under the user's standing autonomous remediation policy.
There are no discarded Medium+ findings in this round.

1. Claude: copying an already-equal address could replace a retained distinct
   mailing draft without confirmation. Preserve that draft unless the displayed
   mailing value is itself distinct; cover stale review, recheck and restoration.
2. Codex: SQL did not bind a new proposal's actor to its parent Family. Bind
   actor identity in the common insert guard and reject null/arbitrary actors
   using the exact web role.
3. Codex: an untouched stale same-as-home flag could reverse another adult's
   newer choice. Adopt refreshed untouched flags, preserve only actual edits,
   and require explicit address/copy decisions when those edits interact.
4. Codex: a corrupted aggregate could bypass Python's complete address rules.
   Add an independent normalized-address SQL guard, including ISO/US vocabulary,
   component bounds, required values and ZIP syntax. Exhaustively test every
   two-letter country/US-region candidate against the pinned Python vocabulary,
   and corrupt valid aggregates immediately before insertion under web grants.

Associated self-check corrections add visible, linked household validation
messages, place same-as-home beside the mailing address, shorten the mobile
unavailable option, and reject Unicode normalization expanding beyond a field
bound. These are included in the next correction review, not hidden follow-ups.

The focused SQL correction suite passes 40 tests; pure validation/input checks
pass 178. All 72 Family browser cases pass in 87 seconds. Broader database
post-fix validation and the next two rounds remain pending.

The independent exact-main schema audit now finds two added functions (the
household-source helper and normalized-address guard), the same three changed
functions, and no other object changes. The resulting 301-function fingerprint
is `e1fc3cdafe0456aa2fd1d0d338038f2b48617e2c1fc5c8bcab4293dff83d9c23`.
No retained database was upgraded or deleted.

## Round 2

- Session: `20260913-175559-7252f5`.
- Correction delta: `2f7a989..f869e7d` (517 diff lines).
- Reviewed tree: `2c564260a7acfb803253df418cd860d3edc23747`, clean.
- One Claude reviewer and one Codex reviewer completed successfully.
- Three validated findings from 14 raw findings: one agreed Medium and two
  Claude-only Medium findings. The raw severity totals include four Medium
  findings before cross-source agreement and ten Low findings. No High/Critical.
- No failed agents, degradation, verdict mismatch or salvage.
- Finalized artifact SHA-256:
  `63551842aa44b755aa4cfbcd1403809f5a1488da372ba6ca1cbfa6400fb60f6e`.

The same exact-path native permission preflight passed before review launch.
All round-1 corrections subsequently pass 182 database tests in 196 seconds,
72 Family browser tests in 87 seconds and 4,656 default tests in 44 seconds.

All three round-2 findings were verified and accepted. A shared stop-copy
transition restores the retained separate mailing value before applying a new
address-conflict choice. Server error mapping now accepts digit-bearing
`line1`/`line2` paths, with focusable summary links and inline errors. Household
validation still computes validity on changes, but only exposes errors after
the component is touched or Review is attempted. Explicit form submission
validation ensures native early blocking cannot bypass that Review summary.
New three-engine cases cover each reported interaction. The old blur test
initially failed in all three engines because it expected an error before
leaving the field; it now performs an actual blur. The corrected complete Family
suite passes all 87 cases in 103 seconds. Browser-free collection, Ruff lint
and format, and Markdown checks pass. Round 3 remains pending.

## Round 3

- Session: `20260913-181015-7a3f4d`.
- Correction delta: `f869e7d..c6f12f6` (269 diff lines).
- Reviewed tree: `7da73426766173f60a132dd86666a2e753ad6692`, clean.
- One Claude reviewer and one Codex reviewer completed successfully.
- One validated Claude-only Medium finding from eight raw findings; seven Low.
  No High/Critical findings. Codex returned Approved with no findings.
- No failed agents, degradation, verdict mismatch or salvage. Codex's read-only
  sandbox did not support rerunning browser tests; parent validation supplies
  the recorded executed tests, not a claim of an independent browser rerun.
- Finalized artifact SHA-256:
  `c42c17926733182703ab5b875fe09bff1009374987b5829365378dd0cf6d0d6f`.

The exact-path native permission probe passed again before review launch.
The remaining finding was verified and accepted: equal final address/copy
choices could produce different mailing values depending on intermediate
clicks. Both choices now start from the same retained separate mailing value.
Explicit manual mailing edits and selected mailing-conflict values update that
retained value; an abandoned home copy never does. The UI explains the meaning
of keeping addresses separate. The regression reaches the same final choices
both directly and through an intermediate copy and checks the same result.

Final post-fix validation passes all 93 Family browser tests in 114 seconds,
including both click-history paths. Python and SQL are unchanged from the
passing 182-database/4,656-default-test checkpoint; Ruff and Markdown checks
also pass. All eight validated findings across three rounds are resolved, with
no High/Critical in the final round. Under the standing cycle, an additional
review is not required solely because this final round included its correction
and regression test. Full final-head CI remains required before protected merge.
