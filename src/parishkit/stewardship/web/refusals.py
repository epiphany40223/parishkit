"""User-facing refusals: say what was wrong and how to fix it, safely.

Most refusals stay closed: ``error_response`` maps an exception to a generic,
static message and never shows exception text, because that text may echo
submitted values or internal detail. A refusal an Admin can actually trigger
and act on (a template that a schedule still uses, a page changed in another
tab, a missing earlier setup step) can instead raise one of the types below.
Each carries a reviewed, static explanation, an optional "how to fix it"
sentence and an optional link to the page that fixes it. The browser error
page, the setup "not available" page and the JSON body for scripts show these
fields; the HTTP status stays the one the base exception type already maps to.

Never build a refusal message from submitted values or another exception's
text: pass only translated literals (or a fixed choice among them) and
server-owned paths from ``reverse``.
"""

from dataclasses import dataclass

from django.core import signing
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.audit.refusal_context import REASONS
from parishkit.stewardship.storage import StaleRecordError

from .contracts import ErrorCode, FieldError, validation_response


@dataclass(frozen=True)
class Refusal:
    """The static, reviewed text of one refusal and its optional fix link.

    ``title`` replaces the browser error page's heading, which otherwise
    follows the status (a 409 reads "This information changed"), for a
    refusal whose status does not describe it, such as a page refused
    because of the deployment's state rather than an out-of-date form.
    Scripts have no heading, so it is not part of :meth:`as_dict`.

    ``page_fix`` replaces ``fix`` on the browser error page that answers a
    form's POST, for a fix that would not work there: on that page the
    browser's Reload sends the same refused form again, while a dialog or
    in-place region (which reads ``fix`` from the JSON) is still on the
    page it can reload.
    """

    message: str
    fix: str | None = None
    link: str | None = None
    link_label: str | None = None
    title: str | None = None
    page_fix: str | None = None
    # The closed reason code the refusal log records (#1403;
    # ``audit.refusal_context.REASONS``); never shown.
    reason: str | None = None

    def as_dict(self):
        """JSON-safe fields for scripts; the link is a same-origin path."""
        return {
            "message": str(self.message),
            "fix": str(self.fix) if self.fix else None,
            "link": (
                {"url": self.link, "label": str(self.link_label)} if self.link else None
            ),
        }


class TypedRefusal:
    """Mixin for an exception that carries its own closed ``reason`` code.

    Only these are read for a reason (``reason_of``): an arbitrary
    exception's own ``reason`` attribute (a network error's, say) is never
    trusted as one (#1403).
    """

    reason = None


class UserFacing(TypedRefusal):
    """Mixin giving an exception a safe ``refusal`` to show the Admin.

    ``link`` must be a server-owned same-origin path (for example from
    ``reverse``); ``link_label`` names where it goes. ``title`` is the page's
    own heading, when the closed category's would mislead; ``page_fix`` the
    fix a POST's error page shows instead of ``fix`` (see ``Refusal``).
    ``reason`` is the closed reason code the refusal log records (#1403),
    the class's ``default_reason`` when not given; a new kind of refusal adds
    its code to ``audit.refusal_context.REASONS``.
    """

    default_reason = None

    def __init__(
        self,
        message,
        *,
        fix=None,
        link=None,
        link_label=None,
        title=None,
        page_fix=None,
        reason=None,
    ):
        """Validate the fix link and reason, and keep the reviewed text."""
        if link is not None and (
            type(link) is not str or not link.startswith("/") or link.startswith("//")
        ):
            raise TypeError("A refusal link must be a same-origin path.")
        if (link is None) != (link_label is None):
            raise TypeError("A refusal link needs a label, and a label a link.")
        reason = reason or self.default_reason
        if reason is not None and reason not in REASONS:
            raise TypeError("A refusal reason must be a closed reason code.")
        super().__init__(str(message))
        self.refusal = Refusal(message, fix, link, link_label, title, page_fix, reason)

    @property
    def reason(self):
        """The refusal's closed reason code, or None (see ``reason_of``)."""
        return self.refusal.reason


