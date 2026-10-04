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
    ):
        assert f"`{command}" in GUIDE.read_text(), command
    # The spec's command table names every laptop command the script accepts.
    for command in (
        "`start`",
        "`deploy [--schema-change] [--bulk on\\|off] [--smtp-latency-ms N]`",
        "`rehearse [--due-in MIN] [--send-only] [--timeout MIN] [--label NAME]`",
        "`deploy --rollback`",
        "`reset --reinstall`",
        "`down`",
        "`ca`",
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
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    base = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("PARISHKIT_") and k != "LIMA_HOME"
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
        ("rehearse", "--bogus"),
        ("rehearse", "--due-in"),
        ("deploy", "--smtp-latency-ms", "123456"),
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
    assert vm_commands(calls) == ["snapshot post-setup"]
    _, calls, _ = run_local(tmp_path / "b", "snapshot", "--seeded")
    assert vm_commands(calls) == ["snapshot seeded"]
    _, calls, _ = run_local(
        tmp_path / "c", "reset", "--seeded", env={"FAKE_HAS_SNAPSHOT": "1"}
    )
    assert vm_commands(calls) == ["has-snapshot seeded", "reset seeded"]
    result, calls, _ = run_local(tmp_path / "d", "reset", "--seeded")
    assert result.returncode == 1 and "No seeded snapshot" in result.stderr
    assert vm_commands(calls) == ["has-snapshot seeded"]


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
    assert vm_commands(calls) == ["status"]
    # A stopped VM is a status, not an error.
    result, calls, _ = run_local(tmp_path / "status2", "status", status="Stopped")
    assert result.returncode == 0 and ssh_calls(calls) == []
    _, calls, _ = run_local(tmp_path / "signin", "sign-in", "--email", "a@example.test")
    assert vm_commands(calls) == ["sign-in a@example.test"]
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
        printf "constant-%s" "$name" ;;
    *" local-seed") echo "invalid command; choose ..." >&2; exit 2 ;;
    *"SELECT EXISTS"*) echo "${FAKE_COMPLETED:-f}" ;;
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
        'echo "rsync $*" >>"$FAKE_LOG"; mkdir -p "${@: -1}"; '
        'cp -R "${@: -2:1}". "${@: -1}"',
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
    """Stop first, rsync --delete --numeric-ids the root with its YAML, restart."""
    calls, output, root = run_vm(tmp_path, "snapshot", "post-setup", prepare=installed)
    stop = first(calls, "compose -p parishkit-local stop")
    copy = first(calls, "rsync ")
    start = first(calls, " up --detach web")
    assert stop < copy < start
    snapshot = tmp_path / "snapshots" / "post-setup"
    assert calls[copy] == f"rsync -aHAX --delete --numeric-ids {root}/ {snapshot}/root/"
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
    echo "host bulk=$bulk latency=$latency" >>"$FAKE_LOG"
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
    assert "bulk=on latency=600" in calls[-1]
    # Without them, both carry over.
    calls, _, _ = run_vm(
        tmp_path / "keep",
        "deploy",
        NEW_IMAGE,
        "0",
        str(tmp_path / "keep" / "host.sh"),
        prepare=with_host_script(tmp_path / "keep"),
        env={"FAKE_IMAGE_EXISTS": "1", "FAKE_HOST_ENV": "1"},
    )
    assert "bulk=keep latency=keep" in calls[-1]


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
