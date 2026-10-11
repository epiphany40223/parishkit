"""Check archive workbooks in the isolated helper, and ``archive-check`` (#1018).

``check_archive`` hands one workbook's bytes to ``archive.check_worker`` in
a short-lived process, isolated like the chart renderer's helper: ``python
-I`` (no user site, no ``PYTHON*`` variables, no current directory on the
path), an empty environment, stderr discarded, a bounded reply, and a kill
at 60 seconds. The helper itself applies its 1 GiB address-space limit. A
kill or the memory limit is "too large to check"; a kill is logged with the
reviewed timeout name ``archive_check``, its limit and the elapsed time.

The import (``archive.imports``) passes
``provider_checks.helper_timeout_recorder`` as ``on_timeout``, so a kill
during a Review leaves a durable ``helper_timed_out`` entry naming
``archive_check`` and the helper ``archive_check_worker``. The offline
command has no database and keeps the process-log line.

``pk-stewardship archive-check <file>...`` validates workbooks offline with
the same helper and prints one JSON line per file: the summary (counts,
totals and dates, no note text and no Family row) or the located problems.
It needs no database, session or configuration.
"""

import json
import logging
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from threading import Event, Thread

from parishkit.stewardship.provider_checks import (
    ProviderCheckDrainFailure,
    stop_helper,
)

from .package import MAX_FILE_BYTES

# A converted file checks in a few seconds; a minute means a hostile one.
CHECK_SECONDS = 60
# The reply holds aggregates and at most one short row per Family.
MAX_REPLY_BYTES = 64 * 1024 * 1024
TIMEOUT_NAME = "archive_check"
TOO_LARGE_EXIT = 3
COMMAND = (sys.executable, "-I", "-m", "parishkit.stewardship.archive.check_worker")
TOO_LARGE = {
    "code": "too_large_to_check",
    "sheet": None,
    "cell": None,
    "rule": "the file is too large to check",
}
FAILED = {
    "code": "check_failed",
    "sheet": None,
    "cell": None,
    "rule": "the file could not be checked",
}
# Exit codes of archive-check: 0 every file valid; 2 a usage error, before
# any file is read; 3 a file refused or unreadable.
VALID, USAGE, REFUSED = 0, 2, 3
# The shape of the helper's reply, checked before anything reads it: a
# reply of any other shape is "could not be checked", never a traceback.
VALID_SHAPE = {
    "minor_version": int,
    "metadata": dict,
    "notes": dict,
    "other_counts": dict,
    "flagged_notes": list,
    "family_id_systems": list,
    "families": int,
    "submission_rows": int,
    "unattributed_rows": int,
    "ministry_rows": int,
    "pledging_families": int,
    "annual_pledged": str,
    "daily": list,
    "renewal_families": list,
    "band_set_version": int,
    "bands": list,
    "quartiles": (list, type(None)),
}
PROBLEM_SHAPE = {
    "code": str,
    "rule": str,
    "sheet": (str, type(None)),
    "cell": (str, type(None)),
}


def _refused(problem):
    """A refusal document with one fixed problem."""
    return {"valid": False, "problems": [dict(problem)], "more_problems": 0}


def check_archive(data, *, limit_seconds=CHECK_SECONDS, on_timeout=None):
    """The helper's document for one workbook's bytes.

    ``{"valid": True, ...}`` carries the validated rows and aggregates;
    ``{"valid": False, "problems": [...], "more_problems": n}`` the located
    problems. ``on_timeout(stopped)`` runs right after a kill at the deadline
    (by default a process-log line); the kill is then "too large to check".
    """
    if len(data) > MAX_FILE_BYTES:
        return _refused(
            {
                "code": "file_size",
                "sheet": None,
                "cell": None,
                "rule": "the file is larger than 20 MB",
            }
        )
    started = time.monotonic()
    if on_timeout is None:
        on_timeout = _timeout_logger(started, limit_seconds)
    try:
        process = subprocess.Popen(
            list(COMMAND),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            # Nothing of this process's environment, and openpyxl's stdlib
            # parser: lxml would honour encodings such as UTF-7 or EBCDIC
            # that the package checks' byte search cannot see through.
            env={"OPENPYXL_LXML": "False"},
        )
    except OSError:
        return _refused(FAILED)
    try:
        state, stopped = _exchange(process, data, started + limit_seconds)
    finally:
        stop_helper(process)
        for stream in (process.stdin, process.stdout):
            with suppress(Exception):
                stream.close()
    if stopped is not None:
        with suppress(Exception):
            on_timeout(stopped)
        return _refused(TOO_LARGE)
    if state.get("returncode") == TOO_LARGE_EXIT:
        return _refused(TOO_LARGE)
    reply = state.get("data")
    if state.get("returncode") != 0 or not reply or len(reply) > MAX_REPLY_BYTES:
        return _refused(FAILED)
    try:
        document = json.loads(reply)
    except ValueError:
        return _refused(FAILED)
    return document if _well_formed(document) else _refused(FAILED)


def _shaped(value, shape):
    """Whether a dict has exactly the keys of ``shape``, of those types.

    ``bool`` is never accepted where an ``int`` is expected.
    """
    return (
        type(value) is dict
        and set(value) == set(shape)
        and all(
            type(value[key]) in (kinds if isinstance(kinds, tuple) else (kinds,))
            for key, kinds in shape.items()
        )
    )


