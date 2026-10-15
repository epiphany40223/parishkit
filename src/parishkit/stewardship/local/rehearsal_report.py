"""The local bulk-send rehearsal's offline report (BG-12 PR 1).

``tools/stewardship-local.sh rehearse`` collects four files in one directory
and runs ``pk-stewardship local-rehearsal-report --input DIR`` over them in
an isolated container (no network, no database):

- ``meta.json``: what the operator script knew (the due instant, when it
  started, stopped and restarted mail-dispatch, the deployment's bulk
  switch, SMTP latency and mail consumers, the ``pg_stat_database``
  deadlock counter before and after, and Mailpit's message count);
- ``measure.json``: the seeder's ``measure`` step (``rehearsal.py``): the
  send's occurrences, messages, outcomes with their send statistics, and
  correctness counts;
- ``timings.jsonl``: the services' JSON log lines carrying the DEBUG timing
  lines (``bulk timing: {...}`` from ``jobs/family_mail_bulk.py`` and
  ``lease renewal timing: {...}`` from ``jobs/lifetime.py``);
- ``locks.tsv``: one line a second from the operator script's sampler: the
  epoch, the work-order lock's granted holders and waiters, the holders'
  role and state, the waiters' roles, the VM's ``/proc/stat`` CPU line and
  its one-minute load average.

The report prints the protocol's measurements (docs/plans/stewardship/
background-processing.md, BG-12 "Rehearsal protocol") as text and writes
the same numbers to ``summary.json`` beside the inputs when the directory is
writable. Everything here is pure: parsing and arithmetic over those files,
so it is unit tested without a deployment.
"""

import json
import math
import sys
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path

# The DEBUG timing lines' prefixes, as the instrumented code writes them.
BULK_PREFIX = "bulk timing: "
RENEWAL_PREFIX = "lease renewal timing: "
# The send statistics phases the mail send report prints, in its order.
PHASES = ("wait_ms", "request_ms", "submit_ms", "helper_ms", "smtp_ms", "total_ms")
# The bulk timing line kinds, in the order a send goes through them.
KINDS = ("prepare", "commit", "outcome")
# The seed's ordering invariant that no message is created before its
# occurrence is due. Since BG-12 PR 2 the bulk sweep prepares a Production
# Reminder up to PREPARE_AHEAD early (campaigns/family_schedule_planning.py;
# a test keeps the two equal), so for such a run the whole-database count is
# replaced by the run's own count bounded by that lead. Every other run
# (bulk off, Testing, an Initial invitation) keeps the invariant unchanged.
CREATION_INVARIANT = "messages before their occurrence"
PREPARE_AHEAD = timedelta(hours=2)
# Occurrence and message states that end a send's work.
FINAL_OCCURRENCE_STATES = frozenset(
    {"succeeded", "skipped", "coalesced", "failed", "delivery_unknown"}
)


def percentiles(values):
    """Nearest-rank p50, p90, p99 and maximum of ``values``, plus their count.

    Nearest rank (the smallest value with at least that share at or below
    it) keeps every reported number one that was actually measured. An
    empty list gives a count of zero and no percentiles.
    """
    ordered = sorted(values)
    if not ordered:
        return {"n": 0}

    def rank(share):
        """The value at the nearest rank for ``share``."""
        return ordered[max(0, math.ceil(share * len(ordered)) - 1)]

    return {
        "n": len(ordered),
        "p50": rank(0.5),
        "p90": rank(0.9),
        "p99": rank(0.99),
        "max": ordered[-1],
    }


def parse_timings(lines):
    """The bulk and renewal timing records in the services' JSON log lines.

    Each log line is one JSON object whose ``extra.debug.message`` holds the
    original DEBUG message (debug logging on, as the local environment runs
    by default). Lines that are not JSON, or whose message is not a timing
    line, are skipped. Returns ``(bulk, renewals)``: the bulk records, each
    with its log ``timestamp`` added, and the renewal waits in milliseconds.
    """
    bulk, renewals = [], []
    for line in lines:
        try:
            record = json.loads(line)
            message = record["extra"]["debug"]["message"]
        except (ValueError, TypeError, KeyError):
            continue
        if not isinstance(message, str):
            continue
        try:
            if message.startswith(BULK_PREFIX):
                values = json.loads(message[len(BULK_PREFIX) :])
                if isinstance(values, dict) and values.get("kind") in KINDS:
                    values["timestamp"] = record.get("timestamp")
                    bulk.append(values)
            elif message.startswith(RENEWAL_PREFIX):
                values = json.loads(message[len(RENEWAL_PREFIX) :])
                if isinstance(values, dict) and isinstance(values.get("wait_ms"), int):
                    renewals.append(values["wait_ms"])
        except ValueError:
            continue
    return bulk, renewals


