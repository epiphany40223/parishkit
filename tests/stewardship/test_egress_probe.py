"""The backup profile's ``egress-probe`` (#235, contract, "No route out").

Each attempt kind against loopback stand-ins (a listener that answers, a
closed port, a silent UDP port, a fake resolver), the summary's ``closed``
and exit status, and every refusal of its arguments. The real-Docker
evidence (the probe from an internal network) is in
``test_egress_probe_container.py``.
"""

import json
import socket
import struct
import threading
from types import SimpleNamespace

import pytest

from parishkit.stewardship import egress_probe
from parishkit.stewardship.cli import main


def args(**changed):
    """A valid argument namespace for ``execute``."""
    values = {
        "gateway": "172.29.242.65",
        "google_address": "142.250.1.95",
        "listen": "tcp:22,udp:53,tcp:443",
        "engine_version": "27.3.1",
    }
    return SimpleNamespace(**{**values, **changed})


@pytest.fixture
def stubbed(monkeypatch):
    """Every attempt answered from a table instead of the network."""
    calls = []
    answers = {}

    def attempt(kind):
        def answer(address, *rest, **options):
            calls.append((kind, address, *rest))
            return answers.get((kind, address, *rest), "unanswered")

        return answer

    monkeypatch.setattr(egress_probe, "tcp_attempt", attempt("tcp"))
    monkeypatch.setattr(egress_probe, "udp_attempt", attempt("udp"))
    monkeypatch.setattr(egress_probe, "dns_attempt", attempt("dns"))
    return SimpleNamespace(calls=calls, answers=answers)


