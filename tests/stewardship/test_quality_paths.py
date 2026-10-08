"""Path-based CI skipping must only ever skip work a change cannot affect."""

import ast
import itertools
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from parishkit.stewardship import quality_paths as paths

ROOT = Path(__file__).resolve().parents[2]
ALL = dict.fromkeys(paths.GROUPS, True)
NONE = dict.fromkeys(paths.GROUPS, False)
# Each protected gate: its (job result, path classification) variables, and
# the validate output each classification variable reads.
GATES = {
    "stewardship-postgresql": {"SHARD_RESULT": ("PATH_RUN", "postgresql")},
    "stewardship-browser": {"BROWSER_RESULT": ("PATH_RUN", "browser")},
    "stewardship-compose": {
        "CORE_RESULT": ("CORE_RUN", "compose"),
        "OPERATIONAL_RESULT": ("OPERATIONAL_RUN", "operational"),
    },
}
HEAVY = {
    "stewardship-postgresql-shard": "postgresql",
    "stewardship-browser-engine": "browser",
    "stewardship-compose-core": "compose",
    "stewardship-operational": "operational",
}
# (EVENT, DRAFT, JOBS) contexts a gate can see. A pull request has no
# dispatch inputs and a dispatch has no draft flag; the crossed combinations
# prove that neither can unlock the other.
CONTEXTS = (
    ("pull_request", "false", ""),
    ("pull_request", "true", ""),
    ("pull_request", "", ""),
    ("pull_request", "true", "affected"),
    ("workflow_dispatch", "", "all"),
    ("workflow_dispatch", "", "affected"),
    ("workflow_dispatch", "", ""),
    ("workflow_dispatch", "false", "all"),
)


def workflow():
    """Load the real CI workflow."""
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())


def gate_passes(env, pairs):
    """The gate contract: each job succeeded or was skipped on purpose.

    A skip is intentional only after a successful preflight, in a ready pull
    request or an explicitly "affected" dispatch, with that job's group
    classified false. Draft skips, failed preflight, a missing classification,
    a default dispatch, failures and cancellations must all fail the gate.
    """
    allowed = env["VALIDATE_RESULT"] == "success" and (
        (env["EVENT"] == "pull_request" and env["DRAFT"] == "false")
        or (env["EVENT"] == "workflow_dispatch" and env["JOBS"] == "affected")
    )
    return all(
        env[result] == "success"
        or (allowed and env[run] == "false" and env[result] == "skipped")
        for result, (run, _group) in pairs.items()
    )