def parse_samples(lines):
    """The sampler's lines as dicts; malformed lines are skipped.

    Columns, tab-separated: epoch seconds, granted holders, waiters, the
    holders as ``role:state`` (comma-separated, ``-`` for none), the
    waiters' roles (likewise), the ``/proc/stat`` CPU counters
    (space-separated) and the one-minute load average.
    """
    samples = []
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if len(fields) != 7:
            continue
        try:
            samples.append(
                {
                    "epoch": float(fields[0]),
                    "holders": int(fields[1]),
                    "waiters": int(fields[2]),
                    "holder": [] if fields[3] in ("", "-") else fields[3].split(","),
                    "waiting": [] if fields[4] in ("", "-") else fields[4].split(","),
                    "cpu": [int(value) for value in fields[5].split()],
                    "load": float(fields[6]),
                }
            )
        except ValueError:
            continue
    return samples


def cpu_busy(samples):
    """The VM's busy CPU share over the samples, from first to last counters.

    ``/proc/stat``'s first line counts jiffies in user, nice, system, idle,
    iowait, irq, softirq and steal (and guest fields already inside user);
    idle and iowait are the idle time. None with fewer than two samples.
    """
    usable = [sample["cpu"] for sample in samples if len(sample["cpu"]) >= 5]
    if len(usable) < 2:
        return None
    first, last = usable[0], usable[-1]
    width = min(len(first), len(last), 8)
    total = sum(last[:width]) - sum(first[:width])
    idle = (last[3] + last[4]) - (first[3] + first[4])
    return None if total <= 0 else round(1 - idle / total, 3)


def lock_summary(samples):
    """Shares of samples in which the work-order lock was held and waited on."""
    if not samples:
        return {"samples": 0}
    held = [sample for sample in samples if sample["holders"]]
    states, roles = {}, {}
    for sample in held:
        for holder in sample["holder"]:
            role, _, state = holder.partition(":")
            states[state] = states.get(state, 0) + 1
            roles[role] = roles.get(role, 0) + 1
    waiting = {}
    for sample in samples:
        for role in sample["waiting"]:
            waiting[role] = waiting.get(role, 0) + 1
    count = len(samples)
    return {
        "samples": count,
        "held_share": round(len(held) / count, 3),
        "waited_share": round(
            sum(1 for sample in samples if sample["waiters"]) / count, 3
        ),
        "mean_waiters": round(sum(sample["waiters"] for sample in samples) / count, 2),
        "max_waiters": max(sample["waiters"] for sample in samples),
        "holder_states": dict(sorted(states.items())),
        "holder_roles": dict(sorted(roles.items())),
        "waiter_roles": dict(sorted(waiting.items())),
        "cpu_busy": cpu_busy(samples),
        "max_load": max(sample["load"] for sample in samples),
    }


def hold_summary(bulk):
    """Per kind: batches, items, and the hold, item and work times (ms)."""
    summary = {}
    for kind in KINDS:
        rows = [row for row in bulk if row.get("kind") == kind]
        if not rows:
            continue
        summary[kind] = {
            "batches": len(rows),
            "items": sum(row.get("items", 0) for row in rows),
            "tried": sum(row.get("tried", 0) for row in rows),
            "items_per_batch": round(
                sum(row.get("items", 0) for row in rows) / len(rows), 2
            ),
            "hold_ms": percentiles([row.get("hold_ms", 0) for row in rows]),
            # Every batch's hold added up: the lock time per message (PR 4).
            "hold_total_ms": sum(row.get("hold_ms", 0) for row in rows),
            "item_ms": percentiles(
                [value for row in rows for value in row.get("item_ms", [])]
            ),
            "work_ms": percentiles(
                [value for row in rows for value in row.get("work_ms", [])]
            ),
            # Builds made outside the lock before each batch (BG-12 PR 2).
            "build_ms": percentiles(
                [value for row in rows for value in row.get("build_ms", [])]
            ),
            "prebuilt": sum(row.get("prebuilt", 0) for row in rows),
            "rebuilt": sum(row.get("rebuilt", 0) for row in rows),
            "unbuilt": sum(row.get("unbuilt", 0) for row in rows),
            "consumers": _consumers(rows),
        }
    return summary


def _consumers(rows):
    """Per process label: items, prebuilt, rebuilt and unbuilt (BG-12).

    With two mail consumers both building the head of the queue, the
    rehearsal compares their counts to see how often one consumer's builds
    were taken first by the other's batch (its sends then go unbuilt).
    Lines written before the label existed are left out.
    """
    counts = {}
    for row in rows:
        label = row.get("consumer")
        if not isinstance(label, str):
            continue
        entry = counts.setdefault(
            label, {"items": 0, "prebuilt": 0, "rebuilt": 0, "unbuilt": 0}
        )
        for name in entry:
            entry[name] += row.get(name, 0)
    return dict(sorted(counts.items()))


