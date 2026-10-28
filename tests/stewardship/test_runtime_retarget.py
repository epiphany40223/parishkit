"""A provisioned deployment changes image, and only image, through one command."""

import json
import os
from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_provisioning as provisioning
from parishkit.stewardship import runtime_retarget as retarget
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.key_files import read_private, write_private
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import DeploymentProfile, load_deployment
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.startup_interlock import StartupLease

# The renderer admits only the development image for a development profile,
# so the image swap is exercised through the one function that picks it.
OLD = "parishkit-stewardship:development"
NEW = "parishkit-stewardship:development-next"
# The refusal a missing password file gets from read_private, so an
# unrelated refusal cannot pass for it.
UNAVAILABLE = "Credential file is unavailable"


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
    digests = ["ghcr.io/example/parishkit/stewardship@sha256:" + c * 64 for c in "ab"]
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
    # The image was renamed from parishkit to stewardship; the old name is refused.
    old_name = "ghcr.io/example/parishkit/parishkit@sha256:" + "c" * 64
    for refused in ("ghcr.io/example/parishkit/stewardship:1.2.3", old_name, OLD):
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


def test_a_record_from_a_release_without_a_defaulted_path_still_matches(
    deployment, monkeypatch
):
    """A new default in the deployment document is not an operator change.

    Retarget runs with a read-only root and no writable /tmp, so the
    re-derivation must not need a temporary file.
    """
    import tempfile

    def no_temporary_directory(*args, **kwargs):
        raise FileNotFoundError("No usable temporary directory found")

    # Writes into the private runtime root stay possible; only the default
    # temporary directory is gone, as in the documented container.
    monkeypatch.setattr(tempfile, "gettempdir", no_temporary_directory)
    marker = deployment.paths.root / ".stewardship-provisioned.json"
    recorded = record(deployment)
    # The provisioning release knew no backups path; drop it from the record.
    del recorded["deployment"]["deployment"]["paths"]["backups"]
    write_private(marker, json.dumps(recorded).encode())
    assert retarget.retarget_image(deployment, image=NEW)["image_changed"] is True
    assert record(deployment)["deployment"]["deployment"]["paths"]["backups"] == str(
        deployment.paths["backups"]
    )
    # A real input change is still refused.
    recorded = record(deployment)
    recorded["deployment"]["deployment"]["public_origin"] = "http://localhost:9999"
    write_private(marker, json.dumps(recorded).encode())
    with pytest.raises(ConfigError, match="only the image may change"):
        retarget.retarget_image(deployment, image=NEW)


def test_the_export_download_window_can_change_after_provisioning(deployment):
    """The window is an operational switch, not a provisioning input (#1398):
    a retarget with another value is accepted and renders it, so a shorter
    window applies at once; the default renders nothing."""
    layout = RuntimeLayout(deployment)
    shorter = replace(deployment, export_download_window_minutes=30)
    retarget.retarget_image(shorter, image=NEW)
    worker = json.loads(read_private(layout.service_directory / "worker.yaml"))
    assert worker["deployment"]["export_download_window_minutes"] == 30
    retarget.retarget_image(deployment, image=NEW)
    worker = json.loads(read_private(layout.service_directory / "worker.yaml"))
    assert "export_download_window_minutes" not in worker["deployment"]


def test_any_other_change_is_refused_and_nothing_is_written(deployment):
    """Changed inputs, missing passwords, bad images and online services refuse."""
    layout = RuntimeLayout(deployment)
    before = read_private(layout.service_directory / "compose.json")
    with pytest.raises(ConfigError, match="only the image may change"):
        retarget.retarget_image(
            replace(deployment, public_origin="http://localhost:9999"), image=NEW
        )
    # The alert policy reaches services only through the rendered documents,
    # so a later change to it is a changed input too.
    from parishkit.stewardship.jobs.operational_policy import IncidentPolicy

    with pytest.raises(ConfigError, match="only the image may change"):
        retarget.retarget_image(
            replace(
                deployment,
                operational_alerts=IncidentPolicy(suppression_seconds=120),
            ),
            image=NEW,
        )
    password = layout.database_password("web")
    kept = read_private(password)
    password.unlink()
    # A generated password is never re-created: its absence is a refusal.
    with pytest.raises(CryptographicError, match=UNAVAILABLE):
        retarget.retarget_image(deployment, image=NEW)
    write_private(password, kept)
    with pytest.raises(ConfigError):
        retarget.retarget_image(deployment, image="ghcr.io/else/where:latest")
    # An online service holds the interlock shared: no topology is replaced.
    with StartupLease(layout.interlock, offline=False), pytest.raises(ConfigError):
        retarget.retarget_image(deployment, image=NEW)
    assert read_private(layout.service_directory / "compose.json") == before
    assert record(deployment)["image"] == OLD


