"""Decide which heavy CI job groups a change's paths can affect.

The full suite costs about 280 runner minutes, and the free plan runs only 20
jobs at once (issues #158 and #626). A change that touches only documentation,
for example, cannot change what the PostgreSQL shards or browser engines
observe, so CI may skip them. Every rule here errs toward running. Each changed
path maps to the groups it needs, and a group is skipped only when no changed
path needs it. Unknown paths, CI and test infrastructure, schema, migrations,
settings, deployment files, a failed or unexpected diff, and any event other
than a pull request or an explicitly "affected" dispatch run every group. A
false positive only costs runner time; a false negative would let an untested
change merge.

Documentation is not free of tests: several non-database tests read docs,
workflows and deployment files (traceability, build and release contracts).
The ``validate`` job therefore runs the complete non-database suite whenever
the PostgreSQL group is skipped, because shard one normally runs that suite.
Compose-core runs the complete suite inside the image too, over the checkout
paths the development ``tests`` service bind-mounts (all of ``tests/``,
selected scripts, tools and documentation, and the workflows), and checks
host/container collection parity. Any change under those mounts, documentation
included, therefore runs compose-core. The mounts are read from the Compose
file itself, so the list cannot drift.

Browser tests render templates and import application forms, views and helpers
directly. An application Python change therefore skips the browser engines
only when the browser tests cannot reach it: the module is outside the static
import closure of the browser tests, their conftests and Django's implicit
entry points (settings, template tag libraries, and each app's ``apps``,
``models`` and ``admin`` modules). The closure is computed from the checkout,
so it cannot drift from the tests.
"""

import argparse
import ast
import re
import subprocess
import sys
import time
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

import yaml

GROUPS = ("postgresql", "browser", "compose", "operational")
EVERY = frozenset(GROUPS)

# fnmatchcase's "*" also matches "/", so "dir/*" covers a whole subtree.
# Paths that decide what runs, how it installs, or what every group exercises:
# the workflows, the pip retry wrapper, deployment and image files, Django
# settings, the schema and migrations, and the CI quality tooling itself.
ALWAYS_RUN = (
    ".github/*",
    "deploy/*",
    "tools/ci-pip-install.sh",
    "src/*/settings/*",
    "src/*/schema/*",
    "src/*/migrations/*",
    "src/parishkit/stewardship/quality*.py",
    "tests/conftest.py",
    "tests/stewardship/conftest.py",
)

# Paths a database test reads directly from the checkout. They also keep
# whatever their own category below needs.
DATABASE_INPUTS = (
    # test_ops_sql_postgresql.py runs these operator SQL files.
    "tools/stewardship-ops/*",
    # test_mail_send_report_postgresql.py runs the guide's SQL.
    "docs/guides/stewardship-mail-send-report.md",
)

# Ordered (patterns, groups) rules; the first matching rule wins. A path that
# matches none of them runs every group. Any path under a compose mount also
# runs compose-core (see classify).
RULES = (
    # Documentation needs only validate, which then runs the complete
    # non-database suite (traceability and documentation contracts), plus
    # compose-core when the tests service mounts it. README.md is not here:
    # the application image and package metadata include it.
    (
        (
            "docs/*",
            "AGENTS.md",
            "CLAUDE.md",
            "LICENSE",
            ".pymarkdown.json",
            "scripts/*/README.md",
        ),
        frozenset(),
    ),
    # Templates: the browser engines exercise them, compose-core builds them
    # into the image, and many database tests render pages and mail from
    # them. Only the operational bootstrap scenarios do not depend on markup.
    (("src/*/templates/*",), frozenset({"postgresql", "browser", "compose"})),
    # Static assets are served to browsers, never rendered by a database test.
    (("src/*/static/*",), frozenset({"browser", "compose"})),
    # Browser-only and database-only tests also run in the image (collection
    # parity), so they keep the container groups.
    (
        ("tests/stewardship/browser/*",),
        frozenset({"browser", "compose", "operational"}),
    ),
    (
        ("tests/stewardship/database/*",),
        frozenset({"postgresql", "compose", "operational"}),
    ),
    # Non-stewardship tools, wrappers and their tests: the image runs them,
    # validate's complete non-database suite covers them on the host.
    (
        ("tools/*", "scripts/*", "tests/test_*.py"),
        frozenset({"compose", "operational"}),
    ),
)

