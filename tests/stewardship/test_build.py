"""Credential-free contracts for matching host, CI, and image build tools."""

import os
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
        ("ci.yml", "stewardship-compose"),
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
    assert install["run"].strip().splitlines() == INSTALL_COMMANDS


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


@pytest.mark.parametrize(
    "step_name", ["Scoped line and branch coverage", "Migration drift"]
)
def test_release_requires_ci_quality_gates_before_build(step_name):
    """Require unmodified CI quality checks before building release artifacts."""
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    expected = next(
        step
        for job in ci["jobs"].values()
        for step in job["steps"]
        if step.get("name") == step_name
    )
    steps = release["jobs"]["validate-build"]["steps"]
    matches = [step for step in steps if step.get("name") == step_name]
    # Compare the complete step, including environment and failure/skip policy.
    assert matches == [expected]
    build = next(step for step in steps if step.get("name") == "Build artifacts")
    assert steps.index(matches[0]) < steps.index(build)


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
