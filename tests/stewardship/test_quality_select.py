"""Coverage-map database test selection runs the whole group on any doubt."""

import ast
import json
from pathlib import Path

import pytest
import yaml
from coverage import CoverageData

from parishkit.stewardship import quality_select as qs

ROOT = Path(__file__).resolve().parents[2]
DB = "tests/stewardship/database"
TESTS = [f"{DB}/test_a.py::test_one", f"{DB}/test_a.py::test_two", f"{DB}/test_b.py::t"]
MAP = {
    "schema": 1,
    "count": 14,
    "tests": TESTS,
    "files": {
        "src/parishkit/stewardship/forms.py": [0],
        "src/parishkit/stewardship/runtime_grants.py": [2],
        "src/parishkit/stewardship/reports/views.py": [1, 2],
    },
}


@pytest.mark.parametrize(
    "path,reason",
    [
        ("src/parishkit/stewardship/schema/0011_x.sql", "always runs"),
        ("src/parishkit/stewardship/campaigns/migrations/0005_x.py", "always runs"),
        ("src/parishkit/stewardship/settings/base.py", "always runs"),
        ("src/parishkit/stewardship/quality_select.py", "always runs"),
        (".github/workflows/ci.yml", "always runs"),
        ("tests/stewardship/conftest.py", "always runs"),
        (f"{DB}/conftest.py", "shared database test code"),
        (f"{DB}/campaign_builders.py", "shared database test code"),
        (f"{DB}/credential_builders.py", "shared database test code"),
        (f"{DB}/role_grants.py", "no coverage record"),
        ("src/parishkit/stewardship/templates/family/page.html", "no coverage"),
        ("src/parishkit/stewardship/unmeasured.py", "no coverage record"),
        ("src/parishkit/common.py", "no coverage record"),
        ("tests/stewardship/campaign_factory.py", "no coverage record"),
        ("tools/stewardship-ops/report.sql", "no coverage record"),
        ("docs/guides/stewardship-mail-send-report.md", "no coverage record"),
        ("requirements.txt", "no coverage record"),
    ],
)
def test_untrusted_paths_run_the_whole_group(path, reason):
    """Each fallback path, even beside a mappable change, runs every test."""
    selection, why = qs.select(["src/parishkit/stewardship/forms.py", path], MAP)
    assert selection is None
    assert path in why and reason in why


def test_empty_diff_runs_the_whole_group():
    """An empty diff more likely means a checkout problem than no change."""
    assert qs.select([], MAP) == (None, "the diff lists no paths")


def test_mapped_python_changes_select_their_tests_only():
    """Paths no database test can observe are ignored; mapped files select."""
    selection, why = qs.select(
        [
            "docs/specs/stewardship/operations/spec.md",
            "src/parishkit/stewardship/static/admin.css",
            "tests/stewardship/browser/test_admin.py",
            "src/parishkit/stewardship/reports/views.py",
        ],
        MAP,
    )
    assert selection == {
        "schema": 1,
        "tests": sorted(TESTS[1:]),
        "known": TESTS,
        "files": [],
        "sql_rules": False,
    }
    assert "2 mapped tests" in why and "sql_rules tests skipped" in why


def test_changed_test_files_and_grants_inputs():
    """Changed test files run in full; grants changes include sql_rules tests."""
    selection, why = qs.select(
        [f"{DB}/test_c.py", "src/parishkit/stewardship/runtime_grants.py"], MAP
    )
    assert selection["files"] == [f"{DB}/test_c.py"]
    assert selection["tests"] == [TESTS[2]]
    assert selection["sql_rules"] is True
    assert "sql_rules tests included" in why


