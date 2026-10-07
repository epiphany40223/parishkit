"""The release evidence rule, tools/stewardship-ops/release_evidence.py (#662).

release.yml and release.sh both ask this script which full CI run decides
a release; these tests pin its closed docs-safe allowlist and how it picks
the deciding run.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "release_evidence", ROOT / "tools" / "stewardship-ops" / "release_evidence.py"
)
evidence = importlib.util.module_from_spec(SPEC)
# No __pycache__ in tools/stewardship-ops: other tests read every file there.
_write_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True
try:
    SPEC.loader.exec_module(evidence)
finally:
    sys.dont_write_bytecode = _write_bytecode


@pytest.mark.parametrize(
    "path",
    [
        "docs/guides/stewardship-operator-scripts.md",
        "docs/specs/stewardship/operations/spec.md",
        "docs/README.md",
        "AGENTS.md",
        "CLAUDE.md",
    ],
)
def test_docs_safe_paths(path):
    """Markdown under docs/ and the root agent instructions are docs-safe."""
    assert evidence.docs_safe(path)


@pytest.mark.parametrize(
    "path",
    [
        # The image and package metadata include README.md.
        "README.md",
        # send-report.sh runs this guide's SQL.
        "docs/guides/stewardship-mail-send-report.md",
        # A test input (traceability), not Markdown.
        "docs/development/stewardship-acceptance.yaml",
        "docs/guides/image.png",
        "docs/guides/NOTES.MD",
        "docs",
        "docs.md",
        "scripts/pk-stewardship/README.md",
        "tools/stewardship-ops/README.md",
        "src/parishkit/stewardship/DECISIONS.md",
        "tests/README.md",
        "deploy/stewardship/README.md",
        ".github/workflows/release.yml",
        ".github/workflows/notes.md",
        "pyproject.toml",
        "requirements/stewardship.txt",
        "deploy/stewardship/Dockerfile",
        "src/parishkit/stewardship/schema/migrations/0008_x.sql",
        "RELEASE_NOTES.md",
        "LICENSE",
    ],
)
def test_everything_else_is_not_docs_safe(path):
    """The allowlist is closed: anything a build, test or tool reads is out."""
    assert not evidence.docs_safe(path)


def run(number, sha, title="CI (jobs: all)", event="workflow_dispatch", **state):
    """A run as gh run list reports it."""
    return {
        "databaseId": number,
        "headSha": sha,
        "displayTitle": title,
        "event": event,
        "status": state.get("status", "completed"),
        "conclusion": state.get("conclusion", "success"),
    }


TREES = {
    "same": [],
    "docs": ["docs/guides/x.md", "CLAUDE.md"],
    "code": ["docs/guides/x.md", "src/parishkit/x.py"],
    "gone": None,
}


def select(runs):
    """The deciding run's id and docs-path count, against the TREES diffs."""
    chosen = evidence.select(runs, "tag", lambda base, target: TREES[base])
    return chosen and (chosen[0]["databaseId"], len(chosen[1]))


def test_identical_and_docs_only_trees_qualify():
    """An identical tree or a docs-only difference makes a run evidence."""
    assert select([run(1, "same")]) == (1, 0)
    assert select([run(2, "docs")]) == (2, 2)


def test_other_differences_never_qualify():
    """A non-docs path anywhere in the difference is skipped."""
    assert select([run(3, "code")]) is None
    assert select([run(3, "code"), run(1, "same")]) == (1, 0)


@pytest.mark.parametrize(
    "title", ["CI (jobs: affected)", "CI", "", "CI (jobs: all) ", "ci (jobs: all)"]
)
def test_only_a_run_named_ci_jobs_all_counts(title):
    """A partial or differently named run is never evidence (#626)."""
    assert select([run(5, "same", title=title)]) is None


@pytest.mark.parametrize("event", ["push", "pull_request", "schedule"])
def test_only_a_dispatch_counts(event):
    """A run from another event is never evidence."""
    assert select([run(6, "same", event=event)]) is None


def test_an_unreadable_newer_run_decides():
    """A newer full run whose commit cannot be read is not skipped.

    It might be a newer failure on the same tree, so it decides (callers
    refuse it); an older unreadable run behind a qualifying one does not.
    """
    chosen = evidence.select([run(4, "gone"), run(1, "same")], "tag", TREES.get)
    assert chosen == (run(4, "gone"), None)
    assert select([run(1, "same"), run(4, "gone")]) == (1, 0)


def test_the_newest_full_run_of_a_commit_decides():
    """A newer failed full run on the same commit withdraws an older success."""
    runs = [run(8, "same", conclusion="failure"), run(7, "same")]
    assert select(runs) == (8, 0)
    # A newer pending run decides too (the caller waits for it).
    runs = [run(9, "docs", status="in_progress", conclusion=""), run(7, "same")]
    assert select(runs) == (9, 2)
    # A newer affected run on the commit is ignored.
    runs = [run(10, "same", title="CI (jobs: affected)"), run(7, "same")]
    assert select(runs) == (7, 0)


def git(cwd, *args):
    """Run git quietly in cwd; return its stripped stdout."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    ).stdout.strip()


def checkout_with_remote(tmp_path):
    """A clone of an empty bare repository; the remote decides readability."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(work))
    return work


