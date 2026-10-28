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

from parishkit.stewardship.audit.refusal_context import (
    IDENTIFIER,
    REASONS,
    SETTING_REASONS,
)
from parishkit.stewardship.storage import StaleRecordError

from .contracts import ErrorCode, FieldError, validation_response


def setting_values(reason, expected, actual):
    """``(expected, actual)`` as strings, refused unless ``reason`` may carry them.

    Only a reason in ``SETTING_REASONS`` records the setting values it was
    refused for (a parish zone, a size in bytes): the database checks only
    their shape, which a submitted secret can also fit, so the closed list
    in Python is the gate (#1403). The refusal log still drops a value of
    the wrong shape.
    """
    if expected is None and actual is None:
        return None, None
    if reason not in SETTING_REASONS:
        raise TypeError("Only a setting reason records expected and actual values.")
    return (
        None if expected is None else str(expected),
        None if actual is None else str(actual),
    )


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
    # Whether the browser error page offers Sign in again for a 403. False
    # for a refusal that signing in cannot fix, such as a role that lacks
    # the page (#1924).
    sign_in: bool = True
    # Whether the refusal says the Admin session has ended (#2058). The
    # browser-error middleware sends such a page load to the sign-in page
    # instead of showing an error page.
    signed_out: bool = False

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
    # Copied to ``Refusal.sign_in``: a subclass whose refusal signing in
    # cannot fix sets it False.
    offers_sign_in = True
    # Copied to ``Refusal.signed_out``: True only for ``UserFacingSignedOut``.
    ends_session = False
    # What the refusal log adds (#1403): the number of submitted field names
    # the form does not have, and its own fields sent more than once
    # (``unexpected_fields``); never shown.
    unexpected_count = None
    duplicates = ()

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
        expected=None,
        actual=None,
    ):
        """Validate the fix link, reason and values; keep the reviewed text.

        ``expected`` and ``actual`` are the setting values the refusal log
        records, allowed only with a reason in ``SETTING_REASONS``
        (``setting_values``).
        """
        if link is not None and (
            type(link) is not str or not link.startswith("/") or link.startswith("//")
        ):
            raise TypeError("A refusal link must be a same-origin path.")
        if (link is None) != (link_label is None):
            raise TypeError("A refusal link needs a label, and a label a link.")
        reason = reason or self.default_reason
        if reason is not None and reason not in REASONS:
            raise TypeError("A refusal reason must be a closed reason code.")
        self.expected, self.actual = setting_values(reason, expected, actual)
        super().__init__(str(message))
        self.refusal = Refusal(
            message,
            fix,
            link,
            link_label,
            title,
            page_fix,
            reason,
            self.offers_sign_in,
            self.ends_session,
        )

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


class UserFacingSignedOut(UserFacingDenied):
    """The Admin session has ended, so only signing in again helps (#2058).

    Still a 403 ``UserFacingDenied`` for scripts and in-place regions; a
    browser's page load is sent to the sign-in page instead (see
    ``web.error_pages``).
    """

    ends_session = True


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


