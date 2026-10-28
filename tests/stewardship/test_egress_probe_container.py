"""The backup profile's ``egress-probe`` on real Docker (#235, contract).

Operations specification, "Dedicated off-site uploader", **No route out**:
the CI evidence that a container on an internal Docker network alone, as
the backup worker is since the contract release, reaches nothing outside.
It runs in the operational Compose job with the development image.

- From an internal network, the probe's external TCP, its DNS query
  through Docker's embedded resolver and the one straight to a public
  resolver reach nothing, and it prints the Engine version it was given.
- Positive controls on the same network: a TCP listener (in the Google
  address's place and at the gateway's place), a UDP listener that answers
  (reached) and one that never answers (only unanswered, which is why the
  upgrade requires the host's INPUT drop before it trusts UDP).
- On a Linux Docker host, the real gateway: a host listener on the wildcard
  address and a port published by another container, then the INPUT drop
  for the network's subnet, under which neither is reached. Without the
  drop the answers are printed as evidence; the INPUT drop is what the
  upgrade requires on Production either way.
"""

import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PARISHKIT_RUN_RUNTIME_TESTS") != "1",
    reason="Requires explicitly opted-in disposable Docker runtime validation",
)
IMAGE = os.environ.get(
    "PARISHKIT_RUNTIME_TEST_IMAGE", "parishkit-stewardship:development"
)
# A listener for the positive controls: TCP 443 and 8443, a UDP echo on
# 5353 and a UDP port on 5354 that takes datagrams and never answers.
LISTENER = """
import socket, sys, threading

def tcp(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen()
    while True:
        s.accept()[0].close()

def udp(port, echo):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    while True:
        data, peer = s.recvfrom(4096)
        if echo:
            s.sendto(data, peer)

for target, args in ((tcp, (443,)), (tcp, (8443,)), (udp, (5353, True)),
                     (udp, (5354, False))):
    threading.Thread(target=target, args=args, daemon=True).start()
print("ready", flush=True)
threading.Event().wait()
"""


def docker(*arguments, check=True, timeout=120):
    """One Docker command; returns the completed process."""
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, timeout=timeout
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture
def network():
    """A fresh internal network, as the backup worker's own is; removed after."""
    name = f"pk-egress-{uuid4().hex[:12]}"
    docker("network", "create", "--internal", name)
    containers = []
    try:
        yield name, containers
    finally:
        for container in containers:
            docker("rm", "-fv", container, check=False)
        docker("network", "rm", name, check=False)


def inspect(name, template):
    """One ``docker network inspect`` value."""
    return docker("network", "inspect", "--format", template, name).stdout.strip()


def engine():
    """The Docker Engine version, as the upgrade script reads it."""
    return docker("version", "--format", "{{.Server.Version}}").stdout.strip()


def probe(network_name, *, gateway, google, listen=""):
    """Run the probe as the upgrade does: hardened, on that network alone."""
    result = docker(
        "run",
        "--rm",
        "--init",
        "--network",
        network_name,
        "--user",
        "10001:10001",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,mode=1777",
        IMAGE,
        "egress-probe",
        "--gateway",
        gateway,
        "--google-address",
        google,
        "--listen",
        listen,
        "--engine-version",
        engine(),
        check=False,
    )
    assert result.returncode in (0, 3), result.stdout + result.stderr
    answers = json.loads(result.stdout.strip().splitlines()[-1])
    print(json.dumps(answers, sort_keys=True))
    return result.returncode, answers


def answer(result, target):
    """The probe's answer for the one target that starts with ``target``."""
    (found,) = [
        item["answer"]
        for item in result["answers"]
        if item["target"].startswith(target)
    ]
    return found


def google_address():
    """A Google API address, resolved on the Docker host as the upgrade does."""
    try:
        return socket.gethostbyname("www.googleapis.com")
    except OSError:
        # Unresolvable here: any public address still tests the route.
        return "142.250.80.10"


def test_an_internal_network_reaches_nothing_outside(network):
    """External TCP and both DNS routes are closed; the version is recorded."""
    name, _ = network
    gateway = inspect(name, "{{range .IPAM.Config}}{{.Gateway}}{{end}}")
    code, result = probe(name, gateway=gateway, google=google_address())
    assert code == 0 and result["closed"] is True, result
    assert result["engine_version"] == engine()
    assert answer(result, "127.0.0.11:53") != "reached"
    assert answer(result, "8.8.8.8:53") != "reached"
    # The rendered networks have no IPv6, so the container has none either.
    assert answer(result, "IPv6") == "none"


