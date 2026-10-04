"""The operator helper scripts in tools/stewardship-ops (#459).

They drive GitHub and a deployment's host, so, like the upgrade and local
scripts' tests, these run them with stand-ins on PATH: a scripted `gh` that
answers as Actions would, and a recording `ssh` whose answers stand in for
psql and docker on the host. The release helper runs against a real
temporary git repository with a bare remote, so its version check, tag and
push are the real thing. What is pinned: argument handling and refusals
(nothing reaches gh, git push or ssh after a refusal), the timeout records,
the generic psql invocation, the ssh limits, and the send report's SQL being
taken from its guide at run time. The scripts' own SQL runs against the
PostgreSQL test schema in database/test_ops_sql_postgresql.py.
"""

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "tools" / "stewardship-ops"
SCRIPTS = (
    "ci-watch.sh",
    "release.sh",
    "predeploy-check.sh",
    "send-monitor.sh",
    "send-report.sh",
)
REPORT_GUIDE = ROOT / "docs" / "guides" / "stewardship-mail-send-report.md"
OPS_GUIDE = ROOT / "docs" / "guides" / "stewardship-operator-scripts.md"
RUNBOOK = ROOT / "docs" / "guides" / "stewardship-deployment-runbook.md"
IMAGE = "ghcr.io/epiphany40223/parishkit/stewardship@sha256:" + "e" * 64
PSQL_PREFIX = (
    "docker exec -i stewardship-postgres-1 psql -X -U pk_stewardship_operator "
    "-d stewardship -v ON_ERROR_STOP=1"
)


def executable_stub(bin_dir, name, body):
    """Write a bash stand-in into a directory that can run it."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    probe = bin_dir / "probe"
    if not probe.exists():
        probe.write_text("#!/usr/bin/env bash\nexit 0\n")
        probe.chmod(0o755)
        # The Compose test container mounts /tmp noexec, so the stand-ins
        # could not run there; the other CI jobs still run these tests.
        try:
            subprocess.run([str(probe)])
        except PermissionError:
            pytest.skip("the temporary directory cannot run the stand-in programs")
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


def clean_env(tmp_path, bin_dir, **extra):
    """The environment without the operator's own STEWARDSHIP_ settings."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("STEWARDSHIP_")}
    return {
        **base,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STEWARDSHIP_POLL_SECONDS": "0",
        "STEWARDSHIP_OPS_LOG": str(tmp_path / "ops.log"),
        **extra,
    }


def lines(path):
    """The lines a recording stand-in wrote, or none."""
    return path.read_text().splitlines() if path.exists() else []


def test_scripts_are_executable_strict_bash():
    """Shebang, executable bit, strict mode, `bash -n` and a usage header."""
    for name in SCRIPTS:
        script = OPS / name
        text = script.read_text()
        assert text.startswith("#!/usr/bin/env bash\n"), name
        assert os.access(script, os.X_OK), name
        assert "set -euo pipefail" in text, name
        assert "# Usage: tools/stewardship-ops/" + name in text, name
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, name
    assert subprocess.run(["bash", "-n", str(OPS / "lib.sh")]).returncode == 0


def test_shellcheck_is_clean():
    """Every script and the shared library pass shellcheck at default severity."""
    if shutil.which("shellcheck") is None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("the CI runner lacks shellcheck")
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(
        ["shellcheck", "-s", "bash", *(str(OPS / n) for n in (*SCRIPTS, "lib.sh"))],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_nothing_names_a_host_or_deployment():
    """The repository is public: hosts and UUIDs come from the environment."""
    uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    # Any dotted host name, apart from the public services the scripts use.
    host = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:org|com|net|io|us|church|edu)\b")
    allowed = {"github.com", "ghcr.io"}
    for path in OPS.iterdir():
        text = path.read_text()
        assert not uuid.search(text), path
        assert {m.group(0) for m in host.finditer(text)} <= allowed, path
        # No user@host ssh destination either (the GitHub URL form aside).
        assert not re.search(r"\b(?!git@github)[a-z_][a-z0-9_-]*@[a-z0-9]", text), path


