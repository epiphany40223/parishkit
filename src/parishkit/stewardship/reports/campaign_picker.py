"""The retired report campaign chooser (admin-portal spec, navigation rule 10).

Reports show the current campaign only until the single-campaign change (#145),
so the old "Choose a retained campaign" address no longer lists campaigns. It
redirects to the reports root, which opens the current campaign's
Participation report, and keeps the query string, where report filters live.
The redirect reveals nothing, so it needs no sign-in check of its own: the
reports root checks access as before. It is a temporary (302) redirect even
though its target is fixed, because NAV-11 moves the reports root itself and
adds the permanent redirects for every old report address; a browser that
cached a permanent redirect here would keep sending readers to the old root.
"""

from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_GET


@require_GET
def picker(request):
    """Send the retired chooser to the reports root, filters kept."""
    query = request.META.get("QUERY_STRING", "")
    response = HttpResponseRedirect(
        reverse("admin:reports") + (f"?{query}" if query else "")
    )
    response["Cache-Control"] = "no-store"
    return response
