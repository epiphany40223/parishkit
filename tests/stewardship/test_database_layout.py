"""The full stop for PostgreSQL's connection sources (#2111), on real Docker.

The release that adds the source rules changes the database networks:
fixed ``backend`` addresses, a dynamic ``ip_range``, a one-shot network per
profile and PostgreSQL's ``hba_file``. Compose can neither recreate a
network a running container is on nor attach a stopped container to the
recreated one, so the upgrade (and the rollback, in the other direction)
removes every container but caddy and starts postgres and valkey again on
the rendered networks. ``tools/stewardship-upgrade-host.sh`` and
``tools/stewardship-ops/pk-restore`` share that code as one block; the
first test keeps the two copies identical, and the opted-in one runs the
block for a rollout and then a rollback on the real Production topology.
"""

import ipaddress
import json
import os
import random
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from parishkit.stewardship.runtime_hba import HBA_FILE
from parishkit.stewardship.runtime_network import RuntimeNetwork
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import render_runtime

from .test_runtime_topology import IMAGE as PRODUCTION_IMAGE
from .test_runtime_topology import configuration_at

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "tools" / "stewardship-upgrade-host.sh"
RESTORE = ROOT / "tools" / "stewardship-ops" / "pk-restore"
BEGIN = "# BEGIN database network layout (#2111)."
END = "# END database network layout (#2111).\n"
HBA_BEGIN = "# BEGIN PostgreSQL source rules check (#2111)."
HBA_END = "# END PostgreSQL source rules check (#2111).\n"


def layout_block(path, begin=BEGIN, end=END):
    """One shared block's text in one script (the layout block by default)."""
    text = path.read_text()
    return text[text.index(begin) : text.index(end) + len(end)]


def hba_block(path):
    """The shared source rules check block's text in one script."""
    return layout_block(path, HBA_BEGIN, HBA_END)


def test_both_scripts_carry_the_same_layout_block():
    """The upgrade and pk-restore must decide and stop the same way."""
    assert layout_block(HOST) == layout_block(RESTORE)
    # And reload and check the source rules the same way, after the layout.
    assert hba_block(HOST) == hba_block(RESTORE)
    for path in (HOST, RESTORE):
        text = path.read_text()
        call = text.index("hba_reload", text.index(HBA_END))
        assert text.rindex("changes=$(layout_changes)") < call
    # Called after the retarget in both; the upgrade also finishes an
    # earlier run's full stop before its step 1 needs PostgreSQL.
    call = "changes=$(layout_changes)"
    for path, after in ((HOST, "retarget-image --config"), (RESTORE, "retarget-image")):
        text = path.read_text()
        assert call in text[text.index(after, text.index(END)) :]
    text = HOST.read_text()
    assert text.index(END) < text.index(call, text.index(END)) < text.index("# Step 1:")


runtime = pytest.mark.skipif(
    os.environ.get("PARISHKIT_RUN_RUNTIME_TESTS") != "1",
    reason="Requires explicitly opted-in disposable Docker runtime validation",
)


