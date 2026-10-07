"""The Admin automation host wrapper, tools/stewardship-ops/pk-admin (ADM-11).

The host has Docker but not the ParishKit package, so the wrapper is a POSIX
shell script. These tests run it with a stand-in `docker` on PATH that
records its arguments and standard input and answers with a chosen document
and exit status. What is pinned: session file creation (0600 in a 0700
directory, exclusive create, the tagged 43-character secret), refusal of
wider modes, malformed or oversize files and bad names before anything runs,
session selection, the HMAC host digest and exit 2 without a machine
identity, the preamble sent exactly once, standard input forwarded only for a
terminal or a `-` input, and which outcomes delete the session file. Export
fetching arrives with the export commands (ADM-11 PR 8).
"""

import hashlib
import hmac
import json
import os
import pty
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "tools" / "stewardship-ops" / "pk-admin"
MACHINE_ID = "0123456789abcdef0123456789abcdef"
SECRET = re.compile(r"pk-admin-session/1 ([A-Za-z0-9_-]{43})\n")

FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_DOCKER_ARGS"
if [ -n "${FAKE_DOCKER_LINES:-}" ]; then
    head -n "$FAKE_DOCKER_LINES" > "$FAKE_DOCKER_STDIN"
else
    cat > "$FAKE_DOCKER_STDIN"
fi
if [ -f "$FAKE_DOCKER_OUTPUT" ]; then cat "$FAKE_DOCKER_OUTPUT"; fi
exit "${FAKE_DOCKER_STATUS:-0}"
"""


def host_digest():
    """What the wrapper must send: HMAC-SHA256 keyed with the machine identity."""
    return hmac.new(
        MACHINE_ID.encode(), b"parishkit-admin-automation/1", hashlib.sha256
    ).hexdigest()


@pytest.fixture
def host(tmp_path):
    """A deployment root, a private session directory and a stand-in docker."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    try:
        subprocess.run([str(docker)], input=b"", capture_output=True, check=False)
    except PermissionError:
        pytest.skip("the temporary directory cannot run the stand-in docker")
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    sessions = tmp_path / "run" / "admin-automation"
    sessions.mkdir(parents=True)
    sessions.chmod(0o700)
    machine = tmp_path / "machine-id"
    machine.write_text(MACHINE_ID + "\n")
    state = tmp_path / "docker"
    state.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PK_ADMIN_", "PARISHKIT_"))
    }
    env.update(
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        PARISHKIT_ROOT=str(tmp_path),
        PK_ADMIN_MACHINE_ID=str(machine),
        PK_ADMIN_COMPOSE_FILE="/srv/compose.json",
        PK_ADMIN_PROJECT="parish",
        PK_ADMIN_WEB_CONFIG="/srv/web.yaml",
        FAKE_DOCKER_ARGS=str(state / "args"),
        FAKE_DOCKER_STDIN=str(state / "stdin"),
        FAKE_DOCKER_OUTPUT=str(state / "output"),
        TMPDIR=str(tmp_path),
    )

    class Host:
        """The test's view of the host."""

        root = tmp_path
        directory = sessions
        environment = env

        @staticmethod
        def answer(document, status=0):
            """What the stand-in docker prints, and its exit status.

            The document gets the command line's shape (``command`` first,
            an error's ``message`` after its ``code``) and is returned.
            """
            document = {"command": "test", **document}
            if "error" in document:
                document["error"] = {"message": "text", **document["error"]}
            (state / "output").write_text(json.dumps(document, sort_keys=True) + "\n")
            env["FAKE_DOCKER_STATUS"] = str(status)
            return document

        @staticmethod
        def popen(*arguments, **extra):
            """Start the wrapper in its own process group (for signals)."""
            return subprocess.Popen(
                [str(WRAPPER), *arguments],
                env={**env, **extra},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )

        @staticmethod
        def run(*arguments, stdin=b"", **extra):
            """Run the wrapper; ``stdin`` is bytes, or a file descriptor.

            A run that waits on its input would hang the suite, so every
            run has a time limit.
            """
            options = {"input": stdin} if isinstance(stdin, bytes) else {"stdin": stdin}
            return subprocess.run(
                [str(WRAPPER), *arguments],
                env={**env, **extra},
                capture_output=True,
                check=False,
                timeout=20,
                **options,
            )

        @staticmethod
        def fake_stat(style, owner=None, file_owner=None):
            """Put a stand-in ``stat`` of one style (gnu or bsd) on PATH.

            It reports this account (or ``owner``; ``file_owner`` for
            session files only) with 0700 for directories and 0600 for files.
            """
            script = bin_dir / "stat"
            script.write_text(
                "#!/usr/bin/env bash\n"
                f"style={style}; owner={owner or ''}; file_owner={file_owner or ''}\n"
                'path="${!#}"; uid=$(id -u); mode=600\n'
                '[ -d "$path" ] && mode=700\n'
                '[ -n "$owner" ] && uid=$owner\n'
                '[ -n "$file_owner" ] && [ -f "$path" ] && uid=$file_owner\n'
                'case "$1/$style" in -c/gnu|-f/bsd)\n'
                '    echo "$uid $mode"; exit 0 ;;\n'
                "esac\n"
                "exit 1\n"
            )
            script.chmod(0o755)

        @staticmethod
        def calls():
            """The docker arguments, or None when docker never ran."""
            path = state / "args"
            return path.read_text().splitlines() if path.exists() else None

        @staticmethod
        def stdin():
            """What docker read on standard input."""
            return (state / "stdin").read_bytes()

        @staticmethod
        def session(name="ops", mode=0o600, content=None):
            """Write a session file directly."""
            path = sessions / f"{name}.session"
            path.write_text(
                content if content is not None else f"pk-admin-session/1 {'a' * 43}\n"
            )
            path.chmod(mode)
            return path

    return Host


