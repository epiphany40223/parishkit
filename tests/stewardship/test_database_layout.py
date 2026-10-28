"""The full stop for PostgreSQL's connection sources (#2111), on real Docker.

The release that adds the source rules changes the database networks:
fixed ``backend`` addresses, a dynamic ``ip_range``, a one-shot network per
profile and PostgreSQL's ``hba_file``. Compose can neither recreate a
network a running container is on nor attach a stopped container to the
recreated one, so the upgrade (and the rollback, in the other direction)
removes every container but caddy and starts postgres and valkey again on
the rendered networks. ``tools/stewardship-upgrade-host.sh`` and
``tools/stewardship-ops/pk-restore`` share that code as one block, and
the reload and hba check that follow it as a second; the first test keeps
both pairs of copies identical. The opted-in ones run the blocks on real
Docker: a rollout and then a rollback on the real Production topology
with stand-ins for the services, and the combined upgrade path with the
application image, from a root it provisions: the full stop, the hba
check in its isolated container against a real reload, and
``database-sources`` from every service, in both directions.
"""

import ipaddress
import json
import os
import random
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from parishkit.stewardship.deployment import DeploymentProfile
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
# The off-site uploader's two shared blocks (#235): the crontab collection
# (pk-restore's job check and the expand rollback's preflight) and the host's
# copy lock (the upgrade's, the restore's and the drill's offline work).
CRON_BEGIN = "# BEGIN host crontab collection (#235)."
CRON_END = "# END host crontab collection (#235).\n"
LOCK_BEGIN = "# BEGIN off-site copy lock (#235)."
LOCK_END = "# END off-site copy lock (#235).\n"


def layout_block(path, begin=BEGIN, end=END):
    """One shared block's text in one script (the layout block by default)."""
    text = path.read_text()
    return text[text.index(begin) : text.index(end) + len(end)]


def hba_block(path):
    """The shared source rules check block's text in one script."""
    return layout_block(path, HBA_BEGIN, HBA_END)


def test_both_scripts_carry_the_same_crontab_and_copy_lock_blocks():
    """The upgrade and pk-restore read the crontabs and take the lock alike.

    pk-restore's job check keeps its own filter over the shared collection,
    and the upgrade its uploader-line filter; both take the copy lock
    through the one function.
    """
    for begin, end in ((CRON_BEGIN, CRON_END), (LOCK_BEGIN, LOCK_END)):
        assert layout_block(HOST, begin, end) == layout_block(RESTORE, begin, end)
    restore = RESTORE.read_text()
    check_jobs = restore[restore.index("check_jobs() {") :]
    check_jobs = check_jobs[: check_jobs.index("\n}\n")]
    assert "cron_lines" in check_jobs and "crontab -l" not in check_jobs
    assert "uploader_cron_lines" in HOST.read_text().split(CRON_END, 1)[1]
    for path in (HOST, RESTORE):
        assert "copy_lock_take " in path.read_text().split(LOCK_END, 1)[1]


