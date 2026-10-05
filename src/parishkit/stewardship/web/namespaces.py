"""The one path rule that separates Family and Admin cookie namespaces.

Dependency-free so session, CSRF and access-gate code can all share it
without importing models. Only cookie transport is shared: Family and Admin
never read each other's session or CSRF cookies, so a login or rotation in
one namespace cannot invalidate the other's open pages.
"""

import re

ADMIN_PREFIX = "/admin/"
# (session cookie, CSRF cookie, cookie path) for each namespace.
FAMILY_COOKIES = ("pk_family", "pk_family_csrf", "/")
ADMIN_COOKIES = ("pk_admin", "pk_admin_csrf", ADMIN_PREFIX)


ADMIN_HOME = ADMIN_PREFIX
# One conservative shape for a same-origin Admin return path: slash-separated
# unreserved segments, never "//", "\\", "%", ":", whitespace or controls, so
# no browser can reinterpret it as another origin or decode a dot segment. The
# segment/slash alternation is unambiguous, so matching stays linear.
_ADMIN_PATH = re.compile(r"/admin/(?:[A-Za-z0-9_~.-]+/)*[A-Za-z0-9_~.-]*")
_QUERY = re.compile(r"[A-Za-z0-9_~.%=&+-]*")
# Authentication endpoints are never a destination: returning to them would
# restart sign-in, sign out, or count a failed OAuth callback; the LOCAL
# test sign-in (#476) is one too (#613).
_NOT_RETURNABLE = ("/admin/login", "/admin/logout", "/admin/oauth/", "/admin/local/")


def admin_return_path(value):
    """Accept only a same-origin Admin page path; anything else means /admin/.

    Used for the sign-in ``next`` field and error-page return links. Absolute
    or scheme-relative URLs, backslashes, control characters, encoded or
    literal dot segments and authentication routes all fall back to /admin/.
    """
    if type(value) is not str or len(value) > 1024:
        return ADMIN_HOME
    path, mark, query = value.partition("?")
    if (
        not _ADMIN_PATH.fullmatch(path)
        or (mark and not _QUERY.fullmatch(query))
        or any(part in {".", ".."} for part in path.split("/"))
        or path.startswith(_NOT_RETURNABLE)
    ):
        return ADMIN_HOME
    return value


def is_admin(request):
    """Whether a request belongs to the Admin namespace."""
    return request.path_info.startswith(ADMIN_PREFIX)


def cookie_namespace(request):
    """Select the namespace by path, identically for session and CSRF state."""
    return ADMIN_COOKIES if is_admin(request) else FAMILY_COOKIES
