"""The Admin automation host wrapper, tools/stewardship-ops/pk-admin (ADM-11).

The host has Docker but not the ParishKit package, so the wrapper is a POSIX
shell script. These tests run it with a stand-in `docker` on PATH that
records its arguments and standard input and answers with a chosen document
and exit status. What is pinned: session file creation (0600 in a 0700
directory, exclusive create, the tagged 43-character secret), refusal of
wider modes, malformed or oversize files and bad names before anything runs,
session selection, the HMAC host digest and exit 2 without a machine
identity, the preamble sent exactly once, standard input forwarded only for a
terminal or a `-` input, which outcomes delete the session file, that a
streamed file (``logs export``, ``export download``) is never written to a
terminal, and ``export fetch`` (PR 8b): an owner-only file created
exclusively, checked against its size and digest, never overwritten, never
copied elsewhere, and removed when the check, the download or the run fails;
``exports clean`` deletes only fetched files. An interrupted ``--watch``
(INT, TERM or HUP; #598) is stopped inside the stand-in container by its
token, shows its final document (not after HUP) and exits 130, 143 or 129,
even when its standard error is a hung-up terminal; a failed stop is
reported; other commands are never signalled there.
"""

import contextlib
import hashlib
import hmac
import json
import os
import pty
import re
import shutil
import subprocess
import sys
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

    ``whoami`` does not prompt, so an interactive run with nothing typed
    must finish at once rather than wait for end of input.
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