def test_the_crontab_block_finds_only_uncommented_uploader_lines(tmp_path):
    """Run the shared block itself over every crontab it reads."""
    if shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script = tmp_path / "cron.sh"
    script.write_text(
        layout_block(HOST, CRON_BEGIN, CRON_END)
        + 'echo "all:"; cron_lines; echo "uploader:"; uploader_cron_lines\n'
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "crontab").write_text(
        "#!/usr/bin/env bash\n"
        'echo "*/5 * * * * run --rm -T backup-uploader"\n'
        'echo "# */5 * * * * run --rm -T backup-uploader commented"\n'
    )
    (bin_dir / "crontab").chmod(0o755)
    (tmp_path / "crontab").write_text(
        "0 3 * * * root run --rm -T backup-worker\n"
        "  # 0 4 * * * root backup-uploader indented comment\n"
    )
    (tmp_path / "cron.d").mkdir()
    (tmp_path / "cron.d" / "other").write_text(
        "0 5 * * * root my-backup-uploader-copy\n"
        "0 6 * * * root run backup-uploader upload --config x\n"
    )
    result = subprocess.run(
        ["bash", str(script)],
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "SUDO_USER": "operator",
            "PK_RESTORE_CRONTAB": str(tmp_path / "crontab"),
            "PK_RESTORE_CRON_DIR": str(tmp_path / "cron.d"),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    everything, uploader = result.stdout.split("uploader:\n")
    # root's and SUDO_USER's crontabs, then /etc/crontab and cron.d.
    assert everything.splitlines()[1:] == [
        "*/5 * * * * run --rm -T backup-uploader",
        "*/5 * * * * run --rm -T backup-uploader",
        "0 3 * * * root run --rm -T backup-worker",
        "0 5 * * * root my-backup-uploader-copy",
        "0 6 * * * root run backup-uploader upload --config x",
    ]
    assert uploader.splitlines() == [
        "*/5 * * * * run --rm -T backup-uploader",
        "*/5 * * * * run --rm -T backup-uploader",
        "0 6 * * * root run backup-uploader upload --config x",
    ]


@pytest.mark.skipif(shutil.which("flock") is None, reason="util-linux flock only")
def test_the_copy_lock_block_waits_for_a_held_lock_and_logs_its_expiry(tmp_path):
    """On a host with flock: the block takes both locks, or times out logged.

    A Python copier holding the directory lock (as the uploader holds it)
    makes the block's bounded wait expire; released, the block takes both
    and creates the legacy file owner-only.
    """
    import fcntl

    backups = tmp_path / "backups"
    backups.mkdir(mode=0o700)
    script = tmp_path / "lock.sh"
    script.write_text(
        layout_block(HOST, LOCK_BEGIN, LOCK_END)
        + 'if copy_lock_take "$1" 2; then echo held; else echo refused; fi\n'
    )
    descriptor = os.open(backups, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        result = subprocess.run(
            ["bash", str(script), str(backups)], capture_output=True, text=True
        )
    finally:
        os.close(descriptor)
    assert result.stdout.strip() == "refused"
    assert "TIMEOUT: waiting for the off-site copy lock" in result.stderr
    assert "(limit 2s)" in result.stderr
    result = subprocess.run(
        ["bash", str(script), str(backups)], capture_output=True, text=True
    )
    assert result.stdout.strip() == "held"
    lock = backups / ".offsite-copy.lock"
    assert lock.is_file() and (lock.stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("plant", ["link", "fifo", "none"])
def test_a_lock_file_swapped_before_the_open_refuses_without_hanging(tmp_path, plant):
    """A link or FIFO swapped in after the checks is never locked (#235, L1).

    The backup worker can write the backups directory, so the name may
    change between the block's checks and its open. A ``umask`` stand-in
    (run just before the open) swaps the lock file at that moment: the
    read-write open never blocks on a FIFO, and the opened descriptor is
    not the regular file at the name, so the block refuses before flock.
    The run is bounded, so a hang fails the test instead of stalling it.
    """
    from .host_stubs import install_gnu_tools

    if shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o700)
    lock = backups / ".offsite-copy.lock"
    lock.write_text("")
    target = tmp_path / "elsewhere"
    target.write_text("kept")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    flock = bin_dir / "flock"
    flock.write_text('#!/usr/bin/env bash\necho "$*" >>"$FLOCK_LOG"\n')
    flock.chmod(0o755)
    install_gnu_tools(bin_dir)
    script = tmp_path / "lock.sh"
    script.write_text(
        layout_block(HOST, LOCK_BEGIN, LOCK_END)
        + "umask() {\n"
        + '    if [ "$#" -gt 0 ] && [ -n "$PLANT" ] && [ ! -e "$TARGET.done" ]; then\n'
        + '        : >"$TARGET.done"; rm -f "$LOCK"\n'
        + '        if [ "$PLANT" = link ]; then ln -s "$TARGET" "$LOCK"; '
        + 'else mkfifo "$LOCK"; fi\n'
        + "    fi\n"
        + '    builtin umask "$@"\n'
        + "}\n"
        + 'if copy_lock_take "$1" 2; then echo held; else echo refused; fi\n'
    )
    log = tmp_path / "flock.log"
    result = subprocess.run(
        ["bash", str(script), str(backups)],
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PLANT": "" if plant == "none" else plant,
            "LOCK": str(lock),
            "TARGET": str(target),
            "FLOCK_LOG": str(log),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    if plant == "none":
        assert result.stdout.strip() == "held", result.stderr
        assert log.read_text().splitlines() == ["-E 75 -w 2 8", "-E 75 -w 2 9"]
        return
    assert result.stdout.strip() == "refused"
    assert "is not a regular file" in result.stderr
    assert not log.exists()
    assert target.read_text() == "kept"


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
        # A one-shot (the backup worker keeps application-egress too) was
        # on backend before it had its own network.
        if "backend" in networks or len(networks) < len(service["networks"]):
            networks["backend"] = {}
        service["networks"] = networks
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
        # Docker on Linux also reports the gateway the old network was given.
        assert re.search(
            r"network backend differs from the rendered one \(ip_range[,)]", changes
        )
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
        # The inspection login (#2148) signs in over the socket to the
        # deployment's database only; the rendered rule admits nothing else.
        operator("CREATE ROLE pk_stewardship_inspect LOGIN")

        def inspect_socket(name):
            """One ``SELECT 1`` as the inspection login over the socket."""
            return compose(
                "current",
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "pk_stewardship_inspect",
                "-d",
                name,
                "-At",
                "-c",
                "SELECT 1",
                check=False,
            )

        assert inspect_socket(database).stdout.strip() == "1"
        assert "pg_hba.conf rejects connection" in inspect_socket("postgres").stderr
        operator("DROP ROLE pk_stewardship_inspect")
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


# The combined upgrade path (#2111 slices a, b and c) with the application
# image: a deployment provisioned by the image itself, the full stop, then
# the hba check rendered in its isolated container and a real reload, then
# `database-sources` from every service. The image is the development one
# CI's runtime step builds; PARISHKIT_RUNTIME_TEST_IMAGE names another
# local build.
APPLICATION_IMAGE = os.environ.get(
    "PARISHKIT_RUNTIME_TEST_IMAGE", "parishkit-stewardship:development"
)


def isolated_definition():
    """The upgrade host's `isolated=(...)` array, as the script defines it."""
    text = HOST.read_text()
    start = text.index("isolated=(docker run")
    return text[start : text.index(")\n", start) + 2]


def provisioned_root(volume, networks):
    """Provision a Production root in a fresh native volume with the image.

    The volume is mounted at its own daemon-side path, so the runtime root,
    every rendered bind source and the host scripts' `source=$root` mounts
    are all one path, as on a Production host. Returns that root, the
    operator document's path, the rendered Compose document and every path
    provisioning created.
    """
    from parishkit.stewardship.deployment import load_deployment
    from parishkit.stewardship.deployment_documents import deployment_document

    docker("volume", "create", "--label", "parishkit.test=2111", volume)
    mountpoint = docker(
        "volume", "inspect", "--format", "{{.Mountpoint}}", volume
    ).stdout.strip()
    root = Path(mountpoint) / "runtime"
    backend, proxy, offline = networks
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(root)})
    configuration = replace(
        configuration,
        profile=DeploymentProfile.PRODUCTION,
        public_origin="https://parish.example",
        trusted_proxy_hops=1,
        runtime_network=RuntimeNetwork(backend=backend, proxy=proxy, offline=offline),
    )
    document = json.dumps(deployment_document(configuration))
    at_volume = ("--mount", f"type=volume,source={volume},target={mountpoint}")
    docker(
        "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "sh",
        *at_volume, "-e", "DOCUMENT=" + document, APPLICATION_IMAGE, "-c",
        f'cd "{mountpoint}" && printf %s "$DOCUMENT" >operator.json && mkdir runtime'
        " && chown -R 10001:10001 . && chmod 700 . runtime && chmod 600 operator.json",
    )  # fmt: skip
    yaml = f"{mountpoint}/operator.json"
    isolated = isolated_definition()
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail\n{isolated}"${{isolated[@]}}" "$@"',
            "provision",
            *at_volume,
            APPLICATION_IMAGE,
            "provision-runtime",
            "--config",
            yaml,
            "--image",
            PRODUCTION_IMAGE,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    compose = json.loads(
        docker(
            "run", "--rm", "--network", "none", "--user", "10001:10001",
            "--entrypoint", "cat", *at_volume, APPLICATION_IMAGE,
            f"{root}/config/services/compose.json",
        ).stdout
    )  # fmt: skip
    present = docker(
        "run", "--rm", "--network", "none", "--user", "10001:10001",
        "--entrypoint", "find", *at_volume, APPLICATION_IMAGE, str(root),
    ).stdout.splitlines()  # fmt: skip
    return root, yaml, compose, set(present)


def probe_rendering(compose, present):
    """The rendered Compose document, as the probes can start it.

    PostgreSQL and Valkey are the rendered services. Every application
    service keeps its rendered networks (so its fixed addresses), user,
    hardening and every mount whose source provisioning created (`present`),
    and runs the local image; the provider credentials and stores that
    bootstrap and the installers create later are not here. Caddy is left
    out (the other runtime test keeps it).
    """
    services = {}
    for name, service in compose["services"].items():
        if name in ("postgres", "valkey"):
            services[name] = service
        elif service.get("command", [None, None])[1:2] == ["--config"]:
            service = dict(service, image=APPLICATION_IMAGE)
            service.pop("depends_on", None)
            service["volumes"] = [
                mount for mount in service["volumes"] if mount["source"] in present
            ]
            services[name] = service
    used = {key for service in services.values() for key in service["networks"]}
    return {
        "services": services,
        "networks": {k: v for k, v in compose["networks"].items() if k in used},
    }


@runtime
def test_the_upgrade_path_proves_the_rules_from_every_service(tmp_path):
    """Rollout and rollback: hba check, real reload, then every service's probe."""
    from parishkit.stewardship.database_sources import inventory

    volume = f"pk-2111c-{uuid4().hex[:8]}"
    project = volume
    try:
        root, yaml, rendered, present = provisioned_root(volume, free_networks())
        new = probe_rendering(rendered, present)
        old = old_rendering(new)
        database = new["services"]["postgres"]["environment"]["POSTGRES_DB"]
        files = {name: tmp_path / f"{name}.json" for name in ("old", "new")}
        files["old"].write_text(json.dumps(old))
        files["new"].write_text(json.dumps(new))
        work = tmp_path / "work"
        work.mkdir()
        driver = tmp_path / "driver.sh"
        driver.write_text(
            "set -euo pipefail\n"
            + isolated_definition()
            + layout_block(HOST)
            + hba_block(HOST)
            + "project=$1 compose=$2 work=$3 database=$4 root=$5 yaml=$6 image=$7\n"
            + 'dc=(docker compose -f "$compose" -p "$project")\n'
            + "case $8 in\n"
            + "    changes) layout_changes ;;\n"
            + "    full-stop) layout_full_stop ;;\n"
            + "    rendered) hba_rendered ;;\n"
            + '    reload) hba_render && hba_reload && echo "since=$hba_since" ;;\n'
            + '    answer) hba_answer "$9" ;;\n'
            + "esac\n"
        )

        def shared(name, action, *extra, status=0):
            """One shared-block action against one rendering."""
            result = subprocess.run(
                ["bash", str(driver), project, str(files[name]), str(work)]
                + [database, str(root), yaml, APPLICATION_IMAGE, action, *extra],
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            assert result.returncode == status, result.stdout + result.stderr
            return result.stdout

        def sources(name):
            """`database-sources` from every application service, by `run`.

            `compose run` gives each container its service's networks and
            fixed addresses, as the upgrade host's one-shot checks have.
            """
            outcomes = {}
            for service, value in json.loads(files[name].read_text())[
                "services"
            ].items():
                if service in ("postgres", "valkey"):
                    continue
                result = docker(
                    "compose", "-f", str(files[name]), "-p", project, "run",
                    "--rm", "--no-deps", "-T", service, "database-sources",
                    "--config", value["command"][2], check=False, timeout=180,
                )  # fmt: skip
                outcomes[service] = result
            return outcomes

        docker(
            "compose", "-f", str(files["old"]), "-p", project, "up", "--detach",
            "--wait", "postgres", "valkey", timeout=300,
        )  # fmt: skip
        assert shared("old", "changes") == ""
        assert shared("old", "rendered") == ""

        # The rollout: the full stop, then the hba check rendered by the
        # image in its isolated container, a real reload, a real wait.
        assert "ip_range" in shared("new", "changes")
        shared("new", "full-stop")
        assert shared("new", "rendered").strip() == f"hba_file={HBA_FILE}"
        reloaded = shared("new", "reload")
        assert "the rendered source rules are in force" in reloaded
        since = reloaded.strip().splitlines()[-1].removeprefix("since=")
        assert shared("new", "answer", since).strip() == "t"
        for service, result in sources("new").items():
            assert result.returncode == 0, service + result.stdout + result.stderr
            assert "database sources match the rendered rules" in result.stdout
            assert f"({service})" in result.stdout
        # The check reads the mounted file: a changed byte is found.
        hba = f"{root}/config/services/pg-hba/pg_hba.conf"
        docker(
            "run", "--rm", "--network", "none", "--user", "10001:10001",
            "--entrypoint", "sh", "--mount",
            f"type=volume,source={volume},target={root.parent}",
            APPLICATION_IMAGE, "-c", f'echo "# changed" >>"{hba}"',
        )  # fmt: skip
        assert shared("new", "answer", since).strip() == "hba_file_differs"

        # The rollback to a release before the rules: the same full stop
        # puts PostgreSQL back on its stock rules, which admit every login
        # from everywhere, so every service's probe finds the difference.
        assert "ip_range" in shared("old", "changes")
        shared("old", "full-stop")
        assert shared("old", "changes") == ""
        assert shared("old", "rendered") == ""
        logins = inventory()
        for service, result in sources("old").items():
            assert result.returncode == 1, service + result.stdout + result.stderr
            for login in logins:
                assert f"{login} allowed from " in result.stdout, (service, login)
            assert " refused from " not in result.stdout
    finally:
        ids = docker(
            "ps", "--all", "--quiet", "--filter",
            f"label=com.docker.compose.project={project}", check=False,
        ).stdout.split()  # fmt: skip
        if ids:
            docker("rm", "--force", "--volumes", *ids, check=False)
        for network in docker(
            "network", "ls", "--quiet", "--filter",
            f"label=com.docker.compose.project={project}", check=False,
        ).stdout.split():  # fmt: skip
            docker("network", "rm", network, check=False)
        docker("volume", "rm", volume, check=False)


def pre_uploader_rendering(new):
    """The release before the off-site uploader (#235), from the new rendering.

    The source rules and the five earlier one-shot networks are there; the
    uploader's service, its network and PostgreSQL's address on it are not.
    """
    old = json.loads(json.dumps(new))
    old["services"].pop("backup-uploader")
    old["networks"].pop("offline-backup-uploader")
    old["services"]["postgres"]["networks"].pop("offline-backup-uploader")
    return old


@runtime
def test_the_expand_rollout_and_rollback_fully_stop_for_the_uploader(tmp_path):
    """The uploader's network alone triggers the full stop, both ways (#235).

    Rolling out the expand release adds a network PostgreSQL joins, so the
    shared block's comparison finds it and the full stop recreates the
    stores on it; every service's source check (the uploader's included)
    then matches. Rolling back removes it the same way, and the services
    that remain still match.
    """
    volume = f"pk-235e1-{uuid4().hex[:8]}"
    project = volume
    try:
        root, yaml, rendered, present = provisioned_root(volume, free_networks())
        new = probe_rendering(rendered, present)
        old = pre_uploader_rendering(new)
        files = {name: tmp_path / f"{name}.json" for name in ("old", "new")}
        files["old"].write_text(json.dumps(old))
        files["new"].write_text(json.dumps(new))
        work = tmp_path / "work"
        work.mkdir()
        driver = tmp_path / "driver.sh"
        driver.write_text(
            "set -euo pipefail\n"
            + layout_block(HOST)
            + "project=$1 compose=$2 work=$3\n"
            + 'dc=(docker compose -f "$compose" -p "$project")\n'
            + "case $4 in\n"
            + "    changes) layout_changes ;;\n"
            + "    full-stop) layout_full_stop ;;\n"
            + "esac\n"
        )

        def shared(name, action):
            """One shared-block action against one rendering."""
            result = subprocess.run(
                ["bash", str(driver), project, str(files[name]), str(work), action],
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            return result.stdout

        def sources(name):
            """``database-sources`` from every application service, by ``run``."""
            outcomes = {}
            for service, value in json.loads(files[name].read_text())[
                "services"
            ].items():
                if service in ("postgres", "valkey"):
                    continue
                outcomes[service] = docker(
                    "compose", "-f", str(files[name]), "-p", project, "run",
                    "--rm", "--no-deps", "-T", service, "database-sources",
                    "--config", value["command"][2], check=False, timeout=180,
                )  # fmt: skip
            return outcomes

        docker(
            "compose", "-f", str(files["old"]), "-p", project, "up", "--detach",
            "--wait", "postgres", "valkey", timeout=300,
        )  # fmt: skip
        assert shared("old", "changes") == ""
        changes = shared("new", "changes")
        assert "offline-backup-uploader" in changes
        shared("new", "full-stop")
        assert shared("new", "changes") == ""
        outcomes = sources("new")
        assert "backup-uploader" in outcomes
        for service, result in outcomes.items():
            assert result.returncode == 0, service + result.stdout + result.stderr
            assert "database sources match the rendered rules" in result.stdout
        assert "offline-backup-uploader" in shared("old", "changes")
        shared("old", "full-stop")
        assert shared("old", "changes") == ""
        for service, result in sources("old").items():
            assert result.returncode == 0, service + result.stdout + result.stderr
    finally:
        ids = docker(
            "ps", "--all", "--quiet", "--filter",
            f"label=com.docker.compose.project={project}", check=False,
        ).stdout.split()  # fmt: skip
        if ids:
            docker("rm", "--force", "--volumes", *ids, check=False)
        for network in docker(
            "network", "ls", "--quiet", "--filter",
            f"label=com.docker.compose.project={project}", check=False,
        ).stdout.split():  # fmt: skip
            docker("network", "rm", network, check=False)
        docker("volume", "rm", volume, check=False)
