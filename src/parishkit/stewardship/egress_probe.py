"""The backup profile's ``egress-probe``: proof that it has no route out (#235).

Operations specification, "Dedicated off-site uploader", **No route out**:
since the contract release the backup worker, which reads every tree and all
database data, is on its own internal Docker network alone, and the claim
that it can send nothing out rests on Docker's ``internal`` networks. This
command checks that claim on the real Docker Engine instead of taking it on
faith. It reads no file and connects to nothing but the routes below, each
attempt bounded by ``ATTEMPT_SECONDS``:

- TCP 443 to two fixed public addresses (``PUBLIC_ADDRESSES``) and to a
  Google API address the host resolved and passed in (``--google-address``);
- a DNS query for a public name (``PUBLIC_NAME``) through Docker's embedded
  resolver (``EMBEDDED_RESOLVER``, which ``dockerd`` answers and may forward
  from the host's own network, where no container firewall rule sees it),
  and one sent straight to a public resolver;
- TCP and UDP to the network's gateway (``--gateway``, an address of the
  host itself), on every port the host listens on at a wildcard address or
  at that gateway (``--listen``, which the upgrade script builds from
  ``ss -Htlnu``, always with TCP 80 and 443, Caddy's published ports,
  which ``ss`` does not list when Docker's userland proxy is off).

Every TCP attempt and DNS query must be refused or go unanswered. A DNS
reply counts as reaching the outside only when it carries an answer record:
an error code or an empty reply is the resolver declining. UDP to the
gateway is weaker evidence (a host service can take a datagram and never
answer), so the upgrade script also requires the host's ``INPUT`` drop for
the offline block before it trusts an unanswered UDP port.

Output: one JSON line with every answer, the Docker Engine version the host
passed in (``--engine-version``, recorded with the answers as evidence), and
``closed``; the exit status is 0 when nothing answered and 3 when anything
did. Bad arguments are a generic refusal (2).
"""

import ipaddress
import json
import os
import re
import socket
import struct
import sys
from concurrent.futures import ThreadPoolExecutor

from parishkit.config import ConfigError

# Each connection attempt or query waits at most this long.
ATTEMPT_SECONDS = 5
# Two fixed public addresses that answer TCP 443 from anywhere with a route.
PUBLIC_ADDRESSES = ("1.1.1.1", "8.8.8.8")
# The public resolver a direct DNS query goes to.
PUBLIC_RESOLVER = "8.8.8.8"
# Docker's embedded DNS server inside every user-defined network's container.
EMBEDDED_RESOLVER = "127.0.0.11"
# A public name every working resolver answers with an address.
PUBLIC_NAME = "www.google.com"
# Bounds on what the host may pass, so a probe run stays short.
MAX_LISTENERS = 256
LISTEN = re.compile(r"(tcp|udp):([1-9][0-9]{0,4})")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+~-]{0,40}")
# Answers that mean the route is closed; anything else is "reached".
CLOSED = frozenset({"refused", "unanswered", "no_route"})


def _address(value, what):
    """One IPv4 address from the command line, or a refusal naming ``what``."""
    try:
        return str(ipaddress.IPv4Address(value or ""))
    except ValueError:
        raise ConfigError(f"egress-probe needs {what} as an IPv4 address.") from None


def _listeners(value):
    """The ``--listen`` list: ``tcp:PORT`` and ``udp:PORT``, comma-separated."""
    if not value:
        return []
    items = value.split(",")
    if len(items) > MAX_LISTENERS or not all(
        (match := LISTEN.fullmatch(item)) and int(match.group(2)) <= 65535
        for item in items
    ):
        raise ConfigError("egress-probe --listen takes tcp:PORT and udp:PORT items.")
    return sorted({(item.split(":")[0], int(item.split(":")[1])) for item in items})


def _closed_kind(error):
    """Name an ``OSError`` from a connect or receive by what it means here."""
    if isinstance(error, ConnectionRefusedError):
        return "refused"
    if isinstance(error, TimeoutError):
        return "unanswered"
    # No route, host or network unreachable, and the like: nothing reached.
    return "no_route"


