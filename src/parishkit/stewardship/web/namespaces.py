"""The one path rule that separates Family and Admin cookie namespaces.

Dependency-free so session, CSRF, error and gate code can all share it
without importing models. Only cookie transport is shared: Family and Admin
never read each other's session or CSRF cookies, so a login or rotation in
one namespace cannot invalidate the other's open pages.
"""

ADMIN_PREFIX = "/admin/"
# (session cookie, CSRF cookie, cookie path) for each namespace.
FAMILY_COOKIES = ("pk_family", "pk_family_csrf", "/")
ADMIN_COOKIES = ("pk_admin", "pk_admin_csrf", ADMIN_PREFIX)


def is_admin(request):
    """Whether a request belongs to the Admin namespace."""
    return request.path_info.startswith(ADMIN_PREFIX)


def cookie_namespace(request):
    """Select the namespace by path, identically for session and CSRF state."""
    return ADMIN_COOKIES if is_admin(request) else FAMILY_COOKIES
