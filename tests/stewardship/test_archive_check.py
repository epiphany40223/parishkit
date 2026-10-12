"""The isolated archive check helper and ``pk-stewardship archive-check`` (#1018).

The helper runs for real (``python -I -m``, empty environment) on synthetic
workbooks; stand-in helpers play a hung, failing or memory-starved one.
"""

import json
import logging
import subprocess
import sys

import pytest

from parishkit.stewardship import observability
from parishkit.stewardship.archive import check, check_worker
from parishkit.stewardship.cli import main

from .archive_factory import campaign, workbook_bytes

SECRET = "Zq-secret-7731"


def helper(monkeypatch, code):
    """Replace the helper with a stand-in program."""
    monkeypatch.setattr(check, "COMMAND", (sys.executable, "-I", "-c", code))


@pytest.fixture(autouse=True)
def quiet_logging(monkeypatch):
    """The command's logging setup is a no-op in tests."""
    monkeypatch.setattr(observability, "configure_logging", lambda *a, **k: None)


def test_a_valid_workbook_returns_rows_and_aggregates():
    """The real helper validates and returns the typed rows the import keeps."""
    document = check.check_archive(workbook_bytes())
    assert document["valid"] is True
    assert document["metadata"]["campaign_year"] == 2055
    assert document["families"] == 7 and document["pledging_families"] == 3
    assert document["annual_pledged"] == "12660.75"
    assert len(document["daily"]) == 6
    assert document["daily"][-1]["cumulative_annual_pledged"] == "12660.75"
    assert ["parishsoft_duid", 1001, 1] in document["renewal_families"]
    assert document["band_set_version"] == 1
    assert document["quartiles"] == ["1330.38", "2400.50", "6200.25"]


def test_a_refused_workbook_returns_located_problems():
    """Problems come back as codes, cells and fixed rules."""
    metadata, sheets = campaign()
    sheets["families"][0]["frequency"] = "weekly"
    document = check.check_archive(workbook_bytes(metadata, sheets))
    assert document["valid"] is False
    assert {p["code"] for p in document["problems"]} == {"family"}


def test_the_helper_runs_isolated(monkeypatch):
    """-I, an empty environment, stderr discarded, like every other helper."""
    launched = {}

    def spy(command, **options):
        launched.update(options, command=command)
        raise OSError

    monkeypatch.setattr(subprocess, "Popen", spy)
    document = check.check_archive(b"PK")
    assert launched["command"][1:3] == ["-I", "-m"]
    assert launched["env"] == {"OPENPYXL_LXML": "False"}
    assert launched["close_fds"] is True
    assert launched["stderr"] is subprocess.DEVNULL
    assert document["problems"][0]["code"] == "check_failed"


def test_a_file_over_the_limit_never_starts_the_helper(monkeypatch):
    """Over 20 MB is refused before anything runs."""
    monkeypatch.setattr(subprocess, "Popen", pytest.fail)
    document = check.check_archive(b"x" * (20 * 1024 * 1024 + 1))
    assert document["problems"][0]["code"] == "file_size"


def test_a_helper_killed_at_its_limit_is_too_large_and_logged(monkeypatch, caplog):
    """The kill is logged with the timeout name, its limit and elapsed time."""
    helper(monkeypatch, "import time; time.sleep(30)")
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    document = check.check_archive(b"PK", limit_seconds=1)
    assert document["problems"][0]["code"] == "too_large_to_check"
    lines = [
        json.loads(observability.SafeJsonFormatter().format(record))
        for record in caplog.records
    ]
    [line] = [line for line in lines if "timeout" in line.get("extra", {})]
    assert "helper_timed_out" in json.dumps(line)
    assert line["extra"]["timeout"] == "archive_check"
    assert line["extra"]["limit_seconds"] == 1
    assert line["extra"]["elapsed_seconds"] in (1, 2)


def test_a_custom_timeout_hook_receives_the_kill(monkeypatch):
    """The import (slice 2) passes the durable recorder here."""
    helper(monkeypatch, "import time; time.sleep(30)")
    stopped = []
    check.check_archive(b"PK", limit_seconds=1, on_timeout=stopped.append)
    assert len(stopped) == 1


def test_the_memory_limit_exit_is_too_large(monkeypatch):
    """Exit 3 from the helper means its memory limit was reached."""
    helper(monkeypatch, "import sys; sys.stdin.buffer.read(); sys.exit(3)")
    document = check.check_archive(b"PK")
    assert document["problems"][0]["code"] == "too_large_to_check"