def test_differing_paths_lists_both_sides_of_a_rename(tmp_path):
    """A move out of docs/ shows its new, non-docs path; renames are off."""
    work = checkout_with_remote(tmp_path)
    (work / "docs").mkdir()
    (work / "docs" / "a.md").write_text("text\n" * 20)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "one")
    base = git(work, "rev-parse", "HEAD")
    (work / "tools").mkdir()
    git(work, "mv", "docs/a.md", "tools/a.md")
    git(work, "commit", "-q", "-m", "two")
    git(work, "push", "-q", "origin", "main")
    paths = evidence.differing_paths(str(work), "origin", base, "HEAD")
    assert sorted(paths) == ["docs/a.md", "tools/a.md"]


def test_identical_tree_under_another_sha_has_no_differences(tmp_path):
    """A train head and main's merge of it: different commits, one tree."""
    work = checkout_with_remote(tmp_path)
    (work / "src.py").write_text("x = 1\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "train head")
    train = git(work, "rev-parse", "HEAD")
    git(work, "commit", "-q", "--allow-empty", "-m", "merge on main")
    main = git(work, "rev-parse", "HEAD")
    git(work, "push", "-q", "origin", "main")
    assert train != main
    assert evidence.differing_paths(str(work), "origin", train, main) == []


def test_an_unfetchable_commit_is_unreadable_and_logged(tmp_path, capsys):
    """A commit neither the checkout nor its remote holds reads as None."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(work))
    git(work, "commit", "-q", "--allow-empty", "-m", "one")
    git(work, "push", "-q", "origin", "main")
    missing = "1" * 40
    assert evidence.differing_paths(str(work), "origin", missing, "HEAD") is None
    assert f"cannot fetch {missing} from origin" in capsys.readouterr().err


def test_a_call_past_its_limit_is_logged_and_raised(capsys):
    """A timeout kills the call's process group and says what, limit, elapsed."""
    with pytest.raises(evidence.TimedOut):
        evidence.limited("a slow call", 1, ["sh", "-c", "sleep 30 & sleep 30"])
    err = capsys.readouterr().err
    assert "timed out: a slow call (limit 1 s, elapsed 1 s)" in err


def test_select_exits_3_on_a_timeout(monkeypatch):
    """The command-line exit status for a timeout is 3, as lib.sh's."""

    def slow(*_args):
        raise evidence.TimedOut("gh run list")

    monkeypatch.setattr(evidence, "list_runs", slow)
    assert evidence.main(["select", "--repo", "o/r", "--commit", "abc"]) == 3


def test_docs_tests_exist_and_print(capsys):
    """`docs-tests` prints real test files, the list both callers run."""
    assert all((ROOT / test).is_file() for test in evidence.DOCS_TESTS)
    assert evidence.main(["docs-tests"]) == 0
    assert capsys.readouterr().out.split() == list(evidence.DOCS_TESTS)


def test_list_runs_retries_a_failed_gh_call(monkeypatch):
    """Two failed gh calls are retried; a third success returns its runs."""
    results = iter(
        [
            subprocess.CompletedProcess([], 1, "", "blip"),
            subprocess.CompletedProcess([], 1, "", "blip"),
            subprocess.CompletedProcess([], 0, '[{"databaseId": 1}]', ""),
        ]
    )
    calls = []

    def fake(what, limit, command):
        calls.append(command)
        return next(results)

    monkeypatch.setattr(evidence, "limited", fake)
    monkeypatch.setattr(evidence.time, "sleep", lambda _seconds: None)
    assert evidence.list_runs("o/r", 5) == [{"databaseId": 1}]
    assert len(calls) == 3 and calls[0][:3] == ["gh", "run", "list"]


def test_list_runs_gives_up_after_its_attempts(monkeypatch):
    """Every attempt failing is an error, never an empty list of runs."""
    monkeypatch.setattr(
        evidence,
        "limited",
        lambda *_args: subprocess.CompletedProcess([], 1, "", "down"),
    )
    monkeypatch.setattr(evidence.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError):
        evidence.list_runs("o/r", 5)
    assert evidence.main(["select", "--repo", "o/r", "--commit", "abc"]) == 1


@pytest.fixture(autouse=True)
def no_probe_pause(monkeypatch):
    """The readability probe's retry does not pause in tests."""
    monkeypatch.setattr(evidence, "PROBE_RETRY_SECONDS", 0)


def test_the_readability_probe_is_retried_once(tmp_path, capsys):
    """A failed probe is tried again before a commit reads as unreadable."""
    work = checkout_with_remote(tmp_path)
    git(work, "commit", "-q", "--allow-empty", "-m", "one")
    git(work, "push", "-q", "origin", "main")
    missing = "2" * 40
    assert evidence.differing_paths(str(work), "origin", missing, "HEAD") is None
    err = capsys.readouterr().err
    assert "(attempt 1)" in err and "(attempt 2)" in err


def test_readability_is_decided_by_the_remote(tmp_path, capsys):
    """A commit the checkout holds but the remote does not serve is unreadable.

    An abandoned train branch can survive in an operator's checkout after
    the remote dropped it; release.yml's fresh clone could not read it, so
    neither may release.sh.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(work))
    git(work, "commit", "-q", "--allow-empty", "-m", "one")
    git(work, "push", "-q", "origin", "main")
    git(work, "checkout", "-q", "-b", "train")
    (work / "x.py").write_text("x = 1\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "local only")
    local = git(work, "rev-parse", "HEAD")
    assert evidence.differing_paths(str(work), "origin", local, "main") is None
    assert f"cannot fetch {local} from origin" in capsys.readouterr().err
    git(work, "push", "-q", "origin", "train")
    assert evidence.differing_paths(str(work), "origin", local, "main") == ["x.py"]
