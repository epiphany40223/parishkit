"""quality_browser_hints refits browser partition hints from CI job logs."""

import sys

import pytest

from parishkit.stewardship import quality_browser_hints as hints
from parishkit.stewardship.quality_sharding import browser_estimate

STAMP = "2026-10-23T05:27:15.9051240Z "
PROGRESS = "CI_PROGRESS 2026-10-23T05:27:15+00:00"


def end(node, seconds):
    """One CI_PROGRESS END line as a job log prints it (pytest's dot first)."""
    return f"{STAMP}.{PROGRESS} END {node} elapsed={seconds}s"


def partition(label):
    """One CI_BROWSER_PARTITION line for LABEL, such as "firefox 2/4"."""
    return f"{STAMP}CI_BROWSER_PARTITION {label}: 3 out of 9 cases, estimated 9 s"


def test_measured_seconds_reads_each_engines_tests_and_takes_medians():
    """Engines come from the partition line; repeated attempts take the median."""
    spaced = "tests/b/test_a.py::test_x[/setup-Check readiness and finish-firefox]"
    first = [
        end("tests/b/test_a.py::ignored[chromium]", 9),  # Before any partition.
        partition("firefox 2/4"),
        end(spaced, 1.5),
        end("tests/b/test_a.py::test_y[firefox]", 2),
        partition("webkit"),
        end("tests/b/test_a.py::test_y[webkit]", 4),
        f"{STAMP}{PROGRESS} START tests/b/test_a.py::z",
    ]
    second = [partition("firefox 2/4"), end(spaced, 2.5), end(spaced, 9)]
    assert hints.measured_seconds([first, second]) == {
        ("firefox", spaced): 2.5,
        ("firefox", "tests/b/test_a.py::test_y[firefox]"): 2,
        ("webkit", "tests/b/test_a.py::test_y[webkit]"): 4,
    }


def synthetic(engines, files, extra):
    """Measured seconds: each FILES case costs its ENGINES line, plus EXTRA.

    ENGINES maps an engine to (fixed, per-case) seconds, FILES a file name
    to its case count, and EXTRA a file name to fixed seconds on every engine.
    """
    measured = {}
    for engine, (fixed, per_case) in engines.items():
        for name, cases in files.items():
            first = fixed + extra.get(name, 0)
            for case in range(cases):
                node = f"tests/b/{name}::test[{case}-{engine}]"
                measured[(engine, node)] = per_case + (first if case == 0 else 0)
    return measured


ENGINES = {"chromium": (8, 0.25), "firefox": (20, 0.75), "webkit": (12, 1.25)}
FILES = {f"test_{n}.py": 4 + 3 * n for n in range(12)} | {"test_slow.py": 5}


def test_fit_recovers_each_engines_line_and_a_slow_files_extra_seconds():
    """A slow file earns extra seconds and does not tilt the engines' lines."""
    measured = synthetic(ENGINES, FILES, {"test_slow.py": 300})
    totals = hints.file_totals(measured)
    assert totals["webkit"]["test_slow.py"] == [5, 12 + 5 * 1.25 + 300]
    assert hints.fit_hints(totals) == (ENGINES, {"test_slow.py": 300})


def test_fit_below_the_threshold_records_no_extra_seconds():
    """A file only a few seconds above the line is left to the fit."""
    measured = synthetic(ENGINES, FILES, {"test_slow.py": 8})
    engines, extras = hints.fit_hints(hints.file_totals(measured))
    assert extras == {}
    assert set(engines) == set(ENGINES)


def test_fit_line_never_returns_a_negative_fixed_cost():
    """Seconds falling with size would mean a negative intercept; pin it at 0."""
    assert hints.fit_line([(1, 10), (3, 30)]) == pytest.approx((0, 10))
    assert hints.fit_line([(1, 9), (9, 1)]) == pytest.approx((5, 0))
    assert hints.fit_line([(2, 4), (2, 6)]) == pytest.approx((5, 0))


def test_replay_splits_every_measured_test_once_and_sums_its_seconds():
    """The replay runs the real split on the measured tests, every test once."""
    measured = synthetic(ENGINES, FILES, {})
    jobs = ["firefox:1/2", "firefox:2/2", "chromium:1/1 webkit:1/1"]
    result = hints.replay(measured, jobs)
    assert [job for job, _ in result] == jobs
    rows = [row for _, job_rows in result for row in job_rows]
    assert [label for label, _, _ in rows] == [
        "firefox 1/2",
        "firefox 2/2",
        "chromium",
        "webkit",
    ]
    by_engine = {}
    for (engine, _), seconds in measured.items():
        by_engine[engine] = by_engine.get(engine, 0) + seconds
    assert rows[0][2] + rows[1][2] == pytest.approx(by_engine["firefox"])
    assert rows[3][2] == pytest.approx(by_engine["webkit"])
    nodes = [node for engine, node in measured if engine == "webkit"]
    assert rows[3][1] == browser_estimate(nodes, "webkit")


def test_main_prints_the_fit_and_each_jobs_share_of_the_budget(
    tmp_path, monkeypatch, capsys
):
    """The command prints pasteable hints and one replay line per job."""
    lines = []
    for (engine, node), seconds in synthetic(ENGINES, FILES, {}).items():
        lines += [partition(engine), end(node, seconds)]
    log = tmp_path / "job.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["quality_browser_hints", str(log)])
    hints.main()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"BROWSER_ENGINE_SECONDS = {ENGINES!r}"
    assert out[1] == "BROWSER_FILE_EXTRA_SECONDS = {}"
    assert out[2].startswith(f"{hints.BROWSER_JOBS[0]}: ")
    assert "%); " in out[2] and " estimated " in out[2] and " actual " in out[2]
    assert len(out) == 2 + len(hints.BROWSER_JOBS)


def test_main_fails_without_measured_tests(tmp_path, monkeypatch, capsys):
    """A log with no CI_PROGRESS END lines (such as a setup failure) exits 1."""
    log = tmp_path / "job.log"
    log.write_text(partition("firefox 1/4") + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["quality_browser_hints", str(log)])
    with pytest.raises(SystemExit) as raised:
        hints.main()
    assert raised.value.code == 1
    assert "No CI_PROGRESS END lines" in capsys.readouterr().err