def _instant(value):
    """A datetime from an ISO string, or None."""
    return None if value is None else datetime.fromisoformat(value)


def _minutes(start, end):
    """Minutes from ``start`` to ``end``, or None when either is missing."""
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 60


def _rate(count, minutes):
    """``count`` per minute over ``minutes``, or None."""
    if not minutes or minutes <= 0:
        return None
    return round(count / minutes, 1)


def send_summary(meta, measure):
    """Timing, throughput and correctness of the send, from the measure step."""
    due = _instant(measure.get("due_at") or meta.get("due_at"))
    outcomes = measure.get("outcomes", [])
    messages = measure.get("messages", [])
    occurrences = measure.get("occurrences", [])
    # Accepted messages, by id: a message accepted twice is counted once
    # here and fails the run below (it reached the provider twice).
    acceptances = {}
    for row in outcomes:
        if row.get("reason") == "smtp_accepted":
            acceptances[row["message_id"]] = acceptances.get(row["message_id"], 0) + 1
    accepted = sorted(acceptances)
    settled = [_instant(row["settled_at"]) for row in outcomes if row.get("settled_at")]
    submitted = [
        _instant(row["submitted_at"]) for row in outcomes if row.get("submitted_at")
    ]
    created = [_instant(row["created_at"]) for row in messages if row.get("created_at")]
    # A bulk Production Reminder-only run is the one prepared ahead.
    ahead = (
        meta.get("bulk") is True
        and set(measure.get("kinds", [])) == {"reminder"}
        and set(measure.get("modes", [])) == {"production"}
    )
    last = max(settled, default=None)
    first_submit = min(submitted, default=None)
    resumed = _instant(meta.get("dispatch_started_at"))
    # Send-only runs: sending starts when mail-dispatch starts again (or at
    # the first submission, if that is later); otherwise at the due time.
    send_start = max(filter(None, (resumed, first_submit)), default=None)
    due_minutes = _minutes(due, last)
    send_minutes = _minutes(send_start, last) if resumed else None
    prepare_minutes = _minutes(due, max(created, default=None))
    states, message_states = {}, {}
    for row in occurrences:
        states[row["state"]] = states.get(row["state"], 0) + 1
    for row in messages:
        message_states[row["state"]] = message_states.get(row["state"], 0) + 1
    phases = {
        name: percentiles(
            [
                row["stats"][name]
                for row in outcomes
                if isinstance(row.get("stats"), dict)
                and isinstance(row["stats"].get(name), int)
            ]
        )
        for name in PHASES
    }
    # One count per occurrence, whether its own state or its message's says
    # delivery_unknown (they usually both do).
    unknown_messages = {
        row.get("occurrence_id") or row["id"]
        for row in messages
        if row["state"] == "delivery_unknown"
    }
    unknown = len(
        unknown_messages
        | {row["id"] for row in occurrences if row["state"] == "delivery_unknown"}
    )
    failures = (
        message_states.get("permanent_failure", 0)
        + states.get("failed", 0)
        + sum(row.get("count", 0) for row in measure.get("failed_tasks", []))
    )
    unfinished = sum(
        count for state, count in states.items() if state not in FINAL_OCCURRENCE_STATES
    )
    candidates = (
        len(occurrences) - states.get("skipped", 0) - states.get("coalesced", 0)
    )
    correct = {
        "candidates": candidates,
        "messages": len(messages),
        "accepted": len(accepted),
        "accepted_twice": sum(1 for n in acceptances.values() if n > 1),
        "two_messages": measure.get("targets_with_two_messages", 0),
        "two_fulfillments": measure.get("targets_with_two_fulfillments", 0),
        "delivery_unknown": unknown,
        "failures": failures,
        "unfinished": unfinished,
        "deadlocks": _deadlocks(meta),
        "timed_out": bool(meta.get("timed_out")),
        "invariant_violations": {
            label: count
            for label, count in measure.get("invariant_violations", {}).items()
            if not (ahead and label == CREATION_INVARIANT)
        },
        # The run's messages created earlier than they may be: before the
        # due time, or before the lead for a Reminder prepared ahead.
        "created_too_early": sum(
            1
            for at in created
            if due and at < due - (PREPARE_AHEAD if ahead else timedelta())
        ),
        # Prepared ahead or not, nothing is SENT before its due time.
        "submitted_before_due": sum(1 for at in submitted if due and at < due),
    }
    correct["passed"] = (
        candidates > 0
        and len(accepted) == candidates
        and not correct["accepted_twice"]
        and not correct["invariant_violations"]
        and not correct["created_too_early"]
        and not correct["submitted_before_due"]
        and not correct["two_messages"]
        and not correct["two_fulfillments"]
        and not unknown
        and not failures
        and not unfinished
        and not correct["timed_out"]
    )
    return {
        "due_at": _iso(due),
        "kinds": measure.get("kinds", []),
        "modes": measure.get("modes", []),
        "first_submit": _iso(first_submit),
        "last_outcome": _iso(last),
        "due_to_last_outcome_minutes": _round(due_minutes),
        "accepted_per_minute": _rate(len(accepted), due_minutes),
        "preparation_minutes": _round(prepare_minutes),
        "prepared_per_minute": _rate(len(messages), prepare_minutes),
        "send_only_minutes": _round(send_minutes),
        "send_only_per_minute": _rate(len(accepted), send_minutes),
        "occurrence_states": dict(sorted(states.items())),
        "message_states": dict(sorted(message_states.items())),
        "phases": phases,
        "correctness": correct,
    }


