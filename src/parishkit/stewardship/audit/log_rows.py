"""Closed filters and display rows for the Administrator's combined log screen.

Pure shaping. The stored context was already reduced to reviewed, closed fields
when it was written; this module still shows only scalar values it recognizes,
so a future schema that stores something richer cannot leak through the page.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from django.utils.datastructures import MultiValueDict
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.observability import Event
from parishkit.stewardship.web.contracts import filters

from .schemas import FIELDS, Action

PAGE_SIZE = 50
# The only detail ever shown: fields some reviewed context schema names. A key
# that merely looks like an identifier is not enough, because a future flat text
# field or a trigger-written context would otherwise be rendered verbatim.
DETAIL_FIELDS = frozenset().union(*FIELDS.values())
DETAIL_LIMIT = 128
# Entries cannot predate the application, and a far-future day cannot be advanced
# to its exclusive upper bound without overflowing.
EARLIEST, LATEST = date(2020, 1, 1), date(2999, 12, 31)
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# A word and a symbol, never color alone, distinguish the five levels.
LEVEL_LABELS = {
    "DEBUG": ("·", _("Debug")),
    "INFO": ("i", _("Information")),
    "WARNING": ("!", _("Warning")),
    "ERROR": ("×", _("Error")),
    "CRITICAL": ("‼", _("Critical")),
}
SOURCES = {
    "both": _("Operational and audit"),
    "operational": _("Operational only"),
    "audit": _("Audit only"),
}
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


def _identifier(value):
    """Accept only a canonical lowercase UUID, or nothing."""
    if value and str(UUID(value)) != value:
        raise ValueError("Identifiers must be canonical.")
    return value


@dataclass(frozen=True, repr=False)
class LogQuery:
    """Filters travel only in CSRF POST bodies; identifiers never reach a URL.

    DEBUG is excluded unless chosen. `applied` distinguishes a submitted form
    with no level ticked, which means none, from the first visit's default.
    Paging is a keyset cursor because the log grows while it is being read: an
    offset would skip or repeat entries as new ones arrive.
    """

    applied: str = ""
    debug: str = ""
    info: str = ""
    warning: str = ""
    error: str = ""
    critical: str = ""
    source: str = "both"
    event: str = ""
    actor: str = ""
    correlation: str = ""
    campaign: str = ""
    start: str = ""
    end: str = ""
    before: str = ""
    before_id: str = ""

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
        ticks = (query.debug, query.info, query.warning, query.error, query.critical)
        if (
            query.applied not in {"", "yes"}
            or any(tick not in {"", "yes"} for tick in ticks)
            # Ticks mean something only on a submitted form.
            or (any(ticks) and not query.applied)
            or query.source not in SOURCES
            or (query.event and EVENT.fullmatch(query.event) is None)
        ):
            raise ValueError("Invalid log filters.")
        for value in (query.actor, query.correlation, query.campaign, query.before_id):
            _identifier(value)
        for value in (query.start, query.end):
            if not value:
                continue
            day = date.fromisoformat(value)
            if day.isoformat() != value or not EARLIEST <= day <= LATEST:
                raise ValueError("Invalid log date filter.")
        if query.start and query.end and query.start > query.end:
            raise ValueError("Invalid log date interval.")
        if bool(query.before) != bool(query.before_id) or (
            query.before and INSTANT.fullmatch(query.before) is None
        ):
            raise ValueError("Invalid log cursor.")
        if query.before:
            datetime.fromisoformat(query.before)
        return query

    @property
    def levels(self):
        """Every level but DEBUG by default; exactly the ticked ones otherwise."""
        if not self.applied:
            return LEVELS[1:]
        return tuple(level for level in LEVELS if getattr(self, level.lower()))

    @property
    def cursor(self):
        """The last entry already shown, or None for the newest page."""
        if not self.before:
            return None
        return datetime.fromisoformat(self.before), UUID(self.before_id)

    def form_values(self):
        """Filter fields to carry into the next page, without the cursor."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key not in {"before", "before_id"} and getattr(self, key)
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


def page_context(query, rows, following):
    """The one template context, shared by the view and its browser fixtures."""
    return {
        "rows": rows,
        "query": query,
        "query_fields": query.form_values(),
        "following": following,
        "levels": [
            (level.lower(), LEVEL_LABELS[level], level in query.levels)
            for level in LEVEL_LABELS
        ],
        "sources": SOURCES,
        "events": EVENTS,
    }


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
        "event": record["event"],
        "actor_id": record["actor_id"],
        "correlation_id": record["correlation_id"],
        "campaign_id": None,
        "subject_id": None,
        "details": _details(record["context"]),
    }


def audit_row(record):
    """One audit entry. Audit records carry no severity level of their own."""
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "source": _("Audit"),
        "level": None,
        "level_symbol": "",
        "level_label": _("Audit record"),
        "event": record["event_type"],
        "actor_id": record["actor_id"],
        "correlation_id": record["correlation_id"],
        "campaign_id": record["campaign_reference"],
        "subject_id": record["subject_id"],
        "details": _details(record["auditcontext__context"]),
    }


def merge(operational, audit, *, size=PAGE_SIZE):
    """Newest first across both sources, with a cursor for the entries after.

    Each source supplies at most `size + 1` rows already ordered newest first, so
    the first `size` of their merge are exactly the next page of the union.
    """
    rows = sorted(
        [*operational, *audit],
        key=lambda row: (row["created_at"], row["id"]),
        reverse=True,
    )
    page = rows[:size]
    following = None
    if len(rows) > size:
        last = page[-1]
        following = {
            "before": last["created_at"]
            .astimezone(UTC)
            .isoformat(timespec="microseconds"),
            "before_id": str(last["id"]),
        }
    return page, following