def test_chosen_rules():
    """Changed files and mapped tests always run, sql_rules or not; known
    unmapped tests skip; unknown tests run unless sql_rules (#859 M1)."""
    selection = {
        "tests": frozenset({TESTS[0], TESTS[2]}),
        "known": frozenset(TESTS),
        "files": frozenset({f"{DB}/test_b.py"}),
        "sql_rules": False,
    }
    assert qs.chosen(TESTS[0], False, selection)
    # The map selects it for a changed file, so the marker cannot skip it.
    assert qs.chosen(TESTS[0], True, selection)
    assert not qs.chosen(TESTS[1], False, selection)
    assert not qs.chosen(TESTS[1], True, {**selection, "sql_rules": True})
    # A changed file runs every test in it, sql_rules or not.
    assert qs.chosen(TESTS[2], True, selection)
    # A test the map never saw always runs, unless it is a sql_rules test.
    assert qs.chosen(f"{DB}/test_new.py::t", False, selection)
    assert not qs.chosen(f"{DB}/test_new.py::t", True, selection)
    assert qs.chosen(f"{DB}/test_new.py::t", True, {**selection, "sql_rules": True})


def test_selection_round_trips(tmp_path):
    """The selection file the validate job writes is what the plugin reads."""
    selection, _ = qs.select(["src/parishkit/stewardship/forms.py"], MAP)
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selection))
    loaded = qs.load_selection(path)
    assert loaded["tests"] == {TESTS[0]} and loaded["known"] == set(TESTS)
    path.write_text(json.dumps({**selection, "schema": 2}))
    with pytest.raises(ValueError):
        qs.load_selection(path)


@pytest.mark.parametrize(
    "change",
    [
        {"schema": 2},
        {"count": 8},
        {"tests": "nope"},
        {"files": {"src/a.py": [3]}},
        {"files": {"src/a.py": [-1]}},
        {"files": {"src/a.py": ["0"]}},
        {"files": []},
        None,
    ],
)
def test_unusable_maps_are_rejected(tmp_path, change, capsys):
    """A different schema or partition count, or a malformed map, is unusable."""
    path = tmp_path / "map.json"
    if change is not None:
        path.write_text(json.dumps({**MAP, **change}))
    assert qs.load_map(path, 14) is None
    assert "unusable test map" in capsys.readouterr().err
    path.write_text(json.dumps(MAP))
    assert qs.load_map(path, 14) == MAP


def test_build_map_keeps_only_database_test_contexts(tmp_path):
    """Phases fold into one test; baseline and import-time contexts drop out."""
    root = tmp_path.resolve()
    first, second = root / "src/one.py", root / "src/two.py"
    for path in (first, second):
        path.parent.mkdir(exist_ok=True)
        path.write_text("x = 1\n")
    data = CoverageData(basename=str(tmp_path / "data"))
    for context, path in (
        ("", first),
        (f"{TESTS[1]}|setup", first),
        (f"{TESTS[1]}|run", first),
        (f"{TESTS[0]}|teardown", first),
        ("tests/stewardship/test_unit.py::test_x|run", second),
    ):
        data.set_context(context)
        data.add_arcs({str(path): {(-1, 1), (1, -1)}})
    assert qs.build_map(data, root, TESTS, 14) == {
        "schema": 1,
        "count": 14,
        "tests": TESTS,
        "files": {"src/one.py": [0, 1]},
    }


class Tools:
    """Stand in for gh and git, recording calls and failing on request."""

    def __init__(self, runs, fail=(), maps=None, diff=("src/x.py",), old=None):
        self.runs, self.fail, self.maps, self.diff = runs, fail, maps or {}, diff
        self.old = old or {}
        self.calls = []

    def __call__(self, root, *command, timeout=None):
        """Answer one command like the real tool, or fail it with None."""
        self.calls.append(command)
        key = command[:3]
        if any(command[: len(part)] == part for part in self.fail):
            return None
        if key == ("gh", "run", "list"):
            return json.dumps(self.runs).splitlines()
        if key == ("gh", "run", "download"):
            target = Path(command[command.index("-D") + 1])
            if command[3] not in self.maps:
                return None
            target.mkdir(parents=True)
            (target / "stewardship-test-map.json").write_text(
                json.dumps(self.maps[command[3]])
            )
            return []
        if command[:2] == ("git", "fetch"):
            return []
        if command[:2] == ("git", "diff"):
            return list(self.diff)
        if command[:2] == ("git", "show"):
            text = self.old.get(command[2].split(":", 1)[1])
            return None if text is None else text.splitlines()
        raise AssertionError(command)


