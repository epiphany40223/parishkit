"""The full-size rehearsal's Family probe and Mailpit audit (BG-12 PR 4, #447).

``tools/stewardship-local.sh rehearse --probe-every S`` starts the probe
(``local-send-probe``) beside the send it measures, in a one-off container
on the LOCAL deployment's ``ingress`` network, as the spike check's shards
run (``spike.py``). Every S seconds it takes the newest Family email that
arrived since the run began and that it has not used yet, and runs that
Family's five steps through Caddy with the spike driver's own browser
(``spike.run_family``): open the emailed link, the portal, the form (which
issues the form baseline under the work-order lock), one presence heartbeat
and the unchanged submission. So the rehearsal sees what a Family clicking
its email waits for while the send holds the lock. It prints one JSON line
per probe (the instant and each step's seconds and outcome; never a token,
URL, cookie, address or name) until the operator script creates the stop
file, or at ``--wait-seconds`` (logged with its limit and elapsed time).

After the send, ``local-mail-audit`` reads every message in Mailpit and
counts, for the messages that arrived since the run began, how many carry a
Family link, how many distinct links they carry, how many links arrived in
more than one message (a Family emailed twice), and how many links had never
been emailed before the run (a link token that changed). It prints one JSON
document of counts.

Both talk only to the spike driver's fixed LOCAL endpoints (Caddy by its
Compose name, verified against Caddy's local root certificate, and Mailpit)
and refuse unless ``--profile local`` is given.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep

from . import spike
from .spike import SpikeRefused, bounded

# The probe's interval and wait bounds (seconds), and how many Mailpit
# messages the audit reads at most (Mailpit keeps MAILPIT_MAX_MESSAGES).
EVERY_BOUNDS = (2, 600)
WAIT_BOUNDS = (10, 8 * 3600)
AUDIT_LIMIT = 60_000


def newest_unused(messages, used, message):
    """The newest message's first link token not in ``used``, or None.

    ``messages`` is ``spike.new_messages``' answer (oldest first); ``message``
    reads one message. A message whose links were all used already is
    skipped, so each probe runs a Family that has not been probed.
    """
    for identifier, _created in reversed(messages):
        if identifier in used["messages"]:
            continue
        used["messages"].add(identifier)
        for token in spike.message_tokens(message(identifier)):
            if token not in used["tokens"]:
                used["tokens"].add(token)
                return token
    return None


def run_probe(
    *,
    since,
    every,
    wait_seconds,
    context,
    stop_file,
    fetch=spike.new_messages,
    message=lambda identifier: spike.mailpit_get(f"/api/v1/message/{identifier}"),
    family=spike.run_family,
    browser=spike.Browser,
    clock=monotonic,
    pause=sleep,
    out=sys.stdout,
):
    """Probe one Family every ``every`` seconds until the stop file appears.

    Writes one JSON line per probe to ``out`` and returns how many ran. A
    probe with no unused new email is skipped (the send may be paused). The
    deadline is logged with what, its limit and the elapsed time.
    """
    used = {"messages": set(), "tokens": set()}
    began = clock()
    ran = 0
    next_probe = began
    while not Path(stop_file).exists():
        if clock() - began >= wait_seconds:
            spike.log_timeout("the send probe", wait_seconds, clock() - began)
            break
        if clock() < next_probe:
            pause(0.2)
            continue
        next_probe = clock() + every
        try:
            token = newest_unused(fetch(since), used, message)
        except (OSError, ValueError):
            # Mailpit did not answer usably this time; the next probe retries.
            continue
        if token is None:
            continue
        at = datetime.now(UTC).isoformat(timespec="milliseconds")
        results = family(browser(context), token)
        steps = {
            name: {
                "seconds": None if seconds is None else round(seconds, 3),
                "outcome": result,
            }
            for name, (seconds, result) in results.items()
        }
        out.write(json.dumps({"at": at, "steps": steps}, sort_keys=True) + "\n")
        out.flush()
        ran += 1
    return ran


def audit(since, *, listing=None, message=None, limit=AUDIT_LIMIT):
    """Count the run's Family links in Mailpit (see the module docstring).

    ``listing`` returns every message's (ID, created) oldest first, and
    ``message`` reads one; both default to Mailpit's API. Messages created
    before ``since`` are the history the run's links are compared with.
    """
    listing = listing or (lambda: spike.new_messages(datetime.fromtimestamp(0, UTC)))
    message = message or (
        lambda identifier: spike.mailpit_get(f"/api/v1/message/{identifier}")
    )
    history, per_link = set(), {}
    run_messages = without_link = 0
    for count, (identifier, created) in enumerate(listing()):
        if count >= limit:
            break
        tokens = spike.message_tokens(message(identifier))
        if created < since:
            history.update(tokens)
            continue
        run_messages += 1
        if not tokens:
            without_link += 1
        for token in tokens:
            per_link[token] = per_link.get(token, 0) + 1
    return {
        "check": "mail-audit",
        "history_links": len(history),
        "run_messages": run_messages,
        "run_messages_without_link": without_link,
        "run_links": len(per_link),
        "links_in_two_messages": sum(1 for n in per_link.values() if n > 1),
        "links_not_emailed_before": sum(1 for t in per_link if t not in history),
    }


def _since(value):
    """The ``--since`` instant, refused unless it is an ISO 8601 UTC instant."""
    since = spike._instant(value)
    if since is None or since.tzinfo is None:
        raise SpikeRefused("--since must be an ISO 8601 UTC instant.")
    return since


def execute_send_probe(args):
    """Console entry (``pk-stewardship local-send-probe``); LOCAL only.

    Exits 0 when it ran (however many probes), 2 when refused.
    """
    try:
        if args.profile != "local":
            raise SpikeRefused("The send probe runs only with --profile local.")
        since = _since(args.since)
        every = bounded(args.every, EVERY_BOUNDS, "every")
        wait_seconds = bounded(args.wait_seconds, WAIT_BOUNDS, "wait-seconds")
        if not args.stop_file:
            raise SpikeRefused("--stop-file is required.")
        if not args.ca_file or not Path(args.ca_file).is_file():
            raise SpikeRefused("--ca-file must name Caddy's local root certificate.")
        context = spike._context(args.ca_file)
    except (SpikeRefused, OSError) as error:
        message = str(error) if isinstance(error, SpikeRefused) else "TLS setup failed."
        print(f"ERROR: {message}", file=sys.stderr)
        return 2
    run_probe(
        since=since,
        every=every,
        wait_seconds=wait_seconds,
        context=context,
        stop_file=args.stop_file,
    )
    return 0


def execute_mail_audit(args):
    """Console entry (``pk-stewardship local-mail-audit``); LOCAL only."""
    try:
        if args.profile != "local":
            raise SpikeRefused("The mail audit runs only with --profile local.")
        since = _since(args.since)
    except SpikeRefused as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    try:
        document = audit(since)
    except (OSError, ValueError):
        print("ERROR: Mailpit did not answer the audit's reads.", file=sys.stderr)
        return 1
    print(json.dumps(document, sort_keys=True))
    return 0
