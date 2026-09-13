"""Shared v2 Family-change indications, not a complete source change stream.

The published FamilyChangeList contract is an unpaginated date-range GET with
family_DUID/logDate/currentParishID fields. It supplies no cursor token, upper
event watermark or proof that Member/Ministry/giving changes are covered.
Callers retain their own successful poll boundary, overlap date windows, reload
affected Families and advance durable state only after their atomic promotion.
Ambiguous responses require a full refresh rather than a partial indication set.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from .parishsoft import ParishSoftAPIError


class ChangeFeedIncomplete(RuntimeError):
    """The indication window cannot authorize a delta; no private payload retained."""


@dataclass(frozen=True)
class FamilyChangeIndications:
    """Only distinct identities and dates survive parsing, never contact text."""

    organization_id: int
    start_date: date
    end_date: date
    family_ids: tuple[int, ...]
    indication_count: int


def _identifier(value):
    """Reject bool/string coercion and values outside the int32 identifier range."""
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        raise ChangeFeedIncomplete("The change feed has an ambiguous identity.")
    return value


def _instant(value, start, end):
    """Do not guess an upstream timezone for an offset-free provider timestamp."""
    if type(value) is not str or not 20 <= len(value) <= 40:
        raise ChangeFeedIncomplete("The change feed has an ambiguous timestamp.")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        raise ChangeFeedIncomplete(
            "The change feed has an ambiguous timestamp."
        ) from None
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ChangeFeedIncomplete("The change feed has an ambiguous timezone.")
    # Accept either the provider's offset-local day or its UTC day in the
    # overlapping query. Neither is used to discard supposedly old events.
    try:
        inside = (
            start <= instant.date() <= end
            or start <= instant.astimezone(UTC).date() <= end
        )
    except (ValueError, OverflowError):
        raise ChangeFeedIncomplete(
            "The change feed has an invalid timestamp range."
        ) from None
    if not inside:
        raise ChangeFeedIncomplete("The change feed is outside the requested window.")


def load_family_changes(client, *, organization_id, start_date, end_date, maximum=1000):
    """Fetch uncached, tenant-checked bounded indications or require full refresh.

    The provider contract has no pagination/count metadata. A response at the
    safety ceiling is therefore not truncated or accepted as complete. Duplicate
    Family indications are expected in overlapping windows; all must validate
    before their deterministically sorted distinct identities are returned.
    Relationship/organization transitions cannot be applied as ordinary deltas.
    This function does not update the caller's durable watermark or source data.
    """
    _identifier(organization_id)
    if (
        type(start_date) is not date
        or type(end_date) is not date
        or not 0 <= (end_date - start_date).days <= 31
        or type(maximum) is not int
        or not 1 <= maximum <= 10000
    ):
        raise ValueError("Change-feed input requires a bounded date window and limit.")
    actual = client.validate_organization()
    if _identifier(actual) != organization_id:
        raise ChangeFeedIncomplete("The change feed belongs to another organization.")
    try:
        rows = client.get_uncached(
            "families/change/list",
            {"StartDate": start_date.isoformat(), "EndDate": end_date.isoformat()},
        )
    except ParishSoftAPIError as error:
        if error.status_code in {400, 404, 405, 501}:
            raise ChangeFeedIncomplete(
                "The Family change feed is unsupported."
            ) from None
        raise
    if type(rows) is not list or len(rows) >= maximum:
        raise ChangeFeedIncomplete("The change feed is unbounded or incomplete.")
    identifiers = set()
    for row in rows:
        if type(row) is not dict:
            raise ChangeFeedIncomplete("The change feed has an invalid record.")
        identifier = _identifier(row.get("family_DUID"))
        if _identifier(row.get("currentParishID")) != organization_id:
            raise ChangeFeedIncomplete(
                "The change feed cannot safely scope relationships."
            )
        previous = row.get("previousParishID")
        if previous is not None and _identifier(previous) != organization_id:
            raise ChangeFeedIncomplete(
                "The change feed contains an organization transfer."
            )
        _instant(row.get("logDate"), start_date, end_date)
        identifiers.add(identifier)
    return FamilyChangeIndications(
        organization_id, start_date, end_date, tuple(sorted(identifiers)), len(rows)
    )