def test_wrapper_is_an_executable_posix_shell_script():
    """Shebang, executable bit, strict mode and a usage header."""
    text = WRAPPER.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert os.access(WRAPPER, os.X_OK)
    assert "set -eu" in text
    assert "# Usage: tools/stewardship-ops/pk-admin" in text
    assert subprocess.run(["sh", "-n", str(WRAPPER)]).returncode == 0


def test_shellcheck_is_clean():
    """The wrapper passes shellcheck as POSIX sh."""
    if shutil.which("shellcheck") is None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("the CI runner lacks shellcheck")
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(
        ["shellcheck", "-s", "sh", str(WRAPPER)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_login_start_creates_a_private_session_file_and_sends_the_preamble(host):
    """0600 in a 0700 directory, the tagged secret, the preamble and the options."""
    host.answer({"ok": True, "final": False, "result": {"user_code": "ABCD-EFGH"}})
    result = host.run(
        "login",
        "start",
        "--name",
        "ops",
        "--label",
        "launch assistant",
        "--expect-email",
        "admin",
        "--scope",
        "full",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"]["user_code"] == "ABCD-EFGH"
    path = host.directory / "ops.session"
    assert path.stat().st_mode & 0o777 == 0o600
    secret = SECRET.fullmatch(path.read_text())[1]
    # The secret reaches docker on standard input only, never as an argument.
    assert host.stdin() == f"pk-admin-session/1 {secret} {host_digest()}\n".encode()
    assert secret not in "\n".join(host.calls())
    assert secret not in result.stdout.decode() + result.stderr.decode()
    assert host.calls() == [
        "compose",
        "-f",
        "/srv/compose.json",
        "-p",
        "parish",
        "exec",
        "-T",
        "web",
        "pk-stewardship",
        "admin",
        "login",
        "start",
        "--name",
        "ops",
        "--label",
        "launch assistant",
        "--expect-email",
        "admin",
        "--scope",
        "full",
        "--config",
        "/srv/web.yaml",
        "--session-stdin",
    ]


def test_login_start_never_overwrites_and_removes_a_failed_start(host):
    """Exclusive create; a start the command refused leaves no file behind."""
    existing = host.session("ops")
    result = host.run("login", "start", "--name", "ops", "--scope", "full")
    assert result.returncode == 2 and host.calls() is None
    assert existing.read_text() == f"pk-admin-session/1 {'a' * 43}\n"
    existing.unlink()
    host.answer({"ok": False, "error": {"code": "usage"}}, status=2)
    result = host.run("login", "start", "--name", "ops", "--scope", "full")
    assert result.returncode == 2 and host.calls() is not None
    assert not (host.directory / "ops.session").exists()


@pytest.mark.parametrize(
    "problem",
    ["directory_mode", "file_mode", "malformed", "oversize", "symlink", "name"],
)
def test_wider_modes_bad_files_and_names_are_refused_before_anything_runs(
    host, problem
):
    """Each refusal exits 2 and never reaches docker."""
    arguments = ("--session", "ops", "whoami")
    if problem == "directory_mode":
        host.session()
        host.directory.chmod(0o750)
    elif problem == "file_mode":
        host.session(mode=0o640)
    elif problem == "malformed":
        host.session(content="pk-admin-session/2 " + "a" * 43 + "\n")
    elif problem == "oversize":
        host.session(content="pk-admin-session/1 " + "a" * 200 + "\n")
    elif problem == "symlink":
        target = host.root / "elsewhere.session"
        target.write_text(f"pk-admin-session/1 {'a' * 43}\n")
        target.chmod(0o600)
        (host.directory / "ops.session").symlink_to(target)
    else:
        host.session()
        arguments = ("--session", "Ops/../x", "whoami")
    result = host.run(*arguments)
    assert result.returncode == 2
    assert host.calls() is None
    assert result.stderr.startswith(b"pk-admin: ")


@pytest.mark.parametrize("name", ["", "UPPER", "a" * 33, "has space", "dot.dot"])
def test_login_names_outside_the_allowed_set_are_refused(host, name):
    """Names are 1 to 32 of a-z, 0-9 and '-'."""
    result = host.run("login", "start", "--name", name, "--scope", "full")
    assert result.returncode == 2 and host.calls() is None
    assert not list(host.directory.iterdir())


def test_session_selection_by_option_environment_and_single_file(host):
    """--session, else PK_ADMIN_SESSION, else the only file; several files refuse."""
    host.answer({"ok": True})
    host.session("only")
    assert host.run("whoami").returncode == 0
    host.session("second")
    result = host.run("whoami")
    assert result.returncode == 2
    assert b"only" in result.stderr and b"second" in result.stderr
    assert host.run("whoami", PK_ADMIN_SESSION="second").returncode == 0
    assert host.run("--session", "only", "whoami").returncode == 0
    missing = host.run("--session", "absent", "whoami")
    assert missing.returncode == 2


def test_no_session_file_is_a_usage_error(host):
    """With an empty directory there is nothing to choose."""
    result = host.run("whoami")
    assert result.returncode == 2 and host.calls() is None


def test_the_host_digest_needs_a_machine_identity(host):
    """Without a readable machine identity the wrapper exits 2."""
    host.session()
    result = host.run("whoami", PK_ADMIN_MACHINE_ID=str(host.root / "absent"))
    assert result.returncode == 2 and host.calls() is None


def test_the_preamble_is_sent_once_and_input_closes_without_a_terminal(host):
    """No terminal and no '-' input: docker reads the preamble line and nothing more."""
    host.answer({"ok": True})
    host.session()
    result = host.run("whoami", stdin=b"must not be forwarded\n")
    assert result.returncode == 0
    assert host.stdin() == (f"pk-admin-session/1 {'a' * 43} {host_digest()}\n".encode())


def test_a_dash_input_forwards_standard_input_after_the_preamble(host):
    """A '-' argument forwards this standard input after the preamble."""
    host.answer({"ok": True})
    host.session()
    result = host.run("notes", "add", "--text", "-", stdin=b"line one\nline two\n")
    assert result.returncode == 0
    preamble = f"pk-admin-session/1 {'a' * 43} {host_digest()}\n".encode()
    assert host.stdin() == preamble + b"line one\nline two\n"


def test_an_interactive_run_with_no_input_never_waits_on_the_terminal(host):
    """A terminal is not forwarded to a command that does not prompt.

    No command of this release prompts, so an interactive ``whoami`` with
    nothing typed must finish at once rather than wait for end of input.
    """
    host.answer({"ok": True})
    host.session()
    controller, terminal = pty.openpty()
    try:
        result = host.run("whoami", stdin=terminal)
    finally:
        os.close(terminal)
        os.close(controller)
    assert result.returncode == 0
    preamble = f"pk-admin-session/1 {'a' * 43} {host_digest()}\n".encode()
    assert host.stdin() == preamble


@pytest.mark.parametrize(
    "command,document,status,removed",
    [
        (("whoami",), {"ok": False, "error": {"code": "session_ended"}}, 5, True),
        # A missing session may be a typo'd --session or a database restore
        # in progress: the file stays.
        (("whoami",), {"ok": False, "error": {"code": "session_missing"}}, 5, False),
        # The code alone is not enough: the exit status must agree.
        (("whoami",), {"ok": False, "error": {"code": "session_ended"}}, 3, False),
        # Nor is a match anywhere in the text.
        (("whoami",), {"ok": True, "result": {"code": "session_ended"}}, 5, False),
        (("whoami",), {"ok": False, "error": {"code": "unavailable"}}, 3, False),
        (("logout",), {"ok": True}, 0, True),
        (("logout",), {"ok": False, "error": {"code": "unavailable"}}, 3, False),
        (
            ("login", "wait", "--name", "ops"),
            {"ok": False, "error": {"code": "pairing_expired"}},
            5,
            True,
        ),
        (
            ("login", "wait", "--name", "ops"),
            {"ok": False, "error": {"code": "pairing_pending"}},
            5,
            False,
        ),
    ],
)
def test_outcomes_that_end_the_session_remove_its_file(
    host, command, document, status, removed
):
    """The exit status is the command's; only ended sessions lose their file."""
    path = host.session("ops")
    document = host.answer(document, status=status)
    result = host.run(*command)
    assert result.returncode == status
    assert json.loads(result.stdout) == document
    assert path.exists() is not removed


@pytest.mark.parametrize("production", [False, True])
def test_no_container_mounts_the_session_directory_and_no_backup_keeps_it(
    tmp_path, production
):
    """Session files stay on the host: in no rendered mount and no backup tree.

    A mount of ROOT/run itself, or of any ancestor, would expose the
    directory too, so no mount source may contain it.
    """
    from parishkit.stewardship.backup import ARCHIVED_TREES
    from parishkit.stewardship.runtime_topology import render_runtime

    from .test_runtime_topology import IMAGE, configuration_at

    configuration = configuration_at(tmp_path, production=production)
    compose, _ = render_runtime(
        configuration,
        image=IMAGE if production else "parishkit-stewardship:development",
    )
    sessions = configuration.paths.root / "run" / "admin-automation"
    checked = 0
    for service in compose["services"].values():
        for mount in service.get("volumes", []):
            source = Path(mount["source"]) if isinstance(mount, dict) else None
            if source is None or not source.is_absolute():
                continue
            checked += 1
            assert not sessions.is_relative_to(source), mount
            assert not source.is_relative_to(sessions), mount
    assert checked > 10
    assert "run" not in ARCHIVED_TREES
    archived = {configuration.paths[name] for name in ARCHIVED_TREES}
    assert not any(sessions.is_relative_to(path) for path in archived)


@pytest.mark.parametrize("style", ["gnu", "bsd"])
def test_both_stat_styles_are_read(host, style):
    """GNU stat on the host; BSD stat on developers' machines."""
    host.answer({"ok": True})
    host.session()
    host.fake_stat(style)
    assert host.run("whoami").returncode == 0


@pytest.mark.parametrize("target", ["directory", "file"])
def test_a_directory_or_file_owned_by_another_account_is_refused(host, target):
    """Another owner is refused before anything runs."""
    host.session()
    if target == "directory":
        host.fake_stat("gnu", owner="0")
    else:
        host.fake_stat("gnu", file_owner="0")
    result = host.run("whoami")
    assert result.returncode == 2 and host.calls() is None
    assert b"must be owned by you" in result.stderr


def test_a_symlinked_session_directory_is_refused(host, tmp_path):
    """The directory itself must not be a symbolic link."""
    real = tmp_path / "real-sessions"
    real.mkdir(mode=0o700)
    link = tmp_path / "linked-sessions"
    link.symlink_to(real)
    result = host.run("--session-dir", str(link), "whoami")
    assert result.returncode == 2 and host.calls() is None


def test_session_dir_overrides_the_default(host, tmp_path):
    """--session-dir chooses another private directory."""
    other = tmp_path / "elsewhere"
    other.mkdir(mode=0o700)
    (other / "ops.session").write_text(f"pk-admin-session/1 {'b' * 43}\n")
    (other / "ops.session").chmod(0o600)
    host.answer({"ok": True})
    assert host.run("--session-dir", str(other), "whoami").returncode == 0
    assert f"pk-admin-session/1 {'b' * 43} ".encode() in host.stdin()


def test_a_directory_others_may_search_is_refused(host):
    """Mode 0701 grants others something: refused."""
    host.session()
    host.directory.chmod(0o701)
    result = host.run("whoami")
    assert result.returncode == 2 and host.calls() is None


def test_an_interrupted_login_start_leaves_no_session_file(host):
    """Ctrl-C while the pairing starts removes the file just created."""
    import signal
    import time

    (host.root / "bin" / "docker").write_text(
        "#!/usr/bin/env bash\ncat > /dev/null\nsleep 30\n"
    )
    process = host.popen(
        "login", "start", "--name", "ops", "--label", "x", "--scope", "full"
    )
    path = host.directory / "ops.session"
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.exists()
    time.sleep(0.3)
    os.killpg(process.pid, signal.SIGINT)
    process.communicate(timeout=20)
    assert process.returncode == 130
    assert not path.exists()


def test_the_wrapper_never_traces_the_secret(host):
    """Running it under ``sh -x`` traces nothing after its first command."""
    host.answer({"ok": True})
    host.session()
    result = subprocess.run(
        ["sh", "-x", str(WRAPPER), "whoami"],
        env=host.environment,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert ("a" * 43).encode() not in result.stderr


def prompting(tmp_path):
    """A copy of the wrapper whose PROMPTING list names ``test act``."""
    text = WRAPPER.read_text()
    assert text.count('PROMPTING=""') == 1
    copy = tmp_path / "pk-admin-prompting"
    copy.write_text(text.replace('PROMPTING=""', 'PROMPTING="test_act"'))
    copy.chmod(0o755)
    return copy


def run_at_terminal(host, wrapper, typed=None, **extra):
    """Run ``test act`` with a terminal as standard input, typing ``typed``."""
    controller, terminal = pty.openpty()
    try:
        process = subprocess.Popen(
            [str(wrapper), "test", "act"],
            env={**host.environment, **extra},
            stdin=terminal,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if typed is not None:
            os.write(controller, typed)
        process.communicate(timeout=20)
    finally:
        os.close(terminal)
        os.close(controller)
    return process.returncode


def test_a_prompting_command_gets_one_typed_answer_line(host, tmp_path):
    """From a terminal: the preamble, then exactly the line typed, then EOF."""
    host.answer({"ok": True})
    host.session()
    assert run_at_terminal(host, prompting(tmp_path), b"yes\nmore\n") == 0
    preamble = f"pk-admin-session/1 {'a' * 43} {host_digest()}\n".encode()
    assert host.stdin() == preamble + b"yes\n"


def test_the_wrapper_never_waits_for_an_answer_no_longer_needed(host, tmp_path):
    """A command that ends before its prompt: the wrapper ends too, untyped."""
    host.answer({"ok": False, "error": {"code": "denied"}}, status=1)
    host.session()
    assert run_at_terminal(host, prompting(tmp_path), FAKE_DOCKER_LINES="1") == 1
    preamble = f"pk-admin-session/1 {'a' * 43} {host_digest()}\n".encode()
    assert host.stdin() == preamble
