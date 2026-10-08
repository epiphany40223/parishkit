"""Select the database tests an "affected" CI dispatch must run (issue #858).

``quality_paths`` decides only whether the PostgreSQL group runs. When it
does, on an explicitly "affected" dispatch, this module narrows the group to
the tests that can observe the change:

* Every full coverage run records which ``src/`` files each database test
  executes (``--cov-context=test``) and publishes that map as the
  ``stewardship-test-map`` artifact (``build_map``, called by
  ``quality_ci.combine``).
* An "affected" dispatch fetches the newest map from a successful full run of
  main or a release train (``find_map``), diffs the map's commit against the
  branch head, and selects the tests whose recorded files changed, every test
  file the diff touches, and every test the map does not know (``select``).
* A changed database test module also runs every database test module that
  imports it, directly or through other test modules (``importing_tests``).
* Tests marked ``sql_rules`` assert only that the database refuses something
  (a trigger, guard or grant). The marker mainly documents them and rarely
  skips anything: they run whenever the map or a changed file selects them,
  and only a marked test the map does not know, outside every changed file,
  is kept out unless the diff touches the grants registry or provisioning.

Every doubt runs the whole group: no usable map, an empty diff, any path that
always runs everything (schema, migrations, settings, CI tooling), the shared
database builders and conftests, templates and every other input coverage
cannot see, any Python file the map has no record of, and any mapped
Python change coverage cannot attribute to every test that depends on it
(``global_change``): code outside function bodies, which runs once at import,
a changed cached function, a changed ``ready()``, or a changed function
the file's import-time code calls. Full runs and release
evidence never use a selection; ``quality_ci.combine`` refuses one.
"""

import argparse
import ast
import json
import os
import subprocess
import sys
import time
from fnmatch import fnmatchcase
from pathlib import Path

from .quality_paths import ALWAYS_RUN, _imported_names, _module_files, needs

MAP_ARTIFACT = "stewardship-test-map"
MAP_SCHEMA = 1
DATABASE_TESTS = "tests/stewardship/database/"
# The workflow's full-run name; release.sh and release.yml pin the same text.
FULL_RUN = "CI (jobs: all)"
# Only full runs of trees that reached (or are about to reach) main carry a
# map worth reusing; a topic branch's full run may hold unmerged changes.
MAP_BRANCHES = ("main", "train/*")
# How many candidate runs' artifacts to try before giving up. Older runs may
# predate the map or have had their artifact expire.
MAP_ATTEMPTS = 3
# Each git or gh call has its own limit, and the whole selection shares one
# budget well inside the workflow step's 10-minute timeout, so the selector
# always logs its own kill (what, limit, elapsed) before GitHub kills it.
TOOL_TIMEOUT_SECONDS = 60
BUDGET_SECONDS = 6 * 60
# Functions whose body runs once per process and is then served from a
# cache, so coverage credits only the first test that called them.
CACHE_DECORATORS = frozenset({"cache", "lru_cache", "cached_property"})

# Shared builders, fixtures and conftests feed many tests that coverage of
# src/ cannot attribute, so any change to them runs the whole group.
SHARED_TEST_CODE = (
    "tests/stewardship/database/*builders*.py",
    "tests/stewardship/database/*conftest.py",
)
DATABASE_TEST_FILES = ("tests/stewardship/database/*test_*.py",)
# Inputs that decide database grants and roles: a change here also runs the
# sql_rules tests. (Schema and migrations already run everything.)
SQL_RULE_INPUTS = ("src/*grants*.py", "src/*provisioning*.py")


