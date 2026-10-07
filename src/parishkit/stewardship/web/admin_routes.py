"""Admin URL scheme plumbing: permanent redirects from old Admin addresses.

The Admin URL scheme (admin-portal spec, "URL scheme", #525) moves every
page under its menu group, ends page URLs with ``/`` and drops the campaign
identifier. Bookmarks, sent digest emails and the operator runbooks still
name the old addresses, so each old address stays routed to a ``legacy``
view that redirects to the new one:

- GET and HEAD get 301; every other method gets 308, which keeps the method
  and body, so a form left open on an old page still submits.
- The query string is kept, because report filters live there.
- An old address that names a campaign redirects only for the current
  campaign. Any other campaign gets the 410 refusal and never a redirect, so
  a form left open on an earlier campaign is never re-posted into the current
  one. Because the target depends on which campaign is current, those
  responses are never cached, and they are only given to a signed-in Admin
  portal user: anyone else gets the sign-in refusal first.

``current_campaign`` lets a new campaign-free route reuse an existing view
that still takes ``campaign_id``, so moving a route changes only the URL
pattern, not the view. Family URLs never pass through here.
"""

from django.http.response import HttpResponseRedirectBase
from django.urls import reverse
from django.utils.cache import add_never_cache_headers

from .refusals import gone_response

NAMESPACE = "admin"
CAMPAIGN = "campaign_id"


class HttpResponsePermanentRedirectKeepingMethod(HttpResponseRedirectBase):
    """308 Permanent Redirect: the client repeats the same method and body."""

    status_code = 308


class HttpResponseMovedPermanently(HttpResponseRedirectBase):
    """301 Moved Permanently, for the GET and HEAD of an old page address."""

    status_code = 301


def _current_campaign_id():
    """The current campaign's id, or None when there is none.

    Imported late: URL modules load before the app registry is ready.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    return SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).first()


def _signed_out_refusal(request):
    """The sign-in refusal when the request has no live Admin session, else None.

    Any Admin portal role may hold a campaign address (reports are open to
    Staff and Ministry leaders), so only the session is checked here; the
    target page still checks its own capability. The check renews nothing.
    """
    from parishkit.stewardship.accounts.admin_editing import (
        error_response,
        signed_out,
    )
    from parishkit.stewardship.accounts.authentication import runtime
    from parishkit.stewardship.accounts.sessions import authenticated_admin

    try:
        admin = authenticated_admin(request, store=runtime().store, read_only=True)
    except PermissionError:
        admin = None
    return error_response(signed_out()) if admin is None else None


def _target_url(request, target, arguments):
    """The new address for ``target`` with ``arguments`` and the old query."""
    url = reverse(f"{NAMESPACE}:{target}", kwargs=arguments)
    query = request.META.get("QUERY_STRING", "")
    return f"{url}?{query}" if query else url


def legacy(target, *, campaign=False):
    """The view for an old Admin address that now lives at ``target``.

    ``target`` is the new route's URL name; the old route's own arguments are
    passed on unchanged. With ``campaign=True`` the old route names a
    campaign (``campaign_id``) that the new one does not: it redirects only
    when that campaign is the current one, and refuses with 410 otherwise.
    """

    def view(request, **arguments):
        """Redirect permanently: 301 for GET/HEAD, 308 for other methods."""
        if campaign:
            named = arguments.pop(CAMPAIGN)
            # Whether a campaign is the current one is not for the signed
            # out: answer them the sign-in refusal before comparing, so the
            # 301-or-410 choice never tells anyone which campaign is current.
            refused = _signed_out_refusal(request)
            if refused is not None:
                add_never_cache_headers(refused)
                return refused
            if named != _current_campaign_id():
                from parishkit.stewardship.campaigns.single_campaign import (
                    not_current_refused,
                )

                response = gone_response(not_current_refused())
                add_never_cache_headers(response)
                return response
        url = _target_url(request, target, arguments)
        moved = (
            HttpResponseMovedPermanently
            if request.method in {"GET", "HEAD"}
            else HttpResponsePermanentRedirectKeepingMethod
        )
        response = moved(url)
        if campaign:
            # The target depends on which campaign is current: never cache.
            add_never_cache_headers(response)
        return response

    view.legacy_target = target
    view.legacy_campaign = campaign
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
