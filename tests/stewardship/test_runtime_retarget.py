"""A provisioned deployment changes image, and only image, through one command."""

import json
from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_provisioning as provisioning
from parishkit.stewardship import runtime_retarget as retarget
from parishkit.stewardship.accounts.key_files import read_private, write_private
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import load_deployment
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


def images(configuration):
    """Every image named by the three rendered topologies."""
    layout = RuntimeLayout(configuration)
    found = set()
    for name in retarget.TOPOLOGIES:
        compose = json.loads(read_private(layout.service_directory / name))
        found |= {
            service["image"]
            for service in compose["services"].values()
            if service["image"].startswith("parishkit-stewardship")
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
        "services_started": False,
    }
    assert images(deployment) == {NEW}
    assert record(deployment)["image"] == NEW
    assert read_private(layout.service_directory / "web.yaml") == web
    assert read_private(layout.database_password("web")) == password
    assert retarget.retarget_image(deployment, image=NEW)["image_changed"] is False
    # Provisioning itself still refuses a completed root.
    with pytest.raises(ConfigError, match="already provisioned"):
        provisioning.provision_runtime(deployment, image=NEW)


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


def test_any_other_change_is_refused_and_nothing_is_written(deployment):
    """Changed inputs, edited documents and online services all refuse."""
    layout = RuntimeLayout(deployment)
    before = read_private(layout.service_directory / "compose.json")
    with pytest.raises(ConfigError, match="only the image may change"):
        retarget.retarget_image(
            replace(deployment, public_origin="http://localhost:9999"), image=NEW
        )
    web = layout.service_directory / "web.yaml"
    original = read_private(web)
    write_private(web, b"edited by hand")
    with pytest.raises(ConfigError, match="not an upgrade"):
        retarget.retarget_image(deployment, image=NEW)
    write_private(web, original)
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


def test_retarget_cli_never_echoes_private_error_or_input(capsys):
    """No default target, traceback or raw configuration error reaches the console."""
    assert main(["retarget-image", "--config", "private-input"]) == 2
    assert main(["retarget-image", "--image", "private-image"]) == 2
    captured = capsys.readouterr()
    assert "private-i" not in captured.out + captured.err
    assert captured.err.count("image retarget refused") == 2