def test_a_parsed_document_loads_like_a_file_and_never_with_one(tmp_path):
    """The in-memory form validates the same way and refuses a second source."""
    from parishkit.stewardship.deployment_documents import deployment_document

    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path / "rt")})
    document = deployment_document(configuration)
    loaded = load_deployment(document=document, environ={})
    assert deployment_document(loaded) == document
    assert loaded.configuration_file is None
    with pytest.raises(ConfigError, match="not both"):
        load_deployment(tmp_path / "x.yaml", document=document, environ={})
    with pytest.raises(ConfigError):
        load_deployment(document={"deployment": {"unknown": 1}}, environ={})


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


def test_a_deployment_without_source_rules_gets_their_directory_and_file(deployment):
    """A root provisioned before #2111 has no pg-hba directory; retarget adds it.

    Its record carries no ``runtime_network.offline`` (the default is never
    written), so a release before the setting still reads it, and the same
    record is accepted here unchanged.
    """
    import shutil
    import stat

    layout = RuntimeLayout(deployment)
    rules = read_private(layout.hba_directory / "pg_hba.conf")
    assert (
        "offline"
        not in record(deployment)["deployment"]["deployment"]["runtime_network"]
    )
    shutil.rmtree(layout.hba_directory)
    assert retarget.retarget_image(deployment, image=OLD) == {
        "image_changed": False,
        "documents_changed": 1,
        "services_started": False,
    }
    assert stat.S_IMODE(layout.hba_directory.stat().st_mode) == 0o700
    assert read_private(layout.hba_directory / "pg_hba.conf") == rules
    assert rules.startswith(b"# Rendered by ParishKit Stewardship")


def test_a_linked_or_shared_source_rules_directory_is_refused(deployment, tmp_path):
    """The pg-hba directory must be a real owner-only directory, never a link."""
    import shutil

    layout = RuntimeLayout(deployment)
    shutil.rmtree(layout.hba_directory)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    layout.hba_directory.symlink_to(elsewhere)
    with pytest.raises(ConfigError, match="non-symlink"):
        retarget.retarget_image(deployment, image=NEW)
    assert list(elsewhere.iterdir()) == []
    layout.hba_directory.unlink()
    layout.hba_directory.mkdir(mode=0o755)
    layout.hba_directory.chmod(0o755)
    with pytest.raises(ConfigError, match="ownership or mode"):
        retarget.retarget_image(deployment, image=NEW)
    assert record(deployment)["image"] == OLD


# The identity the creation tests use in place of the off-site uploader
# (#235), which no release has yet: one this release has, so its path and
# its place in the plan are real.
STAND_IN = "backup-worker"


def test_the_creation_list_is_closed_and_inert_in_this_release(deployment):
    """Only the uploader is listed, and this release has no uploader identity.

    So a missing password still refuses for every identity this release
    has, and nothing is created.
    """
    from parishkit.stewardship.runtime_identities import database_identities

    assert sorted(retarget.RETARGET_CREATES) == ["backup-uploader"]
    assert isinstance(retarget.RETARGET_CREATES, frozenset)
    names = {name for name, *_ in database_identities()}
    assert not retarget.RETARGET_CREATES & names
    path = RuntimeLayout(deployment).database_password(STAND_IN)
    path.unlink()
    with pytest.raises(CryptographicError, match=UNAVAILABLE):
        retarget.retarget_image(deployment, image=OLD)
    assert not path.exists()


def test_a_listed_absent_password_is_created_once_and_never_replaced(
    deployment, monkeypatch
):
    """A same-image retarget creates the file instead of reporting no change.

    It is generated as provisioning does (owner-only, 43 URL-safe
    characters), named in the result, and kept by every later run; no other
    password moves.
    """
    import re
    import stat

    monkeypatch.setattr(retarget, "RETARGET_CREATES", frozenset({STAND_IN}))
    layout = RuntimeLayout(deployment)
    path = layout.database_password(STAND_IN)
    web = read_private(layout.database_password("web"))
    path.unlink()
    assert retarget.retarget_image(deployment, image=OLD) == {
        "image_changed": False,
        "documents_changed": 0,
        "services_started": False,
        "passwords_created": [STAND_IN],
    }
    created = read_private(path)
    assert stat.S_IMODE(path.lstat().st_mode) == 0o600
    assert re.fullmatch(rb"[A-Za-z0-9_-]{43}", created)
    assert read_private(layout.database_password("web")) == web
    # Present now, so never created again, with or without a new image.
    assert retarget.retarget_image(deployment, image=OLD) == {
        "image_changed": False,
        "documents_changed": 0,
        "services_started": False,
    }
    assert retarget.retarget_image(deployment, image=NEW)["image_changed"] is True
    assert read_private(path) == created


