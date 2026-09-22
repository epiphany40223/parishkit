# Stewardship manual ParishSoft refresh reviews

This ledger records the independent review/fix rounds of the
[manual refresh increment](stewardship-manual-refresh.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes)'s
minimum of two rounds per PR. The Codex reviewer has been out of quota since
September 20, 2026; under the human's exemption, extended through October
30, 2026, a completed Claude-only pass counts as a round, and each round
records which sources answered.

## Round 1

Claude only (Codex out of quota). Eleven raw findings, two validated, both
corrected:

- Medium: the web role was granted the whole source lease table, which
  superseded the narrower column grant the coalescing read already relies
  on. The table grant is removed, the column grant stands, and the grant
  registry suite asserts the lease is not among the web role's tables.
- Medium: the authorize callback the domain runs under its lock, the one
  check that stops a revoked session recording a command, had no denying
  case. A case now revokes the session after admission and before the
  domain's check, for a new key and a replay alike, and asserts a refusal
  with nothing recorded.

The nine findings the validation step did not confirm were not carried
forward.

## Round 2

Claude only (Codex out of quota). Fifteen raw findings, one validated and
corrected:

- Medium: the domain refuses a source without its configured organization
  with a plain permission error, which the route answered as a denial of
  the Administrator while the page itself answers the same condition as an
  outage. The route now tells the two apart by whether its own
  authorization callback denied, and answers the domain's refusal as
  unavailable; a case asserts it.

The fourteen findings the validation step did not confirm were not carried
forward.

## Round 3

Claude only (Codex out of quota). A correction check of the two rounds'
corrections: sixteen raw findings, one validated and corrected:

- Medium: the page called any waiting refresh task a run the request would
  join, although the domain coalesces only a waiting full refresh of the
  same window whose root is neither leased nor promoted. The page now counts
  full refresh requests whose run is nonterminal and not running, and says
  the request may join that run.

The fifteen findings the validation step did not confirm were not carried
forward.