def _iso(value):
    """An ISO string, or None."""
    return None if value is None else value.isoformat()


def _round(value):
    """Two decimals, or None."""
    return None if value is None else round(value, 2)


def _deadlocks(meta):
    """Deadlocks (SQLSTATE 40P01) the database counted during the run, or None."""
    before, after = meta.get("deadlocks_before"), meta.get("deadlocks_after")
    if not isinstance(before, int) or not isinstance(after, int):
        return None
    return after - before


def summarize(meta, measure, timing_lines, sample_lines, extras=None):
    """Every measurement of the run, as one JSON-serializable document.

    ``extras`` holds the full-size rehearsal's optional inputs (BG-12 PR 4,
    ``read_extras``): the probe lines, the pause and resume answers, the
    credential fingerprints and the Mailpit audit. A run without them (the
    PR 1 rehearsal) reports exactly what it did before.
    """
    extras = extras or {}
    bulk, renewals = parse_timings(timing_lines)
    samples = parse_samples(sample_lines)
    send = send_summary(meta, measure)
    holds = hold_summary(bulk)
    full = full_summary(meta, measure, send, holds, extras)
    if full:
        send["correctness"] = full.pop("correctness")
    return {
        "run": {
            key: meta.get(key)
            for key in (
                "label",
                "image",
                "harness_image",
                "families",
                "bulk",
                "smtp_latency_ms",
                "mail_consumers",
                "send_only",
                "started_at",
                "dispatch_stopped_at",
                "dispatch_started_at",
                "finished_at",
                "timed_out",
            )
        }
        | {
            "mailpit_messages": (
                meta["mailpit_after"] - meta["mailpit_before"]
                if isinstance(meta.get("mailpit_after"), int)
                and isinstance(meta.get("mailpit_before"), int)
                else None
            )
        },
        "send": send,
        "holds": holds,
        "renewal_wait_ms": percentiles(renewals),
        "locks": lock_summary(samples),
    } | ({"full": full} if full else {})


# ---------------------------------------------------------------------------
# The full-size rehearsal's additions (BG-12 PR 4, #447).

# The probe's steps, in the order a Family takes them (spike.STEPS).
PROBE_STEPS = ("access", "portal", "form", "presence", "submit")


def read_extras(root):
    """The full-size rehearsal's optional files in a run directory.

    ``probe.jsonl`` (one line per probe), ``pause.json`` and ``resume.json``
    (the control steps' answers), ``credentials-before.tsv`` and
    ``credentials-after.tsv`` (the fingerprint rows) and ``audit.json`` (the
    Mailpit audit). A missing or unreadable file is simply absent.
    """
    extras = {"probes": []}
    for line in _lines(root / "probe.jsonl"):
        with suppress(ValueError):
            row = json.loads(line)
            if isinstance(row, dict) and isinstance(row.get("steps"), dict):
                extras["probes"].append(row)
    for name in ("pause", "resume", "audit"):
        with suppress(OSError, ValueError):
            value = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
            if isinstance(value, dict):
                extras[name] = value
    for name in ("before", "after"):
        rows = [line.split("\t") for line in _lines(root / f"credentials-{name}.tsv")]
        rows = [row for row in rows if len(row) == 3]
        if rows:
            extras[f"credentials_{name}"] = {
                table: {"rows": count, "digest": digest}
                for table, count, digest in rows
            }
    return extras


