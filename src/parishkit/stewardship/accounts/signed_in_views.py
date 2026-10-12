"""The Signed-in sessions page: who is signed in to the Admin portal now (#953).

``/admin/users/sessions/`` (Administrator only, in Users and access) lists
every live interactive Admin session (``signed_in_sessions.live_sessions``)
in one table that sorts by its headings. Each row but the reader's own
current one has Sign out, a row action (#879): its icon button opens the
shared confirmation dialog, which posts the session to this same address and
then redraws the table in place from the page's own address. The page's
query parameters are the table's sort token and, only on the page a Sign out
redirects to, ``outcome=already_ended``; anything else is refused.

When the session named had already ended (signed out, timed out, or replaced
by a newer sign-in) the post changes nothing and redirects with that outcome.
The page it lands on then carries a hidden ``data-confirm-announce`` line,
which the shared dialog script announces instead of "Signed out …", so the
reader is never told they ended a session that had already ended.
"""

from urllib.parse import urlencode
from uuid import UUID

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.tables import whole_table

from .admin_editing import error_response
from .authentication import runtime
from .automation_views import REFUSALS, _administrator, _fields
from .sessions import database_now
from .signed_in_sessions import SORTING, live_sessions, sign_out

# The one outcome a Sign out reports in the address it redirects to.
ALREADY_ENDED = "already_ended"


def _state(parameters, *, outcome=False):
    """The validated (sort, already ended) of a query; anything else is refused.

    Only the page (``outcome=True``) accepts ``outcome``; a Sign out's own
    address carries the sort alone.
    """
    allowed = {"sort", "outcome"} if outcome else {"sort"}
    values = filters(parameters, allowed=allowed)
    if values.get("outcome") not in (None, ALREADY_ENDED):
        raise ValueError("Unsupported outcome.")
    return SORTING.parse(values), values.get("outcome") == ALREADY_ENDED


def _page_url(sort, anchor="", *, already_ended=False):
    """This page with ``sort`` (left out when it is the default)."""
    fields = [] if sort == SORTING.default else [("sort", sort)]
    if already_ended:
        fields.append(("outcome", ALREADY_ENDED))
    query = "?" + urlencode(fields) if fields else ""
    return reverse("admin:signed_in_sessions") + query + anchor


def page_context(rows, sort):
    """The template context for ``rows`` in the order ``sort`` names.

    Kept apart from the database read so the browser tests render the page
    exactly as the view does. Each row gets the name its Sign out control and
    dialog use.
    """
    for row in rows:
        row["label"] = _("%(email)s’s session") % {
            "email": row["email"] or _("unknown")
        }
    query = "" if sort == SORTING.default else urlencode({"sort": sort})
    return {
        "table": whole_table(rows, sorting=SORTING, sort=sort),
        "state_query": query,
        "refresh_url": _page_url(sort),
    }


@require_http_methods(["GET", "HEAD", "POST"])
def signed_in_sessions_view(request):
    """List the live sessions, or sign the one the dialog names out.

    A POST names exactly one ``session_id``; it ends that session at once
    (its next request is signed out) and redirects back to the table as the
    reader sorted it. One that has already ended is left as it is, and the
    redirect says so (``outcome=already_ended``); the reader's own current
    session, a command session or an unknown id is refused.
    """
    try:
        service = runtime()
        actor = _administrator(request, service)
        posted = request.method == "POST"
        sort, already_ended = _state(request.GET, outcome=not posted)
        current = getattr(request.portal_session, "pk", None)
        if posted:
            _fields(request.POST, {"csrfmiddlewaretoken", "session_id"})
            ended = sign_out(UUID(request.POST.get("session_id", "")), actor, current)
            return HttpResponseRedirect(
                _page_url(sort, "#sessions-table", already_ended=not ended)
            )
        context = page_context(live_sessions(database_now(), current), sort)
        context["already_ended"] = already_ended
        response = render(request, "stewardship/signed-in-sessions.html", context)
        response["Cache-Control"] = "no-store"
        return response
    except REFUSALS as error:
        return error_response(error)
