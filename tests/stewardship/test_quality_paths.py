"""Path-based CI skipping must only ever skip work a change cannot affect."""

import itertools
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from parishkit.stewardship import quality_paths as paths

ROOT = Path(__file__).resolve().parents[2]
ALL = dict.fromkeys(paths.GROUPS, True)
GATES = {
    "stewardship-postgresql": ("postgresql", ("SHARD_RESULT",)),
    "stewardship-browser": ("browser", ("BROWSER_RESULT",)),
    "stewardship-compose": ("compose", ("CORE_RESULT", "OPERATIONAL_RESULT")),
}
HEAVY = {
    "stewardship-postgresql-shard": "postgresql",
    "stewardship-browser-engine": "browser",
    "stewardship-compose-core": "compose",
    "stewardship-operational": "compose",
}


def workflow():
    """Load the real CI workflow."""
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())


def gate_passes(env, results):
    """The gate contract: full success, or one intentional ready-PR path skip.

    Draft skips, failed or skipped preflight, a missing classification, any
    failure or cancellation, and a partial skip must all fail the gate.
    """
    if all(env[name] == "success" for name in results):
        return True
    return (
        env["VALIDATE_RESULT"] == "success"
        and env["EVENT"] == "pull_request"
        and env["DRAFT"] == "false"
        and env["PATH_RUN"] == "false"
        and all(env[name] == "skipped" for name in results)
    )


def assert_gate_truth_table(step, results):
    """Execute a gate step for every relevant input combination."""
    domains = {
        **dict.fromkeys(results, ("success", "failure", "skipped", "")),
        "VALIDATE_RESULT": ("success", "failure"),
        "PATH_RUN": ("true", "false", ""),
        "EVENT": ("pull_request", "workflow_dispatch"),
        "DRAFT": ("false", "true", ""),
    }
    assert set(step["env"]) == set(domains)
    names = list(domains)
    for values in itertools.product(*(domains[name] for name in names)):
        env = dict(zip(names, values, strict=True))
        completed = subprocess.run(
            ["sh", "-e", "-c", step["run"]],
            env=env,
            capture_output=True,
            check=False,
            timeout=5,
        )
        assert (completed.returncode == 0) is gate_passes(env, results), env


@pytest.mark.parametrize("name", GATES)
def test_gates_accept_only_success_or_an_intentional_path_skip(name):
    """Each protected gate's full truth table, including path skips."""
    group, results = GATES[name]
    step = workflow()["jobs"][name]["steps"][0]
    assert step["env"]["PATH_RUN"] == f"${{{{ needs.validate.outputs.{group} }}}}"
    assert step["env"]["VALIDATE_RESULT"] == "${{ needs.validate.result }}"
    assert_gate_truth_table(step, results)


def test_heavy_jobs_follow_their_group_and_validate_exports_every_group():
    """Dispatch runs everything; ready PRs skip only a group marked 'false'."""
    jobs = workflow()["jobs"]
    validate = jobs["validate"]
    assert validate["outputs"] == {
        group: f"${{{{ steps.paths.outputs.{group} }}}}" for group in paths.GROUPS
    }
    for name, group in HEAVY.items():
        assert jobs[name]["if"] == (
            "${{ github.event_name == 'workflow_dispatch' || "
            "(github.event_name == 'pull_request' && "
            "github.event.pull_request.draft == false && "
            f"needs.validate.outputs.{group} != 'false') }}}}"
        )
    for name in GATES:
        assert "validate" in jobs[name]["needs"]


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
    """An intentional skip has no shard evidence to combine; a real run must."""
    steps = workflow()["jobs"]["stewardship-postgresql"]["steps"]
    assert steps[0]["id"] == "gate"
    for step in steps[1:]:
        assert step["if"] == "${{ steps.gate.outputs.intentional != 'true' }}"


MOUNTS = paths.compose_mounts(ROOT)
NONE = dict.fromkeys(paths.GROUPS, False)


def test_compose_mounts_come_from_the_development_tests_service():
    """The mounts are read from the Compose file, including the whole test tree."""
    assert MOUNTS is not None
    for mount in (
        "src",
        "tests",
        "deploy/stewardship",
        "docs/specs/stewardship",
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        "tools/prepare-release.py",
        "tools/ci-pip-install.sh",
        "tools/stewardship-dev-deploy.sh",
        "tools/stewardship-upgrade.sh",
        "tools/stewardship-upgrade-host.sh",
        "tools/stewardship-local.sh",
        "tools/stewardship-local-vm.sh",
        "tools/stewardship-ops",
        "docs/guides/stewardship-local-environment.md",
        "docs/guides/stewardship-operator-scripts.md",
        "docs/guides/stewardship-mail-send-report.md",
        "docs/guides/stewardship-deployment-runbook.md",
    ):
        assert mount in MOUNTS
    assert all(not mount.startswith(("/", "..")) for mount in MOUNTS)


@pytest.mark.parametrize(
    "text",
    ["", "services: {}\n", "services:\n  tests:\n    volumes: [a:b]\n", "{"],
)
def test_unreadable_compose_mounts_run_compose(tmp_path, text):
    """A missing or unexpected Compose file can only make compose run."""
    target = tmp_path / paths.COMPOSE_DEVELOPMENT
    target.parent.mkdir(parents=True)
    target.write_text(text)
    assert paths.compose_mounts(tmp_path) is None
    assert paths.compose_mounts(tmp_path / "missing") is None
    assert paths.classify(["docs/guides/a.md"], None)["compose"] is True


