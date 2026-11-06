"""The local laptop environment's operator script (#476, OPS-10.09).

`tools/stewardship-local.sh` drives a Lima VM, so, like the upgrade script's
tests, these run its two halves with stand-ins: the laptop half with a
recording `limactl`, `ssh` and `git`, to show which commands reach the VM and
that nothing reaches it when the VM is not running or a confirmation was not
typed; and the VM half (`tools/stewardship-local-vm.sh`) with a `docker`
that answers as a healthy local deployment would, to pin the runbook's
first-installation order, the marker-file guard on every destructive
command, the snapshot and restore steps, and the clock-mode marker rule.
`deploy` and its rollback run the scripted upgrade's host half
(`tools/stewardship-upgrade-host.sh`, OPS-10.10) in local mode; here a
recording stand-in takes its place, since the host half's own behaviour is
pinned in test_upgrade_script.py. The real bring-up in the VM is the
documented human run in the developer guide; no Docker runs here.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from parishkit.parishsoft import DEFAULT_API_BASE_URL

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "stewardship-local.sh"
VM_SCRIPT = ROOT / "tools" / "stewardship-local-vm.sh"
HOST_SCRIPT = ROOT / "tools" / "stewardship-upgrade-host.sh"
# Where the laptop half puts the host half in the VM for deploy and rollback.
HOST_REMOTE = "/var/tmp/parishkit-local-upgrade-host.sh"
LIMA_YAML = ROOT / "deploy" / "stewardship" / "lima-local.yaml"
GUIDE = ROOT / "docs" / "guides" / "stewardship-local-environment.md"
SPEC = ROOT / "docs" / "specs" / "stewardship" / "local-environment" / "spec.md"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
IMAGE = f"parishkit-stewardship-local:{COMMIT}-1700000000"
LOCAL_IMAGE_PATTERN = r"parishkit-stewardship-local:[0-9a-f]{40}(-dirty)?-[0-9]{10}"
# The fixed VM-half argument prefix the laptop half sends before the command.
PREFIX = (
    "/opt/parishkit parishkit-local /etc/parishkit/stewardship-deployment.yaml "
    "/etc/parishkit/stewardship-local.env /var/tmp/parishkit-local-build "
    "/opt/parishkit-snapshots"
)


def executable_stub(bin_dir, name, body):
    """Write a bash stand-in into a directory that can run it."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    probe = bin_dir / "probe"
    if not probe.exists():
        probe.write_text("#!/usr/bin/env bash\nexit 0\n")
        probe.chmod(0o755)
        # The Compose test container mounts /tmp noexec, so the stand-ins
        # could not run there; the other CI jobs still run these tests.
        try:
            subprocess.run([str(probe)])
        except PermissionError:
            pytest.skip("the temporary directory cannot run the stand-in programs")
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


def test_both_halves_are_executable_bash_and_parse():
    """Shebang, executable bit and `bash -n` for both halves."""
    for script in (SCRIPT, VM_SCRIPT):
        assert script.read_text().startswith("#!/usr/bin/env bash\n"), script
        assert os.access(script, os.X_OK), script
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, script
        assert "set -euo pipefail" in script.read_text(), script
    # The VM's bash is 5.x: refusals inside $(...) must end the run too.
    assert "shopt -s inherit_errexit" in VM_SCRIPT.read_text()