def _matches(path, patterns):
    """Match a repository-relative path; ``*`` also spans directories."""
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def run_tool(root, *command, timeout=TOOL_TIMEOUT_SECONDS):
    """Run one git or gh query, returning stdout lines or None on any failure.

    Failures are logged on stderr with their reason (a time-limit kill names
    the limit and the elapsed time); the caller then runs the whole group.
    """
    start = time.monotonic()
    label = " ".join(command)
    try:
        result = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"quality_select: killed '{label}' after "
            f"{time.monotonic() - start:.1f}s (limit {timeout}s)",
            file=sys.stderr,
        )
        return None
    except OSError as error:
        print(f"quality_select: cannot run '{label}' ({error})", file=sys.stderr)
        return None
    if result.returncode:
        print(
            f"quality_select: '{label}' exited {result.returncode}: "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    return result.stdout.splitlines()


def build_map(data, root, universe, count):
    """Map each measured ``src/`` file to the database tests that execute it.

    ``data`` is combined CoverageData recorded with ``--cov-context=test``,
    whose contexts are ``<nodeid>|setup``, ``|run`` or ``|teardown``. Contexts
    of the non-database baseline, and the empty context of import-time code,
    are not database tests and are dropped. Tests are stored once, by index,
    to keep the artifact small.
    """
    index = {node: number for number, node in enumerate(universe)}
    files = {}
    for filename in sorted(data.measured_files()):
        tests = set()
        for contexts in data.contexts_by_lineno(filename).values():
            for context in contexts:
                number = index.get(context.rsplit("|", 1)[0])
                if number is not None:
                    tests.add(number)
        if tests:
            path = Path(filename).resolve().relative_to(root).as_posix()
            files[path] = sorted(tests)
    return {"schema": MAP_SCHEMA, "count": count, "tests": universe, "files": files}


def load_map(path, count):
    """Return a usable map from ``path``, or None (whole group) with a reason."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        tests, files = document["tests"], document["files"]
        valid = (
            document["schema"] == MAP_SCHEMA
            and document["count"] == count
            and isinstance(tests, list)
            and all(isinstance(node, str) for node in tests)
            and isinstance(files, dict)
            and all(
                isinstance(numbers, list)
                and all(type(n) is int and 0 <= n < len(tests) for n in numbers)
                for numbers in files.values()
            )
        )
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        print(f"quality_select: unusable test map {path}", file=sys.stderr)
        return None
    return document


def _no_importers(path):
    """Default for ``select``: a test module nothing else imports."""
    return set()


def _no_global_change(path):
    """Default for ``select``: every mapped change is attributable."""
    return None


def select(paths, test_map, importers=_no_importers, global_change=_no_global_change):
    """Return ``(selection, reason)``; a None selection runs the whole group.

    The selection names the mapped tests to run, every database test the map
    knew (so new tests always run), the changed database test files plus
    every test module importing them (which run in full, ``sql_rules`` tests
    included) and whether unknown ``sql_rules`` tests run.

    ``importers(path)`` returns the database test modules importing a test
    module, or None when they cannot be traced. ``global_change(path)``
    returns why a mapped file's change cannot be attributed to individual
    tests, or None. Either doubt runs the whole group.
    """
    if not paths:
        return None, "the diff lists no paths"
    tests, files = test_map["tests"], test_map["files"]
    picked, changed, sql_rules = set(), set(), False
    for path in paths:
        if _matches(path, ALWAYS_RUN):
            return None, f"{path} always runs every test"
        if "postgresql" not in needs(path, frozenset()):
            continue
        if _matches(path, SHARED_TEST_CODE):
            return None, f"{path} is shared database test code"
        if _matches(path, DATABASE_TEST_FILES):
            dependents = importers(path)
            if dependents is None:
                return None, f"cannot trace which test modules import {path}"
            changed |= {path, *dependents}
        elif path.endswith(".py") and path in files:
            if reason := global_change(path):
                return None, f"{path} {reason}"
            picked.update(files[path])
            sql_rules = sql_rules or _matches(path, SQL_RULE_INPUTS)
        else:
            return None, f"{path} has no coverage record"
    selection = {
        "schema": MAP_SCHEMA,
        "tests": sorted(tests[number] for number in picked),
        "known": tests,
        "files": sorted(changed),
        "sql_rules": sql_rules,
    }
    return selection, (
        f"{len(selection['tests']):,} mapped tests, "
        f"{len(changed):,} changed or importing test files, "
        f"unmapped sql_rules tests {'included' if sql_rules else 'skipped'}"
    )


def load_selection(path):
    """Read a selection file into sets for fast per-test lookups."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document["schema"] != MAP_SCHEMA:
        raise ValueError("Unknown database test selection")
    return {
        "tests": frozenset(document["tests"]),
        "known": frozenset(document["known"]),
        "files": frozenset(document["files"]),
        "sql_rules": document["sql_rules"] is True,
    }


def chosen(nodeid, sql_rule, selection):
    """Whether one collected database test runs under a selection.

    A test in a changed (or importing) file, or one the map attributes a
    changed file to, always runs, ``sql_rules`` or not. A test the map knew
    but did not select is skipped. A test the map does not know runs, unless
    it is a ``sql_rules`` test and the grants inputs are unchanged.
    """
    if nodeid.split("::", 1)[0] in selection["files"] or nodeid in selection["tests"]:
        return True
    if nodeid in selection["known"]:
        return False
    return not sql_rule or selection["sql_rules"]


def import_graph(root):
    """Map each stewardship test-tree module to the modules importing it.

    The whole ``tests/stewardship`` tree is scanned, not only the database
    tests, so a chain through a shared helper such as
    ``tests/stewardship/campaign_factory.py`` is followed too. Only direct
    imports are recorded; ``importing_tests`` follows them. Any unreadable or
    unparsable module returns None, which runs the whole group for a changed
    test module.
    """
    imported_by = {}
    try:
        for path in _python_files(root / "tests/stewardship"):
            source = path.relative_to(root).as_posix()
            for name in _imported_names(path, root):
                if not name.startswith("tests."):
                    continue
                for target in _module_files(root, name):
                    target = target.relative_to(root).as_posix()
                    if target != source:
                        imported_by.setdefault(target, set()).add(source)
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None
    return imported_by


def _python_files(directory):
    """List a tree's Python files, never descending into node_modules."""
    found = []
    for parent, directories, files in os.walk(directory):
        directories[:] = sorted(
            name for name in directories if name not in ("node_modules", "__pycache__")
        )
        found.extend(
            Path(parent, name) for name in sorted(files) if name.endswith(".py")
        )
    return found


def importing_tests(path, imported_by):
    """Return the database test files importing ``path``, even transitively.

    The walk passes through helper modules, so a test that imports a helper
    that imports the changed module is included; only test files are
    returned, because only they hold tests to select.
    """
    if imported_by is None:
        return None
    seen, pending = set(), [path]
    while pending:
        for importer in imported_by.get(pending.pop(), ()):
            if importer not in seen:
                seen.add(importer)
                pending.append(importer)
    return {item for item in seen if _matches(item, DATABASE_TEST_FILES)}


def _decorator_name(node):
    """Return the last name of a decorator: ``functools.cache()`` -> cache."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else None


def _skeleton(tree):
    """Dump a module without function bodies or module/class docstrings.

    What remains runs once, at import or class creation: module and class
    statements, decorators, signatures and default values.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.body = [ast.Pass()]
        if (
            isinstance(node, (ast.Module, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body = node.body[1:]
    return ast.dump(tree)


def _functions(node, prefix=""):
    """Map qualified names to the dumps and nodes of every function."""
    found = {}
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = f"{prefix}{child.name}"
            if not isinstance(child, ast.ClassDef):
                found[name] = (ast.dump(child), child)
            found |= _functions(child, f"{name}.")
        else:
            found |= _functions(child, prefix)
    return found


def _names(nodes):
    """Every bare and attribute name the given nodes reference."""
    return {
        node.id if isinstance(node, ast.Name) else node.attr
        for item in nodes
        for node in ast.walk(item)
        if isinstance(node, (ast.Name, ast.Attribute))
    }


def _is_main_guard(node):
    """Whether a statement is ``if __name__ == "__main__":``, never imported."""
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


def _import_time_roots(body):
    """Names module or class code references while the module is imported.

    That is every statement outside function bodies, plus decorators and
    default values (evaluated when a ``def`` runs) and class bases. A main
    guard never runs on import and is left out.
    """
    roots = set()
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args
            roots |= _names(
                [
                    *node.decorator_list,
                    *arguments.defaults,
                    *(item for item in arguments.kw_defaults if item is not None),
                ]
            )
        elif isinstance(node, ast.ClassDef):
            roots |= _names([*node.decorator_list, *node.bases, *node.keywords])
            roots |= _import_time_roots(node.body)
        elif not _is_main_guard(node):
            roots |= _names([node])
    return roots


def import_time_functions(tree):
    """Names of this file's functions that can run while it is imported.

    Starting from what module and class code reference, calls between the
    file's own functions are followed (by name, so a shared name errs toward
    running). Changing one of these is credited by coverage to no test.
    """
    bodies = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bodies.setdefault(node.name, set()).update(_names(node.body))
    reached, pending = set(), list(_import_time_roots(tree.body))
    while pending:
        name = pending.pop()
        if name in bodies and name not in reached:
            reached.add(name)
            pending.extend(bodies[name])
    return reached


def global_change(old, new):
    """Why a Python change cannot be credited to the tests coverage recorded.

    Coverage records code that runs once per process only under the first
    test that triggered it (or under no test at all, at import). So any
    change outside function bodies, to a cached function, to a ``ready()``
    method, or to a function module or class code calls (directly or through
    the file's other functions, decorator factories included) returns a
    reason, which runs the whole group. Only a
    change confined to ordinary function bodies returns None.
    """
    if old is None or new is None:
        return "was added or deleted since the map"
    try:
        before, after = ast.parse(old), ast.parse(new)
    except (SyntaxError, ValueError):
        return "cannot be parsed"
    old_functions, new_functions = _functions(before), _functions(after)
    # Before _skeleton strips the bodies this needs.
    import_time = import_time_functions(before) | import_time_functions(after)
    if _skeleton(before) != _skeleton(after):
        return "changes code outside function bodies"
    for name, (dump, node) in new_functions.items():
        if old_functions.get(name, (None,))[0] == dump:
            continue
        if node.name == "ready":
            return f"changes {name}(), which runs once per process"
        if node.name in import_time:
            return f"changes {name}, which runs when the module is imported"
        if any(
            _decorator_name(item) in CACHE_DECORATORS for item in node.decorator_list
        ):
            return f"changes cached {name}, which runs once per process"
    return None


def bounded(tool, budget=BUDGET_SECONDS):
    """Wrap ``tool`` so every call shares one deadline.

    Each call gets the smaller of its own limit and the time left. Once the
    budget is spent, calls are refused and logged with the limit and the
    elapsed time; the selector then runs the whole group.
    """
    start = time.monotonic()

    def call(root, *command):
        """Run one command inside what is left of the budget."""
        remaining = budget - (time.monotonic() - start)
        if remaining <= 0:
            print(
                f"quality_select: skipped '{' '.join(command)}': selection budget "
                f"{budget}s spent after {time.monotonic() - start:.1f}s",
                file=sys.stderr,
            )
            return None
        return tool(root, *command, timeout=min(TOOL_TIMEOUT_SECONDS, remaining))

    return call


def find_map(root, directory, count, tool=run_tool):
    """Download the newest usable map; return ``(map, commit, run)`` or None.

    Candidates are successful full runs of main or a release train, newest
    first. Each candidate's commit is fetched, because it may not be on the
    checked-out history (a train head), and its artifact downloaded; the
    first candidate that yields a valid map wins.
    """
    listing = tool(
        root,
        "gh",
        "run",
        "list",
        "--workflow",
        "ci.yml",
        "--event",
        "workflow_dispatch",
        "--status",
        "success",
        "--limit",
        "50",
        "--json",
        "databaseId,headSha,headBranch,displayTitle",
    )
    try:
        runs = json.loads("\n".join(listing)) if listing is not None else []
    except ValueError:
        runs = []
    attempts = 0
    for run in runs:
        if run.get("displayTitle") != FULL_RUN or not _matches(
            run.get("headBranch", ""), MAP_BRANCHES
        ):
            continue
        if attempts == MAP_ATTEMPTS:
            break
        attempts += 1
        identifier, commit = str(run["databaseId"]), run["headSha"]
        target = Path(directory) / identifier
        if (
            tool(root, "git", "fetch", "--no-tags", "--depth=1", "origin", commit)
            is None
            or tool(
                root,
                "gh",
                "run",
                "download",
                identifier,
                "-n",
                MAP_ARTIFACT,
                "-D",
                str(target),
            )
            is None
        ):
            continue
        test_map = load_map(target / f"{MAP_ARTIFACT}.json", count)
        if test_map is not None:
            return test_map, commit, identifier
    return None


def decide(root, directory, count, tool=None):
    """Return ``(selection, reason)`` for the checked-out branch head.

    All tool calls share ``BUDGET_SECONDS`` (see ``bounded``).
    """
    tool = tool or bounded(run_tool)
    found = find_map(root, directory, count, tool)
    if found is None:
        return None, "no usable test map from a recent full run"
    test_map, commit, identifier = found
    paths = tool(root, "git", "diff", "--name-only", "--no-renames", commit, "HEAD")
    if paths is None:
        return None, f"cannot diff against the map's commit {commit}"
    graph = []  # Parsed once, and only when a test module changed.

    def importers(path):
        """The database test files importing one changed test module."""
        if not graph:
            graph.append(import_graph(root))
        return importing_tests(path, graph[0])

    def changed_globally(path):
        """Compare the map's version of a mapped file with the checkout's."""
        old = tool(root, "git", "show", f"{commit}:{path}")
        try:
            new = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            new = None
        return global_change(None if old is None else "\n".join(old), new)

    selection, reason = select(
        [path for path in paths if path], test_map, importers, changed_globally
    )
    return selection, f"map from run {identifier} ({commit[:12]}): {reason}"


def main(argv=None):
    """Write a selection file and ``database_selection=subset|all`` output."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--map-directory", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    selection, reason = decide(Path.cwd(), args.map_directory, args.count)
    if selection is not None:
        args.selection.write_text(json.dumps(selection), encoding="utf-8")
    line = f"database_selection={'all' if selection is None else 'subset'}"
    message = (
        f"Database tests: {'whole group' if selection is None else 'selected'}; "
        f"{reason}"
    )
    print(line)
    print(message)
    for path, text in ((args.output, line), (args.summary, message)):
        if path is not None:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"{text}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
