"""Credential-free contracts for matching host, CI, and image build tools."""

import os
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
INSTALL_COMMANDS = [
    "python -m pip install -r requirements/stewardship-build.txt",
    "python -m pip install --no-build-isolation -r requirements.txt",
]
# CI runs the same ordered installs through a bounded retry wrapper, so a
# transient index or network fault on one runner does not fail the job.
CI_INSTALL_COMMANDS = [
    command.replace("python -m pip install ", "tools/ci-pip-install.sh ")
    for command in INSTALL_COMMANDS
]
BUILD_INPUTS = (
    "README.md",
    "pyproject.toml",
    "requirements/stewardship.txt",
    "requirements/stewardship-build.txt",
    ".dockerignore",
    "deploy/stewardship/Dockerfile",
    "deploy/stewardship/Dockerfile.dockerignore",
)


def assert_build_inputs_match(image_root, checkout_root):
    """Require baked build inputs to match read-only checkout reference copies.

    Compare bytes, not timestamps, and report only known fixture names rather
    than contents. Missing or unreadable inputs cannot masquerade as freshness.
    The image's metadata and installed dependencies are never modified.
    """
    stale = []
    for name in BUILD_INPUTS:
        try:
            matches = (image_root / name).read_bytes() == (
                checkout_root / name
            ).read_bytes()
        except OSError:
            matches = False
        if not matches:
            stale.append(name)
    if stale:
        pytest.fail(
            "Build inputs differ or are unreadable: "
            + ", ".join(stale)
            + ". Rebuild the development image.",
            pytrace=False,
        )


def test_image_build_inputs_match_checkout():
    """Compose supplies separate reference mounts; a host run has no image."""
    checkout = os.environ.get("PARISHKIT_TEST_CHECKOUT_ROOT")
    if checkout is not None:
        assert checkout, "PARISHKIT_TEST_CHECKOUT_ROOT must not be empty"
        assert_build_inputs_match(ROOT / "baked-build-inputs", Path(checkout))


@pytest.fixture
def build_input_copies(tmp_path):
    """Create independent synthetic trees with every required build input."""
    roots = (tmp_path / "image", tmp_path / "checkout")
    for root in roots:
        for name in BUILD_INPUTS:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-original\n")
    return roots


def test_build_input_freshness_accepts_identical_copies(build_input_copies):
    """Matching bytes pass without rewriting either input tree."""
    assert_build_inputs_match(*build_input_copies)


@pytest.mark.parametrize("name", BUILD_INPUTS)
@pytest.mark.parametrize("fault", ["changed", "missing_checkout", "missing_image"])
def test_build_input_freshness_rejects_stale_or_missing_copies(
    build_input_copies, name, fault
):
    """Any missing or stale file fails without printing compared file contents."""
    image, checkout = build_input_copies
    if fault == "changed":
        (checkout / name).write_bytes(b"synthetic-private-new-content\n")
    else:
        root = image if fault == "missing_image" else checkout
        (root / name).unlink()
    with pytest.raises(
        pytest.fail.Exception, match="Rebuild the development image"
    ) as exc:
        assert_build_inputs_match(image, checkout)
    assert name in str(exc.value)
    assert "synthetic-private" not in str(exc.value)


def test_build_input_freshness_wiring(monkeypatch, tmp_path):
    """The real test activates only when Compose supplies reference inputs."""
    monkeypatch.delenv("PARISHKIT_TEST_CHECKOUT_ROOT", raising=False)
    test_image_build_inputs_match_checkout()
    monkeypatch.setenv("PARISHKIT_TEST_CHECKOUT_ROOT", str(tmp_path))
    with pytest.raises(pytest.fail.Exception, match="Rebuild the development image"):
        test_image_build_inputs_match_checkout()


def locked_requirements(name):
    """Read generated requirement pins, ignoring only comments and blank lines."""
    requirements = {}
    for line in (ROOT / "requirements" / name).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            requirement = Requirement(line)
            requirements[canonicalize_name(requirement.name)] = requirement
    return requirements


