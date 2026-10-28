"""Check the off-site uploader's password file against the cluster's verifier.

The uploader login (#235) is the first login an existing deployment gains
with a password, so its file and the cluster's stored SCRAM verifier can
disagree: a same-host restore of a set from before the login brings back a
credentials tree without the file while the role stays in the cluster (roles
are not in a dump), and a file can be lost. ``database-grants`` cannot check
by connecting *as* the login, because the source rules refuse the uploader
from the ``database-provision`` network, so it reads the stored verifier
(``pg_authid.rolpassword``, as the superuser operator) and recomputes it from
the file (operations specification, "Uploader password").

``uploader-password-check`` is the same comparison as a command: the upgrade
script runs it in an isolated container before anything stops, with the
verifier on standard input, and it prints only ``match`` or ``mismatch``. It
is also the capability marker: an image that lists it has the uploader.
"""

import base64
import hashlib
import hmac
import re
import sys

from parishkit.config import ConfigError

UPLOADER_IDENTITY = "backup-uploader"
# A decimal count with no sign or leading zero; bounded so a forged count
# cannot make the check run for minutes (PostgreSQL's own default is 4096).
MAXIMUM_ITERATIONS = 10_000_000
_VERIFIER = re.compile(
    r"SCRAM-SHA-256\$([1-9][0-9]{0,7}):([A-Za-z0-9+/=]+)"
    r"\$([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+)"
)
KEY_BYTES = 32
# A real verifier is about 130 characters; anything much longer is refused
# before it is parsed.
MAXIMUM_VERIFIER = 1024


def _strict_base64(text):
    """Decode canonical, padded base64 only; ``None`` for any other form."""
    try:
        value = base64.b64decode(text, validate=True)
    except ValueError:
        return None
    if base64.b64encode(value).decode("ascii") != text:
        return None
    return value


def parse_verifier(text):
    """Parse ``SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>``.

    Returns ``(iterations, salt, stored_key, server_key)``, or ``None`` for
    anything else: an ``md5`` verifier, a null password, a count outside 1 to
    10,000,000 or not plain decimal, base64 that is not canonical and padded,
    an empty salt, or keys that are not exactly 32 bytes.
    """
    if type(text) is not str or len(text) > MAXIMUM_VERIFIER:
        return None
    match = _VERIFIER.fullmatch(text)
    if match is None:
        return None
    iterations = int(match.group(1))
    if not 1 <= iterations <= MAXIMUM_ITERATIONS:
        return None
    salt, stored, server = (_strict_base64(group) for group in match.groups()[1:])
    if not salt or stored is None or server is None:
        return None
    if len(stored) != KEY_BYTES or len(server) != KEY_BYTES:
        return None
    return iterations, salt, stored, server


def password_matches(password, verifier):
    """True only when ``password`` (bytes) is the one ``verifier`` was made from.

    RFC 5802 with SHA-256, as PostgreSQL computes it: the salted password is
    PBKDF2-HMAC-SHA-256 over the password, then ``StoredKey`` is the SHA-256
    of the HMAC of ``Client Key`` and ``ServerKey`` the HMAC of ``Server
    Key``. Both are compared in constant time, and both must match, so a
    verifier forged to match only one is refused. PostgreSQL applies SASLprep
    to the password first; the runtime passwords are printable ASCII without
    spaces (``database_provisioning._password``), which SASLprep leaves as
    they are.
    """
    parsed = parse_verifier(verifier)
    if parsed is None or type(password) is not bytes or not password:
        return False
    iterations, salt, stored, server = parsed
    salted = hashlib.pbkdf2_hmac("sha256", password, salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    # Both comparisons always run: no early exit tells which key differed.
    stored_ok = hmac.compare_digest(hashlib.sha256(client_key).digest(), stored)
    server_ok = hmac.compare_digest(server_key, server)
    return stored_ok & server_ok


def uploader_password(configuration):
    """The uploader's password from its file, admitted as every password is.

    The path honors ``postgres.password_files``; a missing or unusable file
    refuses (``ConfigError`` or ``CryptographicError``), never "mismatch".
    """
    from .database_provisioning import _password
    from .runtime_paths import RuntimeLayout

    return _password(RuntimeLayout(configuration).database_password(UPLOADER_IDENTITY))


def stored_verifier(cursor, login):
    """The login's stored verifier as the superuser reads it, or ``None``.

    ``None`` means no role of that name; a role whose ``rolpassword`` is null
    answers ``""``, which never parses, so it is a mismatch.
    """
    cursor.execute(
        "SELECT coalesce(rolpassword,'') FROM pg_authid WHERE rolname=%s", [login]
    )
    row = cursor.fetchone()
    return None if row is None else row[0]


def require_matching_password(cursor, login, password):
    """Refuse before any grant when the existing login's verifier differs.

    The refusal names the login and the backup runbook's lost-file
    recovery; it never prints the file or the verifier.
    """
    if not password_matches(password, stored_verifier(cursor, login)):
        raise ConfigError(
            f"The {login} password file does not match the database's stored "
            "password; see the backup runbook's uploader lost-file recovery."
        )


def execute_password_check(args, stdin=None):
    """Console entry: ``match`` (exit 0), ``mismatch`` (exit 1), else exit 2.

    Reads the operator's deployment YAML for the file's path and the stored
    verifier as one line on standard input (bounded). Opens no database and
    no network. A missing or unreadable file, a bad configuration or an
    oversized input is a refusal (exit 2) with a generic message, never an
    answer, so the caller cannot mistake it for either.
    """
    from .deployment import load_deployment

    stream = sys.stdin if stdin is None else stdin
    try:
        if args.config is None:
            raise ConfigError("An explicit operator configuration file is required.")
        password = uploader_password(load_deployment(args.config))
        text = stream.read(MAXIMUM_VERIFIER + 2)
        if len(text) > MAXIMUM_VERIFIER + 1:
            raise ConfigError("The stored verifier is too long.")
        verifier = text.removesuffix("\n")
    except Exception:
        print(
            "ERROR: uploader password check refused; check the operator YAML and "
            "that the uploader's password file exists and is owner-only",
            file=sys.stderr,
        )
        return 2
    if password_matches(password, verifier):
        print("match")
        return 0
    print("mismatch")
    return 1
