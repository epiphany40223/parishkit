"""Family and sign-in addresses never move (ADM-12, the Admin URL scheme).

Families reach the portal from emailed codes and links, and Google returns
Administrators to the registered OAuth callback. The Admin URL work moves
only ``/admin/…`` pages, so these route strings and names are frozen: any
change here fails CI and needs a deliberate, redirect-backed decision,
never an incidental edit (Production rule: never break emailed Family
links).
"""

from django.urls import reverse

from parishkit.stewardship import urls

PUBLIC = [
    ("branding/<uuid:asset_id>.png", "branding_asset"),
    ("", "entry"),
    ("access/<str:token>", "access"),
    ("files/<str:token>", "hosted_file"),
]
FAMILY = [
    ("form", "form"),
    ("submit", "submit"),
    ("presence", "presence"),
    ("", "entry"),
    ("keepalive", "keepalive"),
    ("logout", "logout"),
]
TOP = [
    ("admin/oauth/callback", "google_callback"),
    ("admin/oauth/start", "google_login"),
]


def _routes(patterns):
    """``(route, name)`` for each plain path in a pattern list."""
    return [
        (str(pattern.pattern), pattern.name)
        for pattern in patterns
        if getattr(pattern, "name", None)
    ]


def test_public_family_routes_are_frozen():
    """The entry, code-access and hosted-file links stay where emails point."""
    assert _routes(urls.public_patterns) == PUBLIC


def test_family_portal_routes_are_frozen():
    """The Family portal's own routes stay under /family/."""
    assert _routes(urls.family_patterns) == FAMILY


def test_oauth_and_namespaces_are_frozen():
    """The Google callback is registered with Google; prefixes stay put."""
    assert _routes(urls.urlpatterns) == TOP
    prefixes = [
        (str(pattern.pattern), pattern.namespace)
        for pattern in urls.urlpatterns
        if getattr(pattern, "namespace", None)
    ]
    assert prefixes == [
        ("", "public"),
        ("family/", "family"),
        ("admin/", "admin"),
        ("", "internal"),
    ]


def test_admin_sign_in_and_sign_out_are_frozen():
    """Sign-in and sign-out stay where sessions, error pages and scripts send
    people (NAV-13 may add a trailing slash later, with redirects)."""
    assert reverse("admin:login") == "/admin/login"
    assert reverse("admin:logout") == "/admin/logout"
