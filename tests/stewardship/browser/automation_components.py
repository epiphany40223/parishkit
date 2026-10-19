"""Admin automation pages for the browser tests, from the real templates (ADM-11).

Automation access and the dashboard are served at their real addresses,
because a form[data-in-place] swaps regions only when its post redirects
back to the same page (#559): the page before a change at its address, and
the page after it at the same address with a query (the fixture server's
POST answers redirect there). Revoke is a row action (#879): its dialog
posts to the session collection, then redraws both tables from the page's
data-refresh-url, which on each start page below is the revoked state's
address. Automation access is rendered through the
view's own ``access_context`` for every query its headings and "Include
ended sessions" box lead to, two choices deep, so sorting and the box
refresh in place exactly as on the server (#621). The approval page is
served in each of its states: code entry, the review and the approved
result. The LOCAL sign-in page is served at its real address too.
"""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.http import QueryDict
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import automation_views as views

NOW = datetime(2026, 10, 4, 15, tzinfo=UTC)
ACCESS = reverse("admin:automation_access")
APPROVAL = reverse("admin:automation_approval")
NOTICES = reverse("admin:automation_notices")
# Automation access with the notices region (#1673: the notices left Home).
# Not a real address: Automation access's own paths are its sort pages.
NOTICES_PAGE = "/automation-notices-page"
LOCAL_SIGN_IN = "/admin/local/sign-in"
OWN = UUID(int=701)
OTHER = UUID(int=702)
REVOKE = reverse("admin:automation_sessions")
# The fixture server's answers to the pages' posts (status, Location, body).
POSTS = {
    REVOKE: (303, ACCESS + "?revoked=1", ""),
    # Revoking while ended sessions are shown keeps them shown (#621).
    REVOKE + "?ended=yes": (303, ACCESS + "?ended=yes&revoked=1", ""),
    REVOKE + "?live_sort=label": (303, ACCESS + "?live_sort=label&revoked=1", ""),
    NOTICES: (303, NOTICES_PAGE + "?cleared=1", ""),
}


ME = UUID(int=801)
SOMEONE = UUID(int=802)
# What ACCESS's tests start from, and how deep the fixture follows the
# page's own headings and box from each (two choices in a row). The race
# tests start from "live_sort=label", whose box answers slowly.
STARTS = ("", "ended=yes", "live_sort=label")
DEPTH = 2


def canonical(path):
    """``path`` with its query's pairs sorted, so lookups ignore their order.

    The fixture server falls back to this form when the exact path is not
    served, so a page's controls may send the state in any order (#621).
    """
    base, _, query = path.partition("?")
    return base + ("?" + "&".join(sorted(query.split("&"))) if query else "")


# Answered after a pause (see the server in conftest.py): ticking the box on
# the race tests' page.
SLOW_GETS = {canonical(ACCESS + "?live_sort=label&ended=yes")}


def _session(identifier, label, *, created, principal=ME, ended=None, reason=None):
    """One session row as the views read it: live unless ``ended`` is given."""
    return {
        "id": identifier,
        "label": label,
        "scope": "full",
        "created_at": NOW - timedelta(days=created),
        "expires_at": NOW + timedelta(days=29),
        "last_used_at": NOW - timedelta(hours=1),
        "revoked_at": ended,
        "end_reason": reason,
        "ended_at": ended,
        "host": "0123456789ab",
        "live": ended is None,
        "principal_id": principal,
        "principal_email": (
            "admin@example.org" if principal == ME else "other@example.org"
        ),
    }


def _revoked(query):
    """The address of ``query``'s page after the own session is revoked."""
    return ACCESS + "?" + "&".join(filter(None, (query, "revoked=1")))


def _access_page(context, admin, query, *, own_live=True, refresh=None, notices=None):
    """Automation access for one query, built by the view's own context.

    The own session ("launch <assistant>", the older of the two live ones)
    is live, or revoked by its owner after the revoke test's post. Sorting
    the live table by Administrator therefore changes its order, and so does
    sorting the ended table by label. ``refresh``, when given, replaces the
    page's own address as the one the Revoke dialog redraws from;
    ``notices``, when given, are this Administrator's automation notices.
    """
    state = views._access_state(QueryDict(query))
    own = _session(
        OWN,
        "launch <assistant>",
        created=3,
        ended=None if own_live else NOW,
        reason=None if own_live else "revoked_by_owner",
    )
    everyone = [_session(OTHER, "other job", created=1, principal=SOMEONE)]
    ended = []
    if own_live:
        everyone.append(own)
    if state[0]:
        ended = [
            _session(
                UUID(int=703),
                "Zeta task",
                created=6,
                ended=NOW - timedelta(days=5),
                reason="logout",
            ),
            _session(
                UUID(int=704),
                "old job",
                created=10,
                ended=NOW - timedelta(hours=2),
                reason="revoked_by_administrator",
            ),
        ] + ([] if own_live else [own])
    page = views.access_context(state, ME, everyone, ended)
    if refresh:
        page["refresh_url"] = refresh
    if notices is not None:
        page["notices"] = notices
    html = render_to_string(
        "stewardship/automation-access.html",
        context | {"admin_chrome": admin} | page,
    )
    return html, page


