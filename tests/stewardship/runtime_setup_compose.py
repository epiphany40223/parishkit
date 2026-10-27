"""Host-only orchestration of a fully isolated initial configuration demonstration."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

from test_parishsoft_source import page

from parishkit.stewardship.runtime_paths import RuntimeLayout

from .test_integration_candidates import account
from .test_setup_forms import VALUES
from .test_source_giving import contribution, pledge
from .test_source_loading import provider_pages

# The completed setup's private integration invariants, read with operator
# SQL; the restore test (runtime_backup_restore) checks they survive a restore.
SETUP_PROOF = (
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
    "JOIN stewardship_task_run t ON t.id=f.task_id"
)


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
    """Drive the original browser while the consumers acknowledge by themselves.

    worker and mail-dispatch already mount the credential directories (#2228,
    operations "Setup credential pickup"), so the host neither recreates them
    nor runs acknowledge-credential: it only watches. After completion a
    consumer admitted without its key drains and exits once, and in
    Production Compose's restart policy brings it back with every credential.
    """
    from .test_operational_compose import compose_run

    layout = RuntimeLayout(configuration)
    if abort:
        # Pause YAML preparation before the consumers' automatic ACKs could
        # permit it. The browser cancels only after both real consumers
        # acknowledge.
        compose_run(file, project, "stop", "--timeout", "10", "config-installer")
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
    fixture = {
        "steps": VALUES,
        "workspace": account().decode(),
        "abort": abort,
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
        result = browser.result(timeout=540)
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
    # Both acknowledgements came from the consumers' own idle passes.
    acknowledged = operator_sql(
        file,
        project,
        configuration,
        "SELECT count(*) FROM stewardship_credential_consumer_ack",
    )
    assert acknowledged == "2", acknowledged
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
        SETUP_PROOF,
    ).stdout.strip()
    # Setup collects no campaign (#142): its final load reads no giving, and
    # it creates no Families, codes or mail schedules.
    assert proof == "promoted|succeeded|0|0|0|0|f|0|0|0|0|0", proof
    if configuration.profile.behind_proxy:
        readmitted(file, project)


def operator_sql(file, project, configuration, query):
    """One read-only operator SQL answer, stripped."""
    from .test_operational_compose import compose_run

    return compose_run(
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
        query,
    ).stdout.strip()


def readmitted(file, project):
    """Wait until worker and mail-dispatch restarted with every credential.

    Each started before setup's keys existed, so after completion it logs
    consumer_readmission (setup_completed), exits 0 and restarts under
    Compose's unless-stopped policy; the fresh process records no deferred
    key in its receipts (#2228).
    """
    from .test_operational_compose import compose_run

    deadline = time.monotonic() + 180
    for service in ("worker", "mail-dispatch"):
        while True:
            receipts = compose_run(
                file,
                project,
                "exec",
                "-T",
                service,
                "cat",
                "/tmp/stewardship-consumer/background.json",
                check=False,
            )
            healthy = compose_run(
                file,
                project,
                "exec",
                "-T",
                service,
                "pk-stewardship",
                "installer-healthcheck",
                check=False,
            )
            if (
                receipts.returncode == 0
                and json.loads(receipts.stdout)["deferred"] == []
                and healthy.returncode == 0
            ):
                break
            assert time.monotonic() < deadline, (service, receipts.stdout)
            time.sleep(1)
        logs = compose_run(file, project, "logs", service, check=False)
        assert "consumer_readmission" in logs.stdout + logs.stderr, service


def restore_initial_consumers(file, project, configuration):
    """After rollback unlinks candidate files, the running consumers stay healthy.

    Their directory mounts show the removal, so nothing is recreated before
    another attempt (#2228).
    """
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
