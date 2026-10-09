"""Rows-per-page and carried filters for the private POST report tables.

The active parishioner family directory, Financial stewardship detail and Additional
information reports keep their filters in CSRF POST bodies and page through
an installed SQL selection that also owns their sort order (see
``web.tables.report_table``). The page size is parsed here, apart from each
report's own query object, so it never enters the selection's closed filter
JSON, a persisted export selection or audit context.
"""

import re
import secrets
from dataclasses import replace
from urllib.parse import urlencode
from uuid import UUID

from django.contrib.sessions.backends.base import UpdateError
from django.contrib.sessions.exceptions import SessionInterrupted


def pop_page_size(parameters, sizes, *, default=50):
    """Remove and return the rows-per-page choice from a mutable POST QueryDict.

    Only sizes the report's SQL selection accepts are offered and accepted.
    """
    values = parameters.pop("size", [str(default)])
    if (
        len(values) != 1
        or not (values[0].isascii() and values[0].isdecimal())
        or str(int(values[0])) != values[0]
        or int(values[0]) not in sizes
    ):
        raise ValueError("Unsupported report page size.")
    return int(values[0])


def carried_filters(query, *extra):
    """The private filters every navigator and heading form carries as hidden
    fields; page, size and sort come from the table itself."""
    return [
        *((key, value) for key, value in query.form_values().items() if key != "sort"),
        *extra,
    ]


def last_page(total, size):
    """The last page holding a matching row (1 for an empty result)."""
    return max(1, -(-total // size))


def clamp_query(query, total, size):
    """The query moved back to the last page when it asked for one past it.

    A typed page number or a stale Next click after the result shrank would
    otherwise show an empty page; like every other Admin table, a page past
    the end shows the last page instead; an empty result's last page is 1.
    Returns None when no move is needed.
    """
    last = last_page(total, size)
    return replace(query, page=last) if query.page > last else None


# The signed-in session remembers each queue view (its applied filters, sort,
# page and size) under a random token, so an item page opened from the queue
# can lead to the next item and back to the same view (#534). Only the token
# travels in a URL; the private filters stay in the session, as a
# configuration change's remembered origin does (admin_navigation.py).
QUEUE_VIEWS_KEY = "pk_admin_queue_views"
QUEUE_VIEWS_KEPT = 20
_TOKEN = re.compile(r"[0-9a-f]{32}")


def queue_token(value):
    """A well-formed queue token, or "" for none; anything else is refused."""
    if value and not _TOKEN.fullmatch(value):
        raise ValueError("Invalid queue token.")
    return value


def remember_queue(request, name, campaign_id, values):
    """Remember one queue view and return its token.

    ``values`` maps form field names to text. The same view keeps the token it
    already has (moved to most recent), and only the most recent views are
    kept, so the session stays small. Without a session (a test request) the
    view is not remembered and the token is "".

    The session is saved here, before the caller's guarded response opens its
    read-only transaction: the session middleware must not write through it
    (web/responses.py), so it is left only to send the cookie, as after a
    session rotation (accounts/sessions.py).
    """
    session = getattr(request, "session", None)
    if session is None:
        return ""
    view = {"queue": name, "campaign": str(campaign_id), "values": dict(values)}
    views = dict(session.get(QUEUE_VIEWS_KEY, {}))
    token = next((key for key, kept in views.items() if kept == view), None)
    if token is not None and token == next(reversed(views)):
        return token  # Already the most recent view: nothing to save.
    if token is None:
        token = secrets.token_hex(16)
    else:
        del views[token]
    views[token] = view
    session[QUEUE_VIEWS_KEY] = dict(list(views.items())[-QUEUE_VIEWS_KEPT:])
    try:
        session.save()
    except UpdateError:
        # The session ended meanwhile (signed out or rotated elsewhere).
        raise SessionInterrupted("Session ended; please log in again.") from None
    session.modified = False
    session.stewardship_persisted = True
    return token


def recall_queue(request, name, campaign_id, token):
    """The remembered values for ``token`` on this queue and campaign, or None.

    A token from another queue, another campaign or an earlier sign-in (or
    one dropped as older than the last few views) is simply not found.
    """
    session = getattr(request, "session", None)
    if not token or session is None:
        return None
    view = session.get(QUEUE_VIEWS_KEY, {}).get(token)
    if (
        not isinstance(view, dict)
        or view.get("queue") != name
        or view.get("campaign") != str(campaign_id)
        or not isinstance(view.get("values"), dict)
    ):
        return None
    return view["values"]


def next_open(read_page, size, current, is_open):
    """The id of the first open row after ``current`` in a paged selection.

    ``read_page(number)`` returns one page of ``size`` rows in the queue's own
    order. The row just saved is skipped, as is every row ``is_open`` rejects.
    When ``current`` is not in the selection (it was opened from elsewhere),
    the first open row is next. None when nothing open follows: Save and next
    then returns to the queue (Administrator decision on #534).
    """
    current, first, seen, number = str(current), None, False, 1
    while True:
        rows = read_page(number)
        for row in rows:
            if str(row["id"]) == current:
                seen = True
            elif is_open(row):
                if seen:
                    return str(row["id"])
                first = first or str(row["id"])
        if len(rows) < size:
            return None if seen else first
        number += 1


def pop_navigation(parameters):
    """Remove Save and next's fields from a mutable follow-up POST QueryDict.

    Returns ``(token, advance, following)``: the queue token ("" for none),
    whether Save and next was pressed, and the next item's id that the item
    page found (None at the end of the queue). The rest of the form keeps its
    own strict grammar. The id only chooses which page to open next; that
    page checks access again, as it does when opened from the queue.
    """

    def single(key):
        """One value, or "" when the field is absent."""
        values = parameters.pop(key, [""])
        if len(values) != 1:
            raise ValueError("Repeated navigation field.")
        return values[0]

    token = queue_token(single("queue"))
    then, following = single("then"), single("next")
    if then not in {"", "next"}:
        raise ValueError("Unknown follow-up navigation.")
    following = UUID(following) if following else None
    return token, then == "next", following if then == "next" else None


def with_queue(url, token):
    """``url`` carrying the queue token, when there is one."""
    return f"{url}?{urlencode({'queue': token})}" if token else url
