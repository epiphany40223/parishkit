"""Closed filters and display rows for the Administrator's combined log screen.

Pure shaping. The stored context was already reduced to reviewed, closed fields
when it was written; this module still shows only scalar values it recognizes,
so a future schema that stores something richer cannot leak through the page.
"""

import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.utils.datastructures import MultiValueDict
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.observability import Event
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import UnknownZone, browser_day_start
from parishkit.stewardship.web.tables import PAGE_SIZES, Sorting, TablePage

from .log_contract import SERIOUS
from .log_descriptions import ACTOR_KINDS, describe, field_label, field_value
from .log_details import explain
from .schemas import FIELDS, Action

PAGE_SIZE = 50
# Only the Time column sorts. Both directions are served by the (created_at,
# id) index each source has (operational_created_id, audit_event_created_id),
# so a page reads index-ordered keys and merges them. Excluded columns:
# Level (audit entries have none, so it cannot order the union), Type (the
# audit log's audit_event_time index on (event_type, created_at) orders its
# types, but the operational log's event column is unindexed; its
# operational_level_time index orders by level. No index orders the union
# by type, so every view would sort every matching operational row), Actor
# (the name shown is an email looked up for display, not a stored value) and
# Related / Recorded detail (links and key-value lists, not scalar values).
LOG_SORTING = Sorting({"newest": ("time", True), "oldest": ("time", False)}, "newest")
# Fields that page through one snapshot. The filter form keeps only the
# rows-per-page and sort choices (``TablePage.view_fields``), never the
# snapshot or page, so applying filters starts a new snapshot at page 1.
PAGING = frozenset({"through", "page", "size", "sort"})
# Filters that may travel in a web address, so a filtered view can be
# bookmarked or linked (#536): the closed choices, the type, the Ministry,
# the days with their zone, and the view's size and sort. Private filters
# stay in POST state, as the Admin tables rule requires: an address is kept
# in the web server's access log and the browser's history (the page writes
# its link there) and can travel on when pasted. So the identifiers (actor,
# correlation, campaign, subject), one of which ties a person to what they
# did, never do; nor does the search text, which can name a person (a
# review reason, a shown DUID), as the Find a Family search keeps its text
# out of URLs. Nor does the snapshot (``through``, ``page``), which belongs
# to one reading. Those travel only in CSRF POST bodies, as before (#519).
LINK_FIELDS = frozenset(
    {
        "applied",
        "debug",
        "info",
        "warning",
        "error",
        "critical",
        "audit",
        "event",
        "ministry",
        "start",
        "end",
        "zone",
        "size",
        "sort",
    }
)
# Search text is a short phrase. Text with an "@" (an address) is refused
# with a hint: no log value holds an address, so it could only find nothing.
TEXT_LIMIT = 64
# The only detail ever shown: fields some reviewed context schema names. A key
# that merely looks like an identifier is not enough, because a future flat text
# field or a trigger-written context would otherwise be rendered verbatim.
DETAIL_FIELDS = frozenset().union(*FIELDS.values())
DETAIL_LIMIT = 128
# Entries cannot predate the application, and a far-future day cannot be advanced
# to its exclusive upper bound without overflowing.
EARLIEST, LATEST = date(2020, 1, 1), date(2999, 12, 31)
# A word and an icon, never color alone, distinguish the five levels, listed
# least severe first; the order is the form's and the one source of LEVELS. The
# level choices show the word beside the icon; the table's Level column shows
# the icon with the word as screen-reader text plus a tooltip.
LEVEL_LABELS = {
    "DEBUG": ("·", _("Debug")),
    "INFO": ("i", _("Information")),
    "WARNING": ("!", _("Warning")),
    "ERROR": ("×", _("Error")),
    "CRITICAL": ("‼", _("Critical")),
}
LEVELS = tuple(LEVEL_LABELS)
# Audit records have no level. They are the sixth kind of entry the filter row
# offers (#601), with their own icon (a clipboard, distinct in shape from the
# five level icons), named for screen readers and as a tooltip.
AUDIT_LABEL = _("Audit record")
# The retired Source select's values (#601). A tab opened before it was
# removed may still send one, so for one release it is mapped onto the
# checkboxes (``LogQuery._from_source``) instead of refused.
LEGACY_SOURCES = frozenset({"both", "operational", "audit"})
# Suggestions only. Many audit types are written directly by their owners and by
# SQL triggers, such as `admin_login`, so the two vocabularies here are not the
# whole set; hiding the rest behind a fixed list would make them unsearchable.
EVENTS = tuple(sorted({item.value for item in Action} | {item.value for item in Event}))
# A type is searched as an exact identifier, never as free text: the same shape
# the audit table's own check constraint enforces on every stored type.
EVENT = re.compile(r"[a-z][a-z0-9_]{0,63}")
INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00"
)


