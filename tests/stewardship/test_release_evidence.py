"""The release evidence rule, tools/stewardship-ops/release_evidence.py (#662).

release.yml and release.sh both ask this script which full CI run decides
a release; these tests pin its closed docs-safe allowlist and how it picks
the deciding run.
"""

import importlib.util
import json
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


# ---------------------------------------------------------------------------
# A named run is also read directly; the listing alone decides (#730). These
# fake gh: `gh api` answers the named run's REST record, and each `gh run
# list` answers the next listing (a partial or stale page, then a fuller one).


def rest(number, sha, path=".github/workflows/ci.yml", **state):
    """A run as `gh api repos/OWNER/NAME/actions/runs/ID` reports it."""
    listed = run(number, sha, **state)
    return {
        "id": number,
        "head_sha": sha,
        "status": listed["status"],
        "conclusion": listed["conclusion"] or None,
        "display_title": listed["displayTitle"],
        "event": listed["event"],
        "path": path,
    }


def fake_gh(monkeypatch, record, *listings):
    """Answer gh calls from record and listings; return the calls made.

    The last listing repeats once the others are used up, and pauses are
    recorded instead of slept.
    """
    calls, pauses, pages = [], [], list(listings)

    def fake(what, limit, command):
        calls.append(command[:2])
        if command[1] == "api":
            body = record
        else:
            body = pages.pop(0) if len(pages) > 1 else pages[0]
        return subprocess.CompletedProcess(command, 0, json.dumps(body), "")

    monkeypatch.setattr(evidence, "limited", fake)
    monkeypatch.setattr(evidence.time, "sleep", pauses.append)
    return calls, pauses


def choose(named, limit=100):
    """The deciding run's (id, status, conclusion), against the TREES diffs."""
    chosen, _ = evidence.choose(
        "o/r", limit, "tag", lambda base, target: TREES[base], named
    )
    return chosen and (
        chosen[0]["databaseId"],
        chosen[0]["status"],
        chosen[0]["conclusion"],
    )


def test_a_named_run_missing_from_every_listing_is_not_chosen(monkeypatch, capsys):
    """release.yml sees only the listing, so a run it omits never decides."""
    page = [run(78, "code")]
    calls, pauses = fake_gh(monkeypatch, rest(80, "same"), page)
    assert choose(80) is None
    err = capsys.readouterr().err
    assert "gh run list returned 1 of 100 requested runs" in err
    assert "run 80 is missing from the listing (listing 1 of 4, elapsed" in err
    assert "gave up re-reading the run listing" in err
    assert "(limit 4 listings, elapsed " in err
    assert [c[1] for c in calls] == ["api", "run", "run", "run", "run"]
    assert pauses == [10, 20, 30]


def test_a_fuller_listing_ends_the_retries(monkeypatch, capsys):
    """Once a re-read listing holds the named run, it decides and is used."""
    calls, pauses = fake_gh(
        monkeypatch, rest(80, "same"), [run(78, "code")], [run(80, "same")]
    )
    assert choose(80) == (80, "completed", "success")
    assert [c[1] for c in calls] == ["api", "run", "run"] and pauses == [10]
    assert "gave up" not in capsys.readouterr().err


def test_a_lagging_listing_is_re_read_until_it_agrees(monkeypatch, capsys):
    """A listing that still shows the run pending gets time to catch up."""
    pending = run(80, "same", status="in_progress", conclusion="")
    calls, pauses = fake_gh(monkeypatch, rest(80, "same"), [pending], [run(80, "same")])
    assert choose(80) == (80, "completed", "success") and pauses == [10]
    assert "shows run 80 as in_progress/, not completed/success" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("listed", "direct"),
    [
        # A re-run keeps its run id: the listing can hold the old success
        # while the direct read sees the re-run pending or failed.
        ({}, {"status": "in_progress", "conclusion": ""}),
        ({}, {"conclusion": "failure"}),
        ({"conclusion": "failure"}, {}),
    ],
)
def test_a_persistent_state_disagreement_is_refused(
    monkeypatch, capsys, listed, direct
):
    """Neither the listing nor the direct read alone decides: no run does."""
    calls, pauses = fake_gh(
        monkeypatch, rest(80, "same", **direct), [run(80, "same", **listed)]
    )
    assert choose(80) is None
    assert len(calls) == 5 and pauses == [10, 20, 30]
    assert "no run decides, so the caller refuses" in capsys.readouterr().err


