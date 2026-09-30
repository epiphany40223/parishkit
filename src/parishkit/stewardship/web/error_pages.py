"""Readable pages for typed errors when a person, not a script, made the request.

Views report failures through ``validation_response``, which returns closed
JSON error codes for the progressive-enhancement scripts. A browser that
navigated or submitted an HTML form must never be shown that JSON, so this
middleware renders the same closed messages as an ordinary page instead,
keeping the status code, no-store caching and the safe-error marking. Only
server-owned text is shown: never exception strings or submitted values.
"""

from urllib.parse import urlsplit

from django.db import DatabaseError
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.template.loader import render_to_string
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _

from .contracts import MESSAGES, ErrorCode
from .namespaces import ADMIN_HOME, admin_return_path, is_admin

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
}
_NOT_FOUND = (
    _("Page unavailable"),
    _("This item may have been removed, or the link may be incomplete."),
)


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


def error_page(request, response):
    """Render a typed error response's closed messages as an HTML page."""
    setattr(request, ERROR_PAGE_ATTRIBUTE, True)
    errors = response.stewardship_errors
    admin = is_admin(request)
    code = errors[0].code
    title, guidance = _NOT_FOUND if response.status_code == 404 else _GUIDANCE[code]
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
        "title": title,
        "guidance": guidance,
        # Distinct closed messages only; field errors have no field to link here.
        "error_messages": messages,
        "fix_link": refusal.link if refusal else None,
        "fix_label": refusal.link_label if refusal else None,
        "reauthenticate": reauthenticate,
        "next": admin_return_path(request.get_full_path()),
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
        """Replace only responses that carry typed ``stewardship_errors``."""
        response = self.get_response(request)
        if not getattr(response, "stewardship_errors", None):
            return response
        patch_vary_headers(response, NEGOTIATION_HEADERS)
        if not page_request(request):
            return response
        response.close()
        return error_page(request, response)
