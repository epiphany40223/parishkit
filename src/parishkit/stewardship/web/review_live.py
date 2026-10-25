"""Review regions that update themselves (#2174).

The [review pipeline](docs/specs/stewardship/review-pipeline/spec.md#concurrency)
keeps every open review page current while someone else decides its items
(UX conventions, "Pages that update themselves", items others change). A
review region (``[data-live-items]``: a review worklist's table, a Family
hub tab's rows, the Review work cards) draws each item with a short key of
its id (``item_key``) and a token of what it shows (``item_token``). About
once a minute ``ui-v1.js`` sends those pairs back in one passive GET of the
page's own address (``live=key.token,...``); the page reads its items as it
always does, keeps only the drawn ones whose token changed (``changed``) and
answers with those, drawn as the page draws them, or with nothing. The page
then redraws each changed item in place.

This module is the contract every review page shares: ``drawn`` parses the
read, ``pick`` narrows a stream's rows to the drawn items before paging,
``changed`` keeps the ones that changed after decoration, and
``changed_count`` compares a whole rendered answer (the Family hub's tab,
whose page draws every one of the Family's items anyway; its region is
marked ``data-live-whole``, the only kind ui-v1.js may redraw whole). The
template filters ``live_key``, ``live_token`` and ``live_seen`` draw the
same values.
"""

import base64
import dataclasses
import enum
import hashlib
import json
import re
from datetime import date, datetime
from uuid import UUID

from django.utils.functional import Promise

# The query parameter a region's read carries, and its table's columns.
LIVE = "live"
COLUMNS = "live_columns"
# The columns a worklist table may draw or not (``Worklist.live_columns``).
COLUMN_NAMES = frozenset({"now", "select"})
# The most items one read names. A key and its token take 18 characters
# once the comma between pairs is encoded, so 300 of them keep the request
# line well inside the web server's 8190 (runtime_process); a longer list
# sends 300 per read, in turn (ui-v1.js).
MAX_LIVE = 300
# A key is 48 bits of its id's digest: unique enough within one page's items.
KEY_CHARS = 8
# A token is 36 bits of the digest of what the item shows: a change that
# kept its token would wait for the next one, about once in 68 billion.
TOKEN_CHARS = 6
_PIECE = re.compile(r"[A-Za-z0-9_-]+")
# What a review row draws (``review_worklist._decorate`` and the stream
# pages' own additions), so a change to any of it changes the token. SEEN is
# what the reader sees: its identity cells (a Family's or Member's name),
# values, stage and last decision. OFFERED is what the row offers: its
# version (which its forms post) and its actions, whose icons are compared
# less each one's request key, which every draw makes anew. A row redrawn
# because only OFFERED changed (the campaign was held, say) shows no
# "Changed by someone else" tag: ``seen_token`` tells the two apart.
SEEN = (
    "identity",
    "dated",
    "family_name",
    "placement",
    "stage_label",
    "stage_look",
    "stage_note",
    "decision_label",
    "decided_by",
    "decided_at",
    "note",
    "submitted_at",
    "baseline",
    "family_answer",
    "reviewer_value",
    "parishsoft_now",
    "shows_now",
    "not_seen",
)
OFFERED = (
    "version",
    "kinds",
    "choice",
    "offers_edit",
    "offers_entered",
    "icons",
)
SHOWN = SEEN + OFFERED
FRESH_EVERY_DRAW = frozenset({"post"})


def _digest(text, size):
    """The first ``size`` URL-safe characters of ``text``'s SHA-256."""
    raw = hashlib.sha256(text.encode()).digest()
    return base64.urlsafe_b64encode(raw).decode()[:size]


def _plain(value):
    """``value`` as plain JSON data, the same on every draw.

    Lazy translations, enums, dates and UUIDs become their text, a
    dataclass its fields and a mapping its items, so the token never
    depends on an object's identity.
    """
    if isinstance(value, Promise):
        return str(value)
    if isinstance(value, enum.Enum):
        return _plain(value.value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _plain(item)
            for key, item in value.items()
            if key not in FRESH_EVERY_DRAW
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(item) for item in value]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    return str(value)


def item_key(item_id, scope=""):
    """The short key a region draws for one item (its id, within ``scope``)."""
    return _digest(f"{scope}:{item_id}", KEY_CHARS)


def _identity(cells):
    """A row's identity cells as the token reads them: each value, its
    column's key and the value stacked under it (its column's ``detail``,
    such as the DUID line, #2124), when the cell has one. The link a cell
    opens is left out: an item page's link carries the page's remembered
    view (Ministry requests' ``queue``), which a check may hold differently
    while the cell shows the same."""
    return [
        (value, getattr(column, "key", column), *shown)
        for value, column, _link, *shown in cells or ()
    ]


def _token(item, names):
    """The digest of ``item``'s ``names`` (a review row), or of all of it."""
    shown = item
    if isinstance(item, dict):
        shown = {name: item.get(name) for name in names}
        if "identity" in shown:
            shown["identity"] = _identity(shown["identity"])
    text = json.dumps(_plain(shown), sort_keys=True, default=str)
    return _digest(text, TOKEN_CHARS)