@pytest.mark.parametrize(
    "code",
    [
        "import sys; sys.stderr.write('secret'); sys.exit(1)",
        "import sys; sys.stdin.buffer.read(); sys.stdout.write('not json')",
        "import sys; sys.stdin.buffer.read(); sys.stdout.write('[1]')",
    ],
)
def test_a_failing_helper_is_a_fixed_problem(monkeypatch, code):
    """No helper text is passed on."""
    helper(monkeypatch, code)
    document = check.check_archive(b"PK")
    assert document["problems"] == [check.FAILED]


@pytest.mark.parametrize(
    "reply",
    [
        {"valid": True},
        {"valid": True, "metadata": {}, "extra": 1},
        {"valid": False, "problems": [{"code": 1}], "more_problems": 0},
        {"valid": False, "problems": [], "more_problems": True},
        {"valid": False, "problems": "x", "more_problems": 0},
        {"valid": "yes"},
    ],
)
def test_a_reply_of_the_wrong_shape_is_a_fixed_problem(monkeypatch, reply):
    """Missing keys or wrong types are "could not be checked", no traceback."""
    text = json.dumps(reply)
    helper(
        monkeypatch,
        f"import sys; sys.stdin.buffer.read(); sys.stdout.write({text!r})",
    )
    assert check.check_archive(b"PK")["problems"] == [check.FAILED]


def _valid_reply():
    """The worker's document for the synthetic workbook, as JSON reads it."""
    return json.loads(json.dumps(check_worker.document(workbook_bytes())))


def _set(path, value):
    """A change that sets one value of the reply, by its key path."""

    def change(document):
        *parents, last = path
        target = document
        for key in parents:
            target = target[key]
        target[last] = value

    return change


def _drop(path):
    """A change that deletes one key of the reply."""

    def change(document):
        *parents, last = path
        target = document
        for key in parents:
            target = target[key]
        del target[last]

    return change


def _extra_note(document):
    document["notes"]["note.02"] = "x" * (256 * 1024)


DEEP_CHANGES = {
    "metadata key missing": _drop(("metadata", "dedup_rule")),
    "metadata key added": _set(("metadata", "extra"), "x"),
    "year as text": _set(("metadata", "campaign_year"), "2055"),
    "year out of range": _set(("metadata", "campaign_year"), 2101),
    "final as text": _set(("metadata", "is_final"), "TRUE"),
    "bad as_of": _set(("metadata", "as_of"), "yesterday"),
    "naive as_of": _set(("metadata", "as_of"), "2054-10-09 14:00:00"),
    "bad start date": _set(("metadata", "campaign_start_date"), "2054-13-01"),
    "last before start": _set(
        ("metadata", "campaign_last_submission_date"), "2054-10-01"
    ),
    "long campaign name": _set(("metadata", "campaign_name"), "n" * 201),
    "active past an integer": _set(("metadata", "active_families"), 2**31),
    "active as a bool": _set(("metadata", "active_families"), True),
    "long provenance": _set(("metadata", "active_families_provenance"), "p" * 10_001),
    "long source system": _set(("metadata", "source_system"), "s" * 10_001),
    "long dedup rule": _set(("metadata", "dedup_rule"), "d" * 10_001),
    "long schema version": _set(("metadata", "schema_version"), "1." + "0" * 20),
    "daily row key added": _set(("daily", 0, "source"), "derived"),
    "daily row key missing": _drop(("daily", 0, "local_date")),
    "daily day zero": _set(("daily", 0, "campaign_day"), 0),
    "daily amount as a number": _set(("daily", 0, "cumulative_annual_pledged"), 1200),
    "daily amount past numeric": _set(
        ("daily", 0, "cumulative_annual_pledged"), "1" + "0" * 16
    ),
    "daily repeated day": _set(("daily", 1, "campaign_day"), 1),
    "no daily rows": _set(("daily",), []),
    "band key added": _set(("bands", 0, "extra"), 1),
    "band total with three decimals": _set(("bands", 0, "total"), "1.005"),
    "band number past the set": _set(("bands", 0, "band"), 9),
    "band repeated": _set(("bands", 1, "band"), 1),
    "renewal pair": _set(("renewal_families", 0), ["parishsoft_duid", 1001]),
    "renewal envelope system": _set(("renewal_families", 0, 0), "pds_envelope"),
    "renewal id as text": _set(("renewal_families", 0, 1), "1001"),
    "renewal day zero": _set(("renewal_families", 0, 2), 0),
    "renewal repeated": _set(("renewal_families", 1), ["parishsoft_duid", 1001, 1]),
    "note as a number": _set(("notes", "note.01"), 1),
    "count as a list": _set(("other_counts", "count.active_now"), ["41"]),
    "notes past 256 KB": _extra_note,
    "unknown id system": _set(("family_id_systems",), ["envelope"]),
    "flagged key as a number": _set(("flagged_notes",), [1]),
    "total as NaN": _set(("annual_pledged",), "NaN"),
    "negative total": _set(("annual_pledged",), "-1.00"),
    "total past numeric": _set(("annual_pledged",), "1" + "0" * 16),
    "pledging past responded": _set(("pledging_families",), 8),
    "families past an integer": _set(("families",), 2**31),
    "quartiles out of order": _set(("quartiles",), ["3.00", "2.00", "1.00"]),
    "two quartiles": _set(("quartiles",), ["1.00", "2.00"]),
    "band set past smallint": _set(("band_set_version",), 2**15),
}


