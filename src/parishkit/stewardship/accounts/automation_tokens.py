"""Session secret and digest formats of the Admin automation command line.

``pk-stewardship admin`` reads its preamble, prints its catalog and maps
errors to exit codes before admission has set up Django, so what it needs
then lives here, free of Django models: the secret and digest formats, the
refusal it reports, and each command's audit event type.
``automation_sessions`` re-exports all of them.
"""

import hashlib
import re

# secrets.token_urlsafe(32): 256 random bits as 43 unpadded base64url characters.
SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


class SessionUnusable(PermissionError):
    """No usable automation session; ``code`` says why for the command line.

    ``session_missing`` (no session has this secret) or ``session_ended``
    (revoked, expired, no longer an Administrator, or a host mismatch).
    """

    def __init__(self, code):
        """Keep the closed error code the command line reports."""
        if code not in {"session_missing", "session_ended"}:
            raise ValueError("Unknown automation refusal.")
        super().__init__("The automation session is not usable.")
        self.code = code


def secret_digest(secret):
    """The lowercase SHA-256 of a well-formed session secret, or ValueError."""
    if type(secret) is not str or SECRET_PATTERN.fullmatch(secret) is None:
        raise ValueError("A session secret is 43 base64url characters.")
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def valid_digest(value):
    """Whether ``value`` is a lowercase hexadecimal SHA-256 digest."""
    return type(value) is str and DIGEST_PATTERN.fullmatch(value) is not None


def command_event_type(command):
    """The audit event type of a command: ``admin_cmd_`` plus its name.

    Lowercase, with hyphens and spaces replaced by ``_``, so ``go-live
    confirm-preview`` becomes ``admin_cmd_go_live_confirm_preview``.
    """
    return "admin_cmd_" + re.sub(r"[-\s]+", "_", command.strip().lower())
