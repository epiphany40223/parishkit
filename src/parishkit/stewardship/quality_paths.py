"""Decide which heavy CI job groups a pull request's changed paths can affect.

The full suite costs about 225 runner minutes, and the free plan runs only 20
jobs at once (issue #158). A pull request that changes only documentation, for
example, cannot change what the PostgreSQL shards or browser engines observe,
so CI may skip them. Every rule here errs toward running: a group is skipped
only when *every* changed path matches that group's explicit skip list and
none is an input the group is known to read. Unknown paths, the CI workflow
itself, a failed or unexpected diff, and any event other than a pull request
run every group. A false positive only costs runner time; a false negative
would let an untested change merge.

Documentation is not free of tests: several non-database tests read docs,
workflows and deployment files (traceability, build and release contracts).
The ``validate`` job therefore runs the complete non-database suite whenever
the PostgreSQL group is skipped, because shard one normally runs that suite.

The compose group also runs that complete suite *inside the image*, over the
checkout paths the development ``tests`` service bind-mounts (all of
``tests/``, selected scripts, tools and documentation, and the workflows).
Those mounts are read from the Compose file itself, so the list cannot drift.
"""

import argparse
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

import yaml

GROUPS = ("postgresql", "browser", "compose")

# fnmatchcase's "*" also matches "/", so "dir/*" covers a whole subtree.
# Paths no heavy job reads, apart from the compose mounts checked separately.
# README.md is not here: the application image and package metadata include
# it. The non-database suite that validate then runs covers the tests that
# read these files.
DOCUMENTATION = (
    "docs/*",
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    ".pymarkdown.json",
    ".github/ISSUE_TEMPLATE/*",
    ".github/workflows/*",
)

# The CI workflow decides what runs, and every job installs through the pip
# retry wrapper, so changing either must run everything.
ALWAYS_RUN = (".github/workflows/ci.yml", "tools/ci-pip-install.sh")

# Browser-only and database-only tests, and non-stewardship tools, scripts and
# their tests; all non-database tests among them run in validate's complete
# non-database suite when the shards are skipped.
BROWSER_TESTS = ("tests/stewardship/browser/*",)
DATABASE_TESTS = ("tests/stewardship/database/*",)
OUTSIDE_APPLICATION = ("tools/*", "scripts/*", "tests/test_*.py")

SKIPPABLE = {
    # Shards run the database tests (plus the non-database suite on shard one,
    # which validate replaces when this group is skipped). Deployment files
    # are read only by non-database contract tests.
    "postgresql": (
        *DOCUMENTATION,
        *BROWSER_TESTS,
        *OUTSIDE_APPLICATION,
        "deploy/*",
    ),
    # Browser engines exercise pages from the checkout, never the image.
    "browser": (
        *DOCUMENTATION,
        *DATABASE_TESTS,
        *OUTSIDE_APPLICATION,
        "deploy/*",
    ),
    # Container scenarios build the image and run every test inside it, so
    # only documentation the tests service does not mount may skip them.
    "compose": DOCUMENTATION,
}

COMPOSE_DEVELOPMENT = Path("deploy/stewardship/compose.development.yaml")


def _matches(path, patterns):
    """Match a repository-relative path; ``dir/*`` covers the whole subtree."""
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def compose_mounts(root):
    """Return repository-relative paths bind-mounted into the tests service.

    Sources are relative to the Compose file's directory. Any parse problem
    returns None, which makes the compose group run.
    """
    try:
        document = yaml.safe_load((root / COMPOSE_DEVELOPMENT).read_text())
        volumes = document["services"]["tests"]["volumes"]
        base = COMPOSE_DEVELOPMENT.parent
        mounts = []
        for volume in volumes:
            source = PurePosixPath(base.as_posix(), volume["source"])
            parts = []
            for part in source.parts:
                if part == "..":
                    parts.pop()
                elif part != ".":
                    parts.append(part)
            mounts.append("/".join(parts))
    except (OSError, yaml.YAMLError, KeyError, TypeError, IndexError):
        return None
    return tuple(mounts) or None


def _mounted(path, mounts):
    """Whether a path is a mounted file or lies under a mounted directory."""
    return any(path == mount or path.startswith(f"{mount}/") for mount in mounts)


def classify(paths, mounts=None):
    """Return ``{group: must_run}`` for one pull request's changed paths.

    An empty or unknown change set runs everything: a diff that lists nothing
    is more likely a checkout problem than a no-op pull request. Without the
    tests service's mounts, the compose group always runs.
    """
    paths = [path for path in paths if path]
    if not paths or any(_matches(path, ALWAYS_RUN) for path in paths):
        return dict.fromkeys(GROUPS, True)
    result = {
        group: not all(_matches(path, SKIPPABLE[group]) for path in paths)
        for group in GROUPS
    }
    if mounts is None or any(_mounted(path, mounts) for path in paths):
        result["compose"] = True
    return result


def run_git(root, *args):
    """Run one git query, returning its stdout lines or None on any failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return None if result.returncode else result.stdout.splitlines()


def changed_paths(root, git=run_git):
    """List paths the checked-out pull request merge commit changes.

    For ``pull_request`` events, actions/checkout checks out GitHub's test
    merge commit, whose first parent is the base branch head, so that diff is
    exactly what merging would change. Anything else (a single-parent commit,
    a missing parent in a shallow clone, a git failure) returns None, so a
    diff of only the last commit can never be mistaken for the whole PR.
    """
    parents = git(root, "rev-list", "--parents", "-n", "1", "HEAD")
    if parents is None or len(parents) != 1 or len(parents[0].split()) != 3:
        return None
    return git(root, "diff", "--name-only", "--no-renames", "HEAD^1", "HEAD")


def decide(event, root, git=run_git):
    """Classify a pull request's paths; every other event runs every group."""
    if event != "pull_request":
        return dict.fromkeys(GROUPS, True)
    paths = changed_paths(root, git)
    if paths is None:
        return dict.fromkeys(GROUPS, True)
    return classify(paths, compose_mounts(root))


def main(argv=None):
    """Write ``group=true|false`` lines for the GitHub Actions step output."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    decision = decide(args.event, Path.cwd())
    lines = [f"{group}={str(decision[group]).lower()}" for group in GROUPS]
    for line in lines:
        print(line)
    if args.output is not None:
        with args.output.open("a", encoding="utf-8") as stream:
            stream.write("".join(f"{line}\n" for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
