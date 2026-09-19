"""Fixed public-origin resolver, with lifetime bounded by its owning process."""

import json
import socket
import sys
from urllib.parse import urlsplit

from .deployment import DeploymentProfile, _origin


def resolve(payload):
    """Validate the closed public input shape before asking the system resolver."""
    if type(payload) is not dict or set(payload) != {"origin", "profile"}:
        raise ValueError("Invalid origin check.")
    profile = DeploymentProfile(payload["profile"])
    origin = urlsplit(_origin(payload["origin"], profile))
    return bool(
        socket.getaddrinfo(
            origin.hostname,
            origin.port or (443 if origin.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    )


def main():
    """Consume one bounded request and never print resolver errors or addresses."""
    try:
        payload = sys.stdin.buffer.read(4097)
        result = len(payload) <= 4096 and resolve(json.loads(payload))
    except Exception:
        result = False
    sys.stdout.buffer.write(b"ready\n" if result else b"unavailable\n")


if __name__ == "__main__":
    main()
