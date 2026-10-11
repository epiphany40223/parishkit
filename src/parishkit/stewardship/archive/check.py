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
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Event, Thread

from parishkit.stewardship.provider_checks import (
    ProviderCheckDrainFailure,
    stop_helper,
)

from .package import MAX_FILE_BYTES
from .workbook import (
    BAND_COUNT,
    FAMILY_ID_SYSTEMS,
    METADATA_KEYS,
    SHEETS,
    STORED_INTEGER_MAX,
    STORED_MONEY_LIMIT,
    STORED_NOTES_BYTES,
    Kind,
    stored_notes_bytes,
)

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
# The keys of a reply's daily row: the sheet's columns but its free-text
# source (the worker drops it and the sheet row).
DAILY_KEYS = frozenset(column.name for column in SHEETS["daily"]) - {"source"}
BAND_KEYS = frozenset(("band", "families", "total"))
# The id systems a kept Family row may name (the table's check constraint).
RENEWAL_SYSTEMS = frozenset(("parishsoft_duid", "pds_fid"))
BIGINT_MAX = 2**63 - 1
SMALLINT_MAX = 2**15 - 1


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
    """Whether the helper's reply is a document of the agreed shape.

    A valid document is checked down to each value the import stores, against
    the stored columns' types and limits (migration 0059), so a reply the
    import would fail to save is "could not be checked" instead.
    """
    if type(document) is not dict or type(document.get("valid")) is not bool:
        return False
    fields = {key: value for key, value in document.items() if key != "valid"}
    if document["valid"]:
        return _shaped(fields, VALID_SHAPE) and _storable(fields)
    return _shaped(fields, {"problems": list, "more_problems": int}) and all(
        _shaped(problem, PROBLEM_SHAPE) for problem in fields["problems"]
    )


def _count(value, low=0, high=STORED_INTEGER_MAX):
    """Whether a value is an int (never a bool) from ``low`` to ``high``."""
    return type(value) is int and low <= value <= high


def _amount(value):
    """Decimal text of a stored amount (numeric(18, 2), not negative), or None."""
    if type(value) is not str:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    if (
        not amount.is_finite()
        or amount.as_tuple().exponent < -2
        or not 0 <= amount < STORED_MONEY_LIMIT
    ):
        return None
    return amount


def _date(value, instant=False):
    """The date (or, with ``instant``, aware datetime) an ISO text gives, or None."""
    if type(value) is not str:
        return None
    try:
        parsed = (datetime if instant else date).fromisoformat(value)
    except ValueError:
        return None
    return None if instant and parsed.tzinfo is None else parsed


def _metadata_value(key, value):
    """Whether one metadata value has its key's type and stored limits."""
    if value is None:
        return key.optional
    if key.kind is Kind.INTEGER:
        return _count(value, key.minimum or 0, key.maximum or STORED_INTEGER_MAX)
    if key.kind is Kind.BOOLEAN:
        return type(value) is bool
    if key.kind is Kind.INSTANT:
        return _date(value, instant=True) is not None
    if key.kind is Kind.DATE:
        return _date(value) is not None
    return type(value) is str and (
        key.max_length is None or len(value) <= key.max_length
    )


def _storable_metadata(metadata):
    """Every defined key with its type and limits, and nothing else."""
    if set(metadata) != {key.name for key in METADATA_KEYS} or not all(
        _metadata_value(key, metadata[key.name]) for key in METADATA_KEYS
    ):
        return False
    start = _date(metadata["campaign_start_date"])
    last = _date(metadata["campaign_last_submission_date"])
    return (
        type(metadata["schema_version"]) is str
        and len(metadata["schema_version"]) <= 16
        and start is not None
        and last is not None
        and start <= last
    )


def _storable_day(row):
    """One daily row: exactly the stored columns, of their types."""
    return (
        type(row) is dict
        and set(row) == DAILY_KEYS
        and _count(row["campaign_day"], 1)
        and _date(row["local_date"]) is not None
        and _count(row["first_submissions"])
        and _count(row["cumulative_families"])
        and _count(row["cumulative_pledging_families"])
        and row["cumulative_pledging_families"] <= row["cumulative_families"]
        and _amount(row["cumulative_annual_pledged"]) is not None
        and type(row["cumulative_pct_of_active"]) in (int, float)
    )


def _storable_band(band):
    """One pledge band: its number, Family count and total."""
    return (
        type(band) is dict
        and set(band) == BAND_KEYS
        and _count(band["band"], 1, BAND_COUNT)
        and _count(band["families"])
        and _amount(band["total"]) is not None
    )


def _storable_renewal(triple):
    """One kept Family row: [id system, id, first campaign day]."""
    return (
        type(triple) is list
        and len(triple) == 3
        and triple[0] in RENEWAL_SYSTEMS
        and _count(triple[1], 1, BIGINT_MAX)
        and _count(triple[2], 1)
    )


def _notes(values):
    """Whether note.* or count.* values map text keys to text or None."""
    return all(
        type(key) is str and (value is None or type(value) is str)
        for key, value in values.items()
    )


def _storable(fields):
    """Whether a valid reply's values all fit what the import stores."""
    daily, bands, renewal = fields["daily"], fields["bands"], fields["renewal_families"]
    systems, quartiles = fields["family_id_systems"], fields["quartiles"]
    counts = ("families", "submission_rows", "unattributed_rows", "ministry_rows")
    if not (
        _storable_metadata(fields["metadata"])
        and _notes(fields["notes"])
        and _notes(fields["other_counts"])
        and stored_notes_bytes({**fields["notes"], **fields["other_counts"]})
        <= STORED_NOTES_BYTES
        and all(type(key) is str for key in fields["flagged_notes"])
        and all(system in FAMILY_ID_SYSTEMS.values() for system in systems)
        and len(set(systems)) == len(systems)
        and all(_count(fields[name]) for name in counts)
        and _count(fields["pledging_families"], 0, fields["families"])
        and _count(fields["minor_version"])
        and _count(fields["band_set_version"], 1, SMALLINT_MAX)
        and _amount(fields["annual_pledged"]) is not None
        and daily
        and all(_storable_day(row) for row in daily)
        and len({row["campaign_day"] for row in daily}) == len(daily)
        and all(_storable_band(band) for band in bands)
        and len({band["band"] for band in bands}) == len(bands)
        and all(_storable_renewal(triple) for triple in renewal)
        and len({(t[0], t[1]) for t in renewal}) == len(renewal)
    ):
        return False
    if quartiles is None:
        return True
    values = [_amount(value) for value in quartiles]
    return len(values) == 3 and None not in values and values == sorted(values)


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
