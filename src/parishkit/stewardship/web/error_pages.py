"""Readable pages for typed errors when a person, not a script, made the request.

Views report failures through ``validation_response``, which returns closed
JSON error codes for the progressive-enhancement scripts. A browser that
navigated or submitted an HTML form must never be shown that JSON, so this
middleware renders the same closed messages as an ordinary page instead,
keeping the status code, no-store caching and the safe-error marking. Only
server-owned text is shown: never exception strings or submitted values.
"""

import contextlib
import json
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.template.loader import render_to_string
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _

from ..deployment import DeploymentProfile
from .contracts import MESSAGES, ErrorCode, FieldError, validation_response
from .namespaces import ADMIN_COOKIES, ADMIN_HOME, admin_return_path, is_admin
from .refusals import Refusal

# The sign-in page's fixed flag (``?ended=1``) for a page load whose Admin
# session had ended (#2058): it shows a short notice saying so. Never free
# text.
ENDED_FLAG = "ended"

# Set by ``accounts.authentication.denial()`` on its generic Admin denial,
# which the middleware redraws for a signed-in reader (#2165).
GENERIC_DENIAL = "stewardship_generic_denial"

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
    ErrorCode.NOT_AVAILABLE: (
        _("Not available right now"),
        _(
            "The campaign's state or the mode does not allow this now. "
            "Choose another page from the menu."
        ),
    ),
    ErrorCode.GONE: (
        _("Page unavailable"),
        _("This page or action no longer works. Use the menu to find the page."),
    ),
}
# The generic denial a signed-in Admin sees instead (#2165): the sign-in
# wording above fits only a reader without a current session, and signing in
# again cannot help someone already signed in. The fix is the role
# refusal's (accounts.role_refusal.role_denied_for, #1924). Scripts get it as
# their JSON refusal too (#2172), which an in-place region shows beside its
# control; no link, as the page already has the menu.
_SIGNED_IN = Refusal(
    _("Your access doesn't include this page."),
    fix=_(
        "Choose a page from the menu, or ask a parish administrator if you "
        "need this one."
    ),
    sign_in=False,
)
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

# Portal addresses whose untyped 404s become the styled page: the Admin
# portal, and the Family portal in its own layout. Everything else (static
# assets, branding images, hosted files, probes, stray addresses) keeps the
# plain normalized 404 from the security middleware.
_STYLED_PREFIXES = ("/admin/", "/family/")


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
    if getattr(request, "principal", None) is None:
        _read_only_admission(request)


def _read_only_admission(request):
    """The request's current Admin principal by a read-only check, or None.

    ``authenticated_admin(read_only=True)`` renews no idle time, rotates
    nothing and revokes no ended session; when it admits, it marks the
    request with the principal (and session) it finds. A request with no
    session cookie costs no query, and during a restore review nothing is
    checked (see ``_admit_for_chrome``). Any failure is None: presentation
    must never turn a refusal or a 404 into a different error.
    """
    session = getattr(request, "session", None)
    if session is None or not session.session_key:
        return None
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
            return authenticated_admin(request, store=runtime().store, read_only=True)
    return None


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


def _referer_path(request):
    """The same-origin Referer's path and query, or None for another origin."""
    referer = urlsplit(request.headers.get("Referer", ""))
    if (referer.scheme, referer.netloc) != (request.scheme, request.get_host()):
        return None
    return referer.path + (f"?{referer.query}" if referer.query else "")


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
        candidate = _referer_path(request)
        if candidate is None:
            return None
    path = admin_return_path(candidate)
    if path == ADMIN_HOME or (request.method != "POST" and path == request.path):
        return None
    return path


# Set on a request whose response became an error page, so the Admin chrome
# drops the step indicator and placed trail the failed view recorded
# (accounts.admin_navigation): a refusal is not a step of any flow.
ERROR_PAGE_ATTRIBUTE = "_stewardship_error_page"


def _signed_in(request):
    """Whether the view admitted a current Admin session for this request.

    ``AdminCaller.admitted`` marks the request with its principal; a request
    refused before that (no session, an ended one, a CSRF failure) has none.
    Nothing clears it when a later recheck refuses the session, so callers
    use ``_live_principal``, which also rechecks.
    """
    return is_admin(request) and getattr(request, "principal", None) is not None


