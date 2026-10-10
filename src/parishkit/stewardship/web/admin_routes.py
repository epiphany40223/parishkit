"""Admin URL scheme plumbing: the slashless redirect and the current campaign.

The Admin URL scheme (admin-portal spec, "URL scheme", #525) puts every page
under its menu group, ends page URLs with ``/`` and drops the campaign
identifier. Old Admin addresses are not kept: the Administrator dropped
them (#864), so each answers 404. A page's form without the trailing slash
is routed to ``redirect_to``, which redirects to the page:

- GET and HEAD get 301; every other method gets 308, which keeps the method
  and body.
- The query string is kept, because report filters live there.

``current_campaign`` lets a campaign-free route reuse an existing view that
still takes ``campaign_id``, so moving a route changes only the URL pattern,
not the view. Family URLs never pass through here.
"""

from django.http.response import HttpResponseRedirectBase
from django.urls import reverse

NAMESPACE = "admin"


class HttpResponsePermanentRedirectKeepingMethod(HttpResponseRedirectBase):
    """308 Permanent Redirect: the client repeats the same method and body."""

    status_code = 308


class HttpResponseMovedPermanently(HttpResponseRedirectBase):
    """301 Moved Permanently, for the GET and HEAD of a slashless page URL."""

    status_code = 301


def _current_campaign_id():
    """The current campaign's id, or None when there is none.

    Imported late: URL modules load before the app registry is ready.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    return SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).first()


def redirect_to(target):
    """The view for a page URL's form without its trailing slash.

    ``target`` is the page's URL name; the route's own arguments are passed
    on unchanged and the query string is kept.
    """

    def view(request, **arguments):
        """Redirect permanently: 301 for GET/HEAD, 308 for other methods."""
        url = reverse(f"{NAMESPACE}:{target}", kwargs=arguments)
        query = request.META.get("QUERY_STRING", "")
        moved = (
            HttpResponseMovedPermanently
            if request.method in {"GET", "HEAD"}
            else HttpResponsePermanentRedirectKeepingMethod
        )
        return moved(f"{url}?{query}" if query else url)

    view.redirect_target = target
    return view


def current_campaign(view):
    """Call an existing ``view(request, campaign_id, …)`` for the current campaign.

    A campaign-free route has no ``campaign_id`` to pass; the current campaign
    is looked up per request. With no current campaign the view receives
    None and refuses as it already does for an unknown campaign.
    """

    def wrapped(request, *args, **kwargs):
        """Supply the current campaign's id as ``campaign_id``."""
        return view(request, *args, campaign_id=_current_campaign_id(), **kwargs)

    wrapped.__name__ = getattr(view, "__name__", "view")
    wrapped.__doc__ = view.__doc__
    return wrapped