def probe_summary(probes):
    """Per step: the seconds of the probes it answered, and every outcome."""
    steps = {}
    for name in PROBE_STEPS:
        seconds, outcomes = [], {}
        for probe in probes:
            step = probe["steps"].get(name)
            if not isinstance(step, dict):
                continue
            outcome = step.get("outcome")
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if outcome == "ok" and isinstance(step.get("seconds"), (int, float)):
                seconds.append(step["seconds"])
        steps[name] = {
            "seconds": percentiles(seconds),
            "outcomes": dict(sorted(outcomes.items(), key=str)),
        }
    failed = sum(
        1
        for probe in probes
        if any(
            isinstance(step, dict) and step.get("outcome") != "ok"
            for step in probe["steps"].values()
        )
    )
    return {"probes": len(probes), "failed_probes": failed, "steps": steps}


def pause_summary(measure, pause, resume):
    """How the pause and resume went, from the control answers and outcomes.

    ``settled_while_paused`` counts outcomes recorded after the pause was
    confirmed and before the resume was: the messages already submitting
    when the pause took hold, which finish rather than being abandoned.
    ``pause_to_quiet_seconds`` is the pause to the last of those, and
    ``resume_to_first_outcome_seconds`` the resume to the next outcome.
    """
    if not pause:
        return None
    paused = _instant(pause.get("confirmed_at"))
    resumed = _instant((resume or {}).get("confirmed_at"))
    settled = sorted(
        _instant(row["settled_at"])
        for row in measure.get("outcomes", [])
        if row.get("settled_at")
    )
    before = [at for at in settled if paused and at <= paused]
    during = [
        at for at in settled if paused and at > paused and (not resumed or at < resumed)
    ]
    after = [at for at in settled if resumed and at >= resumed]
    return {
        "paused": bool(pause.get("paused")),
        "resumed": resume is not None and resume.get("paused") is False,
        "outcomes_before_pause": len(before),
        "settled_while_paused": len(during),
        "outcomes_after_resume": len(after),
        "paused_seconds": _seconds(paused, resumed),
        "pause_to_quiet_seconds": _seconds(paused, max(during, default=None)),
        "resume_quiet_wait_seconds": (resume or {}).get("quiet_seconds"),
        "resume_sender_test_seconds": (resume or {}).get("test_seconds"),
        "resume_to_first_outcome_seconds": _seconds(resumed, min(after, default=None)),
    }


def _seconds(start, end):
    """Seconds from ``start`` to ``end``, or None when either is missing."""
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 1)


def lock_per_message(holds, accepted):
    """The work-order lock time the bulk path's batches held per message.

    The sum of every prepare, commit and outcome batch's hold, divided by
    the messages accepted, plus each kind's own share; None without timing
    lines (the one-at-a-time path, or debug logging off) or acceptances.
    """
    if not holds or not accepted:
        return None
    totals = {kind: values["hold_total_ms"] for kind, values in holds.items()}
    return {
        "total_ms_per_message": round(sum(totals.values()) / accepted, 1),
        "by_kind_ms_per_message": {
            kind: round(total / accepted, 1) for kind, total in totals.items()
        },
    }


def full_summary(meta, measure, send, holds, extras):
    """The full-size rehearsal's measurements and its stricter correctness.

    Empty for a run without any of the full-size inputs. Otherwise the
    correctness check also requires identical credential fingerprints
    before and after (no code, MAC, link token or generation changed), a
    Mailpit audit with no Family link in two messages and none that had not
    been emailed before the run, and, when a pause was asked for, a pause
    that took hold and a resume that was confirmed.
    """
    present = any(
        extras.get(name)
        for name in (
            "probes",
            "pause",
            "audit",
            "credentials_before",
            "credentials_after",
        )
    )
    if not present and not meta.get("pause_after") and not meta.get("probe_every"):
        return {}
    correct = dict(send["correctness"])
    before, after = extras.get("credentials_before"), extras.get("credentials_after")
    correct["credentials_unchanged"] = (
        before is not None and after is not None and before == after
    )
    audit = extras.get("audit")
    correct["mail_audit"] = audit is not None
    correct["links_in_two_messages"] = (audit or {}).get("links_in_two_messages")
    correct["links_not_emailed_before"] = (audit or {}).get("links_not_emailed_before")
    # Every accepted Family message carries exactly one Family link (the
    # resume's sender test and receipts carry none), so the distinct links
    # the run's mail carried must equal the accepted messages.
    correct["links_match_accepted"] = (audit or {}).get("run_links") == send[
        "correctness"
    ]["accepted"]
    pause = pause_summary(measure, extras.get("pause"), extras.get("resume"))
    wanted_pause = bool(meta.get("pause_after"))
    correct["pause_resumed"] = (
        None
        if not wanted_pause
        else bool(pause and pause["paused"] and pause["resumed"])
    )
    correct["passed"] = (
        send["correctness"]["passed"]
        and correct["credentials_unchanged"]
        and audit is not None
        and audit.get("links_in_two_messages") == 0
        and audit.get("links_not_emailed_before") == 0
        and correct["links_match_accepted"]
        and (not wanted_pause or correct["pause_resumed"])
    )
    accepted = send["correctness"]["accepted"]
    paused_seconds = (pause or {}).get("paused_seconds") or 0
    due_minutes = send.get("due_to_last_outcome_minutes")
    active_minutes = (
        None if due_minutes is None else round(due_minutes - paused_seconds / 60, 2)
    )
    return {
        "lock_per_message": lock_per_message(holds, accepted),
        "send_minutes_without_pause": active_minutes,
        "accepted_per_minute_without_pause": _rate(accepted, active_minutes),
        "probes": probe_summary(extras.get("probes", [])),
        "pause": pause,
        "credentials": {
            "before": before,
            "after": after,
            "unchanged": correct["credentials_unchanged"],
        },
        "audit": audit,
        "correctness": correct,
    }


