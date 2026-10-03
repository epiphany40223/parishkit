"""Fixed public-origin resolver, with lifetime bounded by its owning process."""

import json
import socket
import sys
from urllib.parse import urlsplit

from .deployment import LOCAL_PUBLIC_ORIGIN, DeploymentProfile, _origin


def resolve(payload):
    """Validate the closed public input shape before asking the system resolver."""
    if type(payload) is not dict or set(payload) != {"origin", "profile"}:
        raise ValueError("Invalid origin check.")
    profile = DeploymentProfile(payload["profile"])
    canonical = _origin(payload["origin"], profile)
    if profile is DeploymentProfile.LOCAL:
        # The parent does not run the helper for LOCAL; should one ever be
        # asked, the same rule applies here on its own: exactly the one local
        # origin, with no resolver call for a name that is not public.
        return canonical == LOCAL_PUBLIC_ORIGIN
    origin = urlsplit(canonical)
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
