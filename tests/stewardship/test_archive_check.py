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