def _live_principal(request):
    """The admitted Admin session's current principal, by a fresh check, or None.

    The principal the view admitted is no proof the session is still live:
    a later recheck in the same request (Admin home's, a report's
    authorize, a service's) may have refused it as revoked, disabled or
    changed in role. So a denial is drawn as signed in only when a fresh
    read-only check (``_read_only_admission``) still admits the session
    with a role; the request then carries that check's principal, so the
    chrome shows the current roles (#2165, #2172).

    A request refused before admission costs no query. When the check
    refuses, the admitted principal and session are dropped from the
    request, as ``admin_context.greyed_page`` does for a page hidden by
    role, so the chrome (``admin_context.portal_chrome``) draws nothing from the old
    roles.
    """
    if not _signed_in(request):
        return None
    principal = _read_only_admission(request)
    if principal is None or not principal.roles:
        for name in ("principal", "portal_session"):
            request.__dict__.pop(name, None)
        return None
    request.principal = principal
    return principal


def _is_local():
    """Whether this process serves the LOCAL laptop environment (#476)."""
    value = getattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE", None)
    return value is not None and DeploymentProfile(value) is DeploymentProfile.LOCAL


def error_page(request, response, *, live=None):
    """Render a typed error response's closed messages as an HTML page.

    ``live`` says whether the caller's ``_live_principal`` check already
    found the session live, so it is not checked twice; None checks here.
    """
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
    signed_out = admin and refusal is not None and refusal.signed_out
    # A signed-in reader is never told to sign in again (#2165). The check
    # runs even for a signed-out refusal, so that a principal admitted
    # earlier but no longer live leaves no menu behind (#2172).
    if live is None:
        live = code == ErrorCode.DENIED and _live_principal(request) is not None
    signed_in = live and not signed_out
    messages = list(dict.fromkeys(str(MESSAGES[e.code]) for e in errors))
    if signed_in:
        messages = [str(_SIGNED_IN.message)]
        guidance = _SIGNED_IN.fix
    if refusal is not None:
        messages = [str(refusal.message)]
        # The page answering a form's POST shows the full-page fix: Reload
        # here would send the same refused form again (Refusal.page_fix).
        page_fix = refusal.page_fix if request.method == "POST" else None
        guidance = page_fix or refusal.fix or guidance
        title = refusal.title or title
    home = ADMIN_HOME if admin else "/"
    sign_in = ("/admin/login" if admin else "/") if code == ErrorCode.DENIED else None
    if signed_in or (refusal is not None and not refusal.sign_in):
        # A refusal that signing in cannot fix (a role that lacks the page,
        # #1924; any refusal of a signed-in reader, #2165) offers no Sign in
        # again (Refusal.sign_in).
        sign_in = None
    fix_link = refusal.link if refusal else None
    if fix_link == home:
        # The actions row already shows the home link; never show it twice.
        fix_link = None
    if signed_out:
        # Only signing in helps (#2058): one sign-in link that returns here,
        # and no back or home link, which would only be refused again.
        fix_link = sign_in_address(request, ended=False)
    if fix_link is not None and fix_link.partition("?")[0] == sign_in:
        # The refusal's own link is the sign-in; never show it twice.
        sign_in = None
    context = {
        # Admin errors extend the JavaScript-gated Admin base (#565); Family
        # errors keep the ungated base.
        "admin": admin,
        "title": title,
        "guidance": guidance,
        # Distinct closed messages only; field errors have no field to link here.
        "error_messages": messages,
        "fix_link": fix_link,
        "fix_label": refusal.link_label if fix_link else None,
        "sign_in": sign_in,
        "back": None if signed_out else _back_path(request),
        "home": None if signed_out else home,
        "home_label": _("Administration home") if admin else _("Family portal home"),
        # The chrome's forms need this even without request context.
        "csrf_token": get_token(request),
        # So does the chrome's LOCAL wording (#619): the outage fallback below
        # renders without context processors, which otherwise set this flag
        # (accounts.branding_context.local_environment).
        "local_environment": _is_local(),
    }
    if context["local_environment"]:
        # The banner's ParishSoft wording and the Google sign-in flag too
        # (#1399).
        from ..accounts.branding_context import local_banner

        context.update(local_banner())
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


def sign_in_address(request, *, ended):
    """The Admin sign-in address that returns to the page ``request`` came from.

    ``next`` is the page a GET asked for, or the page a form was posted
    from (its same-origin Referer): a POST's own address may answer only
    POST, and signing in must never repeat it. It is kept only when
    ``admin_return_path`` accepts it as a same-origin Admin page other than
    the home page (where sign-in goes anyway). ``ended`` adds the fixed flag
    for the sign-in page's notice (``ENDED_FLAG``), but only when the
    browser sent an Admin session cookie: a visitor who never signed in
    (a new browser, a bookmark) is not told they were signed out.
    """
    ended = ended and ADMIN_COOKIES[0] in request.COOKIES
    query = {ENDED_FLAG: "1"} if ended else {}
    if request.method in {"GET", "HEAD"}:
        candidate = request.get_full_path()
    else:
        candidate = _referer_path(request)
    path = admin_return_path(candidate)
    if path != ADMIN_HOME:
        query["next"] = path
    return "/admin/login" + (f"?{urlencode(query)}" if query else "")


