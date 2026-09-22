"""The release tag publishes the immutable image the runtime admits, and only then."""

from pathlib import Path

import yaml

from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.runtime_topology import _image

ROOT = Path(__file__).resolve().parents[2]


def release():
    """The release workflow as CI reads it."""
    return yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())


def steps_of(job):
    """The job's named steps."""
    return {step.get("name"): step for step in job["steps"]}


def test_image_job_runs_after_validation_with_only_package_scope():
    """No image leaves the workflow before the full validation of the tagged commit."""
    definition = release()
    # PyYAML reads the bare `on:` key as the boolean true.
    assert definition[True]["push"]["tags"] == ["v*"]
    job = definition["jobs"]["publish-image"]
    assert job["needs"] == "validate-build"
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    for name, other in definition["jobs"].items():
        if name != "publish-image":
            assert "packages" not in other.get("permissions", {}), name
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["persist-credentials"] is False
    steps = steps_of(job)
    naming = steps["Name the image"]["run"]
    assert "tr '[:upper:]' '[:lower:]'" in naming
    assert "image=ghcr.io/${repository}/parishkit" in naming
    assert "version=${GITHUB_REF_NAME#v}" in naming
    build = steps["Build the application image"]["run"]
    assert "--file deploy/stewardship/Dockerfile" in build
    login = steps["Log in to GHCR"]
    assert login["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert "--password-stdin" in login["run"]
    push = steps["Push the image and record its digest"]["run"]
    assert "RepoDigests" in push and "image-digest.txt" in push
    upload = steps["Upload the image digest"]["with"]
    assert upload == {
        "name": "image-digest",
        "path": "image-digest.txt",
        "if-no-files-found": "error",
    }


def test_release_publication_carries_the_digest_the_runtime_admits():
    """The release notes name the exact reference the deployment YAML must use."""
    definition = release()
    publish = definition["jobs"]["publish"]
    assert set(publish["needs"]) == {"validate-build", "publish-image"}
    downloaded = {
        step["with"]["name"]
        for step in publish["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    }
    assert "image-digest" in downloaded
    notes = steps_of(publish)["Publish GitHub release"]["run"]
    assert "cat image-digest.txt >> release-notes.md" in notes
    assert notes.index("image-digest.txt") < notes.index("gh release")
    # The reference the workflow prints is the one the production renderer admits.
    example = "ghcr.io/epiphany40223/parishkit/parishkit@sha256:" + "a" * 64
    assert _image(example, DeploymentProfile.PRODUCTION) == example