def render_full(full):
    """The full-size rehearsal's part of the text report."""
    lines = ["", "Full-size rehearsal (BG-12 PR 4)"]
    per = full.get("lock_per_message")
    if per:
        kinds = ", ".join(f"{k} {v}" for k, v in per["by_kind_ms_per_message"].items())
        lines.append(
            f"  Lock held per accepted message: {per['total_ms_per_message']} ms "
            f"({kinds})"
        )
    else:
        lines.append("  Lock held per accepted message: no bulk timing lines")
    lines.append(
        f"  Send without the pause: {full['send_minutes_without_pause']} min, "
        f"{full['accepted_per_minute_without_pause']} accepted/min"
    )
    probes = full["probes"]
    lines.append(
        f"  Family probes: {probes['probes']} ({probes['failed_probes']} with a step "
        "that did not answer ok); seconds per step:"
    )
    for name, values in probes["steps"].items():
        lines.append(
            f"    {name:<9} {_p(values['seconds'])} outcomes {values['outcomes']}"
        )
    pause = full.get("pause")
    if pause:
        lines += [
            f"  Pause: paused {pause['paused']}, resumed {pause['resumed']}, "
            f"{pause['outcomes_before_pause']} outcomes before it, "
            f"{pause['settled_while_paused']} settled while paused, "
            f"{pause['outcomes_after_resume']} after the resume",
            f"    paused {pause['paused_seconds']} s; pause to quiet "
            f"{pause['pause_to_quiet_seconds']} s; resume waited "
            f"{pause['resume_quiet_wait_seconds']} s for quiet and "
            f"{pause['resume_sender_test_seconds']} s for its sender test; "
            f"resume to first outcome {pause['resume_to_first_outcome_seconds']} s",
        ]
    credentials = full["credentials"]
    verdict = "unchanged" if credentials["unchanged"] else "CHANGED OR UNREAD"
    lines.append(
        "  Credentials (codes, code MACs, link tokens, generations, rehearsal "
        f"credentials): {verdict}"
    )
    for table, value in (credentials.get("after") or {}).items():
        lines.append(f"    {table}: {value['rows']} rows")
    audit = full.get("audit")
    if audit:
        lines.append(
            f"  Mailpit audit: {audit['run_messages']} messages since the run "
            f"began, {audit['run_links']} Family links, "
            f"{audit['links_in_two_messages']} in two messages, "
            f"{audit['links_not_emailed_before']} not emailed before, "
            f"{audit['run_messages_without_link']} without a link"
        )
    else:
        lines.append("  Mailpit audit: none")
    return lines