@pytest.mark.parametrize(
    "changed,expected",
    [
        # Unmounted documentation: every heavy group may skip.
        (["docs/guides/a.md", "CLAUDE.md"], NONE),
        ([".github/ISSUE_TEMPLATE/bug.md", "LICENSE"], NONE),
        # Documentation or workflows the in-image suite reads run compose.
        (
            ["docs/specs/stewardship/spec.md"],
            {"postgresql": False, "browser": False, "compose": True},
        ),
        (
            [".github/workflows/release.yml"],
            {"postgresql": False, "browser": False, "compose": True},
        ),
        # The CI workflow itself always runs everything.
        ([".github/workflows/ci.yml"], ALL),
        (["docs/a.md", ".github/workflows/ci.yml"], ALL),
        # Every job installs through the pip retry wrapper.
        (["tools/ci-pip-install.sh"], ALL),
        # Application, requirements and packaging changes run everything.
        (["src/parishkit/stewardship/views.py"], ALL),
        (["src/parishkit/stewardship/schema/functions.sql"], ALL),
        (["requirements/stewardship.txt"], ALL),
        (["pyproject.toml"], ALL),
        (["README.md"], ALL),
        (["tests/stewardship/conftest.py"], ALL),
        (["unknown-top-level-file"], ALL),
        # Browser tests also run in the image (collection parity).
        (
            ["tests/stewardship/browser/test_x.py"],
            {"postgresql": False, "browser": True, "compose": True},
        ),
        # Database tests run in the shards and in the image.
        (
            ["tests/stewardship/database/test_x.py"],
            {"postgresql": True, "browser": False, "compose": True},
        ),
        # Deployment files need only the container scenarios.
        (
            ["deploy/stewardship/Dockerfile"],
            {"postgresql": False, "browser": False, "compose": True},
        ),
        # Tools, wrappers and non-stewardship tests: the image runs them too.
        (
            ["tools/x.sh", "scripts/y/run.py", "tests/test_config.py"],
            {"postgresql": False, "browser": False, "compose": True},
        ),
        # A mixed change runs the union of its needs.
        (
            ["tests/stewardship/browser/a.py", "deploy/stewardship/compose.yaml"],
            {"postgresql": False, "browser": True, "compose": True},
        ),
        # An empty diff is suspicious, never a free pass.
        ([], ALL),
        ([""], ALL),
    ],
)
def test_classify(changed, expected):
    """Skip a group only when every changed path is on its explicit list."""
    assert paths.classify(changed, MOUNTS) == expected


def fake_git(parents, diff):
    """Stub git: answer the parent query and the diff, recording each call."""
    calls = []

    def run(root, *args):
        """Return the canned answer for one git query."""
        calls.append(args)
        return parents if args[0] == "rev-list" else diff

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
    assert paths.decide("pull_request", ROOT, git) == NONE


@pytest.mark.parametrize(
    "parents",
    [None, [], ["single parent"], ["octopus a b c"], ["merge base head", "x"]],
)
def test_anything_but_a_two_parent_merge_runs_everything(parents):
    """A single-parent HEAD would diff only its last commit, so never skip."""
    git = fake_git(parents, ["docs/guide.md"])
    assert paths.changed_paths(Path("."), git) is None
    assert paths.decide("pull_request", ROOT, git) == ALL


def test_a_failed_diff_runs_everything():
    """A git failure after the parent check never skips work."""
    assert paths.decide("pull_request", ROOT, fake_git(["m b h"], None)) == ALL


def test_other_events_never_consult_git():
    """Pushes, dispatches and anything else always run every group."""
    git = fake_git(["m b h"], ["docs/guide.md"])
    for event in ("workflow_dispatch", "push", "merge_group"):
        assert paths.decide(event, ROOT, git) == ALL
    assert git.calls == []


def test_missing_git_runs_everything(tmp_path, monkeypatch):
    """The slim application image has no git; that must not skip anything."""
    monkeypatch.setenv("PATH", str(tmp_path))
    assert paths.run_git(tmp_path, "status") is None
    assert paths.decide("pull_request", tmp_path) == ALL


def git(root, *args):
    """Run git quietly in a throwaway repository."""
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


# The application image deliberately has no git, and compose-core runs the
# complete suite inside it without --require-no-skips; validate runs this on
# the host with --require-no-skips, where git is always present.
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_real_git_merge_commit_diff(tmp_path):
    """End to end against real git: the merge diff, then a single-parent HEAD."""
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "base.py").write_text("base\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "topic")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "one.md").write_text("one\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "first")
    (tmp_path / "docs" / "two.md").write_text("two\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "second")
    # On the topic head itself (single parent) the last commit alone would
    # look like the whole change; the classifier must refuse to skip.
    assert paths.changed_paths(tmp_path) is None
    git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "other.py").write_text("other\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "main moved on")
    git(tmp_path, "merge", "-q", "--no-ff", "-m", "test merge", "topic")
    assert paths.changed_paths(tmp_path) == ["docs/one.md", "docs/two.md"]
    # This throwaway tree has no Compose file, so compose must run.
    assert paths.decide("pull_request", tmp_path) == {
        "postgresql": False,
        "browser": False,
        "compose": True,
    }


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
