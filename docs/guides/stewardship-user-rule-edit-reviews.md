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

No review round has been recorded yet.
