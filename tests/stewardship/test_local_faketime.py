"""The LOCAL fake-clock topology (#476, OPS-10.07).

The fake-clock Compose override is rendered only for LOCAL and only as a
separate document: it sets libfaketime's preload on every application
service, ``postgres`` and ``valkey``, adds exactly one read-only mount (the
clock directory), is absent from normal mode and from every non-LOCAL
rendering, and the derived image tags follow the specification's rule. The
mount policy admits the clock mount read-only for LOCAL alone. The Production
golden files (test_local_profile) prove Production's documents are unchanged;
here the renderings are also searched for any libfaketime trace.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_provisioning as provisioning
from parishkit.stewardship.accounts.key_files import read_private
from parishkit.stewardship.deployment import (
    DeploymentProfile,
    ServiceRole,
    load_deployment,
)
from parishkit.stewardship.local.clock import (
    CLOCK_MOUNT_TARGET,
    FAKETIME_ENVIRONMENT,
    FAKETIME_LIBRARY,
    clock_directory,
    faketime_image,
)
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import (
    DEVELOPMENT_IMAGE,
    POSTGRES_IMAGE,
    VALKEY_IMAGE,
    _image,
    render_faketime_override,
    render_runtime,
)
from parishkit.stewardship.service_boundaries import Mount, validate_mounts

from .test_local_profile import (
    COMMIT,
    FIXTURES,
    GOLDEN_ROOT,
    LOCAL_DIRTY_IMAGE,
    LOCAL_IMAGE,
    PRODUCTION_IMAGE,
    PROFILES,
    canonical,
    configuration_for,
)
from .test_service_boundaries import configured

ROOT = Path(__file__).resolve().parents[2]
IMAGES = {
    DeploymentProfile.PRODUCTION: PRODUCTION_IMAGE,
    DeploymentProfile.LOCAL: LOCAL_IMAGE,
}
FAKETIME_WORDS = ("faketime", "LD_PRELOAD", "parishkit-clock")
POSTGRES_DIGEST = POSTGRES_IMAGE.split("@sha256:")[1]
VALKEY_DIGEST = VALKEY_IMAGE.split("@sha256:")[1]
PREFIX = "parishkit-stewardship-local-faketime-"


def rendering(profile, root=GOLDEN_ROOT, **options):
    """A profile's Compose document and service documents."""
    configuration = configuration_for(profile, root)
    compose, documents = render_runtime(
        configuration, image=IMAGES.get(profile, DEVELOPMENT_IMAGE), **options
    )
    return configuration, compose, documents


# Derived image tags.
@pytest.mark.parametrize(
    ("base", "derived"),
    [
        (LOCAL_IMAGE, f"{PREFIX}stewardship:{COMMIT}-1700000000"),
        (LOCAL_DIRTY_IMAGE, f"{PREFIX}stewardship:{COMMIT}-dirty-1700000000"),
        (POSTGRES_IMAGE, f"{PREFIX}postgres:{POSTGRES_DIGEST}"),
        (VALKEY_IMAGE, f"{PREFIX}valkey:{VALKEY_DIGEST}"),
    ],
)
def test_derived_image_tags_follow_the_specification(base, derived):
    """``…-faketime-<base>:<base digest or local tag>``, and already derived stays."""
    assert faketime_image(base) == derived
    assert faketime_image(derived) == derived
    name, tag = derived.split(":")
    assert len(tag) <= 128 and "/" not in name


@pytest.mark.parametrize("base", ["", None, "postgres", "ghcr.io/x/y:1", "a/b:c"])
def test_derived_image_tags_refuse_other_references(base):
    """Only a digest-pinned image or a bare local tag can be derived from."""
    with pytest.raises(ConfigError):
        faketime_image(base)


def test_local_admits_the_derived_application_tag_and_others_refuse_it():
    """LOCAL can be re-rendered from the derived tag; no other profile admits it."""
    derived = faketime_image(LOCAL_IMAGE)
    assert _image(derived, DeploymentProfile.LOCAL) == derived
    assert _image(faketime_image(LOCAL_DIRTY_IMAGE), DeploymentProfile.LOCAL)
    for profile in PROFILES:
        if profile is not DeploymentProfile.LOCAL:
            with pytest.raises(ConfigError):
                _image(derived, profile)
    # The derived store tags are never an application image.
    for base in (POSTGRES_IMAGE, VALKEY_IMAGE):
        with pytest.raises(ConfigError):
            _image(faketime_image(base), DeploymentProfile.LOCAL)


