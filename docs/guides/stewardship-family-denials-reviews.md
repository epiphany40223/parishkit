# Stewardship Family denials reviews

This ledger records the independent review/fix rounds of the
[Family denials correction](stewardship-family-denials.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, because it changes Family authentication responses, with a
correction check after any round that validates a finding. The Codex
reviewer has been out of quota since September 20, 2026; under the human's
exemption, extended through October 30, 2026, a completed Claude-only pass
counts as a round, and each round records which sources answered.

No rounds have been completed yet.

## Round 1

Claude only (Codex produced no output). Four raw findings, none validated.
Three low-severity notes were taken: Admin `denial()` now derives the kind
from the status alone, like Family `denied()`, so a future non-Admin 400 or
403 cannot read as an outage; the guide says every Admin 429 or 503 changes,
signed-in report views included; the default-kind test uses a 403, which is
the case that keeps the old text, and the Admin test checks `Retry-After`.
The remaining note, an end-to-end `/access/` rate-limit case, was not
carried forward. A further round follows.