def _ended_session_load(request, response):
    """Whether a browser page load (GET or HEAD) was refused as signed out.

    Only these go to the sign-in page (#2058). A form POST keeps its error
    page, so nothing posted is ever replayed after signing in; scripts and
    in-place regions never reach here (``page_request``) and keep the typed
    refusal for the Admin base's session-ended dialog.
    """
    refusal = getattr(response, "stewardship_refusal", None)
    return (
        refusal is not None
        and refusal.signed_out
        and is_admin(request)
        and request.method in {"GET", "HEAD"}
    )


def sign_in_redirect(request, response):
    """Send an ended session's page load to sign-in, keeping cookie changes."""
    redirect = HttpResponseRedirect(sign_in_address(request, ended=True))
    # A revoked session's cookie deletion must still reach the browser.
    redirect.cookies = response.cookies
    patch_vary_headers(redirect, NEGOTIATION_HEADERS)
    return redirect


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
        redrawn = self._signed_in_denial(request, response)
        if redrawn is not None:
            return redrawn
        if _untyped_not_found(request, response):
            response.close()
            response = not_found_response()
        if not getattr(response, "stewardship_errors", None):
            return response
        patch_vary_headers(response, NEGOTIATION_HEADERS)
        if not page_request(request):
            return _signed_in_script_denial(request, response)
        response.close()
        if _ended_session_load(request, response):
            return sign_in_redirect(request, response)
        return error_page(request, response)

    def _signed_in_denial(self, request, response):
        """Redraw a signed-in reader's generic sign-in denial as its error page.

        ``accounts.authentication.denial()`` answers a plain refusal (a
        report view's ``PermissionError``) with the sign-in page's default
        text, which suits only a reader without a current session. It has
        no request, so it marks the response (``GENERIC_DENIAL``) and this
        draws a browser's page load for a signed-in reader as the typed
        ``denied`` page instead, with the menu and no Sign in link (#2165),
        and a signed-in reader's script as the typed ``denied`` JSON with
        the signed-in wording (#2172). Signed-out readers and a session that
        ended after its admission keep the original response.
        """
        if not getattr(response, GENERIC_DENIAL, False):
            return None
        # Its page and script answers now differ by these headers.
        patch_vary_headers(response, NEGOTIATION_HEADERS)
        # Only a session that passes a fresh read-only check gets the
        # signed-in answer, drawn from its current principal; any other
        # keeps the sign-in page (#2165).
        if _live_principal(request) is None:
            return None
        response.close()
        typed = validation_response(
            [FieldError(ErrorCode.DENIED)], status=response.status_code
        )
        typed.cookies = response.cookies
        if not page_request(request):
            # An in-place region would show denial()'s page whole, with its
            # sign-in text; the typed JSON is shown as a line beside the
            # control instead (#2172).
            patch_vary_headers(typed, NEGOTIATION_HEADERS)
            return _signed_in_json(typed)
        return error_page(request, typed, live=True)


def _signed_in_script_denial(request, response):
    """Give a signed-in reader's ``denied`` JSON the signed-in wording (#2172).

    Scripts and in-place regions read the JSON's ``refusal`` (ui-v1.js's
    in-place refusal line) and closed messages, whose ``denied`` text says to
    sign in again. A refusal of a session that a fresh read-only check still
    admits (``_live_principal``) says instead what the page says. Any other
    response, including a refusal that says the session ended and any
    request refused before admission, is returned unchanged.
    """
    refusal = response.stewardship_refusal
    if (
        response.stewardship_errors[0].code != ErrorCode.DENIED
        or (refusal is not None and refusal.signed_out)
        or _live_principal(request) is None
    ):
        return response
    return _signed_in_json(response)


def _signed_in_json(response):
    """Rewrite a typed ``denied`` JSON response in place for a signed-in reader.

    Each ``denied`` error's closed message becomes the signed-in sentence,
    and a response with no reviewed refusal of its own gains the signed-in
    refusal; one with its own sentence keeps it. Status, headers, cookies
    and the typed markers the refusal log already read stay as they are.
    """
    body = json.loads(response.content)
    for item in body["errors"]:
        if item["code"] == ErrorCode.DENIED:
            item["message"] = str(_SIGNED_IN.message)
    if body.get("refusal") is None:
        body["refusal"] = _SIGNED_IN.as_dict()
    response.content = json.dumps(body)
    return response
