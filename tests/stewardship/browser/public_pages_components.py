"""The public About, Privacy policy and Terms of use pages (#2354), as served.

Each page is its real view's answer to an anonymous GET. The fixture build has
no runtime or database, which is the before-setup state the pages must also
handle: they name "the parish" and leave the contact lines out.
"""

from django.test import RequestFactory

from parishkit.stewardship.accounts import public_pages

# Fixture path -> the view that answers it.
PAGES = {
    "/about/": public_pages.about,
    "/privacy/": public_pages.privacy,
    "/terms/": public_pages.terms,
}


def components(context, admin):
    """Render each public page through its view, as an anonymous visitor."""

    def rendered(path, view):
        """The view's page body for an anonymous GET of ``path``."""
        response = view(RequestFactory().get(path))
        assert response.status_code == 200
        return response.content.decode()

    return {path: ("text/html", rendered(path, view)) for path, view in PAGES.items()}