def docker(*arguments, check=True, timeout=180, **options):
    """One Docker command; returns the completed process."""
    result = subprocess.run(
        ["docker", *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        **options,
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def free_networks():
    """Three private /24s no Docker network on this host overlaps."""
    ids = docker("network", "ls", "--quiet").stdout.split()
    used = [
        ipaddress.ip_network(config["Subnet"])
        for network in json.loads(docker("network", "inspect", *ids).stdout)
        for config in (network["IPAM"]["Config"] or [])
        if ":" not in config.get("Subnet", ":")
    ]
    for third in random.sample(range(256), 256):
        blocks = [
            ipaddress.ip_network(f"10.{second}.{third}.0/24")
            for second in (211, 212, 213)
        ]
        if not any(block.overlaps(other) for block in blocks for other in used):
            return [str(block) for block in blocks]
    pytest.fail("no free private /24 for the test's networks")


def old_rendering(new):
    """The release before the source rules, derived from the new rendering.

    No one-shot networks, no gateway or ip_range, no fixed backend address,
    and PostgreSQL only on backend with neither hba_file nor its mount, as
    the Production golden file pinned it before #2111.
    """
    old = json.loads(json.dumps(new))
    old["networks"] = {
        k: v for k, v in old["networks"].items() if not k.startswith("offline-")
    }
    for network in old["networks"].values():
        for config in network.get("ipam", {}).get("config", []):
            config.pop("gateway", None)
            config.pop("ip_range", None)
    for service in old["services"].values():
        networks = {
            k: v for k, v in service["networks"].items() if not k.startswith("offline-")
        }
        if "backend" in networks:
            networks["backend"] = {}
        service["networks"] = networks or {"backend": {}}
    postgres = old["services"]["postgres"]
    command = postgres["command"]
    at = command.index(f"hba_file={HBA_FILE}")
    postgres["command"] = command[: at - 1] + command[at + 1 :]
    postgres["volumes"] = [
        v for v in postgres["volumes"] if "pg-hba" not in v["source"]
    ]
    return old


def rendered_pair(root):
    """The new and the old Compose documents, the hba file and the paths.

    PostgreSQL is the rendered service, with its real command, mounts and
    networks. Valkey, web, caddy and the migration one-shot keep their
    rendered networks and run stand-ins (Valkey without its ACL; the others
    the PostgreSQL image, for psql), since only the networks matter here.
    """
    configuration = configuration_at(root, production=True)
    backend, proxy, offline = free_networks()
    configuration = replace(
        configuration,
        runtime_network=RuntimeNetwork(backend=backend, proxy=proxy, offline=offline),
    )
    compose, documents = render_runtime(configuration, image=PRODUCTION_IMAGE)
    hba = documents[RuntimeLayout(configuration).hba_directory / "pg_hba.conf"]
    rendered = compose["services"]
    postgres = rendered["postgres"]
    image = postgres["image"]

    def stand_in(name, **extra):
        """A service with its rendered networks running a stand-in command.

        A tmpfs over the image's VOLUME path, so removing the container (as
        the full stop does, without its volumes) leaves no anonymous volume.
        """
        return {
            "image": image,
            "entrypoint": ["sleep", "infinity"],
            "networks": rendered[name]["networks"],
            "tmpfs": ["/var/lib/postgresql"],
            **extra,
        }

    services = {
        "postgres": postgres,
        "valkey": {
            "image": rendered["valkey"]["image"],
            "command": ["valkey-server", "--save", "", "--appendonly", "no"],
            "networks": rendered["valkey"]["networks"],
            "tmpfs": ["/data"],
            "healthcheck": {
                "test": ["CMD", "valkey-cli", "ping"],
                "interval": "2s",
                "retries": 30,
            },
        },
        "web": stand_in("web"),
        "caddy": stand_in("caddy"),
        "migration": stand_in("migration", entrypoint=["psql"], profiles=["migration"]),
    }
    names = {key for service in services.values() for key in service["networks"]}
    new = {
        "services": services,
        "networks": {k: v for k, v in compose["networks"].items() if k in names},
    }
    return new, old_rendering(new), hba, configuration


def probe(*command):
    """psql's answer to a sign-in with no password: allowed, refused or the text."""
    result = docker(*command, "-w", "-c", "SELECT 1", check=False, timeout=60)
    text = result.stdout + result.stderr
    if "no password supplied" in text:
        return "allowed"
    if "pg_hba.conf rejects connection" in text:
        return "refused"
    return text


@runtime
def test_a_rollout_then_a_rollback_keeps_the_data(tmp_path):
    """Both directions recreate the stores, keep caddy and change no row."""
    root = tmp_path / "root"
    new, old, hba, configuration = rendered_pair(root)
    database = new["services"]["postgres"]["environment"]["POSTGRES_DB"]
    layout = RuntimeLayout(configuration)
    store = configuration.paths["postgresql"]
    store.mkdir(parents=True)
    store.chmod(0o777)  # the container's 10001 creates its data directory
    password = layout.database_password("operator")
    password.parent.mkdir(parents=True)
    password.write_text("disposable-operator-password\n")
    password.chmod(0o644)
    layout.hba_directory.mkdir(parents=True)
    layout.hba_directory.chmod(0o755)
    (layout.hba_directory / "pg_hba.conf").write_text(hba)
    (layout.hba_directory / "pg_hba.conf").chmod(0o644)
    files = {name: tmp_path / f"{name}.json" for name in ("old", "new")}
    files["old"].write_text(json.dumps(old))
    files["new"].write_text(json.dumps(new))
    project = f"pk-2111c-{uuid4().hex[:8]}"
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "set -euo pipefail\n"
        + layout_block(HOST)
        + "project=$1 compose=$2 work=$3\n"
        + 'dc=(docker compose -f "$compose" -p "$project")\n'
        + "case $4 in changes) layout_changes ;; full-stop) layout_full_stop ;; esac\n"
    )
    work = tmp_path / "work"
    work.mkdir()

    def compose(name, *arguments, **options):
        """docker compose on one of the two renderings."""
        return docker(
            "compose", "-f", str(files[name]), "-p", project, *arguments, **options
        )

    def run_layout(name, action):
        """The shared block's comparison or full stop against one rendering."""
        result = subprocess.run(
            ["bash", str(driver), project, str(files[name]), str(work), action],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def operator(sql):
        """One query as the operator over PostgreSQL's socket."""
        return compose(
            "current",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "pk_stewardship_operator",
            "-d",
            database,
            "-At",
            "-c",
            sql,
        ).stdout.strip()

    def caddy():
        """caddy's container id and start time: it must never restart."""
        (state,) = json.loads(docker("inspect", f"{project}-caddy-1").stdout)
        return state["Id"], state["State"]["StartedAt"], state["State"]["Running"]

    def backend_probe(address, login="pk_stewardship_web"):
        """A container on backend without --ip: a dynamic address."""
        return probe(
            "run",
            "--rm",
            "--network",
            f"{project}_backend",
            "--entrypoint",
            "psql",
            new["services"]["postgres"]["image"],
            f"host={address} dbname={database} user={login} connect_timeout=10",
        )

    postgres_address = new["services"]["postgres"]["networks"]["backend"][
        "ipv4_address"
    ]
    try:
        files["current"] = files["old"]
        compose(
            "old",
            "up",
            "--detach",
            "--wait",
            "postgres",
            "valkey",
            "web",
            "caddy",
            timeout=300,
        )
        operator(
            "CREATE TABLE layout_probe (v text);"
            " INSERT INTO layout_probe VALUES ('kept'), ('too')"
        )
        before = operator(
            "SELECT count(*), md5(string_agg(v, ',' ORDER BY v)) FROM layout_probe"
        )
        started = caddy()
        assert run_layout("old", "changes") == ""
        # The stock rules admit a dynamic address.
        assert backend_probe("postgres") == "allowed"

        # The rollout: the new rendering differs, the full stop moves to it.
        changes = run_layout("new", "changes")
        assert "network backend differs from the rendered one (ip_range)" in changes
        assert "postgres is on backend but rendered on backend offline-" in changes
        run_layout("new", "full-stop")
        files["current"] = files["new"]
        compose("new", "up", "--detach", "--wait", "web", timeout=300)
        assert run_layout("new", "changes") == ""
        assert caddy() == started
        assert (
            operator(
                "SELECT count(*), md5(string_agg(v, ',' ORDER BY v)) FROM layout_probe"
            )
            == before
        )
        assert operator("SHOW hba_file") == HBA_FILE
        connect = f"host=postgres dbname={database} connect_timeout=10"
        assert (
            probe(
                "compose",
                "-f",
                str(files["new"]),
                "-p",
                project,
                "exec",
                "-T",
                "web",
                "psql",
                f"{connect} user=pk_stewardship_web",
            )
            == "allowed"
        )
        assert backend_probe(postgres_address) == "refused"
        assert (
            probe(
                "compose",
                "-f",
                str(files["new"]),
                "-p",
                project,
                "run",
                "--rm",
                "-T",
                "migration",
                f"{connect} user=pk_stewardship_migration",
            )
            == "allowed"
        )
        assert (
            probe(
                "compose",
                "-f",
                str(files["new"]),
                "-p",
                project,
                "run",
                "--rm",
                "-T",
                "migration",
                f"{connect} user=pk_stewardship_web",
            )
            == "refused"
        )

        # The rollback: the old rendering differs again, and the same full
        # stop puts PostgreSQL back on its stock rules. caddy is stopped
        # first, as pk-restore's step 1 leaves it: a stopped caddy keeps
        # its networks and starts again as the same container.
        assert "ip_range" in run_layout("old", "changes")
        compose("new", "stop", "caddy")
        run_layout("old", "full-stop")
        files["current"] = files["old"]
        compose("old", "up", "--detach", "--wait", "web", "caddy", timeout=300)
        assert run_layout("old", "changes") == ""
        assert caddy()[0] == started[0] and caddy()[2]
        assert (
            operator(
                "SELECT count(*), md5(string_agg(v, ',' ORDER BY v)) FROM layout_probe"
            )
            == before
        )
        assert operator("SHOW hba_file").endswith("/pg_hba.conf")
        assert operator("SHOW hba_file") != HBA_FILE
        assert backend_probe("postgres") == "allowed"
        networks = docker(
            "network",
            "ls",
            "--format",
            "{{.Name}}",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ).stdout.split()
        assert not any("offline-" in name for name in networks)
    finally:
        ids = docker(
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={project}",
            check=False,
        ).stdout.split()
        if ids:
            docker("rm", "--force", "--volumes", *ids, check=False)
        for network in docker(
            "network",
            "ls",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={project}",
            check=False,
        ).stdout.split():
            docker("network", "rm", network, check=False)
        # The data directory belongs to the container's user; remove it as root.
        if store.exists():
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--entrypoint",
                "sh",
                "--mount",
                f"type=bind,source={store},target=/store",
                new["services"]["postgres"]["image"],
                "-c",
                "rm -rf /store/*",
                check=False,
            )
        shutil.rmtree(root, ignore_errors=True)