# The rows ``compare`` prints: a label and the path into summary.json.
COMPARE_ROWS = (
    ("Image", ("run", "image")),
    ("Families", ("run", "families")),
    ("Candidates", ("send", "correctness", "candidates")),
    ("Accepted", ("send", "correctness", "accepted")),
    ("Due to last outcome (min)", ("send", "due_to_last_outcome_minutes")),
    ("Accepted per minute", ("send", "accepted_per_minute")),
    ("Send without the pause (min)", ("full", "send_minutes_without_pause")),
    ("Accepted/min without the pause", ("full", "accepted_per_minute_without_pause")),
    ("Preparation (min)", ("send", "preparation_minutes")),
    (
        "Lock held per message (ms)",
        ("full", "lock_per_message", "total_ms_per_message"),
    ),
    ("Commit hold p50 (ms)", ("holds", "commit", "hold_ms", "p50")),
    ("Commit hold p90 (ms)", ("holds", "commit", "hold_ms", "p90")),
    ("Commit item p50 (ms)", ("holds", "commit", "item_ms", "p50")),
    ("Prepare hold p50 (ms)", ("holds", "prepare", "hold_ms", "p50")),
    ("Outcome hold p50 (ms)", ("holds", "outcome", "hold_ms", "p50")),
    ("Commit prebuilt", ("holds", "commit", "prebuilt")),
    ("Commit rebuilt", ("holds", "commit", "rebuilt")),
    ("Commit unbuilt", ("holds", "commit", "unbuilt")),
    ("Lock held (share of samples)", ("locks", "held_share")),
    ("Lock waited on (share)", ("locks", "waited_share")),
    ("Submit p50 (ms)", ("send", "phases", "submit_ms", "p50")),
    ("Request p50 (ms)", ("send", "phases", "request_ms", "p50")),
    ("Probe form p50 (s)", ("full", "probes", "steps", "form", "seconds", "p50")),
    ("Probe form p90 (s)", ("full", "probes", "steps", "form", "seconds", "p90")),
    ("Probe form max (s)", ("full", "probes", "steps", "form", "seconds", "max")),
    ("Probe submit p50 (s)", ("full", "probes", "steps", "submit", "seconds", "p50")),
    ("Probe submit p90 (s)", ("full", "probes", "steps", "submit", "seconds", "p90")),
    ("Probe submit max (s)", ("full", "probes", "steps", "submit", "seconds", "max")),
    ("Probes failed", ("full", "probes", "failed_probes")),
    ("Settled while paused", ("full", "pause", "settled_while_paused")),
    ("Pause to quiet (s)", ("full", "pause", "pause_to_quiet_seconds")),
    (
        "Resume to first outcome (s)",
        ("full", "pause", "resume_to_first_outcome_seconds"),
    ),
    ("Credentials unchanged", ("full", "credentials", "unchanged")),
    ("Links in two messages", ("full", "audit", "links_in_two_messages")),
    ("Links not emailed before", ("full", "audit", "links_not_emailed_before")),
    ("delivery_unknown", ("send", "correctness", "delivery_unknown")),
    ("Deadlocks (40P01)", ("send", "correctness", "deadlocks")),
    ("Correctness passed", ("send", "correctness", "passed")),
)


def _dig(document, path):
    """The value at ``path`` in nested dicts, or None."""
    for key in path:
        if not isinstance(document, dict):
            return None
        document = document.get(key)
    return document


def compare(baseline, candidate):
    """Two runs' summaries side by side, as text (one row per COMPARE_ROWS)."""
    labels = (
        _dig(baseline, ("run", "label")) or "baseline",
        _dig(candidate, ("run", "label")) or "candidate",
    )
    width = max(len(label) for label, _ in COMPARE_ROWS)
    lines = [f"{'':<{width}}  {labels[0]:>24}  {labels[1]:>24}"]
    for label, path in COMPARE_ROWS:
        values = [_dig(baseline, path), _dig(candidate, path)]
        shown = ["-" if value is None else str(value) for value in values]
        if label == "Image":
            # Image tags are long; the commit part is what tells them apart.
            shown = [value.split(":")[-1][:20] for value in shown]
        lines.append(f"{label:<{width}}  {shown[0]:>24}  {shown[1]:>24}")
    return "\n".join(lines) + "\n"


def execute_rehearsal_compare(baseline, candidate):
    """Console entry (``local-rehearsal-compare``): print two runs side by side.

    Each directory must hold the ``summary.json`` its report wrote. Exits 0
    when both runs passed their correctness checks, 1 when either did not,
    and 2 when a summary cannot be read.
    """
    documents = []
    for directory in (baseline, candidate):
        try:
            documents.append(
                json.loads(
                    (Path(directory) / "summary.json").read_text(encoding="utf-8")
                )
            )
        except (OSError, ValueError):
            print(
                "ERROR: each run directory needs a readable summary.json",
                file=sys.stderr,
            )
            return 2
    sys.stdout.write(compare(*documents))
    passed = all(
        _dig(document, ("send", "correctness", "passed")) for document in documents
    )
    return 0 if passed else 1


def _p(values):
    """One percentile summary as text."""
    if not values.get("n"):
        return "n=0"
    return "n={n} p50={p50} p90={p90} p99={p99} max={max}".format(**values)


