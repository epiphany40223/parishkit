"""LOCAL-only Admin test sign-in through the shared identity core (#476).

The local laptop environment has no Google sign-in, so an operator command
mints a one-time link and this route signs the developer in as an
Administrator or Staff member. It is the sole non-Google authentication
endpoint, and only the LOCAL profile has it: ``local_urls`` is selected by
``configure_web`` for LOCAL alone, the view answers 404 under every other
profile, and the command refuses every other configuration before it
touches Valkey. After the token is verified, ``establish_identity`` does
exactly what it does after a Google callback; this module never issues or
refreshes a session on its own.
"""

import hashlib
import json
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from redis.exceptions import RedisError

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import (
    LOCAL_PUBLIC_ORIGIN,
    DeploymentProfile,
    ServiceRole,
    load_deployment,
)
from parishkit.stewardship.web.security import error_response

from .limiting import LimiterUnavailable
from .policy_schema import normalized_email

# ``.authentication`` and ``.sessions`` load Django models, so they are imported
# inside the view and the command: the command must be importable before it
# has configured Django (#508).

# The route path, and the key namespace the web Valkey ACL already grants.
PATH = "/admin/local/sign-in"
NAMESPACE = "stewardship:auth:v1"
TOKEN_SECONDS = 120
# A local subject can never collide with Google's numeric subjects.
SUBJECT_PREFIX = "local-test:"
# secrets.token_urlsafe(32) is 256 random bits as exactly 43 URL-safe characters.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
# Read and delete in one step. The web ACL grants EVAL but not GETDEL, and is
# not changed. Lua's false for a missing key becomes a nil reply (None).
CONSUME = """
local value = redis.call('GET', KEYS[1])
if value then redis.call('DEL', KEYS[1]) end
return value
"""


def token_key(token, namespace=NAMESPACE):
    """Name the Valkey key by the token's SHA-256, so the store never holds it."""
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    return f"{namespace}:local-sign-in:{digest}"


def issue_token(client, email, epoch, *, namespace=NAMESPACE):
    """Store the email and the current revocation epoch under a fresh token.

    The token is the only secret; the store holds its hash with a 120-second
    expiry. The epoch travels with the token so the identity core can refuse
    a link minted before an operator recovery, as it refuses stale OAuth
    state. ``NX`` makes a hash collision fail loudly instead of overwriting.
    """
    token = secrets.token_urlsafe(32)
    value = json.dumps({"email": email, "recovery_epoch": epoch}, separators=(",", ":"))
    if not client.set(token_key(token, namespace), value, ex=TOKEN_SECONDS, nx=True):
        raise ConfigError("The sign-in token could not be stored.")
    return token


def consume_token(client, token, *, namespace=NAMESPACE):
    """Atomically take the token's record out of Valkey; None when there is none.

    A second use, an expired link and an unknown token all look the same.
    Only a record of exactly the shape ``issue_token`` wrote is returned.
    """
    raw = client.eval(CONSUME, 1, token_key(token, namespace))
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if (
        type(value) is not dict
        or set(value) != {"email", "recovery_epoch"}
        or any(type(item) is not str for item in value.values())
    ):
        return None
    return value


def running_profile():
    """The profile ``configure_web`` recorded, or None in a process without one."""
    value = getattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE", None)
    return None if value is None else DeploymentProfile(value)