def test_shared_http_dependency_requires_bounded_streaming():
    """Unpinned package installs require the patched streaming implementation too."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    dependency = next(
        Requirement(value)
        for value in project["dependencies"]
        if Requirement(value).name == "urllib3"
    )
    assert "2.6.3" not in dependency.specifier
    assert "2.7.0" in dependency.specifier
    assert "3.0.0" not in dependency.specifier


def test_ci_runs_real_broker_acl_tests():
    """The opt-in Valkey/Kombu tests are mandatory in the runtime CI profile."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["stewardship-compose-core"]["steps"]
        if step.get("name") == "Validate operational runtime and provisioning"
    )
    assert "tests/stewardship/test_broker_valkey_container.py" in step["run"]
    assert "--require-no-skips" in step["run"]
    assert step["env"]["PARISHKIT_RUN_RUNTIME_TESTS"] == "1"


def test_dev_extra_includes_runtime_extras_without_duplicating_dependencies():
    """The dev extra supplies Django and Google libraries through owned extras."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    extras = project["optional-dependencies"]
    dev = [Requirement(value) for value in extras["dev"]]
    links = [
        requirement
        for requirement in dev
        if canonicalize_name(requirement.name) == canonicalize_name(project["name"])
    ]
    assert len(links) == 1
    link = links[0]
    assert link.extras == {"stewardship", "google"}
    assert not link.specifier and link.marker is None and link.url is None
    runtime_names = {
        canonicalize_name(Requirement(value).name)
        for extra in link.extras
        for value in extras[extra]
    }
    assert {"django", "google-api-python-client"} <= runtime_names
    assert not runtime_names & {canonicalize_name(value.name) for value in dev}


@pytest.mark.parametrize(
    ("workflow", "job"),
    [
        ("ci.yml", "validate"),
        ("ci.yml", "stewardship-compose-core"),
        ("ci.yml", "stewardship-operational"),
        ("release.yml", "validate-build"),
    ],
)
def test_ci_installs_locked_backend_before_editable_project(workflow, job):
    """Each host job uses the locked backend rather than fresh isolated resolution."""
    definition = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    install = next(
        step
        for step in definition["jobs"][job]["steps"]
        if step.get("name") == "Install dependencies"
    )
    assert install["run"].strip().splitlines() == CI_INSTALL_COMMANDS


def test_ci_pip_wrapper_retries_the_same_install():
    """The wrapper only retries pip install with the caller's arguments."""
    script = ROOT / "tools/ci-pip-install.sh"
    text = script.read_text()
    assert os.access(script, os.X_OK)
    assert 'python -m pip install --retries 5 --timeout 60 "$@"' in text
    assert "attempts=3" in text
    for workflow in ("ci.yml", "release.yml"):
        assert (
            "python -m pip install"
            not in (ROOT / ".github/workflows" / workflow).read_text()
        )


def test_release_build_uses_installed_locked_tools():
    """Packaging cannot silently resolve a second, unpinned backend after tests."""
    definition = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    build = next(
        step
        for step in definition["jobs"]["validate-build"]["steps"]
        if step.get("name") == "Build artifacts"
    )
    assert build["run"] == "python -m build --no-isolation"
    assert "build" in locked_requirements("stewardship.txt")


GATE = "Require a successful full CI run of the release tree"


def release_gate():
    """The release workflow's evidence step."""
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    (gate,) = [
        step
        for step in release["jobs"]["validate-build"]["steps"]
        if step.get("name") == GATE
    ]
    return gate