# Application Python, and stewardship test modules and helpers, outside the
# browser tests' reach skip only the engines. (The browser and database test
# trees matched their own rules above.)
APPLICATION_PYTHON = ("src/*.py", "tests/stewardship/*.py")

# Where the browser tests' import closure starts: the tests and every conftest
# pytest loads for them, plus Django's implicit entry points (settings,
# template tag libraries, and what django.setup() and the admin autodiscover
# import from each app). These are pathlib glob patterns; "**" spans
# directories.
BROWSER_ROOTS = (
    "tests/conftest.py",
    "tests/stewardship/conftest.py",
    "tests/stewardship/browser/**/*.py",
    "src/**/settings/*.py",
    "src/**/templatetags/*.py",
    "src/**/apps.py",
    "src/**/models.py",
    "src/**/models/*.py",
    "src/**/admin.py",
)

# A dotted application path in a string, such as a settings entry, include()
# or import_string() target, is followed like an import.
DOTTED = re.compile(r"(?:parishkit|tests)(?:\.\w+)+")

COMPOSE_DEVELOPMENT = Path("deploy/stewardship/compose.development.yaml")

# A git query that runs longer than this is abandoned (and logged), which
# runs every group.
GIT_TIMEOUT_SECONDS = 60


def _matches(path, patterns):
    """Match a repository-relative path; ``dir/*`` covers the whole subtree."""
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def _module_files(root, name):
    """Return the files of a dotted module name and every parent package."""
    parts = name.split(".")
    files = []
    for end in range(1, len(parts) + 1):
        for base in (root / "src", root):
            stem = base.joinpath(*parts[:end])
            for candidate in (stem / "__init__.py", stem.with_suffix(".py")):
                if candidate.is_file():
                    files.append(candidate)
    return files


def _imported_names(path, root):
    """List the dotted names one file imports or names as a module string."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = path.relative_to(root).with_suffix("").parts
    if package[0] == "src":
        package = package[1:]
    # A module's relative imports resolve from its package; a package's
    # __init__ is its own package.
    package = package[:-1]
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package[: len(package) - node.level + 1]
                base = ".".join([*anchor, *([base] if base else [])])
            names.append(base)
            names.extend(f"{base}.{alias.name}" for alias in node.names)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and DOTTED.fullmatch(node.value)
        ):
            names.append(node.value)
    return names


def browser_modules(root):
    """Return repository-relative Python files the browser tests can reach.

    Returns None when any file is unreadable or unparsable, or when no root
    exists at all (an empty closure would otherwise look like "reaches
    nothing"); None makes every application Python change run the browser
    engines.
    """
    pending = [
        path
        for pattern in BROWSER_ROOTS
        for path in root.glob(pattern)
        if "node_modules" not in path.parts
    ]
    seen = set()
    try:
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            for name in _imported_names(path, root):
                if name.startswith(("parishkit", "tests")):
                    pending.extend(_module_files(root, name))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None
    return frozenset(path.relative_to(root).as_posix() for path in seen) or None


def compose_mounts(root):
    """Return repository-relative paths bind-mounted into the tests service.

    Sources are relative to the Compose file's directory. Any parse problem,
    or a mount of the whole repository (which normalises to ""), returns
    None, which makes the compose group run for every change.
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
    if "" in mounts:
        return None
    return tuple(mounts) or None


def _mounted(path, mounts):
    """Whether a path is a mounted file or lies under a mounted directory."""
    return any(path == mount or path.startswith(f"{mount}/") for mount in mounts)


def needs(path, reachable):
    """Return the groups one changed path needs."""
    if _matches(path, ALWAYS_RUN):
        return EVERY
    extra = {"postgresql"} if _matches(path, DATABASE_INPUTS) else set()
    for patterns, groups in RULES:
        if _matches(path, patterns):
            return groups | extra
    if _matches(path, APPLICATION_PYTHON):
        if reachable is None or path in reachable:
            return EVERY
        return EVERY - {"browser"}
    return EVERY


def classify(paths, reachable=None, mounts=None):
    """Return ``{group: must_run}`` for one change's paths.

    ``reachable`` is the browser tests' module closure (``browser_modules``);
    without it every application Python change runs the browser engines.
    ``mounts`` are the tests service's bind mounts (``compose_mounts``); any
    changed path under one runs compose-core, and without them compose-core
    always runs. An empty change set runs everything: a diff that lists
    nothing is more likely a checkout problem than a no-op change.
    """
    paths = [path for path in paths if path]
    if not paths:
        return dict.fromkeys(GROUPS, True)
    required = set().union(*(needs(path, reachable) for path in paths))
    if mounts is None or any(_mounted(path, mounts) for path in paths):
        required.add("compose")
    return {group: group in required for group in GROUPS}


def run_git(root, *args, timeout=GIT_TIMEOUT_SECONDS):
    """Run one git query, returning its stdout lines or None on any failure.

    Every failure is logged on stderr, which the CI job log keeps: a query
    past its time limit is killed and logged with the limit and the elapsed
    time, and a missing git or a non-zero exit is logged with its reason. The
    None returned runs every group.
    """
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"quality_paths: killed 'git {' '.join(args)}' after "
            f"{time.monotonic() - start:.1f}s (limit {timeout}s); "
            "running every job group",
            file=sys.stderr,
        )
        return None
    except OSError as error:
        print(
            f"quality_paths: cannot run 'git {' '.join(args)}' ({error}); "
            "running every job group",
            file=sys.stderr,
        )
        return None
    if result.returncode:
        print(
            f"quality_paths: 'git {' '.join(args)}' exited {result.returncode}: "
            f"{result.stderr.strip()}; running every job group",
            file=sys.stderr,
        )
        return None
    return result.stdout.splitlines()


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