def tcp_attempt(address, port, *, timeout=ATTEMPT_SECONDS):
    """Try one TCP connection; ``reached`` when the handshake completes."""
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return "reached"
    except OSError as error:
        return _closed_kind(error)


def udp_attempt(address, port, payload=b"\0", *, timeout=ATTEMPT_SECONDS):
    """Send one datagram; ``reached`` when anything answers it.

    A connected UDP socket turns the host's ICMP port-unreachable into a
    refused receive; silence is ``unanswered``, which proves nothing alone.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((address, port))
            sock.send(payload)
            sock.recv(4096)
        except OSError as error:
            return _closed_kind(error)
    return "reached"


def dns_query(name):
    """A standard recursive query for ``name``'s A record, with a random id."""
    ident = int.from_bytes(os.urandom(2), "big")
    question = b"".join(
        bytes([len(label)]) + label.encode("ascii") for label in name.split(".")
    )
    return ident, struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0) + (
        question + b"\0" + struct.pack(">HH", 1, 1)
    )


def dns_attempt(resolver, name=PUBLIC_NAME, *, timeout=ATTEMPT_SECONDS):
    """Ask ``resolver`` over UDP; ``reached`` only for an answer with records.

    A reply with an error code (SERVFAIL, REFUSED) or no answer record, as
    Docker's embedded resolver gives a container on internal networks only
    when it does not forward, is ``refused``. A reply that is not an answer
    to this query (wrong id, not a response) is ignored as noise.
    """
    ident, packet = dns_query(name)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((resolver, 53))
            sock.send(packet)
            reply = sock.recv(4096)
        except OSError as error:
            return _closed_kind(error)
    if len(reply) < 12:
        return "refused"
    got, flags, _, answers, _, _ = struct.unpack(">HHHHHH", reply[:12])
    if got != ident or not flags & 0x8000:
        return "refused"
    return "reached" if flags & 0x000F == 0 and answers > 0 else "refused"


def probe(*, gateway, google_address, listeners, engine_version):
    """Run every check at once and return the JSON-able summary.

    The attempts run in parallel, so the whole probe takes about one
    ``ATTEMPT_SECONDS`` however many listeners the host has (a host with
    the ``INPUT`` drop answers none, so each would otherwise wait in turn).
    """
    checks = [
        ("tcp", f"{address}:443", tcp_attempt, (address, 443))
        for address in (*PUBLIC_ADDRESSES, google_address)
    ]
    checks += [
        (
            "dns",
            f"{EMBEDDED_RESOLVER}:53 (embedded)",
            dns_attempt,
            (EMBEDDED_RESOLVER,),
        ),
        ("dns", f"{PUBLIC_RESOLVER}:53 (direct)", dns_attempt, (PUBLIC_RESOLVER,)),
    ]
    checks += [
        (
            kind,
            f"{gateway}:{port} (gateway)",
            tcp_attempt if kind == "tcp" else udp_attempt,
            (gateway, port),
        )
        for kind, port in listeners
    ]
    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = [
            pool.submit(attempt, *arguments) for *_, attempt, arguments in checks
        ]
        answers = [
            {"route": route, "target": target, "answer": future.result()}
            for (route, target, _, _), future in zip(checks, futures, strict=True)
        ]
    reached = [item["target"] for item in answers if item["answer"] not in CLOSED]
    return {
        "engine_version": engine_version,
        "answers": answers,
        "reached": reached,
        "closed": not reached,
    }


def execute(args):
    """Console entry: one JSON line; exit 0 closed, 3 reached, 2 refused."""
    try:
        version = args.engine_version or ""
        if not VERSION.fullmatch(version):
            raise ConfigError("egress-probe needs the Docker Engine version.")
        result = probe(
            gateway=_address(args.gateway, "--gateway"),
            google_address=_address(args.google_address, "--google-address"),
            listeners=_listeners(args.listen),
            engine_version=version,
        )
    except ConfigError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["closed"] else 3
