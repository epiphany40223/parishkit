"""A provisioned deployment changes image, and only image, through one command."""

import json
from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_provisioning as provisioning
from parishkit.stewardship import runtime_retarget as retarget
from parishkit.stewardship.accounts.key_files import read_private, write_private
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import DeploymentProfile, load_deployment
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.startup_interlock import StartupLease

# The renderer admits only the development image for a development profile,
# so the image swap is exercised through the one function that picks it.
OLD = "parishkit-stewardship:development"
NEW = "parishkit-stewardship:development-next"


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    """A provisioned development root whose renderer admits two test images."""
    real = provisioning.render_runtime.__globals__["_image"]

    def either(value, profile):
        """Admit the second test image as the renderer admits the first."""
        return value if value in {OLD, NEW} else real(value, profile)

    monkeypatch.setitem(provisioning.render_runtime.__globals__, "_image", either)
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path / "rt")})
    provisioning.provision_runtime(configuration, image=OLD)
    return configuration


def images(configuration, prefix="parishkit-stewardship"):
    """Every application image named by the three rendered topologies."""
    layout = RuntimeLayout(configuration)
    found = set()
    for name in retarget.TOPOLOGIES:
        compose = json.loads(read_private(layout.service_directory / name))
        found |= {
            service["image"]
            for service in compose["services"].values()
            if service["image"].startswith(prefix)
        }
    return found


def record(configuration):
    """The completed provisioning record."""
    return json.loads(
        read_private(configuration.paths.root / ".stewardship-provisioned.json")
    )


def test_only_the_image_changes_and_a_repeat_changes_nothing(deployment):
    """Topologies and record move to the new image; documents and passwords stay."""
    layout = RuntimeLayout(deployment)
    web = read_private(layout.service_directory / "web.yaml")
    password = read_private(layout.database_password("web"))
    assert images(deployment) == {OLD}
    assert retarget.retarget_image(deployment, image=NEW) == {
        "image_changed": True,
        "documents_changed": 3,
        "services_started": False,
    }
    assert images(deployment) == {NEW}
    assert record(deployment)["image"] == NEW
    assert read_private(layout.service_directory / "web.yaml") == web
    assert read_private(layout.database_password("web")) == password
    assert retarget.retarget_image(deployment, image=NEW) == {
        "image_changed": False,
        "documents_changed": 0,
        "services_started": False,
    }
    # Provisioning itself still refuses a completed root.
    with pytest.raises(ConfigError, match="already provisioned"):
        provisioning.provision_runtime(deployment, image=NEW)


def test_a_production_deployment_moves_between_digests_only(tmp_path):
    """The real production admission: digests move, the ingress document stays."""
    digests = ["ghcr.io/example/parishkit/parishkit@sha256:" + c * 64 for c in "ab"]
    configuration = replace(
        load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path / "rt")}),
        profile=DeploymentProfile.PRODUCTION,
        public_origin="https://parish.example",
        trusted_proxy_hops=1,
    )
    provisioning.provision_runtime(configuration, image=digests[0])
    layout = RuntimeLayout(configuration)
    caddyfile = read_private(layout.service_directory / "Caddyfile")
    web = read_private(layout.service_directory / "web.yaml")
    assert images(configuration, "ghcr.io/") == {digests[0]}
    assert retarget.retarget_image(configuration, image=digests[1]) == {
        "image_changed": True,
        "documents_changed": 3,
        "services_started": False,
    }
    assert images(configuration, "ghcr.io/") == {digests[1]}
    assert record(configuration)["image"] == digests[1]
    assert read_private(layout.service_directory / "Caddyfile") == caddyfile
    assert read_private(layout.service_directory / "web.yaml") == web
    compose = json.loads(read_private(layout.service_directory / "compose.json"))
    assert compose["services"]["caddy"]["restart"] == "unless-stopped"
    assert compose["services"]["web"]["image"] == digests[1]
    # Only a digest of the repository's own image is admitted in production.
    for refused in ("ghcr.io/example/parishkit/parishkit:1.2.3", OLD):
        with pytest.raises(ConfigError):
            retarget.retarget_image(configuration, image=refused)
    assert images(configuration, "ghcr.io/") == {digests[1]}


def test_an_interrupted_retarget_is_finished_by_repeating_it(deployment, monkeypatch):
    """Topologies written, record not: the same command completes the change."""
    original = retarget.write_private

    def stop_at_record(path, value, **options):
        """Fail when the record, written last, is about to change."""
        if path.name == ".stewardship-provisioned.json":
            raise OSError("interrupted")
        return original(path, value, **options)

    with monkeypatch.context() as patched:
        patched.setattr(retarget, "write_private", stop_at_record)
        with pytest.raises(OSError):
            retarget.retarget_image(deployment, image=NEW)
    assert images(deployment) == {NEW} and record(deployment)["image"] == OLD
    assert retarget.retarget_image(deployment, image=NEW)["image_changed"] is True
    assert record(deployment)["image"] == NEW