def branch_paths(root, git=run_git):
    """List paths the checked-out branch changes relative to ``origin/main``.

    A dispatched run checks out a branch head, not a test merge, so the diff
    starts at the merge base with main (which the workflow fetches first). A
    commit that is already on main when the run starts has itself as the
    merge base and an empty diff, which runs everything. A commit that only
    reaches main later (a fast-forward) can still have had a thinned run, so
    the release checks also refuse "affected" runs. A git failure returns None.
    """
    base = git(root, "merge-base", "origin/main", "HEAD")
    if base is None or len(base) != 1:
        return None
    return git(root, "diff", "--name-only", "--no-renames", base[0], "HEAD")


def decide(event, root, jobs="all", git=run_git):
    """Classify a pull request's or an "affected" dispatch's paths.

    Every other event (including a default "all" dispatch and main pushes)
    runs every group.
    """
    if event == "pull_request":
        paths = changed_paths(root, git)
    elif event == "workflow_dispatch" and jobs == "affected":
        paths = branch_paths(root, git)
    else:
        paths = None
    if paths is None:
        return dict.fromkeys(GROUPS, True)
    mounts = compose_mounts(root)
    # Parsing the application takes a few seconds; only Python needs it.
    if not any(_matches(path, APPLICATION_PYTHON) for path in paths):
        return classify(paths, mounts=mounts)
    reachable = browser_modules(root)
    if reachable is not None:
        # A deleted module's former importers cannot be traced in this tree,
        # so a deleted application module runs the browser engines too.
        reachable |= {path for path in paths if not (root / path).exists()}
    return classify(paths, reachable, mounts)


def main(argv=None):
    """Write ``group=true|false`` lines for the GitHub Actions step output."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", required=True)
    # A pull request has no dispatch inputs, so the workflow passes "".
    parser.add_argument("--jobs", choices=("", "all", "affected"), default="all")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    decision = decide(args.event, Path.cwd(), args.jobs)
    lines = [f"{group}={str(decision[group]).lower()}" for group in GROUPS]
    for line in lines:
        print(line)
    if args.output is not None:
        with args.output.open("a", encoding="utf-8") as stream:
            stream.write("".join(f"{line}\n" for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
