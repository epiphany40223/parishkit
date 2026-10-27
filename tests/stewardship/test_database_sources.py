"""The password-free source check and the hba reload check (#2111).

Operations spec, "Database connection sources": ``database-sources`` tries
every login with no password from inside one service and compares what
PostgreSQL answered with the rendered rules; ``hba-check`` renders the query
that proves the rules in force were loaded from the rendered file. The real
PostgreSQL answers are exercised by the operational Compose job.
"""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from parishkit.stewardship import database_sources as sources
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole, load_deployment
from parishkit.stewardship.runtime_hba import HBA_FILE, OPERATOR, held_logins
from parishkit.stewardship.runtime_paths import RuntimeLayout

from .test_local_profile import PROFILES
from .test_runtime_hba import INVENTORY, expected_source, host_lines, rendering
from .test_runtime_retarget import deployment  # noqa: F401 (a fixture)

ALLOWED = (
    'connection to server at "postgres" (172.29.240.2), port 5432 failed: '
    "fe_sendauth: no password supplied"
)
REFUSED = (
    'connection to server at "postgres" (172.29.240.2), port 5432 failed: '
    'FATAL:  pg_hba.conf rejects connection for host "172.29.240.13", user '
    '"pk_stewardship_web", database "stewardship", no encryption'
)


def configuration(role=ServiceRole.WORKER, user="pk_stewardship_worker", target=None):
    """Just what the source check reads of a service document."""
    return SimpleNamespace(
        service_role=role,
        credential_target=target,
        postgres=SimpleNamespace(
            host="postgres",
            port=5432,
            name="stewardship",
            user=user,
            connect_timeout=5,
        ),
    )


def test_the_inventory_is_every_login_and_the_operator():
    """Every login of database_identities() is probed, and the operator last."""
    assert set(sources.inventory()) == INVENTORY
    assert len(sources.inventory()) == len(INVENTORY)
    assert sources.inventory()[-1] == OPERATOR


@pytest.mark.parametrize("profile", PROFILES)
def test_each_services_expected_row_is_exactly_its_rendered_rules(profile):
    """The check's expectation and the renderer derive from one helper.

    For every application service in the rendered Compose document, the
    logins its own document holds are exactly the logins the rendered file
    allows from that service's source, so ``database-sources`` run there
    expects what PostgreSQL was told.
    """
    from parishkit.stewardship.runtime_topology import DEVELOPMENT_IMAGE, render_runtime

    from .test_runtime_hba import IMAGES

    configuration_, compose, hba = rendering(profile)
    image = IMAGES.get(profile, DEVELOPMENT_IMAGE)
    _, documents = render_runtime(configuration_, image=image)
    rules = host_lines(hba)[:-1]
    checked = 0
    for name, service in compose["services"].items():
        command = service.get("command") or []
        if command[1:2] != ["--config"]:
            continue
        document = load_deployment(document=documents[Path(command[2])], environ={})
        source = expected_source(configuration_, compose, name)
        allowed = {fields[2] for fields in rules if fields[3] == source}
        assert set(held_logins(document)) == allowed, name
        checked += 1
    # Web, its installers and background services, and the five one-shots.
    assert checked == 5 + 12 + 5


def test_a_probe_sends_no_password_and_classifies_the_servers_answer(monkeypatch):
    """Allowed, refused, accepted without a password, or unknown."""
    calls, answers = [], []

    def connect(**options):
        """Record the connection options and answer as the server would."""
        calls.append(options)
        answer = answers.pop(0)
        if answer is None:
            return SimpleNamespace(close=lambda: None)
        raise psycopg.OperationalError(answer)

    monkeypatch.setattr(psycopg, "connect", connect)
    passfile = Path("/nonexistent/pgpass")
    for answer, outcome in (
        (ALLOWED, "allowed"),
        (REFUSED, "refused"),
        (None, "trust"),
        ("connection refused", "unknown"),
        (ALLOWED + "\n" + REFUSED, "unknown"),
        # A server error is never libpq's own prompt, whatever it quotes.
        ("FATAL:  no pg_hba.conf entry; " + ALLOWED, "unknown"),
    ):
        answers.append(answer)
        assert sources.probe(configuration(), "pk_stewardship_web", passfile) == outcome
    for options in calls:
        assert "password" not in options
        assert options["passfile"] == str(passfile)
        assert options["sslmode"] == "disable"
        assert options["gssencmode"] == "disable"
        assert options["user"] == "pk_stewardship_web"
        assert options["dbname"] == "stewardship"