def refusal_text(error, *, page=False):
    """A user-facing refusal's explanation and fix as one line, else ``None``.

    For pages and regions that show their own fixed text and only a
    ``web.refusals`` type's static, reviewed text instead. ``page`` picks
    the full-page fix (``Refusal.page_fix``) when there is one.
    """
    refusal = getattr(error, "refusal", None)
    if refusal is None:
        return None
    fix = (refusal.page_fix if page else None) or refusal.fix
    return " ".join(str(part) for part in (refusal.message, fix) if part)


class UserFacingError(UserFacing, ValueError):
    """An invalid request the Admin can correct (HTTP 400)."""


class UserFacingStale(UserFacing, StaleRecordError):
    """The page is out of date and must be reloaded (HTTP 409)."""


class UserFacingMissing(UserFacing, LookupError):
    """A prerequisite is missing, usually an earlier step (HTTP 404)."""


class UserFacingDenied(UserFacing, PermissionError):
    """The action is not allowed in the current state (HTTP 403)."""

    default_reason = "denied"


class AccessDenied(TypedRefusal, PermissionError):
    """A shared access check's refusal: no caller, or no capability (HTTP 403).

    Raised by the Admin caller's own checks and ``admin_editing.principal``
    in place of a bare ``PermissionError``, which it still is, so that the
    refusal log records the fixed reason ``denied`` rather than
    ``unclassified`` (#1403).
    """

    reason = "denied"


class UserFacingGone(UserFacing, PermissionError):
    """A retired action or another campaign's address; it will not work again.

    HTTP 410 Gone (navigation rule 10): a multi-campaign action until the
    single-campaign change (#145) removes it, or a report naming a campaign
    that is not the current one. It is a ``PermissionError`` so any handler
    that does not know it still refuses (403); ``gone_response`` and the
    handlers that call it answer with the 410 page and its explanation,
    never a sign-in prompt. The refusal log records it as ``gone``, at INFO.
    """

    default_reason = "gone"


class UserFacingUnavailable(UserFacing, StaleRecordError):
    """A page its greyed-out menu entry says is unavailable right now (#1128).

    HTTP 409 Conflict (the campaign's state or the mode does not allow it
    now), with the closed ``not_available`` code rather than
    ``stale_version`` (nothing changed, and reloading will not help) or
    ``unavailable`` (an outage, retried later). It is a ``StaleRecordError``
    so a handler that does not know it still answers 409 with its
    explanation; ``unavailable_response`` and the handlers that call it use
    the ``not_available`` code.
    """


def unavailable_response(error):
    """The 409 response for a ``UserFacingUnavailable`` refusal."""
    return validation_response(
        [FieldError(ErrorCode.NOT_AVAILABLE)], status=409, refusal=error.refusal
    )


def gone_response(error):
    """The 410 response for a ``UserFacingGone`` refusal."""
    return validation_response(
        [FieldError(ErrorCode.GONE)], status=410, refusal=error.refusal
    )


def stale_page():
    """The standard refusal when a form's version is older than the saved data."""
    return UserFacingStale(
        _("This page changed in another tab or session."),
        fix=_(
            "Reload the page to see the latest version, then make your change again."
        ),
        reason="stale_page",
    )


