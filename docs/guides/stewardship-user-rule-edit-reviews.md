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

## Round 2, single-source under the second exemption

Reviewed `f58d9a58`, the complete diff from main `e01b52ba`. The Claude
reviewer completed with ten findings, two Medium and eight Low; Codex exited
without output. Under the exemption this counts as round 2. Accepted and
fixed:

- **Medium: an address's reach was read from the page's bounded index.** That
  index loads only identities the applied policy names, so a new rule for an
  address outside every configured domain, the common consumer-account case,
  was told no identity was recorded when one was. The address count is now
  read directly for that one address; the domain count keeps the page's index,
  which no Chairperson confirmation can change.
- **Medium: the round-1 reach correction had no test with real identities.** A
  case now records an authorized colleague, an exact-address holder, an
  alias-domain claim, a disabled identity and a consumer account with one
  usable and one disabled identity, and asserts the domain review equals the
  page's column, a new domain rule reaches nothing yet, and the consumer
  address is counted although no rule names it.
- Low: the own-rule removal wording is asserted; confirming one signed review
  twice is one request whose provenance the installer accepts; the label
  helper is public under its own name; the stale refusal passes the route's
  final authorization recheck like every other response; the consumer-domain
  list lives in the policy schema and is shared with the builder, so the
  schema now refuses `googlemail.com` too; the policy refusal names only the
  cause this route can produce; and the guide is re-flowed.

Post-fix validation: four database-free, four PostgreSQL and nine browser
cases passed locally, with the page's own suite and the parish and Ministry
editors.
