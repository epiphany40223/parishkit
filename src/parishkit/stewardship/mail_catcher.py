"""The LOCAL mail catcher (#476): its credential document and transport rule.

The local laptop environment sends every message to Mailpit instead of Gmail.
Two things make that safe in both directions, and both are enforced where a
credential is installed and again where it is used:

- the mail-catcher credential is a small JSON document with a distinct type
  marker and no secret, and the SMTP endpoint is the code constant
  ``LOCAL_SMTP_ENDPOINT``, never a document field, so no file can point the
  transport anywhere else;
- ``workspace_transport`` admits that document only for the LOCAL profile and
  a Google service account only for every other profile, so a LOCAL deployment
  cannot reach Gmail and a Production deployment cannot be pointed at a mail
  catcher.

Helper subprocesses run with ``python -I`` and an empty environment, so they
cannot read the profile themselves: every mail helper request carries an
explicit ``profile`` field (``deployment.recorded_profile`` supplies the
parent's), and the helper applies the same rule to it. Nothing here imports
Django, because the helpers import this module too.
"""

import json
import smtplib
from enum import StrEnum

from parishkit.config import ConfigError

from .accounts.integration_candidates import _object
from .accounts.key_files import MAX_FILE_BYTES
from .deployment import DeploymentProfile

# Plain SMTP to the Mailpit service on the backend network: no TLS, no
# authentication. The host is the Compose service name.
LOCAL_SMTP_ENDPOINT = ("mailpit", 1025)
# The type marker of the mail-catcher credential document. ``up`` installs
# exactly MAIL_CATCHER_DOCUMENT as the LOCAL ``google_workspace`` credential.
MAIL_CATCHER_TYPE = "parishkit-mail-catcher"
MAIL_CATCHER_DOCUMENT = json.dumps({"type": MAIL_CATCHER_TYPE}).encode() + b"\n"


class MailTransport(StrEnum):
    """Which mail transport an installed ``google_workspace`` document selects."""

    GMAIL = "gmail"
    MAIL_CATCHER = "mail_catcher"


def mail_catcher_info(value):
    """Parse the mail-catcher document strictly; anything else is a ConfigError.

    The document is exactly ``{"type": "parishkit-mail-catcher"}``: no
    endpoint, no address and no secret, so it can never redirect mail.
    """
    try:
        if type(value) is not bytes or not 0 < len(value) <= MAX_FILE_BYTES:
            raise ValueError
        info = json.loads(value.decode("utf-8"), object_pairs_hook=_object)
        if info != {"type": MAIL_CATCHER_TYPE}:
            raise ValueError
        return info
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ConfigError("Mail-catcher credential information is invalid.") from None


def is_mail_catcher(value):
    """True when ``value`` is the mail-catcher document, without raising."""
    try:
        mail_catcher_info(value)
    except ConfigError:
        return False
    return True


def workspace_transport(value, profile):
    """Select the transport an installed Workspace document may use under ``profile``.

    This is the two-way refusal. LOCAL admits only the mail-catcher document
    and refuses anything else, so a Google service account can never reach
    Gmail from LOCAL. Every other profile refuses the mail-catcher document
    and hands anything else to the unchanged Gmail path, whose own strict
    service-account parser (``workspace_info``) refuses a malformed file as it
    always has. The profile must be explicit: a caller that does not know its
    profile cannot send.
    """
    if not isinstance(profile, DeploymentProfile):
        raise ConfigError("Mail transport selection requires a deployment profile.")
    if is_mail_catcher(value):
        if profile is not DeploymentProfile.LOCAL:
            raise ConfigError(
                "The mail-catcher credential is admitted only in the local profile."
            )
        return MailTransport.MAIL_CATCHER
    if profile is DeploymentProfile.LOCAL:
        raise ConfigError(
            "The local profile admits only the mail-catcher credential, never a "
            "Google service account."
        )
    return MailTransport.GMAIL


def open_mail_catcher(*, smtp_factory=smtplib.SMTP):
    """Open one plain SMTP connection to the mail catcher; the caller EHLOs.

    Only the code constant names the endpoint. The default factory is plain
    ``smtplib.SMTP`` (Mailpit speaks neither TLS nor authentication on this
    port); tests substitute a fake.
    """
    host, port = LOCAL_SMTP_ENDPOINT
    return smtp_factory(host, port, timeout=10)
