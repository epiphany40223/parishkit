"""The inspection login's host wrapper, tools/stewardship-ops/pk-inspect (#2148).

The host has Docker but not the ParishKit package, so the wrapper is a POSIX
shell script. These tests run it with a stand-in ``docker`` on PATH that
records its arguments and then runs the container side itself: the inner
``sh -c`` with ``POSTGRES_DB`` set and a stand-in ``psql`` that records the
argument list psql would get. What is pinned: the container for Production
and for pk-local's project, standard input forwarded with a terminal only
when there is one, the caller's options first and the connection options
last (so they win), and ``--`` refused before docker runs. The Docker
provisioning test runs the real wrapper against a real PostgreSQL.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "tools" / "stewardship-ops" / "pk-inspect"

# Records docker's arguments, then runs what `docker exec` would run in the
# container: everything after the container name, with POSTGRES_DB set.
FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$@" > "$STATE/docker-args"
shift 3
export POSTGRES_DB=stewardship_db
exec "$@"
"""
FAKE_PSQL = """#!/bin/sh
printf '%s\\n' "$@" > "$STATE/psql-args"
cat > "$STATE/psql-stdin"
"""


@pytest.fixture
def host(tmp_path):
    """A PATH with the stand-in docker and psql, and where they record."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("psql", FAKE_PSQL)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return tmp_path


def run(host, *arguments, environment=None, stdin=b""):
    """Run the wrapper without a terminal; its result and what was recorded."""
    env = {
        "PATH": f"{host / 'bin'}:/usr/bin:/bin",
        "STATE": str(host),
        **(environment or {}),
    }
    result = subprocess.run(
        [str(WRAPPER), *arguments],
        input=stdin,
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )

    def recorded(name):
        """One recorded argument list, or None when that program never ran."""
        path = host / name
        return path.read_text().splitlines() if path.exists() else None

    return result, recorded("docker-args"), recorded("psql-args")


CONNECTION = [
    "-h",
    "/var/run/postgresql",
    "-U",
    "pk_stewardship_inspect",
    "-d",
    "stewardship_db",
]


def test_production_defaults_and_connection_options_last(host):
    """Production's container; the caller's options first, the login last."""
    result, docker, psql = run(
        host, "-At", "-U", "pk_stewardship_operator", "-c", "SELECT 1", stdin=b"x"
    )
    assert result.returncode == 0, result.stderr
    assert docker[:3] == ["exec", "-i", "stewardship-postgres-1"]
    assert docker[3:5] == ["sh", "-c"]
    # psql takes the last -U, -h and -d, so the caller's -U is overridden.
    assert psql == [
        "-X",
        "-At",
        "-U",
        "pk_stewardship_operator",
        "-c",
        "SELECT 1",
        *CONNECTION,
    ]
    assert (host / "psql-stdin").read_bytes() == b"x"


@pytest.mark.parametrize(
    ("environment", "container"),
    [
        ({"PK_INSPECT_PROJECT": "parishkit-local"}, "parishkit-local-postgres-1"),
        (
            {"PK_INSPECT_PROJECT": "ignored", "PK_INSPECT_CONTAINER": "named-pg"},
            "named-pg",
        ),
    ],
)
def test_pk_local_project_and_explicit_container(host, environment, container):
    """pk-local's project names its container; an explicit container wins."""
    result, docker, psql = run(host, environment=environment)
    assert result.returncode == 0, result.stderr
    assert docker[:3] == ["exec", "-i", container]
    assert psql == ["-X", *CONNECTION]


def test_double_dash_is_refused_before_docker_runs(host):
    """After `--` psql would take the login from positional arguments."""
    result, docker, psql = run(host, "--", "stewardship", "pk_stewardship_operator")
    assert result.returncode == 2
    assert b"'--' is not accepted" in result.stderr
    assert docker is None and psql is None


def test_psql_status_is_the_wrappers(host):
    """psql's exit status (here a refused statement) is the wrapper's own."""
    (host / "bin" / "psql").write_text("#!/bin/sh\nexit 3\n")
    result, _, _ = run(host, "-c", "SELECT 1")
    assert result.returncode == 3
