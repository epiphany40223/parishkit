"""Readable pages for typed errors when a person, not a script, made the request.

Views report failures through ``validation_response``, which returns closed
JSON error codes for the progressive-enhancement scripts. A browser that
navigated or submitted an HTML form must never be shown that JSON, so this
middleware renders the same closed messages as an ordinary page instead,
keeping the status code, no-store caching and the safe-error marking. Only
server-owned text is shown: never exception strings or submitted values.
"""

import contextlib
from urllib.parse import urlsplit

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.template.loader import render_to_string
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _

from ..deployment import DeploymentProfile
from .contracts import MESSAGES, ErrorCode, FieldError, validation_response
from .namespaces import ADMIN_HOME, admin_return_path, is_admin
from .refusals import Refusal

# Headers that choose between the JSON and HTML representations.
NEGOTIATION_HEADERS = ("Accept", "Sec-Fetch-Mode", "X-Requested-With")

# One title and next step per closed error code; 404 has its own below.
_GUIDANCE = {
    ErrorCode.INVALID: (
        _("Check your entries"),
        _("Go back, correct the entries, and submit the form again."),
    ),
    ErrorCode.REQUIRED: (
        _("Check your entries"),
        _("Go back, fill in the required entries, and submit the form again."),
    ),
    ErrorCode.STALE: (
        _("This information changed"),
        _(
            "This information changed after you opened the page. Go back and "
            "reload the page to see the current information, then make your "
            "change again."
        ),
    ),
    ErrorCode.UNAVAILABLE: (
        _("Temporarily unavailable"),
        _(
            "Wait a moment, then try again. If this keeps happening, tell your "
            "parish's stewardship administrator."
        ),
    ),
    ErrorCode.DENIED: (
        _("Access unavailable"),
        _(
            "Your session may have ended, or your access may have changed. Sign "
            "in again; if the page is still unavailable, ask a parish "
            "administrator for access."
        ),
    ),
    ErrorCode.GONE: (
        _("Page unavailable"),
        _("This page or action no longer works. Use the menu to find the page."),
    ),
}
_NOT_FOUND = (
    _("Page unavailable"),
    _("This item may have been removed, or the link may be incomplete."),
)

# An address that no page answers (#927): no URL pattern matched, or a view
# raised Http404. Static and closed, so nothing from the address is shown.
_NO_PAGE_TITLE = _("Page not found")
_NO_PAGE = Refusal(
    _("There is no page at this address."),
    fix=_(
        "The link may be incomplete or out of date, or the page may have "
        "moved. Check the address, or use the links below to continue."
    ),
)

# Portal addresses whose untyped 404s become the styled page. Everything else
# (static assets, branding images, hosted files, probes, stray addresses)
# keeps the plain normalized 404 from the security middleware.
_STYLED_PREFIXES = ("/admin/",)


def not_found_response():
    """The typed 404 for an address no page answers; scripts get its JSON."""
    response = validation_response(
        [FieldError(ErrorCode.INVALID)], status=404, refusal=_NO_PAGE
    )
    response.stewardship_no_page = True
    return response


def _untyped_not_found(request, response):
    """Whether Django, not a view's typed error, produced this portal 404.

    That is a URL no pattern matches, or a view that raised ``Http404``
    (including Django's DEBUG 404 page). Any response a view already marked
    safe keeps its own content.
    """
    return (
        response.status_code == 404
        and not getattr(response, "stewardship_safe_error", False)
        and request.path_info.startswith(_STYLED_PREFIXES)
    )


