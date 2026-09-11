"""One fake-backed foundation starts through the real CLI and kernel boundaries."""

import json
import os
import secrets
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.bootstrap import HANDOFF_TARGETS, INITIAL_TARGETS
from parishkit.stewardship.runtime_identities import database_identities
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import render_runtime
from parishkit.stewardship.runtime_valkey import web_acl
from parishkit.stewardship.startup_interlock import MARKER

from .test_container_isolation import _fixture_volume
from .test_runtime_topology import IMAGE as PRODUCTION_IMAGE
from .test_runtime_topology import configuration_at

pytestmark = pytest.mark.skipif(
    os.environ.get("PARISHKIT_RUN_RUNTIME_TESTS") != "1",
    reason="Requires explicitly opted-in disposable Docker runtime validation",
)
IMAGE = "parishkit-stewardship:development"


def seed_runtime(root, *, production=False):
    """Only synthetic credentials; fixture ownership is translated on native Linux."""
    configuration = configuration_at(root, production=production)
    configuration = replace(
        configuration,
        public_origin="https://parish.example"
        if production
        else "http://localhost:8000",
        valkey=replace(
            configuration.valkey,
            password_file=root / "credentials" / "valkey" / "web",
        ),
        postgres=replace(
            configuration.postgres,
            password_files={
                name: root / "credentials" / "selected-sql" / name
                for name in (
                    "operator",
                    "web",
                    "download",
                    "credential-installer-metrics",
                )
            },
        ),
        runtime_budget=replace(configuration.runtime_budget, download_capacity=3),
    )
    layout = RuntimeLayout(configuration)
    directories = [
        *configuration.paths.values.values(),
        layout.deployment_directory,
        layout.service_directory,
        layout.database_password("operator").parent,
        *(layout.database_password(name).parent for name, *_ in database_identities()),
        configuration.valkey.password_file.parent,
        *(layout.credential_directory(name) for name in INITIAL_TARGETS),
        layout.credential_directory("google_oauth"),
        *(layout.credential_directory(name) for name in HANDOFF_TARGETS),
        *(layout.handoff(name).parent for name in HANDOFF_TARGETS),
        configuration.paths["caddy"] / "data",
        configuration.paths["caddy"] / "config",
        configuration.paths["cache"] / "static",
    ]
    for directory in directories:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    for name in ["operator", *(item[0] for item in database_identities())]:
        write_private(
            layout.database_password(name), secrets.token_urlsafe(32).encode()
        )
    write_private(layout.interlock, MARKER)
    write_private(
        layout.credential("google_oauth"),
        b'{"client_id":"fake-client","client_secret":"fake-secret"}',
    )
    write_private(configuration.valkey.password_file, b"disposable-valkey-only")
    write_private(
        configuration.paths["cache"] / "static" / "synthetic.txt",
        b"Synthetic static fixture",
    )
    # The fixture uses the same restricted vocabulary needed by limiter/metrics.
    write_private(
        configuration.valkey.password_file.parent / "server.acl",
        web_acl(b"disposable-valkey-only"),
    )
    compose, documents = render_runtime(
        configuration, image=PRODUCTION_IMAGE if production else IMAGE
    )
    for path, document in documents.items():
        if isinstance(document, str):
            # Test-only local CA: no ACME request, DNS dependency or real TLS key.
            document = document.replace(
                "issuer acme {\n"
                "            dir https://acme-v02.api.letsencrypt.org/directory\n"
                "        }",
                "issuer internal",
            )
            write_private(path, document.encode())
        else:
            write_private(path, json.dumps(document).encode())
    if production:
        for service in compose["services"].values():
            if service["image"] == PRODUCTION_IMAGE:
                service["image"] = (
                    IMAGE  # Synthetic local image, never a registry push.
                )
        compose["services"]["caddy"]["ports"] = ["127.0.0.1::8080", "127.0.0.1::8443"]
    # A random local port avoids interacting with any developer's running app.
    else:
        compose["services"]["web"]["ports"] = ["127.0.0.1::8000"]
    return configuration, compose