def unexpected_fields(*sources, allowed=None, multiple=()):
    """The standard refusal for a form posted with fields it does not have.

    With the submitted ``sources`` (``request.POST``, ``request.FILES``) and
    the form's ``allowed`` names, the refusal log also records how many
    submitted names are not allowed (``unexpected_count``; those names are
    the client's and are never kept) and which allowed names, other than
    the ``multiple`` ones and the CSRF token, were sent more than once
    (named, with the code ``duplicate``). Without them it records neither.

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
    error = UserFacingError(
        _(
            "Nothing was done: this page sent something the server did not "
            "expect, which usually means the page changed after you opened it."
        ),
        fix=_("Reload the page, then try again."),
        page_fix=_("Open the page again, then try again."),
        title=_("This page is out of date"),
        reason="unexpected_fields",
    )
    if allowed is not None:
        allowed = frozenset(allowed)
        submitted = {name for source in sources for name in source}
        error.unexpected_count = len(submitted - allowed)
        # Only the server's own names are looked up and named.
        error.duplicates = tuple(
            name
            for name in sorted(allowed - {*multiple, "csrfmiddlewaretoken"})
            if any(len(source.getlist(name)) > 1 for source in sources)
            and IDENTIFIER.fullmatch(name)
        )
    return error


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


def send_time_passed(link=None):
    """The refusal when a reviewed send time passed before the confirmation.

    A signed schedule review lives up to ``PREVIEW_MAX_AGE``, so a send time
    that was ahead at Review can pass before Apply, and that email would
    then send at once (#1464). Nothing is changed; ``link``, as for
    ``expired_preview``, leads back to a fresh review.
    """
    return UserFacingStale(
        _(
            "A send time in this change has passed since it was reviewed, so "
            "that email would send at once."
        ),
        fix=_("Nothing was changed. Choose a later time, then review the changes."),
        link=link,
        link_label=_("Review the changes again") if link else None,
        reason="send_time_passed",
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
    and an ended session) carries its own; a tampered or foreign
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
    if isinstance(error, UserFacing):
        # The closed details a shared refusal adds (never its text).
        response.stewardship_expected = error.expected
        response.stewardship_actual = error.actual
        response.stewardship_unexpected_count = error.unexpected_count
        response.stewardship_duplicates = error.duplicates
    return response


def with_reason(response, reason, *, expected=None, actual=None):
    """Give a refused response its closed reason, and setting values if any.

    For a refusal no exception carries: a form drawn again whose cause has
    a reason of its own (a campaign zone that is not the parish's, an image
    that is too large). ``expected`` and ``actual`` follow
    ``setting_values``. Returns ``response``.
    """
    if reason not in REASONS:
        raise TypeError("A refusal reason must be a closed reason code.")
    response.stewardship_reason = reason
    response.stewardship_expected, response.stewardship_actual = setting_values(
        reason, expected, actual
    )
    return response


# The closed code a refusal status stands for, for a body of its own that
# says no more (the ``ErrorCode`` the typed answer would carry). 404 and 422
# have none: ``ErrorCode`` has no not-found or unprocessable member, and the
# typed 404 answer uses ``unavailable``, which would say an outage. Such a
# page records its status and any flag codes it passes.
STATUS_CODES = {
    400: ErrorCode.INVALID.value,
    403: ErrorCode.DENIED.value,
    409: ErrorCode.STALE.value,
    410: ErrorCode.GONE.value,
}


def with_status_code(response, *codes):
    """``with_codes`` with the code of the response's status first, if any.

    For a report's own error page, whose flags (a reused form, a missing
    browser zone) give the further ``codes``. Returns ``response``.
    """
    status = STATUS_CODES.get(response.status_code)
    return with_codes(response, *([status] if status else []), *codes)


# Where ``with_default_code`` keeps the codes the refusal log records only
# when nothing else gives one (#1444).
DEFAULT_CODES = "stewardship_default_codes"


def with_default_code(response, *codes):
    """The status's code, then ``codes``, recorded only if nothing else is.

    For a page drawn again with a refusal of its own, which may or may not
    hold a refused form. When the form has errors, or the answer has typed
    errors or codes of its own, those are recorded and these are not.
    Otherwise these are: the code the status stands for (``invalid`` for
    400, ``stale_version`` for 409 and so on, none for 404 or 422), which
    restates the status, then any further ``codes`` the page names (such as
    ``in_use``). Returns ``response``.
    """
    status = STATUS_CODES.get(response.status_code)
    setattr(response, DEFAULT_CODES, _closed((*([status] if status else []), *codes)))
    return response


def _closed(codes):
    """``codes`` as a tuple, refusing any that is not a closed identifier."""
    if not all(type(code) is str and IDENTIFIER.fullmatch(code) for code in codes):
        raise TypeError("A refusal code must be a closed identifier.")
    return tuple(codes)


def with_codes(response, *codes):
    """Give a refused response with a body of its own its closed error codes.

    For the pages that answer a refusal without ``validation_response``'s
    typed errors (the report error pages, a page script's JSON answer), so
    the refusal log can say what was refused. Returns ``response``.
    """
    response.stewardship_codes = _closed(codes)
    return response


# The marker on a 200 answer that is a refusal drawn in place: the notice
# an action's region shows instead of its result (#1403).
IN_PLACE = "stewardship_refused_in_place"

# The reason each refused-step key of an in-place region means (Restore
# review, Held emails, System health): the region words the key for people,
# the refusal log records the reason with the key as its code.
STEP_REASONS = {
    "signed_out": "denied",
    "changed": "stale_page",
    "busy": "stale_page",
    "denied": "denied",
    "production": "production_only",
    "blocked": "release_refused",
    "needs_holds": "release_refused",
    "superseded": "resend_refused",
    "held_elsewhere": "resend_refused",
    # The Go live page's Start go-live (#1585): another Administrator's
    # Start applied first, a stop is still running or failed, or the
    # campaign closed since the check; or the public web address check run
    # again at Apply failed, which is the deployment's state, not the input.
    "started": "stale_page",
    "stopping": "stale_page",
    "stop_failed": "stale_page",
    "closed": "stale_page",
    "origin_unverified": "configuration",
    # Its Stop go-live (#1585 slice 3): go-live was stopped, or Production
    # confirmed, since the step was drawn.
    "stopped": "stale_page",
    "confirmed": "stale_page",
    # Its Confirm Production (#1585 slice 4): the target, the hold, the
    # Family links or readiness changed since the step was drawn. The typed
    # word missing (``typed``) is an incomplete form the page gates.
    "target": "stale_page",
    "hold_ended": "stale_page",
    "moved": "stale_page",
    "problems": "stale_page",
    # The same inputs, but emails fell due since the Review, so the counts
    # it showed are no longer the ones Apply would confirm.
    "due": "stale_page",
    "typed": "unexpected_fields",
    # Production activation's Cancel go-live (#1585 slice 5): the reason
    # missing is an incomplete form the dialog gates; mail being sent (or
    # uncertain, or needing recovery) blocks it until resolved; live
    # delivery was paused, or another Administrator cancelled, since the
    # page was drawn. A start that passed is "started" and a changed count
    # "changed", as above.
    "no_reason": "unexpected_fields",
    "blocking": "send_work_blocks",
    "paused": "stale_page",
    "cancelled": "stale_page",
    # Sent mail's bulk actions (#2296): nothing selected qualifies any more,
    # or Select all N matching's emails mix both kinds, since the list was
    # drawn ("changed" is above).
    "empty": "stale_page",
    "mixed": "stale_page",
    # A hand-built selection the page never posts (a malformed id, more
    # than a page of ids).
    "selection": "unexpected_fields",
}


def refused_step(response, refused, error=None):
    """Give an in-place region's refused step its reason; returns ``response``.

    ``refused`` is the step's refused key, which becomes its code. ``error``
    is the refusal's exception when the key alone does not say why (a stale
    form's unexpected fields), whose own reason is then used. A region that
    answers a refusal with 200 is marked as drawn in place.
    """
    if error is not None:
        annotate(response, error)
    else:
        with_reason(response, STEP_REASONS.get(refused, "unclassified"))
    if type(refused) is str and IDENTIFIER.fullmatch(refused):
        with_codes(response, refused)
    if response.status_code == 200:
        setattr(response, IN_PLACE, True)
    return response


def refused_in_place(response, reason):
    """Mark a 200 answer as a refusal with ``reason``; returns ``response``.

    The refusal log records it as it does a 4xx refusal, without a status.
    """
    with_reason(response, reason)
    setattr(response, IN_PLACE, True)
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