# The override.
@pytest.mark.parametrize("mode", ["initial", "configured", "configured-slack"])
def test_override_fakes_only_application_services_and_the_two_stores(tmp_path, mode):
    """Preload on every application container, postgres and valkey; nothing else."""
    configuration, compose, _ = rendering(
        DeploymentProfile.LOCAL, tmp_path, provider_mode=mode
    )
    override = render_faketime_override(configuration, compose)
    assert set(override) == {"services"}
    application = {
        name
        for name, service in compose["services"].items()
        if service["image"] == LOCAL_IMAGE
    }
    assert application >= {"web", "worker", "scheduler", "mail-dispatch", "migration"}
    assert "fake-parishsoft" in application and "mailpit" in compose["services"]
    assert set(override["services"]) == (application - {"fake-parishsoft"}) | {
        "postgres",
        "valkey",
    }
    for name in ("caddy", "mailpit", "fake-parishsoft"):
        assert name not in override["services"]
    expected_mount = {
        "type": "bind",
        "source": str(clock_directory(configuration)),
        "target": str(CLOCK_MOUNT_TARGET),
        "read_only": True,
        "bind": {"create_host_path": False},
    }
    assert str(clock_directory(configuration)).endswith("/run/local/clock")
    for name, service in override["services"].items():
        assert set(service) == {"image", "environment", "volumes"}, name
        assert service["environment"] == FAKETIME_ENVIRONMENT
        assert service["volumes"] == [expected_mount]
        assert service["image"] == faketime_image(compose["services"][name]["image"])
        assert service["image"].startswith("parishkit-stewardship-local-faketime-")
    assert override["services"]["postgres"]["image"].endswith(":" + POSTGRES_DIGEST)
    assert override["services"]["valkey"]["image"].endswith(":" + VALKEY_DIGEST)
    assert FAKETIME_ENVIRONMENT["LD_PRELOAD"] == FAKETIME_LIBRARY
    assert FAKETIME_ENVIRONMENT["FAKETIME_TIMESTAMP_FILE"] == (
        "/run/parishkit-clock/offset"
    )
    assert FAKETIME_ENVIRONMENT["FAKETIME_CACHE_DURATION"] == "1"
    assert FAKETIME_ENVIRONMENT["FAKETIME_DONT_FAKE_MONOTONIC"] == "1"


def test_override_matches_the_golden_file():
    """Canonical-JSON identical to the committed fixture (see test_local_topology).

    Regenerate only for an intentional change, with ``PYTHONPATH=src:tests``::

        from stewardship.test_local_faketime import override_at
        from stewardship.test_local_profile import FIXTURES, GOLDEN_ROOT, canonical
        (FIXTURES / "local-compose-faketime.golden.json").write_text(
            canonical(override_at(GOLDEN_ROOT)))
    """
    golden = (FIXTURES / "local-compose-faketime.golden.json").read_text()
    assert canonical(override_at(GOLDEN_ROOT)) == golden


def override_at(root):
    """The LOCAL fake-clock override rendered at a runtime root."""
    configuration, compose, _ = rendering(DeploymentProfile.LOCAL, root)
    return render_faketime_override(configuration, compose)


@pytest.mark.parametrize("profile", PROFILES)
def test_normal_mode_and_every_profile_render_without_libfaketime(profile):
    """The base topology and every service document carry no trace of the clock.

    Normal mode is the base topology alone, so LOCAL's own compose.json has no
    preload either; and no other profile can render the override at all.
    """
    configuration, compose, documents = rendering(profile)
    text = canonical(compose) + "".join(
        value if isinstance(value, str) else json.dumps(value)
        for value in documents.values()
    )
    for word in FAKETIME_WORDS:
        assert word not in text, (profile, word)
    if profile is DeploymentProfile.LOCAL:
        assert render_faketime_override(configuration, compose)["services"]
    else:
        with pytest.raises(ConfigError, match="only for the local profile"):
            render_faketime_override(configuration, compose)


def test_production_dockerfile_is_untouched_and_the_layer_is_separate():
    """libfaketime comes only from Dockerfile.faketime, FROM a base image argument."""
    production = (ROOT / "deploy/stewardship/Dockerfile").read_text()
    for word in FAKETIME_WORDS:
        assert word not in production
    layer = (ROOT / "deploy/stewardship/Dockerfile.faketime").read_text()
    assert "ARG BASE\nFROM ${BASE}\n" in layer
    assert "apt-get install -y --no-install-recommends faketime" in layer
    assert FAKETIME_LIBRARY in layer
    # The loader-level preload survives PostgreSQL's entrypoint, which replaces
    # LD_PRELOAD with its nss_wrapper for a uid without a passwd entry.
    assert f"echo {FAKETIME_LIBRARY} > /etc/ld.so.preload" in layer
    assert layer.rstrip().endswith("USER 10001:10001")