class NothingShown(ValueError):
    """A submitted filter form ticked none of the six kinds of entry (#601).

    The view words this refusal on its own: no value the reader typed was
    wrong, they only left every Show choice unticked.
    """


def _ministry(value):
    """Accept only a canonical positive Ministry DUID, as entries store it."""
    if value and not (
        value.isascii() and value.isdecimal() and value[0] != "0" and int(value) < 2**31
    ):
        raise ValueError("Invalid Ministry DUID.")
    return value


def _text(value):
    """Accept short printable search text without an address; trim spaces."""
    value = value.strip()
    if len(value) > TEXT_LIMIT or "@" in value or not value.isprintable():
        raise ValueError("Invalid log search text.")
    return value


def _identifier(value):
    """Accept only a canonical lowercase UUID, or nothing."""
    if value and str(UUID(value)) != value:
        raise ValueError("Identifiers must be canonical.")
    return value


@dataclass(frozen=True, repr=False)
class LogQuery:
    """Closed log filters; identifiers never reach a URL.

    The page's forms send every filter in CSRF POST bodies. A GET may carry
    only ``LINK_FIELDS`` (#536), so a filtered view can be bookmarked or
    linked; ``link_values`` builds that link.

    The five operational levels and audit records (`audit`) are six
    checkboxes (#601). The first visit's default, before `applied` marks a
    submitted form, is every level but DEBUG plus audit records; a submitted
    form shows exactly what it ticks and must tick at least one. An older
    tab's `source` choice is mapped onto the ticks (``_from_source``).
    The log grows while it is being read, so paging is anchored to a snapshot:
    the first view records `through`, the database time it read at, and every
    later page, sort or size change carries it and lists only entries created
    at or before it. New entries therefore cannot shift an offset page; they
    appear once the filters are applied again. ``created_at`` is the insert
    statement's start time, so an entry whose transaction began before the
    snapshot but committed after it can still appear on a later page view,
    shifting that page by one entry; such overlaps are rare and brief.

    From (`start`) and Through (`end`) are calendar days in the viewer's
    browser time zone (#558), which the page script sends as `zone` and every
    navigator, heading and export form carries on with the other filters.
    """

    applied: str = ""
    debug: str = ""
    info: str = ""
    warning: str = ""
    error: str = ""
    critical: str = ""
    audit: str = ""
    source: str = ""
    event: str = ""
    actor: str = ""
    correlation: str = ""
    campaign: str = ""
    subject: str = ""
    text: str = ""
    ministry: str = ""
    start: str = ""
    end: str = ""
    zone: str = ""
    through: str = ""
    page: str = ""
    size: str = ""
    sort: str = ""

    @classmethod
    def parse(cls, parameters):
        """Accept only single bounded values from the closed vocabularies."""
        if type(parameters) is dict:
            if any(type(value) is not str for value in parameters.values()):
                raise ValueError("Log filters require text values.")
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        query = cls(**filters(parameters, allowed=set(cls.__dataclass_fields__)))
        ticks = (
            query.debug,
            query.info,
            query.warning,
            query.error,
            query.critical,
            query.audit,
        )
        if (
            query.applied not in {"", "yes"}
            or any(tick not in {"", "yes"} for tick in ticks)
            # Ticks mean something only on a submitted form.
            or (any(ticks) and not query.applied)
            # A legacy source never comes with the audit tick that replaced it.
            or (query.source and (query.source not in LEGACY_SOURCES or query.audit))
            or (query.event and EVENT.fullmatch(query.event) is None)
        ):
            raise ValueError("Invalid log filters.")
        if query.source:
            query = query._from_source()
        if query.applied and not (query.levels or query.audit):
            # The page's gate keeps Apply unavailable with nothing ticked;
            # a form that bypasses it is refused, not answered with nothing.
            raise NothingShown("Log filters must show at least one kind of entry.")
        for value in (query.actor, query.correlation, query.campaign, query.subject):
            _identifier(value)
        _ministry(query.ministry)
        query = replace(query, text=_text(query.text))
        for value in (query.start, query.end):
            if not value:
                continue
            day = date.fromisoformat(value)
            if day.isoformat() != value or not EARLIEST <= day <= LATEST:
                raise ValueError("Invalid log date filter.")
        if query.start and query.end and query.start > query.end:
            raise ValueError("Invalid log date interval.")
        if query.zone not in timezone_names():
            # Days cannot be placed without the browser's zone (a blank one
            # from a tab opened before #558, or a zone outside the catalog).
            if query.start or query.end:
                raise UnknownZone("Log dates need the browser's time zone.")
            # Without days the zone is unused; drop one the catalog does not
            # know rather than refuse filters that never needed it.
            query = replace(query, zone="")
        if query.through:
            if INSTANT.fullmatch(query.through) is None:
                raise ValueError("Invalid log snapshot.")
            datetime.fromisoformat(query.through)
        if query.page and (
            not (query.page.isascii() and query.page.isdecimal()) or len(query.page) > 9
        ):
            raise ValueError("Invalid log page.")
        if query.size and query.size not in {str(size) for size in PAGE_SIZES}:
            raise ValueError("Invalid log page size.")
        if query.sort:
            LOG_SORTING.parse({"sort": query.sort})
        return query

    @property
    def levels(self):
        """Every level but DEBUG by default; exactly the ticked ones otherwise."""
        if not self.applied:
            return LEVELS[1:]
        return tuple(level for level in LEVELS if getattr(self, level.lower()))

    @property
    def audits(self):
        """Whether audit records are shown: by default, or when ticked."""
        return not self.applied or bool(self.audit)

    def _from_source(self):
        """Map a retired Source choice (#601) onto the six checkboxes.

        The levels it came with (or the default ones, on a form that was not
        applied, such as the old "Same campaign" action) are kept, except that
        "audit" (audit only) clears them; "operational" leaves audit records
        out. The result is an ordinary applied query, so paging and the export
        carry the checkboxes, never ``source`` again.
        """
        levels = () if self.source == "audit" else self.levels
        return replace(
            self,
            applied="yes",
            source="",
            audit="" if self.source == "operational" else "yes",
            **{level.lower(): "yes" if level in levels else "" for level in LEVELS},
        )

    @property
    def bounds(self):
        """The From and Through days as a UTC interval ``[lower, upper)``.

        From starts at local midnight of its day in the browser's zone;
        Through ends where the next local day starts, so a daylight-saving day
        is 23 or 25 hours long. Either bound is None when its day is unset.
        """
        start, end = (
            date.fromisoformat(value) if value else None
            for value in (self.start, self.end)
        )
        return (
            browser_day_start(start, self.zone) if start else None,
            browser_day_start(end + timedelta(days=1), self.zone) if end else None,
        )

    @property
    def snapshot(self):
        """The instant this reading is anchored to, or None for a fresh one."""
        return datetime.fromisoformat(self.through) if self.through else None

    @property
    def page_number(self):
        """The requested 1-based page; the view clamps it to what exists."""
        return max(1, int(self.page)) if self.page else 1

    @property
    def page_size(self):
        """Entries per page."""
        return int(self.size) if self.size else PAGE_SIZE

    @property
    def order(self):
        """The validated sort token."""
        return self.sort or LOG_SORTING.default

    @property
    def oldest(self):
        """Whether the page lists the oldest entries first."""
        return not LOG_SORTING.tokens[self.order][1]

    @property
    def private(self):
        """Whether an identifier filter is applied."""
        return any((self.actor, self.correlation, self.campaign, self.subject))

    @property
    def unlinked(self):
        """Whether an applied filter is one a link leaves out (search or an
        identifier), so the page says the link does not carry it."""
        return self.private or bool(self.text)

    def link_values(self):
        """The applied filters a web address may carry (``LINK_FIELDS``).

        The zone goes with the days, which mean nothing without it, and is
        left out otherwise.
        """
        values = {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key in LINK_FIELDS and getattr(self, key)
        }
        if not (self.start or self.end):
            values.pop("zone", None)
        return values

    def form_values(self):
        """Filter fields to carry into other pages, without paging or sort."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key not in PAGING and getattr(self, key)
        }


def _details(context):
    """Only reviewed fields with short scalar values; nothing else is rendered."""
    if type(context) is not dict:
        return []
    # Filter before sorting, so an unexpected key can never break the page.
    known = {
        key: value
        for key, value in context.items()
        if type(key) is str and key in DETAIL_FIELDS
    }
    shown = []
    for key, value in sorted(known.items()):
        if value is None or type(value) in (int, bool):
            shown.append((key, "" if value is None else str(value)))
        elif type(value) is str and len(value) <= DETAIL_LIMIT:
            shown.append((key, value))
        elif type(value) is list and all(type(item) is int for item in value):
            shown.append((key, ", ".join(str(item) for item in value)))
    return shown


def detail_labels(details):
    """The recorded detail with each field named in words, for display only.

    Exports keep the stored field names, which are what a filter or script
    matches; the page shows "Lag microseconds" instead of "lag_microseconds",
    and a report's recorded choices in its own menu's words (#556).
    """
    return [(field_label(key), field_value(key, value)) for key, value in details]


def log_table(query, rows, *, through, action, number=1, total=None, capped=False):
    """Describe one page of the log for the shared POST navigator.

    ``total`` defaults to the rows given (a fixture's single page);
    ``capped`` marks a bounded total, so the navigator says "more than" and
    pages on while ``has_next`` allows. The snapshot instant travels with the
    filters on every navigator and heading form, never in a URL.
    """
    size = query.page_size
    total = len(rows) if total is None else total
    return TablePage(
        rows=list(rows),
        number=number,
        pages=max(1, -(-total // size)),
        count=total,
        size=size,
        prefix="",
        carried=(
            *query.form_values().items(),
            ("through", through.astimezone(UTC).isoformat(timespec="microseconds")),
        ),
        has_next=number * size < total,
        allow_all=False,
        sorting=LOG_SORTING,
        sort=query.order,
        method="post",
        action=action,
        capped=capped,
    )


def page_context(query, table, *, depth_limited=False, linked=False):
    """The one template context, shared by the view and its browser fixtures.

    ``table`` is the page's ``web.tables.TablePage``; ``depth_limited`` says a
    requested page lay past the paging depth and the last reachable one is
    shown instead. ``linked`` says the filters came from a web address (a
    bookmark or link, #536).
    ``link`` is this view's own address: the page's address plus the
    filters a link may carry. ``link_zone`` names the zone a followed link's
    days are in; the page notes it when it is not the browser's own.
    """
    values = query.link_values()
    return {
        "link": table.action + (f"?{urlencode(values)}" if values else ""),
        "link_zone": values.get("zone", "") if linked else "",
        "rows": table.rows,
        "table": table,
        "depth_limited": depth_limited,
        "query": query,
        "query_fields": query.form_values(),
        # The six kinds of entry, in the filter row's order: the five levels,
        # least severe first, then audit records.
        "levels": [
            *(
                (level.lower(), label, level in query.levels)
                for level, (_symbol, label) in LEVEL_LABELS.items()
            ),
            ("audit", AUDIT_LABEL, query.audits),
        ],
        "events": EVENTS,
    }


def _summary(record, details):
    """The entry's sentence (#633), or a plain note when a serious entry has none.

    A WARNING-or-above entry whose context neither explains itself nor lists
    any field (one written before #633, or whose detail was refused) says so
    rather than leaving its detail blank.
    """
    text = explain(record.get("schema"), record["context"])
    if text is None and not details and record["level"] in SERIOUS:
        return _("This entry was recorded without detail.")
    return text


def operational_row(record):
    """One diagnostic entry from its stored closed event, level and context."""
    symbol, label = LEVEL_LABELS[record["level"]]
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "source": _("Operational"),
        "level": record["level"],
        "level_symbol": symbol,
        "level_label": label,
        "icon": record["level"].lower(),
        "event": record["event"],
        "description": describe(record["event"], record["context"]),
        # What happened this time, in words, from the stored context (#633).
        "summary": _summary(record, _details(record["context"])),
        "actor_id": record["actor_id"],
        "correlation_id": record["correlation_id"],
        "campaign_id": None,
        "subject_id": None,
        "details": _details(record["context"]),
        "detail_rows": detail_labels(_details(record["context"])),
        "actor_kind_label": None,
    }


def audit_row(record):
    """One audit entry. Audit records carry no severity level of their own."""
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "source": _("Audit"),
        "level": None,
        "level_symbol": "",
        "level_label": AUDIT_LABEL,
        "icon": "audit",
        "event": record["event_type"],
        "description": describe(record["event_type"]),
        "summary": None,
        "actor_id": record["actor_id"],
        "correlation_id": record["correlation_id"],
        "campaign_id": record["campaign_reference"],
        "subject_id": record["subject_id"],
        "details": _details(record["auditcontext__context"]),
        "detail_rows": detail_labels(_details(record["auditcontext__context"])),
        # Audit contexts say what kind of actor acted; the page names portal
        # users and workers itself and uses this for Families, the system
        # and the operator, which have no portal account to look up.
        "actor_kind_label": ACTOR_KINDS.get(record.get("auditcontext__actor_kind")),
    }


def task_subject(row):
    """Whether the entry's subject is a background task with its own page.

    Task entries name the task as their subject, and so does a view of one
    task's page; other subjects (sessions, link generations and the like)
    have no Admin page to open.
    """
    return bool(
        row["subject_id"]
        and (
            row["event"].startswith("task_")
            or row["event"] == Action.BACKGROUND_VIEWED.value
        )
    )


def merge(operational, audit, *, oldest=False):
    """Order both sources' rows as one log: by time, then identifier.

    Each source supplies its first rows in the same order its own query uses
    ((created_at, id), in the chosen direction), so the first N of the merge
    are exactly the first N of the union.
    """
    return sorted(
        [*operational, *audit],
        key=lambda row: (row["created_at"], row["id"]),
        reverse=not oldest,
    )