@require_http_methods(["GET", "HEAD", "POST"])
def sign_in(request):
    """Serve the token form, or consume a token and sign in through the core.

    Outside LOCAL the view is unreachable (``urls.py`` never routes here) and
    answers 404 even when called directly. A GET never consumes the token, so
    link prefetching cannot use it up; the static script moves the fragment
    into the form, and the developer posts it under CSRF protection. The POST
    re-checks the profile and that ``Host`` is the one LOCAL origin (defence
    in depth behind Caddy's loopback publication), spends the admin bucket
    and counts failures exactly as the Google callback does, consumes the
    token atomically, and hands the identity to ``establish_identity``.
    """
    from .authentication import denial, establish_identity, record_failure, runtime
    from .sessions import database_now

    if running_profile() is not DeploymentProfile.LOCAL:
        return error_response(request, status=404)
    if request.method != "POST":
        return render(request, "stewardship/local-sign-in.html")
    if request.get_host() != urlsplit(LOCAL_PUBLIC_ORIGIN).netloc:
        return denial()
    try:
        limiter = runtime().limiter
        delay = limiter.bucket("admin", request.client_address)
        if delay:
            record_failure(request)
            return denial(status=429, retry=delay)
        token = request.POST.get("token", "")
        stored = None
        if type(token) is str and _TOKEN.fullmatch(token) is not None:
            try:
                stored = consume_token(
                    limiter.client, token, namespace=limiter.namespace
                )
            except RedisError:
                raise LimiterUnavailable(
                    "Authentication is temporarily unavailable."
                ) from None
        if stored is None:
            delay = record_failure(request)
            return denial(status=429 if delay else 403, retry=delay)
        try:
            email = normalized_email(stored["email"])
        except ConfigError:
            delay = record_failure(request)
            return denial(status=429 if delay else 403, retry=delay)
        # Presence was just proved with the link, so freshness is now; the
        # core decides authorization from the login rules, as for Google.
        return establish_identity(
            request,
            SUBJECT_PREFIX + email,
            email,
            None,
            authenticated_at=database_now(),
            recovery_epoch=stored["recovery_epoch"],
            destination=None,
        )
    except (LimiterUnavailable, ConfigError):
        return denial(status=503, retry=5)


def admit_local_configuration(configuration):
    """Refuse every configuration but LOCAL inside web at the localhost origin.

    This runs before Django, the database or Valkey are touched, so a
    production configuration is refused without any side effect.
    """
    if configuration.profile is not DeploymentProfile.LOCAL:
        raise ConfigError("The test sign-in exists only in the LOCAL profile.")
    if urlsplit(configuration.public_origin).hostname != "localhost":
        raise ConfigError("The test sign-in requires the localhost origin.")
    if configuration.service_role is not ServiceRole.WEB:
        raise ConfigError("Run the test sign-in inside the web service.")


def local_sign_in_command(configuration, email):
    """Mint a one-time link for ``email`` and return it.

    The email is normalized here and must still satisfy the deployment's
    login rules when the link is used; the command does not pre-judge them.
    Django is configured only to read the current revocation epoch with the
    web service's own database login; the web Valkey credential stores the
    token under the namespace its ACL already grants.
    """
    from django.db import connections

    from parishkit.stewardship.operator_commands import configure_operator_database
    from parishkit.stewardship.runtime_web import valkey_client

    admit_local_configuration(configuration)
    address = normalized_email(email)
    configure_operator_database(configuration)
    from .sessions import revocation_epoch

    try:
        epoch = revocation_epoch()
        client = valkey_client(configuration)
        try:
            token = issue_token(client, address, epoch)
        finally:
            client.connection_pool.disconnect()
    finally:
        connections.close_all()
    # The token rides in the fragment, which browsers never send to the
    # server, so it reaches no access log or Referer header.
    return f"{LOCAL_PUBLIC_ORIGIN}{PATH}#{token}"


def execute_local_sign_in(args):
    """Console entry: print the one link, or one generic refusal.

    The CLI has already required ``--config`` and ``--email``; every refusal
    here depends on the configuration or the address and stays generic.
    """
    try:
        link = local_sign_in_command(load_deployment(Path(args.config)), args.email)
    except Exception:
        print(
            "ERROR: test sign-in refused; it exists only in the LOCAL profile, "
            "run inside web with web's own configuration and a valid email",
            file=sys.stderr,
        )
        return 2
    print(link)
    return 0