def compose_run(file, project, *arguments, check=True, timeout=60):
    """Every command is scoped to this fixture's UUID Compose project."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            project,
            "--file",
            str(file),
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check:
        detail = ""
        if result.returncode and arguments[:2] == ("run", "--rm"):
            # This fixture contains synthetic values only. Capture the original
            # exception at the private CLI boundary without weakening production
            # diagnostics or copying any real operator credentials into a report.
            command = arguments[3:] or tuple(
                json.loads(file.read_text())["services"][arguments[2]]["command"]
            )
            script = (
                "import sys, traceback\n"
                "from parishkit.stewardship.cli import main\n"
                "def trace(frame, event, arg):\n"
                "    if (event == 'exception' and\n"
                "            frame.f_code.co_name == 'execute_operator'):\n"
                "        traceback.print_exception(*arg)\n"
                "    return trace\n"
                "sys.settrace(trace)\n"
                "main(sys.argv[1:])\n"
            )
            diagnosis = compose_run(
                file,
                project,
                "run",
                "--rm",
                "--entrypoint",
                "python",
                arguments[2],
                "-c",
                script,
                *command,
                check=False,
            )
            detail = diagnosis.stdout + diagnosis.stderr
        assert result.returncode == 0, result.stdout + result.stderr + detail
    return result


@pytest.mark.parametrize("production", [False, True])
def test_complete_foundation_bootstrap_and_online_exclusion(tmp_path, production):
    """Use real operator profiles, narrow mounts, SQL identities and native inodes."""
    root = tmp_path / "seed"
    configuration, compose = seed_runtime(root, production=production)
    layout = RuntimeLayout(configuration)
    project = "parishkit-runtime-" + uuid4().hex
    volume = project + "-state"
    file = tmp_path / "compose.json"
    deployment = str(uuid4())
    try:
        mountpoint = _fixture_volume(root, IMAGE, volume, owner=10001)
        for service in compose["services"].values():
            for mount in service["volumes"]:
                path = Path(mount["source"])
                if path.is_relative_to(root):
                    mount["source"] = str(mountpoint / path.relative_to(root))
        file.write_text(json.dumps(compose))
        compose_run(file, project, "up", "--detach", "--wait", "postgres", "valkey")
        for command in ("database-roles",):
            compose_run(
                file,
                project,
                "run",
                "--rm",
                "database-provision",
                command,
                "--config",
                str(layout.service_directory / "database-provision.yaml"),
                "--confirm-deployment",
                deployment,
            )
        for phase in ("prepare", "import"):
            if phase == "import":
                compose_run(file, project, "run", "--rm", "migration", timeout=90)
                policy_command = (
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    "pk_stewardship_operator",
                    "-d",
                    configuration.postgres.name,
                    "-Atc",
                    "SELECT capacity,version FROM stewardship_download_policy "
                    "WHERE id=1",
                )
                first_policy = compose_run(
                    file, project, *policy_command
                ).stdout.strip()
                assert first_policy.startswith("3|")
                compose_run(file, project, "run", "--rm", "migration", timeout=90)
                assert (
                    compose_run(file, project, *policy_command).stdout.strip()
                    == first_policy
                )
                compose_run(
                    file,
                    project,
                    "run",
                    "--rm",
                    "database-provision",
                    "database-grants",
                    "--config",
                    str(layout.service_directory / "database-provision.yaml"),
                    "--confirm-deployment",
                    deployment,
                )
            compose_run(
                file,
                project,
                "run",
                "--rm",
                "bootstrap",
                "bootstrap",
                "--config",
                str(layout.service_directory / "bootstrap.yaml"),
                "--phase",
                phase,
                "--deployment-id",
                deployment,
                "--admin-email",
                "admin@example.org",
            )
        preview = compose_run(
            file,
            project,
            "run",
            "--rm",
            "admin-recovery",
            "preview-admin-recovery",
            "--config",
            str(layout.service_directory / "admin-recovery.yaml"),
            "--confirm-deployment",
            deployment,
            "--target-email",
            "Replacement@Example.org",
        )
        proposed = json.loads(preview.stdout)["recovery_preview"]
        assert proposed["current_admin_rules"] == ["admin@example.org"]
        assert proposed["target_email"] == "replacement@example.org"
        assert proposed["before_roles"] == []
        assert proposed["after_roles"] == ["administrator"]
        assert proposed["parish_name"] is None
        # No online process holds the interlock yet: this must be the configured
        # upgrade hold, not the later online/offline mutual-exclusion assertion.
        held = compose_run(file, project, "run", "--rm", "migration", check=False)
        assert held.returncode != 0
        assert "offline operation refused" in held.stderr
        compose_run(file, project, "up", "--detach", "web", "config-installer")
        deadline = time.monotonic() + 45
        while True:
            health = compose_run(
                file,
                project,
                "exec",
                "-T",
                "web",
                "pk-stewardship",
                "healthcheck",
                check=False,
            )
            if health.returncode == 0:
                break
            if time.monotonic() >= deadline:
                logs = compose_run(file, project, "logs", "web", "config-installer")
                probe = (
                    "import sys\nfrom pathlib import Path\n"
                    "from parishkit.stewardship.deployment import load_deployment\n"
                    "from parishkit.stewardship.runtime_web import configure_web\n"
                    "configure_web(load_deployment(Path(sys.argv[1])))\n"
                )
                diagnostic = compose_run(
                    file,
                    project,
                    "run",
                    "--rm",
                    "--entrypoint",
                    "python",
                    "web",
                    "-c",
                    probe,
                    str(layout.service_directory / "web.yaml"),
                    check=False,
                )
                pytest.fail(
                    "Synthetic runtime liveness failed: "
                    + logs.stdout
                    + diagnostic.stderr
                )
            time.sleep(0.5)
        diagnosis = compose_run(
            file,
            project,
            "exec",
            "-T",
            "web",
            "pk-stewardship",
            "health",
            "--config",
            str(layout.service_directory / "web.yaml"),
        )
        assert json.loads(diagnosis.stdout)["ready"] is True
        auth_probe = compose_run(
            file,
            project,
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            Path(__file__).with_name("runtime_auth_probe.py").read_text(),
            str(layout.service_directory / "web.yaml"),
        )
        assert auth_probe.stdout.strip() == "RUNTIME_AUTH_CONSUMERS_OK"
        from .runtime_health_checks import check_dependency_failure

        check_dependency_failure(
            file, project, layout.service_directory / "web.yaml", compose_run
        )
        if production:
            from .runtime_ingress_checks import check_ingress

            check_ingress(file, project, configuration, compose_run)
        cohort_probe = (
            "import json, sys\nfrom pathlib import Path\n"
            "from parishkit.stewardship.deployment import load_deployment\n"
            "from parishkit.stewardship import consumer_runtime as consumers\n"
            "value = consumers.loaded_service_receipts("
            "load_deployment(Path(sys.argv[1])))\n"
            "print(json.dumps({'targets': sorted(value), 'workers_agree': True}))\n"
        )
        workers = compose_run(
            file,
            project,
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            cohort_probe,
            str(layout.service_directory / "web.yaml"),
        )
        assert json.loads(workers.stdout) == {
            "workers_agree": True,
            "targets": [
                "django_signing",
                "family_code_mac",
                "general_encryption",
                "google_oauth",
                "metrics",
                "token_public",
            ],
        }
        # A newly launched one-off container may reopen the same files but has
        # no admitted supervisor/worker cohort, and cannot attest for the service.
        one_off_document = json.loads(file.read_text())
        for network in one_off_document["services"]["web"]["networks"].values():
            network.pop("ipv4_address", None)
        one_off_file = file.with_name("one-off.json")
        one_off_file.write_text(json.dumps(one_off_document))
        one_off = compose_run(
            one_off_file,
            project,
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "web",
            "-c",
            cohort_probe,
            str(layout.service_directory / "web.yaml"),
            check=False,
        )
        assert one_off.returncode != 0
        assert "Runtime directory is unavailable" in one_off.stderr
        rotation_script = (
            Path(__file__).with_name("runtime_rotation_probe.py").read_text()
        )
        installer = "credential-installer-metrics"
        installer_configuration = str(layout.service_directory / (installer + ".yaml"))
        public = compose_run(
            file,
            project,
            "run",
            "--rm",
            "--entrypoint",
            "python",
            installer,
            "-c",
            rotation_script,
            installer_configuration,
            "public",
        )
        staged = compose_run(
            file,
            project,
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            rotation_script,
            str(layout.service_directory / "web.yaml"),
            "stage",
            public.stdout.strip(),
        )
        identifier = json.loads(staged.stdout)["request_id"]
        compose_run(file, project, "up", "--detach", installer)

        def wait_for_state(expected):
            """Observe only the exact synthetic request through web's normal grants."""
            deadline = time.monotonic() + 30
            while True:
                result = compose_run(
                    file,
                    project,
                    "exec",
                    "-T",
                    "web",
                    "python",
                    "-c",
                    rotation_script,
                    str(layout.service_directory / "web.yaml"),
                    "state",
                    identifier,
                )
                state = json.loads(result.stdout)["state"]
                if state == expected:
                    return
                if time.monotonic() >= deadline:
                    logs = compose_run(file, project, "logs", installer)
                    pytest.fail(f"Synthetic rotation stayed {state}: {logs.stdout}")
                time.sleep(0.5)

        wait_for_state("awaiting_ack")
        acknowledgement = (
            "exec",
            "-T",
            "web",
            "pk-stewardship",
            "acknowledge-credential",
            "--config",
            str(layout.service_directory / "web.yaml"),
            "--request-id",
            identifier,
        )
        old_consumer = compose_run(file, project, *acknowledgement, check=False)
        assert old_consumer.returncode == 2
        assert "acknowledgement refused" in old_consumer.stderr
        # Compose stops/removes the old process namespace and remounts the new
        # credential inode. An ordinary worker reload cannot accomplish this.
        compose_run(file, project, "up", "--detach", "--force-recreate", "web")
        deadline = time.monotonic() + 30
        while True:
            result = compose_run(file, project, *acknowledgement, check=False)
            if result.returncode == 0:
                break
            if time.monotonic() >= deadline:
                pytest.fail("Recreated synthetic consumer could not acknowledge")
            time.sleep(0.5)
        wait_for_state("applied")
        for live_installer in ("config-installer", installer):
            compose_run(
                file,
                project,
                "exec",
                "-T",
                live_installer,
                "pk-stewardship",
                "installer-healthcheck",
            )
        if production:
            from .runtime_health_checks import check_web_crash_recovery

            check_web_crash_recovery(file, project, compose_run)
        denied = compose_run(file, project, "run", "--rm", "migration", check=False)
        assert denied.returncode != 0
        assert "offline operation refused" in denied.stderr
    finally:
        if file.exists():
            compose_run(file, project, "down", "--timeout", "10", check=False)
        # Only this fixture's named volume and ephemeral containers are removed.
        subprocess.run(
            ["docker", "volume", "rm", volume],
            capture_output=True,
            timeout=30,
        )