def test_libpqs_environment_is_cleared_and_nothing_else():
    """No PGPASSWORD, PGPASSFILE or PGSERVICE can reach a probe."""
    environ = {
        "PGPASSWORD": "x",
        "PGPASSFILE": "/run/secret",
        "PGSERVICE": "s",
        "PATH": "/bin",
        "PARISHKIT_DEBUG_LOGGING": "0",
    }
    sources.clear_libpq_environment(environ)
    assert environ == {"PATH": "/bin", "PARISHKIT_DEBUG_LOGGING": "0"}


def test_only_differences_from_the_rendered_row_are_mismatches():
    """An unexpected allowed or any trust is a security failure."""
    expected = {"pk_stewardship_web", "pk_stewardship_download"}
    results = {
        "pk_stewardship_web": "allowed",
        "pk_stewardship_download": "refused",
        "pk_stewardship_worker": "refused",
        OPERATOR: "allowed",
        "pk_stewardship_migration": "trust",
        "pk_stewardship_scheduler": "unknown",
    }
    found = {login: why for login, _, why in sources.mismatches(results, expected)}
    assert set(found) == {
        "pk_stewardship_download",
        OPERATOR,
        "pk_stewardship_migration",
        "pk_stewardship_scheduler",
    }
    assert "security failure" in found[OPERATOR]
    assert "security failure" in found["pk_stewardship_migration"]
    assert "security failure" not in found["pk_stewardship_download"]


def run_check(monkeypatch, capsys, outcomes, document):
    """Run check_sources with stand-in probes; returns (status, out, err).

    ``outcomes`` maps logins to a non-default outcome; every other login is
    refused. The probes record that libpq's environment was already cleared
    and that the password file was empty and private when they ran.
    """
    seen = []

    def probe(configuration_, login, passfile):
        """Answer from ``outcomes`` and record what the probe would have used."""
        seen.append(
            (
                "PGPASSWORD" in os.environ,
                passfile.read_bytes(),
                passfile.stat().st_mode & 0o077,
            )
        )
        return outcomes.get(login, "refused")

    monkeypatch.setattr(sources, "probe", probe)
    monkeypatch.setattr(sources, "source_address", lambda _: "172.29.240.4")
    monkeypatch.setenv("PGPASSWORD", "from-the-environment")
    status = sources.check_sources(document)
    assert seen and set(seen) == {(False, b"", 0)}
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_the_check_passes_only_on_the_rendered_row(monkeypatch, capsys):
    """Web holds two logins; every other one must be refused."""
    web = configuration(ServiceRole.WEB, "pk_stewardship_web")
    allowed = {"pk_stewardship_web": "allowed", "pk_stewardship_download": "allowed"}
    status, out, err = run_check(monkeypatch, capsys, allowed, web)
    assert status == 0
    assert err == ""
    assert "pk_stewardship_web allowed from 172.29.240.4 (web)" in out
    assert f"{OPERATOR} refused from 172.29.240.4 (web)" in out
    assert out.splitlines()[-1] == (
        "database sources match the rendered rules from 172.29.240.4 (web): "
        f"2 allowed, {len(INVENTORY) - 2} refused"
    )


def test_a_difference_names_the_login_source_and_service(monkeypatch, capsys):
    """An operator allowed from a worker fails the check as a security failure."""
    status, _, err = run_check(
        monkeypatch,
        capsys,
        {"pk_stewardship_worker": "allowed", OPERATOR: "allowed"},
        configuration(),
    )
    assert status == 1
    assert (
        f"MISMATCH: {OPERATOR} is allowed from 172.29.240.4 (worker): the rendered "
        "rules refuse it (security failure)"
    ) in err
    assert err.splitlines()[-1] == (
        "ERROR: database sources differ from the rendered rules from "
        "172.29.240.4 (worker)"
    )


