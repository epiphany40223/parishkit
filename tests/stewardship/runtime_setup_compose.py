"""Host-only orchestration of a fully isolated initial configuration demonstration."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

from test_parishsoft_source import page

from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import render_runtime

from .campaign_factory import campaign, financial
from .test_integration_candidates import account
from .test_runtime_topology import IMAGE as PRODUCTION_IMAGE
from .test_setup_forms import VALUES
from .test_source_giving import contribution, pledge
from .test_source_loading import provider_pages


def inject_providers(compose):
    """Test entrypoints cannot give the web Docker or installer filesystem access."""
    pages = provider_pages(family_change={"registeredOrganizationID": 1})
    pages[0] = [{"organizationID": 1}]
    pages += [
        page([pledge(organizationID=1)]),
        page([contribution(organizationId=1, memberId=1)]),
    ]
    script = Path(__file__).with_name("runtime_setup_provider.py").read_text()
    for name in (
        "scheduler",
        "worker",
        "mail-dispatch",
        "credential-installer-parishsoft",
        "credential-installer-google-workspace",
    ):
        compose["services"][name]["entrypoint"] = [
            "python",
            "-c",
            script,
            json.dumps(pages),
        ]


def complete_setup(file, project, configuration, mountpoint, *, abort=False):
    """Drive the original browser while the host recreates actual ACK consumers."""
    from .test_operational_compose import IMAGE, compose_run

    layout = RuntimeLayout(configuration)
    compose_run(
        file,
        project,
        "up",
        "--detach",
        "--wait",
        "--wait-timeout",
        "60",
        "credential-installer-parishsoft",
        "credential-installer-google-workspace",
        timeout=90,
    )
    # Render another complete topology, not a weakening overlay on initial mounts.
    configured, _ = render_runtime(
        configuration,
        image=PRODUCTION_IMAGE
        if configuration.profile is DeploymentProfile.PRODUCTION
        else IMAGE,
    )
    inject_providers(configured)
    for service in configured["services"].values():
        if service["image"] == PRODUCTION_IMAGE:
            service["image"] = IMAGE
        for mount in service["volumes"]:
            path = Path(mount["source"])
            if path.is_relative_to(configuration.paths.root):
                mount["source"] = str(
                    mountpoint / path.relative_to(configuration.paths.root)
                )
    configured_file = file.with_name("configured.json")
    configured_file.write_text(json.dumps(configured))
    fixture = {
        "steps": VALUES,
        "workspace": account().decode(),
        "abort": abort,
        "campaign": campaign(
            modules=["census", "financial"],
            financial=financial(fund_duids=[9], comparison_fund_duids=[9]),
        )["values"],
    }
    command = (
        "exec",
        "-T",
        "web",
        "python",
        "-c",
        Path(__file__).with_name("runtime_setup_probe.py").read_text(),
        str(layout.service_directory / "web.yaml"),
        json.dumps(fixture),
    )
    with ThreadPoolExecutor(max_workers=1) as executor, ExitStack() as cleanup:
        browser = executor.submit(
            compose_run, file, project, *command, check=False, timeout=540
        )

        def stop_unfinished_browser():
            """An operator failure must not wait out the browser's final deadline."""
            if not browser.done():
                compose_run(file, project, "stop", "--timeout", "5", "web", check=False)

        cleanup.callback(stop_unfinished_browser)
        acknowledged = set()
        deadline = time.monotonic() + 480
        while not browser.done() and time.monotonic() < deadline:
            status = compose_run(
                file,
                project,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "pk_stewardship_operator",
                "-d",
                configuration.postgres.name,
                "-Atc",
                "SELECT target,id FROM stewardship_secret_request "
                "WHERE state='awaiting_ack' ORDER BY target",
            ).stdout.strip()
            for line in status.splitlines():
                target, identifier = line.split("|")
                if identifier in acknowledged:
                    continue
                service = "worker" if target == "parishsoft" else "mail-dispatch"
                if abort and not acknowledged:
                    # Pause before any ACK can permit YAML preparation. The
                    # browser cancels only after both real consumers acknowledge.
                    compose_run(
                        file, project, "stop", "--timeout", "10", "config-installer"
                    )
                compose_run(
                    configured_file,
                    project,
                    "up",
                    "--detach",
                    "--no-deps",
                    "--force-recreate",
                    "--wait",
                    "--wait-timeout",
                    "60",
                    service,
                    timeout=90,
                )
                compose_run(
                    configured_file,
                    project,
                    "exec",
                    "-T",
                    service,
                    "pk-stewardship",
                    "acknowledge-credential",
                    "--config",
                    str(layout.service_directory / f"{service}.yaml"),
                    "--request-id",
                    identifier,
                )
                acknowledged.add(identifier)
            time.sleep(0.5)
        result = browser.result(timeout=60)
    if result.returncode:
        state = compose_run(
            file,
            project,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "pk_stewardship_operator",
            "-d",
            configuration.postgres.name,
            "-Atc",
            "SELECT task_type,state,phase,attempt,progress_current,progress_total "
            "FROM stewardship_task_run ORDER BY created_at",
            check=False,
        )
        logs = compose_run(file, project, "logs", "--tail", "50", check=False)
        raise AssertionError(
            result.stdout + result.stderr + state.stdout + logs.stdout + logs.stderr
        )
    assert len(acknowledged) == 2
    if abort:
        assert "INITIAL_SETUP_CANCELLED_AFTER_ACK_OK" in result.stdout
        restore_initial_consumers(file, project, configuration)
        return
    assert "INITIAL_SETUP_ATOMIC_COMPLETION_OK" in result.stdout
    # Inspect private integration invariants with read-only operator SQL. The web
    # intentionally cannot SELECT * from source snapshots or read target staging.
    proof = compose_run(
        file,
        project,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "pk_stewardship_operator",
        "-d",
        configuration.postgres.name,
        "-Atc",
        "SELECT s.state,t.state,s.counts->>'pledge',s.counts->>'contribution',"
        "(SELECT count(*) FROM stewardship_family_campaign WHERE portal_eligible),"
        "(SELECT count(*) FROM stewardship_family_campaign "
        "WHERE portal_eligible AND code_ciphertext IS NULL),"
        "EXISTS (SELECT 1 FROM stewardship_campaign_credentials p "
        "JOIN stewardship_source_current c ON c.snapshot_id=p.source_snapshot_id "
        "WHERE NOT p.population_dirty),"
        "(SELECT count(*) FROM stewardship_setup_sealed_credential "
        "WHERE scrubbed_at IS NULL OR ciphertext IS NOT NULL),"
        "(SELECT count(*) FROM stewardship_setup_draft_section "
        "WHERE scrubbed_at IS NULL),"
        "(SELECT count(*) FROM stewardship_schedule_definition),"
        "(SELECT count(*) FROM stewardship_schedule_occurrence),"
        "(SELECT count(*) FROM stewardship_schedule_fulfillment) "
        "FROM stewardship_setup_completion f "
        "JOIN stewardship_source_snapshot s ON s.id=f.snapshot_id "
        "JOIN stewardship_task_run t ON t.id=f.task_id",
    ).stdout.strip()
    assert proof == "promoted|succeeded|1|1|1|0|t|0|0|1|0|0", proof


def restore_initial_consumers(file, project, configuration):
    """After rollback unlinks candidate files, recreate the no-provider profiles."""
    from .test_operational_compose import compose_run

    compose_run(file, project, "start", "config-installer")
    deadline = time.monotonic() + 120
    while True:
        proof = compose_run(
            file,
            project,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "pk_stewardship_operator",
            "-d",
            configuration.postgres.name,
            "-Atc",
            "SELECT count(*),count(*) FILTER (WHERE state='expired'),"
            "(SELECT count(*) FROM stewardship_setup_completion) "
            "FROM stewardship_secret_request",
        ).stdout.strip()
        if proof == "2|2|0":
            break
        assert time.monotonic() < deadline, proof
        time.sleep(0.5)
    compose_run(
        file,
        project,
        "up",
        "--detach",
        "--no-deps",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "60",
        "worker",
        "mail-dispatch",
        timeout=90,
    )
    for service in ("worker", "mail-dispatch"):
        compose_run(
            file,
            project,
            "exec",
            "-T",
            service,
            "pk-stewardship",
            "installer-healthcheck",
        )
