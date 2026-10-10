"""The not-found page for an unknown portal address (#927), as served.

Each page is the real ``BrowserErrorMiddleware`` answer to a person who
opened an address no page answers: the view stand-in returns Django's own
plain 404, exactly as an unmatched URL does. The fixture server answers
these paths with 200, so the browser test serves the body with status 404.
"""

from django.http import HttpResponseNotFound
from django.test import RequestFactory

from parishkit.stewardship.web.error_pages import BrowserErrorMiddleware

PAGE = {"HTTP_ACCEPT": "text/html", "HTTP_SEC_FETCH_MODE": "navigate"}
# Fixture path -> the portal address the page answers.
PAGES = {"/not-found-admin": "/admin/no-such-page"}


def components(context, admin):
    """Render each not-found page through the real middleware."""

    def not_found(path):
        """The middleware's page for a person opening ``path``."""
        request = RequestFactory().get(path, **PAGE)
        response = BrowserErrorMiddleware(lambda _: HttpResponseNotFound())(request)
        assert response.status_code == 404
        return response.content.decode()

    return {fixture: ("text/html", not_found(path)) for fixture, path in PAGES.items()}
