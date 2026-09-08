"""Explicit, fail-closed container entry points for the Phase 0 scaffold."""

import os
import secrets
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from .deployment import DeploymentProfile, ServiceRole


def run_service(
    profile: str | None, role: str | None, *, bind_all_interfaces: bool = False
) -> int:
    """Replace the launcher with the development web process, or refuse startup.

    Workers, migrations, bootstrap, and production must not look operational
    before their database, authorization, and startup interlocks exist. Exec
    preserves signal delivery through Compose's init process. The development
    server reloader watches the read-only source bind mount. Direct host use is
    loopback-only; containers must explicitly opt into listening on every
    interface and separately constrain their published host ports.
    """
    if profile != DeploymentProfile.DEVELOPMENT or role != ServiceRole.WEB:
        print(
            "ERROR: this service is not implemented; startup refused", file=sys.stderr
        )
        return 2
    environment = dict(os.environ)
    environment["DJANGO_SETTINGS_MODULE"] = "parishkit.stewardship.settings.development"
    address = "0.0.0.0:8000" if bind_all_interfaces is True else "127.0.0.1:8000"
    os.execve(
        sys.executable,
        [sys.executable, "-m", "django", "runserver", address],
        environment,
    )
    return 2  # Defensive fallback for mocked/nonconforming exec implementations.


def healthcheck() -> int:
    """Probe only local liveness without proxies, redirects, or response logging."""
    # A redirected response must fail, not probe an arbitrary remote endpoint.
    from urllib.request import HTTPRedirectHandler

    class NoRedirect(HTTPRedirectHandler):
        """Reject redirects from the internal health endpoint."""

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = Request("http://127.0.0.1:8000/health/live")
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            request, timeout=3
        ) as response:
            return 0 if response.status == 200 and response.read(4) == b"ok\n" else 1
    except (URLError, OSError):
        return 1


def prepare_development(root: Path) -> None:
    """Create a new private local scaffold tree with a synthetic DB password.

    This is not application bootstrap: it creates no parish authority, provider
    credentials, or application keys. The parent must already exist, and any
    existing target (including a symlink) is refused without changes. Partial
    failures leave the newly created tree for operator inspection; no cleanup
    traverses durable storage.
    """
    root.mkdir(mode=0o700)
    for relative in (
        "config",
        "credentials",
        "cache",
        "logs",
        "reports",
        "run",
        "run/persistent",
        "run/persistent/postgresql",
        "run/persistent/valkey",
        "run/persistent/caddy",
        "run/persistent/caddy/data",
        "run/persistent/caddy/config",
        "run/persistent/media",
    ):
        (root / relative).mkdir(mode=0o700)
    # PostgreSQL 18 drops privileges after chowning PGDATA (18/docker), not
    # its mount root. Permit traversal there without listing or write access;
    # the enclosing host tree and the actual database stay owner-only.
    (root / "run/persistent/postgresql").chmod(0o711)
    password_path = root / "credentials" / "development-postgres-password"
    descriptor = os.open(password_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(secrets.token_urlsafe(32) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