def run(identifier, branch="main", title=qs.FULL_RUN):
    """One ``gh run list`` row."""
    return {
        "databaseId": identifier,
        "headSha": f"{identifier:040d}",
        "headBranch": branch,
        "displayTitle": title,
    }


def test_find_map_skips_other_runs_and_limits_attempts(tmp_path):
    """Only full runs of main or a train count; at most MAP_ATTEMPTS are tried."""
    runs = [
        run(1, title="CI (jobs: affected)"),
        run(2, branch="pr/topic"),
        run(3),
        run(4, branch="train/2026-10-08"),
        run(5),
        run(6),
    ]
    tools = Tools(runs, maps={"6": MAP})
    assert qs.find_map(ROOT, tmp_path, 14, tools) is None
    downloads = [
        call[3] for call in tools.calls if call[:3] == ("gh", "run", "download")
    ]
    assert downloads == ["3", "4", "5"]
    tools = Tools(runs, maps={"4": MAP, "5": MAP})
    assert qs.find_map(ROOT, tmp_path / "again", 14, tools) == (MAP, f"{4:040d}", "4")


def test_find_map_skips_invalid_maps_and_unfetchable_commits(tmp_path):
    """A stale-scheme map or a commit that cannot be fetched is passed over."""
    tools = Tools([run(1), run(2)], maps={"1": {**MAP, "count": 8}, "2": MAP})
    assert qs.find_map(ROOT, tmp_path, 14, tools)[2] == "2"
    tools = Tools([run(1)], fail=[("git", "fetch")], maps={"1": MAP})
    assert qs.find_map(ROOT, tmp_path / "again", 14, tools) is None
    for listing in ([("gh", "run", "list")], []):
        tools = Tools([], fail=listing)
        assert qs.find_map(ROOT, tmp_path / "none", 14, tools) is None


def test_decide_diffs_against_the_map_commit(tmp_path):
    """The diff starts at the map's own commit, so main's later changes count."""
    root = tmp_path / "checkout"
    forms = "src/parishkit/stewardship/forms.py"
    (root / forms).parent.mkdir(parents=True)
    (root / forms).write_text("def f():\n    return 2\n")
    old = {forms: "def f():\n    return 1\n"}
    tools = Tools([run(7)], maps={"7": MAP}, diff=(forms, ""), old=old)
    selection, why = qs.decide(root, tmp_path / "maps", 14, tools)
    assert selection["tests"] == [TESTS[0]]
    assert "run 7" in why
    assert ("git", "diff", "--name-only", "--no-renames", f"{7:040d}", "HEAD") in (
        tools.calls
    )
    assert ("git", "show", f"{7:040d}:{forms}") in tools.calls
    # A module-level change, or an unreadable old version, runs everything.
    (root / forms).write_text("LIMIT = 3\ndef f():\n    return 2\n")
    tools = Tools([run(7)], maps={"7": MAP}, diff=(forms,), old=old)
    selection, why = qs.decide(root, tmp_path / "global", 14, tools)
    assert selection is None and "outside function bodies" in why
    tools = Tools([run(7)], maps={"7": MAP}, diff=(forms,))
    assert qs.decide(root, tmp_path / "unread", 14, tools)[0] is None
    tools = Tools([run(7)], fail=[("git", "diff")], maps={"7": MAP})
    assert qs.decide(root, tmp_path / "again", 14, tools)[0] is None
    assert qs.decide(root, tmp_path / "none", 14, Tools([]))[0] is None