def unexpected_fields():
    """The standard refusal for a form posted with fields it does not have.

    A field the form does not have, a field sent twice, or a hidden or
    always-sent field left out (the Admin portal requires JavaScript, so a
    required checkbox is always present too) almost always means a page left
    open while it changed (a release changed the form), a stale, cached copy
    of it, or a hand-made post: nothing the person typed is wrong (#1401).
    So the page is titled for that, rather than the ``invalid`` category's
    "Check your entries", and the wording says that nothing was done. A
    dialog or in-place region is told to reload; the full error page that
    answers a form's POST, where Reload would send the same form again, is
    told to open the page again, and its "Return to the previous page" link
    does that. The status (400) and the JSON code stay ``invalid``.
    """
    return UserFacingError(
        _(
            "Nothing was done: this page sent something the server did not "
            "expect, which usually means the page changed after you opened it."
        ),
        fix=_("Reload the page, then try again."),
        page_fix=_("Open the page again, then try again."),
        title=_("This page is out of date"),
        reason="unexpected_fields",
    )


# Signed previews stay valid for fifteen minutes. The limit is enforced here;
# pages no longer announce it, and an expired preview gets a plain refusal.
PREVIEW_MAX_AGE = 900
# The go-live, Production, withdrawal and delivery controls bind exact
# irreversible intent and keep a shorter five-minute lifetime.
CONTROL_PREVIEW_MAX_AGE = 300


def expired_preview(link=None):
    """The standard refusal when a reviewed preview is too old or out of date.

    ``link`` returns the Admin to the page that builds the preview, so one click
    starts a fresh review; nothing is saved by following it.
    """
    return UserFacingStale(
        _("This preview is out of date."),
        fix=_("Review the changes again, then confirm."),
        link=link,
        link_label=_("Review the changes again") if link else None,
        reason="expired_preview",
    )


def load_preview(token, *, salt, link=None, max_age=None):
    """Verify a signed preview, turning expiry into the plain stale refusal.

    ``max_age`` is the preview's lifetime in seconds, ``PREVIEW_MAX_AGE`` when
    not given (read at call time, so tests can shorten it). Tampered or
    foreign tokens still raise ``BadSignature`` (a generic 400); only a
    genuine, expired signature becomes the recoverable refusal, which the
    command line reports as ``stale_version``.
    """
    try:
        return signing.loads(
            token,
            salt=salt,
            max_age=PREVIEW_MAX_AGE if max_age is None else max_age,
        )
    except signing.SignatureExpired:
        raise expired_preview(link) from None


def reason_of(error):
    """The closed reason code an exception carries for the refusal log, or None.

    Only a ``TypedRefusal`` (the ``web.refusals`` types, ``AccessDenied``
    and a missing fresh sign-in) carries its own; a tampered or foreign
    signed value is ``bad_signature``. Anything else has none, and the log
    records it as ``unclassified`` with its class name (#1403).
    """
    if isinstance(error, TypedRefusal) and error.reason in REASONS:
        return error.reason
    if isinstance(error, signing.BadSignature):
        return "bad_signature"
    return None


def annotate(response, error):
    """Carry ``error``'s reason and class on its refused response.

    The refusal log middleware (``web.refusal_log``) reads both: the reason
    names the refusal, and the class gives only its name and level. The
    exception itself, and so its text, is not kept. Returns ``response``.
    """
    response.stewardship_reason = reason_of(error)
    response.stewardship_exception_class = type(error)
    return response


# The marker on a request or response whose refusal its own code already
# records (a refused sign-in, a refused automation session), so the refusal
# log adds no second record (#1403).
RECORDED = "stewardship_refusal_recorded"


def mark_recorded(target):
    """Mark a request or response as already recorded; returns ``target``."""
    setattr(target, RECORDED, True)
    return target


# The marker on a response that refuses no input: a page its menu greys out
# right now, opened directly (ui-conventions, "Unavailable menu pages").
NOT_INPUT = "stewardship_refusal_not_input"


def mark_not_input(response):
    """Mark a response as no refusal of input; returns ``response``."""
    setattr(response, NOT_INPUT, True)
    return response


def return_link(request):
    """The page a web form was posted from, for a stale refusal's link back.

    Every page using it answers GET and POST on one path, so the path shows
    the page again. A command-line caller (or a request with an empty path)
    has no page and gets ``None``.
    """
    return getattr(request, "path", None) or None
