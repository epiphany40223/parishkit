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
from datetime import datetime
from pathlib import Path

# The DEBUG timing lines' prefixes, as the instrumented code writes them.
BULK_PREFIX = "bulk timing: "
RENEWAL_PREFIX = "lease renewal timing: "
# The send statistics phases the mail send report prints, in its order.
PHASES = ("wait_ms", "request_ms", "submit_ms", "helper_ms", "smtp_ms", "total_ms")
# The bulk timing line kinds, in the order a send goes through them.
KINDS = ("prepare", "commit", "outcome")
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
            "item_ms": percentiles(
                [value for row in rows for value in row.get("item_ms", [])]
            ),
            "work_ms": percentiles(
                [value for row in rows for value in row.get("work_ms", [])]
            ),
            "prebuilt": sum(row.get("prebuilt", 0) for row in rows),
            "rebuilt": sum(row.get("rebuilt", 0) for row in rows),
        }
    return summary


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
        "invariant_violations": measure.get("invariant_violations", {}),
    }
    correct["passed"] = (
        candidates > 0
        and len(accepted) == candidates
        and not correct["accepted_twice"]
        and not correct["invariant_violations"]
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


def summarize(meta, measure, timing_lines, sample_lines):
    """Every measurement of the run, as one JSON-serializable document."""
    bulk, renewals = parse_timings(timing_lines)
    samples = parse_samples(sample_lines)
    return {
        "run": {
            key: meta.get(key)
            for key in (
                "label",
                "image",
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
        "send": send_summary(meta, measure),
        "holds": hold_summary(bulk),
        "renewal_wait_ms": percentiles(renewals),
        "locks": lock_summary(samples),
    }


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
        f"  Preparation (due to last message created): {send['preparation_minutes']}"
        f" min, {send['prepared_per_minute']} prepared/min",
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
            f"prebuilt {values['prebuilt']}, rebuilt {values['rebuilt']}",
            f"    hold    {_p(values['hold_ms'])}",
            f"    item    {_p(values['item_ms'])}",
            f"    work    {_p(values['work_ms'])}",
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
        f"  ordering invariant violations {correct['invariant_violations'] or 'none'}",
        f"  occurrence states {send['occurrence_states']}",
        f"  message states {send['message_states']}",
    ]
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
        meta, measure, _lines(root / "timings.jsonl"), _lines(root / "locks.tsv")
    )
    sys.stdout.write(render(summary))
    # A convenience beside the printed report, which the operator script
    # keeps itself; a read-only directory just goes without it.
    with suppress(OSError):
        (root / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if summary["send"]["correctness"]["passed"] else 1
