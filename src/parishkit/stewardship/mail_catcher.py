"""The LOCAL mail catcher (#476): its credential document and transport rule.

The local laptop environment sends every message to Mailpit instead of Gmail.
Two things make that safe in both directions, and both are enforced where a
credential is installed and again where it is used:

- the mail-catcher credential is a small JSON document with a distinct type
  marker and no secret, and the SMTP endpoint is the code constant
  ``LOCAL_SMTP_ENDPOINT``, never a document field, so no file can point the
  transport anywhere else;
- ``workspace_transport`` admits that document only for the LOCAL profile,
  so a Production deployment cannot be pointed at a mail catcher, and admits
  a Google service account in LOCAL only beside a mail redirect, on the
  normal clock (``admit_real_workspace``, #1399).

Below both, ``envelope`` is the forced mail redirect (#1399): a LOCAL-only
``mail_redirect`` address replaces every envelope recipient of every SMTP
submission, and LOCAL without one may submit only to the mail catcher. It
is what keeps a real address from being reached when LOCAL uses a real
mailbox; unset, every other profile's envelopes are unchanged.

Helper subprocesses run with ``python -I`` and an empty environment, so they
cannot read the profile themselves: every mail helper request carries an
explicit ``profile`` field (``deployment.recorded_profile`` supplies the
parent's), and the helper applies the same rule to it; a LOCAL parent with
a redirect adds ``mail_redirect`` beside it. Nothing here imports Django at
module level, because the helpers import this module too.
"""

import json
import os
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


def fake_clock(environ=None):
    """Whether this process runs under the LOCAL fake clock (#476).

    libfaketime fakes the clock only when its ``FAKETIME_*`` variables are
    set (deploy/stewardship/Dockerfile.faketime), and only the LOCAL
    fake-clock override sets them (``local.clock.FAKETIME_ENVIRONMENT``), on
    the faked services and the seeder's one-shot containers. Helpers start
    with an empty environment, so they always run on real time.
    """
    environ = os.environ if environ is None else environ
    return any(name.startswith("FAKETIME") for name in environ)


def admit_real_workspace(profile, redirect, environ=None):
    """Refuse a real Google Workspace use that this process may not make (#1399).

    One rule for every path that reaches Google with the Workspace key (Gmail
    submission, the provider and mailbox checks, smoke and the off-site Drive
    copies). In LOCAL it is admitted only with a mail redirect, so a real
    mailbox can never be reached from the laptop without the forced
    redirect, and only on the normal clock: the key's token assertion and
    Google's certificates are checked against this process's clock, which
    the fake clock holds in the past. Every other profile is admitted as
    before, and refuses a redirect as ``envelope`` does. A refusal is a
    ConfigError.
    """
    if not isinstance(profile, DeploymentProfile):
        raise ConfigError("Mail transport selection requires a deployment profile.")
    if profile is not DeploymentProfile.LOCAL:
        if redirect is not None:
            raise ConfigError("A mail redirect is admitted only in the local profile.")
        return
    if redirect is None:
        raise ConfigError(
            "The local profile admits a Google service account only with a mail "
            "redirect."
        )
    if fake_clock(environ):
        raise ConfigError(
            "The local profile admits a Google service account only on the normal "
            "clock."
        )


def workspace_transport(value, profile, *, redirect, environ=None):
    """Select the transport an installed Workspace document may use under ``profile``.

    The mail-catcher document is admitted only in LOCAL. Anything else goes
    to the unchanged Gmail path, whose own strict service-account parser
    (``workspace_info``) refuses a malformed file as it always has, once
    ``admit_real_workspace`` admits a real key here: in LOCAL only beside the
    deployment's mail ``redirect`` (#1399) and on the normal clock. The
    profile must be explicit, and every caller names the redirect (None when
    the deployment sets none), so a caller that does not know them cannot
    send.
    """
    if not isinstance(profile, DeploymentProfile):
        raise ConfigError("Mail transport selection requires a deployment profile.")
    if is_mail_catcher(value):
        if profile is not DeploymentProfile.LOCAL:
            raise ConfigError(
                "The mail-catcher credential is admitted only in the local profile."
            )
        return MailTransport.MAIL_CATCHER
    admit_real_workspace(profile, redirect, environ)
    return MailTransport.GMAIL


def open_mail_catcher(*, smtp_factory=smtplib.SMTP):
    """Open one plain SMTP connection to the mail catcher; the caller EHLOs.

    Only the code constant names the endpoint. The default factory is plain
    ``smtplib.SMTP`` (Mailpit speaks neither TLS nor authentication on this
    port); tests substitute a fake.
    """
    host, port = LOCAL_SMTP_ENDPOINT
    return smtp_factory(host, port, timeout=10)


def redirect_address(value):
    """Validate a ``mail_redirect`` address: one bare mailbox, else ValueError.

    The deployment setting (#1399) is one address, the developer's Testing
    recipient. It is checked with the same bare-mailbox rule every routed
    recipient meets, so it cannot carry a display name, a second address or
    an injected header.
    """
    from .jobs.outbox_validation import mailbox

    return mailbox(value)


def request_redirect(request, profile):
    """The ``mail_redirect`` a helper request names, or None; else ValueError.

    Mail parents add the field only when the deployment sets a redirect, so
    every other request is exactly what it was before (#1399). The field is
    admitted only beside a ``local`` profile: a helper refuses any other
    request that carries it, so no Production send can ever be redirected.
    """
    if "mail_redirect" not in request:
        return None
    if profile is not DeploymentProfile.LOCAL:
        raise ValueError("A mail redirect is admitted only in the local profile.")
    return redirect_address(request["mail_redirect"])


def redirect_field(redirect):
    """The helper request field that carries ``redirect``: empty when unset."""
    return {} if redirect is None else {"mail_redirect": redirect}


def envelope(recipients, *, profile, transport, redirect):
    """The SMTP envelope recipients one submission may use (#1399).

    This is the forced redirect, applied by every SMTP submission in
    Stewardship (``family_delivery._submit``, ``readiness_delivery._submit``
    and ``smoke --send-to``) just before RCPT, whatever the message's routing
    class or campaign mode:

    - with a redirect, every recipient is replaced by the redirect address,
      index for index, so per-recipient outcomes keep their positions. Only
      LOCAL may name one; any other profile is refused;
    - without one, LOCAL may submit only to the mail catcher: any other
      transport is refused before a connection is opened, so a real mailbox
      can never be reached from LOCAL without the redirect;
    - every other profile gets ``recipients`` back unchanged.

    A refusal is a ConfigError, which each caller reports as "not sent".
    """
    if not isinstance(profile, DeploymentProfile) or not isinstance(
        transport, MailTransport
    ):
        raise ConfigError("Mail routing requires a profile and a transport.")
    if redirect is not None:
        if profile is not DeploymentProfile.LOCAL:
            raise ConfigError("A mail redirect is admitted only in the local profile.")
        return (redirect,) * len(recipients)
    local = profile is DeploymentProfile.LOCAL
    if local and transport is not MailTransport.MAIL_CATCHER:
        raise ConfigError(
            "The local profile sends beyond the mail catcher only with a mail redirect."
        )
    return tuple(recipients)


def redirected_message(message, redirect):
    """Show only the redirect address in ``message``'s recipient headers.

    The envelope decides delivery; the headers are rewritten too, so a
    redirected copy neither shows the original addresses nor lets reply-all
    reach them. Without a redirect the message is returned untouched.
    """
    if redirect is not None:
        del message["To"]
        del message["Cc"]
        message["To"] = redirect
    return message
