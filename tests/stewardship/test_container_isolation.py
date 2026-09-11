"""Opt-in real kernel mount proof with synthetic owner-only credentials.

This exercises the built image without a Docker socket in the application. It
does not provision production services or claim their queue boundaries tested.
"""

import json
import os
import subprocess
import sys
import warnings
from pathlib import Path
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.cryptography import Key, TokenPrivateKeyring
from parishkit.stewardship.accounts.key_files import serialize_keyring
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS

pytestmark = pytest.mark.skipif(
    os.environ.get("PARISHKIT_RUN_ISOLATION_TESTS") != "1",
    reason="Container isolation requires its explicit test profile.",
)

# No host secrets or arbitrary targets are accepted; subprocesses bypass a shell.
PROBE = """
import json, os
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from parishkit.stewardship.deployment import ServiceRole, load_deployment
from parishkit.stewardship.service_boundaries import admit_online_service
from parishkit.stewardship.accounts.key_files import load_keyring

role = ServiceRole(os.environ['ISOLATION_ROLE'])
target = os.environ.get('ISOLATION_TARGET') or None
names = json.loads(os.environ['ISOLATION_SECRET_NAMES'])
paths = {name: Path('/run/secrets') / name for name in names}
if target:
    paths[target] = Path('/opt/parishkit/credentials') / target / 'credential'
config = replace(load_deployment(environ={}), service_role=role,
    credential_target=target, secrets=MappingProxyType(paths))
assert admit_online_service(config) == role
authority = config.paths['config'] / 'stewardship' / 'synthetic-write'
try:
    authority.write_text('synthetic')
except OSError:
    assert role != ServiceRole.CONFIG_INSTALLER
else:
    assert role == ServiceRole.CONFIG_INSTALLER
for name, path in paths.items():
    if name == target:
        continue
    try:
        path.write_text('must not overwrite')
    except OSError:
        pass
    else:
        raise AssertionError('Consumer secret is writable')
if target:
    (config.paths['credentials'] / target / 'synthetic-write').write_text('synthetic')
    other = 'slack' if target != 'slack' else 'parishsoft'
    assert not (config.paths['credentials'] / other).exists()
private_path = Path('/run/secrets/token_private')
if role in {ServiceRole.MAIL_DISPATCH, ServiceRole.TOKEN_KEY_ROTATION}:
    private = load_keyring(private_path, 'token_private')
    value = private.decrypt(os.environ['ISOLATION_CIPHER'], context=b'isolation')
    assert value == b'synthetic-token'
elif target != 'token_private':
    assert not private_path.exists()
if 'token_public' in paths:
    public = load_keyring(paths['token_public'], 'token_public')
    assert not hasattr(public, 'decrypt')
    assert public.encrypt(b'synthetic', context=b'isolation')
assert not Path('/var/run/docker.sock').exists()
print('isolated')
"""


@pytest.fixture
def image():
    """A missing image is a failure when CI explicitly enables the profile."""
    value = os.environ.get(
        "STEWARDSHIP_ISOLATION_IMAGE", "parishkit-stewardship:development"
    )
    subprocess.run(
        ["docker", "image", "inspect", value], check=True, capture_output=True
    )
    return value


@pytest.mark.parametrize("role", list(ALLOWED_SECRETS))
def test_online_role_kernel_mount_boundaries(tmp_path, image, role):
    _probe(tmp_path, image, role)


@pytest.mark.parametrize(
    "target", ["google_oauth", "google_workspace", "token_private", "slack"]
)
def test_credential_target_kernel_mount_boundaries(tmp_path, image, target):
    _probe(tmp_path, image, ServiceRole.CREDENTIAL_INSTALLER, target=target)