def test_a_closed_network_answers_nothing_and_exits_zero(stubbed, capsys):
    """Every route checked once; nothing reached; exit 0 with the version."""
    assert egress_probe.execute(args()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["closed"] is True and result["reached"] == []
    assert result["engine_version"] == "27.3.1"
    assert sorted(stubbed.calls) == sorted(
        [
            ("tcp", "1.1.1.1", 443),
            ("tcp", "8.8.8.8", 443),
            ("tcp", "142.250.1.95", 443),
            ("dns", "127.0.0.11"),
            ("dns", "8.8.8.8"),
            ("tcp", "172.29.242.65", 22),
            ("tcp", "172.29.242.65", 443),
            ("udp", "172.29.242.65", 53),
        ]
    )


@pytest.mark.parametrize(
    "reached",
    [
        ("tcp", "8.8.8.8", 443),
        ("tcp", "142.250.1.95", 443),
        ("dns", "127.0.0.11"),
        ("dns", "8.8.8.8"),
        ("tcp", "172.29.242.65", 22),
        ("udp", "172.29.242.65", 53),
    ],
)
def test_any_route_that_answers_fails_the_probe(stubbed, capsys, reached):
    """One reached route anywhere: closed is false, it is named, exit 3."""
    stubbed.answers[reached] = "reached"
    assert egress_probe.execute(args()) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["closed"] is False
    assert len(result["reached"]) == 1 and reached[1] in result["reached"][0]


@pytest.mark.parametrize(
    "changed",
    [
        {"gateway": None},
        {"gateway": "not-an-address"},
        {"gateway": "::1"},
        {"google_address": ""},
        {"google_address": "www.googleapis.com"},
        {"listen": "tcp:0"},
        {"listen": "tcp:70000"},
        {"listen": "sctp:22"},
        {"listen": "tcp:22,"},
        {"listen": ",".join(f"tcp:{port}" for port in range(1, 300))},
        {"engine_version": None},
        {"engine_version": "latest"},
        {"engine_version": "27.3"},
    ],
)
def test_bad_arguments_are_refused_before_any_attempt(stubbed, capsys, changed):
    """A generic refusal (exit 2) and no network attempt at all."""
    assert egress_probe.execute(args(**changed)) == 2
    assert capsys.readouterr().err.startswith("ERROR: egress-probe ")
    assert stubbed.calls == []


def test_no_listeners_is_allowed(stubbed, capsys):
    """A host listening on nothing at the gateway still gets the other checks."""
    assert egress_probe.execute(args(listen="")) == 0
    assert len(stubbed.calls) == 5


def test_the_command_line_dispatches_to_the_probe(monkeypatch, capsys):
    """``pk-stewardship egress-probe`` takes exactly its four options."""
    seen = []
    monkeypatch.setattr(egress_probe, "execute", lambda a: seen.append(a) or 0)
    assert (
        main(
            [
                "egress-probe",
                "--gateway",
                "172.29.242.65",
                "--google-address",
                "142.250.1.95",
                "--listen",
                "tcp:22",
                "--engine-version",
                "27.3.1",
            ]
        )
        == 0
    )
    assert seen[0].listen == "tcp:22"
    with pytest.raises(SystemExit):
        main(["egress-probe", "--config", "c.yaml"])


def test_tcp_attempts_tell_a_listener_from_a_closed_port():
    """A completed handshake is reached; a closed loopback port is refused."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert egress_probe.tcp_attempt("127.0.0.1", port, timeout=2) == "reached"
    assert egress_probe.tcp_attempt("127.0.0.1", port, timeout=2) == "refused"


def test_a_tcp_attempt_that_times_out_is_unanswered(monkeypatch):
    """A dropped SYN (the INPUT rule's effect) is unanswered, not reached."""

    def silent(*a, **k):
        raise TimeoutError

    monkeypatch.setattr(egress_probe.socket, "create_connection", silent)
    assert egress_probe.tcp_attempt("192.0.2.1", 443) == "unanswered"

    def no_route(*a, **k):
        raise OSError(101, "Network is unreachable")

    monkeypatch.setattr(egress_probe.socket, "create_connection", no_route)
    assert egress_probe.tcp_attempt("192.0.2.1", 443) == "no_route"


def udp_server(reply):
    """A loopback UDP socket that answers one datagram with ``reply(data)``."""
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(5)

    def serve():
        try:
            data, peer = server.recvfrom(4096)
        except OSError:
            return
        answer = reply(data)
        if answer is not None:
            server.sendto(answer, peer)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return server, thread


def test_udp_attempts_tell_an_answer_from_silence():
    """Any answer is reached; silence is only unanswered."""
    server, thread = udp_server(lambda data: b"pong")
    with server:
        port = server.getsockname()[1]
        assert egress_probe.udp_attempt("127.0.0.1", port, timeout=2) == "reached"
        thread.join(5)
    server, thread = udp_server(lambda data: None)
    with server:
        port = server.getsockname()[1]
        assert egress_probe.udp_attempt("127.0.0.1", port, timeout=1) == "unanswered"
        thread.join(5)


def dns_reply(rcode, answers, *, ident_delta=0, response=True):
    """A fake resolver's reply to the query it receives."""

    def reply(data):
        ident = struct.unpack(">H", data[:2])[0] + ident_delta
        flags = (0x8000 if response else 0) | 0x0100 | 0x0080 | rcode
        return struct.pack(">HHHHHH", ident & 0xFFFF, flags, 1, answers, 0, 0)

    return reply


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (dns_reply(0, 1), "reached"),
        (dns_reply(2, 0), "refused"),
        (dns_reply(5, 0), "refused"),
        (dns_reply(0, 0), "refused"),
        (dns_reply(0, 1, ident_delta=1), "refused"),
        (dns_reply(0, 1, response=False), "refused"),
        (lambda data: b"short", "refused"),
    ],
)
def test_dns_counts_only_an_answer_with_records(monkeypatch, reply, expected):
    """SERVFAIL, REFUSED, an empty answer or a stray packet is not a route out."""
    server, thread = udp_server(reply)
    with server:
        port = server.getsockname()[1]
        real = socket.socket.connect

        def to_fake(sock, address):
            return real(sock, ("127.0.0.1", port))

        monkeypatch.setattr(socket.socket, "connect", to_fake)
        assert egress_probe.dns_attempt("127.0.0.11", timeout=2) == expected
        thread.join(5)


def test_the_dns_query_asks_for_one_a_record():
    """A recursive A/IN query for the name, with the id it returns."""
    ident, packet = egress_probe.dns_query("www.google.com")
    assert struct.unpack(">HHHHHH", packet[:12]) == (ident, 0x0100, 1, 0, 0, 0)
    assert packet[12:] == b"\x03www\x06google\x03com\x00\x00\x01\x00\x01"