def test_shellcheck_is_clean():
    """The specification requires shellcheck on the operator script."""
    if shutil.which("shellcheck") is None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("the CI runner lacks shellcheck")
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(
        ["shellcheck", "-s", "bash", str(SCRIPT), str(VM_SCRIPT), str(HOST_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_lima_configuration_matches_the_specification():
    """vz, arm64 Ubuntu 24.04, no mounts, Docker Engine, only 8443 and 8025."""
    text = LIMA_YAML.read_text()
    assert "vmType: vz" in text and "arch: aarch64" in text
    assert "ubuntu-24.04-server-cloudimg-arm64.img" in text
    assert "mounts: []" in text
    assert "system: false" in text and "user: false" in text
    assert "get.docker.com" in text
    assert text.count("hostIP: 127.0.0.1") == 2
    assert "guestPort: 8443" in text and "guestPort: 8025" in text
    assert "guestPortRange: [1, 65535]" in text and "ignore: true" in text


def test_the_guide_and_specification_are_cross_linked():
    """The guide exists, the spec links it, and the guide links the spec."""
    assert GUIDE.exists()
    assert "stewardship-local-environment.md" in SPEC.read_text()
    assert "local-environment/spec.md" in GUIDE.read_text()
    for command in (
        "vm create",
        "up",
        "start",
        "deploy",
        "deploy --rollback",
        "snapshot",
        "reset",
        "reset --reinstall",
        "status",
        "down",
        "ca",
        "rehearse",
        "spike",
        "finish-setup",
    ):
        assert f"`{command}" in GUIDE.read_text(), command
    # The spec's command table names every laptop command the script accepts.
    for command in (
        "`start`",
        "`deploy [--schema-change] [--bulk on\\|off] [--smtp-latency-ms N] "
        "[--parishsoft real\\|fake] [--mail-redirect ADDRESS\\|off]`",
        "`rehearse [--due-in MIN] [--send-only] [--timeout MIN] [--label NAME]`",
        "`spike [--families N] [--sources S] [--during-send] [--wait MIN] "
        "[--label NAME]`",
        "`deploy --rollback`",
        "`reset --reinstall`",
        "`down`",
        "`ca`",
        "`finish-setup`",
    ):
        assert command in SPEC.read_text(), command


def test_every_timeout_in_the_vm_half_logs_what_limit_and_elapsed():
    """The project rule: a timeout says what it waited for, the limit and the time.

    Both waiting helpers say so, and every Compose wait, stop and removal
    goes through one of them rather than a bare `--wait-timeout`.
    """
    text = VM_SCRIPT.read_text()
    for helper in ("wait_until() {", "timed() {"):
        start = text.index(helper)
        body = text[start : text.index("\n}\n", start)]
        assert "TIMEOUT" in body, helper
        for part in ("$what", "limit ${limit}s"):
            assert part in body, (helper, part)
    # Backslash-continued lines are one statement.
    for line in text.replace("\\\n", " ").splitlines():
        if "--wait-timeout" in line or " stop --timeout" in line or " down " in line:
            assert "timed " in line or line.strip().startswith("#"), line


# ---------------------------------------------------------------------------
# The laptop half: a recording limactl, ssh and git.

FAKE_LIMACTL = r"""
echo "limactl $*" >>"$FAKE_LOG"
case "$*" in
    "list "*"--format"*)
        [ -n "$FAKE_VM_STATUS" ] || { echo "No instance" >&2; exit 1; }
        echo "$FAKE_VM_STATUS" ;;
    "list "*) echo "NAME STATUS"; echo "$2 ${FAKE_VM_STATUS:-?}" ;;
esac
exit 0
"""

FAKE_SSH = r"""
n=$(ls "$FAKE_STDIN_DIR" | wc -l | tr -d ' ')
echo "ssh $*" >>"$FAKE_LOG"
cat >"$FAKE_STDIN_DIR/$n"
case "$*" in
    *" has-snapshot "*) [ -n "$FAKE_HAS_SNAPSHOT" ] ;;
    *" ca ; rc="*) [ -n "$FAKE_CA_FAIL" ] || echo "-----BEGIN CERTIFICATE-----" ;;
    *) exit 0 ;;
esac
"""

FAKE_GIT = r"""
case "$*" in
    "rev-parse --show-toplevel")
        [ -z "$FAKE_NO_CHECKOUT" ] || { echo "not a git repository" >&2; exit 128; }
        echo "$FAKE_CHECKOUT" ;;
    "rev-parse HEAD") echo 0123456789abcdef0123456789abcdef01234567 ;;
    "rev-parse --short HEAD") echo 0123456 ;;
    "rev-parse --abbrev-ref HEAD") echo pr/topic ;;
    "diff --quiet HEAD --") exit "${FAKE_DIRTY:-0}" ;;
    "ls-files -z") printf 'README.md\0deploy/stewardship/Dockerfile\0gone.txt\0' ;;
esac
exit 0
"""


# The Mac's free space as `df -Pk` reports it (FAKE_MAC_FREE_KB, default 100 GiB).
FAKE_MAC_DF = r"""
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "fake 1 1 ${FAKE_MAC_FREE_KB-104857600} 1% /"
"""
MAC_FREE = "104857600"


def run_local(tmp_path, *args, status="Running", env=None, stdin=""):
    """Run the laptop half with the stand-ins; return (result, calls, stdins)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    log = tmp_path / "calls.log"
    log.touch()
    stdin_dir = tmp_path / "stdin"
    stdin_dir.mkdir(exist_ok=True)
    checkout = tmp_path / "checkout"
    (checkout / "deploy" / "stewardship").mkdir(parents=True, exist_ok=True)
    (checkout / "README.md").write_text("readme\n")
    (checkout / "deploy" / "stewardship" / "Dockerfile").write_text("FROM x\n")
    executable_stub(bin_dir, "limactl", FAKE_LIMACTL)
    executable_stub(bin_dir, "ssh", FAKE_SSH)
    executable_stub(bin_dir, "git", FAKE_GIT)
    executable_stub(bin_dir, "df", FAKE_MAC_DF)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    base = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PARISHKIT_", "STEWARDSHIP_USERS_")) and k != "LIMA_HOME"
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={
            **base,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(home),
            "FAKE_LOG": str(log),
            "FAKE_STDIN_DIR": str(stdin_dir),
            "FAKE_CHECKOUT": str(checkout),
            "FAKE_VM_STATUS": status,
            **(env or {}),
        },
        input=stdin,
        text=True,
        capture_output=True,
        timeout=60,
    )
    calls = log.read_text().splitlines()
    stdins = [
        p.read_bytes() for p in sorted(stdin_dir.iterdir(), key=lambda p: int(p.name))
    ]
    return result, calls, stdins


def ssh_calls(calls):
    return [c for c in calls if c.startswith("ssh ")]


def vm_commands(calls):
    """The VM-half command lines, in order, without the fixed prefix."""
    return [
        c.split(f"{PREFIX} ")[1].split(" ;")[0] for c in ssh_calls(calls) if PREFIX in c
    ]


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("bogus",),
        ("vm",),
        ("vm", "bogus"),
        ("vm", "start", "x"),
        ("up", "--bogus"),
        ("up", "--families"),
        ("start", "x"),
        ("sign-in",),
        ("sign-in", "--email"),
        ("snapshot", "x"),
        ("snapshot", "--reinstall"),
        ("down", "x"),
        ("status", "x"),
        ("deploy", "--bogus"),
        ("deploy", "--rollback", "--schema-change"),
        ("deploy", "--rollback", "x"),
        ("deploy", "--bulk"),
        ("deploy", "--bulk", "yes"),
        ("deploy", "--smtp-latency-ms"),
        ("deploy", "--smtp-latency-ms", "fast"),
        ("deploy", "--smtp-latency-ms", "5001"),
        ("deploy", "--rollback", "--bulk", "on"),
        ("deploy", "--rollback", "--smtp-latency-ms", "600"),
        ("deploy", "--parishsoft"),
        ("deploy", "--parishsoft", "production"),
        ("deploy", "--rollback", "--parishsoft", "real"),
        ("deploy", "--mail-redirect"),
        ("deploy", "--mail-redirect", "Jeff <jeff@example.org>"),
        ("deploy", "--mail-redirect", "a@example.org,b@example.org"),
        ("deploy", "--mail-redirect", "nobody"),
        ("deploy", "--rollback", "--mail-redirect", "jeff@example.org"),
        ("rehearse", "--bogus"),
        ("rehearse", "--due-in"),
        ("finish-setup", "x"),
        ("deploy", "--smtp-latency-ms", "123456"),
        ("snapshot", "save"),
        ("snapshot", "save", "a", "--force"),
        ("snapshot", "list", "x"),
        ("snapshot", "delete"),
        ("snapshot", "restore"),
        ("switch",),
        ("switch", "a", "b"),
        ("reset", "--bogus"),
        ("reset", "a", "b"),
        ("seed", "--testing", "x"),
    ],
)
def test_bad_usage_is_refused_before_the_vm(tmp_path, args):
    """Anything but a documented command prints the usage and reaches no VM."""
    result, calls, _ = run_local(tmp_path, *args)
    assert result.returncode == 2, args
    assert "Usage:" in result.stderr and "vm create" in result.stderr
    assert ssh_calls(calls) == []


def test_help_prints_the_usage_and_exits_zero(tmp_path):
    """-h, --help and help are not errors."""
    for flag in ("-h", "--help", "help"):
        result, calls, _ = run_local(tmp_path / flag.strip("-"), flag)
        assert result.returncode == 0, flag
        assert "Usage:" in result.stderr and "reset --reinstall" in result.stderr
        assert calls == []


def test_a_stopped_or_missing_vm_is_refused_before_ssh(tmp_path):
    """`up` names `vm start` for a stopped VM and `vm create` for none."""
    result, calls, _ = run_local(tmp_path / "stopped", "up", status="Stopped")
    assert result.returncode == 1 and "vm start" in result.stderr
    assert ssh_calls(calls) == []
    result, calls, _ = run_local(tmp_path / "missing", "up", status="")
    assert result.returncode == 1 and "vm create" in result.stderr
    assert "PARISHKIT_LOCAL_VM" in result.stderr
    assert ssh_calls(calls) == []


@pytest.mark.parametrize(
    "root", ["/", "/opt", "relative/path", "/opt/../etc", "/opt/parishkit/.."]
)
def test_an_unsafe_runtime_root_is_refused_first(tmp_path, root):
    """The root is wiped by reset, so it must be absolute, deep and without `..`."""
    result, calls, _ = run_local(tmp_path, "status", env={"PARISHKIT_LOCAL_ROOT": root})
    assert result.returncode == 1 and "PARISHKIT_LOCAL_ROOT" in result.stderr
    assert calls == []


def test_up_packs_the_checkout_then_runs_the_vm_half_once(tmp_path):
    """The tracked files that exist go first; then the VM half with its arguments."""
    result, calls, stdins = run_local(tmp_path, "up")
    assert result.returncode == 0, result.stderr
    ssh = ssh_calls(calls)
    assert len(ssh) == 2
    assert "sudo tar -xzf - -C '/var/tmp/parishkit-local-build'" in ssh[0]
    # The archive holds the two files that exist, never the deleted one.
    names = (
        subprocess.run(["tar", "-tzf", "-"], input=stdins[0], capture_output=True)
        .stdout.decode()
        .split()
    )
    assert sorted(names) == ["README.md", "deploy/stewardship/Dockerfile"]
    # VM half: uploaded to a file, run as root with the fixed prefix, then
    # the command and its arguments; the tag matches the LOCAL pattern.
    assert "sudo bash" in ssh[1]
    match = re.search(rf"{PREFIX} up (\S+) 100 admin@example\.test 1 ; rc=", ssh[1])
    assert match, ssh[1]
    assert re.fullmatch(LOCAL_IMAGE_PATTERN, match.group(1))
    assert "-dirty" not in match.group(1)
    assert stdins[1] == VM_SCRIPT.read_bytes()
    # It says which checkout and commit it builds.
    assert f"Building checkout {tmp_path / 'checkout'} (pr/topic at 0123456)" in (
        result.stdout
    )
    assert "stewardship-local.sh ca" in result.stdout


def test_up_outside_a_checkout_is_refused_before_ssh(tmp_path):
    """No work tree, no `cd ""`: the refusal says to run from the checkout."""
    result, calls, _ = run_local(tmp_path, "up", env={"FAKE_NO_CHECKOUT": "1"})
    assert result.returncode == 1 and "Not inside a git work tree" in result.stderr
    assert ssh_calls(calls) == []


def test_up_takes_the_options_and_marks_a_dirty_checkout(tmp_path):
    """--families and the environment reach the VM half; -dirty when edited."""
    result, calls, _ = run_local(
        tmp_path,
        "up",
        "--families",
        "20",
        env={
            "FAKE_DIRTY": "1",
            "PARISHKIT_LOCAL_ADMIN_EMAIL": "dev@example.test",
            "PARISHKIT_LOCAL_DEBUG_LOGGING": "0",
            "PARISHKIT_LOCAL_VM": "pk-local",
            "LIMA_HOME": "/lima/elsewhere",
        },
    )
    assert result.returncode == 0, result.stderr
    assert re.search(
        rf"{PREFIX} up parishkit-stewardship-local:{COMMIT}-dirty-[0-9]{{10}} "
        r"20 dev@example\.test 0 ; rc=",
        ssh_calls(calls)[1],
    )
    # The instance name selects Lima's ssh configuration and alias; LIMA_HOME
    # moves the configuration directory.
    assert "lima-pk-local" in ssh_calls(calls)[1]
    assert "-F /lima/elsewhere/pk-local/ssh.config" in ssh_calls(calls)[1]
    assert "-o ControlPath=none" in ssh_calls(calls)[1]
    result, calls, _ = run_local(tmp_path / "bad", "up", "--families", "0")
    assert result.returncode == 1 and "positive integer" in result.stderr
    assert ssh_calls(calls) == []


def test_deploy_builds_then_uploads_the_host_half_and_runs_it(tmp_path):
    """Pack, build, upload the shared host half, then the VM half's deploy."""
    result, calls, stdins = run_local(tmp_path, "deploy")
    assert result.returncode == 0, result.stderr
    ssh = ssh_calls(calls)
    assert len(ssh) == 4
    assert "sudo tar -xzf" in ssh[0]
    tag = re.search(rf"{PREFIX} build (\S+) ; rc=", ssh[1]).group(1)
    assert re.fullmatch(LOCAL_IMAGE_PATTERN, tag)
    # The host half is the file Production's upgrade script uploads, byte for
    # byte, installed at the fixed path the VM half is then told about.
    assert ssh[2].endswith(f"sudo install -m 0644 /dev/stdin '{HOST_REMOTE}'")
    assert stdins[2] == HOST_SCRIPT.read_bytes()
    assert vm_commands(calls) == [f"build {tag}", f"deploy {tag} 0 {HOST_REMOTE}"]
    # --schema-change is Production's STEWARDSHIP_SCHEMA_CHANGE=1.
    _, calls, _ = run_local(tmp_path / "schema", "deploy", "--schema-change")
    assert vm_commands(calls)[-1].startswith("deploy parishkit-stewardship-local:")
    assert vm_commands(calls)[-1].endswith(f" 1 {HOST_REMOTE}")
    # A stopped VM refuses before anything is packed.
    result, calls, _ = run_local(tmp_path / "stopped", "deploy", status="Stopped")
    assert result.returncode == 1 and ssh_calls(calls) == []


def test_deploy_passes_the_rehearsal_rendering_choices(tmp_path):
    """--bulk and --smtp-latency-ms reach the VM half; unset ones say keep."""
    result, calls, _ = run_local(tmp_path, "deploy", "--bulk", "on")
    assert result.returncode == 0, result.stderr
    assert vm_commands(calls)[-1].endswith(f" 0 {HOST_REMOTE} on keep")
    _, calls, _ = run_local(
        tmp_path / "both", "deploy", "--smtp-latency-ms", "600", "--bulk", "off"
    )
    assert vm_commands(calls)[-1].endswith(f" 0 {HOST_REMOTE} off 600")
    _, calls, _ = run_local(tmp_path / "latency", "deploy", "--smtp-latency-ms", "0")
    assert vm_commands(calls)[-1].endswith(f" 0 {HOST_REMOTE} keep 0")
    # Base 10 whatever the leading zeros: 0600 is 600, not octal 384.
    _, calls, _ = run_local(tmp_path / "zeros", "deploy", "--smtp-latency-ms", "0600")
    assert vm_commands(calls)[-1].endswith(f" 0 {HOST_REMOTE} keep 600")
    # Which ParishSoft pk-local reads (#1399) is a third, trailing choice.
    _, calls, _ = run_local(tmp_path / "real", "deploy", "--parishsoft", "real")
    assert vm_commands(calls)[-1].endswith(f" 0 {HOST_REMOTE} keep keep real")
    _, calls, _ = run_local(
        tmp_path / "fake", "deploy", "--bulk", "on", "--parishsoft", "fake"
    )
    assert vm_commands(calls)[-1].endswith(f" 0 {HOST_REMOTE} on keep fake")
    # The mail redirect (#1399) is a fourth, trailing choice.
    _, calls, _ = run_local(
        tmp_path / "redirect", "deploy", "--mail-redirect", "jeff@example.org"
    )
    assert vm_commands(calls)[-1].endswith(
        f" 0 {HOST_REMOTE} keep keep keep jeff@example.org"
    )
    _, calls, _ = run_local(tmp_path / "off", "deploy", "--mail-redirect", "off")
    assert vm_commands(calls)[-1].endswith(f" 0 {HOST_REMOTE} keep keep keep off")


def test_deploy_passes_the_users_upgrade_inputs_to_the_vm_half(tmp_path):
    """The STEWARDSHIP_USERS_* variables (#952) reach the host half through sudo."""
    users = {
        "STEWARDSHIP_USERS_OPERATION": "11111111-2222-4333-8444-555555555555",
        "STEWARDSHIP_USERS_OPERATOR": "Test Operator",
        "STEWARDSHIP_USERS_REASON": "Rehearsal",
        "STEWARDSHIP_USERS_ADMINISTRATORS": "admin@example.test",
    }
    result, calls, _ = run_local(tmp_path, "deploy", "--schema-change", env=users)
    assert result.returncode == 0, result.stderr
    deploy = ssh_calls(calls)[-1]
    assert vm_commands(calls)[-1].endswith(f" 1 {HOST_REMOTE}")
    assert (
        "sudo env STEWARDSHIP_USERS_OPERATION=11111111-2222-4333-8444-555555555555 "
        "STEWARDSHIP_USERS_OPERATOR=Test\\ Operator STEWARDSHIP_USERS_REASON=Rehearsal "
        "STEWARDSHIP_USERS_ADMINISTRATORS=admin@example.test bash "
    ) in deploy
    # Every other VM-half command, and a deploy without them, has no prefix.
    assert not any("sudo env" in call for call in ssh_calls(calls)[:-1])
    _, calls, _ = run_local(tmp_path / "plain", "deploy")
    assert not any("sudo env" in call for call in ssh_calls(calls))
    # A rollback never converts.
    result, calls, _ = run_local(
        tmp_path / "rollback", "deploy", "--rollback", env=users
    )
    assert result.returncode != 0 and ssh_calls(calls) == []


def test_rehearse_checks_its_options_then_runs_the_vm_half(tmp_path):
    """Defaults, explicit options, and refusals before ssh."""
    result, calls, _ = run_local(tmp_path, "rehearse")
    assert result.returncode == 0, result.stderr
    assert vm_commands(calls) == ["rehearse 5 0 60 run"]
    _, calls, _ = run_local(
        tmp_path / "options",
        "rehearse",
        "--due-in",
        "130",
        "--send-only",
        "--timeout",
        "90",
        "--label",
        "bulk-1100",
    )
    assert vm_commands(calls) == ["rehearse 130 1 90 bulk-1100"]
    for n, args in enumerate(
        (
            ("--due-in", "1"),
            ("--due-in", "181"),
            ("--due-in", "x"),
            ("--timeout", "0"),
            ("--label", "a b"),
            ("--label", "x" * 41),
        )
    ):
        result, calls, _ = run_local(tmp_path / f"bad{n}", "rehearse", *args)
        assert result.returncode == 1, args
        assert ssh_calls(calls) == [], args


def test_spike_checks_its_options_then_runs_the_vm_half(tmp_path):
    """Defaults, explicit options, and refusals before ssh."""
    result, calls, _ = run_local(tmp_path, "spike")
    assert result.returncode == 0, result.stderr
    assert vm_commands(calls) == ["spike 200 8 0 20 spike"]
    _, calls, _ = run_local(
        tmp_path / "options",
        "spike",
        "--families",
        "1100",
        "--sources",
        "16",
        "--during-send",
        "--wait",
        "90",
        "--label",
        "launch-1100",
    )
    assert vm_commands(calls) == ["spike 1100 16 1 90 launch-1100"]
    for n, args in enumerate(
        (
            ("--families", "0"),
            ("--families", "5001"),
            ("--sources", "33"),
            ("--sources", "x"),
            ("--wait", "0"),
            ("--wait", "241"),
            ("--label", "a b"),
            ("--bogus",),
        )
    ):
        result, calls, _ = run_local(tmp_path / f"bad{n}", "spike", *args)
        assert result.returncode != 0, args
        assert ssh_calls(calls) == [], args


def test_deploy_rollback_uploads_the_host_half_without_building(tmp_path):
    """--rollback needs no checkout: upload, then the VM half's rollback."""
    result, calls, stdins = run_local(tmp_path, "deploy", "--rollback")
    assert result.returncode == 0, result.stderr
    ssh = ssh_calls(calls)
    assert len(ssh) == 2 and "tar" not in ssh[0]
    assert ssh[0].endswith(f"sudo install -m 0644 /dev/stdin '{HOST_REMOTE}'")
    assert stdins[0] == HOST_SCRIPT.read_bytes()
    assert vm_commands(calls) == [f"rollback {HOST_REMOTE}"]


def test_snapshot_and_reset_name_the_right_snapshot(tmp_path):
    """Default post-setup; --seeded selects the seeded one; reset restores."""
    _, calls, _ = run_local(tmp_path / "a", "snapshot")
    assert vm_commands(calls) == [f"snapshot post-setup 1 {MAC_FREE} 8"]
    _, calls, _ = run_local(tmp_path / "b", "snapshot", "--seeded")
    assert vm_commands(calls) == [f"snapshot seeded 1 {MAC_FREE} 8"]
    _, calls, _ = run_local(
        tmp_path / "c", "reset", "--seeded", env={"FAKE_HAS_SNAPSHOT": "1"}
    )
    assert vm_commands(calls) == ["has-snapshot seeded", "reset seeded"]
    result, calls, _ = run_local(tmp_path / "d", "reset", "--seeded")
    assert result.returncode == 1 and "No seeded snapshot" in result.stderr
    assert vm_commands(calls) == ["has-snapshot seeded"]


def test_named_snapshots_pass_the_name_the_mac_space_and_the_cap(tmp_path):
    """save, --replace, list, the cap override and reset NAME (#1395)."""
    _, calls, _ = run_local(tmp_path / "save", "snapshot", "save", "testing")
    assert vm_commands(calls) == [f"snapshot testing 0 {MAC_FREE} 8"]
    _, calls, _ = run_local(
        tmp_path / "replace",
        "snapshot",
        "save",
        "pr-1395",
        "--replace",
        env={"PARISHKIT_LOCAL_SNAPSHOT_LIMIT": "3", "FAKE_MAC_FREE_KB": "42"},
    )
    assert vm_commands(calls) == ["snapshot pr-1395 1 42 3"]
    _, calls, _ = run_local(tmp_path / "list", "snapshot", "list")
    assert vm_commands(calls) == [f"snapshot-list {MAC_FREE}"]
    _, calls, _ = run_local(
        tmp_path / "reset", "reset", "testing", env={"FAKE_HAS_SNAPSHOT": "1"}
    )
    assert vm_commands(calls) == ["has-snapshot testing", "reset testing"]
    # A missing named snapshot is refused; only post-setup falls back to a
    # reinstall, and a snapshot named "reinstall" is just a name.
    for name in ("testing", "reinstall"):
        result, calls, _ = run_local(tmp_path / f"none-{name}", "reset", name)
        assert result.returncode == 1 and f"No {name} snapshot" in result.stderr
        assert vm_commands(calls) == [f"has-snapshot {name}"]


@pytest.mark.parametrize(
    "args",
    [
        ("snapshot", "save", "Testing"),
        ("snapshot", "save", "-x"),
        ("snapshot", "save", "a" * 33),
        ("snapshot", "save", "a/b"),
        ("snapshot", "delete", "../x"),
        ("snapshot", "restore", "a_b"),
        ("switch", "UPPER"),
        ("reset", "a.b"),
    ],
)
def test_a_bad_snapshot_name_is_refused_before_ssh(tmp_path, args):
    """Names are 1-32 of [a-z0-9-], not starting with '-'."""
    result, calls, _ = run_local(tmp_path, *args)
    assert result.returncode == 1, args
    assert "1-32 lowercase letters" in result.stderr
    assert ssh_calls(calls) == []


def test_restore_and_switch_upload_the_host_half_then_switch(tmp_path):
    """The switch's upgrade path runs the host half, so it is uploaded first."""
    for args in (("snapshot", "restore", "seeded"), ("switch", "seeded")):
        result, calls, stdins = run_local(tmp_path / args[0], *args)
        assert result.returncode == 0, result.stderr
        ssh = ssh_calls(calls)
        assert ssh[0].endswith(f"sudo install -m 0644 /dev/stdin '{HOST_REMOTE}'")
        assert stdins[0] == HOST_SCRIPT.read_bytes()
        assert vm_commands(calls) == [f"snapshot-restore seeded {HOST_REMOTE}"]


def test_snapshot_delete_needs_the_typed_snapshot_name(tmp_path):
    """The wrong name deletes nothing; a missing snapshot is never asked about."""
    result, calls, _ = run_local(tmp_path / "missing", "snapshot", "delete", "old")
    assert result.returncode == 1 and "No old snapshot" in result.stderr
    assert vm_commands(calls) == ["has-snapshot old"]
    result, calls, _ = run_local(
        tmp_path / "wrong",
        "snapshot",
        "delete",
        "old",
        stdin="seeded\n",
        env={"FAKE_HAS_SNAPSHOT": "1"},
    )
    assert result.returncode == 1 and "Not confirmed" in result.stderr
    assert "Type the snapshot name (old)" in result.stderr
    assert vm_commands(calls) == ["has-snapshot old"]
    result, calls, _ = run_local(
        tmp_path / "right",
        "snapshot",
        "delete",
        "old",
        stdin="old\n",
        env={"FAKE_HAS_SNAPSHOT": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert vm_commands(calls) == ["has-snapshot old", "snapshot-delete old"]


def test_seed_testing_uploads_the_host_half_and_logs(tmp_path):
    """seed --testing switches (so it needs the host half) and logs as seed does."""
    result, calls, stdins = run_local(tmp_path, "seed", "--testing")
    assert result.returncode == 0, result.stderr
    assert stdins[0] == HOST_SCRIPT.read_bytes()
    assert vm_commands(calls) == [f"seed-testing {HOST_REMOTE}"]
    assert (tmp_path / "home" / ".parishkit-local" / "seed.log").exists()


def test_an_unreadable_mac_free_space_is_refused_before_ssh(tmp_path):
    """A df answer that is not a number never silently skips the Mac check."""
    for args in (("snapshot", "save", "x"), ("snapshot", "--seeded"),
                 ("snapshot", "list")):  # fmt: skip
        result, calls, _ = run_local(
            tmp_path / args[-1].strip("-"), *args, env={"FAKE_MAC_FREE_KB": ""}
        )
        assert result.returncode == 1, args
        assert "Could not read the Mac's free space" in result.stderr
        assert vm_commands(calls) == []
    # status still reports, without the Mac's figure.
    result, calls, _ = run_local(
        tmp_path / "status", "status", env={"FAKE_MAC_FREE_KB": ""}
    )
    assert result.returncode == 0 and vm_commands(calls) == ["status ''"]


def test_switch_and_seed_testing_pass_the_users_upgrade_inputs(tmp_path):
    """A pre-#952 snapshot's upgrade path needs them, as a deploy does."""
    users = {
        "STEWARDSHIP_USERS_OPERATION": "11111111-2222-4333-8444-555555555555",
        "STEWARDSHIP_USERS_OPERATOR": "Op",
        "STEWARDSHIP_USERS_REASON": "Switch",
        "STEWARDSHIP_USERS_ADMINISTRATORS": "admin@example.test",
    }
    for args in (("switch", "seeded"), ("seed", "--testing")):
        _, calls, _ = run_local(tmp_path / args[0], *args, env=users)
        last = ssh_calls(calls)[-1]
        assert "sudo env STEWARDSHIP_USERS_OPERATION=11111111-" in last, args
        assert "STEWARDSHIP_USERS_ADMINISTRATORS=admin@example.test bash " in last
        assert not any("sudo env" in c for c in ssh_calls(calls)[:-1])
    _, calls, _ = run_local(tmp_path / "plain", "switch", "seeded")
    assert not any("sudo env" in c for c in ssh_calls(calls))


def test_reset_without_a_snapshot_needs_the_typed_instance_name(tmp_path):
    """The wrong name changes nothing; the right one builds, wipes, installs."""
    result, calls, _ = run_local(tmp_path / "wrong", "reset", stdin="nope\n")
    assert result.returncode == 1 and "Not confirmed" in result.stderr
    assert "Type the instance name (parishkit-local)" in result.stderr
    assert vm_commands(calls) == ["has-snapshot post-setup"]
    result, calls, _ = run_local(tmp_path / "right", "reset", stdin="parishkit-local\n")
    assert result.returncode == 0, result.stderr
    ssh = ssh_calls(calls)
    assert "has-snapshot post-setup" in ssh[0]
    # The image is built from the packed checkout before anything is removed.
    assert "sudo tar -xzf" in ssh[1]
    assert re.search(rf"{PREFIX} build (\S+) ; rc=", ssh[2])
    assert f"{PREFIX} wipe ; rc=" in ssh[3]
    tag = re.search(rf"{PREFIX} up (\S+) 100 ", ssh[4]).group(1)
    assert re.search(rf"{PREFIX} build {re.escape(tag)} ; rc=", ssh[2])
    assert len(ssh) == 5


def test_reset_reinstall_wipes_and_installs_even_with_a_snapshot(tmp_path):
    """--reinstall never probes for a snapshot; it needs the typed name, then wipes."""
    result, calls, _ = run_local(
        tmp_path / "wrong",
        "reset",
        "--reinstall",
        stdin="no\n",
        env={"FAKE_HAS_SNAPSHOT": "1"},
    )
    assert result.returncode == 1 and "Not confirmed" in result.stderr
    assert ssh_calls(calls) == []
    result, calls, _ = run_local(
        tmp_path / "right",
        "reset",
        "--reinstall",
        stdin="parishkit-local\n",
        env={"FAKE_HAS_SNAPSHOT": "1"},
    )
    assert result.returncode == 0, result.stderr
    ssh = ssh_calls(calls)
    assert "sudo tar -xzf" in ssh[0]
    assert [c.split()[0] for c in vm_commands(calls)] == ["build", "wipe", "up"]
    assert not any("has-snapshot" in c for c in ssh)
    assert "its CA is new" in result.stdout


def test_the_simple_commands_reach_the_vm_half(tmp_path):
    """start, down, status, sign-in, seed and reseed pass through with arguments."""
    _, calls, _ = run_local(tmp_path / "start", "start")
    assert vm_commands(calls) == ["start"]
    _, calls, _ = run_local(tmp_path / "down", "down")
    assert vm_commands(calls) == ["down"]
    result, calls, _ = run_local(tmp_path / "status", "status")
    assert calls[0].startswith("limactl list parishkit-local")
    assert vm_commands(calls) == [f"status {MAC_FREE}"]
    # A stopped VM is a status, not an error.
    result, calls, _ = run_local(tmp_path / "status2", "status", status="Stopped")
    assert result.returncode == 0 and ssh_calls(calls) == []
    _, calls, _ = run_local(tmp_path / "signin", "sign-in", "--email", "a@example.test")
    assert vm_commands(calls) == ["sign-in a@example.test"]
    _, calls, _ = run_local(tmp_path / "finish", "finish-setup")
    assert vm_commands(calls) == ["finish-setup"]
    _, calls, _ = run_local(
        tmp_path / "fake", "fake-parishsoft", "member", "500010", "email=x@example.test"
    )
    assert vm_commands(calls) == ["fake-parishsoft member 500010 email=x@example.test"]
    result, calls, _ = run_local(tmp_path / "seed", "seed", "--response-scale", "4")
    assert vm_commands(calls) == ["seed 4"]
    assert (tmp_path / "seed" / "home" / ".parishkit-local" / "seed.log").exists()
    _, calls, _ = run_local(
        tmp_path / "reseed", "reseed", env={"FAKE_HAS_SNAPSHOT": "1"}
    )
    assert vm_commands(calls) == [
        "has-snapshot post-setup",
        "reset post-setup",
        "seed 1",
    ]


def test_ca_saves_the_certificate_and_prints_the_trust_command(tmp_path):
    """The certificate lands under ~/.parishkit-local; trust is never changed."""
    result, calls, _ = run_local(tmp_path, "ca")
    assert result.returncode == 0, result.stderr
    state = tmp_path / "home" / ".parishkit-local"
    saved = state / "caddy-root.crt"
    assert saved.read_text().startswith("-----BEGIN CERTIFICATE-----")
    assert "security add-trusted-cert" in result.stdout
    assert not any("security" in c for c in calls)
    # An answer that is not a certificate leaves no file behind.
    result, _, _ = run_local(tmp_path / "fail", "ca", env={"FAKE_CA_FAIL": "1"})
    assert result.returncode == 1 and "Could not fetch" in result.stderr
    assert list((tmp_path / "fail" / "home" / ".parishkit-local").iterdir()) == []


def test_vm_create_uses_the_tracked_lima_configuration(tmp_path):
    """A new instance is created from deploy/stewardship/lima-local.yaml."""
    result, calls, _ = run_local(tmp_path, "vm", "create", status="")
    assert result.returncode == 0, result.stderr
    assert any(
        c.startswith("limactl start --name=parishkit-local --tty=false")
        and c.endswith("deploy/stewardship/lima-local.yaml")
        for c in calls
    )
    result, calls, _ = run_local(tmp_path / "exists", "vm", "create")
    assert result.returncode == 1 and "already exists" in result.stderr
    _, calls, _ = run_local(tmp_path / "stop", "vm", "stop")
    assert "limactl stop parishkit-local" in calls


# ---------------------------------------------------------------------------
# The VM half: a stand-in docker that behaves like a healthy local deployment.

# The stand-in records every call. `provision-runtime` creates what the real
# command would leave behind that the script then writes into (the service
# directory, the static destination, the credential directory and run/).
# FAKE_COMPLETED is what the setup-completion query answers (default f);
# FAKE_IMAGE_EXISTS makes `image inspect` find the tag; FAKE_DEPS_FAIL makes
# the postgres/valkey start fail; FAKE_LEFTOVER makes a container survive
# `down`; FAKE_KILLED reports the worker as killed by the stop timeout.
# The wizard and credentials seeder steps answer FAKE_SEED_RESULT (default:
# a frozen attempt with PS_ID and GW_ID), and setup completes once both are
# acknowledged.
FAKE_DOCKER = r"""
echo "$*" >>"$FAKE_LOG"
last=${*: -1}
case "$*" in
    "image inspect "*) [ -n "$FAKE_IMAGE_EXISTS" ] || exit 1 ;;
    *" provision-runtime --config "*)
        mkdir -p "$FAKE_ROOT/config/services" "$FAKE_ROOT/cache/static" \
            "$FAKE_ROOT/run" "$FAKE_ROOT/credentials/google_oauth" \
            "$FAKE_ROOT/credentials/backup_data"
        for f in compose-initial.json compose.json compose-slack.json; do
            echo '{"services": {"web": {}}}' >"$FAKE_ROOT/config/services/$f"
        done
        echo '{"provisioned": true}' ;;
    *" collect-static --destination "*) echo '{"collected": 1}' ;;
    *" backup-keygen --destination "*)
        echo '{"public_key": "age1fakepublickey",' \
            '"recipient_fingerprint": "SHA256:fake"}' ;;
    *"--entrypoint python"*)
        name=$(printf '%s' "$last" | sed -n 's/.*import \([A-Z_]*\) as value.*/\1/p')
        if [ "$name" = STEPS ] && [ -n "$FAKE_STEPS" ]; then
            printf '%s' "$FAKE_STEPS"
            exit 0
        fi
        printf "constant-%s" "$name" ;;
    *" local-seed") echo "invalid command; choose ..." >&2; exit 2 ;;
    *"SELECT EXISTS"*)
        # Acknowledging both credentials by hand completes setup, and so do
        # the consumers themselves (#2228): FAKE_COMPLETES_AFTER answers f
        # that many times, then t.
        seen=$(( $(cat "$FAKE_ROOT/.fake-exists" 2>/dev/null || echo 0) + 1 ))
        echo "$seen" >"$FAKE_ROOT/.fake-exists"
        if [ "$(cat "$FAKE_ROOT/.fake-acked" 2>/dev/null | wc -l)" -ge 2 ]; then
            echo t
        elif [ "$seen" -gt "${FAKE_COMPLETES_AFTER:-999999}" ]; then
            echo t
        else
            echo "${FAKE_COMPLETED:-f}"
        fi ;;
    *"interval '10 minutes'"*) echo "${FAKE_OVERDUE:-0}" ;;
    *" cat /tmp/stewardship-consumer/background.json")
        echo '{"deferred": [], "receipts": {}}' ;;
    *" local-seed --step wizard "*|*" local-seed --step credentials "*)
        frozen='{"result": "frozen", "requests": {"parishsoft": "'$PS_ID'",'
        frozen+=' "google_workspace": "'$GW_ID'"}}'
        echo "${FAKE_SEED_RESULT:-$frozen}" ;;
    *"count(*) FROM stewardship_secret_request"*) echo "${FAKE_AWAITING:-2}" ;;
    *" acknowledge-credential "*) echo "$last" >>"$FAKE_ROOT/.fake-acked" ;;
    *"FROM django_migrations"*)
        [ -z "$FAKE_MIGRATIONS" ] || tr ';' '\n' <<<"$FAKE_MIGRATIONS" ;;
    *"FROM stewardship_system_configuration"*)
        summary=${FAKE_SUMMARY:-$'testing\tdraft\t100\ttesting@example.test'}
        printf '%s\n' "$summary" ;;
    "compose ls --all --format json") echo '[]' ;;
    *" up --detach --wait --wait-timeout 180 postgres valkey")
        [ -z "$FAKE_DEPS_FAIL" ] || { echo "postgres refused" >&2; exit 1; } ;;
    *" ps --all --quiet") [ -z "$FAKE_LEFTOVER" ] || echo deadbeef ;;
    *" ps --all --format json"|*" ps --format json")
        for s in web worker caddy; do
            code=0
            [ -z "$FAKE_KILLED" ] || [ "$s" != worker ] || code=137
            fields="\"State\":\"running\",\"Health\":\"healthy\""
            echo "{\"Service\":\"$s\",$fields,\"ExitCode\":$code}"
        done ;;
    *" run --rm -T "*) echo '{"ok": true}' ;;
    *" fake-parishsoft-edit show"*) echo "SHOWN" ;;
    *" fake-parishsoft-edit "*)
        # The edit command reads the fake's document and prints the new one;
        # FAKE_EDIT_FAIL refuses the edit as the real command would.
        [ -z "$FAKE_EDIT_FAIL" ] || { echo "ERROR: There is no Member 1" >&2; exit 2; }
        # FAKE_EDIT_EMPTY answers as `--help` does: exit 0 and no document.
        [ -z "$FAKE_EDIT_EMPTY" ] || exit 0
        echo '{"seed": 7, "edited": true}' ;;
    *" local-seed --step pledge-period "*)
        # FAKE_PERIOD_FAIL: the step refuses (no campaign collecting pledges).
        [ -z "$FAKE_PERIOD_FAIL" ] || { echo "seed step refused" >&2; exit 2; }
        echo '{"fund": 301, "start": "2027-01-01"}' ;;
    *" ps -q postgres") [ -n "$FAKE_NO_POSTGRES" ] || echo pgcontainer ;;
    *" local-spike --profile local "*)
        # One shard's document; FAKE_SHARD_FAIL makes shard 1 fail.
        case "$*" in *"--shard 1 "*) [ -z "$FAKE_SHARD_FAIL" ] || exit 3 ;; esac
        echo '{"check": "spike", "families": 2}' ;;
esac
exit 0
"""

FAKE_INSTALL = r"""
if [[ " $* " == *" -d "* ]]; then
    for a in "$@"; do
        case "$a" in -*|10001|0[0-7][0-7][0-7]) ;; *) mkdir -p "$a" ;; esac
    done
else
    cp "${@: -2:1}" "${@: -1}"
fi
"""

# BSD date has no -d; the stand-in drops "-d <when>" and runs the real date.
FAKE_DATE = r"""
args=() skip=0
for a in "$@"; do
    if [ "$skip" = 1 ]; then skip=0; continue; fi
    if [ "$a" = -d ]; then skip=1; continue; fi
    args+=("$a")
done
exec /bin/date "${args[@]}"
"""


FAKE_VM_DF = r"""
if [ "$*" = "-h /" ]; then echo "fake 38G 12G 26G 30% /"; exit 0; fi
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "fake 1 1 ${FAKE_VM_FREE_KB:-31457280} 1% /"
"""


def run_vm(tmp_path, *args, status=0, prepare=None, env=None):
    """Run the VM half against the stand-ins; return (docker calls, output, root)."""
    if shutil.which("jq") is None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("the CI runner lacks jq; VM-half tests cannot run")
        pytest.skip("the VM half needs jq")
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "root"
    etc = tmp_path / "etc"
    etc.mkdir(exist_ok=True)
    build = tmp_path / "build"
    (build / "deploy" / "stewardship").mkdir(parents=True, exist_ok=True)
    (build / "deploy" / "stewardship" / "Dockerfile").write_text("FROM x\n")
    snapshots = tmp_path / "snapshots"
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    log.touch()
    bin_dir = tmp_path / "bin"
    executable_stub(bin_dir, "docker", FAKE_DOCKER)
    executable_stub(bin_dir, "install", FAKE_INSTALL)
    executable_stub(bin_dir, "date", FAKE_DATE)
    executable_stub(bin_dir, "chown", "exit 0")
    executable_stub(bin_dir, "sleep", "exit 0")
    # The VM's free space (FAKE_VM_FREE_KB, default 30 GiB) and its trim.
    executable_stub(bin_dir, "df", FAKE_VM_DF)
    executable_stub(bin_dir, "fstrim", 'echo "fstrim $*" >>"$FAKE_LOG"')
    # coreutils timeout is Linux-only here; the stand-in just runs the command.
    executable_stub(bin_dir, "timeout", 'shift; exec "$@"')
    # The application's pre-wizard answer through Caddy: a 503 from gunicorn.
    executable_stub(
        bin_dir,
        "curl",
        'echo "curl $*" >>"$FAKE_LOG"; printf "HTTP/2 503\\r\\nserver: gunicorn\\r\\n"',
    )
    executable_stub(
        bin_dir,
        "rsync",
        'echo "rsync $*" >>"$FAKE_LOG"; [ -z "$FAKE_RSYNC_FAIL" ] || exit 23; '
        'mkdir -p "${@: -1}"; cp -R "${@: -2:1}". "${@: -1}"',
    )
    if prepare is not None:
        prepare(root, etc, snapshots)
    result = subprocess.run(
        [
            "bash",
            str(VM_SCRIPT),
            str(root),
            "parishkit-local",
            str(etc / "deployment.yaml"),
            str(etc / "local.env"),
            str(build),
            str(snapshots),
            *args,
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_LOG": str(log),
            "FAKE_ROOT": str(root),
            "STEWARDSHIP_LOG_DIR": str(logs),
            "PS_ID": PS_ID,
            "GW_ID": GW_ID,
            # What the running database's django_migrations answers; a test
            # sets it empty for a database that cannot be read.
            "FAKE_MIGRATIONS": MIGRATIONS,
            **(env or {}),
        },
        text=True,
        capture_output=True,
        timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == status, output
    return log.read_text().splitlines(), output, root


def first(calls, fragment):
    """Index of the first recorded call containing the fragment."""
    return next(i for i, call in enumerate(calls) if fragment in call)


def installed(root, etc, snapshots, *, mode="normal", completed=False):
    """A root as `up` leaves it: marker, services, record and clock marker."""
    (root / "config" / "services").mkdir(parents=True)
    for f in ("compose-initial.json", "compose.json"):
        (root / "config" / "services" / f).write_text("{}")
    (root / ".parishkit-local").write_text("parishkit-local deployment x\n")
    clock = root / "run" / "local" / "clock"
    clock.mkdir(parents=True)
    if mode is not None:
        (clock / "mode").write_text(mode + "\n")
    (etc / "deployment.yaml").write_text("deployment: {}\n")
    (etc / "local.env").write_text(
        f"UUID=u\nADMIN_EMAIL=a@example.test\nIMAGE={IMAGE}\nFAMILIES=100\n"
        "SEED=7\nANCHOR_DATE=2026-09-16\nDEBUG=1\n"
    )


UP = ("up", IMAGE, "100", "a@example.test", "1")
# The frozen setup attempt's sealed credential ids the seeder stand-in reports.
PS_ID = "11111111-1111-4111-8111-111111111111"
GW_ID = "22222222-2222-4222-8222-222222222222"


def test_up_follows_the_runbook_first_installation_order(tmp_path):
    """Build, provision, marker, static, roles, prepare, migrate, grants, import."""
    calls, output, root = run_vm(tmp_path, *UP)
    order = [
        first(calls, "build --quiet --file"),
        first(calls, "provision-runtime --config /run/operator.yaml --image " + IMAGE),
        first(calls, "collect-static --destination"),
        first(calls, "database-roles --config"),
        first(calls, "--phase prepare --deployment-id"),
        first(calls, " run --rm -T migration"),
        first(calls, "database-grants --config"),
        first(calls, "--phase import --deployment-id"),
        first(calls, " up --detach web"),
    ]
    assert order == sorted(order), calls
    # A new deployment reads the fake ParishSoft, so census publication is
    # rendered on from the start (#1884), as an operational switch.
    assert "-e PARISHKIT_STEWARDSHIP_PARISHSOFT_PUBLICATION=1 " in calls[order[1]]
    # The persistent dependencies start before the offline steps.
    assert first(calls, "postgres valkey") < order[3]
    # Every Compose call names the fixed project and the initial file.
    for call in calls:
        if call.startswith("compose -f"):
            assert "-p parishkit-local" in call
            assert "compose-initial.json" in call
    # The runbook's inputs: one UUID throughout, the admin email in both phases.
    env = dict(
        line.split("=", 1)
        for line in (tmp_path / "etc" / "local.env").read_text().splitlines()
        if "=" in line
    )
    assert re.fullmatch(r"[0-9a-f-]{36}", env["UUID"])
    assert env["ADMIN_EMAIL"] == "a@example.test" and env["IMAGE"] == IMAGE
    assert env["FAMILIES"] == "100" and env["DEBUG"] == "1"
    assert sum(f"--confirm-deployment {env['UUID']}" in c for c in calls) == 2
    assert (
        sum(
            f"--deployment-id {env['UUID']} --admin-email a@example.test" in c
            for c in calls
        )
        == 2
    )
    # The deployment YAML is the LOCAL profile at the fixed origin.
    yaml = (tmp_path / "etc" / "deployment.yaml").read_text()
    for line in (
        "profile: local",
        "public_origin: https://localhost:8443",
        "trusted_proxy_hops: 1",
    ):
        assert line in yaml, line
    assert f"root: {root}" in yaml
    # The marker, written only after provisioning (which needs an empty root);
    # the in-progress marker is gone once it exists.
    assert (
        (root / ".parishkit-local")
        .read_text()
        .startswith("parishkit-local deployment ")
    )
    assert not (tmp_path / "etc" / "local.env.installing").exists()
    # The sentinel OAuth client and the fake configuration.
    assert (
        "constant-LOCAL_OAUTH_DOCUMENT"
        in (root / "credentials/google_oauth/credential").read_text()
    )
    fake = json.loads((root / "run/local/fake-parishsoft.json").read_text())
    assert fake["families"] == 100 and fake["release_at"] is None
    # The backup recipient key, as the runbook's first installation installs
    # it: the bare public key line as the backup_data credential, generated
    # by the image into run/local (never archived by a backup).
    keygen = first(calls, " backup-keygen --destination /keys/backup-key")
    assert f"source={root}/run/local,target=/keys" in calls[keygen]
    assert keygen < order[3]
    assert (
        root / "credentials/backup_data/credential"
    ).read_text() == "age1fakepublickey\n"
    assert "recipient fingerprint SHA256:fake" in output
    assert (
        fake["seed"] == int(env["SEED"]) and fake["anchor_date"] == env["ANCHOR_DATE"]
    )
    # No faketime override in the image yet: normal mode, offset zero.
    assert (root / "run/local/clock/mode").read_text() == "normal\n"
    assert (root / "run/local/clock/offset").read_text() == "0\n"
    # The summary names the site, the sign-in command and the wizard values.
    for text in (
        "https://localhost:8443",
        "sign-in --email a@example.test",
        "constant-LOCAL_PARISHSOFT_KEY",
        "Clock mode: normal",
    ):
        assert text in output, text
    assert any("https://localhost:8443/" in c for c in calls if c.startswith("curl"))


def test_up_reuses_an_image_the_reinstall_built(tmp_path):
    """When the tag already exists (built before the wipe) nothing is rebuilt."""
    calls, output, _ = run_vm(tmp_path, *UP, env={"FAKE_IMAGE_EXISTS": "1"})
    assert not any("build --quiet" in c for c in calls)
    assert "built a moment ago" in output


def test_up_refuses_what_it_did_not_create_before_building(tmp_path):
    """Unmarked root, existing deployment, stray YAML, failed install: no build."""

    def populated(root, etc, snapshots):
        root.mkdir()
        (root / "data").write_text("x")

    def stray_yaml(root, etc, snapshots):
        (etc / "deployment.yaml").write_text("deployment: {}\n")

    def half_installed(root, etc, snapshots):
        (etc / "local.env.installing").write_text(f"installing {root}\n")

    for n, (prepare, message) in enumerate(
        (
            (populated, "carries no marker"),
            (installed, "already a local deployment"),
            (stray_yaml, "carries no marker; another deployment"),
            (half_installed, "did not finish"),
        )
    ):
        calls, output, _ = run_vm(tmp_path / str(n), *UP, status=1, prepare=prepare)
        assert message in output, message
        assert calls == [], message
    calls, output, _ = run_vm(
        tmp_path / "f", "up", IMAGE, "x", "a@example.test", "1", status=1
    )
    assert "positive integer" in output and calls == []
    # The existing-deployment refusal points at the commands that apply.
    _, output, _ = run_vm(tmp_path / "g", *UP, status=1, prepare=installed)
    for hint in ("'start'", "'reset'", "'reset --reinstall'"):
        assert hint in output, hint


def test_a_failed_dependency_start_is_reported_with_its_limit(tmp_path):
    """The postgres/valkey wait is a timed step: what, exit, elapsed and limit."""
    _, output, _ = run_vm(
        tmp_path, "start", status=1, prepare=installed, env={"FAKE_DEPS_FAIL": "1"}
    )
    assert "FAILED: starting postgres and valkey (exit 1 after" in output
    assert "limit 210s" in output
    assert "Starting web" not in output


@pytest.mark.parametrize(
    "args",
    [
        ("down",),
        ("snapshot", "post-setup"),
        ("reset", "post-setup"),
        ("wipe",),
        ("seed", "1"),
        ("sign-in", "a@example.test"),
        ("ca",),
        ("start",),
        ("deploy", IMAGE, "0", "/x/host.sh"),
        ("rollback", "/x/host.sh"),
        ("wizard",),
        ("finish-setup",),
        ("snapshot", "testing", "0", "1", "8"),
        ("snapshot-restore", "seeded", "/x/host.sh"),
        ("seed-testing", "/x/host.sh"),
        ("fake-parishsoft", "member", "500010", "email=x@example.test"),
        ("fake-parishsoft", "refresh"),
    ],
)
def test_every_destructive_or_deployment_command_needs_the_marker(tmp_path, args):
    """Without ROOT/.parishkit-local nothing is run, not even a Compose call."""

    def unmarked(root, etc, snapshots):
        root.mkdir()
        (root / "data").write_text("x")

    calls, output, _ = run_vm(tmp_path, *args, status=1, prepare=unmarked)
    assert "does not carry the marker" in output
    assert calls == []


def test_down_stops_with_a_timeout_and_never_removes_volumes(tmp_path):
    """`down` is a timed Compose stop with an explicit grace period; no removal."""
    calls, output, _ = run_vm(tmp_path, "down", prepare=installed)
    stops = [c for c in calls if " stop" in c]
    assert stops == ["compose -p parishkit-local stop --timeout 90"]
    assert not any(
        "--volumes" in c or " down" in c or c.startswith("rm") for c in calls
    )
    assert "exit 137" not in output
    # A container Docker had to kill is named.
    _, output, _ = run_vm(
        tmp_path / "killed", "down", prepare=installed, env={"FAKE_KILLED": "1"}
    )
    assert "killed after the 90s stop timeout (exit 137): worker" in output


def test_start_applies_the_recorded_clock_mode(tmp_path):
    """normal: one file; fake: the faketime override too; missing marker: error."""
    calls, _, _ = run_vm(tmp_path / "normal", "start", prepare=installed)
    ups = [
        c for c in calls if c.endswith(" up --detach web") or c.endswith(" up --detach")
    ]
    assert ups and all("compose.faketime.json" not in c for c in ups)
    assert all("compose-initial.json" in c for c in ups)

    def fake_with_override(root, etc, snapshots):
        installed(root, etc, snapshots, mode="fake", completed=True)
        (root / "config/services/compose.faketime.json").write_text("{}")

    calls, _, _ = run_vm(
        tmp_path / "fake",
        "start",
        prepare=fake_with_override,
        env={"FAKE_COMPLETED": "t"},
    )
    ups = [
        c for c in calls if c.endswith(" up --detach web") or c.endswith(" up --detach")
    ]
    assert ups
    for c in ups:
        # Setup is complete, so compose.json, plus the one override.
        assert "-f " in c and "compose.json" in c and "compose-initial" not in c
        assert (
            "-f " + str(tmp_path / "fake/root/config/services/compose.faketime.json")
            in c
        )

    def fake_without_override(root, etc, snapshots):
        installed(root, etc, snapshots, mode="fake")

    calls, output, _ = run_vm(
        tmp_path / "nofile", "start", status=1, prepare=fake_without_override
    )
    assert "compose.faketime.json is missing" in output
    assert not any(c.endswith(" up --detach web") for c in calls)
    calls, output, _ = run_vm(
        tmp_path / "nomarker",
        "start",
        status=1,
        prepare=lambda r, e, s: installed(r, e, s, mode=None),
    )
    assert "No clock-mode marker" in output
    assert not any(c.endswith(" up --detach web") for c in calls)
    _, output, _ = run_vm(
        tmp_path / "bad",
        "start",
        status=1,
        prepare=lambda r, e, s: installed(r, e, s, mode="sometimes"),
    )
    assert "expected fake or normal" in output


@pytest.mark.parametrize("mode", ["fake", "normal"])
def test_the_fake_clock_refuses_a_real_oauth_client_before_starting(tmp_path, mode):
    """A real Google client cannot run under the fake clock (#1399).

    Every fake-clock Compose selection refuses it before anything starts, so
    the developer reads why instead of meeting a web that will not start.
    The sentinel passes, and the normal clock admits a real client.
    """
    from parishkit.stewardship.runtime_web import LOCAL_OAUTH_DOCUMENT

    def prepare(client):
        """An installed deployment holding ``client`` as its OAuth client."""

        def setup(root, etc, snapshots):
            installed(root, etc, snapshots, mode=mode, completed=True)
            (root / "config/services/compose.faketime.json").write_text("{}")
            oauth = root / "credentials" / "google_oauth"
            oauth.mkdir(parents=True)
            (oauth / "credential").write_bytes(client)

        return setup

    real = b'{"client_id": "id.apps.googleusercontent.com", "client_secret": "s"}'
    calls, output, _ = run_vm(
        tmp_path / "real",
        "start",
        status=1 if mode == "fake" else 0,
        prepare=prepare(real),
        env={"FAKE_COMPLETED": "t"},
    )
    started = any(c.endswith(" up --detach web") for c in calls)
    if mode == "fake":
        assert "A real Google OAuth client is installed" in output
        assert "reinstall the sentinel OAuth client first" in output
        assert not started
    else:
        assert started
    calls, _, _ = run_vm(
        tmp_path / "sentinel",
        "start",
        prepare=prepare(LOCAL_OAUTH_DOCUMENT),
        env={"FAKE_COMPLETED": "t"},
    )
    assert any(c.endswith(" up --detach web") for c in calls)


def test_the_vm_half_names_the_sentinel_oauth_client():
    """The VM half's copy of the sentinel's client ID is the package's."""
    from parishkit.stewardship.runtime_web import LOCAL_OAUTH_SENTINEL

    text = (ROOT / "tools" / "stewardship-local-vm.sh").read_text()
    assert f"OAUTH_SENTINEL_ID={LOCAL_OAUTH_SENTINEL['client_id']}\n" in text


def test_sign_in_needs_the_clock_mode_marker_too(tmp_path):
    """A refusal inside the Compose-file selection ends the command; no exec runs."""
    calls, output, _ = run_vm(
        tmp_path,
        "sign-in",
        "a@example.test",
        status=1,
        prepare=lambda r, e, s: installed(r, e, s, mode=None),
    )
    assert "No clock-mode marker" in output
    assert not any("local-sign-in" in c for c in calls)


def test_snapshot_stops_copies_with_numeric_ids_and_restarts(tmp_path):
    """Stop first, rsync --delete --numeric-ids the root with its YAML, restart.

    The copy is made beside the snapshot and renamed into place once whole.
    """
    calls, output, root = run_vm(
        tmp_path, "snapshot", "post-setup", prepare=installed,
        env={"FAKE_MIGRATIONS": MIGRATIONS},
    )  # fmt: skip
    stop = first(calls, "compose -p parishkit-local stop")
    copy = first(calls, "rsync ")
    start = first(calls, " up --detach web")
    assert stop < copy < start
    snapshot = tmp_path / "snapshots" / "post-setup"
    partial = tmp_path / "snapshots" / ".post-setup.partial"
    assert calls[copy] == f"rsync -aHAX --delete --numeric-ids {root}/ {partial}/root/"
    assert not partial.exists()
    assert (snapshot / "deployment.yaml").exists() and (
        snapshot / "deployment.env"
    ).exists()
    assert (snapshot / "taken").exists()
    assert "pre-wizard snapshot" in output  # setup not complete in the stand-in


def test_reset_restores_only_an_existing_snapshot_after_stopping(tmp_path):
    """No snapshot: refuse. With one: stop, rsync it over the root, recreate."""
    _, output, _ = run_vm(
        tmp_path / "none", "reset", "post-setup", status=1, prepare=installed
    )
    assert "No post-setup snapshot" in output

    def with_snapshot(root, etc, snapshots):
        installed(root, etc, snapshots)
        (snapshots / "seeded" / "root").mkdir(parents=True)
        (snapshots / "seeded" / "taken").write_text("2026-10-04T07:49:38Z\n")
        shutil.copy(etc / "deployment.yaml", snapshots / "seeded" / "deployment.yaml")
        shutil.copy(etc / "local.env", snapshots / "seeded" / "deployment.env")

    calls, _, root = run_vm(tmp_path / "some", "reset", "seeded", prepare=with_snapshot)
    stop = first(calls, "compose -p parishkit-local stop")
    copy = first(calls, "rsync ")
    assert stop < copy < first(calls, " up --detach --force-recreate web")
    snapshot = tmp_path / "some" / "snapshots" / "seeded"
    assert calls[copy] == (
        "rsync -aHAX --checksum --inplace --delete --numeric-ids "
        f"{snapshot}/root/ {root}/"
    )
    assert "has-snapshot" not in "".join(calls)
    # has-snapshot answers by directory.
    run_vm(tmp_path / "probe", "has-snapshot", "seeded", prepare=with_snapshot)
    run_vm(
        tmp_path / "probe2",
        "has-snapshot",
        "post-setup",
        status=1,
        prepare=with_snapshot,
    )


def test_wipe_removes_containers_then_the_root_without_volumes(tmp_path):
    """Compose down without --volumes; the root goes only once no container is left."""
    calls, _, root = run_vm(tmp_path, "wipe", prepare=installed)
    assert calls == [
        "compose -p parishkit-local down --remove-orphans",
        "compose -p parishkit-local ps --all --quiet",
    ]
    assert root.exists() and not any(root.iterdir())
    for name in ("local.env", "deployment.yaml"):
        assert not (tmp_path / "etc" / name).exists(), name
    # A surviving container stops the removal.
    _, output, root = run_vm(
        tmp_path / "left",
        "wipe",
        status=1,
        prepare=installed,
        env={"FAKE_LEFTOVER": "1"},
    )
    assert "still exist; not removing" in output
    assert (root / ".parishkit-local").exists()

    # The in-progress marker of a failed `up` is enough for wipe, and only wipe.
    def half_installed(root, etc, snapshots):
        root.mkdir()
        (root / ".stewardship-provisioning.json").write_text("{}")
        (etc / "local.env.installing").write_text(f"installing {root}\n")

    _, _, root = run_vm(tmp_path / "half", "wipe", prepare=half_installed)
    assert not any(root.iterdir())
    assert not (tmp_path / "half" / "etc" / "local.env.installing").exists()
    _, output, _ = run_vm(
        tmp_path / "halfdown", "down", status=1, prepare=half_installed
    )
    assert "does not carry the marker" in output


def test_seed_refuses_a_deployment_in_normal_clock_mode(tmp_path):
    """A normal-clock deployment is seeded already: seed refuses, runs no step."""
    if shutil.which("flock") is None:
        pytest.skip("the seed lock needs flock (Linux)")
    calls, output, _ = run_vm(tmp_path, "seed", "2", status=1, prepare=installed)
    assert "normal clock mode" in output
    assert not any("local-seed" in call for call in calls)


def test_sign_in_and_ca_read_the_deployment(tmp_path):
    """sign-in runs the command in web; ca prints Caddy's root certificate."""
    calls, _, _ = run_vm(
        tmp_path / "signin", "sign-in", "a@example.test", prepare=installed
    )
    assert calls[-1].endswith(
        "exec -T web pk-stewardship local-sign-in --config "
        + str(tmp_path / "signin" / "root" / "config" / "services" / "web.yaml")
        + " --email a@example.test"
    )

    def with_cert(root, etc, snapshots):
        installed(root, etc, snapshots)
        crt = root / "run/persistent/caddy/data/caddy/pki/authorities/local/root.crt"
        crt.parent.mkdir(parents=True)
        crt.write_text("CERT\n")

    _, output, _ = run_vm(tmp_path / "ca", "ca", prepare=with_cert)
    assert output == "CERT\n"
    _, output, _ = run_vm(tmp_path / "noca", "ca", status=1, prepare=installed)
    assert "not written its local root certificate" in output


# The stand-in for the scripted upgrade's host half: it records its arguments
# and the debug-logging setting it inherits, and retargets the Compose file
# to the image it was given as the real one does; FAKE_HOST_FAIL makes it
# fail as an abandoned upgrade would (before the retarget, unless
# FAKE_HOST_RETARGETED says it got that far first).
FAKE_HOST = r"""
echo "host $* debug=$PARISHKIT_DEBUG_LOGGING" >>"$FAKE_LOG"
if [ -n "$FAKE_HOST_ENV" ]; then
    bulk=$PARISHKIT_LOCAL_BULK_FAMILY_SEND latency=$PARISHKIT_LOCAL_SMTP_LATENCY_MS
    parishsoft=$PARISHKIT_LOCAL_PARISHSOFT redirect=$PARISHKIT_LOCAL_MAIL_REDIRECT
    echo "host bulk=$bulk latency=$latency parishsoft=$parishsoft" \
        "redirect=$redirect" >>"$FAKE_LOG"
fi
retarget() {
    echo '{"services": {"web": {"image": "'"$2"'"}}}' >"$3/config/services/compose.json"
}
if [ -n "$FAKE_HOST_FAIL" ]; then
    [ -z "$FAKE_HOST_RETARGETED" ] || retarget "$@"
    echo "the host half refused" >&2
    exit 1
fi
retarget "$@"
"""
NEW_IMAGE = f"parishkit-stewardship-local:{'e' * 40}-1700000600"


def with_host_script(tmp_path, *, snapshot=True, previous=None):
    """A prepare function: an installed root, the seeded snapshot, the stand-in."""

    def prepare(root, etc, snapshots):
        installed(root, etc, snapshots)
        # The Compose file names the recorded image, as after a real install.
        (root / "config" / "services" / "compose.json").write_text(
            json.dumps({"services": {"web": {"image": IMAGE}}})
        )
        if previous is not None:
            with (etc / "local.env").open("a") as record:
                record.write(f"PREVIOUS_IMAGE={previous}\n")
        if snapshot:
            (snapshots / "seeded" / "root").mkdir(parents=True)
            (snapshots / "seeded" / "taken").write_text("2026-10-04T07:49:38Z\n")
        host = tmp_path / "host.sh"
        host.write_text(f"#!/usr/bin/env bash\n{FAKE_HOST}\n")
        host.chmod(0o755)

    return prepare


def record(tmp_path):
    """The deployment record as a dict."""
    return dict(
        line.split("=", 1)
        for line in (tmp_path / "etc" / "local.env").read_text().splitlines()
        if "=" in line
    )


def test_deploy_runs_the_host_half_in_local_mode_and_records_the_images(tmp_path):
    """The host half gets the Production argument order plus `local BUILD`."""
    host = tmp_path / "host.sh"
    calls, output, root = run_vm(
        tmp_path,
        "deploy",
        NEW_IMAGE,
        "0",
        str(host),
        prepare=with_host_script(tmp_path),
        env={"FAKE_IMAGE_EXISTS": "1"},
    )
    etc = tmp_path / "etc"
    # A deployment installed before the backup key step gets its key here,
    # before the host half's required backup; one with a key is left alone.
    assert calls[0] == f"image inspect {NEW_IMAGE}"
    assert " backup-keygen --destination /keys/backup-key" in calls[1]
    assert calls[2] == (
        f"host - {NEW_IMAGE} {root} parishkit-local {etc / 'deployment.yaml'} u 0 "
        f"upgrade local {tmp_path / 'build'} debug=1"
    )
    assert len(calls) == 3
    assert (
        root / "credentials/backup_data/credential"
    ).read_text() == "age1fakepublickey\n"
    # The return point is named before the upgrade starts; the record follows.
    assert "return point: 'reset --seeded' restores the seeded snapshot" in output
    assert output.index("return point") < output.index("Deploying")
    assert record(tmp_path)["IMAGE"] == NEW_IMAGE
    assert record(tmp_path)["PREVIOUS_IMAGE"] == IMAGE
    assert "deploy --rollback" in output and "reset --seeded" in output

    # --schema-change travels as the seventh argument; an installed key is kept.
    def keyed(root, etc, snapshots):
        with_host_script(tmp_path / "schema")(root, etc, snapshots)
        (root / "credentials" / "backup_data").mkdir(parents=True)
        (root / "credentials" / "backup_data" / "credential").write_text("age1kept\n")

    calls, _, root = run_vm(
        tmp_path / "schema",
        "deploy",
        NEW_IMAGE,
        "1",
        str(tmp_path / "schema" / "host.sh"),
        prepare=keyed,
        env={"FAKE_IMAGE_EXISTS": "1"},
    )
    assert " u 1 upgrade local " in calls[-1]
    assert not any("backup-keygen" in c for c in calls)
    assert (root / "credentials/backup_data/credential").read_text() == "age1kept\n"


def test_deploy_refuses_without_a_return_point_an_image_or_a_host_half(tmp_path):
    """No snapshot, an unbuilt image or a missing upload: the host half never runs."""
    calls, output, _ = run_vm(
        tmp_path / "nosnap",
        "deploy",
        NEW_IMAGE,
        "0",
        str(tmp_path / "nosnap" / "host.sh"),
        status=1,
        prepare=with_host_script(tmp_path / "nosnap", snapshot=False),
        env={"FAKE_IMAGE_EXISTS": "1"},
    )
    assert "No snapshot to return to" in output and "'snapshot'" in output
    assert not any(c.startswith("host ") for c in calls)
    calls, output, _ = run_vm(
        tmp_path / "noimage",
        "deploy",
        NEW_IMAGE,
        "0",
        str(tmp_path / "noimage" / "host.sh"),
        status=1,
        prepare=with_host_script(tmp_path / "noimage"),
    )
    assert "is not built" in output and not any(c.startswith("host ") for c in calls)
    calls, output, _ = run_vm(
        tmp_path / "nohost",
        "deploy",
        NEW_IMAGE,
        "0",
        str(tmp_path / "nohost" / "elsewhere.sh"),
        status=1,
        prepare=with_host_script(tmp_path / "nohost"),
        env={"FAKE_IMAGE_EXISTS": "1"},
    )
    assert "No uploaded upgrade host script" in output
    # A host half that abandons leaves the record as it was.
    _, output, _ = run_vm(
        tmp_path / "fail",
        "deploy",
        NEW_IMAGE,
        "0",
        str(tmp_path / "fail" / "host.sh"),
        status=1,
        prepare=with_host_script(tmp_path / "fail"),
        env={"FAKE_IMAGE_EXISTS": "1", "FAKE_HOST_FAIL": "1"},
    )
    assert "the host half refused" in output
    assert record(tmp_path / "fail")["IMAGE"] == IMAGE
    assert "PREVIOUS_IMAGE" not in record(tmp_path / "fail")
    # One that failed after the retarget leaves the record truthful: the
    # Compose file's image is recorded, and the failure is still reported.
    _, output, _ = run_vm(
        tmp_path / "late",
        "deploy",
        NEW_IMAGE,
        "0",
        str(tmp_path / "late" / "host.sh"),
        status=1,
        prepare=with_host_script(tmp_path / "late"),
        env={
            "FAKE_IMAGE_EXISTS": "1",
            "FAKE_HOST_FAIL": "1",
            "FAKE_HOST_RETARGETED": "1",
        },
    )
    assert "the host half refused" in output
    assert f"The Compose file now names {NEW_IMAGE}; recorded it" in output
    assert record(tmp_path / "late")["IMAGE"] == NEW_IMAGE
    assert record(tmp_path / "late")["PREVIOUS_IMAGE"] == IMAGE


def test_rollback_needs_a_recorded_previous_image_and_swaps_the_record(tmp_path):
    """Without a deploy there is nothing to roll back to; with one, the images swap."""
    calls, output, _ = run_vm(
        tmp_path / "none",
        "rollback",
        str(tmp_path / "none" / "host.sh"),
        status=1,
        prepare=with_host_script(tmp_path / "none"),
    )
    assert "No previous image is recorded" in output and "'reset --seeded'" in output
    assert calls == []

    # The Compose file and the record disagreeing means the target cannot be
    # trusted: refused before the host half runs.
    def mismatched(root, etc, snapshots):
        with_host_script(tmp_path / "mismatch", previous=NEW_IMAGE)(
            root, etc, snapshots
        )
        (root / "config" / "services" / "compose.json").write_text(
            json.dumps(
                {"services": {"web": {"image": "parishkit-stewardship-local:x"}}}
            )
        )

    calls, output, _ = run_vm(
        tmp_path / "mismatch",
        "rollback",
        str(tmp_path / "mismatch" / "host.sh"),
        status=1,
        prepare=mismatched,
    )
    assert "but the deployment record says" in output and calls == []
    calls, output, root = run_vm(
        tmp_path / "some",
        "rollback",
        str(tmp_path / "some" / "host.sh"),
        prepare=with_host_script(tmp_path / "some", previous=NEW_IMAGE),
    )
    etc = tmp_path / "some" / "etc"
    assert calls == [
        f"host - {NEW_IMAGE} {root} parishkit-local {etc / 'deployment.yaml'} u 0 "
        f"rollback local {tmp_path / 'some' / 'build'} debug=1",
    ]
    assert record(tmp_path / "some")["IMAGE"] == NEW_IMAGE
    assert record(tmp_path / "some")["PREVIOUS_IMAGE"] == IMAGE
    assert f"Rolled back to {NEW_IMAGE}" in output
    # status names the image a rollback would return to.
    _, output, _ = run_vm(
        tmp_path / "status",
        "status",
        prepare=with_host_script(tmp_path / "status", previous=NEW_IMAGE),
    )
    assert f"Previous image ('deploy --rollback' returns to it): {NEW_IMAGE}" in output


def test_deploy_hands_the_rehearsal_choices_to_the_host_half(tmp_path):
    """BULK and LATENCY become the host half's two local-mode variables."""
    host = tmp_path / "host.sh"
    calls, _, _ = run_vm(
        tmp_path,
        "deploy",
        NEW_IMAGE,
        "0",
        str(host),
        "on",
        "600",
        prepare=with_host_script(tmp_path),
        env={"FAKE_IMAGE_EXISTS": "1", "FAKE_HOST_ENV": "1"},
    )
    assert "bulk=on latency=600 parishsoft=keep redirect=keep" in calls[-1]
    calls, _, _ = run_vm(
        tmp_path / "real",
        "deploy",
        NEW_IMAGE,
        "0",
        str(tmp_path / "real" / "host.sh"),
        "keep",
        "keep",
        "real",
        prepare=with_host_script(tmp_path / "real"),
        env={"FAKE_IMAGE_EXISTS": "1", "FAKE_HOST_ENV": "1"},
    )
    assert "bulk=keep latency=keep parishsoft=real redirect=keep" in calls[-1]
    calls, _, _ = run_vm(
        tmp_path / "redirect",
        "deploy",
        NEW_IMAGE,
        "0",
        str(tmp_path / "redirect" / "host.sh"),
        "keep",
        "keep",
        "keep",
        "jeff@example.org",
        prepare=with_host_script(tmp_path / "redirect"),
        env={"FAKE_IMAGE_EXISTS": "1", "FAKE_HOST_ENV": "1"},
    )
    assert "parishsoft=keep redirect=jeff@example.org" in calls[-1]
    # Without them, all carry over.
    calls, _, _ = run_vm(
        tmp_path / "keep",
        "deploy",
        NEW_IMAGE,
        "0",
        str(tmp_path / "keep" / "host.sh"),
        prepare=with_host_script(tmp_path / "keep"),
        env={"FAKE_IMAGE_EXISTS": "1", "FAKE_HOST_ENV": "1"},
    )
    assert "bulk=keep latency=keep parishsoft=keep redirect=keep" in calls[-1]


def test_rehearse_needs_a_seeded_set_up_deployment(tmp_path):
    """Fake clock (unseeded) or an unfinished wizard: no Reminder is added."""
    calls, output, _ = run_vm(
        tmp_path / "fake",
        "rehearse",
        "5",
        "0",
        "60",
        "run",
        status=1,
        prepare=lambda root, etc, snapshots: installed(
            root, etc, snapshots, mode="fake"
        ),
    )
    assert "seeded deployment (normal clock mode)" in output
    assert not any("local-seed" in call for call in calls)
    calls, output, _ = run_vm(
        tmp_path / "wizard", "rehearse", "5", "0", "60", "run", status=1,
        prepare=installed,
    )  # fmt: skip
    assert "setup wizard has not completed" in output
    assert not any("local-seed" in call for call in calls)
    _, output, _ = run_vm(tmp_path / "args", "rehearse", "5", status=1)
    assert "rehearse needs DUE_IN SEND_ONLY TIMEOUT LABEL" in output
    # The VM half checks what it puts in a path and in SQL itself.
    for n, args in enumerate((("5", "0", "60", "../x"), ("5", "2", "60", "run"))):
        calls, output, _ = run_vm(
            tmp_path / f"bad{n}", "rehearse", *args, status=1, prepare=installed
        )
        assert "rehearse" in output and calls == [], args


def test_spike_needs_a_seeded_set_up_deployment(tmp_path):
    """Fake clock (unseeded) or an unfinished wizard: no shard is started."""
    calls, output, _ = run_vm(
        tmp_path / "fake",
        "spike",
        "200",
        "8",
        "0",
        "20",
        "spike",
        status=1,
        prepare=lambda root, etc, snapshots: installed(
            root, etc, snapshots, mode="fake"
        ),
    )
    assert "seeded deployment (normal clock mode)" in output
    assert not any("local-spike" in call for call in calls)
    calls, output, _ = run_vm(
        tmp_path / "wizard", "spike", "200", "8", "0", "20", "spike", status=1,
        prepare=installed,
    )  # fmt: skip
    assert "setup wizard has not completed" in output
    assert not any("local-spike" in call for call in calls)
    _, output, _ = run_vm(tmp_path / "args", "spike", "200", status=1)
    assert "spike needs FAMILIES SOURCES DURING_SEND WAIT LABEL" in output
    for n, args in enumerate(
        (("200", "8", "0", "20", "../x"), ("200", "8", "2", "20", "spike"))
    ):
        calls, output, _ = run_vm(
            tmp_path / f"bad{n}", "spike", *args, status=1, prepare=installed
        )
        assert "spike" in output and calls == [], args


def spike_ready(root, etc, snapshots):
    """A seeded, set-up deployment whose Caddy has written its root certificate."""
    installed(root, etc, snapshots)
    crt = root / "run/persistent/caddy/data/caddy/pki/authorities/local/root.crt"
    crt.parent.mkdir(parents=True)
    crt.write_text("-----BEGIN CERTIFICATE-----\n")


def test_spike_runs_every_shard_then_the_offline_report(tmp_path):
    """The success path: postgres found, S labeled shards, run record, report."""
    calls, output, _ = run_vm(
        tmp_path,
        "spike",
        "4",
        "2",
        "0",
        "1",
        "t1",
        prepare=spike_ready,
        env={"FAKE_COMPLETED": "t"},
    )
    shards = [call for call in calls if " local-spike --profile local " in call]
    assert len(shards) == 2
    # The shards run in parallel, so their calls are logged in either order.
    assert sorted(int(c.split("--shard ")[1].split()[0]) for c in shards) == [0, 1]
    for call in shards:
        assert "--network parishkit-local_ingress" in call
        assert "--label parishkit.local.spike=t1-" in call
        assert "--shards 2 --families 4" in call
        assert "--since 1970-01-01T00:00:00Z --wait-seconds 60" in call
        assert "--ca-file /spike/root.crt" in call
    # The deadlock counter is read in the postgres container, before and after.
    assert sum("exec -i -e PGOPTIONS" in call and "pgcontainer" in call
               for call in calls) == 2  # fmt: skip
    report = first(calls, "local-spike-report --input /spike")
    assert report > max(calls.index(call) for call in shards)
    # Leftover shards of this run are looked for by its label.
    assert any("ps -aq --filter label=parishkit.local.spike=t1-" in c for c in calls)
    (run,) = (tmp_path / "logs").glob("stewardship-spike-t1-*")
    meta = json.loads((run / "meta.json").read_text())
    assert meta["families"] == 4 and meta["failed_shards"] == 0
    assert (run / "shard-0.json").read_text().startswith('{"check": "spike"')
    assert "Spike t1 passed" in output


def test_spike_counts_a_failed_shard_and_still_reports(tmp_path):
    """One shard failing is recorded for the report, not fatal to the others."""
    calls, output, _ = run_vm(
        tmp_path,
        "spike",
        "4",
        "2",
        "0",
        "1",
        "t2",
        prepare=spike_ready,
        env={"FAKE_COMPLETED": "t", "FAKE_SHARD_FAIL": "1"},
    )
    (run,) = (tmp_path / "logs").glob("stewardship-spike-t2-*")
    assert json.loads((run / "meta.json").read_text())["failed_shards"] == 1
    assert "1 of 2 shards failed" in output
    assert any("local-spike-report" in call for call in calls)


def test_spike_refuses_without_postgres_or_the_certificate(tmp_path):
    """Nothing starts when postgres is down or Caddy has no root certificate."""
    calls, output, _ = run_vm(
        tmp_path / "pg", "spike", "4", "2", "0", "1", "t3", status=1,
        prepare=spike_ready, env={"FAKE_COMPLETED": "t", "FAKE_NO_POSTGRES": "1"},
    )  # fmt: skip
    assert "postgres is not running" in output
    assert not any("local-spike" in call for call in calls)
    calls, output, _ = run_vm(
        tmp_path / "crt", "spike", "4", "2", "0", "1", "t4", status=1,
        prepare=installed, env={"FAKE_COMPLETED": "t"},
    )  # fmt: skip
    assert "local root certificate" in output
    assert not any("local-spike" in call for call in calls)


# ---------------------------------------------------------------------------
# Finishing setup (runbook first installation, step 5; #1355), now a wait
# with the runbook's fallback for an overdue credential (#2228).

# The LOCAL seeder's fixed backend address, which the rendered compose.json
# carries for the one-off web container's `--ip` (#2111).
SEEDER_ADDRESS = {"x-local-seeder": {"ipv4_address": "172.29.240.30"}}


def frozen_wizard(root, etc, snapshots):
    """An installed fake-clock root whose wizard froze its attempt.

    compose.json carries web's rendered environment, mounts and tmpfs, which
    the seeder's one-off web container copies.
    """
    installed(root, etc, snapshots, mode="fake")
    web = {
        "image": IMAGE,
        "environment": {"PARISHKIT_STEWARDSHIP_PROFILE": "local"},
        "volumes": [{"source": "/x", "target": "/y", "read_only": True}],
        "tmpfs": ["/tmp"],
    }
    services = root / "config" / "services"
    (services / "compose.json").write_text(
        json.dumps({"services": {"web": web}} | SEEDER_ADDRESS)
    )
    (services / "compose.faketime.json").write_text(
        json.dumps({"services": {"web": {**web, "volumes": []}}})
    )


def test_finish_setup_waits_while_the_consumers_pick_up_the_credentials(tmp_path):
    """#2228: look up, wait for the installers, then for setup to complete and
    for worker and mail-dispatch to restart with every credential; nothing
    is recreated or acknowledged by hand."""
    calls, output, _ = run_vm(
        tmp_path,
        "finish-setup",
        prepare=frozen_wizard,
        env={"FAKE_COMPLETES_AFTER": "2"},
    )
    order = [
        first(calls, "local-seed --step credentials "),
        first(calls, "count(*) FROM stewardship_secret_request"),
        first(calls, "exec -T worker cat /tmp/stewardship-consumer/background.json"),
        first(
            calls, "exec -T mail-dispatch cat /tmp/stewardship-consumer/background.json"
        ),
    ]
    assert order == sorted(order), calls
    assert not any("acknowledge-credential" in call for call in calls)
    assert not any("--force-recreate" in call for call in calls)
    assert "fallback" not in output
    assert "Setup complete in" in output


def test_finish_setup_runs_the_fallback_only_for_an_overdue_credential(tmp_path):
    """Overdue (10 minutes after installation): say so, recreate from
    compose.json, acknowledge inside the consumers, complete, re-admit."""
    calls, output, _ = run_vm(
        tmp_path, "finish-setup", prepare=frozen_wizard, env={"FAKE_OVERDUE": "1"}
    )
    configured = "-f {0}/compose.json -f {0}/compose.faketime.json -p parishkit-local"
    configured = configured.format(tmp_path / "root" / "config" / "services")
    order = [
        first(calls, "local-seed --step credentials "),
        first(calls, "count(*) FROM stewardship_secret_request"),
        first(calls, f"{configured} up --detach --force-recreate worker mail-dispatch"),
        first(
            calls, f"{configured} exec -T worker pk-stewardship acknowledge-credential"
        ),
        first(calls, " exec -T mail-dispatch pk-stewardship acknowledge-credential"),
        first(calls, "exec -T worker cat /tmp/stewardship-consumer/background.json"),
    ]
    assert order == sorted(order), calls
    assert first(calls, "interval '10 minutes'") < order[2]
    assert "still unacknowledged 10 minutes after its installation" in output
    assert f"--request-id {PS_ID}" in calls[order[3]]
    assert f"--request-id {GW_ID}" in calls[order[4]]
    # Read-only lookup: the one-off web container takes the clock directory.
    assert "--network parishkit-local_backend" in calls[order[0]]
    assert "Setup complete in" in output
    assert "Log: " in output


def test_finish_setup_is_idempotent_and_refuses_before_finishing_setup(tmp_path):
    """Complete setup changes nothing; an unfrozen or partial attempt is refused."""
    for name, env in (
        ("done", {"FAKE_COMPLETED": "t"}),
        ("done-late", {"FAKE_SEED_RESULT": '{"result": "setup was already complete"}'}),
    ):
        calls, output, _ = run_vm(
            tmp_path / name, "finish-setup", prepare=frozen_wizard, env=env
        )
        assert "already complete; nothing to finish" in output
        assert not any("acknowledge-credential" in call for call in calls)
        assert not any("--force-recreate" in call for call in calls)
    calls, output, _ = run_vm(
        tmp_path / "early",
        "finish-setup",
        status=1,
        prepare=frozen_wizard,
        env={"FAKE_SEED_RESULT": '{"result": "not frozen"}'},
    )
    assert "has not reached Finishing setup" in output
    assert not any("--force-recreate" in call for call in calls)
    partial = json.dumps({"result": "frozen", "requests": {"parishsoft": PS_ID}})
    calls, output, _ = run_vm(
        tmp_path / "partial",
        "finish-setup",
        status=1,
        prepare=frozen_wizard,
        env={"FAKE_SEED_RESULT": partial},
    )
    assert "lacks its ParishSoft or Google Workspace credential" in output
    assert not any("--force-recreate" in call for call in calls)


def test_wizard_finishes_setup_through_the_same_step(tmp_path):
    """`wizard` and `finish-setup` share one Finishing setup implementation."""
    calls, output, _ = run_vm(
        tmp_path, "wizard", prepare=frozen_wizard, env={"FAKE_OVERDUE": "1"}
    )
    assert any("local-seed --step wizard " in call for call in calls)
    acknowledged = [c for c in calls if "acknowledge-credential" in c]
    assert [c.split("--request-id ")[1] for c in acknowledged] == [PS_ID, GW_ID]
    # Setup creates no campaign (#142): wizard creates it afterwards.
    assert first(calls, "local-seed --step campaign ") > first(
        calls, " exec -T mail-dispatch pk-stewardship acknowledge-credential"
    )
    # Without an overdue credential nothing is acknowledged by hand (#2228).
    calls, _, _ = run_vm(
        tmp_path / "auto",
        "wizard",
        prepare=frozen_wizard,
        env={"FAKE_COMPLETES_AFTER": "2"},
    )
    assert not any("acknowledge-credential" in call for call in calls)
    assert first(calls, "local-seed --step campaign ") > first(
        calls, "exec -T mail-dispatch cat /tmp/stewardship-consumer/background.json"
    )
    assert "Setup and the campaign complete in" in output
    text = VM_SCRIPT.read_text()
    assert text.count("acknowledge-credential") == 2
    for command in ("cmd_wizard() {", "cmd_finish_setup() {"):
        start = text.index(command)
        assert "finish_setup " in text[start : text.index("\n}\n", start)], command


# ---------------------------------------------------------------------------
# Named snapshots, the data-only switch and the Testing state (#1395).

OLD_IMAGE = f"parishkit-stewardship-local:{'a' * 40}-1690000000"
MIGRATIONS = "stewardship_accounts 0001_initial;stewardship_accounts 0002_more"
# Migration rows from either side of the digest retirement (#1834).
CONVERSION = "stewardship_reports 0041_report_conversion"
RETIRED = f"{MIGRATIONS};{CONVERSION};stewardship_reports 0046_retire_digests"


def snapshot_of(
    snapshots,
    name,
    *,
    image=IMAGE,
    uuid="u",
    clock="normal",
    migrations=None,
    meta=None,
    leader=None,
):
    """A saved snapshot as cmd_snapshot leaves it (meta and migrations optional)."""
    dir = snapshots / name
    # Each snapshot is a day younger than the one made before it.
    day = len(list(snapshots.iterdir())) + 1 if snapshots.exists() else 1
    (dir / "root" / "run" / "local" / "clock").mkdir(parents=True)
    (dir / "root" / "run" / "local" / "clock" / "mode").write_text(clock + "\n")
    (dir / "root" / "config" / "services").mkdir(parents=True)
    (dir / "root" / "config" / "services" / "compose.json").write_text(
        json.dumps(
            {
                "services": {
                    "web": {
                        "image": image,
                        "environment": {"A": "1"},
                        "volumes": [],
                        "tmpfs": ["/tmp"],
                    }
                }
            }
            | SEEDER_ADDRESS
        )
    )
    (dir / "taken").write_text(f"2026-10-0{day}T00:00:00Z\n")
    (dir / "deployment.yaml").write_text("deployment: {}\n")
    (dir / "deployment.env").write_text(
        f"UUID={uuid}\nADMIN_EMAIL=a@example.test\nIMAGE={image}\nDEBUG=0\n"
    )
    if migrations is not None:
        (dir / "migrations").write_text(migrations.replace(";", "\n") + "\n")
    if meta is not None:
        (dir / "meta").write_text(meta)
    if leader is not None:
        (dir / "root" / "run" / "local" / "leader-user").write_text(leader + "\n")


def running(root, etc, snapshots):
    """An installed, normal-clock root whose files name the running image."""
    installed(root, etc, snapshots)
    services = root / "config" / "services"
    (services / "compose.json").write_text(
        json.dumps(
            {
                "services": {
                    "web": {
                        "image": IMAGE,
                        "environment": {"A": "1"},
                        "volumes": [],
                        "tmpfs": ["/tmp"],
                    }
                }
            }
            | SEEDER_ADDRESS
        )
    )
    (services / "worker.yaml").write_text(
        json.dumps(
            {"deployment": {"bulk_family_send": True, "local_smtp_latency_ms": 600}}
        )
    )
    with (etc / "local.env").open("a") as env:
        env.write(f"PREVIOUS_IMAGE={OLD_IMAGE}\n")


def test_snapshot_save_writes_the_meta_record_read_before_the_stop(tmp_path):
    """meta and migrations come from the running database, then the copy."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        (root / "run" / "local" / "leader-user").write_text("leader@example.test\n")

    calls, output, _ = run_vm(
        tmp_path,
        "snapshot",
        "testing",
        "0",
        MAC_FREE,
        "8",
        prepare=prepare,
        env={"FAKE_MIGRATIONS": MIGRATIONS},
    )
    stop = first(calls, " stop --timeout 90")
    assert first(calls, "FROM django_migrations") < stop
    assert first(calls, "FROM stewardship_system_configuration") < stop
    # The record's queries are read-only.
    for call in calls:
        if "FROM django_migrations" in call or "FROM stewardship_system" in call:
            assert "default_transaction_read_only=on" in call
    assert stop < first(calls, "rsync -aHAX --delete --numeric-ids")
    dir = tmp_path / "snapshots" / "testing"
    assert (dir / "migrations").read_text() == (
        "stewardship_accounts 0001_initial\nstewardship_accounts 0002_more\n"
    )
    meta = dict(line.split("=", 1) for line in (dir / "meta").read_text().splitlines())
    assert meta["image"] == IMAGE and meta["commit"] == COMMIT
    assert meta["uuid"] == "u" and meta["clock"] == "normal"
    assert (meta["mode"], meta["campaign"], meta["families"]) == (
        "testing",
        "draft",
        "100",
    )
    assert meta["testing_recipient"] == "testing@example.test"
    assert meta["migrations"] == "2" and meta["leader_user"] == "leader@example.test"
    assert (
        meta["size_kb"].isdigit()
        and meta["taken"] == (dir / "taken").read_text().strip()
    )


def test_snapshot_save_refuses_an_existing_name_unless_replacing(tmp_path):
    """REPLACE 0 refuses before anything stops; 1 and the alias form overwrite."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        snapshot_of(snapshots, "testing")

    calls, output, _ = run_vm(
        tmp_path / "keep", "snapshot", "testing", "0", MAC_FREE, "8",
        status=1, prepare=prepare,
    )  # fmt: skip
    assert "'snapshot save testing --replace'" in output
    assert calls == []
    calls, _, _ = run_vm(
        tmp_path / "replace", "snapshot", "testing", "1", MAC_FREE, "8",
        prepare=prepare,
    )  # fmt: skip
    assert any(c.startswith("rsync ") for c in calls)
    # The one-argument form (an older laptop half) replaces, as before #1395.
    calls, _, _ = run_vm(tmp_path / "alias", "snapshot", "testing", prepare=prepare)
    assert any(c.startswith("rsync ") for c in calls)


@pytest.mark.parametrize(
    "args, env, reason",
    [
        (("1", "2"), {}, "already 2 other snapshots (the cap is 2)"),
        (("1", "8"), {"FAKE_VM_FREE_KB": "1000"}, "below its 5.0 GiB reserve"),
        (("20971520", "8"), {}, "the Mac would have"),
    ],
)
def test_snapshot_save_refuses_past_the_cap_or_a_disk_reserve(
    tmp_path, args, env, reason
):
    """The refusal comes before anything stops and lists the snapshots by age."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        (root / "run" / "persistent").mkdir(parents=True)
        (root / "run" / "persistent" / "data").write_bytes(b"x" * 200_000)
        snapshot_of(snapshots, "seeded")
        snapshot_of(snapshots, "post-setup")

    mac_free = MAC_FREE if args[0] == "1" else args[0]
    calls, output, _ = run_vm(
        tmp_path, "snapshot", "testing", "0", mac_free, args[1],
        status=1, prepare=prepare, env=env,
    )  # fmt: skip
    assert "Not saving the testing snapshot" in output and reason in output
    assert output.index("seeded  taken 2026-10-01") < output.index("post-setup  taken")
    assert "snapshot delete NAME" in output
    assert not any(" stop" in c or c.startswith("rsync") for c in calls)


def test_snapshot_save_counts_a_replacement_against_the_others_only(tmp_path):
    """Replacing one of two snapshots under a cap of 2 is allowed."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        snapshot_of(snapshots, "seeded")
        snapshot_of(snapshots, "post-setup")

    calls, _, _ = run_vm(
        tmp_path, "snapshot", "seeded", "1", MAC_FREE, "2", prepare=prepare
    )
    assert any(c.startswith("rsync ") for c in calls)


@pytest.mark.parametrize("name", ["Bad", "-x", "a" * 33, "../x", "a/b"])
def test_every_snapshot_command_refuses_a_bad_name_first(tmp_path, name):
    """The VM half checks the name itself, before the marker or any call."""
    for args in (
        ("snapshot", name),
        ("snapshot-delete", name),
        ("snapshot-restore", name, "/x/h.sh"),
        ("reset", name),
    ):
        calls, output, _ = run_vm(tmp_path / args[0], *args, status=1, prepare=running)
        assert "1-32 lowercase letters" in output, args
        assert calls == [], args


def test_snapshot_list_shows_each_record_and_what_a_switch_would_do(tmp_path):
    """Same image, same or older or newer schema, another install, a gone image."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        snapshot_of(
            snapshots, "seeded",
            meta="mode=production\ncampaign=active\nsize_kb=1048576\n",
        )  # fmt: skip
        snapshot_of(snapshots, "same-schema", image=OLD_IMAGE, migrations=MIGRATIONS)
        snapshot_of(
            snapshots, "older", image=OLD_IMAGE,
            migrations="stewardship_accounts 0001_initial",
        )  # fmt: skip
        snapshot_of(
            snapshots, "newer", image=OLD_IMAGE,
            migrations=MIGRATIONS + ";stewardship_accounts 0003_new",
        )  # fmt: skip
        snapshot_of(snapshots, "elsewhere", uuid="other")
        snapshot_of(snapshots, "legacy", image=OLD_IMAGE, clock="fake")
        snapshot_of(
            snapshots, "converted", image=OLD_IMAGE,
            migrations=f"{MIGRATIONS};{CONVERSION}",
        )  # fmt: skip

    def row(output, name):
        return next(line for line in output.splitlines() if line.split()[:1] == [name])

    _, output, _ = run_vm(
        tmp_path / "a", "snapshot-list", MAC_FREE, prepare=prepare,
        env={"FAKE_MIGRATIONS": MIGRATIONS, "FAKE_IMAGE_EXISTS": "1"},
    )  # fmt: skip
    assert "production/active" in row(output, "seeded")
    assert "data only" in row(output, "seeded") and "1.0 GiB" in row(output, "seeded")
    assert COMMIT[:7] in row(output, "seeded")
    # Another image always migrates forward, even with the same migrations:
    # the upgrade check also covers grants and logins.
    assert "migrates forward" in row(output, "same-schema")
    assert "migrates forward" in row(output, "older")
    assert "refused: newer code" in row(output, "newer")
    assert "refused: other install" in row(output, "elsewhere")
    # No record and another image: newer code cannot be ruled out.
    assert "refused: no record" in row(output, "legacy")
    assert " fake " in row(output, "legacy")
    assert "?/?" in row(output, "legacy")
    assert "VM free: 30.0 GiB" in output and "Mac free: 100.0 GiB" in output
    # Without the snapshot's image the upgrade path is refused.
    _, output, _ = run_vm(
        tmp_path / "b", "snapshot-list", prepare=prepare,
        env={"FAKE_MIGRATIONS": MIGRATIONS},
    )  # fmt: skip
    assert "refused: image gone" in row(output, "older")
    assert "Mac free" not in output
    # Once the running code retired the digests (#1834), only a snapshot
    # taken after the report email conversion can migrate forward.
    _, output, _ = run_vm(
        tmp_path / "d", "snapshot-list", prepare=prepare,
        env={"FAKE_MIGRATIONS": RETIRED, "FAKE_IMAGE_EXISTS": "1"},
    )  # fmt: skip
    assert "refused: unconverted" in row(output, "older")
    assert "migrates forward" in row(output, "converted")
    # status ends with the same summary.
    _, output, _ = run_vm(tmp_path / "c", "status", MAC_FREE, prepare=prepare)
    assert "Snapshots in " in output and "Mac free: 100.0 GiB" in output


def test_snapshot_delete_removes_only_that_snapshot_then_trims(tmp_path):
    """Delete, then fstrim so the sparse VM disk file can shrink on the Mac."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        snapshot_of(snapshots, "old")
        snapshot_of(snapshots, "seeded")

    calls, output, root = run_vm(tmp_path, "snapshot-delete", "old", prepare=prepare)
    snapshots = tmp_path / "snapshots"
    assert not (snapshots / "old").exists() and (snapshots / "seeded").exists()
    assert calls == ["fstrim -v /"]
    assert root.exists()
    calls, output, _ = run_vm(
        tmp_path / "none", "snapshot-delete", "old", status=1, prepare=running
    )
    assert "No old snapshot" in output and calls == []


def test_a_data_switch_keeps_the_code_and_restores_only_the_data(tmp_path):
    """Same schema: stop, restore with the kept paths excluded, recreate, sign in."""
    host = tmp_path / "host.sh"

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        snapshot_of(
            snapshots, "testing", migrations=MIGRATIONS,
            leader="leader@example.test",
        )  # fmt: skip

    before = None

    def capture(root, etc, snapshots):
        nonlocal before
        prepare(root, etc, snapshots)
        before = (etc / "local.env").read_text()

    calls, output, root = run_vm(
        tmp_path, "snapshot-restore", "testing", str(host), prepare=capture,
        env={"FAKE_MIGRATIONS": MIGRATIONS},
    )  # fmt: skip
    stop = first(calls, " stop --timeout 90")
    copy = first(calls, "rsync ")
    assert first(calls, "FROM django_migrations") < stop < copy
    snapshot = tmp_path / "snapshots" / "testing"
    assert calls[copy] == (
        "rsync -aHAX --checksum --inplace --delete --numeric-ids "
        "--exclude=/.parishkit-local --exclude=/.stewardship-provisioned.json "
        "--exclude=/.stewardship-provisioning.json --exclude=/config/services/ "
        "--exclude=/config/deployment/ --exclude=/cache/ "
        f"--exclude=/run/persistent/caddy/ {snapshot}/root/ {root}/"
    )
    assert any(" up --detach --force-recreate web" in c for c in calls[copy:])
    assert not any(c.startswith("host ") for c in calls)
    # The deployment record (image, previous image, debug) is untouched.
    assert (tmp_path / "etc" / "local.env").read_text() == before
    signins = [c for c in calls if "local-sign-in" in c]
    assert [c.split("--email ")[1] for c in signins] == [
        "a@example.test",
        "leader@example.test",
    ]
    assert "Switched to the testing snapshot (data)" in output
    assert "http://localhost:8025" in output


def test_the_switch_names_the_real_conversion_and_retirement_migrations():
    """plan_switch's #1834 check names migrations that exist, as the host half does."""
    text = VM_SCRIPT.read_text()
    migrations = ROOT / "src/parishkit/stewardship/reports/migrations"
    for variable in ("conversion_migration", "retirement_migration"):
        name = re.search(rf"^{variable}=(\d{{4}}_[a-z_]+)$", text, re.M).group(1)
        assert (migrations / f"{name}.py").is_file(), variable
    conversion = re.search(r"^conversion_migration=(\S+)$", text, re.M).group(1)
    assert f"name='{conversion}'" in HOST_SCRIPT.read_text()


@pytest.mark.parametrize(
    "snapshot, env, reason",
    [
        (
            {"image": OLD_IMAGE, "migrations": MIGRATIONS + ";stewardship_x 0009_new"},
            {"FAKE_MIGRATIONS": MIGRATIONS},
            f"taken on commit {'a' * 40}",
        ),
        ({"uuid": "other"}, {}, "belongs to another install"),
        ({"image": OLD_IMAGE}, {}, "before snapshots recorded their migrations"),
        (
            {"image": OLD_IMAGE, "migrations": MIGRATIONS},
            {"FAKE_MIGRATIONS": "", "FAKE_IMAGE_EXISTS": "1"},
            "Could not read the running database's migrations",
        ),
        (
            {"image": OLD_IMAGE, "migrations": "stewardship_accounts 0001_initial"},
            {"FAKE_MIGRATIONS": MIGRATIONS},
            "which is no longer in the VM",
        ),
        (
            {"image": OLD_IMAGE, "migrations": MIGRATIONS},
            {"FAKE_MIGRATIONS": RETIRED, "FAKE_IMAGE_EXISTS": "1"},
            "taken before the report email conversion",
        ),
    ],
)
def test_a_switch_refuses_before_any_service_stops(tmp_path, snapshot, env, reason):
    """Newer code, another install, or a missing image: nothing stops or changes."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        snapshot_of(snapshots, "x", **snapshot)

    calls, output, _ = run_vm(
        tmp_path, "snapshot-restore", "x", str(tmp_path / "host.sh"),
        status=1, prepare=prepare, env=env,
    )  # fmt: skip
    assert reason in output
    assert not any(" stop" in c or c.startswith("rsync") for c in calls)


def test_an_older_snapshot_is_migrated_forward_by_the_host_half(tmp_path):
    """Restore whole on its image, then the upgrade to the running image."""
    host = tmp_path / "host.sh"

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        host.write_text(f"#!/usr/bin/env bash\n{FAKE_HOST}\n")
        snapshot_of(
            snapshots, "older", image=OLD_IMAGE,
            migrations="stewardship_accounts 0001_initial",
        )  # fmt: skip

    calls, output, root = run_vm(
        tmp_path, "snapshot-restore", "older", str(host), prepare=prepare,
        env={
            "FAKE_MIGRATIONS": MIGRATIONS, "FAKE_IMAGE_EXISTS": "1",
            "FAKE_HOST_ENV": "1",
        },
    )  # fmt: skip
    copy = first(calls, "rsync ")
    snapshot = tmp_path / "snapshots" / "older"
    assert first(calls, " stop --timeout 90") < copy
    # Only Caddy's data is kept: the rest is the snapshot's, on its image.
    assert calls[copy] == (
        "rsync -aHAX --checksum --inplace --delete --numeric-ids "
        f"--exclude=/run/persistent/caddy/ {snapshot}/root/ {root}/"
    )
    started = first(calls, " up --detach --force-recreate web")
    upgrade = first(calls, "host - ")
    assert copy < started < upgrade
    etc = tmp_path / "etc"
    # The running image, with the schema change, keeping the debug setting.
    assert calls[upgrade] == (
        f"host - {IMAGE} {root} parishkit-local {etc / 'deployment.yaml'} u 1 "
        f"upgrade local {tmp_path / 'build'} debug=1"
    )
    # The running rendering choices travel to the host half.
    assert calls[upgrade + 1] == "host bulk=on latency=600 parishsoft=fake redirect=off"
    # The record is the running deployment's again.
    assert record(tmp_path)["IMAGE"] == IMAGE
    assert record(tmp_path)["PREVIOUS_IMAGE"] == OLD_IMAGE
    assert record(tmp_path)["DEBUG"] == "1"
    assert "Switched to the older snapshot (upgrade)" in output
    assert any("local-sign-in" in c for c in calls[upgrade:])


@pytest.mark.parametrize(
    ("value", "carried"),
    [(DEFAULT_API_BASE_URL, "real"), ("https://other.example/api/v2", None)],
)
def test_an_older_snapshot_switch_carries_the_parishsoft_choice(
    tmp_path, value, carried
):
    """A running real-API choice (#1399) reaches the host half; any other
    rendered URL is refused before anything stops, as the host half would."""
    host = tmp_path / "host.sh"

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        worker = root / "config" / "services" / "worker.yaml"
        document = json.loads(worker.read_text())
        document["deployment"]["parishsoft_base_url"] = value
        worker.write_text(json.dumps(document))
        host.write_text(f"#!/usr/bin/env bash\n{FAKE_HOST}\n")
        snapshot_of(
            snapshots, "older", image=OLD_IMAGE,
            migrations="stewardship_accounts 0001_initial",
        )  # fmt: skip

    calls, output, _ = run_vm(
        tmp_path, "snapshot-restore", "older", str(host), prepare=prepare,
        status=0 if carried else 1,
        env={
            "FAKE_MIGRATIONS": MIGRATIONS, "FAKE_IMAGE_EXISTS": "1",
            "FAKE_HOST_ENV": "1",
        },
    )  # fmt: skip
    if carried:
        upgrade = first(calls, "host - ")
        assert calls[upgrade + 1] == (
            f"host bulk=on latency=600 parishsoft={carried} redirect=off"
        )
    else:
        assert "not the real API; refusing" in output
        assert not any(" stop" in c or c.startswith("rsync") for c in calls)


def test_an_older_snapshot_switch_carries_the_mail_redirect(tmp_path):
    """A running mail redirect (#1399) reaches the host half on a switch."""
    host = tmp_path / "host.sh"

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        worker = root / "config" / "services" / "worker.yaml"
        document = json.loads(worker.read_text())
        document["deployment"]["mail_redirect"] = "jeff@example.org"
        worker.write_text(json.dumps(document))
        host.write_text(f"#!/usr/bin/env bash\n{FAKE_HOST}\n")
        snapshot_of(
            snapshots, "older", image=OLD_IMAGE,
            migrations="stewardship_accounts 0001_initial",
        )  # fmt: skip

    calls, _, _ = run_vm(
        tmp_path, "snapshot-restore", "older", str(host), prepare=prepare,
        env={
            "FAKE_MIGRATIONS": MIGRATIONS, "FAKE_IMAGE_EXISTS": "1",
            "FAKE_HOST_ENV": "1",
        },
    )  # fmt: skip
    upgrade = first(calls, "host - ")
    assert calls[upgrade + 1] == (
        "host bulk=on latency=600 parishsoft=fake redirect=jeff@example.org"
    )


def test_the_vm_half_names_the_real_parishsoft_api():
    """The VM half's copy of the real API's base URL is the package's."""
    text = (ROOT / "tools" / "stewardship-local-vm.sh").read_text()
    assert f"            {DEFAULT_API_BASE_URL}) parishsoft=real ;;" in text


def post_setup_ready(root, etc, snapshots):
    """A running deployment with a post-setup snapshot of a Testing draft."""
    running(root, etc, snapshots)
    snapshot_of(snapshots, "post-setup", clock="fake", migrations=MIGRATIONS)
    (root / "config" / "services" / "compose.faketime.json").write_text(
        json.dumps(
            {"services": {"web": {"image": IMAGE, "environment": {}, "volumes": []}}}
        )
    )
    (root / "run" / "local" / "clock" / "offset").write_text("-1468800\n")
    faketime = root.parent / "build" / "deploy" / "stewardship" / "Dockerfile.faketime"
    faketime.write_text("FROM x\n")


def test_seed_testing_switches_to_post_setup_and_moves_the_clock_forward(tmp_path):
    """A Testing draft at real time; the mail catcher is cleared first."""
    calls, output, root = run_vm(
        tmp_path, "seed-testing", str(tmp_path / "host.sh"), prepare=post_setup_ready,
        env={
            "FAKE_MIGRATIONS": MIGRATIONS, "FAKE_COMPLETED": "t",
            "FAKE_IMAGE_EXISTS": "1",
        },
    )  # fmt: skip
    clock = root / "run" / "local" / "clock"
    assert (clock / "mode").read_text() == "normal\n"
    assert (clock / "offset").read_text() == "+0\n"
    clear = first(
        calls, "-X DELETE --max-time 20 http://127.0.0.1:8025/api/v1/messages"
    )
    assert first(calls, "rsync ") < clear
    # The clock moves only with every service stopped, then all are recreated.
    stops = [i for i, c in enumerate(calls) if " stop --timeout 90" in c]
    assert len(stops) == 2 and clear < stops[1]
    assert any(" up --detach --force-recreate web" in c for c in calls[stops[1] :])
    assert "has no 'testing' step" in output
    assert "A Testing-mode draft campaign of 100 Families" in output
    assert "snapshot save testing" in output
    assert any("local-sign-in" in c for c in calls)


def test_seed_testing_runs_the_seeder_step_when_the_image_has_it(tmp_path):
    """An image whose seeder offers `testing` gets it, under the web identity."""
    calls, output, _ = run_vm(
        tmp_path, "seed-testing", str(tmp_path / "host.sh"), prepare=post_setup_ready,
        env={
            "FAKE_MIGRATIONS": MIGRATIONS, "FAKE_COMPLETED": "t",
            "FAKE_IMAGE_EXISTS": "1", "FAKE_STEPS": '["prepare", "testing"]',
        },
    )  # fmt: skip
    step = first(calls, "local-seed --step testing")
    assert "--user 10001:10001" in calls[step]
    assert first(calls, "-X DELETE") < step


@pytest.mark.parametrize(
    "summary, reason",
    [
        ("production\tactive\t100\ttesting@example.test", "not a Testing draft"),
        ("testing\tscheduled\t100\ttesting@example.test", "not a Testing draft"),
        ("testing\tdraft\t100\treal@parish.example", "not an @example.test address"),
    ],
)
def test_seed_testing_refuses_anything_but_a_synthetic_testing_draft(
    tmp_path, summary, reason
):
    """The clock never moves and no mail is cleared for a refused state."""
    calls, output, root = run_vm(
        tmp_path, "seed-testing", str(tmp_path / "host.sh"), status=1,
        prepare=post_setup_ready,
        env={
            "FAKE_MIGRATIONS": MIGRATIONS, "FAKE_COMPLETED": "t",
            "FAKE_IMAGE_EXISTS": "1", "FAKE_SUMMARY": summary,
        },
    )  # fmt: skip
    assert reason in output
    assert (root / "run" / "local" / "clock" / "mode").read_text() == "fake\n"
    assert not any("-X DELETE" in c for c in calls)


def test_seed_testing_refuses_a_recorded_live_post_setup_before_switching(tmp_path):
    """A meta record that is not a Testing draft refuses before anything stops."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        snapshot_of(
            snapshots, "post-setup", clock="fake",
            meta="mode=production\ncampaign=active\n",
        )  # fmt: skip

    calls, output, _ = run_vm(
        tmp_path, "seed-testing", str(tmp_path / "host.sh"), status=1, prepare=prepare
    )
    assert "not a Testing draft" in output and calls == []
    calls, output, _ = run_vm(
        tmp_path / "none", "seed-testing", str(tmp_path / "host.sh"), status=1,
        prepare=running,
    )  # fmt: skip
    assert "No post-setup snapshot" in output and calls == []


def test_a_failed_upgrade_of_an_older_snapshot_says_where_it_stopped(tmp_path):
    """The host half's failure ends the switch with what the deployment holds."""
    host = tmp_path / "host.sh"

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        host.write_text(f"#!/usr/bin/env bash\n{FAKE_HOST}\n")
        snapshot_of(
            snapshots, "older", image=OLD_IMAGE,
            migrations="stewardship_accounts 0001_initial",
        )  # fmt: skip

    calls, output, _ = run_vm(
        tmp_path, "snapshot-restore", "older", str(host), status=1, prepare=prepare,
        env={
            "FAKE_MIGRATIONS": MIGRATIONS, "FAKE_IMAGE_EXISTS": "1",
            "FAKE_HOST_FAIL": "1",
        },
    )  # fmt: skip
    assert "did not finish" in output and f"holds that data on {OLD_IMAGE}" in output
    assert not any("local-sign-in" in c for c in calls)


def test_snapshot_save_refuses_unreadable_migrations_before_the_stop(tmp_path):
    """No empty migrations record: the save refuses with nothing stopped."""
    calls, output, _ = run_vm(
        tmp_path, "snapshot", "testing", "0", MAC_FREE, "8", status=1,
        prepare=running, env={"FAKE_MIGRATIONS": ""},
    )  # fmt: skip
    assert "Could not read the applied migrations" in output
    assert not any(" stop" in c or c.startswith("rsync") for c in calls)
    assert not (tmp_path / "snapshots" / "testing").exists()


def test_a_failed_save_restarts_the_services_and_keeps_the_earlier_copy(tmp_path):
    """The half copy is never a snapshot; a replaced one stays whole."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        snapshot_of(snapshots, "testing", meta="mode=testing\n")

    calls, output, _ = run_vm(
        tmp_path, "snapshot", "testing", "1", MAC_FREE, "8", status=23,
        prepare=prepare, env={"FAKE_RSYNC_FAIL": "1"},
    )  # fmt: skip
    stop = first(calls, " stop --timeout 90")
    assert any(" up --detach" in c for c in calls[stop:])
    assert "FAILED: the testing snapshot (exit 23)" in output
    assert "services are running again" in output
    snapshot = tmp_path / "snapshots" / "testing"
    assert (snapshot / "taken").read_text() == "2026-10-01T00:00:00Z\n"
    assert (snapshot / "meta").read_text() == "mode=testing\n"
    # A directory without `taken` (what a half copy would be) is no snapshot.
    (tmp_path / "snapshots" / "half" / "root").mkdir(parents=True)
    run_vm(tmp_path / "probe", "has-snapshot", "half", status=1)
    _, output, _ = run_vm(tmp_path, "snapshot-list", prepare=None)
    assert "half" not in output and ".testing.partial" not in output


def test_a_replacement_counts_the_whole_copy_against_the_vm_reserve(tmp_path):
    """It is written beside the old copy, so only the full size is safe."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        (root / "run" / "persistent").mkdir(parents=True)
        (root / "run" / "persistent" / "data").write_bytes(b"x" * 400_000)
        snapshot_of(snapshots, "testing")
        shutil.copytree(root, snapshots / "testing" / "root", dirs_exist_ok=True)

    calls, output, _ = run_vm(
        tmp_path, "snapshot", "testing", "1", MAC_FREE, "8", status=1,
        prepare=prepare, env={"FAKE_VM_FREE_KB": str(5 * 1024 * 1024 + 100)},
    )  # fmt: skip
    assert "below its 5.0 GiB reserve" in output
    assert calls == []


def test_a_successful_replacement_links_the_old_copy_then_trims(tmp_path):
    """--link-dest the earlier copy, swap it out once whole, then fstrim."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        snapshot_of(snapshots, "testing")

    calls, _, root = run_vm(
        tmp_path, "snapshot", "testing", "1", MAC_FREE, "8", prepare=prepare
    )
    snapshots = tmp_path / "snapshots"
    copy = first(calls, "rsync ")
    assert f"--link-dest={snapshots}/testing/root" in calls[copy]
    assert calls[-1] == "fstrim -v /"
    assert not (snapshots / ".testing.old").exists()
    assert (snapshots / "testing" / "meta").exists()


def test_the_one_argument_save_keeps_the_old_defaults(tmp_path):
    """An older laptop half's `snapshot NAME`: replace, no Mac check, cap 8."""

    def with_others(count):
        def prepare(root, etc, snapshots):
            running(root, etc, snapshots)
            snapshot_of(snapshots, "post-setup")
            for n in range(count):
                snapshot_of(snapshots, f"other-{n}")

        return prepare

    calls, _, _ = run_vm(tmp_path / "seven", "snapshot", "post-setup",
                         prepare=with_others(7))  # fmt: skip
    assert any(c.startswith("rsync ") for c in calls)
    calls, output, _ = run_vm(tmp_path / "eight", "snapshot", "post-setup",
                              status=1, prepare=with_others(8))  # fmt: skip
    assert "(the cap is 8)" in output and "Mac would" not in output


def test_a_fake_clock_data_switch_builds_the_fake_clock_images(tmp_path):
    """The running image's derived images; refused without its override."""

    def prepare(root, etc, snapshots):
        post_setup_ready(root, etc, snapshots)

    _, output, _ = run_vm(
        tmp_path / "ok", "snapshot-restore", "post-setup", str(tmp_path / "h.sh"),
        prepare=prepare, env={"FAKE_IMAGE_EXISTS": "1"},
    )  # fmt: skip
    assert "Building the fake-clock images" in output
    assert "Starting web (fake clock mode)" in output

    def no_override(root, etc, snapshots):
        post_setup_ready(root, etc, snapshots)
        (root / "config" / "services" / "compose.faketime.json").unlink()

    calls, output, _ = run_vm(
        tmp_path / "none", "snapshot-restore", "post-setup", str(tmp_path / "h.sh"),
        status=1, prepare=no_override,
    )  # fmt: skip
    assert "renders no fake-clock override" in output
    assert not any(" stop" in c for c in calls)


def test_a_failed_data_switch_names_the_whole_restore_that_recovers(tmp_path):
    """After the stop nothing is started on mixed data; the advice names it."""

    def prepare(root, etc, snapshots):
        running(root, etc, snapshots)
        snapshot_of(snapshots, "testing", migrations=MIGRATIONS)

    calls, output, _ = run_vm(
        tmp_path, "snapshot-restore", "testing", str(tmp_path / "h.sh"),
        status=23, prepare=prepare, env={"FAKE_RSYNC_FAIL": "1"},
    )  # fmt: skip
    stop = first(calls, " stop --timeout 90")
    assert not any(" up --detach" in c for c in calls[stop:])
    assert "FAILED: the switch to the testing snapshot (exit 23)" in output
    assert "'reset testing'" in output and "reset NAME" not in output


# ---------------------------------------------------------------------------
# Changing the fake ParishSoft's data and refreshing it (#1780).


def fake_parishsoft_root(root, etc, snapshots):
    """A set-up root with web's rendering and the fake's configuration file."""
    frozen_wizard(root, etc, snapshots)
    (root / "run" / "local" / "fake-parishsoft.json").write_text('{"seed": 7}\n')
    (root / "config" / "services" / "worker.yaml").write_text(
        json.dumps({"deployment": {"profile": "local"}})
    )


def test_a_seeder_step_refuses_a_rendering_without_the_seeder_address(tmp_path):
    """No `docker run` on a dynamic backend address, which no source rule allows."""

    def prepare(root, etc, snapshots):
        fake_parishsoft_root(root, etc, snapshots)
        compose = root / "config" / "services" / "compose.json"
        compose.write_text(
            json.dumps({"services": json.loads(compose.read_text())["services"]})
        )

    calls, output, _ = run_vm(
        tmp_path,
        "fake-parishsoft",
        "refresh",
        status=1,
        prepare=prepare,
        env={"FAKE_COMPLETED": "t"},
    )
    assert "names no seeder address" in output
    assert not any("local-seed --step refresh " in call for call in calls)


def test_a_fake_parishsoft_edit_rewrites_the_file_and_recreates_only_the_fake(
    tmp_path,
):
    """The image checks the edit; the new document replaces the file, then only
    the fake ParishSoft is recreated, since a container keeps its old inode."""
    calls, output, root = run_vm(
        tmp_path,
        "fake-parishsoft",
        "member",
        "500010",
        "email=x@example.test",
        prepare=fake_parishsoft_root,
    )
    edit = first(calls, "fake-parishsoft-edit member 500010 email=x@example.test")
    assert calls[edit].startswith("run --rm --init --network none ")
    assert " -i " in calls[edit] and IMAGE in calls[edit]
    recreate = first(calls, "up --detach --no-deps --force-recreate fake-parishsoft")
    assert recreate > edit
    # The fake stops before its file is read, so no contact PUT it records
    # there (#1884) is lost when the new document replaces the file.
    assert first(calls, " stop fake-parishsoft") < edit
    file = root / "run" / "local" / "fake-parishsoft.json"
    assert json.loads(file.read_text()) == {"seed": 7, "edited": True}
    # The Compose selection, which can refuse, runs before the edit and write.
    assert first(calls, "compose ls --all --format json") < edit
    assert "fake-parishsoft refresh" in output


def test_a_refused_fake_parishsoft_edit_changes_nothing(tmp_path):
    """The image's refusal is shown; the file and the running fake are kept."""
    calls, output, root = run_vm(
        tmp_path,
        "fake-parishsoft",
        "member",
        "1",
        "email=x@example.test",
        status=2,
        prepare=fake_parishsoft_root,
        env={"FAKE_EDIT_FAIL": "1"},
    )
    assert "There is no Member 1" in output
    file = root / "run" / "local" / "fake-parishsoft.json"
    assert file.read_text() == '{"seed": 7}\n'
    assert not any("--force-recreate" in call for call in calls)
    # The fake, stopped for the edit, is started again on the same file.
    assert first(calls, " stop fake-parishsoft") < first(
        calls, " start fake-parishsoft"
    )


def test_fake_parishsoft_show_prints_and_writes_nothing(tmp_path):
    """show is read-only: no file write and no recreate."""
    calls, output, root = run_vm(
        tmp_path, "fake-parishsoft", "show", prepare=fake_parishsoft_root
    )
    assert "SHOWN" in output
    assert (root / "run" / "local" / "fake-parishsoft.json").read_text() == (
        '{"seed": 7}\n'
    )
    assert not any("--force-recreate" in call for call in calls)


def test_fake_parishsoft_refresh_runs_the_seeder_refresh_step_under_web(tmp_path):
    """The Admin's full refresh runs as the seeder's refresh step, one-off web."""
    calls, output, _ = run_vm(
        tmp_path,
        "fake-parishsoft",
        "refresh",
        prepare=fake_parishsoft_root,
        env={"FAKE_COMPLETED": "t"},
    )
    step = calls[first(calls, "local-seed --step refresh ")]
    assert "--network parishkit-local_backend" in step
    # Its own fixed address, the one the LOCAL source rules allow (#2111).
    assert "--ip 172.29.240.30 " in step
    assert "--admin-email a@example.test" in step
    assert "Full ParishSoft refresh finished" in output
    # Refused before setup is complete.
    calls, output, _ = run_vm(
        tmp_path / "early",
        "fake-parishsoft",
        "refresh",
        status=1,
        prepare=fake_parishsoft_root,
    )
    assert "has not completed" in output
    assert not any("local-seed" in call for call in calls)


@pytest.mark.parametrize("args", [("refresh",), ("member", "500010", "suffix=Jr.")])
def test_fake_parishsoft_refuses_a_deployment_reading_the_real_parishsoft(
    tmp_path, args
):
    """After deploy --parishsoft real nothing runs: no edit, no refresh."""

    def real(root, etc, snapshots):
        fake_parishsoft_root(root, etc, snapshots)
        url = "https://ps-fs-external-api-prod.azurewebsites.net/api/v2"
        (root / "config" / "services" / "worker.yaml").write_text(
            json.dumps({"deployment": {"parishsoft_base_url": url}})
        )

    calls, output, _ = run_vm(
        tmp_path,
        "fake-parishsoft",
        *args,
        status=1,
        prepare=real,
        env={"FAKE_COMPLETED": "t"},
    )
    assert "reads the real ParishSoft" in output
    assert calls == []


def test_fake_parishsoft_refuses_without_the_fake_configuration(tmp_path):
    """A root without the fake's file is refused before any Docker call."""
    calls, output, _ = run_vm(
        tmp_path, "fake-parishsoft", "show", status=1, prepare=frozen_wizard
    )
    assert "No fake ParishSoft configuration" in output
    assert calls == []


def test_a_pledge_edit_defaults_to_the_campaigns_fund_and_period(tmp_path):
    """Without fund= and start= the campaign's own are read, then passed on."""
    calls, _, _ = run_vm(
        tmp_path,
        "fake-parishsoft",
        "pledge",
        "100003",
        "1800",
        prepare=fake_parishsoft_root,
        env={"FAKE_COMPLETED": "t"},
    )
    lookup = first(calls, "local-seed --step pledge-period ")
    edit = first(
        calls, "fake-parishsoft-edit pledge 100003 1800 fund=301 start=2027-01-01"
    )
    assert lookup < edit
    # Both given: no lookup.
    calls, _, _ = run_vm(
        tmp_path / "given",
        "fake-parishsoft",
        "pledge",
        "100003",
        "1800",
        "fund=300",
        "start=2026-01-01",
        prepare=fake_parishsoft_root,
        env={"FAKE_COMPLETED": "t"},
    )
    assert not any("pledge-period" in call for call in calls)
    # Before setup there is no campaign to read: refused, nothing written.
    calls, output, root = run_vm(
        tmp_path / "early",
        "fake-parishsoft",
        "pledge",
        "100003",
        "1800",
        status=1,
        prepare=fake_parishsoft_root,
    )
    assert "give fund= and start=" in output
    assert not any("fake-parishsoft-edit" in call for call in calls)
    # The step itself refuses: the command stops with that advice.
    calls, output, root = run_vm(
        tmp_path / "nopledges",
        "fake-parishsoft",
        "pledge",
        "100003",
        "1800",
        status=1,
        prepare=fake_parishsoft_root,
        env={"FAKE_COMPLETED": "t", "FAKE_PERIOD_FAIL": "1"},
    )
    assert "Could not read the campaign's pledge fund" in output
    assert not any("fake-parishsoft-edit" in call for call in calls)
    assert (root / "run" / "local" / "fake-parishsoft.json").read_text() == (
        '{"seed": 7}\n'
    )


@pytest.mark.parametrize("args", [(), ("--help",), ("-h",), ("help",)])
def test_fake_parishsoft_without_a_command_is_refused(tmp_path, args):
    """Help words never reach the edit command, so the file is never emptied."""
    calls, output, root = run_vm(
        tmp_path, "fake-parishsoft", *args, status=1, prepare=fake_parishsoft_root
    )
    assert "fake-parishsoft needs a command" in output
    assert not any("fake-parishsoft-edit" in call for call in calls)


def test_an_answer_that_is_not_a_document_is_never_written(tmp_path):
    """An empty or non-document answer leaves the file and the fake alone."""
    calls, output, root = run_vm(
        tmp_path,
        "fake-parishsoft",
        "undo",
        status=1,
        prepare=fake_parishsoft_root,
        env={"FAKE_EDIT_EMPTY": "1"},
    )
    assert "did not answer with a configuration document" in output
    assert (root / "run" / "local" / "fake-parishsoft.json").read_text() == (
        '{"seed": 7}\n'
    )
    assert not any("--force-recreate" in call for call in calls)


def test_an_unreadable_service_document_fails_the_guard_closed(tmp_path):
    """A service document jq cannot read counts as possibly the real ParishSoft."""

    def unreadable(root, etc, snapshots):
        fake_parishsoft_root(root, etc, snapshots)
        (root / "config" / "services" / "web.yaml").write_text("not: [json\n")

    calls, output, _ = run_vm(
        tmp_path, "fake-parishsoft", "show", status=1, prepare=unreadable
    )
    assert "reads the real ParishSoft" in output
    assert calls == []