def _restore_review():
    """Whether a restore awaits review: the flag the access gate reads."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    return bool(
        SystemConfiguration.objects.values_list(
            "restore_review_required", flat=True
        ).first()
    )


def _admit_for_chrome(request):
    """Let a signed-in Admin's not-found page show the usual header and menu.

    An unknown address runs no view, so nothing has authenticated the request
    and the chrome (``admin_context.portal_chrome``) would be empty. This is
    the read-only check the old-address redirects use: it renews no idle time,
    rotates nothing and revokes no ended session, and marks the request with
    the principal it finds. (Like every Admin check, it still refuses a
    command-line session's cookie used in a browser, which queues that
    session's revocation.) A signed-out visitor (no session cookie) costs no
    query, and any failure just leaves the minimal layout: presentation must
    never turn a 404 into a different error.

    During a restore review every real Admin page shows the maintenance page
    (or sends an Administrator to it) from the access gate, which never runs
    for an unknown address. So the not-found page then skips the session
    check, too, and keeps the menu-less layout rather than offering a menu
    whose every link is closed. It reads the same flag the gate reads.
    """
    if getattr(request, "principal", None) is not None:
        return
    session = getattr(request, "session", None)
    if session is None or not session.session_key:
        return
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts.authentication import runtime
    from parishkit.stewardship.accounts.limiting import LimiterUnavailable
    from parishkit.stewardship.accounts.sessions import authenticated_admin

    # The failures the access gate and old-address redirects already treat
    # as "no chrome": a missing runtime, a limiter or database outage, or a
    # refused session.
    with contextlib.suppress(
        ConfigError, LimiterUnavailable, DatabaseError, PermissionError
    ):
        if not _restore_review():
            authenticated_admin(request, store=runtime().store, read_only=True)


def page_request(request):
    """Whether a browser navigated or posted a form, rather than a script fetch.

    Browsers label navigations, reloads and HTML form posts with
    ``Sec-Fetch-Mode: navigate``; ``fetch()`` sends ``cors`` or
    ``same-origin``. Without that header (older browsers, tests), only an
    explicit ``text/html`` in Accept marks a page, and ``X-Requested-With``
    always marks script. The supplied scripts send ``Accept: application/json``.
    """
    if request.headers.get("X-Requested-With"):
        return False
    mode = request.headers.get("Sec-Fetch-Mode")
    if mode:
        return mode == "navigate"
    return any(
        media.main_type == "text" and media.sub_type == "html"
        for media in request.accepted_types
    )


def _back_path(request):
    """Offer a return link to a same-origin Admin page the person came from.

    After a form POST that is the form's own page. After a failed page load it
    is the same-origin Referer, when that is a valid Admin page other than the
    failed one. Family pages have only their fixed home link.
    """
    if not is_admin(request):
        return None
    if request.method == "POST":
        candidate = request.get_full_path()
    else:
        referer = urlsplit(request.headers.get("Referer", ""))
        if (referer.scheme, referer.netloc) != (request.scheme, request.get_host()):
            return None
        candidate = referer.path + (f"?{referer.query}" if referer.query else "")
    path = admin_return_path(candidate)
    if path == ADMIN_HOME or (request.method != "POST" and path == request.path):
        return None
    return path


# Set on a request whose response became an error page, so the Admin chrome
# drops the step indicator and placed trail the failed view recorded
# (accounts.admin_navigation): a refusal is not a step of any flow.
ERROR_PAGE_ATTRIBUTE = "_stewardship_error_page"


def _is_local():
    """Whether this process serves the LOCAL laptop environment (#476)."""
    value = getattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE", None)
    return value is not None and DeploymentProfile(value) is DeploymentProfile.LOCAL


def error_page(request, response):
    """Render a typed error response's closed messages as an HTML page."""
    setattr(request, ERROR_PAGE_ATTRIBUTE, True)
    errors = response.stewardship_errors
    admin = is_admin(request)
    code = errors[0].code
    title, guidance = _NOT_FOUND if response.status_code == 404 else _GUIDANCE[code]
    if getattr(response, "stewardship_no_page", False):
        title = _NO_PAGE_TITLE
        if admin:
            _admit_for_chrome(request)
    # A user-facing refusal replaces the closed message with its own reviewed
    # explanation, fix and link (see web.refusals); still never raw text.
    refusal = getattr(response, "stewardship_refusal", None)
    messages = list(dict.fromkeys(str(MESSAGES[e.code]) for e in errors))
    if refusal is not None:
        messages = [str(refusal.message)]
        guidance = refusal.fix or guidance
    reauthenticate = admin and getattr(response, "stewardship_reauthenticate", False)
    if reauthenticate:
        title = _("Confirm it's you")
    context = {
        # Admin errors extend the JavaScript-gated Admin base (#565); Family
        # errors keep the ungated base.
        "admin": admin,
        "title": title,
        "guidance": guidance,
        # Distinct closed messages only; field errors have no field to link here.
        "error_messages": messages,
        "fix_link": refusal.link if refusal else None,
        "fix_label": refusal.link_label if refusal else None,
        "reauthenticate": reauthenticate,
        # Where "Confirm with Google" returns: this page, unless the view named
        # the page its form came from (a POST-only route would answer the
        # returning GET with 405). Either way only a valid Admin path.
        "next": admin_return_path(
            getattr(response, "stewardship_return_path", None)
            or request.get_full_path()
        ),
        # The return page's name, when it is not this page (a POST-only
        # route's form page), so the step-up says where it returns.
        "next_label": getattr(response, "stewardship_return_label", None),
        "submitted": request.method == "POST",
        # The view kept the Admin's choices (server-side) for after the step-up.
        "inputs_kept": getattr(response, "stewardship_inputs_kept", False),
        "sign_in": (
            ("/admin/login" if admin else "/")
            if code == ErrorCode.DENIED and not reauthenticate
            else None
        ),
        "back": _back_path(request),
        "home": ADMIN_HOME if admin else "/",
        "home_label": _("Administration home") if admin else _("Family portal home"),
        # Chrome and step-up forms need this even without request context.
        "csrf_token": get_token(request),
        # So does LOCAL's step-up wording (#619): the outage fallback below
        # renders without context processors, which otherwise set this flag
        # (accounts.branding_context.local_environment).
        "local_environment": _is_local(),
    }
    try:
        content = render_to_string("stewardship/error.html", context, request=request)
    except DatabaseError:
        # Admin chrome reads current configuration. When an outage caused this
        # error, still explain it, without the navigation that needs the data.
        content = render_to_string("stewardship/error.html", context)
    page = HttpResponse(content, status=response.status_code)
    page.cookies = response.cookies
    page.stewardship_safe_error = True
    page["Cache-Control"] = "no-store"
    if response.has_header("Retry-After"):
        page["Retry-After"] = response["Retry-After"]
    patch_vary_headers(page, NEGOTIATION_HEADERS)
    return page


class BrowserErrorMiddleware:
    """Show typed errors to people as pages; scripts keep the JSON contract.

    It sits inside the session and CSRF middleware so a rendered page's CSRF
    token and any session change still reach the browser.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """Replace only responses that carry typed ``stewardship_errors``.

        An untyped portal 404 first becomes the typed not-found response, so
        it is negotiated like every other refusal.
        """
        response = self.get_response(request)
        if _untyped_not_found(request, response):
            response.close()
            response = not_found_response()
        if not getattr(response, "stewardship_errors", None):
            return response
        patch_vary_headers(response, NEGOTIATION_HEADERS)
        if not page_request(request):
            return response
        response.close()
        return error_page(request, response)
