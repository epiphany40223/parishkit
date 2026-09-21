# Stewardship security event email reviews

This ledger records the independent review/fix rounds of the
[security event email increment](stewardship-security-event-mail.md), under
the delivery cycle's minimum of three rounds per PR. The Codex reviewer has
been out of quota since September 20, 2026; under the human's second
single-source exemption, recorded in
[the overall plan](../plans/stewardship/overall.md), a completed Claude-only
pass counts as a round through September 25, 2026, and each round records
which sources answered.

## Round 1, single-source under the second exemption

Reviewed `e427fd28`, the complete diff from main `f9ce5278`. Codex did not
answer; Claude, in two shards, returned two High, five Medium and thirteen
Low. All seven validated findings were accepted and fixed.

- **High, two findings: the cohort binding compared orderings from two
  collations.** The owner re-sorted the event's recorded recipients by
  Unicode code point while the binding trigger re-sorted them under the
  database's collation, which ignores punctuation at its first level, so
  addresses such as `j.smith@` beside `jane@` would sort differently, the
  cohort insert would be refused on every retry and the alert never sent.
  The owner now passes the recorded order through, each address once, and
  the trigger compares the two lists as sets with each address exactly once,
  so no ordering can disagree.
- **Medium, two findings: the SQL content twin was unproven.** Nothing
  compared `stewardship_security_content_v1` with the Python compiler, unlike
  the operational owner's contract case. A case now records every kind,
  compiles each for both modes with and without a portal actor, and requires
  the SQL JSON to equal the Python rendering, with the escape helper held to
  Python's escaping.
- **Medium, two findings: the metadata scheduler held an address grant.**
  Deciding whether an event has anyone to tell read the event's recipients
  column, the very addresses the scheduler must not see. A schema-owned
  view now exposes each event's recipient count, the scheduler reads that
  view and holds no grant on the recipients column, and a case proves the
  scheduler is refused the event's recipients, the cohort's addresses and
  the recipient's address while allowed the count.
- **Medium: new SQL admission paths were unexercised.** Cases now cover a
  failed partial preparation cancelling its committed child as
  `preparation_failed` through the security dispatch admission with the
  sibling error trigger leaving two fixed ERROR logs, the scheduler
  refusals above, and an owner assembled from an open part refused.

The thirteen Low findings concerned wording or repeated the validated ones.

Post-fix validation: the security fanout and dispatch suites, the
operational fanout, dispatch, routing and hold suites, the background and
mail grant suites, the immutable-record inventory and the schema baseline
passed locally, with the content, envelope, owner and grant registry
database-free suites.
