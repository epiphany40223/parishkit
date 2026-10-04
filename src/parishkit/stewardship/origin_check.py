"""Finite DNS/public-origin verification for an explicit go-live preview.

The configured origin is the only input; no browser-supplied hostname or URL is
accepted. DNS runs in a killable helper outside all database transactions. This
checks name resolution and the deployment's URL rules, not external TLS reach,
which remains an operational smoke-test concern.

The LOCAL profile has no public DNS name: its one origin is LOCAL_PUBLIC_ORIGIN
behind a Caddy ``tls internal`` certificate, so the local environment
specification admits exactly that origin without a resolver call. The rule
lives here and in the helper, not in deployment._origin, and every other
profile's verdict is unchanged.
"""

import json
import subprocess
import sys

from .deployment import LOCAL_PUBLIC_ORIGIN, DeploymentProfile, _origin


def check_public_origin(origin, profile):
    """Return a closed verdict within five seconds, without HTTP or credentials.

    subprocess.run owns timeout kill/reap. The child emits only a fixed verdict;
    resolver exceptions and addresses never enter application logs or HTML.
    Caller authorization must precede invocation and be repeated afterward.
    """
    if not isinstance(profile, DeploymentProfile):
        raise ValueError("An explicit deployment profile is required.")
    canonical = _origin(origin, profile)
    if profile is DeploymentProfile.LOCAL:
        # LOCAL is served on localhost by Caddy's own local CA, so there is no
        # public name to resolve and the helper is not run. _origin has already
        # refused every other host, scheme and port for LOCAL; the comparison
        # states the admitted origin on its own terms rather than trusting
        # that. No other profile takes this branch, so a production
        # deployment configured with a localhost origin is still resolved and
        # judged exactly as before.
        return canonical == LOCAL_PUBLIC_ORIGIN
    payload = json.dumps({"origin": canonical, "profile": profile.value}).encode()
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-m", "parishkit.stewardship.origin_check_worker"],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env={},
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout == b"ready\n"