def test_release_requires_full_ci_of_the_release_tree_before_build():
    """Releases reuse a full CI run of the tagged tree, apart from docs (#662)."""
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    # PyYAML reads the bare `on:` key as the boolean true. Pushes to main run
    # light validation only (temporarily); a manual dispatch must run every
    # job by default, or a tagged commit could lack complete evidence; the
    # release gate accepts only a run named "CI (jobs: all)"
    # (release_evidence.py and its tests).
    assert ci[True]["push"] == {"branches": ["main"]}
    assert ci[True]["workflow_dispatch"]["inputs"]["jobs"]["default"] == "all"
    for job in ci["jobs"].values():
        condition = job.get("if", "${{ always() }}")
        assert (
            condition == "${{ always() }}"
            or "github.event_name == 'workflow_dispatch' ||" in condition
            or condition == "${{ always() && github.event_name != 'push' }}"
        )
    job = release["jobs"]["validate-build"]
    assert "services" not in job
    assert job["permissions"] == {"actions": "read", "contents": "read"}
    steps = job["steps"]
    names = [step.get("name") for step in steps]
    checkout = steps[names.index("Check out repository")]
    # The evidence commit is diffed against the tag, so history is needed.
    assert checkout["with"]["fetch-depth"] == 0
    tag = steps[names.index("Validate release tag")]
    assert tag["id"] == "tag"
    assert 'echo "commit=${release_commit}" >> "$GITHUB_OUTPUT"' in tag["run"]
    gate = steps[names.index(GATE)]
    assert gate["id"] == "evidence"
    assert gate["env"]["RELEASE_COMMIT"] == "${{ steps.tag.outputs.commit }}"
    # The workflow asks the shared rule itself; release.sh is not trusted.
    assert "python tools/stewardship-ops/release_evidence.py select" in gate["run"]
    assert '--commit "${RELEASE_COMMIT}"' in gate["run"]
    # A docs-only difference reruns the documentation checks on the tagged
    # tree (#662): Markdown lint and every test file that reads guides or
    # specs, without a database.
    docs = steps[names.index("Check the documentation the evidence run did not see")]
    assert docs["id"] == "docs"
    assert docs["if"] == "${{ steps.evidence.outputs.docs != '0' }}"
    assert (
        "pymarkdown --config .pymarkdown.json scan $(git ls-files '*.md')"
        in (docs["run"])
    )
    assert "release_evidence.py docs-tests" in docs["run"]
    assert "python -m pytest ${docs_tests}" in docs["run"]
    assert (
        names.index(GATE) < names.index(docs["name"]) < names.index("Build artifacts")
    )
    notes = steps[names.index("Generate release notes")]
    assert notes["env"]["DOCS_CHECKS"] == "${{ steps.docs.outputs.checks }}"
    assert notes["env"]["EVIDENCE_RUN"] == "${{ steps.evidence.outputs.run }}"
    assert notes["env"]["EVIDENCE_COMMIT"] == "${{ steps.evidence.outputs.commit }}"
    assert "Release evidence: full CI run" in notes["run"]
    assert (
        names.index("Validate release tag")
        < names.index(GATE)
        < names.index("Build artifacts")
        < names.index("Generate release notes")
    )


