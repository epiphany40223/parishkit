# Stewardship login-rule autosave queue reviews

This ledger records the independent review/fix rounds of the
[login-rule autosave queue increment](stewardship-rule-autosave.md), under
the delivery cycle's minimum of three rounds per PR. The Codex reviewer has
been out of quota since September 20, 2026; under the human's second
single-source exemption, recorded in
[the overall plan](../plans/stewardship/overall.md), a completed Claude-only
pass counts as a round through September 25, 2026, and each round records
which sources answered.

## Round 1

Claude only (Codex out of quota). Thirty-two raw findings, seventeen
validated, all corrected:

- High (two): a resubmitted key was refused as stale once the installer had
  activated the request, and an intent that created a rule rebuilt its
  patch with a fresh record id so the same key read as another intent; the
  documented same-key recovery was false in both cases. The apply route now
  answers a used key with its request's committed state before looking at
  the digest or the rules, and autosave serves existing rules only, so
  identical intents build identical patches.
- Medium (builder): a grant to a target another Administrator had deleted
  would have recreated the rule on a conflict retry; a missing target is now
  refused either way and creation stays with the reviewed add forms.
- Medium (client, nine): exhausted retries and polling failures declared or
  stranded requests instead of keeping them uncertain with their key;
  polling was unbounded, uncaught and ran while the tab was hidden; a change
  of mind queued an unchanged intent the server refuses; the live tick was
  copied into the confirmed value; a stale base found by the installer was a
  generic failure; the conflict view moved intents out of the guarded queue,
  let newer edits bypass it, and left discarded ticks and defaults wrong;
  lost access left the restricted tables on screen; and one indicator per
  row lost a role's failure to another role's queued change. The queue is
  now one ordered collection throughout, confirmed values are tracked apart
  from the ticks and set only from receipts or the current rules,
  uncertainty keeps the key and offers another look, a `stale_base` failure
  opens the conflict view, discards return to the current rules, lost
  access clears the page, and the indicator is per role.
- Medium (tests, two): the browser suite lacked the conflict, lost-access,
  same-key retry, terminal-failure and stale-base cases, and the PostgreSQL
  suite lacked same-key recovery after activation and another
  Administrator's real request id; all added.

The fifteen findings the validation step did not confirm were not carried
forward.

## Round 2

Claude only (Codex out of quota). Twenty-two raw findings, ten validated,
all corrected:

- Medium (route): the used-key lookup and the digest check were not
  serialized, so an original request that activated between them was
  refused as stale. The key is looked up again before a stale digest is
  refused; the interleaving itself is not reproduced by a test.
- Medium (client, nine): a redraw of the conflict view reset the
  Administrator's selections; discarding emptied the queue before judging
  a retained intent for the same control; intents that had come to equal
  the confirmed value, or the value in flight, were still sent and refused
  as unchanged, before dispatch and after a refusal alike; the CSRF token
  captured at load stranded a rotated session; untouched controls were not
  reconciled with the current rules after a conflict; requests had no
  deadline; and the row kept reading Applying while the conflict view was
  open. The selection now belongs to the intent, both resolutions reconcile
  every control with the current rules while kept intents keep their ticks,
  the queue is pruned before each dispatch and after each refusal with the
  in-flight value as the reference for its control, every answer names the
  session's CSRF token which the page adopts, every request has a deadline,
  and the row says the change was not saved when it enters the conflict
  view.

The twelve findings the validation step did not confirm were not carried
forward.