# Provisioning.
def test_provisioning_writes_the_override_only_for_local(tmp_path):
    """LOCAL roots get compose.faketime.json beside the three topologies; others not."""
    local_root = tmp_path / "local"
    configuration = load_deployment(
        environ={
            "PARISHKIT_ROOT": str(local_root),
            "PARISHKIT_STEWARDSHIP_PROFILE": "local",
        }
    )
    assert configuration.profile is DeploymentProfile.LOCAL
    provisioning.provision_runtime(configuration, image=LOCAL_IMAGE)
    layout = RuntimeLayout(configuration)
    override_path = layout.service_directory / "compose.faketime.json"
    written = json.loads(read_private(override_path))
    assert written == override_at(local_root)
    compose = json.loads(read_private(layout.service_directory / "compose.json"))
    assert set(written["services"]) <= set(compose["services"])
    assert "LD_PRELOAD" not in json.dumps(compose)
    # The directory the override mounts is not created by provisioning: it is
    # the operator script's, written beside the fake ParishSoft configuration.
    assert not clock_directory(configuration).exists()

    development_root = tmp_path / "development"
    development = load_deployment(environ={"PARISHKIT_ROOT": str(development_root)})
    provisioning.provision_runtime(development, image=DEVELOPMENT_IMAGE)
    service_directory = RuntimeLayout(development).service_directory
    assert not (service_directory / "compose.faketime.json").exists()
    assert sorted(path.name for path in service_directory.glob("compose*.json")) == [
        "compose-initial.json",
        "compose-slack.json",
        "compose.json",
    ]


# The mount policy.
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize(
    "role", [ServiceRole.WEB, ServiceRole.WORKER, ServiceRole.SCHEDULER]
)
def test_clock_mount_is_admitted_read_only_for_local_alone(profile, role):
    """Exactly the target, read-only, only in LOCAL; writable or elsewhere refused."""
    config, mounts = configured(role)
    config = replace(config, profile=profile)
    assert validate_mounts(config, mounts) is role
    read_only = [*mounts, Mount(CLOCK_MOUNT_TARGET, True)]
    if profile is DeploymentProfile.LOCAL:
        assert validate_mounts(config, read_only) is role
    else:
        with pytest.raises(ConfigError, match="unrecognized mount"):
            validate_mounts(config, read_only)
    with pytest.raises(ConfigError, match="unrecognized mount"):
        validate_mounts(config, [*mounts, Mount(CLOCK_MOUNT_TARGET, False)])
    with pytest.raises(ConfigError, match="unrecognized mount"):
        validate_mounts(config, [*mounts, Mount(CLOCK_MOUNT_TARGET / "offset", True)])
    with pytest.raises(ConfigError, match="unrecognized mount"):
        validate_mounts(config, [*mounts, Mount(Path("/run/parishkit-clock2"), True)])


@pytest.mark.parametrize("profile", PROFILES)
def test_clock_mount_is_admitted_offline_read_only_for_local_alone(tmp_path, profile):
    """The one-shot profiles (database-roles, bootstrap, migrate) run faked in LOCAL."""
    from parishkit.stewardship.offline_boundaries import validate_offline_mounts

    from .test_offline_boundaries import configuration as offline_configuration

    config, mounts = offline_configuration(tmp_path, ServiceRole.MIGRATION)
    config = replace(config, profile=profile)
    read_only = [*mounts, Mount(CLOCK_MOUNT_TARGET, True)]
    if profile is DeploymentProfile.LOCAL:
        assert validate_offline_mounts(config, read_only) is ServiceRole.MIGRATION
    else:
        with pytest.raises(ConfigError, match="unrelated mount"):
            validate_offline_mounts(config, read_only)
    with pytest.raises(ConfigError, match="unrelated mount"):
        validate_offline_mounts(config, [*mounts, Mount(CLOCK_MOUNT_TARGET, False)])


@pytest.mark.parametrize("profile", PROFILES)
def test_clock_mount_is_admitted_for_backup_read_only_for_local_alone(
    tmp_path, profile
):
    """The backup worker is an application service too and is faked in LOCAL."""
    from parishkit.stewardship import backup_boundaries

    from .test_backup_boundaries import backup_configuration, mounts

    config = replace(backup_configuration(tmp_path), profile=profile)
    targets = backup_boundaries.backup_targets(config)
    read_only = Mount(CLOCK_MOUNT_TARGET, True)
    if profile is DeploymentProfile.LOCAL:
        assert (
            backup_boundaries.validate_backup_mounts(config, mounts(targets, read_only))
            is ServiceRole.BACKUP_WORKER
        )
    else:
        with pytest.raises(ConfigError, match="unrelated mount"):
            backup_boundaries.validate_backup_mounts(config, mounts(targets, read_only))
    writable = Mount(CLOCK_MOUNT_TARGET, False)
    with pytest.raises(ConfigError, match="unrelated mount"):
        backup_boundaries.validate_backup_mounts(config, mounts(targets, writable))
