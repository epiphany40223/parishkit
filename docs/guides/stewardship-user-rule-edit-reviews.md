# Login rule edit review ledger

Review evidence for the [login rule edit increment](stewardship-user-rule-edits.md).
Severities are the raw reviewer values. Findings below the tool's
Medium/confidence cutoff are counted but listed only where they were acted on or
deliberately left.

At delivery the review corrections are squashed into logical commits, and the
complete commit-by-commit history, including every reviewed SHA below, is pushed
to `pr/stewardship-user-rule-edits-reviewed` with a tree identical to the
delivered head. That branch is review evidence only and is never merged. Until
it exists the reviewed SHAs are on the PR branch itself.

## Round 1, single-source under the second exemption

Reviewed `05fac078`, the complete diff from main `e01b52ba`. The Claude
reviewer completed with 11 findings, one Medium and ten Low; Codex exited
without output. Under the exemption this counts as round 1. Accepted and
fixed:

- **Medium: the review counted reach differently from the page.** For a
  domain it counted every identity that presented the claim, including ones
  with an exact rule, alias-domain identities and disabled ones, while the
  page's column counts only the accounts the evaluator authorizes through the
  rule. The review now indexes the applied policy the way the page does and
  reports the same number for a domain, and usable recorded identities for an
  address, worded as what it counts.
- Low: the shared confirmation rechecks the capability the page admitted
  with, passed by the caller, so the two cannot diverge; refusals render with
  the request, so they keep the Admin chrome and session deadlines; a stale
  form or review gets the page's own explanation with a 409 rather than the
  editors' JSON conflict, and a case now signs a review, activates another
  change, confirms and is refused with nothing requested; removing one's own
  exact rule is explained as the loss of Administrator it is; the unused
  `deny` context and a second role-order source are gone, the role order now
  derived from the labels; the query string is refused through the shared
  helper; the refusal helper in the unit test asserts the exception carries
  only its code; the last-Administrator refusals are matched by a phrase the
  domain-roles message does not share; and the guide states the collected
  count of the page's own cases.

Post-fix validation: four database-free, two PostgreSQL and nine browser cases
passed locally, with the page's own suite and the parish and Ministry editors.