def test_creation_waits_for_the_offline_interlock(deployment, monkeypatch):
    """An online service holding the interlock shared: nothing is created."""
    monkeypatch.setattr(retarget, "RETARGET_CREATES", frozenset({STAND_IN}))
    layout = RuntimeLayout(deployment)
    path = layout.database_password(STAND_IN)
    path.unlink()
    with StartupLease(layout.interlock, offline=False), pytest.raises(ConfigError):
        retarget.retarget_image(deployment, image=OLD)
    assert not os.path.lexists(path)


def test_another_identitys_missing_password_still_refuses(deployment, monkeypatch):
    """Only listed identities are created; any other missing file refuses first.

    With both missing, the refusal comes before the interlock is taken, so
    the listed one is not created either.
    """
    monkeypatch.setattr(retarget, "RETARGET_CREATES", frozenset({STAND_IN}))
    layout = RuntimeLayout(deployment)
    listed, other = layout.database_password(STAND_IN), layout.database_password("web")
    other.unlink()
    with pytest.raises(CryptographicError, match=UNAVAILABLE):
        retarget.retarget_image(deployment, image=OLD)
    listed.unlink()
    with pytest.raises(CryptographicError, match=UNAVAILABLE):
        retarget.retarget_image(deployment, image=NEW)
    assert not os.path.lexists(listed) and not os.path.lexists(other)
    assert record(deployment)["image"] == OLD


def test_a_dangling_link_is_not_absent_and_refuses(deployment, monkeypatch, tmp_path):
    """``lstat`` sees the link, so it is read, refused and left as it was."""
    monkeypatch.setattr(retarget, "RETARGET_CREATES", frozenset({STAND_IN}))
    path = RuntimeLayout(deployment).database_password(STAND_IN)
    path.unlink()
    target = tmp_path / "nowhere"
    path.symlink_to(target)
    with pytest.raises(ConfigError, match="explicit non-symlink path"):
        retarget.retarget_image(deployment, image=OLD)
    assert path.is_symlink() and not target.exists()


def test_creating_never_replaces_or_follows_what_appeared(deployment, tmp_path):
    """Create-only: a file or link that appeared after the check is untouched."""
    path = RuntimeLayout(deployment).database_password(STAND_IN)
    kept = read_private(path)
    with pytest.raises(ConfigError, match="could not be created"):
        retarget._create_password(path)
    assert read_private(path) == kept
    path.unlink()
    target = tmp_path / "followed"
    path.symlink_to(target)
    with pytest.raises(ConfigError, match="could not be created"):
        retarget._create_password(path)
    assert path.is_symlink() and not target.exists()
    assert leftovers(path) == []


def leftovers(path):
    """Temporary names a password creation left beside ``path``."""
    return sorted(p.name for p in path.parent.iterdir() if p.name.startswith("."))


def short_write(real):
    """An ``os.write`` that writes only the first byte, as a full disk can."""
    return lambda descriptor, data: real(descriptor, data[:1])


def failing(*_, **__):
    """Stand in for an I/O call that fails."""
    raise OSError("injected failure")


@pytest.mark.parametrize(
    ("target", "attribute", "replacement"),
    [
        (os, "write", short_write(os.write)),
        (os, "fsync", failing),
        (os, "link", failing),
        # After the link: the target this call linked is removed again.
        (retarget, "_sync_directory", failing),
    ],
)
def test_a_failed_creation_leaves_neither_file(
    deployment, monkeypatch, target, attribute, replacement
):
    """Short writes, flush and link failures leave no temporary or target."""
    path = RuntimeLayout(deployment).database_password(STAND_IN)
    path.unlink()
    monkeypatch.setattr(target, attribute, replacement)
    with pytest.raises(ConfigError, match="could not be written"):
        retarget._create_password(path)
    monkeypatch.undo()
    assert not os.path.lexists(path)
    assert leftovers(path) == []


def test_a_failed_cleanup_still_refuses_and_is_chained(deployment, monkeypatch):
    """The refusal is raised even when removing the temporary file fails."""
    path = RuntimeLayout(deployment).database_password(STAND_IN)
    path.unlink()
    monkeypatch.setattr(os, "fsync", failing)
    monkeypatch.setattr(os, "unlink", failing)
    with pytest.raises(ConfigError, match="could not be written") as raised:
        retarget._create_password(path)
    monkeypatch.undo()
    assert str(raised.value.__cause__) == "injected failure"
    assert not os.path.lexists(path)
    for name in leftovers(path):
        (path.parent / name).unlink()


def test_a_restrictive_umask_still_creates_an_owner_only_file(deployment):
    """The mode is set explicitly, so the umask cannot change it."""
    import stat

    path = RuntimeLayout(deployment).database_password(STAND_IN)
    path.unlink()
    previous = os.umask(0o777)
    try:
        retarget._create_password(path)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.lstat().st_mode) == 0o600
    assert path.lstat().st_nlink == 1
    assert len(read_private(path)) == 43
    assert leftovers(path) == []
