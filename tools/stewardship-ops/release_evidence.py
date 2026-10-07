#!/usr/bin/env python3
"""Find the full CI run that is a release's evidence (issue #662).

A release needs a successful full CI run ("CI (jobs: all)", a
workflow_dispatch of ci.yml) on some commit X whose tree is identical to the
tagged commit's, or differs only in docs-safe paths. X need not be an
ancestor of the tag: a nightly train head that was tested once and then
merged into main in the same order has main's tree under a different SHA.

Both release.yml (independently, on every tag push) and release.sh (before
it tags) ask this script which run decides, so the rule lives in one place:

- Only runs named exactly "CI (jobs: all)" from a workflow_dispatch count; a
  jobs=affected dispatch may have skipped job groups (#626).
- Only the newest such run on each commit counts, so a later failed or
  cancelled full run on X withdraws X's older success.
- Runs are considered newest first, and the first whose commit qualifies
  decides, whatever its state: a pending run is waited for, a failed one
  refuses the release. Its commit qualifies when the two trees differ in no
  path outside the docs-safe allowlist (or not at all).
- A run whose commit cannot be read (not fetchable from the remote, such as
  a deleted train branch) and that is newer than any qualifying run decides
  as "unreadable", which callers refuse: it might have been a newer failure.

The allowlist is closed and conservative: Markdown under docs/ and the two
root agent instruction files. It never covers README.md (the image and the
package metadata include it), the operator guide whose SQL send-report.sh
runs, workflows, tools, src, tests, schema, deployment, or requirements.
Documentation is still a test input (Markdown lint, traceability and
documentation contracts), so release.yml re-runs those checks on the tagged
tree whenever the deciding run's tree differs.

Usage:
    release_evidence.py select --repo OWNER/NAME --commit SHA
        [--checkout DIR] [--remote NAME] [--limit N]
    release_evidence.py docs-tests

prints "RUN_ID STATUS CONCLUSION HEAD_SHA DOCS_PATHS" (tab separated;
STATUS is "unreadable" for an unreadable run; DOCS_PATHS counts the
docs-safe paths that differ) for the deciding run, or nothing when no full
run qualifies. Progress and failures go to stderr. Exit status 3 means a git
or gh call passed its time limit; 1 means gh failed every attempt.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import PurePosixPath

FULL_RUN_TITLE = "CI (jobs: all)"
TIMEOUT_STATUS = 3
GIT_SECONDS = 120
GH_SECONDS = 60
GH_ATTEMPTS = 3
PROBE_ATTEMPTS = 2
PROBE_RETRY_SECONDS = 5

# Root files that are documentation only. README.md is deliberately absent:
# the application image copies it and pyproject.toml uses it as metadata.
DOCS_SAFE_ROOT_FILES = frozenset({"AGENTS.md", "CLAUDE.md"})

# Markdown under docs/ that a tool executes: send-report.sh runs this
# guide's SQL against a deployment, so a change to it changes behavior.
NOT_DOCS_SAFE = frozenset({"docs/guides/stewardship-mail-send-report.md"})


# The tests that read documentation (guides, specs, the root instructions),
# need no database, and run fast. When the evidence tree differs from the
# tagged one, release.yml and release.sh run them, with Markdown lint, on
# the tagged tree (`docs-tests` prints them).
DOCS_TESTS = (
    "tests/stewardship/test_traceability.py",
    "tests/stewardship/test_build.py",
    "tests/stewardship/test_ops_scripts.py",
    "tests/stewardship/test_local_script.py",
)


class TimedOut(Exception):
    """A git or gh call passed its limit (already logged)."""


def log(message):
    """One progress or failure line on stderr."""
    print(f"release_evidence: {message}", file=sys.stderr, flush=True)


def docs_safe(path):
    """Whether a changed path cannot change what was built, tested or run."""
    if path in NOT_DOCS_SAFE:
        return False
    if path in DOCS_SAFE_ROOT_FILES:
        return True
    parts = PurePosixPath(path).parts
    return len(parts) > 1 and parts[0] == "docs" and path.endswith(".md")


def limited(what, limit, command):
    """Run a command with a time limit; log and raise TimedOut past it.

    The command runs in its own process group, and a timeout kills the whole
    group: a helper that git starts (ssh, a remote helper) would otherwise
    keep the output pipes open and the wait would hang.
    """
    start = time.monotonic()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        elapsed = time.monotonic() - start
        log(f"timed out: {what} (limit {limit} s, elapsed {elapsed:.0f} s)")
        raise TimedOut(what) from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def differing_paths(checkout, remote, base, target):
    """Every path whose content, mode or type differs between two commits.

    Whether base is readable is decided by the remote, never by this
    checkout: base is fetched from the remote's URL by SHA into an empty
    scratch repository (commit only, no trees), so a commit that only an
    operator's checkout still holds (an abandoned train branch) reads as
    unreadable here exactly as it does in release.yml's fresh clone. The
    checkout then fetches base too when it lacks it, for the comparison.
    Returns None, logging why, when base cannot be read; that never
    qualifies. diff-tree is plumbing, so user diff configuration cannot
    change its output; renames are off so both sides of a move are checked,
    and no submodule change is ignored.
    """

    def git(what, *args, where=checkout):
        return limited(what, GIT_SECONDS, ["git", "-C", where, *args])

    url = git("git remote get-url", "remote", "get-url", remote)
    if url.returncode:
        log(f"cannot read the URL of remote {remote}: {url.stderr.strip()}")
        return None
    # One retry, as for gh: a network blip must not read as a missing commit.
    for attempt in range(1, PROBE_ATTEMPTS + 1):
        with tempfile.TemporaryDirectory() as scratch:
            git("git init", "init", "-q", where=scratch)
            probe = git(
                f"git fetch of {base} into a scratch repository",
                "fetch",
                "-q",
                "--no-tags",
                "--depth=1",
                "--filter=tree:0",
                url.stdout.strip(),
                base,
                where=scratch,
            )
        if probe.returncode == 0:
            break
        reason = probe.stderr.strip()
        log(f"cannot fetch {base} from {remote} (attempt {attempt}): {reason}")
        if attempt < PROBE_ATTEMPTS:
            time.sleep(PROBE_RETRY_SECONDS)
    else:
        return None
    if git("git cat-file", "cat-file", "-e", f"{base}^{{commit}}").returncode:
        fetch = git(f"git fetch of {base}", "fetch", "-q", "--no-tags", remote, base)
        if fetch.returncode:
            log(f"cannot fetch {base} from {remote}: {fetch.stderr.strip()}")
            return None
    diff = git(
        f"git diff-tree {base} {target}",
        "diff-tree",
        "-r",
        "--no-renames",
        "--name-only",
        "-z",
        "--ignore-submodules=none",
        base,
        target,
    )
    if diff.returncode:
        log(f"cannot compare {base} with {target}: {diff.stderr.strip()}")
        return None
    return [path for path in diff.stdout.split("\0") if path]


def select(runs, target, paths_between):
    """Return (run, paths) for the deciding run, or None.

    runs are newest first; paths_between(base, target) returns the differing
    paths, or None when base cannot be read. An unreadable newest-per-commit
    full run decides with paths None.
    """
    seen = set()
    for run in runs:
        if (
            run.get("displayTitle") != FULL_RUN_TITLE
            or run.get("event") != "workflow_dispatch"
        ):
            continue
        sha = run["headSha"]
        if sha in seen:
            continue
        seen.add(sha)
        paths = paths_between(sha, target)
        if paths is None or all(docs_safe(path) for path in paths):
            return run, paths
    return None


def list_runs(repo, limit):
    """The newest workflow_dispatch runs of ci.yml, newest first.

    Retries a failed gh call a few times (a network blip should not fail a
    release); a call past its limit is not retried.
    """
    command = [
        "gh",
        "run",
        "list",
        "--repo",
        repo,
        "--workflow",
        "ci.yml",
        "--event",
        "workflow_dispatch",
        "--limit",
        str(limit),
        "--json",
        "databaseId,headSha,status,conclusion,displayTitle,event",
    ]
    for attempt in range(1, GH_ATTEMPTS + 1):
        result = limited("gh run list", GH_SECONDS, command)
        if result.returncode == 0:
            return json.loads(result.stdout)
        log(f"gh run list failed (attempt {attempt}): {result.stderr.strip()}")
        if attempt < GH_ATTEMPTS:
            time.sleep(5 * attempt)
    raise RuntimeError("gh run list failed")


def main(argv=None):
    """Print the deciding run for the select command."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    choose = sub.add_parser("select")
    choose.add_argument("--repo", required=True)
    choose.add_argument("--commit", required=True)
    choose.add_argument("--checkout", default=".")
    choose.add_argument("--remote", default="origin")
    choose.add_argument("--limit", type=int, default=100)
    sub.add_parser("docs-tests")
    args = parser.parse_args(argv)
    if args.command == "docs-tests":
        print("\n".join(DOCS_TESTS))
        return 0
    try:
        runs = list_runs(args.repo, args.limit)
        chosen = select(
            runs,
            args.commit,
            lambda base, target: differing_paths(
                args.checkout, args.remote, base, target
            ),
        )
    except TimedOut:
        return TIMEOUT_STATUS
    except RuntimeError as error:
        log(str(error))
        return 1
    log(f"scanned {len(runs)} dispatched CI runs for {args.commit}")
    if chosen is not None:
        run, paths = chosen
        status = run["status"] if paths is not None else "unreadable"
        fields = (run["databaseId"], status, run["conclusion"] or "-")
        count = len(paths) if paths is not None else 0
        print(*fields, run["headSha"], count, sep="\t")
    return 0


if __name__ == "__main__":
    sys.exit(main())