def _well_formed(document):
    """Whether the helper's reply is a document of the agreed shape."""
    if type(document) is not dict or type(document.get("valid")) is not bool:
        return False
    fields = {key: value for key, value in document.items() if key != "valid"}
    if document["valid"]:
        return _shaped(fields, VALID_SHAPE) and all(
            type(row) is dict for row in fields["daily"]
        )
    return _shaped(fields, {"problems": list, "more_problems": int}) and all(
        _shaped(problem, PROBLEM_SHAPE) for problem in fields["problems"]
    )


def _exchange(process, data, deadline):
    """Write the bytes and read the bounded reply until the deadline.

    One pump thread owns both pipes, so a blocked write or read cannot stall
    the deadline, as in ``reports.chart_rendering``. Returns the pump's state
    and the moment a deadline kill happened, or None.
    """
    state = {}
    done = Event()

    def pump():
        """Send the workbook, read the bounded reply, reap a finished helper."""
        try:
            process.stdin.write(data)
            process.stdin.close()
            state["data"] = process.stdout.read(MAX_REPLY_BYTES + 1)
            if len(state["data"]) <= MAX_REPLY_BYTES:
                state["returncode"] = process.wait()
        except Exception:
            # Keep pipe errors private; a missing result is a failed check.
            pass
        finally:
            done.set()

    thread = Thread(target=pump, name="archive-check", daemon=True)
    thread.start()
    finished = done.wait(max(0, deadline - time.monotonic()))
    stopped = None if finished else time.monotonic()
    # Killing ends the pump's read or wait, so ``state`` is final once joined.
    stop_helper(process)
    thread.join(timeout=5)
    if thread.is_alive():
        raise ProviderCheckDrainFailure("Archive check helper could not drain.")
    return state, stopped


def _timeout_logger(started, limit_seconds):
    """The default kill hook: one process-log line naming limit and elapsed."""

    def record(stopped):
        """Log the kill under the reviewed timeout name."""
        from parishkit.stewardship.observability import Event as LogEvent
        from parishkit.stewardship.observability import emit

        emit(
            LogEvent.HELPER_TIMED_OUT,
            level=logging.WARNING,
            timeout=TIMEOUT_NAME,
            limit_seconds=int(limit_seconds),
            elapsed_seconds=int(stopped - started),
        )

    return record


SUMMARY_KEYS = (
    "schema_version",
    "campaign_name",
    "campaign_year",
    "is_final",
    "as_of",
    "timezone",
    "campaign_start_date",
    "campaign_last_submission_date",
    "active_families",
    "source_system",
)


def summary(document):
    """What archive-check prints for a valid file: counts, totals and dates.

    No note text, no Family row, and no pledge band, quartile, pledging
    Family count or average: command output leaves the Admin portal, where
    the comparison report's small-count protection applies.
    """
    metadata = document["metadata"]
    return {
        **{key: metadata.get(key) for key in SUMMARY_KEYS},
        "days": len(document["daily"]),
        "families_responded": document["families"],
        "annual_pledged": document["annual_pledged"],
        "submission_rows": document["submission_rows"],
        "unattributed_rows": document["unattributed_rows"],
        "ministry_rows": document["ministry_rows"],
        "family_id_systems": document["family_id_systems"],
        "notes": len(document["notes"]),
        "notes_flagged": len(document["flagged_notes"]),
    }


def _report(path):
    """The archive-check line for one file argument, and whether it passed."""
    name = Path(path).name
    try:
        with open(path, "rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        return {"file": name, "valid": False, "readable": False}, False
    document = check_archive(data)
    if document["valid"]:
        return {"file": name, "valid": True, "summary": summary(document)}, True
    problems = document.get("problems", [])
    return {
        "file": name,
        "valid": False,
        "problems": [{"code": p["code"], "text": _problem_text(p)} for p in problems],
        "more_problems": document.get("more_problems", 0),
    }, False


def _problem_text(problem):
    """One problem as a line, such as ``families!J17: <rule>``."""
    if problem.get("sheet") is None:
        return problem["rule"]
    where = problem["sheet"]
    if problem.get("cell"):
        where += "!" + problem["cell"]
    return f"{where}: {problem['rule']}"


def main(argv):
    """``pk-stewardship archive-check <file>...``: one JSON line per file.

    Exits 0 when every file is valid, 3 when any is refused or unreadable,
    and 2 on a usage error.
    """
    from parishkit.stewardship.arguments import StewardshipArgumentParser
    from parishkit.stewardship.observability import configure_logging

    parser = StewardshipArgumentParser(
        "pk-stewardship archive-check",
        description=(
            "Validate campaign archive workbooks offline; print each file's "
            "summary or located problems"
        ),
        error_hints={"files": "archive-check requires one or more .xlsx files"},
    )
    parser.add_argument("files", nargs="+", metavar="file")
    args = parser.parse_args(argv)
    configure_logging()
    passed = True
    for path in args.files:
        line, ok = _report(path)
        passed = passed and ok
        print(json.dumps(line, sort_keys=True), flush=True)
    return VALID if passed else REFUSED