def assert_gate_truth_table(step, pairs):
    """Execute a gate step for every relevant input combination.

    Thousands of combinations run in one shell, each in its own ``set -e``
    subshell that assigns every input, which is far faster than one Python
    subprocess apiece. Only PATH is inherited, so a CI runner's
    GITHUB_OUTPUT can never receive the gate's output.
    """
    domains = {
        **dict.fromkeys(pairs, ("success", "failure", "cancelled", "skipped", "")),
        **dict.fromkeys((run for run, _ in pairs.values()), ("true", "false", "")),
        "VALIDATE_RESULT": ("success", "failure"),
        "CONTEXT": CONTEXTS,
    }
    assert set(step["env"]) == set(domains) - {"CONTEXT"} | {"EVENT", "DRAFT", "JOBS"}
    names = list(domains)
    cases = []
    for values in itertools.product(*(domains[name] for name in names)):
        env = dict(zip(names, values, strict=True))
        env["EVENT"], env["DRAFT"], env["JOBS"] = env.pop("CONTEXT")
        cases.append(env)
    script = "".join(
        "(set -e; "
        + " ".join(f"{name}={shlex.quote(value)};" for name, value in env.items())
        + ' eval "$1") >/dev/null 2>&1; echo $?\n'
        for env in cases
    )
    # The script (hundreds of KiB) goes through stdin: Linux caps one argv
    # string at 128 KiB (MAX_ARG_STRLEN). Only the small gate is an argument.
    assert len(step["run"].encode()) < 16 * 1024
    completed = subprocess.run(
        ["sh", "-s", step["run"]],
        input=script,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    codes = completed.stdout.split()
    assert len(codes) == len(cases)
    for env, code in zip(cases, codes, strict=True):
        assert (code == "0") is gate_passes(env, pairs), env


@pytest.mark.parametrize("name", GATES)
def test_gates_accept_only_success_or_an_intentional_path_skip(name):
    """Each protected gate's full truth table, including path skips."""
    pairs = GATES[name]
    step = workflow()["jobs"][name]["steps"][0]
    for run, group in pairs.values():
        assert step["env"][run] == f"${{{{ needs.validate.outputs.{group} }}}}"
    assert step["env"]["VALIDATE_RESULT"] == "${{ needs.validate.result }}"
    assert step["env"]["JOBS"] == "${{ inputs.jobs }}"
    assert_gate_truth_table(step, pairs)


def heavy_condition(group):
    """The ``if`` every heavy job uses: its group runs, in a full-suite event."""
    return (
        f"${{{{ needs.validate.outputs.{group} != 'false' && "
        "(github.event_name == 'workflow_dispatch' || "
        "(github.event_name == 'pull_request' && "
        "github.event.pull_request.draft == false)) }}"
    )


def test_heavy_jobs_follow_their_group_and_validate_exports_every_group():
    """Each heavy job runs only for a full-suite event its group can affect."""
    jobs = workflow()["jobs"]
    validate = jobs["validate"]
    assert validate["outputs"] == {
        **{group: f"${{{{ steps.paths.outputs.{group} }}}}" for group in paths.GROUPS},
        # The database test selection (#858) narrows, never skips, a group.
        "database_selection": "${{ steps.select.outputs.database_selection }}",
    }
    assert set(HEAVY.values()) == set(paths.GROUPS)
    for name, group in HEAVY.items():
        assert jobs[name]["if"] == heavy_condition(group)
    for name in GATES:
        assert "validate" in jobs[name]["needs"]


def test_dispatch_runs_everything_unless_affected_jobs_are_requested():
    """Release and merge evidence stays complete by default.

    ``gh workflow run ci.yml`` without ``-f jobs=affected`` (as release.sh
    dispatches it) must run every job; only the explicit choice may skip.
    """
    dispatch = workflow()[True]["workflow_dispatch"]
    assert dispatch["inputs"]["jobs"] == {
        "description": ("Run all jobs, or only those the branch's changes can affect"),
        "type": "choice",
        "options": ["all", "affected"],
        "default": "all",
    }
    steps = workflow()["jobs"]["validate"]["steps"]
    names = [step.get("name") for step in steps]
    fetch = steps[names.index("Fetch main for an affected-jobs dispatch")]
    assert fetch["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.jobs == 'affected' }}"
    )
    # A failed fetch leaves no merge base, which the classifier runs in full.
    assert fetch["continue-on-error"] is True
    assert "origin +refs/heads/main:refs/remotes/origin/main" in fetch["run"]
    classify = steps[names.index("Classify changed paths")]
    assert classify["env"]["JOBS"] == "${{ inputs.jobs }}"
    assert '--jobs "$JOBS"' in classify["run"]
    assert names.index("Fetch main for an affected-jobs dispatch") < names.index(
        "Classify changed paths"
    )


def test_database_shards_check_out_the_release_history():
    """The upgrade-parity test needs the previous release tag reachable from HEAD.

    actions/checkout defaults to one commit and no tags; depth 0 fetches every
    branch and tag, which is what ``git describe`` needs to find the tag
    (tests/stewardship/database/test_upgrade_parity_postgresql.py).
    """
    checkout = workflow()["jobs"]["stewardship-postgresql-shard"]["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["fetch-depth"] == 0


def test_skipped_shards_are_replaced_by_the_non_database_suite():
    """Shard one's complete non-database suite still runs when shards skip."""
    steps = workflow()["jobs"]["validate"]["steps"]
    names = [step.get("name") for step in steps]
    checkout = steps[0]
    assert checkout["with"]["fetch-depth"] == 2
    classify = steps[names.index("Classify changed paths")]
    assert classify["id"] == "paths"
    assert "parishkit.stewardship.quality_paths" in classify["run"]
    complete = steps[names.index("Complete non-database suite")]
    assert complete["if"] == "${{ steps.paths.outputs.postgresql == 'false' }}"
    assert complete["run"].startswith("python -m pytest tests ")
    assert "--ds=parishkit.stewardship.settings.test" in complete["run"]
    assert names.index("Classify changed paths") < names.index(
        "Complete non-database suite"
    )


def test_coverage_combination_runs_only_when_shards_ran():
    """An intentional skip has no shard evidence to combine; a real run must,
    unless it is an unmeasured "affected" dispatch (#858)."""
    steps = workflow()["jobs"]["stewardship-postgresql"]["steps"]
    assert steps[0]["id"] == "gate"
    for step in steps[1:]:
        assert step["if"] == (
            "${{ steps.gate.outputs.intentional != 'true' && "
            "inputs.jobs != 'affected' }}"
        )


REACHABLE = paths.browser_modules(ROOT)
MOUNTS = paths.compose_mounts(ROOT)
NO_BROWSER = {**ALL, "browser": False}
COMPOSE = {**NONE, "compose": True}
TEMPLATES = {**NONE, "postgresql": True, "browser": True, "compose": True}
STATIC = {**NONE, "browser": True, "compose": True}
CONTAINERS = {**NONE, "compose": True, "operational": True}
# Synthetic application modules: one the browser tests import, one they do not.
UI = "src/parishkit/stewardship/accounts/page_forms.py"
WORKER = "src/parishkit/stewardship/batch_worker.py"


@pytest.mark.parametrize(
    "changed,expected",
    [
        # Documentation needs only validate, which then runs the complete
        # non-database suite, traceability included.
        (["docs/guides/a.md", "CLAUDE.md"], NONE),
        (["LICENSE", "AGENTS.md", ".pymarkdown.json"], NONE),
        # Documentation the tests service mounts also runs compose-core, whose
        # in-image suite reads it (collection parity).
        (["docs/specs/stewardship/spec.md"], COMPOSE),
        (["docs/development/stewardship-acceptance.yaml"], COMPOSE),
        (["scripts/pk-cron-runner/README.md"], COMPOSE),
        # A guide whose SQL a database test executes keeps the shards.
        (
            ["docs/guides/stewardship-mail-send-report.md"],
            {**COMPOSE, "postgresql": True},
        ),
        # CI, deployment, settings, schema, migrations and CI tooling run
        # everything.
        ([".github/workflows/ci.yml"], ALL),
        ([".github/workflows/release.yml"], ALL),
        ([".github/ISSUE_TEMPLATE/bug.md"], ALL),
        (["docs/a.md", ".github/workflows/ci.yml"], ALL),
        (["tools/ci-pip-install.sh"], ALL),
        (["deploy/stewardship/Dockerfile"], ALL),
        (["deploy/stewardship/compose.yaml"], ALL),
        (["src/parishkit/stewardship/settings/base.py"], ALL),
        (["src/parishkit/stewardship/schema/functions.sql"], ALL),
        (["src/parishkit/stewardship/schema/migrations/0009_x.sql"], ALL),
        (["src/parishkit/stewardship/accounts/migrations/0009_x.py"], ALL),
        (["src/parishkit/stewardship/quality_ci.py"], ALL),
        (["src/parishkit/stewardship/quality_paths.py"], ALL),
        # Requirements, packaging, image inputs and test infrastructure.
        (["requirements/stewardship.txt"], ALL),
        (["requirements.txt"], ALL),
        (["pyproject.toml"], ALL),
        (["README.md"], ALL),
        ([".dockerignore"], ALL),
        (["coverage-stewardship.toml"], ALL),
        (["tests/conftest.py"], ALL),
        (["tests/stewardship/conftest.py"], ALL),
        # Stewardship test modules and helpers follow the application rule.
        (["tests/stewardship/test_x.py"], NO_BROWSER),
        (["tests/stewardship/browser_helpers.py"], ALL),
        # Unknown paths, including a new top-level directory, run everything.
        (["unknown-top-level-file"], ALL),
        (["newdir/module.py"], ALL),
        (["src/parishkit/stewardship/accounts/data.json"], ALL),
        # Templates skip only the operational scenarios: database tests render
        # pages and mail from them.
        (
            ["src/parishkit/stewardship/accounts/templates/stewardship/a.html"],
            TEMPLATES,
        ),
        # Static assets: browser engines and compose-core only.
        (
            [
                "src/parishkit/stewardship/accounts/static/stewardship/a.css",
                "src/parishkit/stewardship/accounts/static/stewardship/a.js",
            ],
            STATIC,
        ),
        # Application Python skips the engines only outside their reach.
        ([WORKER], NO_BROWSER),
        ([UI], ALL),
        ([WORKER, "src/parishkit/stewardship/accounts/templates/x.html"], ALL),
        # Browser tests also run in the image (collection parity).
        (["tests/stewardship/browser/test_x.py"], {**CONTAINERS, "browser": True}),
        # Database tests run in the shards and in the image.
        (
            ["tests/stewardship/database/test_x.py"],
            {**CONTAINERS, "postgresql": True},
        ),
        # Tools, wrappers and non-stewardship tests: the image runs them too.
        (["tools/x.sh", "scripts/y/run.py", "tests/test_config.py"], CONTAINERS),
        # Operator SQL is executed by a database test.
        (["tools/stewardship-ops/predeploy.sql"], {**CONTAINERS, "postgresql": True}),
        # A mixed change runs the union of its needs.
        (
            ["docs/a.md", "src/parishkit/stewardship/accounts/static/a.css"],
            STATIC,
        ),
        (["tests/stewardship/browser/a.py", "deploy/stewardship/compose.yaml"], ALL),
        # An empty diff is suspicious, never a free pass.
        ([], ALL),
        ([""], ALL),
    ],
)
def test_classify(changed, expected):
    """Skip a group only when no changed path needs it."""
    reachable = frozenset({UI, "tests/stewardship/browser_helpers.py"})
    assert paths.classify(changed, reachable, MOUNTS) == expected


def test_unknown_browser_reach_runs_the_engines_for_application_python():
    """Without the import closure, any application Python runs everything."""
    assert paths.classify([WORKER], None, MOUNTS) == ALL
    assert paths.classify(["docs/a.md"], None, MOUNTS) == NONE


def test_compose_mounts_come_from_the_development_tests_service():
    """The mounts are read from the Compose file, including the whole test tree."""
    assert MOUNTS is not None
    for mount in (
        "src",
        "tests",
        "deploy/stewardship",
        "docs/specs/stewardship",
        ".github/workflows/ci.yml",
        "tools/stewardship-ops",
        "docs/guides/stewardship-deployment-runbook.md",
    ):
        assert mount in MOUNTS
    assert all(not mount.startswith(("/", "..")) for mount in MOUNTS)


@pytest.mark.parametrize(
    "text",
    ["", "services: {}\n", "services:\n  tests:\n    volumes: [a:b]\n", "{"],
)
def test_unreadable_compose_mounts_run_compose(tmp_path, text):
    """A missing or unexpected Compose file can only make compose-core run."""
    target = tmp_path / paths.COMPOSE_DEVELOPMENT
    target.parent.mkdir(parents=True)
    target.write_text(text)
    assert paths.compose_mounts(tmp_path) is None
    assert paths.compose_mounts(tmp_path / "missing") is None
    assert paths.classify(["docs/guides/a.md"], None, None) == COMPOSE


def test_a_whole_repository_mount_runs_compose_for_everything(tmp_path):
    """A mount of the repository root normalises to "", which matches nothing
    by prefix; it must mean "everything is mounted", so compose-core runs."""
    target = tmp_path / paths.COMPOSE_DEVELOPMENT
    target.parent.mkdir(parents=True)
    target.write_text(
        "services:\n  tests:\n    volumes:\n"
        "      - {source: ../.., target: /app}\n"
        "      - {source: ../../src, target: /app/src}\n"
    )
    assert paths.compose_mounts(tmp_path) is None
    assert paths.classify(["LICENSE"], None, None)["compose"] is True


def test_browser_closure_covers_what_the_browser_tests_import():
    """The closure starts at the browser tests and Django's implicit inputs.

    Direct imports, transitive imports, template tag libraries and dotted
    module strings (settings, include()) are all followed, but some
    application Python stays outside, or the rule could never skip anything.
    """
    assert REACHABLE is not None
    for module in (
        # tests/stewardship/browser/conftest.py imports these directly.
        "src/parishkit/stewardship/accounts/campaign_forms.py",
        "src/parishkit/stewardship/campaigns/domain.py",
        # Template tag libraries and the settings that name the URL routes.
        "src/parishkit/stewardship/accounts/templatetags/stewardship.py",
        "src/parishkit/stewardship/settings/base.py",
        "src/parishkit/stewardship/urls.py",
        # Django's implicit app modules (django.setup(), admin autodiscover).
        "src/parishkit/stewardship/accounts/apps.py",
        "tests/stewardship/browser/conftest.py",
    ):
        assert module in REACHABLE, module
    application = {
        path.relative_to(ROOT).as_posix() for path in (ROOT / "src").rglob("*.py")
    }
    assert application - REACHABLE


def test_browser_closure_resolves_relative_and_string_imports(tmp_path):
    """A synthetic tree pins each kind of import edge, and parse failures."""
    files = {
        "tests/stewardship/browser/test_page.py": "from parishkit.app import forms\n",
        "src/parishkit/__init__.py": "",
        "src/parishkit/app/__init__.py": "",
        "src/parishkit/app/forms.py": "from . import widgets\nfrom .. import shared\n",
        "src/parishkit/app/widgets.py": "ROUTES = 'parishkit.app.routes'\n",
        "src/parishkit/app/routes.py": "",
        "src/parishkit/shared.py": "",
        "src/parishkit/app/worker.py": "from parishkit.app import forms\n",
        "src/parishkit/app/templatetags/tags.py": "import parishkit.app.filters\n",
        "src/parishkit/app/filters.py": "",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    reachable = paths.browser_modules(tmp_path)
    assert reachable == {
        name for name in files if name != "src/parishkit/app/worker.py"
    }
    (tmp_path / "src/parishkit/app/routes.py").write_text("def broken(:\n")
    assert paths.browser_modules(tmp_path) is None


def test_deleted_application_modules_run_the_browser_engines(tmp_path):
    """A deleted module's importers cannot be traced, so never skip for it."""
    (tmp_path / "tests/stewardship/browser").mkdir(parents=True)
    (tmp_path / "tests/stewardship/browser/test_a.py").write_text("")
    (tmp_path / "src/parishkit").mkdir(parents=True)
    (tmp_path / "src/parishkit/kept.py").write_text("")
    git = fake_git(["m b h"], ["src/parishkit/kept.py"])
    assert paths.decide("pull_request", tmp_path, git=git) == NO_BROWSER
    git = fake_git(["m b h"], ["src/parishkit/gone.py"])
    assert paths.decide("pull_request", tmp_path, git=git) == ALL


# Every tracked top-level entry, pinned so that a new one is a conscious
# decision. Anything not matched by an explicit rule already runs everything;
# this keeps that default from being silently narrowed later.
TOP_LEVEL = {
    ".dockerignore",
    ".github",
    ".gitignore",
    ".pymarkdown.json",
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    "README.md",
    "coverage-stewardship.toml",
    "deploy",
    "docs",
    "install.py",
    "parishkit-logo.png",
    "pyproject.toml",
    "requirements",
    "requirements.txt",
    "scripts",
    "src",
    "tests",
    "tools",
}


# The application image has no git; validate runs this on the host with
# --require-no-skips, where git is always present.
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_path_groups_are_pinned_for_every_top_level_entry():
    """A new top-level directory fails here until its path group is decided."""
    listed = paths.run_git(ROOT, "ls-files")
    if listed is None:
        pytest.skip("not a git checkout")
    assert {path.split("/", 1)[0] for path in listed} == TOP_LEVEL
    # Only these top-level entries have any rule that can skip a group.
    patterns = [
        *(pattern for patterns, _ in paths.RULES for pattern in patterns),
        *paths.APPLICATION_PYTHON,
    ]
    assert {pattern.split("/", 1)[0] for pattern in patterns} == {
        ".pymarkdown.json",
        "AGENTS.md",
        "CLAUDE.md",
        "LICENSE",
        "docs",
        "scripts",
        "src",
        "tests",
        "tools",
    }


# Database and browser test files, and the shared tests/stewardship helpers
# they import, that name a checkout directory outside src/ and tests/: each
# either reads a file there (which must then run its group) or only mentions
# the path in page text (None).
CHECKOUT_READERS = {
    "tests/stewardship/database/test_ops_sql_postgresql.py": (
        "tools/stewardship-ops/predeploy.sql"
    ),
    "tests/stewardship/database/test_mail_send_report_postgresql.py": (
        "docs/guides/stewardship-mail-send-report.md"
    ),
    "tests/stewardship/browser/test_automation.py": None,
    # The LOCAL step-up's laptop command, shown as page text (#619).
    "tests/stewardship/browser/test_local_step_up.py": None,
    # A parametrize argument name, not a path.
    "tests/stewardship/browser/test_directories.py": None,
}
CHECKOUT_DIRECTORIES = ("docs", "tools", "scripts", "deploy", ".github")


def names_checkout_directory(path):
    """Whether a test file has a string naming a non-source checkout path."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.split("/", 1)[0] in CHECKOUT_DIRECTORIES
        for node in ast.walk(tree)
    )


def test_database_and_browser_test_checkout_inputs_are_pinned():
    """A database or browser test reading docs or tools keeps its group.

    The path rules skip the shards for documentation and tools, so a test
    there that reads such a file must be listed in DATABASE_INPUTS. A new
    reader fails here until it is.
    """
    candidates = [
        *(ROOT / "tests/stewardship/database").rglob("*.py"),
        *(ROOT / "tests/stewardship/browser").rglob("*.py"),
        # Shared helpers (factories, fakes, conftest); test modules here run
        # only in the non-database suite, which validate always covers.
        *(
            path
            for path in (ROOT / "tests/stewardship").glob("*.py")
            if not path.name.startswith("test_")
        ),
    ]
    readers = {
        path.relative_to(ROOT).as_posix()
        for path in candidates
        if "node_modules" not in path.parts and names_checkout_directory(path)
    }
    assert readers == set(CHECKOUT_READERS)
    for reader, input_path in CHECKOUT_READERS.items():
        if input_path is not None:
            group = "postgresql" if "/database/" in reader else "browser"
            assert (ROOT / input_path).exists(), input_path
            assert paths.classify([input_path], REACHABLE)[group] is True


def fake_git(parents, diff, base=None):
    """Stub git: answer the parent, merge-base and diff queries, recording each."""
    calls = []
    answers = {"rev-list": parents, "merge-base": base, "diff": diff}

    def run(root, *args):
        """Return the canned answer for one git query."""
        calls.append(args)
        return answers[args[0]]

    run.calls = calls
    return run


def test_changed_paths_diffs_the_test_merge_commit_against_its_base():
    """A two-parent HEAD is GitHub's test merge; its first parent is the base."""
    git = fake_git(["merge base head"], ["docs/guide.md"])
    assert paths.changed_paths(Path("."), git) == ["docs/guide.md"]
    assert git.calls == [
        ("rev-list", "--parents", "-n", "1", "HEAD"),
        ("diff", "--name-only", "--no-renames", "HEAD^1", "HEAD"),
    ]
    assert paths.decide("pull_request", ROOT, git=git) == NONE


@pytest.mark.parametrize(
    "parents",
    [None, [], ["single parent"], ["octopus a b c"], ["merge base head", "x"]],
)
def test_anything_but_a_two_parent_merge_runs_everything(parents):
    """A single-parent HEAD would diff only its last commit, so never skip."""
    git = fake_git(parents, ["docs/guide.md"])
    assert paths.changed_paths(Path("."), git) is None
    assert paths.decide("pull_request", ROOT, git=git) == ALL


def test_a_failed_diff_runs_everything():
    """A git failure after the parent check never skips work."""
    assert paths.decide("pull_request", ROOT, git=fake_git(["m b h"], None)) == ALL


@pytest.mark.parametrize(
    "event,jobs",
    [
        ("workflow_dispatch", "all"),
        ("workflow_dispatch", ""),
        ("push", "affected"),
        ("merge_group", "affected"),
    ],
)
def test_other_events_never_consult_git(event, jobs):
    """Pushes, default dispatches and anything else always run every group."""
    git = fake_git(["m b h"], ["docs/guide.md"], ["base"])
    assert paths.decide(event, ROOT, jobs, git) == ALL
    assert git.calls == []


def test_affected_dispatch_diffs_the_branch_against_its_main_merge_base():
    """An explicit "affected" dispatch classifies the branch's own changes."""
    git = fake_git(None, ["docs/guide.md"], ["abc123"])
    assert paths.decide("workflow_dispatch", ROOT, "affected", git) == NONE
    assert git.calls == [
        ("merge-base", "origin/main", "HEAD"),
        ("diff", "--name-only", "--no-renames", "abc123", "HEAD"),
    ]


@pytest.mark.parametrize(
    "base,diff", [(None, ["docs/a.md"]), ([], ["docs/a.md"]), (["a", "b"], ["x"])]
)
def test_affected_dispatch_without_one_merge_base_runs_everything(base, diff):
    """No fetched main (or an ambiguous answer) can never thin a dispatch."""
    git = fake_git(None, diff, base)
    assert paths.decide("workflow_dispatch", ROOT, "affected", git) == ALL


def test_a_git_query_past_its_limit_is_logged_and_runs_everything(monkeypatch, capsys):
    """A hung git is killed at its limit, logged (#287), and never skips work."""

    def hang(command, **kwargs):
        """Behave like subprocess.run when the time limit passes."""
        assert kwargs["timeout"] == paths.GIT_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(paths.subprocess, "run", hang)
    assert paths.run_git(ROOT, "merge-base", "origin/main", "HEAD") is None
    message = capsys.readouterr().err
    assert "killed 'git merge-base origin/main HEAD' after" in message
    assert f"(limit {paths.GIT_TIMEOUT_SECONDS}s)" in message
    assert paths.decide("workflow_dispatch", ROOT, "affected") == ALL


def test_missing_git_runs_everything(tmp_path, monkeypatch, capsys):
    """The slim application image has no git; that must not skip anything."""
    monkeypatch.setenv("PATH", str(tmp_path))
    assert paths.run_git(tmp_path, "status") is None
    assert "cannot run 'git status'" in capsys.readouterr().err
    assert paths.decide("pull_request", tmp_path) == ALL
    assert paths.decide("workflow_dispatch", tmp_path, "affected") == ALL


def git(root, *args):
    """Run git quietly in a throwaway repository."""
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def commit(root, name, text):
    """Write one file and commit it."""
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", name)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_failed_git_query_is_logged(tmp_path, capsys):
    """A non-zero git exit is logged with its reason and runs everything."""
    git(tmp_path, "init", "-q")
    # A fresh repository has no HEAD commit, so this exits non-zero.
    assert paths.run_git(tmp_path, "rev-parse", "--verify", "HEAD") is None
    message = capsys.readouterr().err
    assert "'git rev-parse --verify HEAD' exited" in message
    assert "running every job group" in message


# The application image deliberately has no git, and compose-core runs the
# complete suite inside it without --require-no-skips; validate runs this on
# the host with --require-no-skips, where git is always present.
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_real_git_merge_commit_diff(tmp_path):
    """End to end against real git: the merge diff, then a single-parent HEAD."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "base.py", "base\n")
    git(tmp_path, "checkout", "-q", "-b", "topic")
    commit(tmp_path, "docs/one.md", "one\n")
    commit(tmp_path, "docs/two.md", "two\n")
    # On the topic head itself (single parent) the last commit alone would
    # look like the whole change; the classifier must refuse to skip.
    assert paths.changed_paths(tmp_path) is None
    git(tmp_path, "checkout", "-q", "main")
    commit(tmp_path, "other.py", "other\n")
    git(tmp_path, "merge", "-q", "--no-ff", "-m", "test merge", "topic")
    assert paths.changed_paths(tmp_path) == ["docs/one.md", "docs/two.md"]
    # This throwaway tree has no Compose file, so compose-core must run.
    assert paths.decide("pull_request", tmp_path) == COMPOSE


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_real_git_affected_dispatch(tmp_path):
    """A branch diffs from its merge base; a commit on main runs everything.

    A commit that is already on main when the run starts has an empty diff,
    so even an "affected" dispatch of it runs every group. That says nothing
    about a run made before the commit reached main, which is why release.yml
    and release.sh also refuse any run named "CI (jobs: affected)".
    """
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "base.py", "base\n")
    git(tmp_path, "checkout", "-q", "-b", "topic")
    commit(tmp_path, "docs/one.md", "one\n")
    git(tmp_path, "checkout", "-q", "main")
    commit(tmp_path, "other.py", "other\n")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "main")
    git(tmp_path, "checkout", "-q", "topic")
    assert paths.branch_paths(tmp_path) == ["docs/one.md"]
    assert paths.decide("workflow_dispatch", tmp_path, "affected") == COMPOSE
    assert paths.decide("workflow_dispatch", tmp_path, "all") == ALL
    git(tmp_path, "checkout", "-q", "main")
    assert paths.branch_paths(tmp_path) == []
    assert paths.decide("workflow_dispatch", tmp_path, "affected") == ALL


def test_main_writes_step_outputs(tmp_path, monkeypatch, capsys):
    """Every group is written as a lowercase boolean output line."""
    output = tmp_path / "github-output"
    output.write_text("existing=1\n")
    monkeypatch.chdir(tmp_path)
    assert paths.main(["--event", "workflow_dispatch", "--output", str(output)]) == 0
    expected = [f"{group}=true" for group in paths.GROUPS]
    assert output.read_text().splitlines() == ["existing=1", *expected]
    assert capsys.readouterr().out.splitlines() == expected
    assert paths.main(["--event", "push"]) == 0
    assert paths.main(["--event", "pull_request", "--jobs", ""]) == 0
    with pytest.raises(SystemExit):
        paths.main(["--event", "workflow_dispatch", "--jobs", "some"])