@pytest.mark.parametrize("name", SCRIPTS)
def test_help_prints_the_header(tmp_path, name):
    """-h prints the usage header and touches nothing."""
    result = subprocess.run(
        ["bash", str(OPS / name), "-h"],
        env=clean_env(tmp_path, tmp_path / "bin"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage: tools/stewardship-ops/" + name in result.stdout
    assert "STEWARDSHIP_" in result.stdout


def test_the_guides_link_the_scripts():
    """The operator guide names every script; the runbook and report link it."""
    text = OPS_GUIDE.read_text()
    for name in SCRIPTS:
        assert f"tools/stewardship-ops/{name}" in text, name
    assert "stewardship-operator-scripts.md" in RUNBOOK.read_text()
    assert "stewardship-operator-scripts.md" in REPORT_GUIDE.read_text()


def sql_literals(text, column):
    """The quoted values compared with `column` (= or IN) in a SQL file."""
    found = set()
    for match in re.finditer(
        rf"\b{column}\s*(?:=\s*'([a-z_]+)'|IN\s*\(([^)]*)\))", text
    ):
        if match.group(1):
            found.add(match.group(1))
        else:
            found.update(re.findall(r"'([a-z_]+)'", match.group(2)))
    return found


def task_states():
    """TASK_STATES from jobs/models.py, read without importing Django."""
    import ast

    source = (ROOT / "src/parishkit/stewardship/jobs/models.py").read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "TASK_STATES" for target in node.targets
        ):
            return set(ast.literal_eval(node.value))
    raise AssertionError("TASK_STATES not found")


def test_sql_state_literals_are_real_states():
    """Every state the scripts' SQL names exists in the application's vocabulary."""
    from parishkit.stewardship.jobs.delivery_states import DeliveryState

    delivery = {state.value for state in DeliveryState}
    vocabulary = {
        "stewardship_task_run": task_states(),
        "stewardship_outbox_message": delivery,
    }
    named = {table: set() for table in vocabulary}
    # Each state literal belongs to the table of the FROM before it.
    parts = re.split(
        r"FROM (stewardship_task_run|stewardship_outbox_message)\b",
        (OPS / "predeploy.sql").read_text(),
    )
    for table, segment in zip(parts[1::2], parts[2::2], strict=True):
        named[table] |= sql_literals(segment, "state")
    for table, states in named.items():
        assert states and states <= vocabulary[table], (table, states)
    # The unfinished task states are the application's nonterminal four.
    assert {"queued", "running", "retry_wait", "abandoned"} <= named[
        "stewardship_task_run"
    ]
    # send-monitor.sh's settled rule names the in-flight delivery states.
    monitor = (OPS / "send-monitor.sh").read_text()
    in_flight = re.search(r"\((pending\|submitting\|retry_wait)\)=", monitor)
    assert in_flight and set(in_flight.group(1).split("|")) <= delivery


# ---------------------------------------------------------------------------
# A scripted gh. FAKE_RUN_STATE is every run's status|conclusion|failed jobs
# (default a success); FAKE_GH_FAIL makes `run view` fail and FAKE_GH_SLEEP
# makes it hang that many seconds first; FAKE_MERGEABLE is
# what a pull request answers; FAKE_HEAD and FAKE_EVENT are a run's commit
# and event; ci_runs (a file) lists the dispatch CI runs on the commit,
# newest first, and `workflow run` puts FAKE_NEW_RUN at its top when that is
# set, as a headSha query puts FAKE_NEWER (a run started meanwhile);
# FAKE_OLD_RELEASE is a release.yml run that always exists, and
# FAKE_RELEASE_RUN one that exists once the bare remote FAKE_REMOTE holds a
# tag; a run's log names IMAGE.
FAKE_GH = rf"""
echo "gh $*" >>"$FAKE_DIR/gh.calls"
runs=$FAKE_DIR/ci_runs
prepend() {{ {{ echo "$1"; cat "$runs"; }} >"$runs.new"; mv "$runs.new" "$runs"; }}
case "$*" in
    "workflow run"*)
        if [ -n "${{FAKE_NEW_RUN-}}" ]; then prepend "$FAKE_NEW_RUN"; fi ;;
    "run list"*release.yml*)
        if [ -n "${{FAKE_RELEASE_RUN-}}" ] &&
            [ -n "$(git --git-dir="$FAKE_REMOTE" tag -l)" ]; then
            echo "$FAKE_RELEASE_RUN"
        fi
        if [ -n "${{FAKE_OLD_RELEASE-}}" ]; then echo "$FAKE_OLD_RELEASE"; fi ;;
    "run list"*ci.yml*)
        limit=$(printf '%s\n' "$@" | grep -A1 -x -- --limit | tail -n 1)
        head -n "$limit" "$runs" ;;
    "run view"*--log*) echo "publish Application image: \`{IMAGE}\`" ;;
    "run view"*status,conclusion,jobs*)
        if [ -n "${{FAKE_GH_SLEEP-}}" ]; then sleep "$FAKE_GH_SLEEP" >/dev/null 2>&1; fi
        if [ -n "${{FAKE_GH_FAIL-}}" ]; then echo "no such run" >&2; exit 1; fi
        echo "${{FAKE_RUN_STATE:-completed|success|}}" ;;
    "run view"*headSha,event,conclusion*)
        if [ -n "${{FAKE_NEWER-}}" ]; then prepend "$FAKE_NEWER"; fi
        echo "${{FAKE_HEAD-}}|${{FAKE_EVENT:-workflow_dispatch}}|success" ;;
    "pr view"*) echo "${{FAKE_MERGEABLE:-MERGEABLE}}" ;;
    *) echo "unexpected gh $*" >&2; exit 9 ;;
esac
"""


def fake_gh(tmp_path, ci_runs=""):
    """Install the scripted gh; return its bin directory."""
    bin_dir = tmp_path / "bin"
    executable_stub(bin_dir, "gh", FAKE_GH)
    (tmp_path / "ci_runs").write_text(ci_runs)
    return bin_dir


def run_watch(tmp_path, *args, **env):
    """Run ci-watch.sh with the scripted gh; return (result, gh calls)."""
    bin_dir = fake_gh(tmp_path)
    result = subprocess.run(
        ["bash", str(OPS / "ci-watch.sh"), *args],
        env=clean_env(tmp_path, bin_dir, FAKE_DIR=str(tmp_path), **env),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, lines(tmp_path / "gh.calls")


def test_watch_refuses_bad_arguments_before_gh(tmp_path):
    """A non-numeric run or PR, or a bad limit, never reaches gh."""
    for n, (args, env, needle) in enumerate(
        (
            (("abc",), {}, "RUN_ID must be"),
            (("123", "x"), {}, "PR_NUMBER must be"),
            (("123",), {"STEWARDSHIP_WATCH_MINUTES": "-1"}, "WATCH_MINUTES"),
            (("123",), {"STEWARDSHIP_POLL_SECONDS": "soon"}, "POLL_SECONDS"),
        )
    ):
        result, calls = run_watch(tmp_path / str(n), *args, **env)
        assert result.returncode == 1, args
        assert needle in result.stderr and "refusing" in result.stderr
        assert calls == []
    for n, args in enumerate(((), ("1", "2", "3"))):
        result, calls = run_watch(tmp_path / f"u{n}", *args)
        assert result.returncode == 2 and "usage:" in result.stderr
        assert calls == []


def test_watch_refuses_a_run_it_cannot_read(tmp_path):
    """A first query that fails (a wrong run id) is a refusal, not a long wait."""
    result, calls = run_watch(tmp_path, "123", FAKE_GH_FAIL="1")
    assert result.returncode == 1
    assert "Cannot read Actions run 123" in result.stderr
    assert len(calls) == 1


def test_watch_outcomes(tmp_path):
    """Success, a failed job, a failed run and a conflict each end the watch."""
    result, calls = run_watch(tmp_path / "ok", "123", "45")
    assert result.returncode == 0 and "DONE: run 123" in result.stdout
    assert calls[0].startswith("gh run view 123 --repo epiphany40223/parishkit")
    result, _ = run_watch(
        tmp_path / "job", "123", FAKE_RUN_STATE="in_progress||Database shard 3"
    )
    assert result.returncode == 1
    assert "FAILED EARLY" in result.stdout and "Database shard 3" in result.stdout
    result, _ = run_watch(
        tmp_path / "run", "123", FAKE_RUN_STATE="completed|cancelled|"
    )
    assert result.returncode == 1 and "cancelled" in result.stdout
    result, _ = run_watch(
        tmp_path / "pr",
        "123",
        "45",
        FAKE_RUN_STATE="in_progress||",
        FAKE_MERGEABLE="CONFLICTING",
    )
    assert result.returncode == 1 and "MERGE CONFLICT" in result.stdout
    result, calls = run_watch(
        tmp_path / "repo", "123", STEWARDSHIP_GH_REPO="example/other"
    )
    assert "--repo example/other" in calls[0]


def test_watch_timeout_is_recorded(tmp_path):
    """At the limit it says what, the limit and the elapsed time, and keeps it."""
    result, _ = run_watch(
        tmp_path, "123", FAKE_RUN_STATE="in_progress||", STEWARDSHIP_WATCH_MINUTES="0"
    )
    assert result.returncode == 3
    record = (tmp_path / "ops.log").read_text()
    for text in (result.stderr, record):
        assert "timed out waiting for Actions run 123" in text
        assert "limit 0 minutes, elapsed " in text


# ---------------------------------------------------------------------------
# The release helper, in a temporary repository whose remote is configured
# as the GitHub URL and rewritten (url.insteadOf) to a local bare repository.

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}
GITHUB_URL = "https://github.com/epiphany40223/parishkit.git"


def git(*args, cwd):
    """Run git with a neutral configuration; return its stdout."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def release_repo(tmp_path, version="1.2.3"):
    """A checkout holding the release scripts, pushed to a bare remote."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    work, remote = tmp_path / "work", tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    git("init", "-q", "-b", "main", str(work), cwd=tmp_path)
    ops = work / "tools" / "stewardship-ops"
    ops.mkdir(parents=True)
    for name in ("release.sh", "ci-watch.sh", "lib.sh"):
        shutil.copy(OPS / name, ops / name)
    (work / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "release", cwd=work)
    git("remote", "add", "origin", GITHUB_URL, cwd=work)
    git("config", f"url.{remote}.insteadOf", GITHUB_URL, cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    return work, remote, git("rev-parse", "HEAD", cwd=work)


def run_release(tmp_path, work, *args, ci_runs="", stdin="", **env):
    """Run the copied release.sh; return (result, gh calls)."""
    import sys

    bin_dir = fake_gh(tmp_path, ci_runs)
    result = subprocess.run(
        ["bash", str(work / "tools" / "stewardship-ops" / "release.sh"), *args],
        env=clean_env(
            tmp_path,
            bin_dir,
            **{
                "FAKE_DIR": str(tmp_path),
                "FAKE_REMOTE": str(work.parent / "remote.git"),
                "STEWARDSHIP_PYTHON": sys.executable,
                **GIT_ENV,
                **env,
            },
        ),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, lines(tmp_path / "gh.calls")


def remote_tag(remote, tag):
    """`tag <commit>` for an annotated tag on the remote, or ''."""
    return git(
        "for-each-ref",
        f"refs/tags/{tag}",
        "--format=%(objecttype) %(*objectname)",
        cwd=remote,
    )


def test_release_refuses_bad_arguments_before_anything(tmp_path):
    """A bad VERSION or run id, or an unknown option, reaches neither git nor gh."""
    work, remote, _ = release_repo(tmp_path)
    for n, (args, code, needle) in enumerate(
        (
            (("1.2",), 1, "VERSION must be"),
            (("v1.2.3",), 1, "VERSION must be"),
            (("01.2.3",), 1, "VERSION must be"),
            (("1.2.3", "run"), 1, "CI_RUN_ID must be"),
            ((), 2, "usage:"),
            (("--force", "1.2.3"), 2, "usage:"),
            (("1.2.3", "1", "2"), 2, "usage:"),
        )
    ):
        result, calls = run_release(tmp_path / str(n), work, *args)
        assert result.returncode == code, args
        assert needle in result.stderr, args
        assert calls == []
    assert remote_tag(remote, "v1.2.3") == ""


def test_release_refuses_a_remote_that_is_not_the_repository(tmp_path):
    """The remote's configured URL must name STEWARDSHIP_GH_REPO on GitHub."""
    work, remote, _ = release_repo(tmp_path)
    result, calls = run_release(
        tmp_path / "repo", work, "--yes", "1.2.3", STEWARDSHIP_GH_REPO="other/fork"
    )
    assert result.returncode == 1 and "not GitHub's other/fork" in result.stderr
    assert calls == []
    git("remote", "set-url", "origin", str(remote), cwd=work)
    result, calls = run_release(tmp_path / "path", work, "--yes", "1.2.3")
    assert result.returncode == 1 and "Git remote origin is" in result.stderr
    assert calls == []


def test_release_refuses_a_version_main_does_not_carry(tmp_path):
    """The committed version, read with tomllib, must be VERSION."""
    work, remote, _ = release_repo(tmp_path, version="1.2.2")
    result, calls = run_release(tmp_path, work, "--yes", "1.2.3", "77")
    assert result.returncode == 1
    assert "is version '1.2.2', not 1.2.3" in result.stderr
    assert "land the version bump first" in result.stderr
    assert calls == []
    assert remote_tag(remote, "v1.2.3") == ""
    result, _ = run_release(
        tmp_path / "py", work, "--yes", "1.2.3", STEWARDSHIP_PYTHON="false"
    )
    assert result.returncode == 1 and "Cannot read the project version" in result.stderr


def test_release_refuses_an_existing_tag(tmp_path):
    """A tag already on the remote (or in the checkout) is never moved."""
    work, remote, _ = release_repo(tmp_path)
    git("tag", "v1.2.3", cwd=work)
    git("push", "-q", "origin", "v1.2.3", cwd=work)
    result, _ = run_release(tmp_path / "local", work, "--yes", "1.2.3", "77")
    assert result.returncode == 1 and "already exists in this checkout" in result.stderr
    git("tag", "-d", "v1.2.3", cwd=work)
    result, calls = run_release(tmp_path / "remote", work, "--yes", "1.2.3", "77")
    assert result.returncode == 1 and "already exists on origin" in result.stderr
    assert calls == []


def test_release_refuses_a_run_release_yml_would_not_check(tmp_path):
    """The named run must be the newest dispatch CI run on main's head."""
    work, remote, sha = release_repo(tmp_path)
    result, calls = run_release(
        tmp_path, work, "--yes", "1.2.3", "77", ci_runs="78\n77\n", FAKE_HEAD=sha
    )
    assert result.returncode == 1
    assert "not the newest workflow_dispatch CI run" in result.stderr
    assert f"--commit {sha}" in calls[0]
    assert not any("workflow run" in c for c in calls)
    assert remote_tag(remote, "v1.2.3") == ""


def test_release_refuses_a_failed_mismatched_or_superseded_run(tmp_path):
    """A failed watch, a run on another event, or a newer run never tags."""
    work, remote, sha = release_repo(tmp_path)
    common = ("--yes", "1.2.3", "77")
    result, _ = run_release(
        tmp_path / "fail",
        work,
        *common,
        ci_runs="77\n",
        FAKE_HEAD=sha,
        FAKE_RUN_STATE="completed|failure|",
    )
    assert result.returncode == 1 and "did not pass; not tagging" in result.stderr
    result, _ = run_release(
        tmp_path / "event",
        work,
        *common,
        ci_runs="77\n",
        FAKE_HEAD=sha,
        FAKE_EVENT="push",
    )
    assert result.returncode == 1 and "/push/success, not" in result.stderr
    result, _ = run_release(
        tmp_path / "newer",
        work,
        *common,
        ci_runs="77\n",
        FAKE_HEAD=sha,
        FAKE_NEWER="80",
    )
    assert result.returncode == 1 and "A newer CI run (80)" in result.stderr
    result, _ = run_release(
        tmp_path / "slow",
        work,
        *common,
        ci_runs="77\n",
        FAKE_HEAD=sha,
        FAKE_RUN_STATE="in_progress||",
        STEWARDSHIP_WATCH_MINUTES="0",
    )
    assert result.returncode == 3 and "timed out waiting for Actions run 77" in (
        result.stderr
    )
    assert remote_tag(remote, "v1.2.3") == ""


def test_release_with_a_named_run_tags_and_prints_the_digest(tmp_path):
    """Typed confirmation, annotated tag at main's head, digest on stdout."""
    work, remote, sha = release_repo(tmp_path)
    common = {"ci_runs": "77\n", "FAKE_HEAD": sha, "FAKE_RELEASE_RUN": "99"}
    result, calls = run_release(
        tmp_path / "wrong", work, "1.2.3", "77", stdin="yes\n", **common
    )
    assert result.returncode == 1 and "not confirmed" in result.stderr
    assert remote_tag(remote, "v1.2.3") == ""
    result, calls = run_release(
        tmp_path / "ok",
        work,
        "1.2.3",
        "77",
        stdin="v1.2.3\n",
        FAKE_OLD_RELEASE="98",
        **common,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == IMAGE + "\n"
    assert remote_tag(remote, "v1.2.3") == f"tag {sha}"
    assert git("tag", "-l", "--format=%(contents:subject)", "v1.2.3", cwd=work) == (
        "ParishKit 1.2.3"
    )
    assert not any("workflow run" in c for c in calls)
    release_list = next(c for c in calls if "release.yml" in c)
    assert f"--branch v1.2.3 --commit {sha} --event push" in release_list
    # The run that existed before the push (an old run of a re-pushed tag)
    # is never the one watched.
    assert "gh run view 99 --repo epiphany40223/parishkit --log" in calls
    assert not any(c.startswith("gh run view 98 ") for c in calls)


def test_release_deletes_the_local_tag_when_the_push_fails(tmp_path):
    """A rejected push leaves no local tag behind and says what to check."""
    work, remote, sha = release_repo(tmp_path)
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    result, _ = run_release(
        tmp_path / "push", work, "--yes", "1.2.3", "77", ci_runs="77\n", FAKE_HEAD=sha
    )
    assert result.returncode == 1
    assert "the local tag was deleted again" in result.stderr
    assert git("tag", "-l", "v1.2.3", cwd=work) == ""


def test_release_dispatches_and_finds_a_new_run(tmp_path):
    """Without a run id it dispatches CI and waits for the run that is new."""
    work, remote, sha = release_repo(tmp_path)
    result, calls = run_release(
        tmp_path,
        work,
        "--yes",
        "1.2.3",
        ci_runs="50\n",
        FAKE_HEAD=sha,
        FAKE_NEW_RUN="51",
        FAKE_RELEASE_RUN="99",
    )
    assert result.returncode == 0, result.stderr
    assert "gh workflow run ci.yml --repo epiphany40223/parishkit --ref main" in calls
    assert any(c.startswith("gh run view 51 ") for c in calls)
    assert not any(c.startswith("gh run view 50 ") for c in calls)
    assert remote_tag(remote, "v1.2.3") == f"tag {sha}"


def test_release_timeouts_are_recorded(tmp_path):
    """A dispatched run or release run that never appears is a recorded timeout."""
    work, remote, sha = release_repo(tmp_path)
    result, _ = run_release(
        tmp_path / "ci",
        work,
        "--yes",
        "1.2.3",
        ci_runs="50\n",
        FAKE_HEAD=sha,
        STEWARDSHIP_FIND_MINUTES="0",
    )
    assert result.returncode == 3
    assert "the dispatched CI run on " + sha in result.stderr
    assert "limit 0 minutes, elapsed" in (tmp_path / "ci" / "ops.log").read_text()
    assert remote_tag(remote, "v1.2.3") == ""
    result, _ = run_release(
        tmp_path / "rel",
        work,
        "--yes",
        "1.2.3",
        "50",
        ci_runs="50\n",
        FAKE_HEAD=sha,
        FAKE_OLD_RELEASE="98",
        STEWARDSHIP_FIND_MINUTES="0",
    )
    assert result.returncode == 3
    assert "the release.yml run for v1.2.3 to appear" in result.stderr


# ---------------------------------------------------------------------------
# The host scripts, with a recording ssh. It records its options and the
# remote command (its last argument). A psql call prints the next line of
# psql.lines when that file has one, else FAKE_PSQL; any other remote
# command prints the log counts. FAKE_SSH_STATUS is its exit status and
# FAKE_SSH_SLEEP makes it hang that many seconds first.
FAKE_SSH = r"""
remote=${@: -1}
printf '%s\n' "$*" >>"$FAKE_DIR/ssh.argv"
printf '%s\n' "$remote" >>"$FAKE_DIR/ssh.calls"
# The sleep holds no output pipe, as real ssh leaves none behind when killed.
if [ -n "${FAKE_SSH_SLEEP-}" ]; then sleep "$FAKE_SSH_SLEEP" >/dev/null 2>&1; fi
case "$remote" in
    *psql*)
        cat >>"$FAKE_DIR/ssh.stdin"
        queue=$FAKE_DIR/psql.lines
        if [ -s "$queue" ]; then
            head -n 1 "$queue"
            tail -n +2 "$queue" >"$queue.new"
            mv "$queue.new" "$queue"
        else
            printf '%s\n' "${FAKE_PSQL-}"
        fi ;;
    *) echo "deadlock=0 errors=1" ;;
esac
exit "${FAKE_SSH_STATUS:-0}"
"""


def run_host(tmp_path, script, *args, unset=(), psql_lines=(), **env):
    """Run a host script with the recording ssh; return (result, calls, stdin)."""
    bin_dir = tmp_path / "bin"
    executable_stub(bin_dir, "ssh", FAKE_SSH)
    if psql_lines:
        (tmp_path / "psql.lines").write_text("".join(f"{x}\n" for x in psql_lines))
    full = clean_env(
        tmp_path,
        bin_dir,
        **{"FAKE_DIR": str(tmp_path), "STEWARDSHIP_HOST": "host.invalid", **env},
    )
    for name in unset:
        full.pop(name, None)
    result = subprocess.run(
        ["bash", str(script), *args],
        env=full,
        capture_output=True,
        text=True,
        timeout=30,
    )
    stdin = tmp_path / "ssh.stdin"
    return (
        result,
        lines(tmp_path / "ssh.calls"),
        (stdin.read_text() if stdin.exists() else ""),
    )


def psql_argv(call):
    """The remote psql command, split, with the statement_timeout -c checked."""
    argv = shlex.split(call)
    assert argv[:2] == ["docker", "exec"]
    assert argv[-4:] == ["-c", "SET statement_timeout = '5min'", "-f", "-"]
    return argv


WINDOW = ("--since", "2026-10-03 12:00Z", "--until", "now")


@pytest.mark.parametrize(
    "script,args",
    [
        ("predeploy-check.sh", ()),
        ("send-monitor.sh", ()),
        ("send-report.sh", WINDOW),
    ],
)
def test_host_scripts_refuse_a_bad_setting_before_ssh(tmp_path, script, args):
    """Host, project, database, time zone and limits are checked first."""
    result, calls, _ = run_host(
        tmp_path / "host", OPS / script, *args, unset=("STEWARDSHIP_HOST",)
    )
    assert result.returncode == 1 and "Set STEWARDSHIP_HOST" in result.stderr
    assert calls == []
    for n, (name, value) in enumerate(
        (
            ("STEWARDSHIP_HOST", "-oProxyCommand=x"),
            ("STEWARDSHIP_PROJECT", "x; rm -rf /"),
            ("STEWARDSHIP_DATABASE", "db name"),
            ("STEWARDSHIP_STATEMENT_TIMEOUT", "forever"),
            ("STEWARDSHIP_REMOTE_SECONDS", "-5"),
            ("STEWARDSHIP_REMOTE_SECONDS", "0"),
            ("STEWARDSHIP_STATEMENT_TIMEOUT", "5ms"),
        )
    ):
        result, calls, _ = run_host(
            tmp_path / str(n), OPS / script, *args, **{name: value}
        )
        assert result.returncode == 1 and name in result.stderr, name
        assert calls == []
    if script != "send-report.sh":
        result, calls, _ = run_host(
            tmp_path / "tz", OPS / script, STEWARDSHIP_TIMEZONE="UTC'; DROP"
        )
        assert result.returncode == 1 and "STEWARDSHIP_TIMEZONE" in result.stderr
        assert calls == []


def test_ssh_carries_the_dead_network_options(tmp_path):
    """Every ssh call bounds connecting and a silent connection."""
    run_host(tmp_path, OPS / "predeploy-check.sh", FAKE_PSQL="predeploy: blocking=0")
    argv = lines(tmp_path / "ssh.argv")[0]
    assert argv.startswith(
        "-o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 "
        "-- host.invalid docker exec"
    )


def test_predeploy_runs_its_sql_read_only_and_reads_the_count(tmp_path):
    """Generic psql over ssh, the SQL file on stdin, exit status from the count."""
    script = OPS / "predeploy-check.sh"
    result, calls, stdin = run_host(
        tmp_path / "clear", script, FAKE_PSQL="rows\npredeploy: blocking=0"
    )
    assert result.returncode == 0, result.stderr
    assert "Clear to upgrade" in result.stderr and "rows" in result.stdout
    assert calls[0].startswith(PSQL_PREFIX + " -q -v tz=UTC -c ")
    psql_argv(calls[0])
    sql = (OPS / "predeploy.sql").read_text()
    assert stdin == sql
    assert "BEGIN READ ONLY;" in sql and sql.rstrip().endswith("ROLLBACK;")
    result, _, _ = run_host(
        tmp_path / "busy", script, FAKE_PSQL="predeploy: blocking=2"
    )
    assert result.returncode == 1 and "Not clear to upgrade: 2" in result.stderr
    result, _, _ = run_host(tmp_path / "none", script, FAKE_PSQL="garbage")
    assert result.returncode == 1 and "no blocking count" in result.stderr
    result, calls, _ = run_host(
        tmp_path / "env",
        script,
        FAKE_PSQL="predeploy: blocking=0",
        STEWARDSHIP_PROJECT="pk",
        STEWARDSHIP_DATABASE="db",
        STEWARDSHIP_TIMEZONE="America/New_York",
    )
    argv = psql_argv(calls[0])
    assert argv[3] == "pk-postgres-1" and argv[argv.index("-d") + 1] == "db"
    assert "tz=America/New_York" in argv
    result, calls, _ = run_host(tmp_path / "arg", script, "extra")
    assert result.returncode == 2 and calls == []


def test_predeploy_failures_and_timeouts(tmp_path):
    """A failed ssh or psql is a refusal; a hung one is a recorded timeout."""
    script = OPS / "predeploy-check.sh"
    result, _, _ = run_host(
        tmp_path / "fail", script, FAKE_PSQL="ERROR: boom", FAKE_SSH_STATUS="2"
    )
    assert result.returncode == 1
    assert "The checks did not run (ssh or psql exited 2" in result.stderr
    result, _, _ = run_host(
        tmp_path / "hang",
        script,
        FAKE_SSH_SLEEP="10",
        STEWARDSHIP_REMOTE_SECONDS="1",
    )
    assert result.returncode == 3
    record = (tmp_path / "hang" / "ops.log").read_text()
    assert "timed out waiting for ssh to the host (docker exec)" in record
    assert "limit 1 s, elapsed" in record


def test_send_monitor_refuses_bad_options_before_ssh(tmp_path):
    """Bad purpose, mode, since or limit, or an unknown option, never reach ssh."""
    script = OPS / "send-monitor.sh"
    for n, (args, code, needle) in enumerate(
        (
            (("-m", "live"), 1, "MODE must be"),
            (("-p", "Initial;"), 1, "PURPOSE must be"),
            (("-n", "soon"), 1, "MINUTES must be"),
            (("-s", "yesterday"), 1, "SINCE must be"),
            (("-s", "now"), 1, "SINCE must be a fixed timestamp"),
            (("-s", "2026-10-03'; DROP"), 1, "SINCE must be"),
            (("-x",), 2, "usage:"),
            (("extra",), 2, "usage:"),
        )
    ):
        result, calls, _ = run_host(tmp_path / str(n), script, *args)
        assert result.returncode == code, args
        assert needle in result.stderr, args
        assert calls == []


def test_send_monitor_settles_only_after_seeing_work(tmp_path):
    """A first poll with nothing pending never settles; pending then done does."""
    result, calls, stdin = run_host(
        tmp_path,
        OPS / "send-monitor.sh",
        "-p",
        "reminder",
        "-m",
        "testing",
        "-s",
        "2026-10-03 12:00Z",
        psql_lines=(
            "11:59:00 | delivered=10 | accepted_last_min=0",
            "12:00:00 | pending=3 submitting=1 | accepted_last_min=4",
            "12:01:00 | delivered=14 | accepted_last_min=4",
        ),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout.splitlines()
    assert len(out) == 4 and out[-1].startswith("settled")
    assert "deadlock=0 errors=1" in out[0]
    psql = psql_argv(calls[0])
    for value in ("purpose=reminder", "mode=testing", "since=2026-10-03 12:00Z"):
        assert value in psql, value
    assert stdin == (OPS / "send-monitor.sql").read_text() * 3
    # The log counts run on the host for this project's labelled containers.
    counts = shlex.split(calls[1])
    assert counts[:2] == ["sh", "-c"] and counts[-2:] == ["stewardship", "10s"]
    assert "label=com.docker.compose.project=$p" in counts[2]


def test_send_monitor_defaults_since_and_records_its_timeout(tmp_path):
    """Without -s it counts from shortly before it started; the limit is recorded."""
    result, calls, _ = run_host(
        tmp_path,
        OPS / "send-monitor.sh",
        "-n",
        "0",
        FAKE_PSQL="12:00:00 | delivered=5 pending=3 | accepted_last_min=0",
    )
    assert result.returncode == 3
    assert "settled" not in result.stdout
    since = next(a for a in psql_argv(calls[0]) if a.startswith("since="))
    assert re.fullmatch(r"since=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", since)
    record = (tmp_path / "ops.log").read_text()
    assert "the production initial send to settle" in record
    assert "limit 0 minutes, elapsed" in record


# ---------------------------------------------------------------------------
# The send report runner: its SQL is the guide's.


def guide_report_sql(text):
    """The guide's report, found independently of the script's awk."""
    section = text.split("\n## The report\n", 1)[1]
    return section.split("```sql\n", 1)[1].split("\n```\n", 1)[0]


def test_send_report_takes_its_sql_from_the_guide(tmp_path):
    """--print-sql is exactly the guide's block, read at run time."""
    result, calls, _ = run_host(tmp_path, OPS / "send-report.sh", "--print-sql")
    assert result.returncode == 0, result.stderr
    assert result.stdout == guide_report_sql(REPORT_GUIDE.read_text()) + "\n"
    assert result.stdout.startswith("BEGIN;\n")
    assert result.stdout.endswith("ROLLBACK;\n")
    assert "-- 9. What batching saved" in result.stdout
    assert calls == []
    # The script itself carries none of it.
    assert "send_outcome" not in (OPS / "send-report.sh").read_text()


def report_tree(tmp_path, guide_text):
    """A copy of the script beside a guide with the given text."""
    ops = tmp_path / "tools" / "stewardship-ops"
    ops.mkdir(parents=True)
    for name in ("send-report.sh", "lib.sh"):
        shutil.copy(OPS / name, ops / name)
    guides = tmp_path / "docs" / "guides"
    guides.mkdir(parents=True)
    (guides / "stewardship-mail-send-report.md").write_text(guide_text)
    return ops / "send-report.sh"


@pytest.mark.parametrize(
    "guide_text",
    [
        "# No report here\n",
        "## The report\n\nNo SQL block.\n",
        "## The report\n\n```sql\nSELECT 1;\n```\n",
        "## The report\n\n```sql\nBEGIN;\nSELECT 1;\n```\n",
        "## Elsewhere\n\n```sql\nBEGIN;\nROLLBACK;\n```\n",
    ],
)
def test_send_report_refuses_a_guide_without_the_report(tmp_path, guide_text):
    """A reworded or missing block is refused before anything reaches the host."""
    script = report_tree(tmp_path, guide_text)
    result, calls, _ = run_host(tmp_path, script, *WINDOW)
    assert result.returncode == 1
    assert '"The report" SQL block was not found' in result.stderr
    assert calls == []
    # A guide that has the block is used as it stands.
    script = report_tree(
        tmp_path / "good",
        "## The report\n\n```sql\nBEGIN;\nSELECT 2;\nROLLBACK;\n```\n",
    )
    result, _, stdin = run_host(tmp_path / "good", script, *WINDOW)
    assert result.returncode == 0, result.stderr
    assert stdin == "BEGIN;\nSELECT 2;\nROLLBACK;\n"


def test_send_report_refuses_bad_options_before_ssh(tmp_path):
    """Window, purpose and definition are checked before the host is reached."""
    script = OPS / "send-report.sh"
    for n, (args, code, needle) in enumerate(
        (
            (("--since", "2026-10-03"), 2, "Both --since and --until"),
            ((), 2, "Both --since and --until"),
            (("--since", "x", "--until", "now"), 1, "--since must be"),
            (("--since", "now", "--until", "2026-10-03 25"), 1, "--until must be"),
            ((*WINDOW, "--purpose", "spam"), 1, "Unknown --purpose"),
            ((*WINDOW, "--definition", "123"), 1, "--definition must be"),
            (("--since",), 2, "usage:"),
            (("--bogus",), 2, "usage:"),
        )
    ):
        result, calls, _ = run_host(tmp_path / str(n), script, *args)
        assert result.returncode == code, args
        assert needle in result.stderr, args
        assert calls == []


def test_send_report_passes_the_variables_to_psql(tmp_path):
    """The guide's psql variables arrive intact, the SQL on stdin."""
    definition = "0" * 8 + "-0000-4000-8000-" + "0" * 12
    result, calls, stdin = run_host(
        tmp_path,
        OPS / "send-report.sh",
        "--since",
        "2026-10-03 12:00Z",
        "--until",
        "2026-10-04T00:00:00+00:00",
        "--purpose",
        "initial",
        "--definition",
        definition,
    )
    assert result.returncode == 0, result.stderr
    assert calls[0].startswith(PSQL_PREFIX + " ")
    psql = psql_argv(calls[0])
    for value in (
        "since=2026-10-03 12:00Z",
        "until=2026-10-04T00:00:00+00:00",
        "purpose=initial",
        f"definition={definition}",
    ):
        assert value in psql, value
    assert stdin == guide_report_sql(REPORT_GUIDE.read_text()) + "\n"
    # The empty purpose (all Family mail) and definition are passed as empty.
    result, calls, _ = run_host(tmp_path / "all", OPS / "send-report.sh", *WINDOW)
    assert result.returncode == 0
    assert "purpose=" in psql_argv(calls[0])
    assert "definition=" in psql_argv(calls[0])


# ---------------------------------------------------------------------------
# ops_limited's edges: a first gh query that hangs, Ctrl-C during a limited
# call, and no watcher left behind after a call that finished in time.


def test_watch_first_query_timeout_exits_3(tmp_path):
    """A first query past its limit is a recorded timeout, not a refusal."""
    result, _ = run_watch(
        tmp_path, "123", FAKE_GH_SLEEP="10", STEWARDSHIP_GH_SECONDS="1"
    )
    assert result.returncode == 3
    assert "Cannot read" not in result.stderr
    assert "timed out waiting for gh run view: limit 1 s" in (
        (tmp_path / "ops.log").read_text()
    )


def test_ctrl_c_stops_a_limited_call_at_once(tmp_path):
    """SIGINT to the process group ends the script with 130, not at the limit."""
    import signal
    import time

    bin_dir = tmp_path / "bin"
    executable_stub(bin_dir, "ssh", FAKE_SSH)
    env = clean_env(
        tmp_path,
        bin_dir,
        FAKE_DIR=str(tmp_path),
        FAKE_SSH_SLEEP="30",
        STEWARDSHIP_HOST="host.invalid",
        STEWARDSHIP_REMOTE_SECONDS="60",
    )
    process = subprocess.Popen(
        ["bash", str(OPS / "predeploy-check.sh")],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Wait until the stand-in ssh is running, as a terminal user would see.
    calls = tmp_path / "ssh.calls"
    deadline = time.monotonic() + 10
    while not calls.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert calls.exists()
    started = time.monotonic()
    os.killpg(process.pid, signal.SIGINT)
    assert process.wait(timeout=10) == 130
    assert time.monotonic() - started < 5
    assert not (tmp_path / "ops.log").exists()


def test_a_finished_call_leaves_no_watcher(tmp_path):
    """The watcher's sleep goes with it once the limited call returns."""
    if shutil.which("pgrep") is None:
        pytest.skip("pgrep is not installed")
    result, _, _ = run_host(
        tmp_path,
        OPS / "predeploy-check.sh",
        FAKE_PSQL="predeploy: blocking=0",
        STEWARDSHIP_REMOTE_SECONDS="4242",
    )
    assert result.returncode == 0, result.stderr
    stray = subprocess.run(["pgrep", "-f", "sleep 4242"], capture_output=True)
    assert stray.returncode == 1, stray.stdout
