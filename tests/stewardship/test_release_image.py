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
    assert job["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    for name, other in definition["jobs"].items():
        if name != "publish-image":
            for scope in ("packages", "id-token", "attestations"):
                assert scope not in other.get("permissions", {}), (name, scope)
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["persist-credentials"] is False
    steps = steps_of(job)
    naming = steps["Name the image"]["run"]
    assert "tr '[:upper:]' '[:lower:]'" in naming
    assert "image=ghcr.io/${repository}/stewardship" in naming
    assert "version=${GITHUB_REF_NAME#v}" in naming
    # An annotated tag's GITHUB_SHA may be the tag object: the commit tag is
    # the commit the tag points to, resolved as the validation job does.
    assert 'commit=$(git rev-list -n 1 "${GITHUB_REF_NAME}")' in naming
    build = steps["Build the application image"]
    assert "--file deploy/stewardship/Dockerfile" in build["run"]
    push = steps["Push the image and record its digest"]
    for step in (build, push):
        assert step["env"]["COMMIT"] == "${{ steps.name.outputs.commit }}"
        assert '"${IMAGE}:${COMMIT}"' in step["run"]
        assert "GITHUB_SHA" not in step["run"]
    login = steps["Log in to GHCR"]
    assert login["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert "--password-stdin" in login["run"]
    assert "RepoDigests" in push["run"] and "image-digest.txt" in push["run"]
    upload = steps["Upload the image digest"]["with"]
    assert upload == {
        "name": "image-digest",
        "path": "image-digest.txt",
        "if-no-files-found": "error",
    }


def test_pushed_digest_is_smoke_tested_and_attested_before_it_is_evidence():
    """The pushed image must start and carry provenance before any release names it.

    #392 L4 and M2: the smoke run and the attestation both act on the digest
    the push step recorded, and both precede the digest artifact the publish
    job turns into the GitHub Release.
    """
    job = release()["jobs"]["publish-image"]
    names = [step.get("name") for step in job["steps"]]
    steps = steps_of(job)
    push = steps["Push the image and record its digest"]
    assert push["id"] == "push"
    # release.sh reads the digest from this line; keep it as it was.
    assert "printf '\\nApplication image: `%s`\\n' \"${digest}\"" in push["run"]
    assert 'echo "reference=${digest}" >> "$GITHUB_OUTPUT"' in push["run"]
    assert 'echo "digest=${digest#*@}" >> "$GITHUB_OUTPUT"' in push["run"]

    smoke = steps["Smoke-test the pushed image"]
    assert smoke["env"]["REFERENCE"] == "${{ steps.push.outputs.reference }}"
    assert 0 < smoke["timeout-minutes"] <= 5
    run = smoke["run"]
    # Pulled back by digest and run as the runbook runs offline commands.
    assert 'docker pull --quiet "${REFERENCE}"' in run
    for flag in (
        "--network none",
        "--user 10001:10001",
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges:true",
    ):
        assert flag in run
    assert 'test "${reported}" = "pk-stewardship ${VERSION}"' in run
    assert "collect-static --destination /smoke-static" in run
    assert "uid=10001,gid=10001" in run
    assert "--entrypoint pg_dump" in run and '"pg_dump (PostgreSQL) 18."*' in run
    assert "IMAGE" not in run and ":${VERSION}" not in run

    attest = steps["Attest the image's build provenance"]
    assert attest["uses"].startswith("actions/attest-build-provenance@")
    assert attest["with"] == {
        "subject-name": "${{ steps.name.outputs.image }}",
        "subject-digest": "${{ steps.push.outputs.digest }}",
        # Kept in GitHub's store: no second @sha256: reference in the log.
        "push-to-registry": False,
    }
    order = [
        names.index("Push the image and record its digest"),
        names.index("Smoke-test the pushed image"),
        names.index("Attest the image's build provenance"),
        names.index("Upload the image digest"),
    ]
    assert order == sorted(order)


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
    step = steps_of(publish)["Publish GitHub release"]
    # No checkout in this job: gh needs the repository named explicitly.
    assert step["env"]["GH_REPO"] == "${{ github.repository }}"
    notes = step["run"]
    assert "cat image-digest.txt >> release-notes.md" in notes
    assert notes.index("image-digest.txt") < notes.index("gh release")
    # The reference the workflow prints is the one the production renderer admits.
    example = "ghcr.io/epiphany40223/parishkit/stewardship@sha256:" + "a" * 64
    assert _image(example, DeploymentProfile.PRODUCTION) == example
