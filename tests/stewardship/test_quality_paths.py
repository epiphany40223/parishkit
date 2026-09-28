"""Path-based CI skipping must only ever skip work a change cannot affect."""

import itertools
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


@pytest.mark.parametrize(
    "changed,expected",
    [
        # Documentation-only: every heavy group may skip.
        (["docs/guides/a.md", "CLAUDE.md"], dict.fromkeys(paths.GROUPS, False)),
        (
            [".github/workflows/release.yml", "docs/x.md"],
            dict.fromkeys(paths.GROUPS, False),
        ),
        # The CI workflow itself always runs everything.
        ([".github/workflows/ci.yml"], ALL),
        (["docs/a.md", ".github/workflows/ci.yml"], ALL),
        # Application, requirements and packaging changes run everything.
        (["src/parishkit/stewardship/views.py"], ALL),
        (["src/parishkit/stewardship/schema/functions.sql"], ALL),
        (["requirements/stewardship.txt"], ALL),
        (["pyproject.toml"], ALL),
        (["README.md"], ALL),
        (["tests/stewardship/conftest.py"], ALL),
        (["unknown-top-level-file"], ALL),
        # Browser-only tests need only the browser engines.
        (
            ["tests/stewardship/browser/test_x.py"],
            {"postgresql": False, "browser": True, "compose": False},
        ),
        # Database-only tests need only the shards.
        (
            ["tests/stewardship/database/test_x.py"],
            {"postgresql": True, "browser": False, "compose": False},
        ),
        # Deployment files need only the container scenarios.
        (
            ["deploy/stewardship/Dockerfile"],
            {"postgresql": False, "browser": False, "compose": True},
        ),
        # Tools, wrappers and non-stewardship tests need no heavy group.
        (
            ["tools/x.sh", "scripts/y/run.py", "tests/test_config.py"],
            dict.fromkeys(paths.GROUPS, False),
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
    assert paths.classify(changed) == expected


def git(root, *args):
    """Run git quietly in a throwaway repository."""
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_decide_reads_the_test_merge_commit_diff(tmp_path):
    """The merge commit's first parent is the base; its diff is the PR."""
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "base.py").write_text("base\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "topic")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("guide\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "docs")
    git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "other.py").write_text("other\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "main moved on")
    git(tmp_path, "merge", "-q", "--no-ff", "-m", "test merge", "topic")
    assert paths.changed_paths(tmp_path) == ["docs/guide.md"]
    assert paths.decide("pull_request", tmp_path) == dict.fromkeys(paths.GROUPS, False)
    # Pushes, dispatches and anything else always run every group.
    assert paths.decide("workflow_dispatch", tmp_path) == ALL
    assert paths.decide("push", tmp_path) == ALL


def test_a_failed_diff_runs_everything(tmp_path):
    """A shallow or broken checkout without a parent never skips work."""
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "only.md").write_text("x\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "root")
    assert paths.changed_paths(tmp_path) is None
    assert paths.decide("pull_request", tmp_path) == ALL


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
