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
        "attempt": state.get("attempt", 1),
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
        "run_attempt": listed["attempt"],
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
    assert "run 80 is not a 'CI (jobs: all)' or 'CI (stewardship: all)' " in (
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
    assert capsys.readouterr().out == "80\tin_progress\t-\tsame\t0\t1\n"
    assert pauses == []
    # Missing from every listing: nothing is printed, so release.sh refuses.
    _, pauses = fake_gh(monkeypatch, rest(80, "same"), [])
    assert evidence.main([*argv, "--retry-seconds", "0"]) == 0
    assert capsys.readouterr().out == "" and pauses == [0, 0, 0]


def test_select_names_the_attempt_of_a_re_run(monkeypatch, capsys):
    """A run that passed after its failed jobs were re-run says so (#158).

    It is still evidence; the attempt only makes the rerun visible to
    release.yml, release.sh, merge-train.sh and the main push check. A
    listing without the field, as the scripted gh stand-ins in
    test_ops_scripts.py write, reads as attempt 1.
    """
    fake_gh(monkeypatch, None, [run(81, "docs", attempt=3)])
    monkeypatch.setattr(evidence, "differing_paths", lambda *args: TREES[args[2]])
    assert evidence.main(["select", "--repo", "o/r", "--commit", "tag"]) == 0
    assert capsys.readouterr().out == "81\tcompleted\tsuccess\tdocs\t2\t3\n"
    legacy = run(82, "same")
    del legacy["attempt"]
    fake_gh(monkeypatch, None, [legacy])
    assert evidence.main(["select", "--repo", "o/r", "--commit", "tag"]) == 0
    assert capsys.readouterr().out == "82\tcompleted\tsuccess\tsame\t0\t1\n"


RETIREMENT = "src/parishkit/stewardship/schema/migrations/0171_retire_digests.sql"
CONVERSION = "src/parishkit/stewardship/schema/migrations/0161_report_conversion.sql"


def test_the_release_order_table_names_real_frozen_files():
    """Both sides of every row exist, so a renumber cannot silently disarm it."""
    assert evidence.EARLIER_RELEASE_REQUIRED[RETIREMENT] == CONVERSION
    for later, earlier in evidence.EARLIER_RELEASE_REQUIRED.items():
        assert (ROOT / later).is_file() and (ROOT / earlier).is_file()


@pytest.mark.parametrize(
    "tree, previous_tag, previous, refused",
    [
        # Retirement and conversion in one release: refused.
        ({RETIREMENT, CONVERSION}, "v1.0.0", set(), True),
        # No earlier release at all: refused.
        ({RETIREMENT, CONVERSION}, None, set(), True),
        # The conversion already shipped: allowed.
        ({RETIREMENT, CONVERSION}, "v1.0.0", {CONVERSION}, False),
        # No retirement in the tree: nothing to check.
        ({CONVERSION}, "v1.0.0", set(), False),
    ],
)
def test_order_refusals(tree, previous_tag, previous, refused):
    """A later file needs an earlier release that already had its prerequisite."""
    refusals = evidence.order_refusals(
        tree.__contains__, previous_tag, previous.__contains__
    )
    assert bool(refusals) is refused
    if refused:
        assert RETIREMENT in refusals[0] and CONVERSION in refusals[0]
        assert f"previous release ({previous_tag or 'none'})" in refusals[0]


def commit_files(work, message, *paths):
    """Add each path (empty) to the checkout and commit; return the SHA."""
    for path in paths:
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text("-- frozen\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "--allow-empty", "-m", message)
    return git(work, "rev-parse", "HEAD")


def test_release_order_reads_the_remotes_previous_annotated_release(tmp_path, capsys):
    """Only an annotated vX.Y.Z tag that is an ancestor counts, highest first."""
    work = checkout_with_remote(tmp_path)
    commit_files(work, "base")
    git(work, "tag", "-a", "v1.9.0", "-m", "old release")
    converting = commit_files(work, "conversion", CONVERSION)
    # A lightweight tag on the converting commit is not a release.
    git(work, "tag", "v1.10.0")
    head = commit_files(work, "retirement", RETIREMENT)
    git(work, "push", "-q", "--tags", "origin", "main")
    args = ["release-order", "--checkout", str(work), "--commit", head]
    assert evidence.release_order(str(work), "origin", head)
    assert evidence.main(args) == 1
    assert "previous release (v1.9.0)" in capsys.readouterr().err
    # The converting commit released (annotated, and v1.10.0 sorts above
    # v1.9.0 numerically): the retirement may follow.
    git(work, "tag", "-d", "v1.10.0")
    git(work, "tag", "-a", "v1.10.0", "-m", "converting release", converting)
    git(work, "push", "-q", "--force", "origin", "v1.10.0")
    assert evidence.previous_release(str(work), "origin", head) == (
        "v1.10.0",
        converting,
    )
    assert evidence.main(args) == 0
    # A higher release tag that is not an ancestor (another line) is skipped.
    git(work, "checkout", "-q", "--detach", "HEAD~2")
    commit_files(work, "elsewhere")
    git(work, "tag", "-a", "v9.0.0", "-m", "elsewhere")
    git(work, "push", "-q", "origin", "v9.0.0")
    assert evidence.previous_release(str(work), "origin", head)[0] == "v1.10.0"


def test_previous_release_ranks_both_tag_prefixes_by_version(tmp_path):
    """Old vX.Y.Z and new stewardship-vX.Y.Z tags rank together (#2203).

    After the switch the old tags stay the history: a v1.23.0 ancestor is
    still found when no scoped tag is, the highest version wins whatever its
    prefix, a lightweight scoped tag never counts, and a version tagged
    under both prefixes counts once (as stewardship-v).
    """
    work = checkout_with_remote(tmp_path)
    old = commit_files(work, "conversion", CONVERSION)
    git(work, "tag", "-a", "v1.23.0", "-m", "old prefix")
    head = commit_files(work, "retirement", RETIREMENT)
    # Lightweight tags and non-release names are not releases.
    git(work, "tag", "stewardship-v1.24.0")
    git(work, "tag", "-a", "stewardship-v1.24.0-rc1", "-m", "not a release")
    git(work, "tag", "-a", "sync-v9.0.0", "-m", "another product")
    git(work, "push", "-q", "--tags", "origin", "main")
    assert evidence.previous_release(str(work), "origin", head) == ("v1.23.0", old)
    assert evidence.release_order(str(work), "origin", head) == []
    # The scoped release outranks the old one ...
    git(work, "tag", "-d", "stewardship-v1.24.0")
    git(work, "tag", "-a", "stewardship-v1.24.0", "-m", "new prefix", old)
    git(work, "push", "-q", "--force", "origin", "stewardship-v1.24.0")
    assert evidence.previous_release(str(work), "origin", head) == (
        "stewardship-v1.24.0",
        old,
    )
    # ... and v1.100.0 outranks it numerically, not lexically.
    git(work, "tag", "-a", "v1.100.0", "-m", "old prefix", old)
    git(work, "push", "-q", "origin", "v1.100.0")
    assert evidence.previous_release(str(work), "origin", head)[0] == "v1.100.0"
    # One version under both prefixes counts once, as the scoped tag.
    git(work, "tag", "-a", "stewardship-v1.100.0", "-m", "both", old)
    git(work, "push", "-q", "origin", "stewardship-v1.100.0")
    assert evidence.previous_release(str(work), "origin", head)[0] == (
        "stewardship-v1.100.0"
    )
    # A same-version tag that is not an ancestor never hides one that is.
    git(work, "checkout", "-q", "--detach", old)
    elsewhere = commit_files(work, "elsewhere")
    git(work, "tag", "-a", "stewardship-v1.200.0", "-m", "elsewhere", elsewhere)
    git(work, "tag", "-a", "v1.200.0", "-m", "ancestor", old)
    git(work, "push", "-q", "origin", "stewardship-v1.200.0", "v1.200.0")
    assert evidence.previous_release(str(work), "origin", head) == ("v1.200.0", old)


def test_release_order_refuses_when_the_tags_cannot_be_listed(tmp_path, capsys):
    """An unreachable remote is a refusal (exit 1), never a pass."""
    work = checkout_with_remote(tmp_path)
    head = commit_files(work, "retirement", RETIREMENT)
    git(work, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    args = ["release-order", "--checkout", str(work), "--commit", head]
    assert evidence.main(args) == 1
    assert "cannot list the release tags on origin" in capsys.readouterr().err


# Product-aware evidence (#2208). TREES diffs that add other products' paths.
PRODUCT_TREES = {
    "sync": ["src/parishkit/sync/models.py", "tests/sync/test_models.py", "docs/a.md"],
    "stewardship": [
        "src/parishkit/stewardship/views.py",
        "tests/stewardship/test_x.py",
    ],
}
SHARED_PATHS = [
    "src/parishkit/config.py",
    "src/parishkit/syncing.py",
    "src/parishkit/sync",
    "tests/test_config.py",
    "tests/syncs/test_x.py",
    "tools/ci/affected.py",
    "deploy/stewardship/Dockerfile",
    ".github/workflows/ci.yml",
    "requirements/stewardship.txt",
    "pyproject.toml",
    "README.md",
]


def product_select(runs, product, trees):
    """The deciding run's id for product, against the given diffs."""
    chosen = evidence.select(runs, "tag", lambda base, target: trees[base], product)
    return chosen and chosen[0]["databaseId"]


@pytest.fixture
def classifier(monkeypatch):
    """A stand-in for #2207: a product's own paths affect only that product."""

    def affects(product, path):
        owned = path.startswith(evidence.PRODUCT_ROOTS[product])
        others = [p for p in evidence.PRODUCTS if p != product]
        return owned or not any(
            path.startswith(evidence.PRODUCT_ROOTS[o]) for o in others
        )

    monkeypatch.setattr(evidence, "affects", affects)


@pytest.mark.parametrize(
    ("title", "products"),
    [
        ("CI (jobs: all)", {"stewardship"}),
        ("CI (stewardship: all)", {"stewardship"}),
        ("CI (sync: all)", {"sync"}),
        ("CI (jobs: affected)", set()),
        ("CI (stewardship: affected)", set()),
        ("CI (sync: affected)", set()),
        ("CI (Sync: all)", set()),
    ],
)
def test_full_run_names_per_product(title, products):
    """Each product counts only its own full run names; affected runs never.

    "CI (jobs: all)" runs no Sync job group yet, so it is Stewardship's.
    """
    for product in evidence.PRODUCTS:
        counted = product_select([run(1, "same", title=title)], product, TREES)
        assert (counted == 1) == (product in products), product


def test_stewardship_is_the_default_product():
    """Callers that pass no product get exactly the Stewardship decision."""
    assert evidence.DEFAULT_PRODUCT == "stewardship"
    for base in ("same", "docs", "code", "gone"):
        runs = [run(1, base), run(2, "same", title="CI (sync: all)")]
        assert evidence.select(runs, "tag", lambda b, t: TREES[b]) == evidence.select(
            runs, "tag", lambda b, t: TREES[b], "stewardship"
        )


def test_without_the_classifier_other_product_paths_still_break_the_match():
    """Until #2207 lands, affects() says yes to every path: today's rule."""
    trees = {**TREES, **PRODUCT_TREES}
    assert product_select([run(1, "sync")], "stewardship", trees) is None
    for path in [*PRODUCT_TREES["sync"], *SHARED_PATHS]:
        if not evidence.docs_safe(path):
            assert not evidence.ignorable("stewardship", path), path


def test_another_products_own_paths_keep_a_run_valid(classifier):
    """A Sync-only difference leaves a Stewardship run valid, and back."""
    trees = {**TREES, **PRODUCT_TREES}
    assert product_select([run(1, "sync")], "stewardship", trees) == 1
    sync_run = run(2, "stewardship", title="CI (sync: all)")
    assert product_select([sync_run], "sync", trees) == 2
    # A product's own paths still break its own match.
    assert product_select([run(3, "stewardship")], "stewardship", trees) is None
    sync_run = run(4, "sync", title="CI (sync: all)")
    assert product_select([sync_run], "sync", trees) is None


@pytest.mark.parametrize("path", SHARED_PATHS)
def test_shared_paths_always_need_a_fresh_run(monkeypatch, path):
    """Shared code breaks every product's match, whatever a classifier says.

    Administrator, 2026-11-02: a change outside the products' own roots
    always needs a fresh full Stewardship run before a Stewardship release.
    """
    monkeypatch.setattr(evidence, "affects", lambda product, path: False)
    for product in evidence.PRODUCTS:
        assert not evidence.ignorable(product, path)
        trees = {"shared": [path]}
        title = "CI (sync: all)" if product == "sync" else "CI (jobs: all)"
        assert product_select([run(1, "shared", title=title)], product, trees) is None


def test_a_named_run_on_another_products_change_is_used(
    monkeypatch, capsys, classifier
):
    """release.sh's --run path applies the same product rule as the listing."""
    trees = {**TREES, **PRODUCT_TREES}
    record = rest(80, "sync")
    fake_gh(monkeypatch, record, [run(80, "sync")])
    chosen, _ = evidence.choose("o/r", 100, "tag", lambda base, target: trees[base], 80)
    assert chosen[0]["databaseId"] == 80 and len(chosen[1]) == 3


def test_select_takes_a_product_on_the_command_line(monkeypatch, capsys):
    """--product sync ignores Stewardship's full runs; a bad product exits 2."""
    fake_gh(monkeypatch, None, [run(81, "same")])
    monkeypatch.setattr(evidence, "differing_paths", lambda *args: TREES[args[2]])
    argv = ["select", "--repo", "o/r", "--commit", "tag"]
    assert evidence.main([*argv, "--product", "sync"]) == 0
    assert capsys.readouterr().out == ""
    assert evidence.main([*argv, "--product", "stewardship"]) == 0
    assert capsys.readouterr().out == "81\tcompleted\tsuccess\tsame\t0\t1\n"
    with pytest.raises(SystemExit) as raised:
        evidence.main([*argv, "--product", "parish"])
    assert raised.value.code == 2


def test_a_product_never_ignores_its_own_paths(monkeypatch):
    """Even a classifier that says "unaffected" cannot hide a product's own code.

    The owner check in other_product_only is the only thing standing between
    a wrong classifier answer and a Stewardship release shipping Stewardship
    code that no full run ever saw.
    """
    monkeypatch.setattr(evidence, "affects", lambda product, path: False)
    for product in evidence.PRODUCTS:
        for path in PRODUCT_TREES[product]:
            if not evidence.docs_safe(path):
                assert not evidence.ignorable(product, path), (product, path)


def test_a_mixed_diff_needs_a_fresh_full_stewardship_run(classifier):
    """A Sync path plus a shared path is not ignorable: the shared path wins."""
    trees = {
        **TREES,
        **PRODUCT_TREES,
        "mixed": [PRODUCT_TREES["sync"][0], SHARED_PATHS[0]],
    }
    assert product_select([run(1, "mixed")], "stewardship", trees) is None
    # The Sync path alone would have been ignored.
    assert product_select([run(2, "sync")], "stewardship", trees) == 2


def test_the_command_line_defaults_to_stewardship(monkeypatch, capsys):
    """select with no --product decides exactly as --product stewardship."""
    fake_gh(
        monkeypatch, None, [run(83, "same"), run(84, "same", title="CI (sync: all)")]
    )
    monkeypatch.setattr(evidence, "differing_paths", lambda *args: TREES[args[2]])
    argv = ["select", "--repo", "o/r", "--commit", "tag"]
    assert evidence.main(argv) == 0
    default = capsys.readouterr().out
    assert evidence.main([*argv, "--product", "stewardship"]) == 0
    assert capsys.readouterr().out == default
    assert default.startswith("83\t")


def test_full_run_titles_follow_ci_run_name():
    """FULL_RUN_TITLES and ci.yml's run-name cannot drift apart (#2208).

    A product is told apart only by the dispatch's title, which ci.yml builds
    from its dispatch inputs. Evaluate that run-name for every input value:
    only a jobs=all dispatch may carry a full-run title, and only
    Stewardship accepts it. Adding a dispatch input (such as a product
    selector) without a matching run-name and title change fails here.
    """
    import re

    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    # PyYAML reads the bare `on:` key as the boolean true.
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"jobs"}, "a new dispatch input must reach run-name"
    template = re.search(r"format\('([^']*)', inputs\.jobs\)", workflow["run-name"])
    assert template, "run-name must still be built from inputs.jobs"
    for choice in inputs["jobs"]["options"]:
        title = template.group(1).replace("{0}", choice)
        accepted = {
            p for p, names in evidence.FULL_RUN_TITLES.items() if title in names
        }
        # Only a jobs=all dispatch runs every group, and today only
        # Stewardship's groups exist, so nobody else may accept it.
        assert accepted == ({"stewardship"} if choice == "all" else set()), title