@pytest.mark.parametrize("found", [True, False])
def test_main_writes_outputs(tmp_path, monkeypatch, capsys, found):
    """The step output says subset only when a selection file was written."""
    selection = {"schema": 1, "tests": [], "known": [], "files": [], "sql_rules": False}
    monkeypatch.setattr(
        qs,
        "decide",
        lambda root, directory, count: (selection, "why") if found else (None, "no"),
    )
    output, summary = tmp_path / "output", tmp_path / "summary"
    path = tmp_path / "selection.json"
    arguments = ["--count", "14", "--map-directory", str(tmp_path / "maps")]
    arguments += ["--selection", str(path), "--output", str(output)]
    assert qs.main([*arguments, "--summary", str(summary)]) == 0
    expected = "subset" if found else "all"
    assert output.read_text() == f"database_selection={expected}\n"
    assert path.exists() is found
    assert ("whole group" in summary.read_text()) is not found


def test_tool_failures_are_logged_not_raised(tmp_path, capsys):
    """A missing tool, a failure or a time-limit kill returns None and says why."""
    assert qs.run_tool(tmp_path, "definitely-not-a-command-858") is None
    assert qs.run_tool(tmp_path, "sh", "-c", "echo bad >&2; exit 3") is None
    assert qs.run_tool(tmp_path, "sleep", "5", timeout=0.1) is None
    err = capsys.readouterr().err
    assert "cannot run" in err and "exited 3: bad" in err
    assert "killed 'sleep 5'" in err and "limit 0.1s" in err
    assert qs.run_tool(tmp_path, "sh", "-c", "echo ok") == ["ok"]


def workflow():
    """The parsed CI workflow's jobs."""
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]


def test_workflow_selects_only_on_affected_dispatches():
    """Only an "affected" dispatch selects or skips coverage; full runs map."""
    jobs = workflow()
    validate = {step.get("name"): step for step in jobs["validate"]["steps"]}
    step = validate["Select database tests for an affected-jobs dispatch"]
    assert "inputs.jobs == 'affected'" in step["if"]
    assert "steps.paths.outputs.postgresql == 'true'" in step["if"]
    assert step["continue-on-error"] is True
    assert "--count 14 " in step["run"]
    assert jobs["validate"]["permissions"] == {"contents": "read", "actions": "read"}
    shard = {
        step.get("name"): step for step in jobs["stewardship-postgresql-shard"]["steps"]
    }
    measure = shard["Measure isolated database partitions"]["run"]
    assert 'test "$JOBS" = affected; then set -- --no-coverage' in measure
    assert 'test "$SELECTION" = subset' in measure
    assert shard["Retain successful partition evidence"]["if"] == (
        "${{ inputs.jobs != 'affected' }}"
    )
    gate = jobs["stewardship-postgresql"]["steps"]
    for later in gate[1:]:
        assert "inputs.jobs != 'affected'" in later["if"], later
    combine = next(step for step in gate if "quality_ci combine" in step.get("run", ""))
    assert '--map "$RUNNER_TEMP/stewardship-test-map.json"' in combine["run"]
    upload = next(
        step for step in gate if step.get("name") == "Retain the database test map"
    )
    assert upload["with"]["name"] == qs.MAP_ARTIFACT
    assert upload["with"]["path"].endswith(f"{qs.MAP_ARTIFACT}.json")


def test_changed_test_modules_run_their_importers():
    """A changed test module also runs every test file importing it (#859 H1)."""
    helper = f"{DB}/test_background_grants_postgresql.py"
    importers = {helper: {f"{DB}/test_x.py", f"{DB}/test_y.py"}}
    selection, why = qs.select([helper], MAP, importers.get)
    assert selection["files"] == sorted([helper, *importers[helper]])
    assert "3 changed or importing test files" in why
    selection, why = qs.select([helper], MAP, lambda path: None)
    assert selection is None and "cannot trace" in why