def test_the_probe_sees_a_deliberately_reachable_listener(network):
    """Positive controls on the same network: reached, and silence only unanswered."""
    name, containers = network
    listener = f"pk-egress-listener-{uuid4().hex[:8]}"
    docker(
        "run",
        "-d",
        "--name",
        listener,
        "--network",
        name,
        "--user",
        "0",
        "--entrypoint",
        "python",
        IMAGE,
        "-c",
        LISTENER,
    )
    containers.append(listener)
    for _ in range(60):
        if "ready" in docker("logs", listener).stdout:
            break
        time.sleep(1)
    else:
        pytest.fail("the listener never became ready")
    address = docker(
        "inspect",
        "--format",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        listener,
    ).stdout.strip()
    code, result = probe(
        name,
        gateway=address,
        google=address,
        listen="tcp:8443,udp:5353,udp:5354",
    )
    assert code == 3 and result["closed"] is False
    assert answer(result, f"{address}:443") == "reached"
    assert answer(result, f"{address}:8443") == "reached"
    assert answer(result, f"{address}:5353") == "reached"
    # A UDP service that never answers proves nothing either way.
    assert answer(result, f"{address}:5354") == "unanswered"


@pytest.fixture
def host_listeners():
    """A TCP and a UDP listener on this host's wildcard address."""
    tcp = socket.socket()
    tcp.bind(("0.0.0.0", 0))
    tcp.listen()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("0.0.0.0", 0))
    stop = threading.Event()

    def accept():
        tcp.settimeout(0.5)
        while not stop.is_set():
            try:
                tcp.accept()[0].close()
            except OSError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield tcp.getsockname()[1], udp.getsockname()[1]
    finally:
        stop.set()
        thread.join(5)
        tcp.close()
        udp.close()


def sudo_iptables(*arguments):
    """One iptables call through passwordless sudo (the CI runner's)."""
    return subprocess.run(
        ["sudo", "-n", "iptables", "-w", "5", *arguments],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_the_input_drop_closes_the_gateway_on_a_linux_host(network, host_listeners):
    """The host's listeners and a published port, without and with the drop.

    Only meaningful where this process runs on the Docker host itself (CI's
    Linux runner), with passwordless sudo for iptables.
    """
    if platform.system() != "Linux" or shutil.which("iptables") is None:
        pytest.skip("needs a Linux Docker host with iptables")
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
        pytest.skip("needs passwordless sudo for iptables")
    name, containers = network
    tcp_port, udp_port = host_listeners
    published = f"pk-egress-published-{uuid4().hex[:8]}"
    docker(
        "run",
        "-d",
        "--name",
        published,
        "-p",
        "0.0.0.0::8443",
        "--user",
        "0",
        "--entrypoint",
        "python",
        IMAGE,
        "-c",
        LISTENER,
    )
    containers.append(published)
    port = docker("port", published, "8443/tcp").stdout.splitlines()[0].rsplit(":")[-1]
    gateway = inspect(name, "{{range .IPAM.Config}}{{.Gateway}}{{end}}")
    subnet = inspect(name, "{{range .IPAM.Config}}{{.Subnet}}{{end}}")
    listen = f"tcp:{tcp_port},tcp:{port},udp:{udp_port}"
    # Without the drop: printed as evidence (what an Engine lets through
    # to its own host differs by version and firewall backend).
    _, before = probe(name, gateway=gateway, google="192.0.2.1", listen=listen)
    assert answer(before, f"{gateway}:{udp_port}") != "reached"
    rule = ("INPUT", "-s", subnet, "-j", "DROP")
    added = sudo_iptables("-I", "INPUT", "1", *rule[1:])
    assert added.returncode == 0, added.stderr
    try:
        assert sudo_iptables("-C", *rule).returncode == 0
        code, after = probe(name, gateway=gateway, google="192.0.2.1", listen=listen)
    finally:
        sudo_iptables("-D", *rule)
    assert code == 0 and after["closed"] is True, after
    assert answer(after, f"{gateway}:{tcp_port}") == "unanswered"