def test_a_streamed_file_is_never_written_to_a_terminal(host):
    """``logs export`` with a terminal as standard output is refused at once."""
    host.answer({"ok": True})
    host.session()
    controller, terminal = pty.openpty()
    try:
        result = subprocess.run(
            [str(WRAPPER), "logs", "export", "--format", "csv"],
            env=host.environment,
            stdin=subprocess.DEVNULL,
            stdout=terminal,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
    finally:
        os.close(terminal)
        os.close(controller)
    assert result.returncode == 2
    assert b"redirect standard output to a file" in result.stderr
    assert host.calls() is None


# A stand-in docker for a streaming command: the file's bytes on standard
# output, then its logs and document on standard error, as the command
# line writes them. While the command runs it records every byte the
# wrapper's work directory holds, so a copy of the file would show.
STREAMING_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_DOCKER_ARGS"
cat > "$FAKE_DOCKER_STDIN"
cat "$FAKE_DOCKER_BODY"
printf '{"event": "info line"}\\n' >&2
cat "$FAKE_DOCKER_OUTPUT" >&2
sleep 0.2
for path in "$TMPDIR"/pk-admin.*/*; do
    # Regular files only: a FIFO there (the input writer's) would block.
    [ -f "$path" ] && [ ! -L "$path" ] && cat "$path"
done > "$FAKE_DOCKER_WORK"
exit "${FAKE_DOCKER_STATUS:-0}"
"""


def run_streaming(host, body, document, status=0):
    """Run ``logs export`` against STREAMING_DOCKER; return (result, work bytes)."""
    state = host.root / "docker"
    docker = host.root / "bin" / "docker"
    docker.write_text(STREAMING_DOCKER)
    docker.chmod(0o755)
    (state / "body").write_bytes(body)
    document = host.answer(document, status=status)
    result = host.run(
        "logs",
        "export",
        "--format",
        "csv",
        FAKE_DOCKER_BODY=str(state / "body"),
        FAKE_DOCKER_WORK=str(state / "work"),
    )
    return result, document, (state / "work").read_bytes()


def test_a_streamed_file_passes_through_and_is_never_copied(host):
    """The bytes reach standard output as they are; no copy is kept on disk.

    The command's standard error comes through with its document last.
    """
    body = b"time,actor_email\r\nT,admin@example.org\r\n\x00\xff\r\n"
    host.session()
    result, document, work = run_streaming(host, body, {"ok": True})
    assert result.returncode == 0 and result.stdout == body
    assert b"admin@example.org" not in work and body not in work
    lines = result.stderr.decode().splitlines()
    assert json.loads(lines[-1]) == document and "info line" in lines[0]
    assert {"logs", "export"} <= set(host.calls())


def test_a_streaming_command_on_an_ended_session_removes_its_file(host):
    """The document on standard error still ends a dead session's file."""
    path = host.session("ops")
    result, document, _ = run_streaming(
        host, b"", {"ok": False, "error": {"code": "session_ended"}}, status=5
    )
    assert result.returncode == 5 and result.stdout == b""
    assert not path.exists()
    assert b"removed session file" in result.stderr


def test_a_streaming_commands_standard_error_is_shown_as_it_comes(host):
    """Interrupted mid-run, the wrapper has already shown what arrived.

    The stand-in prints its document, then keeps running until Ctrl-C (here
    SIGINT to the wrapper's process group); the document is on the
    operator's standard error before the interruption, never held back.
    """
    import signal

    docker = host.root / "bin" / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat > /dev/null\n"
        'printf \'{"command": "logs export", "ok": false}\\n\' >&2\n'
        "sleep 20\n"
    )
    docker.chmod(0o755)
    host.session()
    process = subprocess.Popen(
        [str(WRAPPER), "logs", "export"],
        env=host.environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        line = process.stderr.readline()
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=20)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
    assert json.loads(line) == {"command": "logs export", "ok": False}
    assert process.returncode == 130


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


@pytest.mark.parametrize(
    "arguments,watch",
    [
        (("task", "show", "t-1", "--watch", "5"), True),
        (("task", "show", "t-1", "--watch=5"), True),
        (("task", "show", "t-1", "--watch", "+5"), True),
        (("task", "show", "t-1", "--watch=+5"), True),
        (("whoami",), False),
        # An option's value, or anything after `--`, is not a watch.
        (("notes", "add", "--text", "--watch"), False),
        (("notes", "add", "--", "--watch", "5"), False),
    ],
)
def test_only_a_watch_carries_a_stop_token(host, arguments, watch):
    """``--watch`` runs get ``-e PK_ADMIN_RUN=TOKEN`` (fresh hex); others don't."""
    host.answer({"ok": True})
    host.session()
    tokens = set()
    for _ in range(2):
        assert host.run(*arguments).returncode == 0
        calls = host.calls()
        web = calls.index("web")
        assert calls[web + 1 : web + 3] == ["pk-stewardship", "admin"]
        if not watch:
            assert "-e" not in calls[:web]
            assert not any(call.startswith("PK_ADMIN_RUN") for call in calls)
            return
        assert calls[web - 2] == "-e"
        match = re.fullmatch(r"PK_ADMIN_RUN=([0-9a-f]{32})", calls[web - 1])
        assert match
        tokens.add(match[1])
    assert len(tokens) == 2


def test_a_setsid_without_wait_is_not_used(host):
    """A setsid that refuses ``-w`` (busybox) is probed, not trusted: the
    watch then runs with plain ``docker`` instead of failing."""
    fake = host.root / "bin" / "setsid"
    fake.write_text('#!/bin/sh\n[ "$1" != -w ] || exit 1\nexit 99\n')
    fake.chmod(0o755)
    host.answer({"ok": True})
    host.session()
    result = host.run("task", "show", "t-1", "--watch", "5")
    assert result.returncode == 0, result.stderr
    assert "PK_ADMIN_RUN" in " ".join(host.calls())


def needs_linux_signals():
    """The stop tests need /proc, pgrep and setsid: present on Linux (CI).

    On Linux a missing tool fails rather than skips, since CI runs this file
    where skips are not otherwise caught; elsewhere (macOS) the tests skip.
    """
    missing = [
        name
        for name, present in (
            ("/proc", Path("/proc/self/environ").exists()),
            ("pgrep", shutil.which("pgrep") is not None),
            ("setsid", shutil.which("setsid") is not None),
        )
        if not present
    ]
    if not missing:
        return
    if sys.platform.startswith("linux"):
        pytest.fail(f"this Linux host lacks {', '.join(missing)}")
    pytest.skip(f"this host lacks {', '.join(missing)}")


# The command inside the stand-in container: it records its process id, then
# the first signal it gets, on which it prints a final document and exits 7,
# as a watch does on SIGINT.
FAKE_INNER = """
import json, os, signal, sys, time
with open(os.environ["FAKE_INNER_PID"], "w") as pid:
    pid.write(str(os.getpid()))
def stop(number, frame):
    with open(os.environ["FAKE_MARKER"], "w") as marker:
        marker.write(signal.Signals(number).name)
    document = {"command": "task show", "final": True, "ok": False,
                "error": {"code": "watch_interrupted", "message": "text"}}
    print(json.dumps(document, sort_keys=True), flush=True)
    sys.exit(7)
for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(number, stop)
print(json.dumps({"command": "task show", "final": False}), flush=True)
time.sleep(30)
sys.exit(0)
"""

# The stand-in docker client, as `docker compose exec` behaves: SIGINT ends
# it at once with 130 even when inherited as ignored, losing whatever the
# command prints after. The command runs in its own session (no signal to
# the wrapper's process group reaches a real container), with the token in
# its environment, and its output is relayed.
FAKE_CLIENT = """
import os, signal, subprocess, sys
signal.signal(signal.SIGINT, lambda number, frame: os._exit(130))
environment = dict(os.environ)
if sys.argv[1]:
    environment["PK_ADMIN_RUN"] = sys.argv[1]
inner = subprocess.Popen(
    [sys.executable, "-c", os.environ["FAKE_INNER"]],
    env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL, start_new_session=True,
)
for line in inner.stdout:
    sys.stdout.buffer.write(line)
    sys.stdout.flush()
sys.exit(inner.wait())
"""

# The stand-in client for the wrapper's stop, as `docker compose exec`
# behaves: SIGINT ends it at once with 130, so a second Ctrl-C that reached
# it would lose the stop. FAKE_STOP_DELAY holds it back that many seconds
# first; then it runs the stop script on this host.
FAKE_STOPPER = """
import os, signal, subprocess, sys, time
signal.signal(signal.SIGINT, lambda number, frame: os._exit(130))
time.sleep(float(os.environ.get("FAKE_STOP_DELAY") or 0))
sys.exit(subprocess.run(sys.argv[1:], stdin=subprocess.DEVNULL).returncode)
"""

# The stand-in docker: `exec ... pk-stewardship` runs the client above;
# `exec -T web sh -c SCRIPT sh TOKEN` (the wrapper's stop) records its
# arguments and runs the script through the stop client above on this host,
# whose /proc stands in for the container's, unless FAKE_STOP_FAILS asks it
# to fail as docker would. Anything else exits 99.
FAKE_CONTAINER = """#!/usr/bin/env bash
case " $* " in
*" pk-stewardship "*)
    cat > /dev/null
    token=""
    for argument in "$@"; do
        case $argument in PK_ADMIN_RUN=*) token=${argument#PK_ADMIN_RUN=} ;; esac
    done
    exec "$FAKE_PYTHON" -c "$FAKE_CLIENT" "$token"
    ;;
*" sh -c "*)
    while [ "$#" -gt 0 ] && [ "$1" != web ]; do shift; done
    [ "$#" -gt 1 ] || exit 99
    shift
    printf '%s\\n' "$@" > "$FAKE_STOP_ARGS"
    if [ -n "${FAKE_STOP_FAILS:-}" ]; then
        echo 'service "web" is not running' >&2
        exit 1
    fi
    exec "$FAKE_PYTHON" -c "$FAKE_STOPPER" "$@"
    ;;
esac
exit 99
"""


@pytest.fixture
def container(host):
    """The stand-in container above, its state files, and cleanup.

    ``container.start(*arguments, stderr=...)`` starts the wrapper in its
    own process group once its first document has arrived; ``container.end``
    signals it and waits. Whatever the run leaves (an inner command a test
    meant to outlive the wrapper) is stopped afterwards.
    """
    import signal

    state = host.root / "docker"
    (host.root / "bin" / "docker").write_text(FAKE_CONTAINER)
    host.session()
    extra = {
        "FAKE_PYTHON": sys.executable,
        "FAKE_INNER": FAKE_INNER,
        "FAKE_CLIENT": FAKE_CLIENT,
        "FAKE_STOPPER": FAKE_STOPPER,
        "FAKE_INNER_PID": str(state / "inner"),
        "FAKE_MARKER": str(state / "marker"),
        "FAKE_STOP_ARGS": str(state / "stop"),
    }
    processes = []

    class Container:
        """The test's view of the stand-in container."""

        marker = state / "marker"
        stop = state / "stop"

        @staticmethod
        def start(*arguments, stderr=subprocess.PIPE, **more):
            process = subprocess.Popen(
                [str(WRAPPER), *arguments],
                env={**host.environment, **extra, **more},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr,
                start_new_session=True,
            )
            processes.append(process)
            first = process.stdout.readline()
            assert json.loads(first)["final"] is False
            return process

        @staticmethod
        def end(process, name, group, *, again=None):
            """Send signal ``name``; return (stdout, stderr, seconds taken).

            ``again`` is a number of seconds after which the same signal is
            sent a second time (a second Ctrl-C).
            """
            import time

            started = time.monotonic()
            send = os.killpg if group else os.kill
            send(process.pid, getattr(signal, name))
            if again is not None:
                time.sleep(again)
                send(process.pid, getattr(signal, name))
            out, err = process.communicate(timeout=20)
            return out, err or b"", time.monotonic() - started

    yield Container
    for process in processes:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError, ValueError):
        os.kill(int((state / "inner").read_text()), signal.SIGKILL)


@pytest.mark.parametrize(
    "name,status,group",
    [
        ("SIGINT", 130, True),
        ("SIGTERM", 143, False),
        ("SIGTERM", 143, True),
        ("SIGHUP", 129, False),
        ("SIGHUP", 129, True),
    ],
)
def test_an_interrupted_watch_is_stopped_inside_the_container(
    container, name, status, group
):
    """INT, TERM or HUP reaches the watch in the container as SIGINT.

    Ctrl-C reaches the whole process group, as a closed terminal's HUP does;
    `kill` reaches only the wrapper. The client exits at once on SIGINT, as
    `docker compose` does, so the watch's last document is shown only
    because the client is kept out of the terminal's reach. The inner
    command is in its own session, so only the wrapper's stop (found by its
    token in /proc) can reach it. Only after Ctrl-C does the wrapper wait
    for that document: after HUP there is no terminal to show it on, and TERM
    to the whole group has already ended what relays it.
    """
    needs_linux_signals()
    process = container.start("task", "show", "t-1", "--watch", "5")
    out, err, seconds = container.end(process, name, group)
    assert process.returncode == status, err
    assert seconds < 4, err
    assert container.marker.read_text() == "SIGINT"
    stop = container.stop.read_text().splitlines()
    assert stop[0:2] == ["sh", "-c"] and re.fullmatch(r"[0-9a-f]{32}", stop[-1])
    assert b"stopping the watch in the web container" in err
    assert b"did not" not in err and b"could not" not in err
    if name == "SIGINT":
        last = json.loads(out.decode().splitlines()[-1])
        assert last["error"]["code"] == "watch_interrupted" and last["final"]


def test_a_second_ctrl_c_does_not_cut_the_stop_off(container):
    """Ctrl-C twice: the stop command, still running when the second
    arrives, is out of the terminal's reach, so the watch is still stopped
    and its last document still shown."""
    needs_linux_signals()
    process = container.start(
        "task", "show", "t-1", "--watch", "5", FAKE_STOP_DELAY="1"
    )
    out, err, seconds = container.end(process, "SIGINT", True, again=0.3)
    assert process.returncode == 130, err
    assert seconds < 5, err
    assert container.marker.read_text() == "SIGINT"
    assert b"could not" not in err and b"did not" not in err
    last = json.loads(out.decode().splitlines()[-1])
    assert last["error"]["code"] == "watch_interrupted"


def test_a_closed_standard_error_pipe_does_not_end_the_wrapper(container):
    """Standard error to a pipe whose reader has gone (`2>&1 | jq` ended):
    the handler's writes fail instead of killing it with SIGPIPE, so it
    still stops the watch and exits 130 after its own cleanup."""
    needs_linux_signals()
    process = container.start("task", "show", "t-1", "--watch", "5")
    process.stderr.close()
    process.stderr = None
    _, _, seconds = container.end(process, "SIGINT", True)
    assert process.returncode == 130
    assert seconds < 4
    assert container.marker.read_text() == "SIGINT"


def test_a_closed_terminal_still_stops_the_watch(container):
    """HUP with standard error on a hung-up terminal: writes fail with EIO,
    and the stop is sent anyway (it is started before any message)."""
    needs_linux_signals()
    controller, terminal = pty.openpty()
    try:
        process = container.start(
            "task", "show", "t-1", "--watch", "5", stderr=terminal
        )
    finally:
        os.close(terminal)
    os.close(controller)  # hang up: writes to the terminal now fail
    _, _, seconds = container.end(process, "SIGHUP", False)
    assert process.returncode == 129
    assert seconds < 4
    assert container.marker.read_text() == "SIGINT"


def test_a_failed_stop_is_reported(container):
    """A stop command that fails is reported with its output, at once."""
    needs_linux_signals()
    process = container.start(
        "task", "show", "t-1", "--watch", "5", FAKE_STOP_FAILS="1"
    )
    _, err, seconds = container.end(process, "SIGINT", True)
    assert process.returncode == 130
    assert seconds < 4
    assert b"the watch could not be stopped (exit 1)" in err
    assert b'service "web" is not running' in err
    assert not container.marker.exists() or container.marker.read_text() != "SIGINT"


def test_an_interrupted_command_that_is_not_a_watch_is_not_signalled(container):
    """Ctrl-C on any other command ends the wrapper at once (130) and sends
    no stop into the container: an interrupted write must run to its end."""
    if shutil.which("pgrep") is None:
        pytest.skip("pgrep is not installed")
    process = container.start("whoami")
    _, _, seconds = container.end(process, "SIGINT", True)
    assert seconds < 4
    assert process.returncode == 130
    assert not container.stop.exists()
    assert not container.marker.exists() or container.marker.read_text() != "SIGINT"


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
    """A copy of the wrapper whose PROMPTING list also names ``test act``."""
    text = WRAPPER.read_text()
    assert text.count('PROMPTING="') == 1
    copy = tmp_path / "pk-admin-prompting"
    copy.write_text(text.replace('PROMPTING="', 'PROMPTING="test_act ', 1))
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


# Prompting commands that accept only --yes, by catalog name, so the wrapper
# never forwards a terminal's answer to them: none on this branch.
YES_ONLY = frozenset()


def test_the_prompting_list_names_every_prompting_command():
    """PROMPTING is the catalog's prompting commands, less the --yes-only ones.

    The wrapper matches ``AREA_VERB`` (the first two words), so a prompting
    command missing from the list could never be answered at a terminal.
    """
    from parishkit.stewardship import admin_cli

    [listed] = re.findall(r'^PROMPTING="([^"]*)"$', WRAPPER.read_text(), re.M)
    prompting = {
        "_".join(spec.name.split()[:2])
        for spec in admin_cli.COMMANDS
        if spec.prompts and spec.name not in YES_ONLY
    }
    assert set(listed.split()) == prompting
    assert {"test_sample", "test_families"} <= prompting
    assert prompting >= {"delivery_resend", "delivery_refusal-clear"}


def test_export_download_is_never_written_to_a_terminal(host):
    """``export download`` streams too: refused when standard output is a terminal."""
    host.session()
    controller, terminal = pty.openpty()
    try:
        result = subprocess.run(
            [str(WRAPPER), "export", "download", EXPORT_ID, "--stream"],
            env=host.environment,
            stdin=subprocess.DEVNULL,
            stdout=terminal,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
    finally:
        os.close(terminal)
        os.close(controller)
    assert result.returncode == 2
    assert b"redirect standard output to a file" in result.stderr
    assert host.calls() is None


# ``export fetch`` (ADM-11 PR 8b). A stand-in docker that answers both runs
# the wrapper makes: ``export status`` prints its document on standard
# output; ``export download --stream`` prints the file's bytes on standard
# output and its document on standard error. Each run's arguments are
# logged, one line per run.
EXPORT_ID = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
FETCH_DOCKER = """#!/usr/bin/env bash
cat > /dev/null
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case " $* " in
    *" export status "*)
        cat "$FAKE_STATUS_DOCUMENT"
        exit "${FAKE_STATUS_EXIT:-0}" ;;
esac
cat "$FAKE_DOCKER_BODY"
cat "$FAKE_DOCKER_OUTPUT" >&2
exit "${FAKE_DOCKER_STATUS:-0}"
"""


@pytest.fixture
def fetching(host):
    """A host whose docker answers ``export status`` and ``export download``.

    ``fetch(body, state=..., described=...)`` runs ``export fetch`` with the
    download writing ``body`` and the status describing ``described``
    (default: ``body`` itself); it returns the result and the logged runs.
    """
    state = host.root / "docker"
    docker = host.root / "bin" / "docker"
    docker.write_text(FETCH_DOCKER)
    docker.chmod(0o755)
    host.session()
    log = state / "log"

    def fetch(
        body,
        *,
        export_state="ready",
        described=None,
        download=None,
        download_status=0,
        arguments=(),
    ):
        """One ``export fetch`` run; returns (result, logged runs)."""
        described = body if described is None else described
        result_document = {
            "campaign_id": "00000000-0000-4000-8000-000000000001",
            "can_cancel": False,
            "content_type": "text/csv",
            "count": 2,
            "created_at": "2054-10-05T14:00:00+00:00",
            "expires_at": "2054-10-06T14:00:00+00:00",
            "file_name": "participation.csv",
            "format": "csv",
            "id": EXPORT_ID,
            "report": "participation",
            "sha256": hashlib.sha256(described).hexdigest(),
            "size": len(described),
            "state": export_state,
        }
        status_document = {
            "command": "export status",
            "correlation_id": "00000000-0000-4000-8000-000000000002",
            "final": True,
            "ok": True,
            "result": result_document,
            "schema": "pk-admin/1",
            "session": {"expires_at": "2054-11-05T14:00:00+00:00", "id": EXPORT_ID},
        }
        (state / "status").write_text(
            json.dumps(status_document, sort_keys=True) + "\n"
        )
        (state / "body").write_bytes(body)
        host.answer(download or {"ok": True}, status=download_status)
        log.unlink(missing_ok=True)
        result = host.run(
            *arguments,
            "export",
            "fetch",
            EXPORT_ID,
            FAKE_DOCKER_BODY=str(state / "body"),
            FAKE_DOCKER_LOG=str(log),
            FAKE_STATUS_DOCUMENT=str(state / "status"),
        )
        runs = log.read_text().splitlines() if log.exists() else []
        return result, runs

    fetch.directory = host.directory / "exports"
    fetch.target = fetch.directory / f"{EXPORT_ID}-participation.csv"
    fetch.partial = fetch.directory / f".{EXPORT_ID}.partial"
    return fetch


def copies(root, body, *, keep):
    """Every file under ``root`` holding ``body``, but the ones to ``keep``."""
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and path not in keep and body in path.read_bytes()
    ]


def test_export_fetch_writes_an_owner_only_file_and_prints_only_its_receipt(
    host, fetching
):
    """Status, then the stream straight into a 0600 file in a 0700 directory.

    Only the path, size and SHA-256 are printed; the bytes are on disk once,
    in the fetched file, and nowhere else (no work directory copy).
    """
    body = b"date,family\r\n2054-10-05,Example Family <family@example.org>\r\n\x00"
    result, runs = fetching(body)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "path": str(fetching.target),
        "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body),
    }
    assert body not in result.stdout and body not in result.stderr
    assert fetching.target.read_bytes() == body
    assert fetching.target.stat().st_mode & 0o777 == 0o600
    assert fetching.directory.stat().st_mode & 0o777 == 0o700
    assert not fetching.partial.exists()
    assert [" export status " in f" {run} " for run in runs] == [True, False]
    assert f"export download {EXPORT_ID} --stream" in runs[1]
    state = host.root / "docker" / "body"
    assert copies(host.root, body, keep={fetching.target, state}) == []


def test_export_fetch_never_overwrites_a_fetched_file(fetching):
    """A second fetch is refused before it downloads anything."""
    body = b"a,b\r\n"
    assert fetching(body)[0].returncode == 0
    result, runs = fetching(body)
    assert result.returncode == 2 and b"already exists" in result.stderr
    assert len(runs) == 1 and " export status " in f" {runs[0]} "
    assert fetching.target.read_bytes() == body


def test_a_fetch_that_does_not_match_its_receipt_is_deleted(fetching):
    """A wrong size or digest deletes the partial file and exits 3."""
    result, _ = fetching(b"a,b\r\n", described=b"a,c\r\n")
    assert result.returncode == 3 and b"did not match" in result.stderr
    assert not fetching.target.exists() and not fetching.partial.exists()


def test_a_failed_download_leaves_no_file_and_ends_a_dead_session(host, fetching):
    """The download's own exit status; its partial file and dead session go."""
    result, _ = fetching(
        b"",
        download={"ok": False, "error": {"code": "session_ended"}},
        download_status=5,
    )
    assert result.returncode == 5
    assert not fetching.target.exists() and not fetching.partial.exists()
    assert not (host.directory / "ops.session").exists()


def test_an_export_that_is_not_ready_is_not_downloaded(fetching):
    """Exit 1 with the state; only the status ran."""
    result, runs = fetching(b"", export_state="running")
    assert result.returncode == 1 and b"is running, not ready" in result.stderr
    assert len(runs) == 1
    assert not fetching.target.exists() and not fetching.partial.exists()


def test_an_interrupted_or_concurrent_fetch_is_refused(fetching):
    """An existing partial file is never reused or truncated."""
    fetching.directory.mkdir(mode=0o700)
    fetching.partial.write_bytes(b"other")
    result, runs = fetching(b"a,b\r\n")
    assert result.returncode == 2 and b"another fetch" in result.stderr
    assert fetching.partial.read_bytes() == b"other" and len(runs) == 1


@pytest.mark.parametrize("identifier", ["6F1C2A3B-4D5E-4F60-8A7B-9C0D1E2F3A4B", "x"])
def test_export_fetch_needs_one_lowercase_export_id(host, identifier):
    """Anything but one canonical id is refused before docker runs."""
    host.session()
    for arguments in (("export", "fetch", identifier), ("export", "fetch")):
        result = host.run(*arguments)
        assert result.returncode == 2 and host.calls() is None


def test_the_export_directory_must_be_private(host, fetching, tmp_path):
    """A directory open to others is refused; --export-dir chooses another."""
    fetching.directory.mkdir(mode=0o755)
    fetching.directory.chmod(0o755)
    result, runs = fetching(b"a,b\r\n")
    assert result.returncode == 2 and runs == []
    other = tmp_path / "fetched"
    result, _ = fetching(b"a,b\r\n", arguments=("--export-dir", str(other)))
    assert result.returncode == 0, result.stderr
    assert (other / f"{EXPORT_ID}-participation.csv").read_bytes() == b"a,b\r\n"
    assert other.stat().st_mode & 0o777 == 0o700


def test_exports_clean_deletes_fetched_files_only(host, fetching):
    """Fetched files and partials go; anything else in the directory stays."""
    assert fetching(b"a,b\r\n")[0].returncode == 0
    fetching.partial.write_bytes(b"partial")
    other = fetching.directory / "notes.txt"
    other.write_text("kept")
    result = host.run("exports", "clean")
    assert result.returncode == 0 and json.loads(result.stdout) == {"removed": 2}
    assert not fetching.target.exists() and not fetching.partial.exists()
    assert other.exists()
    assert host.run("exports", "clean", "now").returncode == 2


@pytest.mark.parametrize(
    "name,status,group",
    [("SIGINT", 130, True), ("SIGTERM", 143, False), ("SIGHUP", 129, False)],
)
def test_an_interrupted_fetch_leaves_no_partial_file(
    host, fetching, name, status, group
):
    """Ctrl-C, TERM or a dropped session while the file streams: no partial.

    The wrapper stops at once, without waiting for the download (which here
    would run for 20 seconds): Ctrl-C reaches the whole process group, TERM
    and HUP only the wrapper.
    """
    import signal
    import time

    fetching(b"a,b\r\n")  # writes the status document; fetches once
    fetching.target.unlink()
    state = host.root / "docker"
    docker = host.root / "bin" / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat > /dev/null\n"
        'case " $* " in *" export status "*)\n'
        '    cat "$FAKE_STATUS_DOCUMENT"; exit 0 ;;\n'
        "esac\n"
        "printf 'a,b'\n"
        "sleep 20\n"
    )
    process = host.popen(
        "export",
        "fetch",
        EXPORT_ID,
        FAKE_STATUS_DOCUMENT=str(state / "status"),
    )
    deadline = time.monotonic() + 10
    while not (fetching.partial.exists() and fetching.partial.stat().st_size) and (
        time.monotonic() < deadline
    ):
        time.sleep(0.05)
    assert fetching.partial.exists()
    started = time.monotonic()
    try:
        if group:
            os.killpg(process.pid, getattr(signal, name))
        else:
            os.kill(process.pid, getattr(signal, name))
        process.communicate(timeout=20)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    assert time.monotonic() - started < 10
    assert process.returncode == status
    assert not fetching.partial.exists() and not fetching.target.exists()


def test_a_planted_partial_link_is_refused(fetching, tmp_path):
    """A link where the partial file goes is never written through."""
    fetching.directory.mkdir(mode=0o700)
    fetching.partial.symlink_to(tmp_path / "elsewhere")
    result, runs = fetching(b"a,b\r\n")
    assert result.returncode == 2 and len(runs) == 1
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.parametrize("name", ['say"hi', "back\\slash", "new\nline"])
def test_an_export_directory_that_cannot_be_printed_is_refused(
    host, fetching, tmp_path, name
):
    """A quote, backslash or control character is refused before mkdir."""
    directory = tmp_path / name
    result, runs = fetching(b"a,b\r\n", arguments=("--export-dir", str(directory)))
    assert result.returncode == 2 and runs == []
    assert not directory.exists()


def test_an_interrupted_prompt_leaves_no_writer_at_the_terminal(host, tmp_path):
    """Ctrl-C while a command waits at its prompt: the wrapper stops its input
    writer by name, so nothing is left reading the terminal (and swallowing
    the next line typed) even where pgrep cannot walk the process tree."""
    import signal
    import time

    host.answer({"ok": True})
    host.session()
    # A pgrep that finds nothing, as on a host without procps.
    pgrep = tmp_path / "bin" / "pgrep"
    pgrep.write_text("#!/bin/sh\nexit 1\n")
    pgrep.chmod(0o755)
    controller, terminal = pty.openpty()
    try:
        process = subprocess.Popen(
            [str(prompting(tmp_path)), "test", "act"],
            # The command reads the preamble, then waits for the answer.
            env={**host.environment, "FAKE_DOCKER_LINES": "2"},
            stdin=terminal,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        arguments = Path(host.environment["FAKE_DOCKER_ARGS"])
        for _ in range(100):
            if arguments.exists():
                break
            time.sleep(0.1)
        time.sleep(0.5)
        os.killpg(process.pid, signal.SIGINT)
        process.communicate(timeout=20)
        assert process.returncode == 130
        # Nothing of the run is left in its process group.
        for _ in range(50):
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.killpg(process.pid, signal.SIGKILL)
            pytest.fail("a process of the run (the input writer) was left behind")
    finally:
        os.close(terminal)
        os.close(controller)
