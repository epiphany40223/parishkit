"""The operator prepares native Linux storage without network or SQL authority."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from parishkit.stewardship.deployment_documents import deployment_document

from .test_container_isolation import _fixture_volume
from .test_runtime_topology import configuration_at

pytestmark = pytest.mark.skipif(
    os.environ.get("PARISHKIT_RUN_RUNTIME_TESTS") != "1",
    reason="Requires explicitly opted-in disposable Docker runtime validation",
)
IMAGE = "parishkit-stewardship:development"


def test_operator_prepares_private_native_volume_and_public_static_assets(tmp_path):
    """Use real UID 10001 and CLI commands, not host ownership assumptions."""
    seed = tmp_path / "seed"
    seed.mkdir(mode=0o700)
    runtime_root = Path("/fixture/runtime")
    configuration = configuration_at(runtime_root)
    # Keep the broker credential below the mounted native-volume root.
    (seed / "operator.json").write_text(json.dumps(deployment_document(configuration)))
    (seed / "operator.json").chmod(0o600)
    volume = "parishkit-provision-" + uuid4().hex
    try:
        mountpoint = _fixture_volume(seed, IMAGE, volume, owner=10001)

        def run(*arguments, entrypoint="pk-stewardship"):
            """Only this fresh disposable volume is writable; network is absent."""
            return subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--init",
                    "--network",
                    "none",
                    "--user",
                    "10001:10001",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,noexec,mode=1777",
                    "--mount",
                    f"type=volume,source={volume},target=/fixture",
                    "--entrypoint",
                    entrypoint,
                    IMAGE,
                    *arguments,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

        command = (
            "provision-runtime",
            "--config",
            "/fixture/operator.json",
            "--image",
            IMAGE,
            "--bind-source-root",
            str(mountpoint / "runtime"),
        )
        result = run(*command)
        if result.returncode:
            diagnosis = run(
                "-c",
                "from pathlib import Path\n"
                "from parishkit.stewardship.deployment import load_deployment\n"
                "from parishkit.stewardship import runtime_provisioning as provision\n"
                "import sys\n"
                "provision.provision_runtime("
                "load_deployment(Path('/fixture/operator.json')),"
                "image=sys.argv[1],bind_source_root=Path(sys.argv[2]))\n",
                IMAGE,
                str(mountpoint / "runtime"),
                entrypoint="python",
            )
            pytest.fail(
                "Synthetic provisioning failure: " + diagnosis.stdout + diagnosis.stderr
            )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["services_started"] is False
        assert run(*command).returncode == 2
        static = run(
            "collect-static", "--destination", str(runtime_root / "cache/static")
        )
        assert static.returncode == 0, static.stdout + static.stderr
        assert json.loads(static.stdout) == {"static_assets_collected": True}
        inspection = run(
            "-c",
            "import json,os,stat\nfrom pathlib import Path\n"
            "root=Path('/fixture/runtime')\n"
            "paths=list(root.rglob('*'))\n"
            "assert all(p.stat().st_uid==10001 for p in paths)\n"
            "assert all(stat.S_IMODE(p.stat().st_mode)=="
            "(0o700 if p.is_dir() else 0o600)"
            " for p in paths)\n"
            "assert (root/'cache/static/stewardship/ui-v1.css').is_file()\n"
            "print((root/'config/services/compose.json').read_text())\n",
            entrypoint="python",
        )
        assert inspection.returncode == 0, inspection.stderr
        compose = json.loads(inspection.stdout)
        for service in compose["services"].values():
            for mount in service["volumes"]:
                assert Path(mount["source"]).is_relative_to(mountpoint / "runtime")
                assert Path(mount["target"]).is_relative_to(runtime_root) or mount[
                    "target"
                ] in {
                    "/var/lib/postgresql",
                    "/run/secrets/postgres-password",
                    "/data",
                    "/run/secrets/valkey.acl",
                }
    finally:
        subprocess.run(
            ["docker", "volume", "rm", volume], capture_output=True, timeout=30
        )