def test_a_refused_own_login_and_installer_names(monkeypatch, capsys):
    """An installer whose own login is refused names itself by its target."""
    installer = configuration(
        ServiceRole.CREDENTIAL_INSTALLER,
        "pk_stewardship_credential_google_oauth",
        target="google_oauth",
    )
    status, _, err = run_check(monkeypatch, capsys, {}, installer)
    assert status == 1
    assert (
        "MISMATCH: pk_stewardship_credential_google_oauth is refused from "
        "172.29.240.4 (credential-installer-google-oauth): the rendered rules "
        "allow it"
    ) in err


def test_the_source_address_sends_nothing(monkeypatch):
    """A connected UDP socket only asks the kernel for the route."""
    import socket

    calls = []

    class Route:
        """A socket stand-in that records what is done with it."""

        def __init__(self, *arguments):
            calls.append(("socket", arguments))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def connect(self, address):
            calls.append(("connect", address))

        def getsockname(self):
            return ("172.29.240.13", 40000)

        def send(self, *_):
            raise AssertionError("nothing may be sent")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, type: [(2, type, 17, "", ("172.29.240.2", port))],
    )
    monkeypatch.setattr(socket, "socket", Route)
    assert sources.source_address(configuration()) == "172.29.240.13"
    assert calls == [
        ("socket", (2, socket.SOCK_DGRAM, 17)),
        ("connect", ("172.29.240.2", 5432)),
    ]


def test_the_hba_query_tests_each_condition_in_order():
    """Mounted path, rendered digest, no error row, then the reload's time.

    The load is compared with PostgreSQL's own time from just before the
    reload (a psql variable), never with the file's mtime from another clock.
    """
    query = sources.hba_query(b"rules\n")
    digest = hashlib.sha256(b"rules\n").hexdigest()
    positions = [
        query.index(f"current_setting('hba_file') <> '{HBA_FILE}'"),
        query.index(f"'hex') <> '{digest}'"),
        query.index("pg_hba_file_rules WHERE error IS NOT NULL"),
        query.index("pg_conf_load_time() <= :'since'::timestamptz"),
        query.index("ELSE 't'"),
    ]
    assert positions == sorted(positions)
    answers = [query.index(f"THEN '{answer}'") for answer in sources.HBA_ANSWERS]
    assert answers == sorted(answers)
    # A read-only query: nothing that writes or reloads.
    assert "pg_reload_conf" not in query
    assert "pg_stat_file" not in query
    assert query.startswith("SELECT CASE")


def test_the_hba_check_digests_the_file_retarget_writes(
    deployment,  # noqa: F811 (the fixture)
    tmp_path,
    monkeypatch,
    capsys,
):
    """The CLI renders the same bytes retarget-image put in the mounted directory."""
    from parishkit.stewardship.accounts.key_files import read_private

    written = read_private(RuntimeLayout(deployment).hba_directory / "pg_hba.conf")
    assert sources.rendered_hba(deployment) == written
    monkeypatch.setenv("PARISHKIT_ROOT", str(tmp_path / "rt"))
    config = tmp_path / "operator.yaml"
    config.write_text("deployment: {schema_version: 1}\n", encoding="utf-8")
    assert main(["hba-check", "--config", str(config)]) == 0
    assert capsys.readouterr().out == sources.hba_query(written)


@pytest.mark.parametrize(
    "argv",
    [
        ["hba-check"],
        ["database-sources"],
        ["hba-check", "--config", "/nonexistent/deployment.yaml"],
        ["database-sources", "--config", "/nonexistent/deployment.yaml"],
    ],
)
def test_the_commands_refuse_without_a_usable_configuration(argv, capsys):
    """One generic line and exit 2; no configuration detail on the console."""
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"ERROR: {argv[0]} refused; check the configuration\n"


def test_the_commands_take_no_other_options(capsys):
    """An option of another command is a usage error before anything runs."""
    with pytest.raises(SystemExit) as raised:
        main(["database-sources", "--config", "x", "--image", "y"])
    assert raised.value.code == 2
    assert "options not supported by database-sources: --image" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize("name", ["fe_sendauth", "x pg_hba y", "FATAL"])
def test_a_database_name_holding_a_marker_is_refused(monkeypatch, name):
    """The server quotes the name, so it must not hold a classification marker."""
    document = configuration()
    document.postgres.name = name
    monkeypatch.setattr(sources, "probe", lambda *_: pytest.fail("probed"))
    with pytest.raises(sources.ConfigError, match="cannot be probed"):
        sources.check_sources(document)