def test_pending_conclusions_match_across_rest_and_the_listing(monkeypatch):
    """REST's null and the listing's "" (or null) both mean no conclusion yet."""
    direct = rest(80, "same", status="queued", conclusion="")
    assert direct["conclusion"] is None
    for conclusion in ("", None):
        queued = run(80, "same", status="queued", conclusion=conclusion)
        calls, pauses = fake_gh(monkeypatch, direct, [queued])
        assert choose(80) == (80, "queued", conclusion) and pauses == []


def test_a_newer_full_run_on_an_equivalent_tree_is_refused(monkeypatch, capsys):
    """Naming a run never skips the rule: a newer run on the tree means none."""
    page = [run(81, "docs", conclusion="failure"), run(80, "same")]
    calls, pauses = fake_gh(monkeypatch, rest(80, "same"), page)
    assert choose(80) is None
    assert "run 81 decides instead of run 80" in capsys.readouterr().err
    assert len(calls) == 5 and pauses == [10, 20, 30]
    # Without a named run (release.yml), that newer failure decides.
    fake_gh(monkeypatch, None, page)
    assert choose(None) == (81, "completed", "failure")
    # The newest run on the same commit withdraws the named one's success.
    page = [run(81, "same", conclusion="cancelled"), run(80, "same")]
    fake_gh(monkeypatch, rest(80, "same"), page)
    assert choose(80) is None
    fake_gh(monkeypatch, None, page)
    assert choose(None) == (81, "completed", "cancelled")


@pytest.mark.parametrize(
    "record",
    [
        rest(80, "same", title="CI (jobs: affected)"),
        rest(80, "same", event="push"),
        rest(80, "same", path=".github/workflows/release.yml"),
    ],
)
def test_a_named_run_that_is_not_a_full_ci_dispatch_is_never_merged(
    monkeypatch, capsys, record
):
    """Another title, event or workflow file cannot become evidence."""
    calls, pauses = fake_gh(monkeypatch, record, [])
    assert choose(80) is None
    assert "run 80 is not a 'CI (jobs: all)' workflow_dispatch" in (
        capsys.readouterr().err
    )
    assert len(calls) == 2 and pauses == []


def test_a_named_run_on_a_code_different_tree_is_not_waited_for(monkeypatch, capsys):
    """No listing could make it decide, so the listing is read once."""
    calls, pauses = fake_gh(monkeypatch, rest(80, "code"), [])
    assert choose(80) is None
    assert "differs from tag beyond docs-safe paths" in capsys.readouterr().err
    assert len(calls) == 2 and pauses == []


def test_without_a_named_run_one_listing_decides(monkeypatch, capsys):
    """release.yml names no run: one listing, its size logged, no gh api."""
    calls, pauses = fake_gh(monkeypatch, None, [run(78, "code")])
    assert choose(None, limit=5) is None
    assert [c[1] for c in calls] == ["run"] and pauses == []
    assert "gh run list returned 1 of 5 requested runs" in capsys.readouterr().err


def test_select_prints_the_listed_decision(monkeypatch, capsys):
    """The command line passes --run and --retry-seconds through."""
    listed = run(80, "same", status="in_progress", conclusion="")
    _, pauses = fake_gh(
        monkeypatch, rest(80, "same", status="in_progress", conclusion=""), [listed]
    )
    monkeypatch.setattr(evidence, "differing_paths", lambda *args: TREES[args[2]])
    argv = ["select", "--repo", "o/r", "--commit", "tag", "--run", "80"]
    assert evidence.main([*argv, "--retry-seconds", "0"]) == 0
    assert capsys.readouterr().out == "80\tin_progress\t-\tsame\t0\n"
    assert pauses == []
    # Missing from every listing: nothing is printed, so release.sh refuses.
    _, pauses = fake_gh(monkeypatch, rest(80, "same"), [])
    assert evidence.main([*argv, "--retry-seconds", "0"]) == 0
    assert capsys.readouterr().out == "" and pauses == [0, 0, 0]
