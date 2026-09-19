# Report workspace review ledger

Scope and acceptance: [implementation handoff](stewardship-report-workspace.md).
Follow the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle).
Each round uses a fresh exact-command Claude permission probe, Pika's generated
Claude roster and its independent detached Codex reviewer. Finalized results,
not partial reviewer files, control dispositions. Routine technical decisions
use the human's standing delegation.

## Round 1

Session `20260919-065550-a583a5` reviewed the complete `2c3151e6` → `8e572f5`
branch diff. Both reviewers completed normally: no failed agents, verdict
mismatches, degradation or salvage. Finalization returned COMMENT with four
validated Medium findings from 14 raw: five Medium (including one cross-source
duplicate), nine Low and no High/Critical. Nine Low findings were filtered below
the mandatory correction floor, not treated as failed reviews.

| Finding | Disposition and evidence |
| --- | --- |
| Browser timezone initialization | Accepted. Query state now distinguishes explicit timezone selection from the server's UTC fallback; normal navigation leaves it absent until browser detection. Native URLs preserve chosen zones. Three-engine tests begin with a different fallback and verify explicit UTC is retained. Page DOM timestamps already used browser-local presentation; the defect affected selected report/export timezone. |
| Transient export failures shown as denied | Accepted. Native actions distinguish authorization/not-found from transient failures and return fixed, private 503 recovery pages with Retry-After. Error rendering does not run database-backed request context processors. The shared grant consumer propagates operational failure to its owning adapter; the existing JSON endpoint retains its behavior. |
| Missing workflow coverage | Accepted. Actual-role tests now cover successful native retry/replay, expired files, gated disabled controls and rejected commands, reversed/paginated daily values, inactive cards, archived HTTP reports and the guarded selector. Only the pagination size is reduced to exercise multiple pages with the small real source corpus. A future purge sentinel is installed solely in a disposable fixture; all application checks run with guards enabled. |
| Unrelated campaign blocks selected report | Accepted. The selected report guards only its own UUID. A separate campaign picker holds the required multi-campaign protection for its labels. The suggested unguarded name read was not used because the normative read contract still applies. |

Post-correction validation passes four combined PostgreSQL workflow scenarios
in 28.81 seconds and 30 focused presentation/browser tests in 18.13 seconds,
including all three browser engines. Ruff and whitespace checks pass. The
earlier 25-pass selection/archive batch, 17 strict catalog checks and unchanged
model-state check remain supporting evidence. No accepted Medium+ issue from
this round remains open. Two additional completed rounds and final-head
CI/DCO/protected delivery remain required.

## Round 2

Session `20260919-071425-ac602d` reviewed corrections `8e572f5` → `a973677`
with surrounding authorization and read-guard context. Both reviewers completed
normally with no failure, mismatch, degradation or salvage. Finalization
returned COMMENT: two validated Medium findings from 11 raw (two Medium, nine
Low, no High/Critical).

| Finding | Disposition and evidence |
| --- | --- |
| Picker lifecycle race reported as access denial | Accepted. The guard already returns retryable unavailability if its initial state query detects a removed campaign; lifecycle admission can also close after that query, before drainage. The picker now translates only this lifecycle rejection into guarded retryable unavailability, preserving fresh user authorization failures as denials. An actual-role HTTP regression exercises that later admission seam and checks private 503/Retry-After output. |
| Unknown campaign creates misleading view audit | Accepted. Selected campaign admission now precedes STARTED audit, while the guarded recheck remains. HTML and exact-chart requests for fabricated UUIDs return denial without creating any view audit records. |

All three combined native PostgreSQL scenarios pass in 28.29 seconds, including
the new regressions. Ruff checks and formatting pass. Round 1's pushed-head CI
also passed in full. No accepted Medium+ finding remains open from this round;
one additional completed round and final-head CI/DCO remain required.

## Round 3

Session `20260919-072619-82ed4e` reviewed corrections `a973677` → `070aa91`.
Both reviewers completed normally without failure, mismatch, degradation or
salvage. Finalization returned COMMENT with three validated Medium findings
from four raw (three Medium, one Low, no High/Critical).

| Finding | Disposition and evidence |
| --- | --- |
| Picker conflates global closure with lifecycle retry | Accepted for admission after middleware. Shared read admission keeps missing configuration/restore gates as denials, and only maps a known campaign's refusal to retryable unavailability. The claim about ordinary already-restoring requests was incomplete: existing access middleware redirects all four entry points to maintenance before these views run. Real-role HTTP coverage preserves that routing; unit cases cover the later closure seam. |
| Default navigation lifecycle race returns 403 | Accepted. Default navigation now uses the same typed read-admission helper as the picker, selected HTML and PNG. Its discovery-to-admission race returns private 503/Retry-After rather than a misleading role denial. |
| Pre-audit admission masks selected-campaign unavailability | Accepted for retained-campaign lifecycle closure. HTML and exact PNG now distinguish unknown UUIDs (403, no view audit) from known closed admission (503/Retry-After, no premature view audit). Fresh guarded authorization remains mandatory. The suggested blanket restore-to-503 mapping is not adopted: deployment-wide routing/denial remains consistent with the existing maintenance boundary and the preceding finding. |

The helper never changes a refusal into permission and reads only lifecycle
metadata when classifying failure. Export mutation admission, response-lifetime
guards and catalog grants are unchanged. Focused validation covers all four
entry points, unknown UUIDs, global closures, no-audit refusals and role changes.
Validation passes 22 focused unit tests in 0.46 seconds. The two unchanged
native export/access workflows passed in the initial three-case run; its new
restore expectation was corrected to match the middleware's actual maintenance
redirect. The corrected combined navigation regression passes in 19.88 seconds
on a fresh disposable PostgreSQL database, with retained databases untouched.
Ruff and formatting pass. No accepted Medium+ issue remains; all three rounds
are complete. Exact-head full CI/DCO and protected delivery remain required.