def test_import_graph_follows_relative_and_transitive_imports(tmp_path):
    """Imports through helper modules count; only test files are returned."""
    directory = tmp_path / DB
    directory.mkdir(parents=True)
    for package in ("tests", "tests/stewardship", DB):
        (tmp_path / package / "__init__.py").write_text("")
    (directory / "test_base.py").write_text("def task_login():\n    pass\n")
    (directory / "helpers.py").write_text("from .test_base import task_login\n")
    (directory / "test_direct.py").write_text(
        "from tests.stewardship.database.test_base import task_login\n"
    )
    (directory / "test_through.py").write_text("from . import helpers\n")
    # A chain through a helper outside the database tests is followed too.
    (tmp_path / "tests/stewardship/factory.py").write_text(
        "from .database.test_base import task_login\n"
    )
    (directory / "test_factory.py").write_text("from ..factory import task_login\n")
    browser = tmp_path / "tests/stewardship/browser/node_modules/pkg"
    browser.mkdir(parents=True)
    (browser / "broken.py").write_text("def (:\n")
    (directory / "test_other.py").write_text("import os\n")
    graph = qs.import_graph(tmp_path)
    assert qs.importing_tests(f"{DB}/test_base.py", graph) == {
        f"{DB}/test_direct.py",
        f"{DB}/test_factory.py",
        f"{DB}/test_through.py",
    }
    assert qs.importing_tests(f"{DB}/test_other.py", graph) == set()
    (directory / "test_broken.py").write_text("def (:\n")
    assert qs.import_graph(tmp_path) is None
    assert qs.importing_tests(f"{DB}/test_base.py", None) is None


def test_real_suite_import_graph_traces_shared_test_helpers():
    """The checkout's own graph parses, and task_login's importers are found."""
    graph = qs.import_graph(ROOT)
    importers = qs.importing_tests(f"{DB}/test_background_grants_postgresql.py", graph)
    assert f"{DB}/test_family_dispatch_grants_postgresql.py" in importers
    assert f"{DB}/test_boundary_grants_postgresql.py" in importers


BODY_ONLY = "def f(x):\n    return x + 1\n"