def test_an_interrupted_retarget_is_undone_by_the_recorded_image(
    deployment, monkeypatch
):
    """Topologies written, record not: the recorded image puts them back."""
    with monkeypatch.context() as patched:
        patched.setattr(
            retarget, "write_private", lambda *a, **k: (_ for _ in ()).throw(OSError())
        )
        with pytest.raises(OSError):
            retarget.retarget_image(deployment, image=NEW)
    # Nothing was written, so write the topologies by hand as the interrupted
    # command would have, leaving the record on the old image.
    layout = RuntimeLayout(deployment)
    documents = retarget._plan(deployment, record(deployment), image=NEW)[3]
    for name in retarget.TOPOLOGIES:
        path = layout.service_directory / name
        write_private(path, documents[path])
    assert images(deployment) == {NEW} and record(deployment)["image"] == OLD
    assert retarget.retarget_image(deployment, image=OLD) == {
        "image_changed": False,
        "documents_changed": 3,
        "services_started": False,
    }
    assert images(deployment) == {OLD} and record(deployment)["image"] == OLD


def test_generated_documents_are_re_rendered_by_the_running_code(deployment):
    """A document that differs from the current rendering is rewritten, not kept.

    This is how a release whose renderer changed a per-service document or
    the ingress document reaches a deployment; generated documents are never
    edited by hand, so a stray edit is simply replaced too.
    """
    layout = RuntimeLayout(deployment)
    web = layout.service_directory / "web.yaml"
    original = read_private(web)
    write_private(web, b"rendered by an older release")
    compose = layout.service_directory / "compose.json"
    write_private(compose, b'{"services": {}}')
    # A document this release renders that the provisioning release did not.
    added = layout.service_directory / "backup-worker.yaml"
    kept = read_private(added)
    added.unlink()
    assert retarget.retarget_image(deployment, image=OLD) == {
        "image_changed": False,
        "documents_changed": 3,
        "services_started": False,
    }
    assert read_private(web) == original
    assert read_private(added) == kept
    assert images(deployment) == {OLD} and record(deployment)["image"] == OLD


def test_any_other_change_is_refused_and_nothing_is_written(deployment):
    """Changed inputs, missing passwords, bad images and online services refuse."""
    layout = RuntimeLayout(deployment)
    before = read_private(layout.service_directory / "compose.json")
    with pytest.raises(ConfigError, match="only the image may change"):
        retarget.retarget_image(
            replace(deployment, public_origin="http://localhost:9999"), image=NEW
        )
    password = layout.database_password("web")
    kept = read_private(password)
    password.unlink()
    # A generated password is never re-created: its absence is a refusal.
    with pytest.raises(ValueError):
        retarget.retarget_image(deployment, image=NEW)
    write_private(password, kept)
    with pytest.raises(ConfigError):
        retarget.retarget_image(deployment, image="ghcr.io/else/where:latest")
    # An online service holds the interlock shared: no topology is replaced.
    with StartupLease(layout.interlock, offline=False), pytest.raises(ConfigError):
        retarget.retarget_image(deployment, image=NEW)
    assert read_private(layout.service_directory / "compose.json") == before
    assert record(deployment)["image"] == OLD


def test_an_unfinished_provisioning_is_not_upgraded(tmp_path, monkeypatch):
    """Resume provisioning first; an upgrade never completes a fresh root."""
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path / "rt")})

    def interrupt(path, value):
        """Stop provisioning before its completion record is written."""
        raise OSError("interrupted")

    with monkeypatch.context() as patched:
        patched.setattr(provisioning, "_retain", interrupt)
        with pytest.raises(OSError):
            provisioning.provision_runtime(configuration, image=OLD)
    assert (configuration.paths.root / ".stewardship-provisioning.json").exists()
    with pytest.raises(ConfigError, match="unfinished"):
        retarget.retarget_image(configuration, image=OLD)
    # A root that was never provisioned at all is refused too.
    missing = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path / "none")})
    with pytest.raises(ConfigError):
        retarget.retarget_image(missing, image=OLD)


def test_retarget_cli_reports_the_change_and_never_echoes_private_input(
    deployment, tmp_path, monkeypatch, capsys
):
    """The console gets the JSON result or the generic refusal, nothing else."""
    monkeypatch.setenv("PARISHKIT_ROOT", str(tmp_path / "rt"))
    config = tmp_path / "operator.yaml"
    config.write_text("deployment: {schema_version: 1}\n", encoding="utf-8")
    assert main(["retarget-image", "--config", str(config), "--image", NEW]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "image_changed": True,
        "documents_changed": 3,
        "services_started": False,
    }
    assert images(deployment) == {NEW}
    # A missing option, an unreadable configuration, a configuration whose
    # raw error would name its contents and a refused image all get the same
    # generic message.
    assert main(["retarget-image", "--config", str(config)]) == 2
    assert main(["retarget-image", "--image", "private-image"]) == 2
    assert main(["retarget-image", "--config", "private-input", "--image", NEW]) == 2
    config.write_text("deployment: {profile: private-profile}\n", encoding="utf-8")
    assert main(["retarget-image", "--config", str(config), "--image", NEW]) == 2
    config.write_text("deployment: {schema_version: 1}\n", encoding="utf-8")
    assert main(["retarget-image", "--config", str(config), "--image", "x:y"]) == 2
    captured = capsys.readouterr()
    assert "private-" not in captured.out + captured.err
    assert not captured.out
    assert captured.err.count("image retarget refused") == 5
    assert images(deployment) == {NEW}
