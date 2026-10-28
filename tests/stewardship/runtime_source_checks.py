"""PostgreSQL's per-login source rules, proved against the real stack (#2111).

Operations spec, "Database connection sources" (Tests): ``database-sources``
must pass from every started service and every one-shot profile, and a
login presented with its real password from anywhere but its own service's
address is refused by the final reject.
"""

import json

from parishkit.stewardship.runtime_hba import OPERATOR
from parishkit.stewardship.runtime_paths import PROVISIONING_RECORD

ONE_SHOTS = (
    "bootstrap",
    "migration",
    "admin-recovery",
    "database-provision",
    "backup-worker",
    "backup-uploader",
)
# One connection with the password from PGPASSWORD; prints what happened.
CONNECT = (
    "import sys, psycopg\n"
    "try:\n"
    "    psycopg.connect(host='postgres', port=5432, dbname=sys.argv[1],\n"
    "        user=sys.argv[2], sslmode='disable', connect_timeout=10).close()\n"
    "except psycopg.OperationalError as error:\n"
    "    print('REFUSED' if 'pg_hba.conf rejects connection' in str(error)\n"
    "        else 'FAILED')\n"
    "else:\n"
    "    print('CONNECTED')\n"
)


def probe_file(file):
    """The Compose document with the backup worker's record mount dropped.

    The backup worker bind-mounts the provisioning record, which this
    fixture writes only for its restore scenario. The probes read nothing
    from it, and its network identity is unchanged without it.
    """
    document = json.loads(file.read_text())
    service = document["services"]["backup-worker"]
    service["volumes"] = [
        mount
        for mount in service["volumes"]
        if not mount["source"].endswith(PROVISIONING_RECORD)
    ]
    probes = file.with_name("source-probes.json")
    probes.write_text(json.dumps(document))
    return probes


def check_database_sources(file, project, running, run, stopped=()):
    """``database-sources`` passes in each running service and each one-shot.

    Each service is probed with the document its own command names (the
    background services run an ``-initial`` one in some modes). ``stopped``
    names long-running services not started yet, probed by ``run``, which
    gives the container its service's fixed address.
    """
    services = json.loads(file.read_text())["services"]
    for service in running:
        result = run(
            file,
            project,
            "exec",
            "-T",
            service,
            "pk-stewardship",
            "database-sources",
            "--config",
            services[service]["command"][2],
        )
        assert "database sources match the rendered rules" in result.stdout, service
    probes = probe_file(file)
    for service in (*ONE_SHOTS, *stopped):
        result = run(
            probes,
            project,
            "run",
            "--rm",
            service,
            "database-sources",
            "--config",
            services[service]["command"][2],
        )
        assert "database sources match the rendered rules" in result.stdout, service


def _connect(file, project, how, service, password, database, login, run):
    """One real-password connection from ``service`` (``exec`` or ``run``)."""
    prefix = ("exec", "-T") if how == "exec" else ("run", "--rm")
    entrypoint = () if how == "exec" else ("--entrypoint", "python")
    command = ("python",) if how == "exec" else ()
    result = run(
        file,
        project,
        *prefix,
        "-e",
        "PGPASSWORD=" + password,
        *entrypoint,
        service,
        *command,
        "-c",
        CONNECT,
        database,
        login,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def check_refused_sources(file, project, configuration, passwords, wrong, run):
    """A stolen password is useless from any other source.

    ``passwords`` reads a login's synthetic password by its path name;
    ``wrong`` is a running long-lived service that does not hold web's
    login (the worker when it runs). Web's own login works from web, so the
    refusals below are the rules, not a broken probe.
    """
    database = configuration.postgres.name
    web = passwords("web")
    assert (
        _connect(file, project, "exec", "web", web, database, "pk_stewardship_web", run)
        == "CONNECTED"
    )
    assert (
        _connect(file, project, "exec", wrong, web, database, "pk_stewardship_web", run)
        == "REFUSED"
    )
    operator = passwords("operator")
    assert (
        _connect(
            probe_file(file),
            project,
            "run",
            "backup-worker",
            operator,
            database,
            OPERATOR,
            run,
        )
        == "REFUSED"
    )
    # The off-site uploader (#235): its password works from its own
    # profile only. The backup worker reads every login's password (it
    # archives the credentials tree), so the two one-shots are refused each
    # other's logins by the final reject (28000), whatever the password.
    probes = probe_file(file)
    uploader, backup = passwords("backup-uploader"), passwords("backup-worker")
    login = "pk_stewardship_backup_uploader"
    assert (
        _connect(
            probes, project, "run", "backup-uploader", uploader, database, login, run
        )
        == "CONNECTED"
    )
    assert (
        _connect(
            probes, project, "run", "backup-worker", uploader, database, login, run
        )
        == "REFUSED"
    )
    assert (
        _connect(
            probes,
            project,
            "run",
            "backup-uploader",
            backup,
            database,
            "pk_stewardship_backup_worker",
            run,
        )
        == "REFUSED"
    )
    # A container on backend without a fixed address takes one from the
    # dynamic range, which no rule allows, even for web's own definition.
    document = json.loads(file.read_text())
    for network in document["services"]["web"]["networks"].values():
        network.pop("ipv4_address", None)
    dynamic = file.with_name("dynamic-backend.json")
    dynamic.write_text(json.dumps(document))
    assert (
        _connect(
            dynamic, project, "run", "web", web, database, "pk_stewardship_web", run
        )
        == "REFUSED"
    )