@pytest.mark.parametrize(
    "old,new,reason",
    [
        (BODY_ONLY, "def f(x):\n    return x + 2\n", None),
        ('"""Doc."""\n' + BODY_ONLY, '"""New doc."""\n' + BODY_ONLY, None),
        (
            "class A:\n    def m(self):\n        return 1\n",
            'class A:\n    """Doc."""\n    def m(self):\n        return 2\n',
            None,
        ),
        ("X = 1\n" + BODY_ONLY, "X = 2\n" + BODY_ONLY, "outside function bodies"),
        (
            "class A:\n    x = 1\n",
            "class A:\n    x = 2\n",
            "outside function bodies",
        ),
        (BODY_ONLY, "@wrap\n" + BODY_ONLY, "outside function bodies"),
        (BODY_ONLY, "def f(x, y=1):\n    return x + 1\n", "outside function bodies"),
        (BODY_ONLY, BODY_ONLY + "def g():\n    pass\n", "outside function bodies"),
        (
            "@functools.cache\ndef f():\n    return 1\n",
            "@functools.cache\ndef f():\n    return 2\n",
            "cached f",
        ),
        (
            "@lru_cache(maxsize=None)\ndef f():\n    return 1\n",
            "@lru_cache(maxsize=None)\ndef f():\n    return 2\n",
            "cached f",
        ),
        (
            "class A:\n    @cached_property\n    def p(self):\n        return 1\n",
            "class A:\n    @cached_property\n    def p(self):\n        return 2\n",
            "cached A.p",
        ),
        (
            "class C(AppConfig):\n    def ready(self):\n        pass\n",
            "class C(AppConfig):\n    def ready(self):\n        connect()\n",
            "C.ready()",
        ),
        (
            "def f():\n    @cache\n    def g():\n        return 1\n    return g\n",
            "def f():\n    @cache\n    def g():\n        return 2\n    return g\n",
            "cached f.g",
        ),
        # admin_cli.py's shape: module code calls a function at import.
        (
            "def _read_specs():\n    return [1]\nCOMMANDS = _read_specs()\n",
            "def _read_specs():\n    return [2]\nCOMMANDS = _read_specs()\n",
            "_read_specs, which runs when the module is imported",
        ),
        # ...and indirectly, through another function of the same file.
        (
            "def _a():\n    return 1\ndef _b():\n    return _a()\nX = [_b()]\n",
            "def _a():\n    return 2\ndef _b():\n    return _a()\nX = [_b()]\n",
            "_a, which runs when",
        ),
        # A decorator factory runs, with what it returns, at definition time.
        (
            "def register(name):\n    def wrap(f):\n        return f\n"
            "    return wrap\n@register('x')\ndef command():\n    pass\n",
            "def register(name):\n    def wrap(f):\n        TABLE[name] = f\n"
            "        return f\n    return wrap\n@register('x')\ndef command():\n"
            "    pass\n",
            "changes register, which runs when",
        ),
        # Defaults and class bodies count; a main guard does not.
        (
            "def _d():\n    return 1\ndef f(x=_d()):\n    return x\n",
            "def _d():\n    return 2\ndef f(x=_d()):\n    return x\n",
            "_d, which runs when",
        ),
        (
            "def _c():\n    return 1\nclass A:\n    choices = _c()\n",
            "def _c():\n    return 2\nclass A:\n    choices = _c()\n",
            "_c, which runs when",
        ),
        (
            "def main():\n    return 1\nif __name__ == '__main__':\n    main()\n",
            "def main():\n    return 2\nif __name__ == '__main__':\n    main()\n",
            None,
        ),
        (BODY_ONLY, "def (:\n", "cannot be parsed"),
        (None, BODY_ONLY, "added or deleted"),
        (BODY_ONLY, None, "added or deleted"),
    ],
)
def test_global_change(old, new, reason):
    """Only changes confined to ordinary function bodies stay attributable."""
    result = qs.global_change(old, new)
    assert result is None if reason is None else reason in result


def test_real_admin_cli_specs_run_at_import():
    """The checkout's admin_cli builds COMMANDS from _read_specs() at import."""
    tree = ast.parse((ROOT / "src/parishkit/stewardship/admin_cli.py").read_text())
    assert "_read_specs" in qs.import_time_functions(tree)


def test_mapped_global_change_runs_the_whole_group():
    """select() trusts global_change's verdict for a mapped file."""
    path = "src/parishkit/stewardship/forms.py"
    selection, why = qs.select([path], MAP, global_change=lambda p: "changes X")
    assert selection is None and why == f"{path} changes X"


def test_budget_caps_each_call_and_logs_when_spent(monkeypatch, capsys):
    """Calls get at most the time left; a spent budget refuses and logs."""
    clock = iter([0, 10, 359.5, 400, 400])
    monkeypatch.setattr(qs.time, "monotonic", lambda: next(clock))
    limits = []
    tool = qs.bounded(lambda root, *command, timeout: limits.append(timeout) or [])
    assert tool(ROOT, "git", "status") == []
    assert tool(ROOT, "git", "status") == []
    assert limits == [qs.TOOL_TIMEOUT_SECONDS, 0.5]
    assert tool(ROOT, "gh", "run", "list") is None
    err = capsys.readouterr().err
    assert "skipped 'gh run list'" in err and "budget 360s spent after 400.0s" in err


def test_budget_fits_inside_the_workflow_step():
    """Every per-call kill and the budget log land before GitHub kills the step."""
    validate = workflow()["validate"]
    step = next(item for item in validate["steps"] if item.get("id") == "select")
    assert step["timeout-minutes"] * 60 > qs.BUDGET_SECONDS + qs.TOOL_TIMEOUT_SECONDS
    assert validate["timeout-minutes"] >= 25 + step["timeout-minutes"]