def test_the_valid_reply_is_well_formed():
    """The real worker's document passes the deep check unchanged."""
    assert check._well_formed(_valid_reply())


@pytest.mark.parametrize("case", DEEP_CHANGES)
def test_a_reply_the_import_could_not_store_is_refused(case):
    """Each value is checked to its stored column's type and limit."""
    document = _valid_reply()
    DEEP_CHANGES[case](document)
    assert not check._well_formed(document)


def test_a_deep_shape_failure_is_check_failed(monkeypatch):
    """Through the helper, such a reply is "could not be checked"."""
    document = _valid_reply()
    document["metadata"]["campaign_name"] = "n" * 201
    text = json.dumps(document)
    helper(
        monkeypatch,
        f"import sys; sys.stdin.buffer.read(); sys.stdout.write({text!r})",
    )
    assert check.check_archive(b"PK")["problems"] == [check.FAILED]


def test_the_worker_maps_memory_errors_to_its_exit(monkeypatch, capsysbinary):
    """MemoryError anywhere in the read is exit 3, with nothing written."""

    def starve(data):
        raise MemoryError

    monkeypatch.setattr(check_worker, "document", starve)
    monkeypatch.setattr(check_worker, "limit_memory", lambda: None)
    monkeypatch.setattr(
        sys, "stdin", type("In", (), {"buffer": __import__("io").BytesIO(b"PK")})
    )
    assert check_worker.main() == 3
    assert capsysbinary.readouterr().out == b""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux limit only")
def test_the_worker_applies_its_address_space_limit():
    """The helper limits itself to 1 GiB before it reads anything."""
    code = (
        "import resource; from parishkit.stewardship.archive import check_worker;"
        "check_worker.limit_memory(); print(resource.getrlimit(resource.RLIMIT_AS)[0])"
    )
    output = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, check=True, text=True
    ).stdout
    assert int(output) == check_worker.MEMORY_BYTES


# --- The command ------------------------------------------------------------------


def test_archive_check_prints_a_summary_per_file(tmp_path, capsys):
    """Counts, totals and dates; no note text and no Family row."""
    path = tmp_path / "stewardship-2055.xlsx"
    path.write_bytes(workbook_bytes())
    assert main(["archive-check", str(path)]) == 0
    line = json.loads(capsys.readouterr().out)
    assert line["file"] == "stewardship-2055.xlsx" and line["valid"] is True
    summary = line["summary"]
    assert summary["families_responded"] == 7
    assert summary["annual_pledged"] == "12660.75"
    assert summary["days"] == 6 and summary["notes"] == 1
    text = json.dumps(line)
    assert "Synthetic data" not in text and "duid:" not in text
    assert "pledging" not in text and "quartile" not in text


def test_archive_check_reports_problems_without_values(tmp_path, capsys):
    """A refused file gives codes and located rules, exit 3."""
    metadata, sheets = campaign()
    sheets["families"][0]["frequency_detail"] = 12.5
    sheets["families"][1]["envelope_id"] = SECRET
    good = tmp_path / "good.xlsx"
    good.write_bytes(workbook_bytes())
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(workbook_bytes(metadata, sheets))
    assert main(["archive-check", str(good), str(bad)]) == 3
    lines = [json.loads(text) for text in capsys.readouterr().out.splitlines()]
    assert [line["valid"] for line in lines] == [True, False]
    assert lines[1]["problems"] == [
        {"code": "type", "text": "families!O2: frequency_detail must be text"}
    ]
    assert "12.5" not in json.dumps(lines[1])


def test_archive_check_reports_an_unreadable_path(tmp_path, capsys):
    """A missing file is refused without a check."""
    assert main(["archive-check", str(tmp_path / "missing.xlsx")]) == 3
    line = json.loads(capsys.readouterr().out)
    assert line == {"file": "missing.xlsx", "valid": False, "readable": False}


def test_archive_check_needs_a_file():
    """No file is a usage error, with no input echoed."""
    with pytest.raises(SystemExit) as caught:
        main(["archive-check"])
    assert caught.value.code == 2
