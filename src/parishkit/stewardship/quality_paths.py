"""Decide which heavy CI job groups a pull request's changed paths can affect.

The full suite costs about 225 runner minutes, and the free plan runs only 20
jobs at once (issue #158). A pull request that changes only documentation, for
example, cannot change what the PostgreSQL shards, browser engines or container
scenarios observe, so CI may skip them. Every rule here errs toward running:
a group is skipped only when *every* changed path matches that group's explicit
skip list. Unknown paths, the CI workflow itself, a failed diff or any event
other than a pull request run every group. A false positive only costs runner
time; a false negative would let an untested change merge.

Documentation is not free of tests: several non-database tests read docs,
workflows and deployment files (traceability, build and release contracts).
The ``validate`` job therefore runs the complete non-database suite whenever
the PostgreSQL group is skipped, because shard one normally runs that suite.
"""

import argparse
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

GROUPS = ("postgresql", "browser", "compose")

# Paths no heavy job reads. The non-database suite that validate then runs
# covers the tests that do read docs and non-CI workflows. README.md is not
# here: the application image and package metadata include it.
DOCUMENTATION = (
    "docs/*",
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    ".pymarkdown.json",
    ".github/ISSUE_TEMPLATE/*",
    ".github/workflows/*",
)

# The CI workflow decides what runs, so changing it must run everything.
ALWAYS_RUN = (".github/workflows/ci.yml",)

# Browser-only tests, and non-stewardship tools and scripts, whose tests are
# all non-database tests covered by validate's complete non-database run.
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
    # Container scenarios build the image from src, requirements, deploy and
    # README.md, and run in-image tests, so only these are safe.
    "compose": (
        *DOCUMENTATION,
        *BROWSER_TESTS,
        *DATABASE_TESTS,
        *OUTSIDE_APPLICATION,
    ),
}


def _matches(path, patterns):
    """Match a repository-relative path; ``dir/*`` covers the whole subtree."""
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def classify(paths):
    """Return ``{group: must_run}`` for one pull request's changed paths.

    An empty or unknown change set runs everything: a diff that lists nothing
    is more likely a checkout problem than a no-op pull request.
    """
    paths = [path for path in paths if path]
    if not paths or any(_matches(path, ALWAYS_RUN) for path in paths):
        return dict.fromkeys(GROUPS, True)
    return {
        group: not all(_matches(path, SKIPPABLE[group]) for path in paths)
        for group in GROUPS
    }


def changed_paths(root):
    """List paths the checked-out pull request merge commit changes.

    For ``pull_request`` events, actions/checkout checks out GitHub's test
    merge commit, whose first parent is the base branch head. That diff is
    exactly what merging would change. Any git failure returns None.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "HEAD^1", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    return result.stdout.splitlines()


def decide(event, root):
    """Classify a pull request's paths; every other event runs every group."""
    if event != "pull_request":
        return dict.fromkeys(GROUPS, True)
    paths = changed_paths(root)
    return dict.fromkeys(GROUPS, True) if paths is None else classify(paths)


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
