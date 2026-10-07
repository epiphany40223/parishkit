"""Menu group roots: ``/admin/<group>/`` opens the group's first open entry.

The admin-portal spec ("Menu groups") has each group's root URL redirect to
the first entry available to the viewer, or to Home when none is. The
target depends on the viewer's role and the campaign's state, so it is a
temporary redirect that is never cached.
"""

from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError

from .admin_context import first_entry_url
from .admin_editing import error_response, signed_out
from .authentication import runtime
from .limiting import LimiterUnavailable
from .sessions import authenticated_admin


def group_root(section):
    """The view for menu group ``section``'s root URL."""

    @require_safe
    def view(request):
        """Redirect to the group's first open entry, else Home."""
        try:
            actor = authenticated_admin(request, store=runtime().store)
            if actor is None:
                return error_response(signed_out())
            url = first_entry_url(actor, section) or reverse("admin:index")
        except (
            ConfigError,
            DatabaseError,
            LimiterUnavailable,
            PermissionError,
        ) as error:
            return error_response(error)
        response = HttpResponseRedirect(url)
        add_never_cache_headers(response)
        return response

    view.group_root = section
    return view