def item_token(item):
    """The token of what ``item`` shows: a review row's ``SHOWN`` values, or
    every field of anything else (a Review work card)."""
    return _token(item, SHOWN)


def seen_token(item):
    """The token of what a review row shows the reader (``SEEN``): a
    redrawn row whose ``seen_token`` is unchanged changed only what it
    offers, so ui-v1.js shows no "Changed by someone else" tag on it."""
    return _token(item, SEEN)


class Drawn(dict):
    """A read's items, ``{key: token}``, and the columns its table draws
    (``columns``: a frozenset of ``COLUMN_NAMES``, or None when not sent)."""

    columns = None


def drawn(request):
    """The items a region's read names, as a ``Drawn``, or None.

    None for any request that is not such a read. A read is a GET (or HEAD)
    with one ``live`` value, its comma-separated ``key.token`` pairs, and
    optionally one ``live_columns`` value, its table's columns joined by
    ``-``; any other form of it, or more than ``MAX_LIVE`` pairs, is a
    ``ValueError`` (the page's 400).
    """
    if LIVE not in request.GET:
        return None
    values = request.GET.getlist(LIVE)
    if request.method not in {"GET", "HEAD"} or len(values) != 1:
        raise ValueError("A live read is one GET value.")
    pairs = values[0].split(",") if values[0] else []
    if len(pairs) > MAX_LIVE:
        raise ValueError("A live read names too many items.")
    found = Drawn()
    if COLUMNS in request.GET:
        chosen = request.GET.getlist(COLUMNS)
        names = frozenset(chosen[0].split("-")) - {""} if len(chosen) == 1 else None
        if names is None or not names <= COLUMN_NAMES:
            raise ValueError("A live read names unknown columns.")
        found.columns = names
    for pair in pairs:
        key, _, token = pair.partition(".")
        if not (
            len(key) == KEY_CHARS
            and len(token) == TOKEN_CHARS
            and _PIECE.fullmatch(key)
            and _PIECE.fullmatch(token)
        ):
            raise ValueError("A live read names an item malformed.")
        found[key] = token
    return found


def alone(request):
    """Whether a read carries nothing but its own parameters."""
    return LIVE in request.GET and set(request.GET) <= {LIVE, COLUMNS}


def pick(rows, live, *, ident=lambda row: row["id"]):
    """The ``rows`` a read names (all of them when ``live`` is None).

    Applied to a stream's rows at every stage before they are paged, so the
    drawn items are found whatever stage they have moved to.
    """
    if live is None:
        return rows
    return [row for row in rows if item_key(ident(row)) in live]


def changed(rows, live):
    """The decorated ``rows`` whose token differs from the drawn one.

    ``rows`` are the region's rows as the template draws them (after every
    stream's own decoration), so the comparison is with what the page
    showed; with ``live`` None every row is kept.
    """
    if live is None:
        return rows
    return [
        row
        for row in rows
        if live.get(item_key(row["id"]), item_token(row)) != item_token(row)
    ]


# How a rendered page draws an item: its key, then its token (the shared row
# templates write both in this order).
_DRAWN = re.compile(r'data-live-item="([^"]+)" data-live-token="([^"]+)"')


def changed_count(page, live):
    """How many of the drawn items ``page`` (rendered HTML) draws differently."""
    return sum(
        1 for key, token in _DRAWN.findall(page) if key in live and live[key] != token
    )


def narrow(worklist, table, live):
    """``worklist`` and its ``table`` with only the changed rows, and their count.

    A stream page calls it on its final rows when ``live`` is a read's
    items; it answers with nothing (an empty body) when the count is 0. The
    rows are drawn with the columns the page's table draws (``live.columns``),
    not those the changed rows alone would call for, so each has the cells
    its table has: ParishSoft now as the page has it, and the selection
    column as the page has it whatever the reader may now decide (a held
    campaign offers no bulk action, so its rows' selection cells are empty).
    The answer is only ever those rows: ui-v1.js never redraws the page's
    whole table from it (it lacks the other rows, the sort and the pager).
    """
    rows = changed(table.rows, live)
    columns = getattr(live, "columns", None)
    if columns is not None:
        worklist = dataclasses.replace(
            worklist, shows_now="now" in columns, select_column="select" in columns
        )
    return (
        dataclasses.replace(worklist, rows=rows),
        dataclasses.replace(table, rows=rows),
        len(rows),
    )


def checking(request):
    """Whether ``request`` names a review region's items (``live``).

    Such a check never extends the sign-in, whatever its headers say: each
    stream view reads its principal passively for it, as for any automatic
    re-read (``sessions.automatic_read``). A caller with no query (a
    command's ``AdminCaller``) is never one.
    """
    return LIVE in getattr(request, "GET", ())


# Every row, unpaged: a live read pages nothing, since its rows are only the
# drawn ones (at most ``MAX_LIVE``).
EVERY_ROW = {"size": "all"}
