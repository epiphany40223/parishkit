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

With --run (release.sh passes the run it means to use), that run is also
read directly (#730). The listing alone still decides, as it does for
release.yml, and it is used only when it agrees with the direct read. While
it disagrees (the run missing from a partial page, listed in a stale state,
or not deciding) it is read again a bounded number of times; a disagreement
that persists is refused: no run decides.

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
        [--run ID [--retry-seconds N]]
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
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
TIMEOUT_STATUS = 3
GIT_SECONDS = 120
GH_SECONDS = 60
GH_ATTEMPTS = 3
PROBE_ATTEMPTS = 2
PROBE_RETRY_SECONDS = 5
# Listings of a named run's candidates, and the pause unit between them.
LIST_ATTEMPTS = 4
LIST_RETRY_SECONDS = 10

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
        if not is_full_run(run):
            continue
        sha = run["headSha"]
        if sha in seen:
            continue
        seen.add(sha)
        paths = paths_between(sha, target)
        if paths is None or all(docs_safe(path) for path in paths):
            return run, paths
    return None


def gh_json(what, command):
    """Run a gh command that prints JSON; return the parsed output.

    Retries a failed gh call a few times (a network blip should not fail a
    release); a call past its limit is not retried.
    """
    for attempt in range(1, GH_ATTEMPTS + 1):
        result = limited(what, GH_SECONDS, command)
        if result.returncode == 0:
            return json.loads(result.stdout)
        log(f"{what} failed (attempt {attempt}): {result.stderr.strip()}")
        if attempt < GH_ATTEMPTS:
            time.sleep(5 * attempt)
    raise RuntimeError(f"{what} failed")


def view_run(repo, run_id):
    """One run read directly, shaped as gh run list reports it.

    The REST record names the workflow file (`path`), which gh run view
    does not, so a run of another workflow that happens to share the name
    and run name can never pass as a full CI run.
    """
    record = gh_json(
        f"gh api of run {run_id}",
        ["gh", "api", f"repos/{repo}/actions/runs/{run_id}"],
    )
    return {
        "databaseId": record["id"],
        "headSha": record["head_sha"],
        "status": record["status"],
        # gh run list reports a pending run's conclusion as "", REST as null.
        "conclusion": record["conclusion"] or "",
        "displayTitle": record["display_title"],
        "event": record["event"],
        "path": record["path"],
    }


def is_full_run(run):
    """Whether a run is a full CI dispatch (the listing filters workflow)."""
    return (
        run.get("displayTitle") == FULL_RUN_TITLE
        and run.get("event") == "workflow_dispatch"
        and run.get("path", CI_WORKFLOW_PATH) == CI_WORKFLOW_PATH
    )


def listing_doubt(runs, named, chosen):
    """Why the listing disagrees with the direct read of a named run, or None.

    A listing can be a partial page or lag a run that just started, finished
    or was re-run under the same id (#730). release.yml decides from the
    listing alone, so release.sh may use a run only when the listing holds
    it, in the state the direct read reports, and it decides there.
    """
    listed = next((r for r in runs if r["databaseId"] == named["databaseId"]), None)
    if listed is None:
        return f"run {named['databaseId']} is missing from the listing"
    # gh run list reports a pending run's conclusion as "", REST as null.
    seen = (listed["status"], listed["conclusion"] or "")
    if seen != (named["status"], named["conclusion"]):
        return (
            f"the listing shows run {named['databaseId']} as {'/'.join(seen)}, "
            f"not {named['status']}/{named['conclusion']}"
        )
    if chosen is None or chosen[0]["databaseId"] != named["databaseId"]:
        decider = chosen[0]["databaseId"] if chosen else "none"
        return f"run {decider} decides instead of run {named['databaseId']}"
    return None


def choose(repo, limit, target, paths_between, run_id=None, pause=LIST_RETRY_SECONDS):
    """Return (chosen, runs) for select's command line.

    The deciding run always comes from the listing alone, exactly as
    release.yml will choose it. With run_id, that run is also read directly
    (its state is current there) and the listing is re-read, a bounded
    number of times, while it disagrees (see listing_doubt): a partial or
    lagging page then gets time to catch up instead of refusing at once.
    When the last listing still disagrees, no run decides (None), so the
    caller refuses rather than tag on a stale success or on a run
    release.yml could not see. Each retry and the give-up log what, the
    limit and the elapsed time.
    """
    # Each comparison fetches from the remote; a re-read listing reuses them.
    compared = {}

    def paths_once(base, target):
        if base not in compared:
            compared[base] = paths_between(base, target)
        return compared[base]

    named = None
    if run_id is not None:
        named = view_run(repo, run_id)
        if not is_full_run(named):
            log(
                f"run {run_id} is not a {FULL_RUN_TITLE!r} workflow_dispatch of "
                f"{CI_WORKFLOW_PATH}, so it cannot be release evidence"
            )
            named = None
        elif (paths := paths_once(named["headSha"], target)) is not None and not all(
            docs_safe(path) for path in paths
        ):
            # No listing can make it decide, so re-reading would only wait.
            log(f"run {run_id}'s tree differs from {target} beyond docs-safe paths")
            named = None
    start = time.monotonic()
    for attempt in range(1, LIST_ATTEMPTS + 1):
        runs = list_runs(repo, limit)
        chosen = select(runs, target, paths_once)
        doubt = named and listing_doubt(runs, named, chosen)
        if not doubt:
            break
        elapsed = time.monotonic() - start
        if attempt == LIST_ATTEMPTS:
            log(
                f"gave up re-reading the run listing: {doubt} "
                f"(limit {LIST_ATTEMPTS} listings, elapsed {elapsed:.0f} s); "
                "no run decides, so the caller refuses"
            )
            # Neither source can be trusted alone: the listing may hold a
            # stale success (a re-run in progress), and release.yml cannot
            # see what only the direct read shows.
            return None, runs
        log(
            f"re-reading the run listing: {doubt} "
            f"(listing {attempt} of {LIST_ATTEMPTS}, elapsed {elapsed:.0f} s)"
        )
        time.sleep(pause * attempt)
    return chosen, runs


def list_runs(repo, limit):
    """The newest workflow_dispatch runs of ci.yml, newest first.

    Logs how many runs gh returned against how many were requested, so a
    partial page is visible in the log (#730).
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
    runs = gh_json("gh run list", command)
    log(f"gh run list returned {len(runs)} of {limit} requested runs")
    return runs


def main(argv=None):
    """Print the deciding run for the select command."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pick = sub.add_parser("select")
    pick.add_argument("--repo", required=True)
    pick.add_argument("--commit", required=True)
    pick.add_argument("--checkout", default=".")
    pick.add_argument("--remote", default="origin")
    pick.add_argument("--limit", type=int, default=100)
    pick.add_argument("--run", type=int)
    pick.add_argument("--retry-seconds", type=int, default=LIST_RETRY_SECONDS)
    sub.add_parser("docs-tests")
    args = parser.parse_args(argv)
    if args.command == "docs-tests":
        print("\n".join(DOCS_TESTS))
        return 0
    try:
        chosen, runs = choose(
            args.repo,
            args.limit,
            args.commit,
            lambda base, target: differing_paths(
                args.checkout, args.remote, base, target
            ),
            args.run,
            args.retry_seconds,
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