@pytest.mark.parametrize(
    "selection,code,message",
    [
        ("", 1, "No full CI run exists"),
        ("77\tcompleted\tsuccess\tabc000\t2", 0, "Full CI run 77 passed on abc000"),
        ("77\tcompleted\tfailure\tabc000\t0", 1, "did not pass (failure)"),
        ("77\tcompleted\tcancelled\tabc000\t0", 1, "did not pass (cancelled)"),
        ("77\tunreadable\tsuccess\tabc000\t0", 1, "cannot be fetched"),
    ],
)
def test_release_gate_acts_on_the_deciding_run(tmp_path, selection, code, message):
    """The workflow passes only on a completed, successful deciding run.

    release_evidence.py (tested in test_release_evidence.py) names the
    deciding run; a shell function stands in for it here, and the real step
    text runs unchanged after it. The Compose test container mounts /tmp
    noexec, so a stand-in executable there could not run; sleep is stubbed
    so the empty case's retries are instant.
    """
    script = (
        'python() { printf "%b\\n" "$FAKE_SELECTION"; }\n'
        "sleep() { :; }\n" + release_gate()["run"]
    )
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        env={
            "PATH": os.environ["PATH"],
            "FAKE_SELECTION": selection,
            "RELEASE_COMMIT": "abc123",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == code
    assert message in result.stdout
    if code == 0:
        assert output.read_text().splitlines() == [
            "run=77",
            "commit=abc000",
            "docs=2",
        ]


def test_release_gate_waits_for_a_pending_run(tmp_path):
    """A queued or in-progress deciding run is polled until it completes."""
    counter = tmp_path / "count"
    script = (
        "python() {\n"
        f'  n=$(cat "{counter}" 2>/dev/null || echo 0); echo $((n + 1)) >"{counter}"\n'
        '  if [ "$n" -lt 2 ]; then printf "7\\tin_progress\\t-\\tabc\\t0\\n"; '
        'else printf "7\\tcompleted\\tsuccess\\tabc\\t0\\n"; fi\n'
        "}\nsleep() { :; }\n" + release_gate()["run"]
    )
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        env={
            "PATH": os.environ["PATH"],
            "RELEASE_COMMIT": "abc",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_OUTPUT": str(tmp_path / "output"),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("in_progress; waiting.") == 2


def test_build_lock_is_pinned_and_compatible_with_runtime_lock():
    """Installing runtime tools cannot replace shared build dependencies with drift."""
    build = locked_requirements("stewardship-build.txt")
    runtime = locked_requirements("stewardship.txt")
    assert {"hatchling", "editables"} <= build.keys()
    for name, requirement in build.items():
        pins = list(requirement.specifier)
        assert len(pins) == 1 and pins[0].operator == "=="
        assert "*" not in pins[0].version
        if name in runtime:
            assert requirement.specifier == runtime[name].specifier


def test_docker_and_checkout_share_build_lock():
    """The Docker install remains the same locked, non-isolated build approach."""
    dockerfile = (ROOT / "deploy/stewardship/Dockerfile").read_text()
    backend = (
        "python -m pip install --no-cache-dir -r requirements/stewardship-build.txt"
    )
    editable = (
        "python -m pip install --no-cache-dir --no-deps --no-build-isolation -e ."
    )
    assert dockerfile.index(backend) < dockerfile.index(editable)


def test_root_and_stewardship_build_exclusions_stay_synchronized():
    """Specialized ignore support cannot change the default-deny context policy."""
    fallback = (ROOT / ".dockerignore").read_text()
    specialized = (ROOT / "deploy/stewardship/Dockerfile.dockerignore").read_text()
    assert fallback == specialized
    rules = [
        line for line in fallback.splitlines() if line and not line.startswith("#")
    ]
    assert rules[0] == "**"
    assert all(line.startswith("!") for line in rules[1:])
    # Re-including a directory implicitly admits unlisted descendants, too.
    assert not any(line.endswith("/") for line in rules)
    assert "!**" not in rules and "!src/**" not in rules
    assert "!src/parishkit/stewardship/accounts/timezone_names_v1.txt" in rules
    assert "!src/**/*.txt" not in rules
    assets = tuple((ROOT / "src/parishkit/stewardship/schema").glob("*.sql"))
    assert {"functions.sql", "exports.sql"} <= {asset.name for asset in assets}
    for asset in assets:
        assert f"!{asset.relative_to(ROOT).as_posix()}" in rules
    assert "!src/**/*.sql" not in rules
    assert "!src/parishkit/stewardship/schema/*.sql" not in rules


@pytest.mark.parametrize("document", ["README.md", "docs/development/stewardship.md"])
def test_checkout_instructions_match_ci_installation(document):
    """Documented pip installs use the same ordered commands as the CI baseline."""
    text = (ROOT / document).read_text()
    assert "\n".join(INSTALL_COMMANDS) in text
    assert "python -m pip install -r requirements.txt" not in text


@pytest.mark.parametrize(
    "step_name", ["Scoped line and branch coverage", "Migration drift"]
)
def test_readme_documents_ci_validation_commands(step_name):
    """Local validation includes CI's coverage gates and test-settings drift check."""
    definition = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    step = next(
        step
        for job in definition["jobs"].values()
        for step in job["steps"]
        if step.get("name") == step_name
    )
    if step_name == "Scoped line and branch coverage":
        # CI shards and combines; locally the serial runner is the equivalent
        # full run through the same manifest and coverage floors.
        step = {
            "run": "python -m parishkit.stewardship.quality --postgresql "
            '--report "$RUNNER_TEMP/stewardship-coverage.json"'
        }
    command = step["run"].replace(
        '"$RUNNER_TEMP/stewardship-coverage.json"',
        "/absolute/temporary/path/coverage.json",
    )
    if step.get("env"):
        command = " ".join(f"{key}={value}" for key, value in step["env"].items()) + (
            " " + command
        )
    readme = (ROOT / "README.md").read_text()
    validation = readme.split("### Local validation (matching CI)\n", 1)[1].split(
        "\n### ", 1
    )[0]
    assert command in validation
    assert "docs/development/stewardship-compose.md#validation" in validation