def _probe(tmp_path, image, role, *, target=None):
    """Mount exact synthetic targets and remove only the one-shot container."""
    names = {target, "handoff_private"} if target else ALLOWED_SECRETS[role]
    private = TokenPrivateKeyring([Key("isolation", "active", os.urandom(32))])
    authority = tmp_path / "authority"
    authority.mkdir(mode=0o700)
    arguments = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,mode=1777",
        "--mount",
        f"type=bind,src={authority},dst=/opt/parishkit/config/stewardship"
        + ("" if role is ServiceRole.CONFIG_INSTALLER else ",readonly"),
    ]
    directory = None
    if target:
        directory = tmp_path / "target"
        directory.mkdir(mode=0o700)
    for name in sorted(names):
        path = directory / "credential" if name == target else tmp_path / name
        value = b"synthetic-unused-credential"
        if name == "token_private":
            value = serialize_keyring(private)
        elif name == "token_public":
            value = serialize_keyring(private.public())
        path.write_bytes(value)
        path.chmod(0o600)
        if name != target:
            arguments += [
                "--mount",
                f"type=bind,src={path},dst=/run/secrets/{name},readonly",
            ]
    if target:
        arguments += [
            "--mount",
            f"type=bind,src={directory},dst=/opt/parishkit/credentials/{target}",
        ]
    values = {
        "ISOLATION_ROLE": role.value,
        "ISOLATION_TARGET": target or "",
        "ISOLATION_SECRET_NAMES": json.dumps(sorted(names)),
        "ISOLATION_CIPHER": private.public().encrypt(
            b"synthetic-token", context=b"isolation"
        ),
    }
    for name, value in values.items():
        arguments += ["--env", name + "=" + value]
    name = "parishkit-isolation-" + uuid4().hex
    arguments += ["--name", name, "--entrypoint", "python", image, "-c", PROBE]
    volume = "parishkit-isolation-" + uuid4().hex
    try:
        mountpoint = _fixture_volume(tmp_path, image, volume)
        arguments = [
            value.replace(f"src={tmp_path}/", f"src={mountpoint}/")
            for value in arguments
        ]
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "isolated"
    finally:
        _cleanup_probe(name, volume, body_failed=sys.exc_info()[0] is not None)


def _cleanup_probe(name, volume, *, body_failed):
    """Attempt every owned cleanup without replacing a provisioning/probe error."""
    failure = None
    for command, check in (
        (["docker", "rm", "-f", name], False),
        (["docker", "rm", "-f", volume + "-bootstrap"], False),
        (["docker", "volume", "rm", volume], True),
    ):
        try:
            subprocess.run(command, check=check, capture_output=True, timeout=15)
        except (subprocess.SubprocessError, OSError) as error:
            failure = failure or error
    if failure is not None:
        if not body_failed:
            raise failure
        warnings.warn("Disposable isolation fixture cleanup failed.", stacklevel=2)


def _fixture_volume(root, image, name, *, owner=None):
    """Provision owner-only Linux inodes inside one disposable named volume.

    Some Docker Desktop host-file shares report root ownership in every new
    container despite chown. A native volume preserves owner-only semantics.
    Consumers bind individual files from this exact daemon-owned volume path;
    they never receive the volume root or Docker socket. No host owner changes.
    """
    subprocess.run(
        ["docker", "volume", "create", "--label", "parishkit.test=phase1b", name],
        check=True,
        capture_output=True,
        timeout=15,
    )
    script = """
import os, shutil
from pathlib import Path
root = Path('/fixture')
shutil.copytree('/seed', root, dirs_exist_ok=True)
paths = sorted(root.rglob('*'), key=lambda path: len(path.parts), reverse=True)
for path in paths:
    assert not path.is_symlink()
    os.chmod(path, 0o700 if path.is_dir() else 0o600)
    os.chown(path, int(os.environ['UID_TARGET']), int(os.environ['GID_TARGET']))
"""
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            name + "-bootstrap",
            "--network",
            "none",
            "--read-only",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "DAC_READ_SEARCH",
            "--security-opt",
            "no-new-privileges:true",
            "--mount",
            f"type=bind,src={root},dst=/seed,readonly",
            "--mount",
            f"type=volume,src={name},dst=/fixture",
            "--env",
            f"UID_TARGET={os.getuid() if owner is None else owner}",
            "--env",
            f"GID_TARGET={os.getgid() if owner is None else owner}",
            "--entrypoint",
            "python",
            image,
            "-c",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = subprocess.run(
        ["docker", "volume", "inspect", "--format", "{{.Mountpoint}}", name],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    path = Path(result.stdout.strip())
    assert path.is_absolute() and path.name == "_data" and path.parent.name == name
    return path