def render(summary):
    """The summary as the text the operator reads at the end of a run."""
    run, send, locks = summary["run"], summary["send"], summary["locks"]
    correct = send["correctness"]
    lines = [
        f"Rehearsal {run.get('label') or ''} ({run.get('image')})",
        f"  Families {run.get('families')}, bulk {run.get('bulk')}, SMTP latency "
        f"{run.get('smtp_latency_ms')} ms, mail consumers {run.get('mail_consumers')}, "
        f"send-only {run.get('send_only')}",
        f"  Kinds {', '.join(send['kinds']) or '-'}, modes "
        f"{', '.join(send['modes']) or '-'}, due {send['due_at']}",
        "",
        "Time and throughput",
        f"  Due to last outcome: {send['due_to_last_outcome_minutes']} min, "
        f"{send['accepted_per_minute']} accepted/min",
        f"  Preparation (due to last message created; negative when prepared "
        f"ahead): {send['preparation_minutes']} min, "
        f"{send['prepared_per_minute']} prepared/min",
        f"  Send only (mail-dispatch restart to last outcome): "
        f"{send['send_only_minutes']} min, {send['send_only_per_minute']}/min",
        "",
        "Send statistics (ms; the mail send report's phases)",
    ]
    lines += [f"  {name:<11} {_p(values)}" for name, values in send["phases"].items()]
    lines += ["", "Work-order lock holds (bulk path; ms)"]
    if not summary["holds"]:
        lines.append("  none logged (one-at-a-time path, or debug logging off)")
    for kind, values in summary["holds"].items():
        lines += [
            f"  {kind}: {values['batches']} batches, {values['items']} items "
            f"({values['items_per_batch']}/batch, {values['tried']} tried), "
            f"prebuilt {values['prebuilt']}, rebuilt {values['rebuilt']}, "
            f"unbuilt {values['unbuilt']}",
        ]
        lines += [
            f"    consumer {label}: {entry['items']} items, prebuilt "
            f"{entry['prebuilt']}, rebuilt {entry['rebuilt']}, "
            f"unbuilt {entry['unbuilt']}"
            for label, entry in values["consumers"].items()
        ]
        lines += [
            f"    hold    {_p(values['hold_ms'])}",
            f"    item    {_p(values['item_ms'])}",
            f"    work    {_p(values['work_ms'])}",
            f"    build   {_p(values['build_ms'])} (outside the lock)",
        ]
    lines += [
        "",
        f"Lease renewal waits (ms): {_p(summary['renewal_wait_ms'])}",
        "",
        "Lock samples (one a second)",
    ]
    if locks.get("samples"):
        lines += [
            f"  {locks['samples']} samples: held {locks['held_share']:.1%}, waited on "
            f"{locks['waited_share']:.1%}, mean waiters {locks['mean_waiters']}, "
            f"max {locks['max_waiters']}",
            f"  holder states {locks['holder_states']}",
            f"  holder roles {locks['holder_roles']}",
            f"  waiter roles {locks['waiter_roles']}",
            f"  CPU busy {locks['cpu_busy']}, max load {locks['max_load']}",
        ]
    else:
        lines.append("  none")
    if summary.get("full"):
        lines += render_full(summary["full"])
    lines += [
        "",
        "Correctness " + ("PASSED" if correct["passed"] else "FAILED"),
        f"  candidates {correct['candidates']}, messages {correct['messages']}, "
        f"accepted {correct['accepted']}, Mailpit {run.get('mailpit_messages')}",
        f"  accepted twice {correct['accepted_twice']}, "
        f"two messages {correct['two_messages']}, two fulfillments "
        f"{correct['two_fulfillments']}, delivery_unknown "
        f"{correct['delivery_unknown']}, failures {correct['failures']}, "
        f"unfinished {correct['unfinished']}",
        f"  deadlocks (40P01) {correct['deadlocks']}, timed out {correct['timed_out']}",
        f"  ordering invariant violations {correct['invariant_violations'] or 'none'}, "
        f"created too early {correct['created_too_early']}, "
        f"submitted before the due time {correct['submitted_before_due']}",
        f"  occurrence states {send['occurrence_states']}",
        f"  message states {send['message_states']}",
    ]
    if "credentials_unchanged" in correct:
        lines.append(
            f"  credentials unchanged {correct['credentials_unchanged']}, Mailpit "
            f"links in two messages {correct['links_in_two_messages']}, not emailed "
            f"before {correct['links_not_emailed_before']}, links match accepted "
            f"{correct['links_match_accepted']}, pause and resume "
            f"{correct['pause_resumed']}"
        )
    return "\n".join(lines) + "\n"


def _lines(path):
    """A text file's lines, or none when it is missing."""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def execute_rehearsal_report(directory):
    """Console entry: print the report for one run directory.

    Exits 0 when the run passed its correctness check, 1 when it did not
    (including a run that timed out), and 2 when the directory lacks its
    records, so the operator script's own exit status says whether the
    rehearsal passed.
    """
    root = Path(directory)
    try:
        meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        measure = json.loads((root / "measure.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(
            "ERROR: the run directory needs readable meta.json and measure.json",
            file=sys.stderr,
        )
        return 2
    summary = summarize(
        meta,
        measure,
        _lines(root / "timings.jsonl"),
        _lines(root / "locks.tsv"),
        read_extras(root),
    )
    sys.stdout.write(render(summary))
    # A convenience beside the printed report, which the operator script
    # keeps itself; a read-only directory just goes without it.
    with suppress(OSError):
        (root / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if summary["send"]["correctness"]["passed"] else 1