def _next_queries(page):
    """The queries the page's headings and box lead to, as ui-v1.js sends them.

    A heading's link is its table's heading query; the box's form sends its
    hidden sorts in document order, then the box when it is ticked.
    """
    queries = [
        table.heading_query(column)
        for table in (page["live_table"], page["ended_table"])
        if table.rows
        for column in table.sorting.columns()
    ]
    toggled = [] if page["include_ended"] else [("ended", "yes")]
    queries.append(urlencode([*page["sort_fields"], *toggled]))
    return queries


def components(context, admin):
    """The pages a browser test opens, keyed by exact path."""
    responses = {}

    # Every page two choices away from each start (breadth first, so a page
    # is expanded from its shortest path), so a test can sort, sort
    # again, or tick the box, and each request finds its page.
    pending = [(query, 0) for query in STARTS]
    while pending:
        query, depth = pending.pop(0)
        path = canonical(ACCESS + (f"?{query}" if query else ""))
        if path in responses:
            continue
        # A start page redraws, after Revoke, from its revoked state.
        start = depth == 0
        html, page = _access_page(
            context, admin, query, refresh=_revoked(query) if start else None
        )
        responses[path] = ("text/html", html)
        if depth < DEPTH:
            pending.extend((later, depth + 1) for later in _next_queries(page))
    # After the revoke posts (see POSTS): the own session has ended. The
    # box's form carries a hidden revoked=1 from then on, so ticking it after
    # a revoke (the race test) is served the revoked state too, with the
    # ended session among the ended ones, never live again.
    for query in STARTS:
        ticked = (
            query
            if "ended=yes" in query
            else "&".join(filter(None, (query, "ended=yes")))
        )
        for shown in {query, ticked}:
            html, _ = _access_page(
                context, admin, shown, own_live=False, refresh=_revoked(shown)
            )
            html = re.sub(
                r'(<form[^>]*id="automation-filter"[^>]*>)',
                r'\1<input type="hidden" name="revoked" value="1">',
                html,
                count=1,
            )
            assert 'name="revoked"' in html
            responses[canonical(_revoked(shown))] = ("text/html", html)
    # LOCAL's sign-in page, which the laptop command's link opens (#613).
    responses[LOCAL_SIGN_IN] = (
        "text/html",
        render_to_string(
            "stewardship/local-sign-in.html", context | {"local_environment": True}
        ),
    )

    def notices_page(rows):
        """Automation access with the notices region (empty or not)."""
        notices = {"total": len(rows), "rows": rows}
        return _access_page(context, admin, "", notices=notices)[0]

    notice = {
        "id": UUID(int=703),
        "kind": "approved",
        "label": "launch <assistant>",
        "command_type": None,
        "created_at": NOW,
    }
    refused = notice | {"id": UUID(int=704), "kind": "refused", "label": None}
    responses[NOTICES_PAGE] = ("text/html", notices_page([notice]))
    responses[NOTICES_PAGE + "?two=1"] = (
        "text/html",
        notices_page([notice, refused]),
    )
    responses[NOTICES_PAGE + "?cleared=1"] = ("text/html", notices_page([]))

    def approval(**extra):
        """The approval page in one state."""
        return render_to_string(
            "stewardship/automation-approval.html",
            context
            | {"admin_chrome": admin}
            | {
                "back": ACCESS,
            }
            | extra,
        )

    pending = {
        "label": "launch <assistant>",
        "expect_email": "admin@example.org",
        "scope": "full",
        "days": 30,
        "host_digest": "0123456789ab" + "c" * 52,
    }
    responses[APPROVAL] = ("text/html", approval(state="enter"))
    responses["/automation-approval-review"] = (
        "text/html",
        approval(
            state="review",
            code="ABCD-EFGH",
            pending=pending,
            scope_label="Full",
            host="0123456789ab",
            scope_choices=[("full", "Full"), ("read_only", "Read-only")],
        ),
    )
    return responses
