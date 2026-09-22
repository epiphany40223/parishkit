# Stewardship activation runbook reviews

This ledger records the independent review/fix rounds of the launch
runbooks' [Production activation](stewardship-launch-runbooks.md#production-activation)
procedure and the related corrections found by the pre-launch gate's first
integration round (PL-I4): the activation procedure itself, the historical
status of the go-live readiness and Production activation guides, the pause
refusal of withdrawal, and report preparation blocking every closed-campaign
resolution. It follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds for a documentation increment, with a correction check after any
round that validates a finding. The Codex reviewer has been out of quota
since September 20, 2026; under the human's exemption, extended through
October 30, 2026, a completed Claude-only pass counts as a round, and each
round records which sources answered.

## Round 1

Claude only (Codex produced no output). Nine raw findings, four validated
(two High, two Medium), all corrected:

- High: "completed recently" hid a hard limit. A full refresh counts for 30
  minutes from its start (the default source-staleness window), checked at
  readiness, at cleanup start and at confirmation. The procedure now names
  the window, how to start the full refresh, and the recovery order.
- High: the scheduled delta refresh runs every 15 minutes during go-live and
  makes the prepared links stale, not only a manual refresh. The procedure
  now starts preparation just after a quarter-hour delta and goes straight
  to confirmation.
- Medium: a stale preparation must be discarded, and its disposal finish,
  before **Prepare inactive Family links** reappears; the button is on the
  links page. The recovery path now says so.
- Medium: **Sign in again with Google** returns to the home page, and
  verification and confirmation must both finish within five minutes of the
  sign-in. The procedure now says to copy the page address first and go
  straight back, for confirmation and withdrawal alike.

Four of the five findings below the validation cutoff were taken as well:
readiness no longer claims the whole page changes nothing, withdrawal
describes what a paused or blocked campaign shows, the historical notes are
fully in the past tense and credit the link preparation increment too, and
the runbook links this ledger. A correction check follows.

## Round 2

Claude only (Codex produced no output). Correction check: four raw
findings, one validated and corrected:

- Medium: as sequenced, cleanup, the wait for a quarter-hour delta and
  preparation make lapsing the 30-minute full-refresh window the likely path,
  and the procedure gave only after-the-fact recovery. The timing notes now
  plan a second full refresh after cleanup, before preparing and confirming
  (round 3 corrected its timing).

The three findings below the validation cutoff were taken as well: reload
the links page after the delta and within five minutes, link the link
preparation design record, and preview a blocked withdrawal again after
resolving its work. A further correction check follows.

## Round 3

Claude only (Codex produced no output). Correction check: four raw
findings, one validated and corrected:

- Medium: a full refresh timed to "finish just after" a delta cannot work,
  because refreshes never run side by side and a delta that comes due during
  the full refresh runs right after it, making the preparation stale. The
  second full refresh now starts just after a delta, so that it, the
  eligibility catch-up, preparation and confirmation all finish before the
  next quarter hour, and the full refresh is timed on the validation
  deployment beforehand.

The three findings below the validation cutoff were taken as well: an
expired links page refuses with a generic "Check this value." error, a
blocked withdrawal usually needs a new sign-in before its new preview, and
the edited paragraphs are rewrapped. A further correction check follows.
