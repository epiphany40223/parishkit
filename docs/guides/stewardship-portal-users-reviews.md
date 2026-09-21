# Portal users review ledger

Review evidence for the [portal users review increment](stewardship-portal-users.md),
PR #77. Severities are the raw reviewer values. Findings below the tool's
Medium/confidence cutoff are counted but listed only where they were acted on or
deliberately left.

At delivery the review corrections are squashed into logical commits, and the
complete commit-by-commit history, including every reviewed SHA below, is pushed
to `pr/stewardship-portal-users-reviewed` with a tree identical to the delivered
head. That branch is review evidence only and is never merged. Until it exists
the reviewed SHAs are on the PR branch itself.

## Round 1, single-source under the second exemption

Reviewed `f2c856d1`, the complete diff from main `1070fd28`. The Claude reviewer
completed with 20 findings, seven Medium and 13 Low. The Codex reviewer exited
without structured output, as it had on the two preceding attempts for PR #76.
At the time this was recorded as not a completed round. Later that day the
human confirmed Codex was out of quota and granted a
[second exemption](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
through September 25, 2026, under which this completed Claude-only pass counts
as round 1. The findings were acted on before that decision.

All seven Medium findings were accepted and fixed.

- **The last sign-in was not a sign-in.** Every verified Google attempt records
  an identity and refreshes its verification time before policy is evaluated,
  including a refused attempt, so an explicit deny could show a recent sign-in.
  The guide's claim that the time meant a completed sign-in was wrong. The page
  now uses the durable login audit event written only when a session is issued.
- **The in-lock recheck was unproven.** The revoked-Administrator case was
  refused by ordinary admission, so deleting the recheck failed no test. A case
  now loses access between admission and the recheck and asserts a 403, no
  address and no audit row. The revoked case runs under the web role and pins
  403.
- **The confirmed-assignment query was never exercised.** Every database case
  had an empty seeded set. A case now installs seeded rules and real overlays the
  way the evaluator's tests do, and asserts the page agrees with
  `current_principal` for a confirmed and an unconfirmed Chairperson.
- **Several identities for one address were chosen arbitrarily.** Google's
  subject owns identity, so an address can have more than one; a dict kept
  whichever the unordered query returned last. Identities are grouped, the
  latest successful sign-in is shown, and the disabled warning counts them.
- **The third table reasoned from the email ending.** It never called the
  evaluator, which is what the page warns against. Recorded identities are now
  judged by the hosted claim they presented; only an unseen address is described
  from policy, as a condition.
- **The global lock was held across too much.** It covered an unbounded identity
  load that grows with strangers' attempts, per-address rescans, rendering and
  the chrome queries. The identity read is now bounded by policy, records are
  indexed once, and only the observation, recheck and audit stay inside the
  lock. A case pins that forty strangers add no query and no row.
- **Granted roles ignored a disabled identity.** Policy and identity are now
  shown as the separate facts they are, with wording that a disabled identity
  cannot use what its address is granted.

Acted on from the 13 Low findings: domain-rule evidence counts only accounts the
evaluator authorizes through the rule, not any matching claim; the column is no
longer called sign-ins; a suspended seeded role no longer also says to obtain
the role; an unreachable no-role branch for domain rules and its invalid fixture
are removed; sign-in and this page share one confirmed-assignment query; the
POST case carries a CSRF token and pins 405; the navigation entry checks the
page's own capability; origins use neutral labels in their own column rather
than the row header; the audit count covers all three tables; unused record
identifiers left the template context.

Left as is, with the docstring saying so: the page is unavailable during a
restore review, like the editors beside it.

Post-fix validation: five database-free, seven PostgreSQL, 16 navigation and six
browser cases passed locally, and the evaluator's own suites passed with the
shared query.
