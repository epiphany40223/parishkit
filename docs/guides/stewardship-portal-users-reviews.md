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

## Round 2, dual-source

Reviewed `fc41d76c`, the complete diff from main `1070fd28`. Both sources
completed: Codex answered this smaller diff despite its quota, and Claude
returned 12 findings. Three validated Medium findings, one from each source
and one agreed in substance; all accepted and fixed.

- **Codex: "in effect" ignored suspended assignments.** The third table said an
  assignment was in effect whenever a usable identity received the Ministry
  leader role, even with every assignment suspended, which valid policy allows
  after an exact rule is removed. It now requires the evaluator to return scope
  as well as the role, with a case for a matching claim and only suspended
  assignments.
- **Claude: the view audited before it rendered.** The audit row was committed
  when the work transaction ended, so a render that failed afterwards, or a
  reader revoked meanwhile, left a successful disclosure on record. The view
  now observes under the lock, releases it, shapes and renders, and only then
  rechecks access and records the view in a short transaction, as the dashboard
  does.
- **Claude: the recheck test substituted the recheck.** It stubbed the
  admission helper to raise, proving only that something was called. The case
  now disables the Administrator's Google identity mid-request, while the
  observation is being taken, and the genuine read-only authorization refuses
  with 403, no address and no audit row.

Acted on from the nine Low findings: the third table states the root cause
whether or not anyone has signed in; the in-force rule for an assignment is one
shared predicate in the evaluator; the Admin editors' admission helper takes the
page's capability and a read-only flag, so this page has no admission helper of
its own; an incomplete deployment is tested to be sent to setup by the access
gate before the page runs, with no audit row; the seeded fixture reuses one authentication-runtime factory instead of
copying it; the rules-first rationale is stated correctly; the index class is
named `AppliedPolicy`; both tables define the last successful sign-in the same
way, over every recorded identity at the address or domain; and the guide's
checkpoint is rewrapped.

Post-fix validation: six database-free, nine PostgreSQL, 16 navigation and six
browser cases passed locally, with the evaluator's own suites and the parish and
Ministry editors that share the admission helper.

## Round 3, dual-source

Reviewed `c21bb6d3`, the complete diff from main `1070fd28`. Both sources
completed again; two validated Medium findings, one from each source, both
accepted and fixed.

- **Codex: the domain's last sign-in missed consumer accounts.** The row is
  defined over every recorded identity at the domain, but the bounded identity
  read selected only addresses with a rule or assignment and identities with a
  matching hosted claim. A consumer account whose exact rule was since removed
  presented no claim and had no rule, so its sign-in vanished from the domain's
  history. The read now also selects identities by normalized email suffix, and
  the revoked-Administrator case signs in from such an account and asserts the
  domain row shows the sign-in while authorizing nobody.
- **Claude: the mid-request revocation case never proved admission.** It
  ignored the sign-in response and did not check that the disabling hook ran,
  so a 403 from ordinary admission would have passed. It now asserts the 302 of
  a completed sign-in and that the observation ran exactly once.

Acted on from the ten Low findings: the role-denial case also asserts the
sign-in completed; the ledger says the setup redirect comes from the access
gate; the guide's scope sentence describes the domain row's sign-in as
implemented; the chrome is handed the verified configuration the way the
dashboard does it; the identity read drops `Lower()` on an already normalized
column and the cost case pins that; the audit count is taken from the rendered
tables rather than recomputed from the records; the index groups identities in
one pass and the in-effect test is a named predicate; the admission helper's
refusal names the capability it checked; the guide's checkpoint is rewrapped.

Post-fix validation: six database-free, nine PostgreSQL, 16 navigation and six
browser cases passed locally, with the evaluator's own suites.
